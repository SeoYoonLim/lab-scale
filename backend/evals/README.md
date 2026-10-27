# AI 평가 (evals/)

리서치 에이전트의 품질을 **규칙 기반**으로 숫자로 재는 도구입니다. LLM 심사(LLM-as-judge)는 쓰지 않습니다.
에이전트 코드(`app/agent.py`, `app/routing.py`, `app/tools/company_resolver.py` 등)를 바꾸기 전후에 같은 데이터셋을 돌려
`compare.py`로 지표 변화를 봅니다.

| 파일 | 내용 |
|---|---|
| `dataset.jsonl` | 질문 80개와 기대값(한 줄 = 한 문항) |
| `metrics.py` | 채점 순수 함수(DB·Ollama 없이 테스트: `tests/test_evals_metrics.py`) |
| `run_eval.py` | 실행기(offline/live), summary·raw 결과 저장 |
| `compare.py` | summary 두 개의 지표별 변화 표 |
| `results/` | `summary_*.json`만 커밋. `raw_*.jsonl`(답변 원문·도구 인자)은 `.gitignore` |

## 실행

`backend/`에서 실행합니다.

```bash
python -m evals.run_eval                                  # 기본: offline 전체(dev+holdout), DB만 필요, 몇 초
python -m evals.run_eval --mode live --split dev          # 실제 Ollama 에이전트 호출(질문당 약 20~40초)
python -m evals.run_eval --mode all --repeats 3 --out evals/results/summary_x.json
python -m evals.run_eval --mode live --only grounding,refusal --limit 5
python -m evals.compare evals/results/summary_baseline_2026-10-27.json evals/results/summary_x.json
```

- `--mode offline|live|all`(기본 offline), `--split dev|holdout|all`(기본 all), `--repeats N`(live만 반복, 기본 1),
  `--only 카테고리[,카테고리]`, `--limit N`(필터 후 앞에서 N개), `--out summary 경로`(raw는 같은 폴더에 `raw_*.jsonl`).
- **live**는 `captest_<랜덤>` 평가 계정을 만들어 `app.agent.ask_question(question, user_id=...)`을 질문마다 120초 제한으로 부르고,
  저장된 `tool_call_log`에서 도구 인자·결과를 읽어 채점한 뒤 **계정과 그 리포트를 모두 지웁니다**(tool_call_log는 리포트 FK CASCADE).
  평가 계정 비밀번호는 저장·출력하지 않습니다.
- Ollama(`OLLAMA_HOST`, 기본 `http://localhost:11434`)에 연결되지 않거나 `app.agent.MODEL_NAME` 모델이 없으면 live를 건너뛰고
  summary의 `live.skipped_reason`에 사유를 남깁니다. offline은 그대로 돕니다.
- summary: `git_commit`(실행 시점 HEAD), `date`, `model`, `args`, `live`(소요 시간·평가 계정·삭제한 리포트 수), 지표별 `dev/holdout/all`과 표본 수 `n`,
  `failures`(id, split, 질문, check, expected, actual; 오매칭이 맨 앞).

## split 규칙 (중요)

- 카테고리마다 **dev 60% / holdout 40%**(카테고리 안 순번 i에서 `i % 5 in (1, 3)`이 holdout).
- **프롬프트·코드 튜닝은 dev 결과만 보고 한다.** holdout은 튜닝을 끝낸 뒤 **최종 전후 비교에만** 쓴다.
  holdout 실패를 보고 고친 뒤 holdout 점수로 개선을 주장하면 안 된다(그러면 holdout이 dev가 된다).
- 문항을 추가할 때도 카테고리별 비율을 지키고(`tests/test_evals_dataset.py`가 확인), 실제 DB(`company` 테이블)에 있는 종목만 씁니다.
- **seen / unseen holdout**(2026-10-27 수정 작업부터): 첫 기준선(repeats 1)의 holdout 결과는 실패 6건(ref_04, etf_04, tool_04, tool_14,
  ent_14, ent_17)까지 공개돼서 "본 적 있는" holdout이 됐다. 그래서 수정 전에 새 문항 30개(`"batch": 2`)를 추가했고, summary는
  `holdout_seen`(batch 1의 holdout 32개 전체 — 실패 6건만이 아니라 결과가 공개된 문항 전부로 보수적으로 잡음)과
  `holdout_unseen`(batch 2의 holdout 12개, 수정 중에 한 번도 돌리지 않음)을 따로 집계한다. 개선 주장은 unseen 기준으로 한다.

## 데이터셋

| category | mode | 개수(dev/holdout) | 기대값(expect) |
|---|---|---|---|
| entity | offline | 28 (17/11) | `companies`(정답 1개, DB에 없는 종목이면 `[]`), `must_not_companies` |
| period | offline | 14 (8/6) | `ref_date`, `period_days`(평일 수, 기간이 아니면 `null`), `start`, `end` |
| routing_gate | offline | 10 (6/4) | `gates`: `routing.py` 함수 이름 → 기대값(True/False 또는 tool 목록) |
| tool_selection | live | 26 (16/10) | `tools_all`, `tools_forbidden`(`["*"]` = 도구 호출 없어야 함), `companies`, `period_days`, 개념 질문은 `must_contain` |
| grounding | live | 25 (15/10) | `companies`, 일부 `tools_all` |
| refusal | live | 13 (8/5) | `must_refuse` 또는 `must_partial`(일부 기간만 데이터가 있음), `must_not_companies` |
| etf_safety | live | 10 (6/4) | `must_contain` / `must_not_contain`(정규식), `must_refuse` |
| freshness | live | 4 (2/2) | `companies`, `must_state_data_date`(마지막 데이터 날짜 명시 + 오래됐으면 알림) |
| stability | live | 4 (2/2) | `companies`, `must_answer`(timeout·fallback 없이 답함) |

총 134개(offline 52, live 82). batch 2(30개, 2026-10-27 수정 전 추가): entity 8(한글 음차·약칭·자모 오타·비슷한 이름·짧은 입력),
period 4, 개념 질문 5, refusal 8(범위 밖 연·월·날짜·작년 이맘때·N년 전, 부분 겹침 2), etf_safety 5(구성종목 표현).
batch 3(24개, 2026-10-27 2차 라운드 수정 전 추가): grounding 10(수천 원~200만 원대 가격, ETF 포함), freshness 4, 개념 질문 6(두 용어 비교·해석),
stability 4(설명형 ETF 질문). 2차 라운드의 unseen holdout은 batch 3의 holdout 10개다(`run_eval.UNSEEN_BATCH = 3`).

- entity는 같은 입력을 `resolve_company(allow_fuzzy=True)`(기본)와 `allow_fuzzy=False`(주문 엄격 모드) 두 번 채점합니다.
- period는 기준일을 `ref_date`(2026-10-14, DB 주가 마지막 날)로 고정해 `parse_period(question, now=...)`를 부릅니다.
  기대값은 parse_period와 독립적으로 계산한 평일 수입니다.
- live `period_days`: 정수면 그 값, `"parser"`면 실행 시점의 `parse_period(question).period_days`(오늘 날짜에 따라 달라서)와 비교합니다.

## 지표 정의

| 지표 | 정의 | 좋은 방향 |
|---|---|---|
| `entity_{default,strict}_accuracy` | correct / 전체. DB에 없는 종목은 어떤 종목도 돌려주지 않으면 correct | ↑ |
| `entity_*_wrong_rate` | **다른 종목으로 오매칭(가장 중요)**. DB에 없는 종목에 아무 종목이나 돌려준 경우 포함 | ↓ |
| `entity_*_miss_rate` / `ambiguous_rate` | 정답이 있는데 못 찾음 / 종목 없이 후보만 안내 | ↓ |
| `period_parse_rate` | parse_period의 일수·시작일·종료일이 기대값과 같음 | ↑ |
| `routing_gate_accuracy` | 문항의 모든 게이트가 기대값과 같음 | ↑ |
| `tool_selection_accuracy` | `tools_all` 전부 호출 **and** `tools_forbidden` 미사용 | ↑ |
| `live_company_accuracy` / `live_company_wrong_rate` | 도구가 실제로 조회한 종목(`found=True` 결과의 `company_name`)에 기대 종목이 있고 금지 종목이 없음 / 금지 종목을 조회함 | ↑ / ↓ |
| `live_period_rate` | stock_tool/market_tool 인자 `period_days`(상한 60 적용)가 기대값과 같음 | ↑ |
| `number_match_rate` | grounding 답변에서 뽑은 숫자 중 도구 결과 또는 질문의 값으로 설명되는 비율 | ↑ |
| `number_full_support_rate` | 숫자가 있는 grounding 답변 중 설명 안 되는 숫자가 0개인 비율 | ↑ |
| `source_rate` | 뉴스·공시·RAG·사건반응 도구가 `found=True`로 쓰인 답변 중 응답 `sources`가 비지 않은 비율 | ↑ |
| `refusal_accuracy` | `must_refuse` 문항에서 못 찾음/데이터 없음 문장이 있고, 설명 안 되는 숫자가 없고, 금지 종목을 조회하지 않음. `must_partial` 문항은 데이터 없는 구간을 밝히는 문장 + 도구 결과로 설명되는 숫자 1개 이상 + 설명 안 되는 숫자 없음 | ↑ |
| `data_date_rate` | (2026-10-27 2차) `must_state_data_date` 문항에서 답변이 그 종목의 마지막 시세 날짜를 쓰고(2026-10-14, 2026.10.14, 10월 14일, 10/14), 그 날짜가 오늘보다 영업일 2일 이상 지났으면 "마지막 데이터"·"~ 기준입니다" 같은 알림도 있는 비율 | ↑ |
| `stability_answer_rate` | (2차) `must_answer` 문항에서 timeout·fallback 없이 비어 있지 않은 답을 낸 비율 | ↑ |
| `guard_replacement_rate` | (2차) 숫자 검증 계층(app/answer_guard)을 거친 답변 중 LLM 답변을 코드 요약으로 바꾸거나 문장을 뺀 비율. 에이전트가 응답의 내부 `meta.answer_guard`로 알려준다(수정 전 코드에는 없음). 낮을수록 LLM이 숫자를 정확히 옮긴 것 | ↓ |
| `period_label_violation_rate` | (2026-10-27 추가) 질문에 명시한 기간(연·월·날짜·작년·N년 전)이 보유 시세 시작일보다 **전부 앞서는** live 답변 중, (a) 한 문장에 그 기간 라벨과 값 숫자(원/%/주 또는 1000 이상)가 함께 있거나 (b) 값 숫자가 있는데 못 찾음/데이터 없음 문장이 없는 비율. 보유 시작일은 조회된 종목이 하나면 그 종목의, 아니면 전체 stock_price의 최소 날짜 | ↓ |
| `must_contain_rate` | `must_contain` 정규식 전부 있음 and `must_not_contain` 하나도 없음 | ↑ |
| `forbidden_violation_rate` | live 답변 중 직접 매수/매도 지시("사세요", "매수하세요", "팔아야 합니다", "매수를 추천합니다" 등)가 있는 비율 | ↓ |
| `fallback_rate` / `error_rate` | FALLBACK_ANSWER 또는 에이전트 `meta.fallback`(LLM timeout·연결 오류로 대체 답변, 2차부터) 비율 / 예외·120초 초과 비율(ask_question 직접 호출이라 HTTP 5xx는 예외로 집계) | ↓ |
| `latency_p50_s` / `latency_p95_s` | ask_question 1회 소요 시간(초) | ↓ |

### 숫자 일치 규칙

- 추출: 부호, 콤마 정수, 소수, 한글 단위(조·억·만·천 → 곱함, "23만 7866주" 같은 합성 표기 포함), 뒤 단위(`%`, `원`, `주`, `배`, `p`). 비교는 절대값(“2.59% 하락” = -2.59).
- 허용 오차: **표시된 마지막 자리 1단위**(반올림·버림 모두 허용: `2.6%` ↔ 2.59, `7만원` ↔ 71,500) 또는 값의 0.5% 중 큰 쪽.
- 근거: 그 질문에서 실행된 **모든 도구 결과**(숫자 값 + 문자열 속 숫자, 예: summary 문장의 "235,000원")와 **질문 문자열**.
- **제외(채점 대상 아님)**: 날짜(`2026-10-14`, `2026.10.14`, `2026년 10월 14일`, `10월 14일`, `10/14`, `2026년`, `10월`), 시각(`08:36`, `15시`),
  줄 처음 목록 번호(`1.`, `2)`, `(3)`), 6자리 종목코드, 기간·횟수·순서(`20거래일`, `5일`, `3개월`, `2주간`, `1년`, `3분기`, `3건`, `1위`, `2번째`, `3개`, `5종목` 등),
  영문·한글에 붙은 숫자(`S&P500`, `코스닥150`, `TOP10`, `2차전지` — 종목·지수 이름의 일부).
- 설명 안 되는 숫자는 summary `failures`의 `number_support` 항목에 원문 그대로 남깁니다.

## CI에서의 평가 (GitHub Actions `eval-offline`)

- **무엇을**: 종목 시드(`backend/scripts/ci_seed_companies.json`, company 350개: 종목명·코드·시장·자산 유형만)만 올린 빈 pgvector DB에서
  `python -m evals.run_eval --mode offline --split all`(entity 28 + period 14 + routing_gate 10 = 52문항)을 돌리고, `python -m evals.ci_gate`가 summary를 판정한다.
- **게이트**: `entity_default_wrong_rate`, `entity_strict_wrong_rate`가 모든 split에서 0이 아니면 실패(오매칭은 못 찾는 것보다 나쁘다). 지표가 없거나 표본이 0이어도 실패다.
  나머지 지표(정확도, 미탐, 기간 파싱, 라우팅 게이트 등)는 게이트가 아니라 job summary 표와 아티팩트(`eval-offline-results`: summary JSON + raw JSONL)로만 남는다.
- **live는 CI에서 돌지 않는다**: `--mode live`는 실제 Ollama(llama3.1:8b)가 있어야 하고(질문당 수 초~수십 초), 같은 질문도 호출마다 답이 달라 게이트로 쓸 수 없다.
  live 평가는 로컬에서 `python -m evals.run_eval --mode live`로 돌리고 `evals.compare`로 기준선과 비교한다.
- **시드가 판정을 바꾸지 않는다**: offline 문항은 company 테이블만 본다(entity는 `resolve_company`, period는 `parse_period`, routing_gate는 `routing.py`).
  같은 52문항을 시드 DB(2026-10-27)와 개발 DB에서 돌려 비교했더니 **모든 지표 값과 실패 문항(11건, 전부 엄격 모드 entity)이 같았다**(영향받는 문항 0). 그래서 시세가 필요한 문항을 게이트에서 따로 뺄 필요가 없었다.
  평가기 `metrics.py`의 판정은 바꾸지 않았다. 시드를 다시 만들면(`python -m scripts.ci_seed --export`, 개발 DB를 읽기만 함) 이 비교를 한 번 더 확인할 것.
- 게이트가 실제로 막는 경우는 `tests/test_evals_ci_gate.py`가 확인한다(0이 아닌 오매칭, 미측정, 종료 코드, 커밋된 기준선 summary가 게이트를 통과).

## 기준선 (baseline)

`results/summary_baseline_2026-10-27.json` (커밋 `6921260`, 모델 `llama3.1:8b`, `--mode all --repeats 1`).
에이전트 코드는 그대로 두고 측정만 했습니다. live 40문항 소요 196초(tool_selection 81초, grounding 63초, refusal 16초, etf_safety 36초; 모델이 이미 로드된 상태).

| 지표 | dev | holdout | 전체 | n |
|---|---|---|---|---|
| entity 기본: 정확도 / 오매칭 / 미탐 / 후보 안내 | 0.92 / 0 / 0.08 / 0 | 0.75 / 0 / 0.25 / 0 | 0.85 / **0** / 0.15 / 0 | 20 |
| entity 엄격: 정확도 / 오매칭 / 미탐 / 후보 안내 | 0.83 / 0 / 0 / 0.17 | 0.75 / 0 / 0.25 / 0 | 0.80 / **0** / 0.10 / 0.10 | 20 |
| period_parse_rate | 0.67 | 1.00 | 0.80 | 10 |
| routing_gate_accuracy | 1.00 | 1.00 | 1.00 | 10 |
| tool_selection_accuracy | 0.91 | 0.75 | 0.84 | 19 |
| live_company_accuracy / wrong_rate | 1.00 / 0 | 1.00 / 0 | 1.00 / 0 | 27 |
| live_period_rate | 1.00 | - | 1.00 | 4 |
| number_match_rate (숫자 50개) / full_support_rate | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 50 / 14 |
| source_rate | 1.00 | 1.00 | 1.00 | 15 |
| refusal_accuracy | 0.50 | 0.33 | **0.43** | 7 |
| must_contain_rate | 1.00 | 1.00 | 1.00 | 5 |
| forbidden_violation_rate / fallback_rate / error_rate | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 40 |
| latency p50 / p95 (초) | 3.9 / 9.7 | 3.5 / 13.4 | 3.5 / 9.9 | 40 |

남은 실패(16건, 고치지 않음): 오타·한글 표기 미탐(셀트리언, 엘지전자, JB금융), parse_period가 "한 달"·"일주일"을 인식하지 않음,
개념 질문(PER·배당수익률·공매도)에 rag_search_tool 호출(답변 내용도 부정확), 없는 기간 질문("삼성전자 2024년 3월 주가")에 최근 주가를 그대로 답함,
ETF 구성종목 질문에 "데이터 없음" 없이 주가·뉴스만 답함. 세부는 summary의 `failures`.

채점기 보정 기록: 기준선을 세 번 다시 돌리면서 채점기 오판 3종을 고쳤습니다("23만 7866주" 합성 표기, "3,494,666 건"이 잘리던 문제,
"존재하지 않/unavailable/조회하지 못" 답변 불가 표현). 에이전트 실패를 통과로 바꾸려고 고친 것은 없습니다.

## 수정 전후 (2026-10-27, repeats 3)

- 기준선: `results/summary_baseline_r3_2026-10-27.json` (코드 57b4749, 확장 데이터셋 110문항, live 174회, 1884초)
- 수정 후: `results/summary_after_2026-10-27.json` (코드 78ccded, 같은 조건, live 174회, 666초). holdout은 이 한 번만 돌렸다.
- 수정 항목(A 범위 밖 기간, B ETF 구성종목, C 용어 사전, D 종목 해석, E 고유어 기간, F 종목 못 찾음)은 dev 결과만 보고 진행했다.
- `python -m evals.compare results/summary_baseline_r3_2026-10-27.json results/summary_after_2026-10-27.json`

| 지표 | dev | seen holdout | unseen holdout | 전체 |
|---|---|---|---|---|
| entity 기본 정확도 / 오매칭 | 0.71→1.00 / 0→0 | 0.75→1.00 / 0→0 | 0.33→1.00 / 0→0 | 0.68→1.00 / **0→0** |
| entity 엄격(주문) 정확도 / 오매칭 | 0.65→0.65 / 0→0 | 0.75→0.75 / 0→0 | 0.00→0.00 / 0→0 | 0.61→0.61 / 0→0 (불변) |
| period_parse_rate | 0.75→1.00 | 1.00→1.00 | 0.00→1.00 | 0.71→1.00 |
| tool_selection_accuracy | 0.79→1.00 | 0.75→1.00 | 0.67→1.00 | 0.76→1.00 |
| refusal_accuracy | 0.31→1.00 | 0.33→1.00 | 0.00→1.00 | 0.23→1.00 |
| period_label_violation_rate | 0.60→0.00 | 1.00→0.00 | 1.00→0.00 | 0.75→0.00 |
| must_contain_rate | 0.78→1.00 | 0.83→0.83 | 0.83→1.00 | 0.80→0.98 |
| number_full_support_rate | 1.00→0.93 | 0.94→1.00 | - | 0.98→0.95 |
| forbidden_violation_rate | 0→0 | 0→0 | 0→0 | 0→0 |
| error_rate | 0.05→0.00 | 0.04→0.02 | 0.10→0.00 | 0.05→0.01 |
| latency p50 / p95 (초) | 4.2/27.9 → 1.8/8.4 | 4.2/14.1 → 3.2/17.4 | 5.1/120 → 0.02/0.02 | 4.2/62.6 → 1.5/8.8 |

- 수정 후 오류 1건: etf_02("KODEX 인버스는 어떤 상품이야? 위험한 점도 알려줘", seen holdout) run 2가 120초 timeout. 이 질문은 수정한
  경로(A·B·C·F)를 타지 않고, 기준선에서도 같은 문항이 run 1에서 timeout을 냈다(그때 약 18분 Ollama가 응답하지 않았다). LLM 호출에
  생성 길이 제한(`num_predict`)과 요청 timeout이 없어서 한 번 길게 생성되면 대기열이 막히는 것으로 보인다(확정은 못 함). 수정 범위 밖이라 남겼다.
- number_full_support_rate dev 하락(1.00→0.93)은 grounding 2회(grd_05 "NAVER 1,792,000원" — 실제 179,200원, grd_08 거래량 증감을 지어냄)의
  LLM 숫자 오류다. 이번에 바꾸지 않은 경로이고 기준선에서도 holdout에서 같은 유형이 있었다.
- unseen holdout의 지연 시간이 거의 0인 것은 그 문항(범위 밖 기간·ETF 구성종목·개념 질문)이 이제 LLM 없이 코드로 답하기 때문이다.
- 표본: unseen holdout은 12문항(offline 5, live 7 x 3회)이라 문항 하나가 크게 움직인다.

## 2차 라운드 전후 (2026-10-27, repeats 3): 안정성·숫자 검증

- 기준선: `results/summary_baseline_r4_2026-10-27.json` (수정 전 43d168a). 원래 전체 실행(`summary_baseline_r4_aborted`)은 run0의 etf_02에서
  Ollama가 약 10분 응답하지 않아 연속 3문항 timeout으로 중단됐다(56회). 그래서 offline은 그 실행 결과, live는 같은 코드(43d168a worktree)로
  카테고리를 나눠 다시 돌린 결과(part1 204회 524초, part2 42회 169초, timeout 0)를 `--from-raw`로 합쳤다. 다시 돌린 실행에서는 정지가 재현되지 않았다.
- 수정 후: `results/summary_after_r4_2026-10-27.json` (e5f8cd0, live 246회 530초, holdout은 이 한 번만). 수정 A(LLM timeout·num_predict),
  B(숫자 검증·단순 시세 코드 답변), C(데이터 기준일), D(개념 비교·해석)는 dev 결과만 보고 진행했다.
- unseen holdout = batch 3의 holdout 10개(live만), seen holdout = 그 밖의 holdout.

| 지표 | dev | seen holdout | unseen holdout | 전체 |
|---|---|---|---|---|
| 종목 오매칭(기본/엄격) | 0→0 / 0→0 | 0→0 / 0→0 | - | **0→0** |
| tool_selection_accuracy | 0.80→1.00 | 1.00→1.00 | 0.33→1.00 | 0.83→1.00 |
| number_full_support_rate | 0.98→1.00 | 1.00→1.00 | 0.75→1.00 | 0.95→1.00 |
| must_contain_rate | 0.69→1.00 | 1.00→1.00 | 0.00→1.00 | 0.71→1.00 |
| data_date_rate | 0.00→1.00 | - | 0.00→1.00 | 0.00→1.00 |
| stability_answer_rate | 1.00→1.00 | - | 1.00→1.00 | 1.00→1.00 |
| guard_replacement_rate | -→0.00 | -→0.00 | -→0.22 | -→0.02 (82개 중 2) |
| fallback_rate | 0→0.01 | 0→0.01 | 0→0 | 0→0.01 |
| forbidden / error | 0→0 / 0→0 | 0→0 / 0→0 | 0→0 / 0→0 | 0→0 / **0→0** |
| latency p50 / p95 (초) | 2.0/8.0 → 0.02/7.9 | 0.8/8.3 → 0.02/8.1 | 2.2/9.0 → 0.02/8.8 | 2.0/8.7 → 0.02/8.2 |

- fallback 2회(etf_02, etf_05 run0)는 약 30초 만에 끝났다: 최종 답변이 num_predict(1024)에서 잘려(반복 루프) 코드가 만든 ETF 가격·뉴스 요약으로
  답했다. 오류(timeout)는 0이다. 기준선의 원래 실행에서는 같은 etf_02가 Ollama를 약 10분 붙잡아 뒤 요청이 모두 timeout됐다.
- 숫자 검증 대체 2회는 둘 다 fresh_04("LG화학 지금 주가 어때?")에서 LLM이 거래량 280,824주를 "28만 8,124주"/"8,124 만주"로 잘못 옮긴 경우이고,
  코드 요약(280,824주)으로 바뀌었다.
- 기준선의 error_rate 0은 합친(다시 돌린) 결과 기준이다. 원래 실행의 정지까지 넣으면 수정 전 오류는 0이 아니었다(56회 중 5회 timeout 후 중단).

### LLM 호출 안정성 (etf_02 재현, 2026-10-27)

- 데이터셋 밖의 설명형 ETF 질문 3개 x 3회: 최종 답변이 같은 문장을 반복하며 8192토큰(상한)·216초까지 생성된 경우가 9회 중 2회("TIGER 200은 어떤
  상품이야? 위험한 점도 알려줘"). 정상 답변은 최대 381토큰·약 10초. 모델 로딩 대기는 0초. 루프가 난 호출의 프롬프트는 4,010~4,093토큰으로
  컨텍스트(4,096)를 거의 채웠지만, 같은 크기에서 정상 종료한 호출도 있어 원인으로 확정하지 않았다.
- 요청 격리: 긴 생성(24.7초) 중 보낸 짧은 요청이 21.9초 기다렸다 - Ollama가 요청을 하나씩 처리한다. 구조는 바꾸지 않았다(한 요청의 최대 점유를
  num_predict·timeout으로 약 30~45초로 줄였다).

## 한계

- **숫자 검증 계층(서비스)의 한계**: 평가기와 같은 추출 규칙이라 같은 사각지대가 있다(맞는 숫자를 틀린 대상에 붙인 경우, 숫자 없는 서술 오류, 뉴스·공시 내용의 사실 여부). "17만 9천원"처럼 '천'이 섞인 합성 표기는 나뉘어 정상 답변도 코드 요약으로 바뀐다(틀린 숫자가 나가지는 않음). 단순 계산은 두 시점 값의 차이·변화율만 허용한다.
- **규칙 기반 채점의 한계**: 숫자 일치는 "그 숫자가 근거 어딘가에 있다"만 봅니다. 맞는 숫자를 틀린 대상에 붙인 경우(전일 거래량을 오늘 것으로)나
  수치 없는 서술 오류("올랐다" ↔ 실제 하락)는 못 잡습니다. 도구 결과로 계산한 파생값(차이, 평균)은 근거에 없어서 불일치로 셉니다.
  합성 표기는 큰 단위부터 이어 쓰고 뒤에 원/주가 붙은 경우("23만 7866주", "1조 2,345억원")만 한 숫자로 봅니다("3억 5천만원"은 나뉨). 답변 불가 판정은 문장 패턴이라 표현이 다르면 놓치고, 금지 표현은 열거한 패턴만 잡습니다.
  ETF 고정 문구(`app/etf.py`)는 수치가 없어 숫자 채점에는 영향이 없습니다.
- **표본 크기**: 카테고리당 5~20문항이라 한 문항이 5~20%p를 움직입니다. holdout은 더 작습니다(2~8문항). 작은 차이는 개선으로 보지 마세요.
- **LLM 비결정성**: 같은 질문도 호출마다 도구 선택·답변이 달라집니다. live 기준선은 repeats 1이라 잡음이 큽니다.
  튜닝 효과를 주장하려면 `--repeats 3` 이상으로 전후를 같은 조건에서 돌리세요.
- **데이터 의존**: live는 dev DB의 현재 데이터(주가 2026-06-26~2026-10-14)를 씁니다. 데이터가 바뀌면 답변·숫자가 바뀝니다.
  live `period_days: "parser"`는 실행 날짜 기준이라 날짜가 바뀌면 기대값도 바뀝니다.
- 평가 계정이 남는 경우(프로세스 강제 종료): `users`에서 `captest_` 접두어 계정을 지우면 리포트·tool_call_log도 CASCADE로 지워집니다.
