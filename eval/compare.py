"""เทียบผล eval หลายรอบแบบตาราง (อ่านจาก eval/results/<tag>/raw_runs.jsonl + summary.json)

รัน:  python eval/compare.py qwen3 qwen3-v2 v3-bm25 v3-hybrid v4-oss v4-com
      python eval/compare.py v3-hybrid v4-oss --md docs/compare.md

ตัวเลขแยกกันทุกตัว ไม่รวมเป็นคะแนนเดียว · รอบเก่าที่ยังไม่มีตัววัดใหม่จะแสดง "-"
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(tag):
    d = ROOT / "eval/results" / tag
    rows = [json.loads(l) for l in (d / "raw_runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    meta = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    return rows, meta


def mean(xs):
    xs = [x for x in xs if x is not None]
    return f"{sum(xs) / len(xs):.2f}" if xs else "-"


def frac(rows, key):
    xs = [r[key] for r in rows if r.get(key) is not None]
    return f"{sum(bool(x) for x in xs)}/{len(xs)}" if xs else "-"


def overall(rows, meta):
    has = lambda k: any(k in r for r in rows)                       # noqa: E731
    judged = [r for r in rows if "judge" in r]
    hs = [r.get("harness_stats") or {} for r in rows]
    return {
        "model": meta["model"].split("/")[-1],
        "backend": meta["backend"].split("(")[-1].rstrip(")"),
        "runs": str(len(rows)),
        "behavior ok": frac(rows, "behavior_ok"),
        "issue type ok": frac(rows, "issue_type_ok") if has("issue_type_ok") else "-",
        "recall (answered)": mean([r["recall"] for r in rows]),
        "precision (answered)": mean([r["precision"] for r in rows]),
        "key facts": mean([r["key_facts"] for r in rows]),
        "follow-up question cov.": mean([r["question_coverage"] for r in rows]),
        "runs w/ hallucination attempt": str(sum(bool(r["hallucination_attempts"]) for r in rows)),
        "judge: answers w/ unsupported sentence": f"{sum(bool(r.get('unsupported_sentences')) for r in judged)}/{len(judged)}"
                                                 if judged else "-",
        "policy rejections": str(sum(r["policy_rejections"] for r in rows)),
        "top_k clamped": str(sum(h.get("top_k_clamped", 0) for h in hs)) if has("harness_stats") else "-",
        "duplicate queries": str(sum(h.get("duplicate_queries", 0) for h in hs)) if has("harness_stats") else "-",
        "latency mean (s)": mean([r["latency_s"] for r in rows]),
    }


def per_question(tags_data):
    ids = sorted({r["id"] for rows, _ in tags_data.values() for r in rows})
    out = []
    for qid in ids:
        cells = []
        for rows, _ in tags_data.values():
            rs = [r for r in rows if r["id"] == qid]
            cells.append(f"{sum(r['behavior_ok'] for r in rs)}/{len(rs)}" if rs else "-")
        out.append((qid, cells))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--md", default="")
    args = ap.parse_args()
    data = {t: load(t) for t in args.tags}
    ov = {t: overall(*v) for t, v in data.items()}
    keys = list(next(iter(ov.values())))
    lines = ["| ตัววัด | " + " | ".join(args.tags) + " |", "|---|" + "---|" * len(args.tags)]
    lines += [f"| {k} | " + " | ".join(ov[t][k] for t in args.tags) + " |" for k in keys]
    lines += ["", "**behavior ok รายข้อ**", "", "| id | " + " | ".join(args.tags) + " |", "|---|" + "---|" * len(args.tags)]
    lines += [f"| {qid} | " + " | ".join(c) + " |" for qid, c in per_question(data)]
    md = "\n".join(lines)
    print(md)
    if args.md:
        Path(args.md).write_text(md + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
