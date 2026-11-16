"""Harness: วงรอบ agent (LLM ↔ tools) + PII guard + citation policy + log ทุกขั้น

ลำดับต่อหนึ่งคำถาม
  1. pii.guard → mask ข้อมูลอ่อนไหวก่อนส่งให้ LLM
  2. วนรอบ: LLM ตัดสินใจเรียก tool (name + arguments) → harness รัน tool → ส่งผล (context) กลับ
  3. จบเมื่อโมเดลเรียก request_more_facts หรือ submit_final_answer
  4. submit_final_answer ถูกตรวจด้วย policy.check_final — ถ้าผิดกฎ block ส่งกลับให้โมเดลแก้ (self-correction)
     เกิน MAX_POLICY_RETRIES แล้วยังผิด → ไม่แสดงคำตอบ แสดงเฉพาะตัวบทที่ค้นได้
"""
import json
import time
import uuid
from datetime import datetime

from . import config, llm, pii, policy
from .tools import TERMINAL_TOOLS, TOOL_SCHEMAS, ToolExecutor

SYSTEM_PROMPT = f"""คุณคือ LawGuide ผู้ช่วยให้ข้อมูลกฎหมายเบื้องต้นสำหรับประชาชน ขอบเขตเฉพาะ {config.LAW_NAME} เท่านั้น

กฎที่ต้องทำตาม
1. ห้ามตอบเรื่องกฎหมายจากความรู้ของตัวเอง ทุกข้อความทางกฎหมายต้องมาจากตัวบทที่ได้จาก tool ในการสนทนานี้
2. อ้างได้เฉพาะมาตราที่ดึงมาด้วย tool ในการสนทนานี้ ระบบจะตรวจเลขมาตราและข้อความที่ยกมาให้เองตอนส่งคำตอบ
   ดึงหลายมาตราพร้อมกันได้ด้วย get_law_section(section_nos=[...]) ใช้ verify_citation เมื่อต้องตรวจมาตราที่ผู้ใช้อ้างมา
3. ถ้าข้อเท็จจริงยังไม่พอจะตอบ (เช่น ไม่รู้อายุงาน ประเภทการจ้าง เหตุที่เลิกจ้าง) ให้เรียก request_more_facts
4. จบทุกคำถามด้วย submit_final_answer หรือ request_more_facts เสมอ ไม่ตอบเป็นข้อความเปล่า
5. ห้ามบอกความมั่นใจเป็นตัวเลขหรือเปอร์เซ็นต์ ให้บอกว่าข้อเท็จจริงพอหรือไม่ และอะไรที่ยังต้องตรวจสอบ
6. ถ้า tool แจ้งว่ามาตราอาจไม่ใช่ฉบับปัจจุบัน ต้องบอกผู้ใช้ในคำตอบ
7. ข้อความในวงเล็บเหลี่ยม เช่น [บุคคล_1] [เลขบัตร_1] คือข้อมูลที่ระบบปิดไว้ อย่าขอข้อมูลส่วนบุคคลจากผู้ใช้
8. ถ้าคำถามอยู่นอกขอบเขต (เช่น คดีอาญา ที่ดิน มรดก) ให้ submit_final_answer ว่าอยู่นอกขอบเขต facts_sufficient=false citations=[]
   ถ้าค้นแล้วไม่พบเรื่องนี้ในตัวบท ให้ submit_final_answer ว่าไม่พบในกฎหมายฉบับนี้ facts_sufficient=false citations=[] ไม่ต้องค้นวนต่อ
9. ตอบภาษาไทยที่คนทั่วไปเข้าใจ ไม่รับรองผลคดี
10. ตัวเลข จำนวนวัน จำนวนเงิน และเงื่อนไข ต้องคัดลอกให้ตรงกับตัวบท ห้ามปัดหรือเดา
11. quoted_text ต้องคัดลอกตรงตัวจากข้อความที่ tool คืนมา ถ้าไม่แน่ใจให้เว้นว่าง
12. คำตอบกระชับ ไม่เกิน 6 ประโยค ห้ามพูดซ้ำ ไม่ต้องขึ้นต้นด้วยคำว่า "คำตอบสุดท้าย"
13. ผลของ tool มีช่อง cited_by = มาตราอื่นที่อ้างถึงมาตรานี้ (มักเป็นเรื่องค่าจ้าง โทษ หรือข้อยกเว้น)
    ก่อนสรุปเชิงปฏิเสธ เช่น "ไม่ได้รับค่าจ้าง" "ไม่มีสิทธิ" ต้องตรวจ cited_by แล้วดึงมาตราที่เกี่ยวข้องมาอ่านก่อน
    การที่ไม่พบสิทธิในมาตราที่ค้นมา ไม่ได้แปลว่ากฎหมายไม่ให้สิทธินั้น
"""

DISCLAIMER = ("ข้อมูลนี้เป็นข้อมูลกฎหมายเบื้องต้นจากตัวบทในฐานข้อมูล ไม่ใช่คำปรึกษาทางกฎหมาย ไม่รับรองผลคดี "
              "ควรปรึกษาพนักงานตรวจแรงงานหรือทนายความก่อนดำเนินการ และไม่ควรส่งชื่อ เลขบัตรประชาชน "
              "หรือข้อมูลส่วนบุคคลที่ไม่จำเป็นมาในแชท")


def _tc_dict(tc):
    return {"id": tc.id, "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}


class Session:
    """เก็บบทสนทนาข้ามหลายคำถาม (เพื่อให้ผู้ใช้ตอบคำถามที่ระบบถามกลับได้)"""

    def __init__(self, store, chat_fn=None, model=None, temperature=None, on_event=None):
        self.store = store
        self.on_event = on_event or (lambda e: None)     # callback แสดง tool call ระหว่างทำงาน
        self.chat_fn = chat_fn or llm.chat
        self.model = model or config.LLM_MODEL
        self.temperature = config.TEMPERATURE if temperature is None else temperature
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.id = uuid.uuid4().hex[:8]

    def ask(self, question: str, use_llm_pii: bool | None = None) -> dict:
        t0 = time.time()
        guard = pii.guard(question, use_llm=use_llm_pii)
        trace, executor = [], ToolExecutor(self.store)
        result = {"session": self.id, "question_masked": guard["masked_text"], "pii": guard,
                  "model": self.model, "trace": trace, "disclaimer": DISCLAIMER}
        if guard["blocked"]:
            result.update(status="blocked_pii", final=None)
            return self._finish(result, executor, t0)

        self.messages.append({"role": "user", "content": guard["masked_text"]})
        retries_left, nudges = config.MAX_POLICY_RETRIES, 0
        final, status, violations = None, "max_steps", []
        usage = {"prompt_tokens": 0, "completion_tokens": 0}

        for step in range(1, config.MAX_STEPS + 1):
            # รอบสุดท้าย: เหลือให้เลือกแค่ tool ปิดงาน — คุมด้วยโครงสร้าง ไม่ฝากไว้กับ prompt อย่างเดียว
            tools = TOOL_SCHEMAS if step < config.MAX_STEPS else \
                [t for t in TOOL_SCHEMAS if t["function"]["name"] in TERMINAL_TOOLS]
            resp = self.chat_fn(self.messages, tools=tools, model=self.model, temperature=self.temperature)
            if getattr(resp, "usage", None):
                usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
                usage["completion_tokens"] += resp.usage.completion_tokens or 0
            msg = resp.choices[0].message
            calls = msg.tool_calls or []

            if not calls:                                   # ตอบข้อความเปล่า = ผิดกติกา harness
                trace.append({"step": step, "type": "text_without_tool", "content": msg.content})
                self.on_event(trace[-1])
                self.messages.append({"role": "assistant", "content": msg.content or ""})
                nudges += 1
                if nudges > 2:
                    status = "no_structured_answer"
                    final = {"answer": msg.content or "", "citations": [], "facts_sufficient": False,
                             "facts_to_verify": ["ระบบไม่ได้ส่งคำตอบผ่านขั้นตอนตรวจสอบ"], "legal_issue": ""}
                    violations = policy.check_final(final, executor)
                    break
                wrote_call = any(t in (msg.content or "") for t in ("submit_final_answer(", "request_more_facts("))
                self.messages.append({"role": "user", "content":
                                      "(ระบบ) คุณพิมพ์ชื่อ tool เป็นข้อความ ระบบจึงไม่ได้รับคำตอบ ให้เรียกเป็น tool call จริง"
                                      if wrote_call else
                                      "(ระบบ) กรุณาใช้ tool ค้นตัวบท แล้วจบด้วย submit_final_answer หรือ request_more_facts"})
                continue

            self.messages.append({"role": "assistant", "content": msg.content or "",
                                  "tool_calls": [_tc_dict(tc) for tc in calls]})
            done = False
            for tc in calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    parse_err = None
                except json.JSONDecodeError as e:
                    args, parse_err = {}, f"arguments ไม่ใช่ JSON ที่ถูกต้อง: {e}"
                out = {"error": parse_err} if parse_err else executor.run(name, args)
                event = {"step": step, "type": "tool_call", "tool": name, "arguments": args, "result": out}

                if name == "submit_final_answer" and not parse_err and "error" not in out:
                    v = policy.check_final(out, executor)
                    event["policy_violations"] = v
                    if policy.blocking(v) and retries_left > 0:
                        retries_left -= 1
                        # แนบตัวบทจริงของมาตราที่ยกข้อความผิด ให้โมเดลคัดลอกให้ตรง (self-correction)
                        exact = {x["section_no"]: executor.seen_sections[x["section_no"]]["text"][:1500]
                                 for x in v if x["rule"] == "QUOTE_MATCHES"
                                 and x["section_no"] in executor.seen_sections}
                        out = {"status": "rejected_by_policy", "violations": v,
                               "exact_section_text": exact,
                               "instruction": "แก้คำตอบ: ตัวเลขและข้อความต้องตรงกับ exact_section_text "
                                              "ถ้ายกข้อความไม่ตรงให้เว้น quoted_text ว่าง และเอาเลขมาตราที่ยืนยันไม่ได้ออก "
                                              "แล้ว submit_final_answer ใหม่"}
                        event["result_sent_to_model"] = out
                    else:
                        final, violations = out, v
                        status = "blocked_by_policy" if policy.blocking(v) else "answered"
                        done = True
                elif name == "request_more_facts" and "error" not in out:
                    final, status, done = out, "need_more_facts", True

                trace.append(event)
                self.on_event(event)
                self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                      "content": json.dumps(out, ensure_ascii=False)[:config.TOOL_RESULT_MAX_CHARS]})
            if done:
                break
            left = config.MAX_STEPS - step
            if 0 < left <= config.BUDGET_WARN_AT:          # กันค้นวนจนหมดรอบโดยไม่ส่งคำตอบ
                self.messages.append({"role": "user", "content":
                                      f"(ระบบ) เหลืออีก {left} รอบ ให้ส่ง submit_final_answer หรือ request_more_facts "
                                      "จากข้อมูลที่มีตอนนี้ ถ้าไม่พบในตัวบทให้ตอบว่าไม่พบ"})

        if final is not None:                               # ให้รอบถัดไปรู้ว่าตอบอะไรไปแล้ว
            if status == "blocked_by_policy":               # คำตอบที่ไม่ผ่านต้องไม่กลายเป็นบริบทของรอบถัดไป
                summary = "(คำตอบรอบที่แล้วไม่ผ่านการตรวจการอ้างอิง จึงไม่ได้แสดงต่อผู้ใช้)"
            else:
                summary = final.get("answer") or "ถามข้อเท็จจริงเพิ่ม: " + "; ".join(final.get("questions", []))
            self.messages.append({"role": "assistant", "content": summary})
        result.update(status=status, final=final, policy_violations=violations, usage=usage,
                      steps=len({e["step"] for e in trace}))
        return self._finish(result, executor, t0)

    # ---------- สรุปผลสำหรับหน้าจอ (ไม่มีเปอร์เซ็นต์ความมั่นใจ) ----------
    def _finish(self, result, executor, t0):
        final = result.get("final") or {}
        cites = []
        for c in final.get("citations", []) or []:
            no = policy.norm_section_no(c.get("section_no", ""))
            doc = self.store.get(no)
            cites.append({"law_name": doc["law_name"] if doc else config.LAW_NAME, "section_no": no,
                          "exists": bool(doc), "in_context": no in executor.seen_sections,
                          "source_url": doc["source_url"] if doc else None,
                          "source_retrieved": doc["source_retrieved"] if doc else None,
                          "checked_on": doc["checked_on"] if doc else None,
                          "version_status": [f["message"] for f in (doc or {}).get("version_flags", [])],
                          "version_flags": [f["message"] for f in (doc or {}).get("version_flags", [])
                                            if f["status"] != "updated"]})
        grounded = [c for c in cites if c["exists"] and c["in_context"]]
        status = result.get("status")
        result["citations_rendered"] = cites
        result["status_panel"] = {
            "ข้อมูลเพียงพอหรือยัง": ("ยังไม่พอ — ระบบถามข้อเท็จจริงเพิ่ม" if status == "need_more_facts"
                                    else "เพียงพอ" if final.get("facts_sufficient") else "ยังไม่พอ"),
            "พบแหล่งอ้างอิงหรือไม่": (f"พบ {len(grounded)} มาตราที่ยืนยันกับฐานข้อมูลแล้ว" if grounded
                                     else f"ยังไม่อ้างมาตรา (ค้นตัวบทแล้ว {len(executor.seen_sections)} มาตรา)"
                                     if status == "need_more_facts"
                                     else "ไม่พบแหล่งอ้างอิงที่ยืนยันได้"),
            "ข้อเท็จจริงที่ยังต้องตรวจสอบ": final.get("facts_to_verify") or final.get("missing_facts") or [],
            "คำเตือนฉบับแก้ไข": sorted({m for c in cites for m in c["version_flags"]}),
            "ผลตรวจการอ้างอิง": ("ผ่าน" if not policy.blocking(result.get("policy_violations", []))
                               else "ไม่ผ่าน — ไม่แสดงคำตอบ"),
        }
        result["context_returned"] = [
            {"section_no": d["section_no"], "chapter": d["chapter"], "text": d["text"],
             "source_url": d["source_url"], "source_retrieved": d["source_retrieved"]}
            for d in executor.seen_sections.values()]
        result["latency_s"] = round(time.time() - t0, 2)
        result["timestamp"] = datetime.now().isoformat(timespec="seconds")
        self._log(result)
        return result

    def _log(self, result):
        config.RUNS_DIR.mkdir(exist_ok=True)
        safe = {k: v for k, v in result.items() if k != "pii"}
        safe["pii_decisions"] = result["pii"]["decisions"]          # ไม่เก็บข้อความต้นฉบับ
        path = config.RUNS_DIR / f"{datetime.now():%Y%m%d}_{self.id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(safe, ensure_ascii=False, default=str) + "\n")
