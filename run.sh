#!/usr/bin/env bash
# ติดตั้งและรัน LawGuide บน Mac/Linux ในคำสั่งเดียว
#   bash run.sh            ติดตั้ง → ingest → test → ถามทดสอบ 1 ข้อ → เปิดหน้าเว็บ
#   bash run.sh --vector   ติดตั้ง sentence-transformers เพื่อใช้ hybrid search (BM25 + bge-m3) ด้วย
#   bash run.sh --no-app   ทำทุกอย่างยกเว้นเปิดหน้าเว็บ
set -euo pipefail
cd "$(dirname "$0")"

WANT_VECTOR=0; OPEN_APP=1
for a in "$@"; do
  case "$a" in --vector) WANT_VECTOR=1 ;; --no-app) OPEN_APP=0 ;; esac
done

say() { printf "\n\033[1m▶ %s\033[0m\n" "$*"; }

# ---------- 1. Python venv ----------
say "1/6 เตรียม Python environment (.venv)"
PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert sys.version_info >= (3, 10), "ต้องใช้ Python 3.10 ขึ้นไป"'
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
if [ "$WANT_VECTOR" = 1 ]; then
  pip install -q -r requirements-vector.txt
fi

# ---------- 2. .env และ API key ----------
say "2/6 ตรวจ .env และ API key"
[ -f .env ] || cp .env.example .env
has_key() { grep -E '^LLM_API_KEY=.{20,}' .env | grep -vq 'xxxx'; }
if ! has_key; then
  OLD=../kdai-rag-assignment/.env
  if [ -f "$OLD" ] && grep -q '^OPENROUTER_API_KEY=' "$OLD"; then
    read -r -p "ไม่พบ LLM_API_KEY ใน .env — ใช้ OPENROUTER_API_KEY จากโปรเจกต์ kdai-rag-assignment ไหม? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
      KEY=$(grep '^OPENROUTER_API_KEY=' "$OLD" | head -1 | cut -d= -f2-)
      grep -v '^LLM_API_KEY=' .env > .env.tmp && mv .env.tmp .env
      printf 'LLM_API_KEY=%s\n' "$KEY" >> .env
      echo "  ใส่ key แล้ว (ไม่แสดงค่า)"
    fi
  fi
fi
has_key || { echo "  ✗ ยังไม่มี LLM_API_KEY — เปิดไฟล์ .env แล้วใส่ key ของ OpenRouter ก่อน"; exit 1; }

# ---------- 3. External store ----------
say "3/6 External store"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker compose up -d
  printf "  รอ OpenSearch"
  for _ in $(seq 1 60); do
    curl -s localhost:9200 >/dev/null 2>&1 && break
    printf "."; sleep 2
  done
  echo
  curl -s localhost:9200 >/dev/null || { echo "  ✗ OpenSearch ไม่ตอบที่ localhost:9200"; exit 1; }
  export BACKEND=opensearch
  echo "  ✓ OpenSearch พร้อม (BACKEND=opensearch)"
else
  export BACKEND=local
  echo "  ⚠️ ไม่พบ Docker ที่เปิดอยู่ → ใช้ BACKEND=local (BM25 ในหน่วยความจำ)"
  echo "     ตอนสอบต้องเปิด Docker Desktop แล้วรันใหม่ เพื่อให้มี External ตามเกณฑ์"
fi

# ---------- 4. Ingest ----------
say "4/6 Ingest ตัวบท"
RETRIEVED=$(grep -m1 '^ดึงข้อมูล:' data/raw/lpa2541.txt | awk '{print $2}')
INGEST_ARGS=(--retrieved "${RETRIEVED:-$(date +%F)}")
[ "$BACKEND" = local ] && INGEST_ARGS+=(--no-index)
python -m lawguide.ingest "${INGEST_ARGS[@]}" > /dev/null
python - <<'PY'
import json
r = json.load(open("data/processed/ingest_report.json", encoding="utf-8"))
print(f"  มาตรา {r['n_sections']} · เชิงอรรถ {r['n_footnotes']} · ดึงตัวบท {r['source_retrieved']}")
for w in r["corpus_version_warnings"] or ["ตัวบทมีร่องรอยฉบับแก้ไขล่าสุดครบ"]:
    print("  -", w)
PY

# ---------- 5. Test ----------
say "5/6 Test แบบไม่เรียก LLM"
python -m pytest -q tests

# ---------- 6. ถามทดสอบ 1 ข้อด้วย LLM จริง ----------
say "6/6 ถามทดสอบด้วย LLM จริง"
python ask.py "ภรรยาผมเพิ่งคลอดลูก ผมเป็นลูกจ้างบริษัทเอกชน ลาไปช่วยภรรยาได้กี่วัน ได้ค่าจ้างไหม"

if [ "$OPEN_APP" = 1 ]; then
  say "เปิดหน้าเว็บที่ http://localhost:7860  (Ctrl+C เพื่อปิด)"
  python app.py
fi
