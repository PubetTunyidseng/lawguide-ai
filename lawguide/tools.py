"""Tools ที่ agent เรียกได้ (tool name + JSON arguments จริง ตาม OpenAI tool calling)

เครื่องมือค้นข้อมูล (คืน context ให้โมเดล):
  search_law_sections   ค้นตัวบทด้วยคำถาม/คำสำคัญ
  get_law_section       ดึงมาตราตามเลข
  verify_citation       ตรวจว่ามาตรามีอยู่จริง และข้อความที่ยกมาตรงกับตัวบทหรือไม่
  find_agency_procedure หน่วยงาน/ช่องทางดำเนินการ ตามประเภทปัญหา

เครื่องมือปิดรอบ (terminal):
  request_more_facts    ถามข้อเท็จจริงเพิ่มเมื่อข้อมูลยังไม่พอ
  submit_final_answer   ส่งคำตอบสุดท้ายแบบมีโครงสร้าง (harness ตรวจการอ้างอิงต่อ)

หมายเหตุ: ค้นคำพิพากษาศาลฎีกาไม่อยู่ใน MVP ตามคำแนะนำอาจารย์ (ต้องใช้ชุดที่ผู้เชี่ยวชาญตรวจแล้ว)
"""
import json
import re

from . import config
from .store import norm_section_no

AGENCIES = json.loads((config.DATA_DIR / "agencies.json").read_text(encoding="utf-8"))["procedures"]
TERMINAL_TOOLS = {"request_more_facts", "submit_final_answer"}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_law_sections",
        "description": "ค้นตัวบท พ.ร.บ.คุ้มครองแรงงาน จากฐานข้อมูล คืนมาตราที่เกี่ยวข้องพร้อมข้อความเต็ม แหล่งที่มา "
                       "วันที่ตรวจสอบ และคำเตือนกรณีมาตราอาจไม่ใช่ฉบับปัจจุบัน ใช้ภาษาไทยเป็นคำค้น",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "คำค้นภาษาไทย เช่น 'ลาคลอด ค่าจ้าง' หรือ 'ค่าชดเชย เลิกจ้าง'"},
            "top_k": {"type": "integer", "description": "จำนวนมาตราที่ต้องการ (1-10)"}},
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
                       "ต้องเรียกก่อนอ้างมาตราใดในคำตอบสุดท้าย",
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
            "missing_facts": {"type": "array", "items": {"type": "string"}},
            "questions": {"type": "array", "items": {"type": "string"}},
            "partial_answer": {"type": "string", "description": "ข้อมูลเบื้องต้นที่ตอบได้แล้วจากตัวบท (ถ้ามี)"}},
            "required": ["missing_facts", "questions"]}}},
    {"type": "function", "function": {
        "name": "submit_final_answer",
        "description": "ส่งคำตอบสุดท้าย ทุกข้อความทางกฎหมายต้องมาจากมาตราที่ได้จาก tool ในรอบนี้และผ่าน verify_citation แล้ว",
        "parameters": {"type": "object", "properties": {
            "legal_issue": {"type": "string", "description": "ประเด็นกฎหมายที่จำแนกได้ เช่น 'สิทธิลาคลอด'"},
            "answer": {"type": "string", "description": "คำตอบภาษาไทย อ้าง 'มาตรา N' ทุกครั้งที่ใช้ตัวบท"},
            "citations": {"type": "array", "items": {"type": "object", "properties": {
                "section_no": {"type": "string"}, "quoted_text": {"type": "string"}},
                "required": ["section_no"]}},
            "facts_sufficient": {"type": "boolean", "description": "ข้อเท็จจริงที่ผู้ใช้ให้มาเพียงพอต่อคำตอบนี้หรือไม่"},
            "facts_to_verify": {"type": "array", "items": {"type": "string"},
                                "description": "ข้อเท็จจริงที่ยังต้องตรวจสอบ/ยืนยัน"},
            "next_steps": {"type": "array", "items": {"type": "string"}}},
            "required": ["legal_issue", "answer", "citations", "facts_sufficient", "facts_to_verify"]}}},
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

    def __init__(self, store):
        self.store = store
        self.seen_sections: dict[str, dict] = {}     # section_no -> doc ที่ถูกคืนเป็น context
        self.verified: dict[str, dict] = {}          # section_no -> ผล verify_citation ล่าสุด

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
        k = max(1, min(int(top_k or config.TOP_K), 10))
        hits = self.store.search(query, k)
        self._remember(hits)
        per = max(500, config.TOOL_RESULT_MAX_CHARS // max(len(hits), 1))
        return {"query": query, "n_results": len(hits),
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
        return {"issue_type": issue_type, **p,
                "note": "related_sections เป็นเพียงจุดเริ่มค้น ต้องดึงตัวบทด้วย get_law_section ก่อนอ้าง"}

    def t_request_more_facts(self, missing_facts, questions, partial_answer: str = ""):
        return {"status": "need_more_facts", "missing_facts": missing_facts, "questions": questions,
                "partial_answer": partial_answer}

    def t_submit_final_answer(self, legal_issue, answer, citations, facts_sufficient, facts_to_verify,
                              next_steps=None):
        return {"status": "final", "legal_issue": legal_issue, "answer": answer, "citations": citations,
                "facts_sufficient": facts_sufficient, "facts_to_verify": facts_to_verify,
                "next_steps": next_steps or []}
