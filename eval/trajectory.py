"""วัดการทำงานของ agent ระหว่างทาง (trajectory) จาก log ของแต่ละครั้งที่ถาม ไม่ต้องรัน eval ใหม่

run_eval.py วัด "ผลลัพธ์" (ตอบถูกไหม) ไฟล์นี้วัด "วิธีที่ได้ผลลัพธ์นั้น":
  retrieval        มาตราที่ถูก (gold) เคยอยู่ในผลของ tool ไหม → แยก "หาไม่เจอ" ออกจาก "เจอแล้วแต่ใช้ผิด"
  tool selection   เรียก tool ที่ข้อนั้นควรใช้ไหม (testset: expected_tools)
  tool call errors argument ผิด / JSON พัง / tool ที่ไม่มีจริง / พิมพ์ tool เป็นข้อความแทนการเรียก
  efficiency       step, tool call, token, ค่าใช้จ่ายต่อคำถาม, latency
  self-correction  โดน policy ตีกลับแล้วแก้จนผ่านได้กี่ครั้ง
  search behavior  top_k ที่โมเดลขอ, ค้นคำชุดเดิมซ้ำ (คำนวณจาก trace จึงใช้กับรอบก่อน v4 ได้ด้วย)
  consistency      ถามซ้ำแล้วลำดับ tool เหมือนเดิมไหม

จับคู่ log กับผล eval: เอา log ของโมเดลเดียวกันที่บันทึกก่อนไฟล์ raw_runs.jsonl ถูกเขียนครั้งสุดท้าย
แล้วไล่ย้อนหลังให้คำถาม (หลัง mask) ตรงกับลำดับในผล eval ทีละแถว จากนั้นตรวจซ้ำว่ามาตราที่อ้างตรงกันทุกแถว

รัน:  python eval/trajectory.py v3-bm25 v3-hybrid v4-oss v4-com --md eval/results/trajectory.md
"""
import argparse
import collections
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lawguide import config, pii  # noqa: E402
from lawguide.store import _tok, norm_section_no  # noqa: E402

TESTS = {t["id"]: t for t in (json.loads(l) for l in
                              (ROOT / "eval/testset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
MASKED = {qid: pii.guard(t["question"], use_llm=False)["masked_text"] for qid, t in TESTS.items()}


def load_logs():
    rows = []
    for f in sorted(config.RUNS_DIR.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r.get("timestamp", ""))
    return rows


def match_logs(tag, logs):
    """คืน list ของ (eval_row, log_row) และจำนวนแถวที่มาตราที่อ้างไม่ตรงกัน"""
    d = ROOT / "eval/results" / tag
    evals = [json.loads(l) for l in (d / "raw_runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    model = json.loads((d / "summary.json").read_text(encoding="utf-8"))["model"] if (d / "summary.json").exists() \
        else None
    end = datetime.fromtimestamp(os.path.getmtime(d / "raw_runs.jsonl") + 5).isoformat(timespec="seconds")
    pool = [r for r in logs if r.get("timestamp", "") <= end and (model is None or r.get("model") == model)]
    pairs, i = [], len(pool) - 1
    for ev in reversed(evals):                         # ไล่ย้อนจากแถวสุดท้าย ข้าม log ที่ไม่ใช่ของ eval (เช่น ถามจากหน้าเว็บ)
        while i >= 0 and pool[i]["question_masked"] != MASKED[ev["id"]]:
            i -= 1
        if i < 0:
            break
        pairs.append((ev, pool[i]))
        i -= 1
    pairs.reverse()
    mismatch = sum(1 for ev, lg in pairs if sorted(ev["cited"]) != sorted(
        {norm_section_no(c.get("section_no", "")) for c in (lg.get("final") or {}).get("citations", []) or []}))
    return evals, pairs, mismatch, model


def tool_events(lg):
    return [e for e in lg["trace"] if e.get("tool")]


def run_metrics(ev, lg, prices):
    t = TESTS[ev["id"]]
    gold = set(t["gold_sections"])
    ctx = {c["section_no"] for c in lg.get("context_returned", [])}
    tools = tool_events(lg)
    names = [e["tool"] for e in tools]
    searches = [e for e in tools if e["tool"] == "search_law_sections"]
    seen, dup = set(), 0
    for e in searches:
        key = tuple(sorted(set(_tok(e["arguments"].get("query", "")))))
        dup += key in seen
        seen.add(key)
    errors = sum(1 for e in tools if isinstance(e.get("result"), dict) and "error" in e["result"])
    text_calls = sum(1 for e in lg["trace"] if e.get("type") == "text_without_tool")
    rejected = sum(1 for e in tools if e.get("result_sent_to_model"))
    exp = t.get("expected_tools") or []
    u = lg.get("usage") or {}
    price = prices.get(lg.get("model"))
    cost = (u.get("prompt_tokens", 0) * price[0] + u.get("completion_tokens", 0) * price[1]) if price else None
    cited = set(ev["cited"])
    if not gold or ev["behavior"] != "answer":
        failure = None
    elif not gold & ctx:
        failure = "retrieval_miss"              # มาตราที่ถูกไม่เคยถูกดึงมาเลย
    elif not gold <= cited:
        failure = "retrieved_not_used" if gold - cited <= ctx else "partial_retrieval"
    else:
        failure = "ok"
    return {
        "id": ev["id"], "behavior_ok": ev["behavior_ok"],
        "gold_in_context": round(len(gold & ctx) / len(gold), 3) if gold else None,
        "failure_type": failure,
        "expected_tools_ok": all(any(x in names for x in group) for group in exp) if exp else None,
        "tool_calls": len(tools), "steps": lg.get("steps"),
        "tool_errors": errors, "text_tool_calls": text_calls,
        "searches": len(searches), "duplicate_queries": dup,
        "top_k_requested": [e["arguments"].get("top_k") for e in searches],
        "policy_rejections": rejected,
        "recovered_after_rejection": (lg["status"] == "answered") if rejected else None,
        "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
        "cost_usd": cost, "latency_s": lg.get("latency_s"), "tool_sequence": tuple(names),
    }


def fetch_prices():
    try:
        from eval.check_models import fetch_models
    except Exception:
        try:
            sys.path.insert(0, str(ROOT / "eval"))
            from check_models import fetch_models
        except Exception:
            return {}
    try:
        return {mid: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])) for mid, m in fetch_models().items()}
    except Exception:
        return {}


def _mean(xs, nd=2):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), nd) if xs else None


def _frac(xs):
    xs = [x for x in xs if x is not None]
    return f"{sum(bool(x) for x in xs)}/{len(xs)}" if xs else "-"


def summarize(ms):
    by_q = collections.defaultdict(list)
    for m in ms:
        by_q[m["id"]].append(m)
    modal = []
    for runs in by_q.values():
        seqs = collections.Counter(r["tool_sequence"] for r in runs)
        modal.append(seqs.most_common(1)[0][1] / len(runs))
    topk = collections.Counter(k for m in ms for k in m["top_k_requested"])
    fails = collections.Counter(m["failure_type"] for m in ms if m["failure_type"])
    cost = [m["cost_usd"] for m in ms]
    return {
        "runs": len(ms),
        "gold in context (retrieval recall)": _mean([m["gold_in_context"] for m in ms]),
        "answers: ok / retrieval_miss / retrieved_not_used / partial":
            f"{fails['ok']} / {fails['retrieval_miss']} / {fails['retrieved_not_used']} / {fails['partial_retrieval']}",
        "expected tools used": _frac([m["expected_tools_ok"] for m in ms]),
        "tool calls / question": _mean([m["tool_calls"] for m in ms]),
        "steps / question": _mean([m["steps"] for m in ms]),
        "tool call errors": sum(m["tool_errors"] for m in ms),
        "tool written as text": sum(m["text_tool_calls"] for m in ms),
        "searches / question": _mean([m["searches"] for m in ms]),
        "duplicate queries": sum(m["duplicate_queries"] for m in ms),
        "top_k requested": ", ".join(f"{k}:{v}" for k, v in sorted(topk.items(), key=lambda x: str(x[0]))),
        "policy rejections → recovered": f"{sum(bool(m['recovered_after_rejection']) for m in ms if m['policy_rejections'])}"
                                         f"/{sum(1 for m in ms if m['policy_rejections'])}",
        "tokens / question (in / out)": f"{_mean([m['prompt_tokens'] for m in ms], 0)} / "
                                        f"{_mean([m['completion_tokens'] for m in ms], 0)}",
        "cost / question (USD)": f"{_mean(cost, 4)}" if any(c is not None for c in cost) else "-",
        "latency / question (s)": _mean([m["latency_s"] for m in ms]),
        "same tool sequence across repeats": _mean(modal),
    }


def per_question(all_ms, tags):
    ids = sorted({m["id"] for ms in all_ms.values() for m in ms})
    lines = ["| id | " + " | ".join(f"{t}: gold in ctx · tool calls · dup" for t in tags) + " |",
             "|---|" + "---|" * len(tags)]
    for qid in ids:
        cells = []
        for t in tags:
            rs = [m for m in all_ms[t] if m["id"] == qid]
            if not rs:
                cells.append("-")
                continue
            g = _mean([r["gold_in_context"] for r in rs])
            cells.append(f"{'-' if g is None else g} · {_mean([r['tool_calls'] for r in rs], 1)} · "
                         f"{sum(r['duplicate_queries'] for r in rs)}")
        lines.append(f"| {qid} | " + " | ".join(cells) + " |")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--md", default="")
    args = ap.parse_args()
    logs, prices = load_logs(), fetch_prices()
    all_ms, overall, notes = {}, {}, []
    for tag in args.tags:
        evals, pairs, mismatch, model = match_logs(tag, logs)
        all_ms[tag] = [run_metrics(ev, lg, prices) for ev, lg in pairs]
        overall[tag] = summarize(all_ms[tag])
        notes.append(f"- {tag}: จับคู่ log ได้ {len(pairs)}/{len(evals)} แถว · มาตราที่อ้างไม่ตรงกับผล eval {mismatch} แถว"
                     + ("" if prices.get(model) else " · ไม่มีราคาโมเดล (ดึงจาก OpenRouter ไม่ได้)"))
    keys = list(next(iter(overall.values())))
    lines = ["| ตัววัด | " + " | ".join(args.tags) + " |", "|---|" + "---|" * len(args.tags)]
    lines += [f"| {k} | " + " | ".join(str(overall[t][k]) for t in args.tags) + " |" for k in keys]
    lines += ["", "**รายข้อ**", ""] + per_question(all_ms, args.tags) + ["", "**การจับคู่ log**", ""] + notes
    md = "\n".join(lines)
    print(md)
    if args.md:
        Path(args.md).write_text(md + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
