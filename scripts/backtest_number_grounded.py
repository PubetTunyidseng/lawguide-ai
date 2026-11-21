"""ทดสอบกฎ NUMBER_GROUNDED ย้อนหลังกับคำตอบจริงจาก eval รอบก่อน ๆ (ไม่เรียก LLM)

คำถามที่ต้องตอบก่อนเปิดกฎเป็น block: กฎนี้จะตีกลับคำตอบที่ถูกอยู่แล้วบ่อยแค่ไหน (false positive)
และจับคำตอบที่อ้างมาตราไม่รองรับตัวเลขได้จริงไหม

รัน:  python scripts/backtest_number_grounded.py v3-bm25 v3-hybrid
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lawguide.policy import RULES  # noqa: E402
from lawguide.quantities import quantities, quantity_set  # noqa: E402

rule = RULES["NUMBER_GROUNDED"]
U, A = rule.get("units"), rule.get("unit_aliases")
SECTIONS = {d["section_no"]: d["text"] for d in
            (json.loads(l) for l in (ROOT / "data/processed/sections.jsonl").read_text(encoding="utf-8").splitlines())}
AGENCY = json.loads((ROOT / "data/agencies.json").read_text(encoding="utf-8"))["procedures"]
TESTS = {t["id"]: t for t in (json.loads(l) for l in (ROOT / "eval/testset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}

for tag in sys.argv[1:] or ["v3-bm25", "v3-hybrid"]:
    rows = [json.loads(l) for l in (ROOT / f"eval/results/{tag}/raw_runs.jsonl").read_text(encoding="utf-8").splitlines()]
    answered = [r for r in rows if r["behavior"] == "answer" and r["answer"]]
    flagged = []
    for r in answered:
        t = TESTS[r["id"]]
        pool = quantity_set(t["question"], U, A)
        for no in r["cited"]:
            pool |= quantity_set(SECTIONS.get(no, ""), U, A)
        if "find_agency_procedure" in r["tools_used"]:
            pool |= quantity_set(json.dumps(AGENCY.get(t["issue_type"], {}), ensure_ascii=False), U, A)
        missing = [q["span"] for q in quantities(r["answer"], U, A) if (round(q["value"], 2), q["unit"]) not in pool]
        if missing:
            flagged.append((r["id"], r["run"], r["cited"], missing, r["answer"][:160]))
    print(f"\n== {tag}: คำตอบที่มีการอ้าง {len(answered)} คำตอบ · กฎนี้จะตีกลับ {len(flagged)} คำตอบ")
    for f in flagged:
        print(f"  {f[0]} run{f[1]} cited={f[2]} ไม่พบ={f[3]}\n     {f[4]}")
