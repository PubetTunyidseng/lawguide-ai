"""แปลง PDF ฉบับรวมของสำนักงานคณะกรรมการกฤษฎีกา (พิมพ์จาก krisdika.go.th) เป็นข้อความสำหรับ ingest

ใช้:  python scripts/krisdika_pdf_to_text.py data/sources/krisdika_lpa2541_2568-12-11.pdf data/raw/lpa2541.txt 2026-11-16
ต้องมี pdftotext (poppler) — Mac: brew install poppler

หลักการ (ดูจากระยะย่อหน้าในโหมด -layout):
  ย่อหน้า ~29+ ช่อง = ต้นย่อหน้า/ต้นมาตรา   ย่อหน้า ~17 ช่อง = บรรทัดที่ถูกตัดมาจากบรรทัดก่อน
  ภาษาไทยตัดบรรทัดระหว่างคำโดยไม่มีช่องว่าง จึงต่อบรรทัดที่ถูกตัดกลับโดยไม่เติมช่องว่าง
  เชิงอรรถ [N] ที่ยาวเกินบรรทัดก็ต่อแบบเดียวกัน (ไม่งั้นข้อความ "(ฉบับที่ ๒)" จะหลุดไปอีกบรรทัด)
"""
import re
import subprocess
import sys

CONT_MAX_INDENT = 25          # ย่อหน้าน้อยกว่านี้ถือเป็นบรรทัดต่อ
STRAY_MIN_INDENT = 30         # บรรทัดที่อยู่ลึกกว่านี้ และทุกท่อนสั้นไม่เกิน STRAY_MAX_LEN ตัว = เศษที่หลุดมา
STRAY_MAX_LEN = 3
JUNK = [
    re.compile(r"^\d{1,2}/\d{1,2}/\d{2}\s+\d{1,2}:\d{2}"),            # หัวกระดาษ วันเวลาที่พิมพ์
    re.compile(r"^สำนักงานคณะกรรมการกฤษฎีกา$"),
    re.compile(r"^about:blank"),                                       # ท้ายกระดาษ
    re.compile(r"^\d+/\d+$"),                                          # เลขหน้า
    re.compile(r"/\s*(Maker|Checker|Authorizer)\s*$"),                 # ผู้จัดทำ/ตรวจ
    re.compile(r"^(ที่\s*)+$"),                                        # ลายน้ำ
]
FOOTNOTE = re.compile(r"^\[\d+\]")
NEVER_JOIN = re.compile(r"^(ผู้รับสนองพระบรมราชโองการ|หมายเหตุ|อัตราค่าธรรมเนียม|พระราชบัญญัติ.*\(ฉบับที่)")
DATE_AFTER_SIGN = re.compile(r"^\d{1,2}\s+\S+\s+25\d\d$")


UPPER_MARK = re.compile("[\u0e31\u0e34-\u0e37\u0e47-\u0e4c]")   # สระบน/วรรณยุกต์


def is_stray(s: str, indent: int) -> bool:
    """เศษอักษรที่ PDF วางลอยไว้ เช่น "ที่   กี่   รื่" — ทุกท่อนสั้นและมีสระบน/วรรณยุกต์
    บรรทัดต่อที่มีคำเดียวอย่าง "นี้" (ย่อหน้าปกติ) ยังเก็บไว้ เพราะเป็นท้ายคำจริงที่ถูกตัดบรรทัด
    บรรทัดสั้นที่ไม่มีสระบน เช่น "(๑) ตาย" "(๒) เพศ" ไม่ใช่เศษ"""
    toks = s.split()
    if not toks or not all(len(t) <= STRAY_MAX_LEN and UPPER_MARK.search(t) for t in toks):
        return False
    return indent >= STRAY_MIN_INDENT or bool(re.search(r"\S {3,}\S", s))


def convert(pdf_path: str) -> str:
    raw = subprocess.run(["pdftotext", "-layout", pdf_path, "-"], check=True,
                         capture_output=True, text=True).stdout
    out, prev_sign = [], False
    for line in raw.replace("\f", "\n").split("\n"):
        s = line.strip()
        if not s:
            out.append("")
            continue
        # ตัดหัว/ท้ายกระดาษที่อยู่บรรทัดเดียวกับข้อความอื่น เช่น "about:blank      12/52"
        if any(p.search(s) for p in JUNK) or re.fullmatch(r"about:blank\s+\d+/\d+", s):
            prev_sign = bool(re.search(r"(Maker|Checker|Authorizer)", s))
            continue
        if prev_sign and DATE_AFTER_SIGN.match(s):
            prev_sign = False
            continue
        prev_sign = False
        indent = len(line) - len(line.lstrip(" "))
        # เศษตัวอักษรที่ PDF วางลอยไว้กลางหน้า เช่น "ที่" "ลื่" "ซึ่" (สระบน/วรรณยุกต์ที่ล้นจากบรรทัดอื่น)
        if is_stray(s, indent):
            continue
        is_cont = indent <= CONT_MAX_INDENT and not FOOTNOTE.match(s) and not NEVER_JOIN.match(s) and indent > 0
        if is_cont:
            j = len(out) - 1
            while j >= 0 and out[j] == "":
                j -= 1
            if j >= 0:
                out[j] = out[j] + s
                del out[j + 1:]
                continue
        out.append(s)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    header = ("แหล่งข้อมูล: สำนักงานคณะกรรมการกฤษฎีกา ฉบับรวม (Maker 8 ธ.ค. 2568 · Checker/Authorizer 9 ธ.ค. 2568 · "
              "พิมพ์ 11/12/68) https://www.krisdika.go.th/librarian/get?sysid=642571&ext=htm\n")
    retrieved = sys.argv[3] if len(sys.argv) > 3 else ""
    body = convert(src)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(header + (f"ดึงข้อมูล: {retrieved}\n" if retrieved else "") + "\n" + body)
    print(f"wrote {dst} ({len(body):,} chars)")
