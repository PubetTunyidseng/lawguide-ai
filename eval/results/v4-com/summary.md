model `openai/gpt-4.1-mini` · temperature 0.2 · repeats 5 · backend opensearch (opensearch-hybrid(bm25+vector)) · judge `google/gemini-2.5-flash`

| id | expected | behavior ok | same all runs | issue ok | recall | precision | key facts | question cov. | judge supported | cite Jaccard | halluc runs | policy rejected | clamp/dup | cited sets |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| q01 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 0.91 | 1.0 | 0 |  | 0/0 | [['41', '59'], ['41', '59'], ['41', '59'], ['41', '59'], ['41', '59']] |
| q02 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['41/1', '59/2'], ['41/1', '59/2'], ['41/1', '59/2'], ['41/1', '59/2'], ['41/1', '59/2']] |
| q03 | ask_more | 5/5 | True | 5/5 | None | None | None | 0.7 | None | 1.0 | 0 |  | 0/0 | [[], [], [], [], []] |
| q04 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 0.8 | 0 |  | 0/0 | [['118'], ['118', '121'], ['118'], ['118'], ['118']] |
| q05 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['61'], ['61'], ['61'], ['61'], ['61']] |
| q06 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['32', '57', '76'], ['32', '57', '76'], ['32', '57', '76'], ['32', '57', '76'], ['32', '57', '76']] |
| q07 | answer_or_ask | 5/5 | True | 0/5 | 1.0 | 0.5 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['76', '77'], ['76', '77'], ['76', '77'], ['76', '77'], ['76', '77']] |
| q08 | out_of_scope | 0/5 | True | 5/5 | None | None | None | None | None | 1.0 | 0 |  | 0/0 | [[], [], [], [], []] |
| q09 | answer | 5/5 | True | 5/5 | 0.8 | 0.95 | 1.0 | None | 1.0 | 0.717 | 0 |  | 0/0 | [['123', '124'], ['123', '124', '70'], ['123', '124', '70', '76'], ['123', '124'], ['123', '124', '70']] |
| q10 | answer_or_ask | 4/5 | False | 5/5 | None | None | None | None | 1.0 | 1.0 | 0 | {'HAS_CITATION': 2} | 0/0 | [[], [], [], [], []] |
| q11 | answer | 0/5 | True | 5/5 | None | None | None | None | None | 1.0 | 0 |  | 0/0 | [[], [], [], [], []] |
| q12 | answer_or_ask | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['39/1'], ['39/1'], ['39/1'], ['39/1'], ['39/1']] |
| q13 | answer | 5/5 | True | 0/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['70'], ['70'], ['70'], ['70'], ['70']] |
| q14 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['30'], ['30'], ['30'], ['30'], ['30']] |
| q15 | answer_or_ask | 5/5 | False | 5/5 | 0.0 | 0.667 | 1.0 | 1.0 | 1.0 | 0.6 | 0 |  | 0/0 | [['118', '121', '17/1'], [], [], [], []] |

**รวมทั้งชุด**

- runs: 75
- behavior_ok: 64/75
- issue_type_ok: 65/75
- mean_recall: 0.961
- mean_precision: 0.94
- mean_key_facts: 1.0
- mean_question_coverage: 0.833
- runs_with_hallucination_attempt: 0
- answers_with_unsupported_sentence: 2
- judged_answers: 52
- policy_rejected_rules: {'HAS_CITATION': 2}
- top_k_clamped: 0
- duplicate_queries: 0
- mean_latency_s: 5.967