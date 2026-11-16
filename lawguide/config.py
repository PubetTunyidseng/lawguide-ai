"""ค่าตั้งทั้งหมดของระบบ — ปรับผ่าน .env หรือ environment variable ไม่ hardcode ในโค้ด"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
RUNS_DIR = ROOT / "runs"          # log ของแต่ละรอบการถาม (traceable)


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in "'\"" and v[:1] and v.count(v[0]) >= 2:
            v = v[1:v.index(v[0], 1)]
        else:
            v = "" if v.startswith("#") else v.split(" #", 1)[0].split("\t#", 1)[0].strip()   # ตัด comment ท้ายบรรทัด
        os.environ.setdefault(k.strip(), v)


_load_dotenv()


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# ---------- LLM (OpenAI-compatible: OpenRouter / Typhoon / vLLM / Ollama) ----------
LLM_BASE_URL = env("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = env("LLM_API_KEY", os.environ.get("OPENROUTER_API_KEY", ""))
LLM_MODEL = env("LLM_MODEL", "qwen/qwen-2.5-72b-instruct")
JUDGE_MODEL = env("JUDGE_MODEL", LLM_MODEL)          # ใช้ตอน eval faithfulness
TEMPERATURE = float(env("TEMPERATURE", "0.2"))
LLM_TIMEOUT_S = float(env("LLM_TIMEOUT_S", "120"))
# JSON ที่ส่งต่อให้ API ตรง ๆ เช่น {"provider": {"sort": "throughput"}, "frequency_penalty": 0.3}
import json as _json
LLM_EXTRA = _json.loads(env("LLM_EXTRA_JSON", "{}") or "{}")

# ---------- Harness ----------
MAX_STEPS = int(env("MAX_STEPS", "8"))
TOOL_CHOICE = env("TOOL_CHOICE", "required")             # required = บังคับเรียก tool ทุกรอบ | auto
BUDGET_WARN_AT = int(env("BUDGET_WARN_AT", "2"))         # เหลือกี่รอบแล้วเตือนให้ส่งคำตอบ                   # จำนวนรอบ tool call สูงสุดต่อคำถาม
MAX_POLICY_RETRIES = int(env("MAX_POLICY_RETRIES", "2")) # ให้โมเดลแก้คำตอบเมื่ออ้างมาตราที่ตรวจไม่ผ่าน
TOOL_RESULT_MAX_CHARS = int(env("TOOL_RESULT_MAX_CHARS", "6000"))
RELATED_MAX = int(env("RELATED_MAX", "6"))               # จำนวนมาตราที่ "อ้างถึงมาตรานี้" ที่แนบไปกับผล tool

# ---------- Retrieval / External store ----------
BACKEND = env("BACKEND", "opensearch")                  # opensearch | local
OPENSEARCH_HOST = env("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(env("OPENSEARCH_PORT", "9200"))
INDEX_NAME = env("INDEX_NAME", "lawguide-lpa")
TOP_K = int(env("TOP_K", "5"))
_uv = env("USE_VECTOR", "auto")                        # 1 | 0 | auto (ใช้ถ้าติดตั้ง sentence-transformers ไว้)
if _uv == "auto":
    import importlib.util
    USE_VECTOR = importlib.util.find_spec("sentence_transformers") is not None
else:
    USE_VECTOR = _uv == "1"
EMBED_MODEL = env("EMBED_MODEL", "BAAI/bge-m3")
RRF_K = int(env("RRF_K", "60"))

# ---------- กฎหมายที่ใช้ (MVP = ด้านเดียว) ----------
LAW_ID = env("LAW_ID", "lpa2541")
LAW_NAME = env("LAW_NAME", "พระราชบัญญัติคุ้มครองแรงงาน พ.ศ. 2541")
# ตัวบทฉบับรวมแก้ไขถึงฉบับที่ 9 (จัดทำจากตัวบทกฤษฎีกา) — แหล่งทางการคือ krisdika.go.th + ราชกิจจานุเบกษา
SOURCE_URL = env("SOURCE_URL", "https://www.drthawip.com/book/export/html/3172")
OFFICIAL_URL = env("OFFICIAL_URL", "https://www.krisdika.go.th/librarian/get?sysid=642571&ext=htm")
SOURCE_FILE = env("SOURCE_FILE", str(RAW_DIR / "lpa2541.txt"))
if not Path(SOURCE_FILE).is_absolute():
    SOURCE_FILE = str(ROOT / SOURCE_FILE)
SOURCE_RETRIEVED = env("SOURCE_RETRIEVED", "")          # วันที่ดาวน์โหลดตัวบท (YYYY-MM-DD) — ต้องกรอกตอน ingest

# ---------- PII ----------
PII_LLM_EXTRACT = env("PII_LLM_EXTRACT", "0") == "1"   # เปิดให้ LLM ช่วยหาชื่อคน/ข้อมูลอ่อนไหวเพิ่มจาก rule
PII_RULES_FILE = env("PII_RULES_FILE", str(DATA_DIR / "pii_decision_rules.json"))
