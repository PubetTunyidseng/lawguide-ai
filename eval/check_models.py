"""ตรวจก่อนรัน eval ว่าโมเดลที่จะใช้มีอยู่จริงบน OpenRouter และรองรับพารามิเตอร์ที่ระบบส่งไป

ทำไมต้องตรวจ: เราตั้ง provider.require_parameters=true ใน LLM_EXTRA_JSON ถ้าโมเดลไม่รองรับพารามิเตอร์ใด
(เช่น tools, temperature, frequency_penalty) OpenRouter จะหา provider ไม่ได้ และ eval จะล้มกลางทาง

รัน:  python eval/check_models.py qwen/qwen3-235b-a22b-2507 openai/gpt-4.1-mini --judge google/gemini-2.5-flash
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lawguide import config  # noqa: E402


def fetch_models():
    req = urllib.request.Request(config.LLM_BASE_URL.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {config.LLM_API_KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return {m["id"]: m for m in json.load(r)["data"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agents", nargs="+", help="โมเดลที่ใช้เป็น agent (ต้องรองรับ tools)")
    ap.add_argument("--judge", default="", help="โมเดล judge (ไม่ต้องใช้ tools)")
    args = ap.parse_args()
    if "openrouter.ai" not in config.LLM_BASE_URL:
        print(f"ข้ามการตรวจ: LLM_BASE_URL ไม่ใช่ OpenRouter ({config.LLM_BASE_URL})")
        return
    models = fetch_models()
    extra = [k for k in config.LLM_EXTRA if k != "provider"]
    ok = True
    for mid, need in [(m, ["tools", "tool_choice", "temperature"]) for m in args.agents] + \
                     ([(args.judge, ["temperature"])] if args.judge else []):
        m = models.get(mid)
        if not m:
            print(f"✗ {mid}: ไม่พบบน OpenRouter")
            ok = False
            continue
        sup = set(m.get("supported_parameters") or [])
        missing = [p for p in need + extra if p not in sup]
        p = m.get("pricing", {})
        price = f"${float(p.get('prompt', 0)) * 1e6:.2f} / ${float(p.get('completion', 0)) * 1e6:.2f} ต่อล้าน token (in/out)"
        if missing:
            ok = False
            print(f"✗ {mid}: ไม่รองรับ {missing}  ({price})")
        else:
            print(f"✓ {mid}: รองรับ {need + extra}  ({price})")
    if not ok:
        print("\nเลือกโมเดลอื่น หรือเอาพารามิเตอร์ที่ไม่รองรับออกจาก LLM_EXTRA_JSON ใน .env แล้วรันใหม่")
        sys.exit(1)


if __name__ == "__main__":
    main()
