"""ดึง "ปริมาณ" (ตัวเลข + หน่วย) จากข้อความภาษาไทย เพื่อตรวจว่าตัวเลขในคำตอบมีอยู่จริงในมาตราที่อ้าง

ตัวบทเขียนตัวเลขเป็นคำ ("หนึ่งร้อยยี่สิบวัน", "หนึ่งเท่าครึ่ง") ส่วนโมเดลมักเขียนเป็นเลข ("120 วัน", "1.5 เท่า")
จึงแปลงทั้งสองฝั่งให้อยู่ในรูปเดียวกันก่อนเทียบ: (ค่า, หน่วย) เช่น (120.0, "วัน")

ตั้งใจให้จับเฉพาะตัวเลขที่มีหน่วยกำกับ เลขข้อ (1) (2) เลขมาตรา และปี พ.ศ. ไม่นับ
หน่วยและคำพ้อง (alias) กำหนดใน data/citation_policy.json ที่กฎ NUMBER_GROUNDED
"""
import re

THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

_DIGIT = {"ศูนย์": 0, "หนึ่ง": 1, "เอ็ด": 1, "สอง": 2, "ยี่": 2, "สาม": 3, "สี่": 4,
          "ห้า": 5, "หก": 6, "เจ็ด": 7, "แปด": 8, "เก้า": 9}
_MULT = {"สิบ": 10, "ร้อย": 100, "พัน": 1000, "หมื่น": 10_000, "แสน": 100_000, "ล้าน": 1_000_000}
_TOK = "|".join(sorted(list(_DIGIT) + list(_MULT), key=len, reverse=True))
NUMWORD = rf"(?:{_TOK})+"
NUM = rf"(?:\d[\d,]*(?:\.\d+)?|{NUMWORD})"

DEFAULT_UNITS = ["วันทำงาน", "วัน", "สัปดาห์", "เดือน", "ปี", "ชั่วโมง", "นาที", "ครั้ง", "เท่า", "คน", "บาท", "นาฬิกา"]
DEFAULT_ALIASES = {"วันทำงาน": "วัน"}

# ส่วนที่ไม่ใช่ปริมาณ ตัดทิ้งก่อน: เลขมาตรา ปี พ.ศ. ฉบับที่ เลขเชิงอรรถ
_STRIP = [r"มาตรา\s*\d+(?:/\d+)?", r"พ\.ศ\.\s*\d+", r"ฉบับที่\s*\d+", r"\[\d+\]",
          rf"วรรค\s*(?:{NUMWORD}|\d+)", r"\(\d+\)"]


def words_to_num(s: str) -> float:
    """'หนึ่งร้อยยี่สิบ' -> 120, 'สิบห้า' -> 15, 'เก้าสิบแปด' -> 98"""
    big, total, cur = 0, 0, None
    for t in re.findall(_TOK, s):
        if t in _DIGIT:
            cur = _DIGIT[t]
        elif t == "ล้าน":
            big = (big + total + (cur or 0)) * 1_000_000
            total, cur = 0, None
        else:
            total += (1 if cur is None else cur) * _MULT[t]
            cur = None
    return float(big + total + (cur or 0))


def to_num(s: str) -> float:
    s = s.replace(",", "")
    return float(s) if re.fullmatch(r"\d+(?:\.\d+)?", s) else words_to_num(s)


def _blank(text: str, m) -> str:
    return text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]


def quantities(text: str, units=None, aliases=None) -> list:
    """คืน list ของ {"value", "unit", "span"} ตามลำดับที่พบ"""
    units = sorted(units or DEFAULT_UNITS, key=len, reverse=True)
    aliases = DEFAULT_ALIASES if aliases is None else aliases
    t = (text or "").translate(THAI_DIGITS)
    for p in _STRIP:
        t = re.sub(p, lambda m: " " * len(m.group(0)), t)
    out = []

    def take(pattern, make):
        nonlocal t
        for m in list(re.finditer(pattern, t)):
            out.append({**make(m), "span": m.group(0).strip()})
        t = re.sub(pattern, lambda m: " " * len(m.group(0)), t)

    # เวลา 22.00 / 06:00 (น. / นาฬิกา ไม่บังคับ)
    take(r"(?<![\d.,])(\d{1,2})[.:](\d{2})(?!\d)\s*(?:นาฬิกา|น\.)?",
         lambda m: {"value": int(m.group(1)) + int(m.group(2)) / 100, "unit": "นาฬิกา"})
    # ร้อยละ N / N% / N เปอร์เซ็นต์
    take(rf"ร้อยละ\s*({NUM})", lambda m: {"value": to_num(m.group(1)), "unit": "%"})
    take(rf"({NUM})\s*(?:%|เปอร์เซ็นต์)", lambda m: {"value": to_num(m.group(1)), "unit": "%"})
    # หนึ่งเท่าครึ่ง / 1 เท่าครึ่ง = 1.5 เท่า
    take(rf"({NUM})\s*เท่าครึ่ง", lambda m: {"value": to_num(m.group(1)) + 0.5, "unit": "เท่า"})
    # ตัวเลข + หน่วย
    unit_re = "|".join(re.escape(u) + ("(?!กับ)" if u == "เท่า" else "") for u in units)   # "เท่ากับ" ไม่ใช่หน่วย
    take(rf"({NUM})\s*({unit_re})",
         lambda m: {"value": to_num(m.group(1)), "unit": aliases.get(m.group(2), m.group(2))})
    return out


def quantity_set(text: str, units=None, aliases=None) -> set:
    return {(round(q["value"], 2), q["unit"]) for q in quantities(text, units, aliases)}
