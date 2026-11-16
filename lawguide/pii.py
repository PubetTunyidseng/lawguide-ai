"""PII guard: ตรวจ → ตัดสินใจด้วย decision table → mask ก่อนส่งข้อความไป LLM

ขั้นที่ 1 detector แบบ rule (regex + checksum เลขบัตร) — ทำงานในเครื่อง ไม่ส่งข้อมูลออก
ขั้นที่ 2 (เลือกได้) ให้ LLM ช่วย extract ชื่อคน/ที่อยู่/ข้อมูลสุขภาพ ที่ regex จับไม่ได้
          ควรชี้ไปที่ LLM ที่รันในเครื่อง (PII_LLM_BASE_URL) เพราะถ้าส่งข้อความดิบไป API ภายนอก = ส่ง PII ออกไปแล้ว
ขั้นที่ 3 decision table (data/pii_decision_rules.json) ตัดสิน mask/keep/block และเตือนหรือไม่

ข้อความต้นฉบับ + ตาราง placeholder อยู่ในหน่วยความจำเท่านั้น ไม่ลง log
"""
import json
import os
import re

from . import config

DECISION = json.loads(open(config.PII_RULES_FILE, encoding="utf-8").read())["rules"]

# คำที่ขึ้นต้นด้วย "นาย/นาง" แต่ไม่ใช่คำนำหน้าชื่อ
NOT_NAME_AFTER_NAI = r"(?!จ้าง|ก|หน้า|ทุน|ทะเบียน|อำเภอ|ตรวจ|ประกัน|ช่าง|ร้อย|พัน|พล|ดาบ|สิบ|แพทย์|ด่าน|ท้าย|งาน)"
NOT_NAME_AFTER_NANG = r"(?!ฟ้า|งาม|พยาบาล|แบบ|รำ)"
PATTERNS = [
    ("thai_id", re.compile(r"(?<![0-9])[1-8][\s-]?\d{4}[\s-]?\d{5}[\s-]?\d{2}[\s-]?\d(?![0-9])")),
    ("bank_account", re.compile(r"(?:เลขบัญชี|บัญชี(?:เลขที่)?)\s*[:：]?\s*(\d{3}[\s-]?\d[\s-]?\d{5}[\s-]?\d)")),
    ("phone", re.compile(r"(?<![0-9])0[2-9]\d?[\s-]?\d{3}[\s-]?\d{4}(?![0-9])")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("person_name", re.compile(
        rf"(?:นางสาว|น\.ส\.|ด\.ช\.|ด\.ญ\.|นาย{NOT_NAME_AFTER_NAI}|นาง{NOT_NAME_AFTER_NANG}|Mr\.|Mrs\.|Ms\.)\s*"
        r"[ก-๛A-Za-z]{2,}(?:\s+[ก-๛A-Za-z]{2,})?")),
]


def thai_id_valid(raw: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", raw)]
    if len(d) != 13:
        return False
    s = sum(d[i] * (13 - i) for i in range(12))
    return (11 - s % 11) % 10 == d[12]


def rule_detect(text: str) -> list:
    found = []
    for cat, pat in PATTERNS:
        for m in pat.finditer(text):
            g = 1 if pat.groups else 0
            span, val = m.span(g), m.group(g)
            item = {"category": cat, "detector": "rule", "start": span[0], "end": span[1], "text": val}
            if cat == "thai_id":
                item["checksum_ok"] = thai_id_valid(val)
            found.append(item)
    return found


LLM_PROMPT = """หาข้อมูลส่วนบุคคลในข้อความต่อไปนี้ ตอบเป็น JSON array เท่านั้น
แต่ละรายการ: {"text": "<ข้อความที่ตรงตัวในต้นฉบับ>", "category": "person_name|address|organization|health_info|other_sensitive"}
- person_name = ชื่อหรือชื่อเล่นของบุคคลจริง (ไม่ใช่คำว่า นายจ้าง/ลูกจ้าง/หัวหน้า)
- ไม่พบให้ตอบ []
ข้อความ:
"""


def llm_detect(text: str) -> list:
    from .llm import chat_plain
    base = os.environ.get("PII_LLM_BASE_URL", config.LLM_BASE_URL)
    model = os.environ.get("PII_LLM_MODEL", config.LLM_MODEL)
    try:
        out = chat_plain(LLM_PROMPT + text, model=model, base_url=base, temperature=0)
        arr = json.loads(re.search(r"\[.*\]", out, re.S).group(0))
    except Exception as e:                       # extractor ล้ม ≠ ระบบล้ม: ใช้ผล rule อย่างเดียว
        return [{"category": "_error", "detector": "llm", "text": str(e)[:200]}]
    found = []
    for it in arr:
        val = str(it.get("text", "")).strip()
        if not val:
            continue
        for m in re.finditer(re.escape(val), text):
            found.append({"category": it.get("category", "other_sensitive"), "detector": "llm",
                          "start": m.start(), "end": m.end(), "text": val})
    return found


def decide(item: dict) -> dict:
    for r in DECISION:                           # hit policy FIRST
        if r["category"] in (item["category"], "*") and r["detector"] in (item["detector"], "*"):
            return r
    return {"action": "mask", "warn": True, "placeholder": "ข้อมูลอ่อนไหว", "reason": "default"}


def guard(text: str, use_llm: bool | None = None) -> dict:
    use_llm = config.PII_LLM_EXTRACT if use_llm is None else use_llm
    items = rule_detect(text)
    errors = []
    if use_llm:
        for it in llm_detect(text):
            (errors if it["category"] == "_error" else items).append(it)

    # ตัดช่วงซ้อนกัน: เอาช่วงที่ยาวกว่า/เจอก่อน
    items.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))
    chosen, last_end = [], -1
    for it in items:
        if it["start"] >= last_end:
            chosen.append(it)
            last_end = it["end"]

    counters, mapping, decisions, blocked = {}, {}, [], False
    pieces, cursor = [], 0
    for it in chosen:
        rule = decide(it)
        decisions.append({"category": it["category"], "detector": it["detector"], "action": rule["action"],
                          "warn": rule.get("warn", False), "reason": rule.get("reason", ""),
                          **({"checksum_ok": it["checksum_ok"]} if "checksum_ok" in it else {})})
        if rule["action"] == "block":
            blocked = True
        if rule["action"] in ("mask", "block"):
            key = (it["category"], it["text"])
            if key not in mapping:
                counters[rule["placeholder"]] = counters.get(rule["placeholder"], 0) + 1
                mapping[key] = f"[{rule['placeholder']}_{counters[rule['placeholder']]}]"
            pieces.append(text[cursor:it["start"]])
            pieces.append(mapping[key])
            cursor = it["end"]
    pieces.append(text[cursor:])
    masked = "".join(pieces)
    warnings = sorted({f"พบ{d['category']} — {d['reason']}" for d in decisions if d["warn"]})
    return {"masked_text": masked, "blocked": blocked, "decisions": decisions, "warnings": warnings,
            "n_masked": sum(1 for d in decisions if d["action"] in ("mask", "block")),
            "extractor_errors": errors}
