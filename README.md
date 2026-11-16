# LawGuide — Agentic AI ให้ข้อมูลกฎหมายแรงงานเบื้องต้น

Mini project วิชา NLP · ขอบเขต MVP: **พ.ร.บ.คุ้มครองแรงงาน พ.ศ. 2541 (และฉบับแก้ไข)** เพียงฉบับเดียว ตามข้อแนะนำอาจารย์

## สิ่งที่ระบบทำ
1. รับสถานการณ์ปัญหาแรงงานภาษาไทย → **mask ข้อมูลส่วนบุคคล** ก่อนส่ง LLM
2. Agent (LLM ผ่าน API) เลือก tool เอง: ค้นตัวบท → ดึงมาตรา → ตรวจการอ้างอิง → หาหน่วยงาน
3. ถ้าข้อเท็จจริงไม่พอ **ถามกลับ** (`request_more_facts`)
4. คำตอบสุดท้ายผ่าน **citation policy** แบบ deterministic — อ้างมาตราที่ไม่มีจริง/ไม่ได้ดึงมา = ตีกลับให้แก้ แก้ไม่ได้ = ไม่แสดงคำตอบ
5. ทุกคำตอบแสดง ชื่อกฎหมาย · มาตรา · แหล่งที่มา · วันที่ดึงตัวบท · วันที่ตรวจ และสถานะข้อมูลแบบไม่มีเปอร์เซ็นต์

## โครงสร้าง
```
UI (Gradio, app.py)
  └─ Harness (lawguide/harness.py)  ← วงรอบ LLM ↔ tools, log ทุก step
       ├─ PII guard (pii.py)            rule + (เลือกได้) LLM extract → decision table → mask
       ├─ LLM API (llm.py)              OpenAI-compatible: OpenRouter / Typhoon / Ollama
       ├─ Tools (tools.py)              tool name + JSON arguments
       │    search_law_sections · get_law_section · verify_citation
       │    find_agency_procedure · request_more_facts · submit_final_answer
       ├─ Citation policy (policy.py)   กฎใน data/citation_policy.json
       └─ External store (store.py)     OpenSearch: BM25 thai analyzer + bge-m3 vector + RRF
Ingest (ingest.py): ตัวบท krisdika → รายมาตรา + เชิงอรรถ → ตรวจฉบับแก้ไขกับ data/amendments.json
Eval (eval/run_eval.py): ถามซ้ำ N รอบ วัด behavior / correctness / faithfulness / hallucination / consistency
```

## เริ่มใช้งาน (คำสั่งเดียว)
```bash
bash run.sh            # venv → ใส่ key → OpenSearch (ถ้ามี Docker) → ingest → test → ถาม 1 ข้อ → เปิดเว็บ
bash run.sh --vector   # เพิ่ม bge-m3 ให้เป็น hybrid search
python ask.py "คำถาม"   # ทดสอบใน Terminal พร้อมแสดง tool calls
```

## เริ่มใช้งาน (ทีละขั้น)
```bash
pip install -r requirements.txt
cp .env.example .env                       # ใส่ LLM_API_KEY
docker compose up -d                       # OpenSearch ที่ localhost:9200
# วางตัวบทที่ data/raw/lpa2541.txt (ดู data/raw/README.md)
python -m lawguide.ingest --retrieved 2026-11-15
python app.py                              # http://localhost:7860
python -m pytest -q tests                  # test แบบไม่เรียก LLM
python eval/run_eval.py --repeats 5 --judge
```
ไม่มี Docker: ตั้ง `BACKEND=local` ใช้ BM25 ในหน่วยความจำแทน (ไม่ถือเป็น External ตาม rubric — ใช้ทดสอบเท่านั้น)

## Map กับเกณฑ์สอบ
| เกณฑ์ | อยู่ที่ไหน |
|---|---|
| LLM เรียกผ่าน API | `lawguide/llm.py` |
| Harness + tool calling จริง | `lawguide/harness.py`, `lawguide/tools.py` · หน้าจอแท็บ ③ |
| External | OpenSearch (`docker-compose.yml`, `store.py`) |
| หน้าจอ + ชี้ final answer vs context | แท็บ ① Final answer · ④ Context จาก tool |
| ห้ามข้อมูลจริง/ชื่อจริง | test set ใช้ชื่อสมมติ · PII guard mask ก่อนส่ง LLM · log ไม่เก็บข้อความต้นฉบับ |
| Harness tuning (ถามซ้ำ 5 ครั้ง) | `eval/run_eval.py --repeats 5` → `citation_jaccard_mean`, `behavior_same_all_runs` |
| Policy | `data/citation_policy.json` + `policy.py` (เปลี่ยนกฎได้ไม่แก้โค้ด) |
| Open-source เทียบ commercial | รัน eval สองรอบด้วย `--model` ต่างกัน `--tag oss/com` |
| Decision model + LLM extract ข้อมูลอ่อนไหว | `data/pii_decision_rules.json` + `pii.py` (`PII_LLM_EXTRACT=1`) |

## ข้อจำกัดที่รู้อยู่
- ไม่มีคำพิพากษาศาลฎีกา (อาจารย์ให้เป็นงานเพิ่มเติม)
- `gold_sections` ใน test set เป็นร่าง ต้องให้ผู้มีความรู้กฎหมายตรวจ
- ทะเบียนฉบับแก้ไข (`amendments.json`) ยืนยันจากข่าวกรมประชาสัมพันธ์ ยังไม่ได้เทียบราชกิจจาฯ ฉบับจริง
- rule ตรวจชื่อคนจับได้เฉพาะชื่อที่มีคำนำหน้า (นาย/นาง/นางสาว) — ชื่อเล่นหรือ "คุณ..." ต้องพึ่ง LLM extractor
