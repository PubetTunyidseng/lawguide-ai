"""ทดสอบผ่าน Terminal: ถาม 1 คำถาม แล้วพิมพ์ tool calls + context + คำตอบ

ใช้:  python ask.py "ทำงานมา 4 ปี ถูกเลิกจ้างเพราะลดคน ได้ค่าชดเชยกี่วัน"
      python ask.py            (โหมดคุยต่อเนื่อง พิมพ์ q เพื่อออก)
"""
import json
import sys

from lawguide import config, harness
from lawguide.store import get_store


def show(r):
    print(f"\n[ข้อความที่ส่ง LLM] {r['question_masked']}")
    for w in r["pii"]["warnings"]:
        print(f"  ⚠️ {w}")
    print("\n[Tool calls]")
    for e in r["trace"]:
        if e.get("tool"):
            flag = "  ← ตีกลับโดย policy" if e.get("result_sent_to_model") else ""
            print(f"  step {e['step']}: {e['tool']}({json.dumps(e['arguments'], ensure_ascii=False)}){flag}")
        else:
            print(f"  step {e['step']}: (ตอบข้อความเปล่า ไม่เรียก tool)")
    print(f"\n[Context จาก tool] มาตรา {', '.join(c['section_no'] for c in r['context_returned']) or '-'}")
    f = r.get("final") or {}
    print(f"\n[สถานะ] {r['status']}")
    if r["status"] == "need_more_facts":
        print("[ระบบถามกลับ]")
        for q in f.get("questions", []):
            print(f"  - {q}")
    elif f.get("answer"):
        print(f"[ประเด็น] {f.get('legal_issue', '')}\n[Final answer]\n{f['answer']}")
    for c in r["citations_rendered"]:
        ok = "✅" if c["exists"] and c["in_context"] else "❌"
        print(f"  {ok} {c['law_name']} มาตรา {c['section_no']} · ดึงตัวบท {c['source_retrieved']} · ตรวจ {c['checked_on']}")
    print("\n[สถานะข้อมูล]")
    for k, v in r["status_panel"].items():
        print(f"  {k}: {v}")
    if r.get("policy_violations"):
        print("[policy]", [f"{v['rule']}:{v['section_no']}" for v in r["policy_violations"]])
    print(f"\n{r['steps']} steps · {r['latency_s']} s · tokens {r.get('usage')}")


def main():
    store = get_store()
    print(f"backend={config.BACKEND} ({store.name}) · model={config.LLM_MODEL}")
    s = harness.Session(store)
    if len(sys.argv) > 1:
        show(s.ask(" ".join(sys.argv[1:])))
        return
    while True:
        q = input("\nถาม> ").strip()
        if q in ("q", "quit", "exit"):
            break
        if q:
            show(s.ask(q))


if __name__ == "__main__":
    main()
