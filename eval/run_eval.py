"""ประเมินทั้งระบบ (harness + tools + LLM) แบบถามซ้ำ N รอบในเงื่อนไขเดิม

วัดแยกกัน ไม่รวมเป็นคะแนนเดียว:
  behavior      ระบบทำพฤติกรรมถูกไหม (ตอบ / ถามกลับ / บอกว่านอกขอบเขต)
  correctness   มาตราที่อ้างตรงกับ gold_sections แค่ไหน (recall / precision) — gold ต้องให้ผู้รู้กฎหมายตรวจ
  faithfulness  (1) deterministic: มาตราที่อ้างอยู่ใน context ที่ tool คืนมาไหม
                (2) --judge: ให้ LLM อีกตัวตัดสินทีละประโยคว่ามี context รองรับไหม (เก็บประโยคที่ไม่รองรับไว้ดู)
  hallucination จำนวนครั้งที่โมเดลพยายามอ้างมาตราที่ไม่มีจริง (นับจาก trace รวมรอบที่ policy ตีกลับ)
  key facts     คำตอบมีข้อเท็จจริงหลักตามตัวบทไหม เช่น จำนวนวัน อัตรา (testset: key_facts)
  question cov. ถามกลับครบข้อเท็จจริงที่จำเป็นไหม (testset: required_questions)
  consistency   ถามซ้ำ N รอบ: พฤติกรรมเหมือนกันไหม, ชุดมาตราที่อ้างซ้ำกันแค่ไหน (Jaccard เฉลี่ยรายคู่)

รัน:
  python eval/run_eval.py --repeats 5
  python eval/run_eval.py --repeats 5 --ids q01,q03 --model qwen/qwen-2.5-72b-instruct --judge
  python eval/run_eval.py --repeats 5 --model <open-source> --tag oss   แล้วเทียบกับ --model <commercial> --tag com
ผลดิบทุกรอบ: eval/results/<tag>/raw_runs.jsonl  สรุป: summary.json, summary.md
"""
import argparse
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
               "backend": config.BACKEND, "questions": []}

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
            row = {"id": t["id"], "run": i + 1, "status": r["status"], "behavior": beh,
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
                   "tools_used": [e["tool"] for e in r["trace"] if e.get("tool")],
                   "answer": f.get("answer") or "; ".join(f.get("questions", [])),
                   "latency_s": r["latency_s"], "usage": r.get("usage")}
            if args.judge and f.get("answer"):
                verdicts = judge(f["answer"], r["context_returned"], args.judge_model)
                row["judge"] = verdicts
                row["unsupported_sentences"] = [v["sentence"] for v in verdicts if v.get("supported") is False]
            runs.append(row)
            raw_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            raw_f.flush()
            print(f"{t['id']} run{i+1}: {row['behavior']:<28} cited={row['cited']} "
                  f"facts={row['key_facts']} miss={row['key_facts_missing'] or row['questions_missing']} "
                  f"halluc={row['hallucination_attempts']} {row['latency_s']}s")

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
            "cited_sets": [r["cited"] for r in runs],
            "unsupported_sentences": [s for r in runs for s in r.get("unsupported_sentences", [])],
        })
    raw_f.close()
    (outdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "summary.md").write_text(to_md(summary), encoding="utf-8")
    print(to_md(summary))


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def to_md(s):
    lines = [f"model `{s['model']}` · temperature {s['temperature']} · repeats {s['repeats']} · backend {s['backend']}",
             "", "| id | expected | behavior ok | same all runs | recall | precision | key facts | question cov. | cite Jaccard | halluc runs | cited sets |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for q in s["questions"]:
        lines.append(f"| {q['id']} | {q['expected']} | {q['behavior_ok']} | {q['behavior_same_all_runs']} | "
                     f"{q['mean_recall']} | {q['mean_precision']} | {q['mean_key_facts']} | {q['mean_question_coverage']} | "
                     f"{q['citation_jaccard_mean']} | "
                     f"{q['runs_with_hallucination_attempt']} | {q['cited_sets']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
