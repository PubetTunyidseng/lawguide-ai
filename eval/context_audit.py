"""ตรวจว่า context ที่ระบบ "บันทึกว่าส่งให้โมเดลแล้ว" ตรงกับที่โมเดล "เห็นจริง" ไหม — อ่าน log อย่างเดียว ไม่แก้ระบบ

ที่มาของปัญหา (พบ 21 พ.ย. 2569 ตอนเตรียมรีวิว)
  1. harness ตัดผลของ tool ทุกครั้งที่ TOOL_RESULT_MAX_CHARS ตัวอักษร: json.dumps(out)[:N] ก่อนใส่ในบทสนทนา
     แต่งบต่อมาตราใน tools.py คิดจากตัวบทอย่างเดียว ไม่นับ metadata และ cited_by ที่แนบไปด้วย
  2. ToolExecutor จำมาตราไว้ใน seen_sections "ก่อน" การตัด และ verify_citation ก็จำมาตราไว้ด้วย
     ทั้งที่ verify_citation ไม่ได้ส่งตัวบทให้โมเดลเลย
  ผล: context_returned ใน log, policy CITE_IN_CONTEXT และ trajectory.py ใช้ชุด "ก่อนตัด" ทั้งหมด

ไฟล์นี้สร้างข้อความที่ส่งให้โมเดลจริงขึ้นใหม่จาก log (การตัดเป็น deterministic จึงสร้างซ้ำได้ตรงตัว)
แล้ววัดทีละมาตราในผลของ tool ว่าโมเดลเห็นแค่ไหน
  full               เห็นทั้งก้อน
  text_ok_meta_cut   ตัวบทครบ แต่ metadata ท้าย (cited_by ฯลฯ) ถูกตัด
  text_partial       ตัวบทถูกตัดกลางข้อความ
  lost               ไม่เห็นมาตรานี้เลย
หมายเหตุ: ข้อความย่อใน cited_by (160 ตัวอักษร) และผล verify_citation ไม่นับเป็น "ได้รับตัวบท"
และแยก "ตัวบทถูกตัดด้วยงบต่อมาตรา" (เช่น 6000/5 = 1200 ตัวอักษร) ออกมาอีกชั้น

รัน:  python eval/context_audit.py qwen3 qwen3-v2 v3-bm25 v3-hybrid v4-oss v4-com
      ผล → eval/results/context_audit.md และ eval/results/context_audit_events.csv
      (เครื่องที่ timezone ไม่ใช่ Asia/Bangkok ให้รันด้วย TZ=Asia/Bangkok เพราะ trajectory.match_logs ใช้เวลาของไฟล์)
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import trajectory as traj  # noqa: E402
from lawguide import config  # noqa: E402

FULL_TEXT = {d["section_no"]: d["text"] for d in
             (json.loads(l) for l in (ROOT / "data/processed/sections.jsonl").read_text(encoding="utf-8").splitlines()
              if l.strip())}
RANK = {"lost": 0, "text_partial": 1, "text_ok_meta_cut": 2, "full": 3}
RETRIEVAL = ("search_law_sections", "get_law_section")


def items_of(out):
    if not isinstance(out, dict):
        return []
    if "results" in out:
        return out["results"]
    if "sections" in out:
        return out["sections"]
    return [out] if "text" in out and "section_no" in out else []


def item_status(sent_full: str, item: dict, cap: int) -> str:
    item_s = json.dumps(item, ensure_ascii=False)
    start = sent_full.find(item_s)
    if start < 0:
        return "unknown"
    if start + len(item_s) <= cap:
        return "full"
    t = '"text": ' + json.dumps(item.get("text", ""), ensure_ascii=False)
    ts = item_s.find(t)
    if ts < 0:
        return "unknown"
    t_start, t_end = start + ts + len('"text": '), start + ts + len(t)
    if t_end <= cap:
        return "text_ok_meta_cut"
    return "text_partial" if t_start < cap else "lost"


def audit_run(tag, ev, lg, cap, rows):
    gold = set(traj.TESTS[ev["id"]]["gold_sections"])
    cited = set(ev["cited"])
    logged = {c["section_no"] for c in lg.get("context_returned", [])}
    best, complete = {}, set()
    n_results = n_trunc = 0
    for e in lg["trace"]:
        if e.get("tool") == "submit_final_answer":              # ตีกลับพร้อมแนบตัวบทจริง ก็นับว่าเห็น
            for no, txt in ((e.get("result_sent_to_model") or {}).get("exact_section_text") or {}).items():
                st = "full" if len(txt) >= len(FULL_TEXT.get(no, "")) else "text_partial"
                if RANK[st] > RANK.get(best.get(no), -1):
                    best[no] = st
                if st == "full":
                    complete.add(no)
            continue
        if e.get("tool") not in RETRIEVAL:
            continue
        out = e.get("result_sent_to_model") or e.get("result")
        s = json.dumps(out, ensure_ascii=False)
        n_results += 1
        n_trunc += len(s) > cap
        for it in items_of(out):
            no = it["section_no"]
            st = item_status(s, it, cap)
            budget_cut = len(it.get("text", "")) < len(FULL_TEXT.get(no, ""))
            if st in RANK and RANK[st] > RANK.get(best.get(no), -1):
                best[no] = st
            if st in ("full", "text_ok_meta_cut") and not budget_cut:
                complete.add(no)
            rows.append({"tag": tag, "qid": ev["id"], "run": ev["run"], "step": e["step"], "tool": e["tool"],
                         "section_no": no, "status": st, "budget_cut": budget_cut,
                         "text_chars_sent": len(it.get("text", "")), "text_chars_full": len(FULL_TEXT.get(no, "")),
                         "is_gold": no in gold, "is_cited": no in cited, "tool_result_chars": len(s)})
    seen_any = {no for no, st in best.items() if st != "lost"}
    return {"qid": ev["id"], "run": ev["run"], "gold": gold, "cited": cited, "logged": logged,
            "seen_any": seen_any, "complete": complete, "n_results": n_results, "n_trunc": n_trunc,
            "prompt_tokens": (lg.get("usage") or {}).get("prompt_tokens", 0), "steps": lg.get("steps", 0)}


def pct(a, b):
    return f"{a}/{b} ({a / b:.0%})" if b else "-"


def recall(ms, key):
    xs = [len(m["gold"] & m[key]) / len(m["gold"]) for m in ms if m["gold"]]
    return round(sum(xs) / len(xs), 2) if xs else "-"


def summarize(tag, ms, rows):
    r = [x for x in rows if x["tag"] == tag]
    lost_logged = sum(1 for m in ms if m["logged"] - m["seen_any"])
    cited_never = [(m["qid"], m["run"], sorted(m["cited"] - m["seen_any"])) for m in ms if m["cited"] - m["seen_any"]]
    cited_incomplete = [(m["qid"], m["run"], sorted(m["cited"] - m["complete"])) for m in ms
                        if m["cited"] - m["complete"]]
    return {
        "ผล tool ค้น/ดึง ที่ถูกตัด": pct(sum(m["n_trunc"] for m in ms), sum(m["n_results"] for m in ms)),
        "มาตราในผล tool: เห็นครบ": pct(sum(x["status"] == "full" for x in r), len(r)),
        "  ตัวบทครบ แต่ metadata ถูกตัด": pct(sum(x["status"] == "text_ok_meta_cut" for x in r), len(r)),
        "  ตัวบทถูกตัดกลางข้อความ": pct(sum(x["status"] == "text_partial" for x in r), len(r)),
        "  หายทั้งมาตรา": pct(sum(x["status"] == "lost" for x in r), len(r)),
        "  ตัวบทถูกตัดด้วยงบต่อมาตรา": pct(sum(x["budget_cut"] for x in r), len(r)),
        "รอบที่ log บอกว่าอยู่ใน context แต่โมเดลไม่เห็นเลย ≥1 มาตรา": pct(lost_logged, len(ms)),
        "gold recall ตาม log (ที่ใช้ใน trajectory)": recall(ms, "logged"),
        "gold recall ที่โมเดลเห็นจริง (อย่างน้อยบางส่วน)": recall(ms, "seen_any"),
        "gold recall ที่โมเดลเห็นตัวบทครบ": recall(ms, "complete"),
        "รอบที่อ้างมาตราที่ไม่เคยได้รับตัวบท (เห็นแค่ข้อความย่อ cited_by / ผล verify)": pct(len(cited_never), len(ms)),
        "รอบที่อ้างมาตราที่โมเดลไม่เคยเห็นตัวบทครบ": pct(len(cited_incomplete), len(ms)),
        "prompt token เฉลี่ย / คำถาม (รวมทุก step)": round(sum(m["prompt_tokens"] for m in ms) / len(ms)) if ms else "-",
        "step เฉลี่ย / คำถาม": round(sum(m["steps"] for m in ms) / len(ms), 1) if ms else "-",
    }, cited_never, cited_incomplete


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--cap", type=int, default=config.TOOL_RESULT_MAX_CHARS)
    ap.add_argument("--md", default=str(ROOT / "eval/results/context_audit.md"))
    ap.add_argument("--csv", default=str(ROOT / "eval/results/context_audit_events.csv"))
    args = ap.parse_args()
    logs, rows, summ, cases, notes = traj.load_logs(), [], {}, [], []
    for tag in args.tags:
        evals, pairs, mismatch, _ = traj.match_logs(tag, logs)
        notes.append(f"- {tag}: จับคู่ log ได้ {len(pairs)}/{len(evals)} แถว · มาตราที่อ้างไม่ตรงกับผล eval {mismatch} แถว")
        ms = [audit_run(tag, ev, lg, args.cap, rows) for ev, lg in pairs]
        summ[tag], never, incomplete = summarize(tag, ms, rows)
        cases += [f"| {tag} | {q} | {r} | {', '.join(s)} | ไม่เคยได้รับตัวบท |" for q, r, s in never]
        cases += [f"| {tag} | {q} | {r} | {', '.join(s)} | เห็นตัวบทไม่ครบ |" for q, r, s in incomplete
                  if (q, r) not in {(a, b) for a, b, _ in never}]
    keys = list(next(iter(summ.values())))
    md = [f"# Context audit — โมเดลเห็นอะไรจริง (ตัดที่ {args.cap} ตัวอักษรต่อผล tool)", "",
          "สร้างด้วย `python eval/context_audit.py " + " ".join(args.tags) + "` จาก log ใน `runs/` ไม่ได้รันโมเดลใหม่", "",
          "| ตัววัด | " + " | ".join(args.tags) + " |", "|---|" + "---|" * len(args.tags)]
    md += [f"| {k} | " + " | ".join(str(summ[t][k]) for t in args.tags) + " |" for k in keys]
    md += ["", "## รอบที่อ้างมาตราที่โมเดลไม่ได้เห็นตัวบทครบ", "",
           "| รอบ eval | ข้อ | ครั้งที่ | มาตรา | อาการ |", "|---|---|---|---|---|"] + (cases or ["| - | - | - | - | ไม่พบ |"])
    md += ["", "## การจับคู่ log", ""] + notes
    Path(args.md).write_text("\n".join(md) + "\n", encoding="utf-8")
    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\n".join(md))
    print(f"\nเขียน {args.md} และ {args.csv} ({len(rows)} แถว)")


if __name__ == "__main__":
    main()
