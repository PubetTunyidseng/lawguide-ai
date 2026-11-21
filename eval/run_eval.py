"""ประเมินทั้งระบบ (harness + tools + LLM) แบบถามซ้ำ N รอบในเงื่อนไขเดิม

วัดแยกกัน ไม่รวมเป็นคะแนนเดียว:
  behavior      ระบบทำพฤติกรรมถูกไหม (ตอบ / ถามกลับ / บอกว่านอกขอบเขต)
  correctness   มาตราที่อ้างตรงกับ gold_sections แค่ไหน (recall / precision) — gold ต้องให้ผู้รู้กฎหมายตรวจ
  faithfulness  (1) deterministic: มาตราที่อ้างอยู่ใน context ที่ tool คืนมาไหม
                (2) --judge: ให้ LLM อีกตัวตัดสินทีละประโยคว่ามี context รองรับไหม (เก็บประโยคที่ไม่รองรับไว้ดู)
  hallucination จำนวนครั้งที่โมเดลพยายามอ้างมาตราที่ไม่มีจริง (นับจาก trace รวมรอบที่ policy ตีกลับ)
  issue type    จำแนกประเด็นกฎหมายถูกไหม (เทียบ issue_type ที่โมเดลส่งกับ testset)
  key facts     คำตอบมีข้อเท็จจริงหลักตามตัวบทไหม เช่น จำนวนวัน อัตรา (testset: key_facts)
  question cov. ถามกลับครบข้อเท็จจริงที่จำเป็นไหม (testset: required_questions)
  consistency   ถามซ้ำ N รอบ: พฤติกรรมเหมือนกันไหม, ชุดมาตราที่อ้างซ้ำกันแค่ไหน (Jaccard เฉลี่ยรายคู่)
  harness       harness เข้าไปคุมกี่ครั้ง: บังคับ top_k ขั้นต่ำ, กันค้นซ้ำ, policy ตีกลับแยกตามกฎ

รัน:
  python eval/run_eval.py --repeats 5
  python eval/run_eval.py --repeats 5 --ids q01,q03 --model qwen/qwen-2.5-72b-instruct --judge
  python eval/run_eval.py --repeats 5 --model <open-source> --tag oss   แล้วเทียบกับ --model <commercial> --tag com
  python eval/compare.py v3-hybrid v4-oss v4-com                          ตารางเทียบหลายรอบ
ผลดิบทุกรอบ: eval/results/<tag>/raw_runs.jsonl  สรุป: summary.json, summary.md
"""
import argparse
import collections
import itertools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lawguide import config, harness, llm  # noqa: E402
from lawguide.policy import negated_mention  # noqa: E402
from lawguide.store import get_store, norm_section_no  # noqa: E402

JUDGE_PROMPT = """คุณเป็นผู้ตรวจ faithfulness ให้ตัดสินแต่ละประโยคใน "คำตอบ" ว่ามีข้อความใน "context" รองรับหรือไม่
ตอบเป็น JSON array เท่านั้น: [{{"sentence": "...", "supported": true/false, "evidence_section": "<เลขมาตราที่รองรับ หรือ ''>"}}]
ประโยคที่ไม่ใช่ข้อกฎหมาย (เช่น คำแนะนำให้ปรึกษาผู้เชี่ยวชาญ) ให้ supported=true
context:
{context}

คำตอบ:
{answer}
"""


def behavior_of(r: dict) -> str:
    f = r.get("final") or {}
    if r["status"] == "need_more_facts":
        return "ask_more"
    if r["status"] == "answered" and not f.get("citations") and not f.get("facts_sufficient"):
        return "out_of_scope_or_unanswerable"
    return {"answered": "answer"}.get(r["status"], r["status"])


def behavior_ok(expected: str, got: str) -> bool:
    return {"answer": got == "answer", "ask_more": got == "ask_more",
            "out_of_scope": got == "out_of_scope_or_unanswerable",
            "answer_or_ask": got in ("answer", "ask_more", "out_of_scope_or_unanswerable")}[expected]


def hallucination_attempts(r: dict, valid: set) -> list:
    bad = []
    for e in r["trace"]:
        if e.get("tool") == "submit_final_answer":
            a = e["arguments"]
            nos = [norm_section_no(c.get("section_no", "")) for c in a.get("citations", [])]
            nos += [norm_section_no(m) for m in re.findall(r"มาตรา\s*([0-9๐-๙]+(?:/[0-9๐-๙]+)?)", a.get("answer", ""))
                    if not negated_mention(a.get("answer", ""), norm_section_no(m))]   # "ไม่มีมาตรา 999" ไม่นับ
            bad += [n for n in nos if n not in valid]
    return sorted(set(bad))


def judge(answer: str, context: list, model: str) -> list:
    ctx = "\n\n".join(f"[มาตรา {c['section_no']}] {c['text']}" for c in context)[:12000]
    out = llm.chat_plain(JUDGE_PROMPT.format(context=ctx, answer=answer), model=model, temperature=0)
    try:
        return json.loads(re.search(r"\[.*\]", out, re.S).group(0))
    except Exception:
        return [{"sentence": "(judge parse error)", "supported": None, "raw": out[:300]}]


def coverage(text: str, groups: list) -> tuple:
    """groups = [[คำที่ยอมรับ...], ...] คืน (สัดส่วนที่พบ, รายการกลุ่มที่ไม่พบ)"""
    if not groups:
        return None, []
    t = re.sub(r"\s+", "", (text or "").replace(",", ""))
    missing = [g for g in groups if not any(re.sub(r"\s+", "", k) in t for k in g)]
    return round(1 - len(missing) / len(groups), 3), [g[0] for g in missing]


def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--ids", default="")
    ap.add_argument("--model", default=config.LLM_MODEL)
    ap.add_argument("--temperature", type=float, default=config.TEMPERATURE)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--judge-model", default=config.JUDGE_MODEL)
    ap.add_argument("--tag", default="default")
    args = ap.parse_args()

    if args.judge and args.judge_model == args.model:
        print(f"⚠️ judge ใช้โมเดลเดียวกับที่ถูกประเมิน ({args.model}) — ควรตั้ง --judge-model หรือ JUDGE_MODEL เป็นโมเดลอื่น")
    tests = [json.loads(l) for l in (ROOT / "eval/testset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.ids:
        keep = set(args.ids.split(","))
        tests = [t for t in tests if t["id"] in keep]
    store = get_store()
    valid = store.all_section_nos()
    outdir = ROOT / "eval/results" / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    raw_f = open(outdir / "raw_runs.jsonl", "w", encoding="utf-8")
    summary = {"model": args.model, "temperature": args.temperature, "repeats": args.repeats,
               "backend": f"{config.BACKEND} ({store.name})",
               "judge_model": args.judge_model if args.judge else None,
               "harness": {"MAX_STEPS": config.MAX_STEPS, "TOP_K": config.TOP_K, "MIN_TOP_K": config.MIN_TOP_K,
                           "DUPLICATE_QUERY": config.DUPLICATE_QUERY, "MAX_POLICY_RETRIES": config.MAX_POLICY_RETRIES},
               "questions": []}

    all_rows = []
    for t in tests:
        runs = []
        for i in range(args.repeats):
            s = harness.Session(store, model=args.model, temperature=args.temperature)
            r = s.ask(t["question"])
            f = r.get("final") or {}
            cited = {norm_section_no(c.get("section_no", "")) for c in f.get("citations", []) or []}
            gold = set(t["gold_sections"])
            beh = behavior_of(r)
            score_cites = bool(gold) and beh == "answer"          # ถามกลับถูกแล้วไม่ต้องคิด recall
            answer_text = f.get("answer") or ""
            kf, kf_missing = coverage(answer_text, t.get("key_facts", [])) if beh == "answer" else (None, [])
            qtext = " ".join(f.get("questions", []) or [])
            qc, q_missing = coverage(qtext, t.get("required_questions", [])) if beh == "ask_more" else (None, [])
            rejected_rules = [v["rule"] for e in r["trace"] if e.get("result_sent_to_model")
                              for v in e.get("policy_violations", []) if v["severity"] == "block"]
            row = {"id": t["id"], "run": i + 1, "status": r["status"], "behavior": beh,
                   "issue_type_expected": t.get("issue_type"), "issue_type_pred": f.get("issue_type"),
                   "issue_type_ok": f.get("issue_type") == t.get("issue_type") if t.get("issue_type") else None,
                   "behavior_ok": behavior_ok(t["expected_behavior"], beh),
                   "cited": sorted(cited), "gold": sorted(gold),
                   "recall": round(len(cited & gold) / len(gold), 3) if score_cites else None,
                   "precision": round(len(cited & (gold | set(t.get("ok_sections", [])))) / len(cited), 3)
                                if score_cites and cited else None,
                   "key_facts": kf, "key_facts_missing": kf_missing,
                   "question_coverage": qc, "questions_missing": q_missing,
                   "cited_in_context": sorted(c for c in cited if c in {x["section_no"] for x in r["context_returned"]}),
                   "hallucination_attempts": hallucination_attempts(r, valid),
                   "policy_rejections": sum(1 for e in r["trace"] if e.get("result_sent_to_model")),
                   "policy_rejected_rules": rejected_rules,
                   "final_violations": [v["rule"] for v in r.get("policy_violations") or []],
                   "harness_stats": r.get("harness_stats", {}),
                   "tools_used": [e["tool"] for e in r["trace"] if e.get("tool")],
                   "answer": f.get("answer") or "; ".join(f.get("questions", [])),
                   "latency_s": r["latency_s"], "usage": r.get("usage")}
            if args.judge and f.get("answer"):
                verdicts = judge(f["answer"], r["context_returned"], args.judge_model)
                row["judge"] = verdicts
                row["unsupported_sentences"] = [v["sentence"] for v in verdicts if v.get("supported") is False]
                judged = [v for v in verdicts if v.get("supported") is not None]
                row["judge_supported_ratio"] = round(sum(bool(v["supported"]) for v in judged) / len(judged), 3) \
                    if judged else None
            runs.append(row)
            all_rows.append(row)
            raw_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            raw_f.flush()
            hs = row["harness_stats"]
            print(f"{t['id']} run{i+1}: {row['behavior']:<28} issue={row['issue_type_pred']} cited={row['cited']} "
                  f"facts={row['key_facts']} miss={row['key_facts_missing'] or row['questions_missing']} "
                  f"halluc={row['hallucination_attempts']} rejected={rejected_rules} "
                  f"clamp={hs.get('top_k_clamped')} dup={hs.get('duplicate_queries')} "
                  f"unsupported={len(row.get('unsupported_sentences', []))} {row['latency_s']}s")

        pairs = list(itertools.combinations([set(r["cited"]) for r in runs], 2))
        behaviors = [r["behavior"] for r in runs]
        summary["questions"].append({
            "id": t["id"], "expected": t["expected_behavior"], "gold_status": t["gold_status"],
            "behavior_ok": f"{sum(r['behavior_ok'] for r in runs)}/{len(runs)}",
            "behavior_same_all_runs": len(set(behaviors)) == 1, "behaviors": behaviors,
            "mean_recall": _mean([r["recall"] for r in runs]),
            "mean_precision": _mean([r["precision"] for r in runs]),
            "mean_key_facts": _mean([r["key_facts"] for r in runs]),
            "mean_question_coverage": _mean([r["question_coverage"] for r in runs]),
            "citation_jaccard_mean": round(sum(jaccard(a, b) for a, b in pairs) / len(pairs), 3) if pairs else None,
            "runs_with_hallucination_attempt": sum(bool(r["hallucination_attempts"]) for r in runs),
            "issue_type_ok": f"{sum(bool(r['issue_type_ok']) for r in runs)}/{len(runs)}" if t.get("issue_type") else None,
            "mean_judge_supported": _mean([r.get("judge_supported_ratio") for r in runs]),
            "policy_rejected_rules": dict(collections.Counter(x for r in runs for x in r["policy_rejected_rules"])),
            "top_k_clamped": sum(r["harness_stats"].get("top_k_clamped", 0) for r in runs),
            "duplicate_queries": sum(r["harness_stats"].get("duplicate_queries", 0) for r in runs),
            "cited_sets": [r["cited"] for r in runs],
            "unsupported_sentences": [s for r in runs for s in r.get("unsupported_sentences", [])],
        })
    raw_f.close()
    summary["totals"] = totals(all_rows)
    (outdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "summary.md").write_text(to_md(summary), encoding="utf-8")
    print(to_md(summary))


def totals(rows: list) -> dict:
    """ภาพรวมทั้งชุด — ตัวเลขแยกกัน ไม่รวมเป็นคะแนนเดียว"""
    n = len(rows)
    issue = [r["issue_type_ok"] for r in rows if r["issue_type_ok"] is not None]
    return {"runs": n,
            "behavior_ok": f"{sum(r['behavior_ok'] for r in rows)}/{n}",
            "issue_type_ok": f"{sum(issue)}/{len(issue)}" if issue else None,
            "mean_recall": _mean([r["recall"] for r in rows]),
            "mean_precision": _mean([r["precision"] for r in rows]),
            "mean_key_facts": _mean([r["key_facts"] for r in rows]),
            "mean_question_coverage": _mean([r["question_coverage"] for r in rows]),
            "runs_with_hallucination_attempt": sum(bool(r["hallucination_attempts"]) for r in rows),
            "answers_with_unsupported_sentence": sum(bool(r.get("unsupported_sentences")) for r in rows
                                                     if "judge" in r),
            "judged_answers": sum("judge" in r for r in rows),
            "policy_rejected_rules": dict(collections.Counter(x for r in rows for x in r["policy_rejected_rules"])),
            "top_k_clamped": sum(r["harness_stats"].get("top_k_clamped", 0) for r in rows),
            "duplicate_queries": sum(r["harness_stats"].get("duplicate_queries", 0) for r in rows),
            "mean_latency_s": _mean([r["latency_s"] for r in rows])}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def to_md(s):
    lines = [f"model `{s['model']}` · temperature {s['temperature']} · repeats {s['repeats']} · backend {s['backend']}"
             + (f" · judge `{s['judge_model']}`" if s.get("judge_model") else ""),
             "", "| id | expected | behavior ok | same all runs | issue ok | recall | precision | key facts | "
             "question cov. | judge supported | cite Jaccard | halluc runs | policy rejected | clamp/dup | cited sets |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for q in s["questions"]:
        lines.append(f"| {q['id']} | {q['expected']} | {q['behavior_ok']} | {q['behavior_same_all_runs']} | "
                     f"{q.get('issue_type_ok')} | {q['mean_recall']} | {q['mean_precision']} | {q['mean_key_facts']} | "
                     f"{q['mean_question_coverage']} | {q.get('mean_judge_supported')} | {q['citation_jaccard_mean']} | "
                     f"{q['runs_with_hallucination_attempt']} | {q.get('policy_rejected_rules') or ''} | "
                     f"{q.get('top_k_clamped', 0)}/{q.get('duplicate_queries', 0)} | {q['cited_sets']} |")
    if s.get("totals"):
        lines += ["", "**รวมทั้งชุด**", ""] + [f"- {k}: {v}" for k, v in s["totals"].items()]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
