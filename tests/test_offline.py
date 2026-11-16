"""ทดสอบโดยไม่เรียก LLM จริง: parser, version check, tools, policy, PII, และ agent loop ด้วย LLM จำลอง

รัน:  python -m pytest -q tests/test_offline.py     หรือ   python tests/test_offline.py
"""
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lawguide import config  # noqa: E402

config.RUNS_DIR = Path(__file__).resolve().parent / "_runs"
from lawguide import harness, pii, policy  # noqa: E402
from lawguide.ingest import build_docs  # noqa: E402
from lawguide.store import LocalStore  # noqa: E402
from lawguide.tools import ToolExecutor  # noqa: E402

FIXTURE = (Path(__file__).parent / "fixture_law_format.txt").read_text(encoding="utf-8")


NO9 = [{"name": "พระราชบัญญัติคุ้มครองแรงงาน (ฉบับที่ 9) พ.ศ. 2568", "ordinal": 9, "marker": r"\(ฉบับที่\s*9\)",
        "gazette_published": "2025-11-07", "effective": "2025-12-07",
        "sections_changed": ["4/1", "41", "41/1", "59"], "sections_new": ["4/1", "41/1"]}]


def make_store():
    docs, report = build_docs(FIXTURE, retrieved="2026-11-15", today=date(2026, 11, 15), amendments=NO9)
    return LocalStore(docs), docs, report


def test_parser_and_versions():
    _, docs, report = make_store()
    nos = [d["section_no"] for d in docs]
    assert nos == ["1", "41", "41/1", "59", "118"], nos          # มาตรา 2 ของฉบับแก้ไขท้ายเล่มต้องไม่ถูกนับ
    by = {d["section_no"]: d for d in docs}
    assert by["41"]["chapter"].startswith("หมวด 3") or by["41"]["chapter"].startswith("หมวด ๓")
    assert [f["status"] for f in by["41"]["version_flags"]] == ["updated"]
    assert [f["status"] for f in by["59"]["version_flags"]] == ["possibly_outdated"]
    assert any("มาตรา 4/1" in w for w in report["corpus_version_warnings"])   # มาตราใหม่ที่หายไป
    assert "มาตรา ๕๙" in by["41"]["text"]
    assert by["41"]["cites"] == ["59"]                                       # กราฟอ้างอิงจากเนื้อความ
    assert [x["section_no"] for x in by["59"]["cited_by"]] == ["41"]                                    # การอ้างในเนื้อความไม่ถูกตัดเป็นมาตราใหม่


def test_tools_and_policy():
    store, _, _ = make_store()
    ex = ToolExecutor(store)
    r = ex.run("search_law_sections", {"query": "ลาคลอดบุตร ค่าจ้าง"})
    assert r["n_results"] >= 1
    assert ex.run("get_law_section", {"section_no": "มาตรา ๔๑/๑"})["found"]
    assert not ex.run("get_law_section", {"section_no": "999"})["found"]
    v = ex.run("verify_citation", {"section_no": "41", "quoted_text": "ลูกจ้างหญิงลาเพื่อคลอดบุตร"})
    assert v["exists"] and v["quote_found"]
    bad = ex.run("verify_citation", {"section_no": "41", "quoted_text": "ลาได้ 365 วัน"})
    assert bad["quote_found"] is False

    final = {"answer": "ตามมาตรา 41 และมาตรา 777", "citations": [{"section_no": "41"}, {"section_no": "888"}],
             "facts_sufficient": True, "facts_to_verify": []}
    rules = {v["rule"] for v in policy.check_final(final, ex)}
    assert {"CITE_EXISTS", "ANSWER_MENTIONS_GROUNDED"} <= rules


def test_quote_ellipsis_and_negation():
    from lawguide.tools import quote_in_text
    text = "มาตรา ๗๖ ห้ามมิให้นายจ้างหักค่าจ้าง เว้นแต่เป็นการหักเพื่อ (๑) ชำระภาษี (๔) ชดใช้ค่าเสียหาย โดยได้รับความยินยอม"
    assert quote_in_text("ห้ามมิให้นายจ้างหักค่าจ้าง ... (๔) ชดใช้ค่าเสียหาย", text)
    assert quote_in_text("ห้ามมิให้นายจ้างหักค่าจ้าง… โดยได้รับความยินยอม", text)
    assert not quote_in_text("(๔) ชดใช้ค่าเสียหาย ... ห้ามมิให้นายจ้างหักค่าจ้าง", text)    # สลับลำดับไม่ได้
    assert not quote_in_text("ห้ามมิให้นายจ้างหักค่าจ้าง ... หักได้ทุกกรณี", text)          # แต่งเติมไม่ได้
    assert policy.negated_mention("ไม่มีมาตรา 999 ในกฎหมายฉบับนี้", "999")
    assert policy.negated_mention("มาตรา ๙๙๙ ไม่มีอยู่จริง", "999")
    assert not policy.negated_mention("ตามมาตรา 999 ได้โบนัส 2 เดือน", "999")

    store, _, _ = make_store()
    ex = ToolExecutor(store)
    ex.run("verify_citation", {"section_no": "999"})
    ok = {"answer": "ไม่มีมาตรา 999 ในพระราชบัญญัตินี้", "citations": [], "facts_sufficient": False,
          "facts_to_verify": []}
    assert policy.check_final(ok, ex) == []
    bad = {"answer": "ตามมาตรา 999 ได้โบนัส 2 เดือน", "citations": [], "facts_sufficient": False,
           "facts_to_verify": []}
    assert [v["rule"] for v in policy.check_final(bad, ex)] == ["ANSWER_MENTIONS_GROUNDED"]


def test_pii():
    g = pii.guard("ผม นายสมมติ ทดสอบดี เลขบัตร 1-2345-67890-12-1 โทร 081-234-5678 นายจ้างไม่จ่ายค่าจ้าง",
                  use_llm=False)
    cats = [d["category"] for d in g["decisions"]]
    assert "person_name" in cats and "thai_id" in cats and "phone" in cats, cats
    assert "นายจ้าง" in g["masked_text"]                          # ห้าม mask คำว่า นายจ้าง
    assert "สมมติ" not in g["masked_text"] and "081" not in g["masked_text"]
    assert pii.thai_id_valid("1234567890121") is True
    assert pii.thai_id_valid("1234567890122") is False


# ---------- LLM จำลอง: ลองอ้างมาตราที่ไม่มีจริงก่อน แล้วถูก policy ตีกลับ ----------
def _call(i, name, args):
    return NS(id=f"c{i}", function=NS(name=name, arguments=json.dumps(args, ensure_ascii=False)))


def _resp(calls=None, content=""):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls))],
              usage=NS(prompt_tokens=10, completion_tokens=5))


def test_agent_loop_self_correction():
    store, _, _ = make_store()
    script = iter([
        _resp([_call(1, "search_law_sections", {"query": "ลาคลอด"})]),
        _resp([_call(2, "submit_final_answer", {
            "legal_issue": "สิทธิลาคลอด", "answer": "ลาคลอดได้ตามมาตรา 41 และมาตรา 41/9",
            "citations": [{"section_no": "41"}, {"section_no": "41/9"}],
            "facts_sufficient": True, "facts_to_verify": []})]),
        _resp([_call(3, "verify_citation", {"section_no": "41"})]),
        _resp([_call(4, "submit_final_answer", {
            "legal_issue": "สิทธิลาคลอด", "answer": "ลาคลอดได้ตามมาตรา 41",
            "citations": [{"section_no": "41", "quoted_text": "ลูกจ้างหญิงลาเพื่อคลอดบุตร"}],
            "facts_sufficient": True, "facts_to_verify": ["วันที่เริ่มทำงาน"]})]),
    ])
    s = harness.Session(store, chat_fn=lambda *a, **k: next(script))
    r = s.ask("ดิฉันชื่อ นางสาวทดลอง ใจดี ท้องอยู่ ลาคลอดได้กี่วัน")
    assert r["status"] == "answered", r["status"]
    assert [e["tool"] for e in r["trace"]] == ["search_law_sections", "submit_final_answer",
                                               "verify_citation", "submit_final_answer"]
    assert r["trace"][1]["result_sent_to_model"]["status"] == "rejected_by_policy"
    assert r["status_panel"]["ผลตรวจการอ้างอิง"] == "ผ่าน"
    assert "ทดลอง" not in r["question_masked"]
    print(json.dumps(r["status_panel"], ensure_ascii=False, indent=1))


def test_text_tool_call_nudge_and_budget():
    store, _, _ = make_store()
    seen = []
    script = iter([
        _resp(None, content='submit_final_answer(legal_issue="x", answer="...")'),
        _resp([_call(1, "search_law_sections", {"query": "ลาคลอด"})]),
        _resp([_call(2, "verify_citation", {"section_no": "41"})]),
        _resp([_call(3, "submit_final_answer", {"legal_issue": "ลาคลอด", "answer": "ดูมาตรา 41",
               "citations": [{"section_no": "41"}], "facts_sufficient": True, "facts_to_verify": []})]),
    ])

    def fake(messages, **k):
        seen.append(messages[-1]["content"] if messages[-1]["role"] == "user" else "")
        return next(script)
    r = harness.Session(store, chat_fn=fake).ask("ลาคลอดได้กี่วัน")
    assert r["status"] == "answered"
    assert any("เป็นข้อความ" in x for x in seen)          # เตือนว่าพิมพ์ tool เป็นข้อความ


def test_agent_gives_up_when_hallucinating():
    store, _, _ = make_store()
    bad = _resp([_call(9, "submit_final_answer", {
        "legal_issue": "x", "answer": "ตามมาตรา 500", "citations": [{"section_no": "500"}],
        "facts_sufficient": True, "facts_to_verify": []})])
    s = harness.Session(store, chat_fn=lambda *a, **k: bad)
    r = s.ask("คำถามทดสอบ")
    assert r["status"] == "blocked_by_policy"
    assert r["status_panel"]["ผลตรวจการอ้างอิง"].startswith("ไม่ผ่าน")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
