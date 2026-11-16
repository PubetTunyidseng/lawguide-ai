"""เทียบตัวบทสองแหล่งทีละมาตรา (ใช้ parser เดียวกับ ingest)

ใช้:  python scripts/compare_sources.py data/raw/lpa2541_drthawip.txt data/raw/lpa2541.txt
หลักการ: ถ้าสองแหล่งที่จัดทำแยกกันให้ข้อความตรงกันทุกมาตรา ความเสี่ยงที่ตัวบทในฐานข้อมูลผิดจะต่ำลงมาก
         มาตราไหนไม่ตรง จะพิมพ์ตำแหน่งแรกที่ต่างให้ดูด้วยตา
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lawguide.ingest import parse_law_text  # noqa: E402


def norm(s: str) -> str:
    s = re.sub(r"\[[0-9๐-๙]+\]", "", s)                       # เลขเชิงอรรถคนละระบบกัน
    s = s.replace("ํา", "ำ").replace("ํา", "ำ")  # สระอำที่ PDF แยกเป็นสองตัว
    return re.sub(r"\s+", "", s)


def main(a_path, b_path):
    A = {s["section_no"]: s for s in parse_law_text(Path(a_path).read_text(encoding="utf-8"))["sections"]}
    B = {s["section_no"]: s for s in parse_law_text(Path(b_path).read_text(encoding="utf-8"))["sections"]}
    only_a, only_b = sorted(set(A) - set(B)), sorted(set(B) - set(A))
    same, diff = [], []
    for no in sorted(set(A) & set(B), key=lambda x: [int(p) for p in x.split("/")]):
        (same if norm(A[no]["text"]) == norm(B[no]["text"]) else diff).append(no)
    print(f"A={a_path}: {len(A)} มาตรา   B={b_path}: {len(B)} มาตรา")
    print(f"ตรงกัน {len(same)} · ต่างกัน {len(diff)} · มีแค่ใน A {only_a} · มีแค่ใน B {only_b}")
    for no in diff:
        x, y = norm(A[no]["text"]), norm(B[no]["text"])
        i = next((k for k in range(min(len(x), len(y))) if x[k] != y[k]), min(len(x), len(y)))
        print(f"\n--- มาตรา {no} (ยาว {len(x)} vs {len(y)}) ต่างที่ตำแหน่ง {i}")
        print("  A:", x[max(0, i - 40):i + 60])
        print("  B:", y[max(0, i - 40):i + 60])


if __name__ == "__main__":
    main(*sys.argv[1:3])
