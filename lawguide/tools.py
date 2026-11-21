"""Tools ที่ agent เรียกได้ (tool name + JSON arguments จริง ตาม OpenAI tool calling)

เครื่องมือค้นข้อมูล (คืน context ให้โมเดล):
  search_law_sections   ค้นตัวบทด้วยคำถาม/คำสำคัญ
  get_law_section       ดึงมาตราตามเลข
  verify_citation       ตรวจว่ามาตรามีอยู่จริง และข้อความที่ยกมาตรงกับตัวบทหรือไม่
  find_agency_procedure หน่วยงาน/ช่องทางดำเนินการ ตามประเภทปัญหา

เครื่องมือปิดรอบ (terminal):
  request_more_facts    ถามข้อเท็จจริงเพิ่มเมื่อข้อมูลยังไม่พอ
  submit_final_answer   ส่งคำตอบสุดท้ายแบบมีโครงสร้าง (harness ตรวจการอ้างอิงต่อ)

หมายเหตุ: ไม่มี tool ค้นคำพิพากษาศาลฎีกาใน MVP — คำพิพากษาที่ข้อเท็จจริงคล้ายกันไม่ได้แปลว่าใช้กับกรณีของผู้ใช้ได้
ถ้าจะเพิ่มต้องใช้ชุดที่ผู้มีความรู้กฎหมายตรวจแล้ว และต้องมี policy ตรวจเลขคำพิพากษาแบบเดียวกับเลขมาตรา

harness guard ที่อยู่ในชั้นนี้ (ตั้งค่าใน .env):
  MIN_TOP_K        โมเดลขอ top_k ต่ำกว่านี้ไม่ได้
  DUPLICATE_QUERY  ค้นคำชุดเดิมซ้ำในคำถามเดียวกัน → ไม่ค้นซ้ำ ส่งผลเดิมพร้อมคำแนะนำ
"""
import json
import re

from . import config
from .store import _tok, norm_section_no

AGENCIES = json.loads((config.DATA_DIR / "agencies.json").read_text(encoding="utf-8"))["procedures"]
TERMINAL_TOOLS = {"request_more_facts", "submit_final_answer"}
ISSUE_TYPES = sorted(AGENCIES) + ["out_of_scope"]
ISSUE_TYPE_PARAM = {"type": "string", "enum": ISSUE_TYPES,
                    "description": "ประเภทประเด็นกฎหมาย (ใช้ประเมินการจำแนกประเด็น) out_of_scope = ไม่ใช่เรื่อง พ.ร.บ.คุ้มครองแรงงาน"}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_law_sections",
        "description": "ค้นตัวบท พ.ร.บ.คุ้มครองแรงงาน จากฐานข้อมูล คืนมาตราที่เกี่ยวข้องพร้อมข้อความเต็ม แหล่งที่มา "
                       "วันที่ตรวจสอบ และคำเตือนกรณีมาตราอาจไม่ใช่ฉบับปัจจุบัน ใช้ภาษาไทยเป็นคำค้น",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "คำค้นภาษาไทย เช่น 'ลาคลอด ค่าจ้าง' หรือ 'ค่าชดเชย เลิกจ้าง'"},
            "top_k": {"type": "integer", "description": f"จำนวนมาตราที่ต้องการ (ระบบคืนอย่างน้อย {config.MIN_TOP_K} สูงสุด {config.MAX_TOP_K})"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_law_section",
        "description": "ดึงข้อความเต็มของมาตราตามเลขมาตรา ดึงหลายมาตราได้ในครั้งเดียว เช่น ['70', '123', '124']",
        "parameters": {"type": "object", "properties": {
            "section_nos": {"type": "array", "items": {"type": "string"},
                            "description": "เลขมาตรา ไม่ต้องมีคำว่า 'มาตรา' (สูงสุด 6 มาตรา)"},
            "section_no": {"type": "string", "description": "ใช้เมื่อดึงมาตราเดียว"}}}}},
    {"type": "function", "function": {
        "name": "verify_citation",
        "description": "ตรวจว่ามาตรานี้มีอยู่จริงในฐานข้อมูล และ (ถ้าให้ข้อความมา) ข้อความที่จะยกอ้างปรากฏอยู่ในมาตรานั้นจริง "
                       "ใช้เมื่อผู้ใช้อ้างเลขมาตรามาเอง หรือเมื่อต้องการตรวจข้อความก่อนยกอ้าง "
                       "(ตอนส่งคำตอบ ระบบตรวจทุกการอ้างอิงซ้ำให้อยู่แล้ว)",
        "parameters": {"type": "object", "properties": {
            "section_no": {"type": "string"},
            "quoted_text": {"type": "string", "description": "ข้อความสั้น ๆ จากตัวบทที่จะยกอ้าง (ไม่บังคับ)"}},
            "required": ["section_no"]}}},
    {"type": "function", "function": {
        "name": "find_agency_procedure",
        "description": "ค้นหน่วยงานและช่องทางดำเนินการตามประเภทปัญหา พร้อมเลขมาตราที่เกี่ยวข้องที่ควรดึงมาตรวจ",
        "parameters": {"type": "object", "properties": {
            "issue_type": {"type": "string", "enum": sorted(AGENCIES)}},
            "required": ["issue_type"]}}},
    {"type": "function", "function": {
        "name": "request_more_facts",
        "description": "ใช้เมื่อข้อเท็จจริงยังไม่พอจะตอบได้ถูกต้อง ระบุว่าขาดข้อเท็จจริงอะไรและคำถามที่จะถามผู้ใช้ (จบรอบนี้)",
        "parameters": {"type": "object", "properties": {
            "issue_type": ISSUE_TYPE_PARAM,
            "missing_facts": {"type": "array", "items": {"type": "string"}},
            "questions": {"type": "array", "items": {"type": "string"}},
            "partial_answer": {"type": "string", "description": "ข้อมูลเบื้องต้นที่ตอบได้แล้วจากตัวบท (ถ้ามี)"}},
            "required": ["issue_type", "missing_facts", "questions"]}}},
    {"type": "function", "function": {
        "name": "submit_final_answer",
        "description": "ส่งคำตอบสุดท้าย ทุกข้อความทางกฎหมายต้องมาจากมาตราที่ได้จาก tool ในรอบนี้ "
                       "ระบบจะตรวจเลขมาตรา ข้อความที่ยก และตัวเลขในคำตอบกับตัวบทก่อนแสดงผู้ใช้",
        "parameters": {"type": "object", "properties": {
            "issue_type": ISSUE_TYPE_PARAM,
            "legal_issue": {"type": "string", "description": "ประเด็นกฎหมายที่จำแนกได้ เช่น 'สิทธิลาคลอด'"},
            "answer": {"type": "string", "description": "คำตอบภาษาไทย อ้าง 'มาตรา N' ทุกครั้งที่ใช้ตัวบท"},
            "citations": {"type": "array", "items": {"type": "object", "properties": {
                "section_no": {"type": "string"}, "quoted_text": {"type": "string"}},
                "required": ["section_no"]}},
            "facts_sufficient": {"type": "boolean", "description": "ข้อเท็จจริงที่ผู้ใช้ให้มาเพียงพอต่อคำตอบนี้หรือไม่"},
            "facts_to_verify": {"type": "array", "items": {"type": "string"},
                                "description": "ข้อเท็จจริงที่ยังต้องตรวจสอบ/ยืนยัน"},
            "next_steps": {"type": "array", "items": {"type": "string"}}},
            "required": ["issue_type", "legal_issue", "answer", "citations", "facts_sufficient", "facts_to_verify"]}}},
]


ELLIPSIS = re.compile(r"\.{3,}|…")


def quote_in_text(quote: str, text: str) -> bool:
    """ข้อความที่ยกมาต้องมีอยู่จริงในตัวบท รองรับการละข้อความด้วย ... หรือ …
    แต่ละท่อนต้องมีอยู่จริงและเรียงตามลำดับเดิม (ไม่ยอมให้สลับหรือแต่งเติม)"""
    hay = _norm_text(text)
    pos = 0
    parts = [_norm_text(x) for x in ELLIPSIS.split(quote)]
    parts = [x for x in parts if x]
    if not parts:
        return False
    for part in parts:
        i = hay.find(part, pos)
        if i < 0:
            return False
        pos = i + len(part)
    return True


def _norm_text(s: str) -> str:
    s = s.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789"))
    s = re.sub(r"\[[0-9]+\]", "", s)             # ตัดเลขเชิงอรรถ
    return re.sub(r"\s+", "", s)


def _related(d: dict) -> list:
    return d.get("cited_by", [])[:config.RELATED_MAX]


def _section_view(d: dict, max_chars: int) -> dict:
    return {"law_name": d["law_name"], "section_no": d["section_no"], "chapter": d["chapter"],
            "text": d["text"][:max_chars], "amendment_notes": d.get("amendment_notes", []),
            "cited_by": _related(d),
            "version_flags": [f["message"] for f in d.get("version_flags", [])],
            "source_url": d["source_url"], "official_url": d.get("official_url"), "source_retrieved": d["source_retrieved"],
            "checked_on": d["checked_on"]}


class ToolExecutor:
    """รัน tool และจำว่าในรอบนี้ context ไหนถูกส่งให้โมเดลแล้ว (ใช้ตรวจ faithfulness ของการอ้างอิง)"""

    def __init__(self, store, user_texts: list | None = None):
        self.store = store
        self.seen_sections: dict[str, dict] = {}     # section_no -> doc ที่ถูกคืนเป็น context
        self.verified: dict[str, dict] = {}          # section_no -> ผล verify_citation ล่าสุด
        self.user_texts = list(user_texts or [])     # ข้อความผู้ใช้ (mask แล้ว) ใช้ตรวจว่าตัวเลขมาจากผู้ใช้หรือไม่
        self.agency_texts: list[str] = []            # ผล find_agency_procedure (เบอร์ ระยะเวลา ฯลฯ)
        self.queries: dict[tuple, list] = {}         # คำค้น (ชุดคำ) -> มาตราที่ได้ ใช้จับการค้นซ้ำ
        self.stats = {"searches": 0, "top_k_clamped": 0, "duplicate_queries": 0}

    def _remember(self, docs):
        for d in docs:
            self.seen_sections[d["section_no"]] = d

    def run(self, name: str, args: dict) -> dict:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"error": f"ไม่รู้จัก tool '{name}'"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"argument ไม่ถูกต้องสำหรับ {name}: {e}"}

    # ---------- tools ----------
    def t_search_law_sections(self, query: str, top_k: int | None = None):
        self.stats["searches"] += 1
        key = tuple(sorted(set(_tok(query))))
        if config.DUPLICATE_QUERY == "skip" and key in self.queries:
            self.stats["duplicate_queries"] += 1
            return {"query": query, "duplicate_query": True, "n_results": 0, "results": [],
                    "previous_sections": self.queries[key],
                    "note": f"คำค้นชุดนี้ค้นไปแล้วในคำถามนี้ ได้มาตรา {self.queries[key] or 'ไม่พบ'} ค้นซ้ำจะได้ผลเดิม "
                            "ให้เปลี่ยนคำค้น (คำในชีวิตประจำวันมักไม่ตรงกับศัพท์ที่ตัวบทใช้) "
                            "ดึงมาตราจาก cited_by หรือส่งคำตอบว่าไม่พบในกฎหมายฉบับนี้"}
        asked = int(top_k or config.TOP_K)
        k = max(config.MIN_TOP_K, min(asked, config.MAX_TOP_K))
        if k != asked:
            self.stats["top_k_clamped"] += 1
        hits = self.store.search(query, k)
        new = [h["section_no"] for h in hits if h["section_no"] not in self.seen_sections]
        self._remember(hits)
        self.queries[key] = [h["section_no"] for h in hits]
        per = max(500, config.TOOL_RESULT_MAX_CHARS // max(len(hits), 1))
        return {"query": query, "top_k_requested": asked, "top_k_used": k, "n_results": len(hits),
                "new_sections": new,
                "results": [{**_section_view(h, per), "score": h.get("score"), "retriever": h.get("retriever")}
                            for h in hits],
                "note": "ใช้เฉพาะข้อความในผลลัพธ์นี้เป็นฐานของคำตอบ" if hits else
                        "ไม่พบมาตราที่เกี่ยวข้อง — ห้ามตอบจากความรู้ทั่วไป ให้ถามข้อเท็จจริงเพิ่มหรือบอกว่าไม่พบ"}

    def t_get_law_section(self, section_no: str | None = None, section_nos: list | None = None):
        nos = list(section_nos or []) + ([section_no] if section_no else [])
        if not nos:
            return {"error": "ต้องระบุ section_nos หรือ section_no"}
        nos = nos[:6]
        per = max(800, config.TOOL_RESULT_MAX_CHARS // len(nos))
        out = []
        for no in nos:
            d = self.store.get(no)
            if not d:
                out.append({"found": False, "section_no": norm_section_no(no),
                            "note": "ไม่มีมาตรานี้ในฐานข้อมูล ห้ามอ้างมาตรานี้"})
                continue
            self._remember([d])
            out.append({"found": True, **_section_view(d, per)})
        return out[0] if len(out) == 1 else {"sections": out}

    def t_verify_citation(self, section_no: str, quoted_text: str | None = None):
        no = norm_section_no(section_no)
        d = self.store.get(no)
        res = {"section_no": no, "exists": bool(d)}
        if d:
            self._remember([d])
            res["version_flags"] = [f["message"] for f in d.get("version_flags", [])]
            res["cited_by"] = _related(d)
            if quoted_text:
                res["quote_found"] = quote_in_text(quoted_text, d["text"])
                if not res["quote_found"]:
                    res["note"] = "ข้อความที่ยกมาไม่ตรงกับตัวบท ให้ยกข้อความจากตัวบทจริงหรือสรุปโดยไม่ใส่เครื่องหมายคำพูด"
        else:
            res["note"] = "ไม่มีมาตรานี้ในฐานข้อมูล ห้ามอ้าง"
        self.verified[no] = res
        return res

    def t_find_agency_procedure(self, issue_type: str):
        p = AGENCIES.get(issue_type)
        if not p:
            return {"error": f"issue_type ต้องเป็นหนึ่งใน {sorted(AGENCIES)}"}
        self.agency_texts.append(json.dumps(p, ensure_ascii=False))
        return {"issue_type": issue_type, **p,
                "note": "related_sections เป็นเพียงจุดเริ่มค้น ต้องดึงตัวบทด้วย get_law_section ก่อนอ้าง"}

    def t_request_more_facts(self, missing_facts, questions, partial_answer: str = "", issue_type: str = ""):
        return {"status": "need_more_facts", "issue_type": issue_type, "missing_facts": missing_facts,
                "questions": questions, "partial_answer": partial_answer}

    def t_submit_final_answer(self, legal_issue, answer, citations, facts_sufficient, facts_to_verify,
                              next_steps=None, issue_type: str = ""):
        return {"status": "final", "issue_type": issue_type, "legal_issue": legal_issue, "answer": answer,
                "citations": citations, "facts_sufficient": facts_sufficient, "facts_to_verify": facts_to_verify,
                "next_steps": next_steps or []}
