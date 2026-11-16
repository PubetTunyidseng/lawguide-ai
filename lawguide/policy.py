"""ตรวจคำตอบสุดท้ายแบบ deterministic ก่อนแสดงผู้ใช้ (กันการสร้างเลขมาตราที่ไม่มีจริง)

กฎอยู่ใน data/citation_policy.json — เปิด/ปิด/เปลี่ยน severity ได้โดยไม่แก้โค้ด
"""
import json
import re

from . import config
from .store import norm_section_no
from .tools import _norm_text

RULES = {r["id"]: r for r in json.loads(
    (config.DATA_DIR / "citation_policy.json").read_text(encoding="utf-8"))["rules"]}
MENTION = re.compile(r"มาตรา\s*([0-9๐-๙]+(?:/[0-9๐-๙]+)?)")


def mentioned_sections(text: str) -> list:
    return sorted({norm_section_no(m) for m in MENTION.findall(text or "")})


def check_final(final: dict, executor) -> list:
    """คืน list ของ {rule, severity, section_no, message}"""
    out = []

    def add(rule_id, msg, sec=None):
        r = RULES.get(rule_id)
        if r and r["enabled"]:
            out.append({"rule": rule_id, "severity": r["severity"], "section_no": sec, "message": msg})

    store, seen = executor.store, executor.seen_sections
    cited = [norm_section_no(c.get("section_no", "")) for c in final.get("citations", [])]
    for c, no in zip(final.get("citations", []), cited):
        doc = store.get(no)
        if not doc:
            add("CITE_EXISTS", f"มาตรา {no} ไม่มีอยู่ในฐานข้อมูล", no)
            continue
        if no not in seen:
            add("CITE_IN_CONTEXT", f"มาตรา {no} ไม่ได้ถูกดึงมาเป็น context ในรอบนี้", no)
        q = (c.get("quoted_text") or "").strip()
        if q and _norm_text(q) not in _norm_text(doc["text"]):
            add("QUOTE_MATCHES", f"ข้อความที่ยกจากมาตรา {no} ไม่ตรงกับตัวบท", no)
        for f in doc.get("version_flags", []):
            if f["status"] != "updated":
                add("VERSION_FLAG", f"มาตรา {no}: {f['message']}", no)

    for no in mentioned_sections(final.get("answer", "")):
        if not store.get(no):
            add("ANSWER_MENTIONS_GROUNDED", f"คำตอบเขียนถึงมาตรา {no} ซึ่งไม่มีในฐานข้อมูล", no)
        elif no not in seen:
            add("ANSWER_MENTIONS_GROUNDED", f"คำตอบเขียนถึงมาตรา {no} โดยไม่ได้ดึงตัวบทมาในรอบนี้", no)

    if final.get("facts_sufficient") and not cited:
        add("HAS_CITATION", "คำตอบบอกว่าข้อเท็จจริงเพียงพอ แต่ไม่มีการอ้างมาตราใดเลย")
    return out


def blocking(violations: list) -> list:
    return [v for v in violations if v["severity"] == "block"]
