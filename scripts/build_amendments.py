"""สร้างทะเบียนฉบับแก้ไข (data/amendments.json) จากเชิงอรรถและภาคผนวกของฉบับรวมกฤษฎีกา

ใช้:  python scripts/build_amendments.py data/raw/lpa2541.txt

ได้อะไร: ฉบับแก้ไขทุกฉบับ + มาตราที่แต่ละฉบับแก้/เพิ่ม + วันประกาศราชกิจจาฯ + วันมีผล (คำนวณจากมาตรา ๒ ของฉบับนั้น)
ใช้ทำอะไร: ingest เอาทะเบียนนี้ไปตรวจว่าตัวบทที่นำเข้ามีร่องรอยการแก้ไขครบทุกฉบับไหม
           ถ้าวันหน้ามีฉบับที่ 10 ให้เพิ่มรายการเองจากราชกิจจาฯ ตัวบทเก่าที่ไม่มีร่องรอยฉบับที่ 10 จะถูกติดธงทันที
ข้อจำกัด: ทะเบียนที่ได้จากตัวบทฉบับไหน ย่อมตรวจไม่เจอการแก้ไขที่ฉบับนั้นเองยังไม่รู้ ต้องเทียบกับราชกิจจาฯ เป็นระยะ
"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

TH = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
     "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"])}
NUM_WORDS = {"สามสิบ": 30, "เก้าสิบ": 90, "หนึ่งร้อยแปดสิบ": 180, "สองร้อยสี่สิบ": 240,
             "หกสิบ": 60, "หนึ่งร้อยยี่สิบ": 120, "สิบห้า": 15}


def gazette(s: str):
    """'ราชกิจจานุเบกษา เล่ม ๑๔๒/ตอนที่ ๗๔ ก/หน้า ๔๑/๗ พฤศจิกายน ๒๕๖๘' -> (ref, date)"""
    m = re.search(r"เล่ม\s*([๐-๙]+)/ตอน(?:ที่|พิเศษ)\s*([^/]+)/หน้า\s*([๐-๙]+)/([๐-๙]+)\s*(\S+)\s*([๐-๙]+)", s)
    if not m:
        return None, None
    vol, part, page, d, mon, y = m.groups()
    ref = f"ราชกิจจานุเบกษา เล่ม {vol.translate(TH)} ตอน {part.strip().translate(TH)} หน้า {page.translate(TH)}"
    return ref, date(int(y.translate(TH)) - 543, MONTHS[mon], int(d.translate(TH)))


def effective(published: date, rule: str):
    if published is None:
        return None
    m = re.search(r"พ้นกำหนด(\S+?)วัน", rule)          # ข้อความหลักก่อน ส่วน "เว้นแต่..." เป็นข้อยกเว้นบางมาตรา
    if m and m.group(1) in NUM_WORDS:
        return published + timedelta(days=NUM_WORDS[m.group(1)])
    if "วันถัดจากวันประกาศ" in rule:
        return published + timedelta(days=1)
    return None


def build(text: str) -> list:
    foot = dict(re.findall(r"^\[(\d+)\] (.+)$", text, re.M))
    acts = {}
    for n, s in foot.items():
        sec = re.search(r"มาตรา\s*([๐-๙]+(?:/[๐-๙]+)?)", s)
        act = re.search(r"คุ้มครองแรงงาน\s*\(ฉบับที่\s*([๐-๙]+)\)\s*พ\.ศ\.\s*([๐-๙]+)", s)
        if not (sec and act):
            continue
        k = int(act.group(1).translate(TH))
        a = acts.setdefault(k, {"ordinal": k, "year_be": int(act.group(2).translate(TH)),
                                "sections_changed": [], "sections_new": []})
        no = sec.group(1).translate(TH)
        if no not in a["sections_changed"]:
            a["sections_changed"].append(no)
        # มาตราใหม่ทั้งมาตรา = "มาตรา X เพิ่มโดย ..." ตรง ๆ (ถ้ามี วรรค/อนุมาตรา/นิยาม คั่น = เพิ่มแค่บางส่วน)
        if re.match(r"มาตรา\s*[๐-๙/]+\s*เพิ่ม(?:เติม)?โดย", s) and no not in a["sections_new"]:
            a["sections_new"].append(no)

    out = []
    appendix = text[text.find("ผู้รับสนองพระบรมราชโองการ"):]
    for m in re.finditer(r"(พระราชบัญญัติ[^\n\[]{0,120})\[(\d+)\]", appendix):
        head, fn = m.group(1), m.group(2)
        rule_m = re.search(r"มาตรา ๒ ([^\n]+)", appendix[m.end():m.end() + 600])
        rule = rule_m.group(1) if rule_m else ""
        ref, pub = gazette(foot.get(fn, ""))
        eff = effective(pub, rule)
        lpa = re.search(r"คุ้มครองแรงงาน\s*\(ฉบับที่\s*([๐-๙]+)\)", head)
        if lpa:
            k = int(lpa.group(1).translate(TH))
            a = acts.get(k, {"ordinal": k, "sections_changed": [], "sections_new": []})
            out.append({"name": f"พระราชบัญญัติคุ้มครองแรงงาน (ฉบับที่ {k}) พ.ศ. {a.get('year_be', '')}".strip(),
                        "ordinal": k, "marker": rf"\(ฉบับที่\s*{k}\)",
                        "gazette_ref": ref, "gazette_published": pub and pub.isoformat(),
                        "effective": eff and eff.isoformat(), "effective_rule": rule,
                        "sections_changed": a["sections_changed"], "sections_new": a["sections_new"]})
        elif "ปรับเป็นพินัย" in head:
            secs = sorted({x.translate(TH) for x in re.findall(
                r"^มาตรา\s*([๐-๙]+(?:/[๐-๙]+)?)[^\n]*มีความผิดทางพินัย", text, re.M)},
                key=lambda x: [int(p) for p in x.split("/")])
            out.append({"name": "พระราชบัญญัติว่าด้วยการปรับเป็นพินัย พ.ศ. 2565", "ordinal": None,
                        "marker": "พินัย", "gazette_ref": ref, "gazette_published": pub and pub.isoformat(),
                        "effective": eff and eff.isoformat(), "effective_rule": rule[:200],
                        "sections_changed": secs, "sections_new": [],
                        "note": "ไม่ได้แก้ข้อความ พ.ร.บ.คุ้มครองแรงงานโดยตรง แต่มาตรา 39 เปลี่ยนโทษปรับอาญาสถานเดียวเป็นค่าปรับเป็นพินัย "
                                "(effective = วันมีผลทั่วไป ตามมาตรา 2 มีข้อยกเว้นบางมาตรา)"})
    return sorted(out, key=lambda a: (a["gazette_published"] or ""))


def main(src):
    text = Path(src).read_text(encoding="utf-8")
    acts = build(text)
    reg = {"law_id": "lpa2541",
           "note": "สร้างอัตโนมัติด้วย scripts/build_amendments.py จากเชิงอรรถ/ภาคผนวกของฉบับรวมกฤษฎีกา "
                   "วันมีผลคำนวณจากมาตรา 2 ของแต่ละฉบับ (วันประกาศ + จำนวนวันที่กำหนด) — ตรวจกับราชกิจจาฯ ฉบับจริงก่อนอ้างอิง",
           "source": src, "amendments": acts}
    out = Path(__file__).resolve().parent.parent / "data" / "amendments.json"
    out.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for a in acts:
        print(f"{a['name']:<58} ประกาศ {a['gazette_published']} มีผล {a['effective']} "
              f"แก้ {len(a['sections_changed'])} มาตรา (ใหม่ {len(a['sections_new'])})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/lpa2541.txt")
