model `qwen/qwen3-235b-a22b-2507` · temperature 0.2 · repeats 5 · backend opensearch (opensearch-hybrid(bm25+vector)) · judge `google/gemini-2.5-flash`

| id | expected | behavior ok | same all runs | issue ok | recall | precision | key facts | question cov. | judge supported | cite Jaccard | halluc runs | policy rejected | clamp/dup | cited sets |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| q01 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 0.8 | 1.0 | 0 |  | 0/0 | [['41', '59'], ['41', '59'], ['41', '59'], ['41', '59'], ['41', '59']] |
| q02 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['41/1', '59/2'], ['41/1', '59/2'], ['41/1', '59/2'], ['41/1', '59/2'], ['41/1', '59/2']] |
| q03 | ask_more | 5/5 | True | 5/5 | None | None | None | 1.0 | None | 1.0 | 0 |  | 0/0 | [[], [], [], [], []] |
| q04 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 0.8 | 0 |  | 0/0 | [['118'], ['118'], ['118', '119'], ['118'], ['118']] |
| q05 | answer | 5/5 | True | 5/5 | 1.0 | 0.733 | 1.0 | None | 1.0 | 0.517 | 0 |  | 0/0 | [['61', '70'], ['61'], ['61', '70'], ['61'], ['61', '65', '72']] |
| q06 | answer | 5/5 | True | 5/5 | 0.6 | 1.0 | 1.0 | None | 1.0 | 0.567 | 0 |  | 0/0 | [['32', '57'], ['57'], ['57'], ['57', '76'], ['57', '76']] |
| q07 | answer_or_ask | 5/5 | True | 5/5 | 1.0 | 0.5 | 1.0 | None | 1.0 | 1.0 | 0 | {'QUOTE_MATCHES': 5} | 0/0 | [['76', '77'], ['76', '77'], ['76', '77'], ['76', '77'], ['76', '77']] |
| q08 | out_of_scope | 5/5 | True | 5/5 | None | None | None | None | 0.2 | 1.0 | 0 |  | 0/0 | [[], [], [], [], []] |
| q09 | answer | 5/5 | True | 5/5 | 0.9 | 1.0 | 1.0 | None | 1.0 | 0.583 | 0 |  | 0/0 | [['123', '70'], ['123', '125', '70'], ['123', '124', '125', '70'], ['123', '124', '125'], ['123', '70']] |
| q10 | answer_or_ask | 5/5 | False | 3/5 | None | None | None | None | 0.4 | 0.4 | 0 |  | 0/0 | [['122', '135', '60', '62', '87'], [], ['122', '135', '60', '62', '87'], [], []] |
| q11 | answer | 4/5 | False | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 0.4 | 0 | {'QUOTE_MATCHES': 2} | 0/0 | [['118', '119'], ['118'], ['118', '119'], [], ['118']] |
| q12 | answer_or_ask | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['39/1'], ['39/1'], ['39/1'], ['39/1'], ['39/1']] |
| q13 | answer | 5/5 | True | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 1.0 | 0 |  | 0/0 | [['123', '70'], ['123', '70'], ['123', '70'], ['123', '70'], ['123', '70']] |
| q14 | answer | 3/5 | False | 5/5 | 1.0 | 1.0 | 1.0 | None | 1.0 | 0.4 | 0 |  | 0/0 | [[], ['30'], ['30'], ['30'], []] |
| q15 | answer_or_ask | 5/5 | True | 5/5 | None | None | None | 1.0 | None | 1.0 | 0 |  | 0/0 | [[], [], [], [], []] |

**รวมทั้งชุด**

- runs: 75
- behavior_ok: 72/75
- issue_type_ok: 73/75
- mean_recall: 0.952
- mean_precision: 0.926
- mean_key_facts: 1.0
- mean_question_coverage: 1.0
- runs_with_hallucination_attempt: 0
- answers_with_unsupported_sentence: 10
- judged_answers: 62
- policy_rejected_rules: {'QUOTE_MATCHES': 7}
- top_k_clamped: 0
- duplicate_queries: 0
- mean_latency_s: 17.711