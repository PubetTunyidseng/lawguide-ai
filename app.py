"""หน้าจอใช้งาน LawGuide (Gradio) — แต่ละกล่องมีป้ายบอกว่าคืออะไร ใช้ชี้ตอนสอบได้

รัน:  python app.py            -> http://localhost:7860
"""
import json

import gradio as gr

from lawguide import config, harness
from lawguide.store import get_store

STORE = get_store()
REPORT = (config.PROCESSED_DIR / "ingest_report.json")
CORPUS_WARN = json.loads(REPORT.read_text(encoding="utf-8")).get("corpus_version_warnings", []) if REPORT.exists() else []

BANNER = f"""### ⚖️ LawGuide — ข้อมูลกฎหมายแรงงานเบื้องต้น
ขอบเขต: **{config.LAW_NAME}** เท่านั้น · {harness.DISCLAIMER}
"""


def render_answer(r):
    f = r.get("final") or {}
    if r["status"] == "blocked_pii":
        return "ข้อความมีข้อมูลอ่อนไหวที่ระบบไม่ประมวลผล กรุณาลบข้อมูลส่วนบุคคลแล้วถามใหม่"
    if r["status"] == "blocked_by_policy":
        return ("**ระบบไม่แสดงคำตอบ** เพราะตรวจพบการอ้างมาตราที่ยืนยันกับฐานข้อมูลไม่ได้ "
                "ดูตัวบทที่ค้นได้ในกล่อง ④ Context")
    if r["status"] == "need_more_facts":
        out = "**ต้องการข้อเท็จจริงเพิ่ม**\n\n" + "\n".join(f"- {q}" for q in f.get("questions", []))
        if f.get("partial_answer"):
            out += f"\n\nข้อมูลเบื้องต้น: {f['partial_answer']}"
        return out
    if not f:
        return f"(สถานะ: {r['status']})"
    md = f"**ประเด็น:** {f.get('legal_issue', '-')} · `{f.get('issue_type') or '-'}`\n\n{f.get('answer', '')}\n\n"
    if r["citations_rendered"]:
        md += "| กฎหมาย | มาตรา | ยืนยันกับฐานข้อมูล | แหล่งที่มา | วันที่ดึงตัวบท | วันที่ตรวจ |\n|---|---|---|---|---|---|\n"
        for c in r["citations_rendered"]:
            ok = "✅" if c["exists"] and c["in_context"] else "❌"
            md += (f"| {c['law_name']} | {c['section_no']} | {ok} | [ลิงก์]({c['source_url']}) | "
                   f"{c['source_retrieved']} | {c['checked_on']} |\n")
    if f.get("next_steps"):
        md += "\n**ขั้นตอนที่ทำได้**\n" + "\n".join(f"- {s}" for s in f["next_steps"])
    return md


def render_status(r):
    p = r["status_panel"]
    lines = []
    for k, v in p.items():
        if isinstance(v, list):
            v = "\n".join(f"  - {x}" for x in v) if v else "  - (ไม่มี)"
            lines.append(f"**{k}**\n{v}")
        else:
            lines.append(f"**{k}:** {v}")
    if CORPUS_WARN:
        lines.append("**คำเตือนระดับคลังข้อมูล**\n" + "\n".join(f"  - {w}" for w in CORPUS_WARN))
    lines.append(f"\n<sub>model {r['model']} · {r.get('steps', 0)} steps · {r['latency_s']} s · status `{r['status']}`</sub>")
    return "\n\n".join(lines)


def _tool_note(e):
    """สรุปผลแต่ละ step ให้เห็นว่า harness เข้าไปคุมตรงไหน"""
    res = e.get("result") or {}
    if e.get("result_sent_to_model"):
        return "ตีกลับโดย policy: " + ", ".join(sorted({v["rule"] for v in e.get("policy_violations", [])}))
    if isinstance(res, dict) and res.get("duplicate_query"):
        return "ค้นซ้ำ → harness ไม่ค้นใหม่ ส่งคำแนะนำให้เปลี่ยนคำค้น"
    if isinstance(res, dict) and "top_k_used" in res:
        clamp = f" (ขอ top_k={res['top_k_requested']} → ใช้ {res['top_k_used']})" \
            if res["top_k_used"] != res["top_k_requested"] else ""
        return f"ได้ {res['n_results']} มาตรา ใหม่ {res.get('new_sections', [])}{clamp}"
    return "ok"


def render_tools(r):
    rows = [[e["step"], e.get("tool", e["type"]), json.dumps(e.get("arguments", {}), ensure_ascii=False),
             _tool_note(e)] for e in r["trace"]]
    return rows or [["-", "-", "-", "-"]]


def render_context(r):
    if not r["context_returned"]:
        return "(ไม่มี context — ระบบยังไม่ได้ดึงตัวบท)"
    return "\n\n---\n\n".join(f"**มาตรา {c['section_no']}** · {c['chapter']}\n\n{c['text']}\n\n"
                              f"<sub>{c['source_url']} · ดึงตัวบท {c['source_retrieved']}</sub>"
                              for c in r["context_returned"])


def render_pii(r):
    p = r["pii"]
    d = "\n".join(f"- {x['category']} ({x['detector']}) → **{x['action']}** — {x['reason']}" for x in p["decisions"])
    w = "\n".join(f"- ⚠️ {x}" for x in p["warnings"])
    return f"**ข้อความที่ส่งให้ LLM จริง (หลัง mask):**\n\n> {p['masked_text']}\n\n{d or '- ไม่พบข้อมูลอ่อนไหว'}\n\n{w}"


def ask(q, chat, sess, use_llm_pii):
    if not q.strip():
        return gr.update(), chat, sess, *(gr.update(),) * 6
    sess = sess or harness.Session(STORE)
    try:
        r = sess.ask(q, use_llm_pii=use_llm_pii)
    except Exception as e:
        chat = chat + [{"role": "user", "content": q}, {"role": "assistant", "content": f"เกิดข้อผิดพลาด: {e}"}]
        return "", chat, sess, *(gr.update(),) * 6
    answer = render_answer(r)
    chat = chat + [{"role": "user", "content": r["question_masked"]}, {"role": "assistant", "content": answer}]
    return ("", chat, sess, answer, render_status(r), render_tools(r), render_context(r), render_pii(r),
            json.dumps(r, ensure_ascii=False, indent=1, default=str))


with gr.Blocks(title="LawGuide") as demo:
    gr.Markdown(BANNER)
    sess = gr.State(None)
    with gr.Row():
        with gr.Column(scale=5):
            _kw = dict(height=430, label="บทสนทนา (ข้อความผู้ใช้แสดงแบบ mask แล้ว)")
            try:                                     # gradio 5 ต้องระบุ type, gradio 6 ใช้ messages เป็นค่าเริ่มต้น
                chat = gr.Chatbot(type="messages", **_kw)
            except TypeError:
                chat = gr.Chatbot(**_kw)
            q = gr.Textbox(placeholder="เล่าสถานการณ์ เช่น ทำงานมา 3 ปี ถูกเลิกจ้างโดยไม่บอกล่วงหน้า ได้อะไรบ้าง",
                           label="คำถาม (ไม่ต้องใส่ชื่อ เลขบัตร หรือเบอร์โทร)")
            with gr.Row():
                send = gr.Button("ถาม", variant="primary")
                reset = gr.Button("เริ่มเรื่องใหม่")
                use_llm_pii = gr.Checkbox(value=config.PII_LLM_EXTRACT, label="ให้ LLM ช่วยหาข้อมูลอ่อนไหวเพิ่ม")
        with gr.Column(scale=6):
            with gr.Tab("① Final answer"):
                ans = gr.Markdown("คำตอบสุดท้ายของระบบจะแสดงที่นี่")
            with gr.Tab("② สถานะข้อมูล"):
                status = gr.Markdown()
            with gr.Tab("③ Tool calls"):
                tools = gr.Dataframe(headers=["step", "tool name", "arguments", "ผล"], wrap=True)
            with gr.Tab("④ Context จาก tool"):
                ctx = gr.Markdown()
            with gr.Tab("⑤ PII guard"):
                piib = gr.Markdown()
            with gr.Tab("⑥ Raw trace"):
                raw = gr.Code(language="json")
    outs = [q, chat, sess, ans, status, tools, ctx, piib, raw]
    send.click(ask, [q, chat, sess, use_llm_pii], outs)
    q.submit(ask, [q, chat, sess, use_llm_pii], outs)
    reset.click(lambda: ([], None), None, [chat, sess])

if __name__ == "__main__":
    import os
    # ตอนสาธิตทางไกล: GRADIO_SHARE=1 ได้ลิงก์ *.gradio.live (อยู่ 72 ชม. วิ่งผ่านเครื่องนี้ ใช้ key ของเครื่องนี้)
    # ควรตั้ง GRADIO_AUTH=user:pass ด้วย เพราะใครได้ลิงก์ก็ใช้เครดิต OpenRouter ของเราได้
    share = os.environ.get("GRADIO_SHARE", "0") == "1"
    auth = tuple(os.environ["GRADIO_AUTH"].split(":", 1)) if ":" in os.environ.get("GRADIO_AUTH", "") else None
    if share and not auth:
        print("⚠️ GRADIO_SHARE=1 แต่ไม่ได้ตั้ง GRADIO_AUTH — ใครได้ลิงก์ก็ใช้ได้")
    demo.launch(share=share, auth=auth)
