"""ตรวจทะเบียนฉบับแก้ไข (สร้างจากฉบับรวมกฤษฎีกา) กับไฟล์ราชกิจจานุเบกษาของแต่ละฉบับ

ใช้:  python scripts/verify_amendments.py          (ต้องมี pdftotext)
หลักการ: ทะเบียนมาจากแหล่งหนึ่ง (เชิงอรรถฉบับรวม) ราชกิจจาฯ เป็นอีกแหล่งที่จัดทำแยกกัน
         ถ้าเล่ม/ตอน/หน้า วันประกาศ และกติกาวันมีผล (มาตรา ๒) ตรงกัน = ทะเบียนเชื่อได้ในส่วนนั้น
ข้อจำกัด: PDF ราชกิจจาฯ บางฉบับใช้ฟอนต์เก่า ดึงข้อความไม่ได้ (เช่น ฉบับที่ ๙ ส่วนเนื้อความ) จะรายงานว่าตรวจไม่ได้
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TH = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
FILES = {2: "lpa_no2_2551.pdf", 5: "lpa_no5_2560.pdf", 6: "lpa_no6_2560.pdf",
         7: "lpa_no7_2562.pdf", 8: "lpa_no8_2566.pdf", 9: "lpa_no9_2568.pdf"}


PUA = str.maketrans({"": "่", "": "้", "": "๊", "": "๋", "": "์"})
PUA_REST = re.compile("[-]")             # PUA อื่นที่เหลือ = เครื่องหมายบน/ล่างรูปแบบอื่นของฟอนต์
MARKS = re.compile("[ัิ-ฺ็-๎]")   # สระบน/ล่าง วรรณยุกต์ การันต์


def squash(s):
    """โครงตัวอักษรสำหรับเทียบ: PDF ราชกิจจาฯ แต่ละยุคเข้ารหัสสระบน/วรรณยุกต์ต่างกัน
    (ฟอนต์ใช้ Private Use Area, สลับลำดับ "เลม่", สระอำหายเป็น "กระทา") จึงตัดเครื่องหมายบน/ล่างทิ้งทั้งหมด
    แล้วเทียบเฉพาะพยัญชนะ สระหลัก และตัวเลข — พอสำหรับยืนยันเล่ม/ตอน/หน้า และกติกาวันมีผล"""
    s = PUA_REST.sub("", s.translate(PUA)).translate(TH).replace("ำ", "า")
    return re.sub(r"\s+", "", MARKS.sub("", s))


def main():
    reg = {a["ordinal"]: a for a in json.loads((ROOT / "data/amendments.json").read_text(encoding="utf-8"))["amendments"]}
    for k, fname in FILES.items():
        a = reg.get(k)
        txt = subprocess.run(["pdftotext", str(ROOT / "data/sources/gazette" / fname), "-"],
                             capture_output=True, text=True).stdout
        s = squash(txt)
        m = re.search(r"เลม(\d+)ตอนท(\d+)กหนา(\d+)", s)
        ref_pdf = f"เล่ม {m.group(1)} ตอน {m.group(2)} ก หน้า {m.group(3)}" if m else None
        ref_ok = bool(m) and a and ref_pdf in a["gazette_ref"]
        rule_reg = squash("มาตรา ๒ " + a["effective_rule"]) if a else "?"
        rule = "มาตรา2พระราช" in s                       # มีมาตรา ๒ ที่อ่านออกไหม
        rule_ok = rule_reg in s
        print(f"ฉบับที่ {k}: ราชกิจจาฯ {ref_pdf or 'อ่านไม่ได้'} → {'✓' if ref_ok else '✗'} · "
              f"มาตรา ๒ {('✓' if rule_ok else '✗') if rule else 'อ่านไม่ได้ (ฟอนต์)'}")


if __name__ == "__main__":
    main()
