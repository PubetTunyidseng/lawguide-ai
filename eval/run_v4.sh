#!/usr/bin/env bash
# Eval รอบ v4: harness guard (top_k ขั้นต่ำ, กันค้นซ้ำ) + กฎ NUMBER_GROUNDED + จำแนก issue_type + LLM judge
# และเทียบ open-source กับ commercial ภายใต้ harness/tools/ข้อมูลชุดเดียวกัน (เปลี่ยนแค่โมเดล)
#
#   bash eval/run_v4.sh
#   COM_MODEL=openai/gpt-5-mini REPEATS=3 bash eval/run_v4.sh        เปลี่ยนโมเดล/จำนวนรอบได้
#
# judge ใช้โมเดลที่สาม (ไม่ใช่ทั้งสองตัวที่ถูกประเมิน) และใช้ตัวเดียวกันทั้งสองรอบ ผลจึงเทียบกันได้
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate

OSS_MODEL=${OSS_MODEL:-qwen/qwen3-235b-a22b-2507}
COM_MODEL=${COM_MODEL:-openai/gpt-4.1-mini}
JUDGE=${JUDGE:-google/gemini-2.5-flash}
REPEATS=${REPEATS:-5}

echo "▶ 1/5 offline tests"
python -m pytest -q tests

echo "▶ 2/5 ตรวจว่าโมเดลมีจริงและรองรับพารามิเตอร์ที่ส่งไป"
python eval/check_models.py "$OSS_MODEL" "$COM_MODEL" --judge "$JUDGE"

echo "▶ 3/5 open-source: $OSS_MODEL"
python eval/run_eval.py --repeats "$REPEATS" --model "$OSS_MODEL" --judge --judge-model "$JUDGE" --tag v4-oss

echo "▶ 4/5 commercial: $COM_MODEL"
python eval/run_eval.py --repeats "$REPEATS" --model "$COM_MODEL" --judge --judge-model "$JUDGE" --tag v4-com

echo "▶ 5/5 ตารางเทียบ"
python eval/compare.py qwen3 qwen3-v2 v3-bm25 v3-hybrid v4-oss v4-com --md eval/results/compare.md
echo "บันทึกที่ eval/results/compare.md"
