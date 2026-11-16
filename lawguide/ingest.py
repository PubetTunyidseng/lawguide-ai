"""แปลงตัวบทกฎหมาย (ข้อความจากหน้า krisdika) เป็นรายมาตรา + ตรวจฉบับแก้ไข แล้วลงฐานข้อมูล

ใช้:
    python -m lawguide.ingest --source data/raw/lpa2541.txt --retrieved 2026-11-15
    python -m lawguide.ingest --source ... --retrieved ... --no-index   # แค่ parse ไม่ลง store

ผลลัพธ์:
    data/processed/sections.jsonl   (1 บรรทัด = 1 มาตรา พร้อม metadata)
    data/processed/ingest_report.json (สรุปจำนวนมาตรา + คำเตือนเรื่องฉบับแก้ไข)
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path

from . import config

THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
SEC_NO = r"[0-9๐-๙]+(?:/[0-9๐-๙]+)?"
SECTION_HEAD = re.compile(rf"^\s*มาตรา\s*({SEC_NO})(?=\s|\[|$)")   # krisdika: "มาตรา ๔๑[๒๐]"
CHAPTER_HEAD = re.compile(r"^\s*((?:หมวด\s*[0-9๐-๙]+(?:/[0-9๐-๙]+)?|บทเฉพาะกาล|ข้อความเบื้องต้น).*)$")
FOOTNOTE_LINE = re.compile(r"^\s*\[([0-9๐-๙]+)\]\s*(.+)$")
FOOTNOTE_REF = re.compile(r"\[([0-9๐-๙]+)\]")
# จุดที่ตัวบทหลักจบ (ต่อจากนี้เป็นท้าย พ.ร.บ. / หมายเหตุ / ตัวบทของฉบับแก้ไขที่แนบท้าย)
END_OF_MAIN = re.compile(r"^\s*(ผู้รับสนองพระบรมราชโองการ|หมายเหตุ\s*[:：]?)")
APPENDED_ACT = re.compile(r"^\s*พระราชบัญญัติ.*\(ฉบับที่\s*[0-9๐-๙]+\)")


def to_arabic(s: str) -> str:
    return s.translate(THAI_DIGITS)


def parse_law_text(raw: str) -> dict:
    """คืน {sections: [...], footnotes: {n: text}, appendix_text: str, warnings: [...]}"""
    lines = raw.replace("\r\n", "\n").replace("\xa0", " ").split("\n")
    sections, footnotes, warnings = [], {}, []
    chapter, cur, main_done, want_title = "", None, False, False
    appendix = []

    for line in lines:
        fn = FOOTNOTE_LINE.match(line)
        if fn:                                   # เชิงอรรถอยู่ได้ทุกที่ เก็บแยกไว้ผูกกับมาตรา
            footnotes[to_arabic(fn.group(1))] = fn.group(2).strip()
            continue
        if main_done:
            appendix.append(line)
            continue
        if sections and (END_OF_MAIN.match(line) or APPENDED_ACT.match(line)):
            main_done = True
            appendix.append(line)
            continue
        ch = CHAPTER_HEAD.match(line)
        if ch:
            new = re.sub(r"\s+", " ", ch.group(1)).strip()
            if not chapter.startswith(new + " "):      # "หมวด ๓" ซ้ำหลัง "หมวด ๓ การใช้แรงงานหญิง" ไม่ต้องทับ
                chapter = new
                want_title = new.startswith("หมวด") and len(new.split()) <= 2
            continue
        if want_title and line.strip():                 # รูปแบบ "หมวด ๓" บรรทัดเดียว แล้วชื่อหมวดอยู่บรรทัดถัดไป
            want_title = False
            if not SECTION_HEAD.match(line):
                chapter = f"{chapter} {line.strip()}"
                continue
        m = SECTION_HEAD.match(line)
        if m:
            cur = {"section_no": to_arabic(m.group(1)), "chapter": chapter, "lines": [line.strip()]}
            sections.append(cur)
            continue
        if cur is not None and line.strip():
            cur["lines"].append(line.strip())

    seen, out = set(), []
    for s in sections:
        if s["section_no"] in seen:
            warnings.append(f"พบเลขมาตรา {s['section_no']} ซ้ำ — เก็บตัวแรกไว้")
            continue
        seen.add(s["section_no"])
        text = "\n".join(s["lines"])
        refs = [to_arabic(r) for r in FOOTNOTE_REF.findall(text)]
        out.append({"section_no": s["section_no"], "chapter": s["chapter"], "text": text, "footnote_refs": refs})
    return {"sections": out, "footnotes": footnotes, "appendix_text": "\n".join(appendix), "warnings": warnings}


def check_versions(sections: list, footnotes: dict, appendix_text: str, amendments: list,
                   today: date) -> tuple[dict, list]:
    """ตรวจว่าตัวบทมีร่องรอยฉบับแก้ไขล่าสุดหรือยัง

    คืน (flags_by_section, corpus_warnings)
    หลักการ: ฉบับแก้ไขที่ทะเบียนบอกว่าแก้มาตรา X ต้องมีเชิงอรรถของมาตรา X ที่อ้างถึง "(ฉบับที่ N)"
    ถ้าไม่มี = ตัวบทในฐานข้อมูลอาจเป็นฉบับเก่า → ติดธงไว้ทั้งที่มาตราและระดับคลัง
    """
    by_no = {s["section_no"]: s for s in sections}
    flags, corpus_warn = {}, []
    full_notes = " ".join(footnotes.values()) + " " + appendix_text
    for a in amendments:
        ordinal = str(a["ordinal"])
        marker = re.compile(rf"\(ฉบับที่\s*{ordinal}\)")
        eff = date.fromisoformat(a["effective"])
        not_yet = eff > today
        corpus_has_act = bool(marker.search(to_arabic(full_notes)))
        if not corpus_has_act:
            corpus_warn.append(f"ไม่พบร่องรอย {a['name']} (มีผล {a['effective']}) ในตัวบทที่นำเข้า — "
                               f"ตัวบทอาจเป็นฉบับก่อนแก้ไข ต้องตรวจกับราชกิจจานุเบกษา")
        for no in a["sections_new"]:
            if no not in by_no:
                corpus_warn.append(f"ไม่พบมาตรา {no} ที่ {a['name']} เพิ่มเข้ามา")
        for no in a["sections_changed"]:
            s = by_no.get(no)
            if s is None:
                continue
            notes = " ".join(footnotes.get(r, "") for r in s["footnote_refs"])
            if marker.search(to_arabic(notes)):
                status = "updated"
                msg = f"แก้ไขโดย {a['name']} แล้ว"
            else:
                status = "possibly_outdated"
                msg = (f"มาตรานี้ถูกแก้ไขโดย {a['name']} (ประกาศ {a['gazette_published']}, มีผล {a['effective']}) "
                       f"แต่ข้อความในฐานข้อมูลไม่มีร่องรอยการแก้ไขนั้น — อาจเป็นข้อความฉบับเก่า")
            if not_yet:
                msg += " (ฉบับแก้ไขยังไม่มีผลบังคับ ณ วันที่ตรวจ)"
            flags.setdefault(no, []).append({"status": status, "amendment": a["name"],
                                             "effective": a["effective"], "message": msg,
                                             "sources": a["sources"]})
    return flags, corpus_warn


def build_docs(raw_text: str, retrieved: str, today: date | None = None) -> tuple[list, dict]:
    today = today or date.today()
    parsed = parse_law_text(raw_text)
    amend = json.loads((config.DATA_DIR / "amendments.json").read_text(encoding="utf-8"))["amendments"]
    flags, corpus_warn = check_versions(parsed["sections"], parsed["footnotes"], parsed["appendix_text"],
                                        amend, today)
    docs = []
    for s in parsed["sections"]:
        notes = [parsed["footnotes"][r] for r in s["footnote_refs"] if r in parsed["footnotes"]]
        docs.append({
            "id": f"{config.LAW_ID}:{s['section_no']}",
            "law_id": config.LAW_ID,
            "law_name": config.LAW_NAME,
            "section_no": s["section_no"],
            "chapter": s["chapter"],
            "text": s["text"],
            "amendment_notes": notes,
            "version_flags": flags.get(s["section_no"], []),
            "source_url": config.SOURCE_URL,
            "official_url": config.OFFICIAL_URL,
            "source_retrieved": retrieved,
            "checked_on": today.isoformat(),
        })
    report = {"law": config.LAW_NAME, "source_url": config.SOURCE_URL, "source_retrieved": retrieved,
              "checked_on": today.isoformat(), "n_sections": len(docs), "n_footnotes": len(parsed["footnotes"]),
              "parse_warnings": parsed["warnings"], "corpus_version_warnings": corpus_warn,
              "flagged_sections": {k: [f["status"] for f in v] for k, v in flags.items()}}
    return docs, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=config.SOURCE_FILE)
    ap.add_argument("--retrieved", default=config.SOURCE_RETRIEVED,
                    help="วันที่ดาวน์โหลดตัวบท YYYY-MM-DD (จำเป็น — ใช้แสดงในทุกคำตอบ)")
    ap.add_argument("--no-index", action="store_true")
    args = ap.parse_args()
    if not args.retrieved:
        ap.error("ต้องระบุ --retrieved (วันที่ดาวน์โหลดตัวบท) เพื่อให้ทุกคำตอบแสดงวันที่ตรวจสอบข้อมูลได้")

    raw = Path(args.source).read_text(encoding="utf-8")
    docs, report = build_docs(raw, args.retrieved)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.PROCESSED_DIR / "sections.jsonl", "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    (config.PROCESSED_DIR / "ingest_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not args.no_index:
        from .store import get_store
        store = get_store()
        store.index(docs)
        print(f"indexed {len(docs)} sections into backend={config.BACKEND}")


if __name__ == "__main__":
    main()
