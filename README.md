# LawGuide — Agentic AI ให้ข้อมูลกฎหมายแรงงานเบื้องต้น

Mini project วิชา NLP · ขอบเขต MVP: **พ.ร.บ.คุ้มครองแรงงาน พ.ศ. 2541 และฉบับแก้ไขทั้ง 9 ฉบับ** เพียงฉบับเดียว

## ขอบเขตและหลักการออกแบบ

| หลักการ | ทำในระบบอย่างไร | อยู่ที่ไหน |
|---|---|---|
| กฎหมายด้านเดียว | ฐานข้อมูลมีเฉพาะ พ.ร.บ.คุ้มครองแรงงาน คำถามนอกขอบเขต (ที่ดิน อาญา มรดก) ตอบว่าอยู่นอกขอบเขต | `config.py` `LAW_NAME`, prompt กฎข้อ 8, testset q08 |
| ตัวบทจากแหล่งทางการ ตรวจฉบับแก้ไขกับราชกิจจาฯ | ใช้ฉบับรวมกฤษฎีกาเป็นแหล่งหลัก ทะเบียนฉบับแก้ไขเทียบกับ PDF ราชกิจจาฯ เทียบข้ามแหล่งทีละมาตรา จุดที่ไม่ตรงแนบเป็นคำเตือน ไม่แก้ตัวบทเอง | `data/raw/README.md`, `data/sources/`, `scripts/` |
| ไม่ใช้คำพิพากษาใน MVP | คำพิพากษาที่ข้อเท็จจริงคล้ายกันไม่ได้แปลว่าใช้กับกรณีของผู้ใช้ได้ ถ้าเพิ่มต้องใช้ชุดที่ผู้มีความรู้กฎหมายตรวจแล้ว | ข้อจำกัดด้านล่าง |
| RAG ไม่รับประกันความเป็นปัจจุบัน | ทุกคำตอบแสดงชื่อกฎหมาย มาตรา แหล่งที่มา วันที่ดึงตัวบท วันที่ตรวจ · มาตราที่อาจไม่ใช่ฉบับล่าสุดมีธงเตือน | แท็บ ① ② ใน `app.py`, `ingest.check_versions` |
| ไม่แสดงความมั่นใจเป็นเปอร์เซ็นต์ | แสดง "ข้อมูลเพียงพอหรือยัง" "พบแหล่งอ้างอิงหรือไม่" "ข้อเท็จจริงที่ยังต้องตรวจสอบ" · "พบแหล่งอ้างอิง" คำนวณจากผลตรวจกับฐานข้อมูล ส่วนข้อเท็จจริงพอไหม/ต้องตรวจอะไรเป็นใช่-ไม่ใช่และรายการจากโมเดล ไม่มีตัวเลขความมั่นใจ | `harness._finish` `status_panel` |
| Tool ชัดเจน | ค้นตัวบท · ดึงมาตรา · ตรวจการอ้างอิง · ค้นหน่วยงาน/ขั้นตอน · ถามข้อเท็จจริงเพิ่ม · ส่งคำตอบ | `lawguide/tools.py` |
| ห้ามสร้างเลขมาตราที่ไม่มีจริง | policy ตรวจทุกเลขมาตราและข้อความที่ยกก่อนแสดงผล ผิดกฎ = ตีกลับให้แก้ แก้ไม่ได้ = ไม่แสดงคำตอบ | `lawguide/policy.py`, `data/citation_policy.json` |
| ข้อมูลส่วนบุคคล | เตือนผู้ใช้ไม่ให้ส่งชื่อ/เลขบัตร · mask ก่อนส่ง LLM · log ไม่เก็บข้อความต้นฉบับ | `lawguide/pii.py`, `data/pii_decision_rules.json` |
| Disclaimer ไม่แทนการควบคุมความถูกต้อง | แสดง disclaimer ทุกหน้า แต่ความถูกต้องคุมด้วย policy + eval ไม่ได้พึ่งข้อความเตือน | `harness.DISCLAIMER`, `docs/eval_report.md` |

## สิ่งที่ระบบทำ
1. รับสถานการณ์ปัญหาแรงงานภาษาไทย → **mask ข้อมูลส่วนบุคคล** ก่อนส่ง LLM
2. Agent (LLM ผ่าน API) เลือก tool เอง: ค้นตัวบท → ดึงมาตรา → (ตรวจการอ้างอิง) → หาหน่วยงาน
3. ถ้าข้อเท็จจริงไม่พอ **ถามกลับ** (`request_more_facts`)
4. คำตอบสุดท้ายผ่าน **citation policy** แบบ deterministic ก่อนแสดงผู้ใช้
5. ทุกคำตอบแสดง ชื่อกฎหมาย · มาตรา · แหล่งที่มา · วันที่ดึงตัวบท · วันที่ตรวจ · สถานะข้อมูลแบบไม่มีเปอร์เซ็นต์

## โครงสร้าง
```
UI (Gradio, app.py)  แท็บ ① Final answer ② สถานะข้อมูล ③ Tool calls ④ Context ⑤ PII ⑥ Raw trace
  └─ Harness (lawguide/harness.py)  ← วงรอบ LLM ↔ tools, log ทุก step
       ├─ PII guard (pii.py)            rule + (เลือกได้) LLM extract → decision table → mask
       ├─ LLM API (llm.py)              OpenAI-compatible: OpenRouter / Typhoon / Ollama
       ├─ Tools (tools.py)              tool name + JSON arguments (+ guard: top_k ขั้นต่ำ, กันค้นซ้ำ)
       ├─ Citation policy (policy.py)   กฎใน data/citation_policy.json
       └─ External store (store.py)     OpenSearch: BM25 thai analyzer + bge-m3 vector + RRF
Ingest (ingest.py): ตัวบทกฤษฎีกา → รายมาตรา + เชิงอรรถ + กราฟอ้างอิงระหว่างมาตรา → ตรวจกับทะเบียนฉบับแก้ไข
Eval (eval/): ถามซ้ำ N รอบ วัดแยกตัว — behavior / issue type / correctness / faithfulness / hallucination / consistency
```

## Tools
| tool | ทำอะไร | ประเภท |
|---|---|---|
| `search_law_sections` | ค้นตัวบท (BM25 หรือ hybrid BM25 + vector) คืนข้อความเต็ม แหล่งที่มา วันที่ตรวจ ธงฉบับแก้ไข และ `cited_by` | ค้นข้อมูล |
| `get_law_section` | ดึงมาตราตามเลข ได้ถึง 6 มาตราต่อครั้ง มาตราที่ไม่มีตอบว่าไม่มี | ค้นข้อมูล |
| `verify_citation` | ตรวจว่ามาตรามีจริง และข้อความที่ยกตรงกับตัวบท | ตรวจสอบ |
| `find_agency_procedure` | หน่วยงาน ช่องทางร้องเรียน แบบฟอร์ม ตามประเภทปัญหา | ค้นข้อมูล |
| `request_more_facts` | ถามข้อเท็จจริงที่ขาด พร้อม `issue_type` | จบรอบ |
| `submit_final_answer` | ส่งคำตอบแบบมีโครงสร้าง: `issue_type`, ประเด็น, คำตอบ, citations, ข้อเท็จจริงพอไหม, สิ่งที่ต้องตรวจ | จบรอบ |

## Harness: กฎที่คุมด้วยโค้ด ไม่ได้ฝากไว้กับ prompt
| กลไก | ทำไม |
|---|---|
| `tool_choice=required` + เตือนเมื่อพิมพ์ tool เป็นข้อความ | โมเดลเคยพิมพ์ `submit_final_answer(...)` เป็นข้อความแทนการเรียกจริง |
| step สุดท้ายเหลือเฉพาะ tool ปิดรอบ + เตือนเมื่อใกล้หมด step | กันค้นวนจนหมดรอบโดยไม่มีคำตอบ |
| `MIN_TOP_K` | โมเดลเคยขอ `top_k=1` จนมองไม่เห็นมาตราที่ถูก |
| `DUPLICATE_QUERY=skip` | โมเดลเคยค้นคำชุดเดิมซ้ำหลายครั้ง |
| policy ตีกลับพร้อมแนบตัวบทจริง (สูงสุด `MAX_POLICY_RETRIES`) | ให้โมเดลแก้จากตัวบท ไม่ใช่เดาใหม่ |
| คำตอบที่ไม่ผ่าน policy ไม่ถูกเก็บเป็นบริบทของคำถามถัดไป | กันข้อมูลผิดไหลต่อ |

## Citation policy (`data/citation_policy.json` เปิด/ปิด/เปลี่ยน severity ได้โดยไม่แก้โค้ด)
| กฎ | ตรวจ |
|---|---|
| `CITE_EXISTS` | มาตราที่อ้างมีอยู่จริงในฐานข้อมูล |
| `CITE_IN_CONTEXT` | มาตราที่อ้างถูกดึงมาเป็น context ในรอบนี้ |
| `ANSWER_MENTIONS_GROUNDED` | ทุก "มาตรา N" ในเนื้อคำตอบมีจริงและอยู่ใน context (ยกเว้นประโยคปฏิเสธหลังตรวจแล้วว่าไม่มีจริง) |
| `QUOTE_MATCHES` | ข้อความที่ยกตรงกับตัวบท (ละด้วย ... ได้ แต่ต้องเรียงตามเดิม) |
| `NUMBER_GROUNDED` | ตัวเลขที่มีหน่วย (วัน ครั้ง เท่า ปี เวลา ร้อยละ) ต้องพบในมาตราที่อ้าง หรือในข้อความผู้ใช้ |
| `HAS_CITATION` | คำตอบที่บอกว่าข้อเท็จจริงพอ ต้องอ้างอย่างน้อย 1 มาตรา |
| `VERSION_FLAG` (warn) | มาตราที่อ้างอาจไม่ใช่ฉบับปัจจุบัน |

## เริ่มใช้งาน
```bash
bash run.sh            # venv → ใส่ key → OpenSearch (ถ้ามี Docker) → ingest → test → ถาม 1 ข้อ → เปิดเว็บ
bash run.sh --vector   # เพิ่ม bge-m3 ให้เป็น hybrid search
python ask.py "คำถาม"   # ทดสอบใน Terminal พร้อมแสดง tool calls
python -m pytest -q tests                  # test แบบไม่เรียก LLM
bash eval/run_v4.sh                        # eval open-source vs commercial + judge
python eval/compare.py v3-hybrid v4-oss    # เทียบผลลัพธ์หลายรอบ
python eval/trajectory.py v3-hybrid v4-oss # เทียบการทำงานระหว่างทาง (tool, retrieval, step, ค่าใช้จ่าย) จาก log
```
ไม่มี Docker: ตั้ง `BACKEND=local` ใช้ BM25 ในหน่วยความจำแทน (ใช้ทดสอบเท่านั้น ไม่ใช่ External store)

## ผลการประเมิน
รายละเอียดทุกรอบ สาเหตุที่พัง และกลไกที่ใช้แก้: [`docs/eval_report.md`](docs/eval_report.md) · ผลดิบ: `eval/results/`

| รอบ | behavior ok | สิ่งที่เปลี่ยน |
|---|---|---|
| v1 | 38/50 | รอบแรกหลังเปลี่ยนเป็น Qwen3 |
| v2 | 48/50 | บังคับ tool call, เตือนงบ step, quote แบบละข้อความ, ปฏิเสธมาตราที่ไม่มีจริง |
| v3 (bm25 / hybrid) | 70/75 · 70/75 | ดึงหลายมาตราต่อครั้ง, step สุดท้ายเหลือเฉพาะ tool ปิดรอบ, เพิ่มคำถามภาษาพูด 5 ข้อ |
| v4-oss (Qwen3) | 72/75 | top_k ขั้นต่ำ, กันค้นซ้ำ, `NUMBER_GROUNDED`, `issue_type`, LLM judge · issue type ok 73/75 |
| v4-com (gpt-4.1-mini) | 64/75 | harness เดียวกัน เปลี่ยนแค่โมเดล · เร็วกว่า 3 เท่า ค่าใช้จ่ายต่อคำถามเท่ากัน ถามกลับเกินจำเป็น |

## Map กับเกณฑ์สอบ
| เกณฑ์ | อยู่ที่ไหน |
|---|---|
| LLM เรียกผ่าน API | `lawguide/llm.py` |
| Harness + tool calling จริง | `lawguide/harness.py`, `lawguide/tools.py` · หน้าจอแท็บ ③ |
| External | OpenSearch (`docker-compose.yml`, `store.py`) |
| หน้าจอ + ชี้ final answer vs context | แท็บ ① Final answer · ④ Context จาก tool |
| ห้ามข้อมูลจริง/ชื่อจริง | test set ใช้ชื่อสมมติ · PII guard mask ก่อนส่ง LLM · log ไม่เก็บข้อความต้นฉบับ |
| Harness tuning (ถามซ้ำ 5 ครั้ง) | `eval/run_eval.py --repeats 5` → `behavior_same_all_runs`, `citation_jaccard_mean` · ประวัติการปรับใน `docs/eval_report.md` |
| Policy | `data/citation_policy.json` + `policy.py` |
| Open-source เทียบ commercial | `bash eval/run_v4.sh` → Qwen3 72/75 vs gpt-4.1-mini 64/75 (harness/judge เดียวกัน) |
| Decision model + LLM extract ข้อมูลอ่อนไหว | `data/pii_decision_rules.json` + `pii.py` (`PII_LLM_EXTRACT=1`) |

## ข้อจำกัดที่รู้อยู่
- ไม่มี tool ค้นคำพิพากษาศาลฎีกา (เหตุผลในตารางหลักการด้านบน)
- `gold_sections` และ `key_facts` ใน test set เป็นร่าง ต้องให้ผู้มีความรู้กฎหมายตรวจ
- คำในชีวิตประจำวันที่ไม่ตรงกับศัพท์ตัวบท (เช่น ลาพักร้อน → หยุดพักผ่อนประจำปี) ยังค้นไม่เจอ ใน v4 q14 ผ่านได้เพราะโมเดลตามการอ้างอิงในตัวบท หรือไล่ดึงมาตราเอง ไม่ใช่เพราะการค้นดีขึ้น ดู `docs/eval_report.md`
- ฉบับที่ 9 (2568) อ่านเนื้อความจาก PDF ราชกิจจาฯ ไม่ได้เพราะฟอนต์ วันมีผลจึงยืนยันจากภาคผนวกฉบับกฤษฎีกา
- rule ตรวจชื่อคนจับได้เฉพาะชื่อที่มีคำนำหน้า (นาย/นาง/นางสาว) ชื่อเล่นหรือ "คุณ..." ต้องพึ่ง LLM extractor
- `NUMBER_GROUNDED` ตรวจเฉพาะตัวเลขที่มีหน่วยในเนื้อคำตอบ ตัวเลขที่คำนวณต่อ (เช่น ค่าจ้าง × 90 วัน) จะถูกตีกลับให้ระบุที่มา
