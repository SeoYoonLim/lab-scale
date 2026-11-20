# 57차 KRX 휴장일 반영과 평가 기준일 계산 정확도 개선 (2026-11-15)

**평가 코드만 바꿨다.** 제품 코드·API·프런트엔드·개발 DB는 바꾸지 않았다. 평가 세트 v1·v2와 기존 질문·기대값·정답 데이터·임계값도 바꾸지 않았다.
**현재 평가 세트의 결과는 변경 전과 문항 단위까지 같다**(11절). 이번 수정은 휴장일 근처에서 생기는 판정 오류를 막는 것이다. 지금 평가 입력에는 그런 날이 없었다. 수정 효과는 휴장일 사례를 넣은 단위 테스트로 확인했다(7·14절).

## 1. 시작 브랜치와 기준 커밋

| 항목 | 값 |
| --- | --- |
| 브랜치 | `feature/backend`, `origin/feature/backend`와 같은 커밋, 작업 트리 깨끗함 |
| 기준 커밋 | `4d55296`(56차) |
| 확인한 문서·코드 | `docs/KRX_CALENDAR_COMPLETENESS_56.md`, `app/data/krx_calendar.json`, 평가 모듈 `forecast_check`·`run_quality`·`quality_judges`(그리고 같은 계산을 하는 `metrics`·`quality_fixtures`·`rescore_routes`) |
| 평가 실행 명령(기존) | `python -m evals.run_quality --mode deterministic`(품질 244문항), `python -m evals.run_eval --mode offline --split all`(CI `eval-offline`), `python -m evals.run_multilingual <문항> --out …` + `python -m evals.rescore_routes`(v1·v2, live) |
| 실행 중인 백엔드 | PID 28088(15:12 시작). 건드리지 않았다. 이번 변경은 평가 코드라 서버와 관계없다(55·56차 반영을 위한 재시작은 여전히 승인 대기) |

## 2. 기존 휴장일 계산 문제

평가 코드에서 날짜를 계산하는 곳은 6곳이다. 4곳이 KRX 휴장일을 보지 않았다.

| 위치 | 56차까지 | 문제 |
| --- | --- | --- |
| `forecast_check._expected_day24` | **평일만**(주말만 건너뜀) | 휴장일(평일)을 기대 거래일로 잡는다. 휴장일 저녁·휴장일 다음 날 장중에 만든 맞는 답을 "미수집·휴장일 추정"으로 틀리게 채점한다. 예: 10-06(화) 10:00 → 10-05(개천절 대체공휴일). 제품은 10-02 |
| `forecast_check._before_close24` | 평일이고 마감 전이면 True | 휴장일 오전을 "장중"으로 본다. 기준일 조회 조건(`<` / `<=`)이 제품(휴장일은 장중 아님)과 다르다 |
| `run_quality._visible_rows` | 평일이고 마감 전이면 오늘 행 제외 | 휴장일에도 오늘 행을 뺀다(제품은 휴장일에 빼지 않는다) |
| `metrics._business_days_between`(live `data_date_ok`의 "영업일 2일 이상 지연") | **평일 수** | 제품(`app/data_status`)은 36차부터 KRX 거래일 수다. 연휴 앞 금요일 데이터를 연휴 뒤 첫 거래일에 보면 평가기 2일(지연 안내 요구), 제품 1일(안내 없음)이라 맞는 답을 틀리게 채점한다 |
| `quality_judges.expected_trading_day`·`trading_days_behind`·`krx_closed` | KRX 휴장일 반영(36차) | 범위 밖·미확정 평일을 **조용히 거래일로** 본다(요구사항 3.4 위반) |
| `quality_fixtures.trading_days`(합성 입력) | 평일만 | 합성 시세가 휴장일 날짜에도 생긴다. 판정에는 영향이 없다(16절) |

- 세 모듈의 달력 읽기 코드가 서로 달랐다: `quality_judges`는 휴장일 함수와 마감 함수를 따로 두었고, `forecast_check`와 `run_quality`는 마감만 빌려 썼다.
- `forecast_check`의 5·20·60거래일 수익률·이동평균·고점·저점·조회 기간 검산(`expected`·`check_numbers`·`regime_expected`·`check_numbers23`)은 **DB의 실제 시세 행을 N개 세는 방식**이다. 시세 행은 거래일에만 있으므로 휴장일이 들어갈 수 없다. 이 부분은 바꾸지 않았다(문제 없음 확인).

## 3. `krx_calendar.json` 실제 구조

| 키 | 내용 | 평가기에서 쓰는 곳 |
| --- | --- | --- |
| `coverage` | 2023-10-30 ~ 2026-12-31 | 범위 판정 |
| `holidays` | 평일 휴장일 55일(`date`·`name`·`source`) | 휴장일 |
| `unconfirmed` | 2026-12-31(연말 휴장 관례, 공지 전) | 판정 보류 |
| `fixed_annual` | 고정 공휴일 9개(`md`, 범위 밖 해에만) | 범위 밖 휴장일 |
| `regular_open`·`regular_close` | 09:00·15:30 | 시각 |
| `special_sessions` | 2026-11-19 `open` 10:00·`close` 16:30 | 특수 거래일 시각(거래일로 센다) |
| `version` 등 | `krx-2026-11-15.2` | 쓰지 않음 |

구분:

| 날짜 | 판정 |
| --- | --- |
| 일반 거래일 | `trading` |
| 토·일 | `weekend` |
| 달력 휴장일 | `holiday` |
| 특수 거래시간 날(11-19) | `trading`, 시각만 다름 |
| 미확정일(2026-12-31) | `unknown` |
| 범위 밖 평일 | `unknown`. 범위 밖이라도 고정 공휴일이면 `holiday`, 주말이면 `weekend` |

확인되지 않은 휴장일은 추가하지 않았다(JSON 변경 없음).

## 4. 거래일 판정 정책

- 날짜는 KST 날짜, 시각은 KST `"HH:MM"`으로 받는다. 평가기 호출부는 이미 KST로 바꾼 값을 넘긴다(`run_quality._kst`, 답에 적힌 `KST` 생성 시각).
- 거래일 = `trading`만. 주말·휴장일은 거래일이 아니다.
- 특수 거래일(11-19)은 정상 거래일 **하루**다:
  - 거래일 수 계산에서 한 번 센다
  - 시각 기준 계산(마감 전 판정, 기대 거래일)은 그날 16:30을 쓴다
  - 일반 거래일은 15:30이다
- 기대 거래일 = 그 시각에 종가가 있어야 할 가장 최근 거래일. 거래일 마감 전이면 직전 거래일, 주말·휴장일이면 그 앞 거래일. 제품 `krx_calendar.expected_trading_day`와 같은 규칙이고, 구현은 따로 했다.
- "마감 전"(`before_close`) = 그날이 거래일이고 시각이 그날 마감 전. 주말·휴장일은 False다(제품 `session_is_open`과 같다).

## 5. 달력 범위 밖 날짜 처리 정책

| 날짜 | 평가기 | 제품(참고) |
| --- | --- | --- |
| 범위 밖 주말 | 휴장(규칙으로 확정) | 같음 |
| 범위 밖 고정 공휴일(1/1·3/1·5/1·5/5·6/6·8/15·10/3·10/9·12/25) | 휴장(규칙으로 확정) | 같음 |
| 범위 밖 그 밖의 평일, 미확정일 | **`CalendarUnknown` 오류**(평가 중단, 날짜와 사유 표시: "KRX 거래일 여부를 확정할 수 없습니다(달력 범위(…) 밖 평일). app/data/krx_calendar.json을 갱신하세요") | "열릴 수 있는 날"로 세고 미확인 일수를 따로 알린다 |

- 조용히 거래일로 처리하지 않는다. 잘못된 정답으로 채점하는 것보다 평가를 멈추는 편이 낫다.
- 오류는 계산이 그 날짜를 실제로 만날 때만 난다(예: 12-30 기준 기대 거래일은 계산된다. 12-31을 지나가는 계산은 멈춘다).
- 영향: 평가 입력 날짜는 모두 범위 안이다(2023-10-30~2026-11-09, `now` 2026-10-30~11-09). 앞으로 영향이 생기는 경우는 둘이다:
  - 실행 날짜를 쓰는 live 판정(`data_date_ok`)
  - 2026-12-31 이후에 만든 답의 `check_data24`
  - 그 전에 2027 달력과 12-31 공지를 넣어야 한다(16절).

## 6. 공통 평가 유틸리티 구조

새 모듈 **`backend/evals/krx_days.py`**:
- 평가 전용이다. `app.*`을 import하지 않는다(AST 검사 테스트).
- DB·네트워크를 쓰지 않는다. 제품과 같은 `app/data/krx_calendar.json`을 읽고, 형식은 따로 검사한다.

| 함수 | 내용 |
| --- | --- |
| `load(path)`·`calendar()` | 파일 읽기·검사(캐시) |
| `day_status(d)` | `trading`·`weekend`·`holiday`·`unknown` |
| `is_trading_day(d)` | True/False, `unknown`이면 `CalendarUnknown` |
| `open_hhmm(d)`·`close_hhmm(d)` | 그날 개장·마감 |
| `before_close(d, hhmm)` | 거래일이고 마감 전인가 |
| `last_/previous_/next_trading_day(d)` | 직전·다음 거래일 |
| `nth_trading_day(d, n)` | n번째 거래일(n>0 뒤로, n<0 앞으로) |
| `trading_days(a, b)`·`count_trading_days(a, b)` | 기간의 거래일(양 끝 포함) |
| `trading_days_after(a, b)` | a 뒤 ~ b의 거래일 수(지연 일수) |
| `expected_trading_day(d, hhmm)` | 기대 거래일 |

파일 읽을 때 거부하는 것은 모두 `CalendarError`다:
- 파일 없음, JSON 오류, `coverage` 없음, 시작 > 끝
- 날짜 형식(`YYYY-MM-DD`만, `2026/10/09`·`20261009` 거부), 시각 형식(`HH:MM`, 00:00~23:59)
- 휴장일 중복·주말·범위 밖, 특수일 중복, 특수일이 거래일이 아님, 미확정일이 휴장일과 겹침
- 개장 >= 마감, `fixed_annual` 형식

`quality_judges`의 기존 이름(`krx_close_hhmm`·`krx_closed`·`trading_days_behind`·`expected_trading_day`)은 호출부를 바꾸지 않으려고 남겼다. 이제 이 함수들은 `krx_days`로 넘기기만 한다. 이전에 따로 있던 달력 읽기 코드 두 벌은 지웠다.

## 7. `forecast_check.py` 변경 사항

| 함수 | 변경 |
| --- | --- |
| `_expected_day24(gen)` | 평일만 → `krx_days.expected_trading_day`(휴장일 건너뜀, 특수 마감) |
| `_before_close24(gen)` | 평일 → `krx_days.before_close`(휴장일 False) |
| `_close_hhmm(d)` | `krx_days.close_hhmm` |
| 수익률·이동평균·조회 기간 검산 | 변경 없음(DB 시세 행 기준, 2절) |

달라지는 판정(테스트 `test_forecast_expected_day_fixes_holidays`):

| 답 생성 시각 | 56차(평일만) | 57차 | 제품 |
| --- | --- | --- | --- |
| 2026-10-05 20:00(개천절 대체공휴일 저녁) | 10-05 | **10-02** | 10-02 |
| 2026-10-06 10:00(휴장일 다음 날 장중) | 10-05 | **10-02** | 10-02 |
| 2026-10-09 18:00(한글날 저녁) | 10-09 | **10-08** | 10-08 |
| 2026-10-12 10:00(한글날 다음 월요일 장중) | 10-09 | **10-08** | 10-08 |
| 2026-09-28 10:00(추석 연휴 뒤) | 09-25 | **09-23** | 09-23 |
| 2026-02-19 10:00(설 연휴 뒤) | 02-18 | **02-13** | 02-13 |

- 휴장일이 끼지 않은 날은 결과가 같다. 2026-10-13~11-30의 매일 7개 시각에서 56차 함수와 같다(`test_forecast_expected_day_unchanged_away_from_holidays`).
- 휴장일 당일 장중(예: 10-05 12:00)은 56차에도 맞았다. 하루를 빼고 주말을 건너 10-02가 나왔다.

## 8. `run_quality.py` 변경 사항

- `_visible_rows`: "평일이고 마감 전"을 "KRX 거래일이고 마감 전"(`krx_days.before_close`)으로 바꿨다. 휴장일에는 오늘 행을 빼지 않는다(제품 `drop_open_session`과 같다).
- 평가 기간·기준일·대상 거래일 계산은 원래 `J.expected_trading_day`·`J.trading_days_behind`(→ `krx_days`)를 쓰고 있었다. 호출부는 바꾸지 않았다.
- 평가 기간의 행은 합성 입력(`quality_fixtures`) 그대로다(16절).

## 9. `quality_judges.py` 변경 사항

- 달력 읽기(`_krx_holidays`·`_krx_closes`)를 지우고 `krx_days`로 넘겼다.
- **정책 변경**: `krx_closed`·`expected_trading_day`·`trading_days_behind`가 범위 밖·미확정 평일을 만나면 `CalendarUnknown`을 낸다(56차까지는 열린 날로 셈).
  - 이 옛 정책을 고정하던 기존 테스트 1줄(`tests/test_evals_quality.py`, `krx_closed(2027-02-08)`이 False)을 새 정책(오류)으로 고쳤다.
  - 같은 테스트의 다른 단언(휴장일 건너뜀, 범위 밖 고정 공휴일 휴장)은 그대로다.
- `weekdays_behind`(평일 기준, 환율·뉴스용 판정)는 그대로다.

같은 계산을 하던 평가 코드 두 곳도 같은 유틸리티로 맞췄다:
- `metrics._business_days_between`: 평일 수 → KRX 거래일 수(`krx_days.trading_days_after`). live `data_date_ok`에만 쓰인다.
- `quality_fixtures.trading_days`: 코드 변경 없음, 설명만 고침(16절).

## 10. 변경 전 평가 결과

같은 날(2026-11-15), 같은 입력. 변경 전 코드는 `4d55296`이다.

| 평가 | 조건 | 결과 |
| --- | --- | --- |
| 품질 평가 `run_quality --mode deterministic` | 244문항, DB·LLM 없음 | **244/244**(제품 경로 142/142, 판정기 검증 102/102, 기준선 대비 회귀 0) |
| `run_eval --mode offline --split all`(CI와 같은 명령) | 59문항, 개발 DB 읽기 전용 세션(`default_transaction_read_only=on`) | **실패 11건**: `ent_08`·`ent_14`·`ent_15`·`ent_17`·`ent_21`~`ent_27`. 33차부터 기록된 "엄격 모드 의도된 거절"과 같다 |
| 저장된 실제 답 재채점(`check_data24`, `evals/results/raw_b2x_*.jsonl`) | 생성 시각 문장이 있는 답 24개, 개발 DB 읽기 전용 | 24개 모두 `시세 기준일 2026-11-03 != 2026-11-04`·`지연 분류 없음`. 원인: 답은 모두 2026-11-04(수) 16:53~18:38에 만들어졌고 그때 DB 최신일은 11-03이었다. 지금 DB에는 11-04 이후 시세가 있어 DB를 읽는 판정은 재현되지 않는다. 기대 거래일(DB 없음)은 24개 모두 11-04 |
| 평가 세트 v1·v2(live, 아래) | 같은 답을 v1·v2로 채점 | v1 41/42, v2 37/42 |

v1·v2 실행:
- 49차 v1 문항 44개를 **한 번** 실답으로 돌렸다(`run_multilingual`, 2026-11-15 16:59~17:03).
- 조건: Ollama `llama3.1:8b`(`OLLAMA_HOST=http://127.0.0.1:13434` 실행 환경 변수만, 설정 변경 없음), 개발 DB 읽기 전용 세션, `save_report=False`.
- 같은 답을 v1·v2 기대값으로 따로 채점했다(`rescore_routes --run-date 2026-11-15`, 50차와 같은 방식). 제품 코드는 이번에 바꾸지 않으므로 답은 한 번만 만들었다.

| 기대값 | 의도 판정 | 언어별 | 기간 실패 |
| --- | --- | --- | --- |
| v1 | 41/42 | en 13/13, ja 10/11, ko 6/6, zh 12/12 | L01 |
| v2 | 37/42 | en 12/13, ja 9/11, ko 5/6, zh 11/12 | P08·P03·P05·L01·P07 |

- L01: LLM 경로에서 종목을 못 찾았다(도구 0회). 50차 13.2절에 기록된 간헐 실패와 같다.
- v2의 P08·P03·P05·P07: v2 기대값 "1개월 = 24거래일"은 **2026-11-13 실행일에 고정된 값**이다(50차 13.1절). 오늘(11-15) 제품의 1개월은 22거래일이고, `rescore_routes`가 "실행일 2026-11-15의 1개월 = 22거래일(v2 기대값 24과 다름)" 경고를 냈다. 실행일 차이이고 이번 변경과 무관하다.
- 같은 실행의 다른 지표: 종목 경고 2(L01·N02), 번역 fallback 5, 번역 검증 실패 시도 30.

## 11. 변경 후 평가 결과

같은 입력·조건으로 다시 실행했다.

| 평가 | 결과 | 변경 전과 비교 |
| --- | --- | --- |
| 품질 평가 | 244/244 | 244문항 모두 통과 여부·실패 내용 같음 |
| offline | 실패 11건(같은 문항) | 59문항 모두 flags·failures 같음, 지표 같음 |
| 저장 답 재채점 | 24개, 같은 오류 | 출력 파일이 바이트까지 같음 |
| v1·v2 재채점 | v1 41/42, v2 37/42 | 출력이 바이트까지 같음 |

## 12. 평가 점수가 바뀐 이유

요청한 분류:

| 분류 | 건수 |
| --- | --- |
| 1. 휴장일 처리 오류가 바로잡혀 개선 | 0 |
| 2. 거래일 계산 변경으로 평가 대상 날짜가 달라짐 | 0 |
| 3. 기존과 결과 동일 | 전부(품질 244, offline 59, 저장 답 24, v1·v2 44) |
| 4. 새 오류 | 0 |

점수가 바뀌지 않은 이유:
- 휴장일 판정이 갈리는 입력이 지금 평가 세트에 없다.
  - 품질 평가 `now`: 2026-10-30·11-01(일)·11-02·11-06·11-07·11-09. 휴장일 아님
  - 저장 답 생성 시각: 11-04(수)
  - v1·v2 채점(`route_checks`)은 기간 숫자만 보고 바뀐 모듈을 import하지 않는다.
- 이번 수정은 **앞으로** 휴장일 근처(연휴 앞뒤, 휴장일 저녁)에 만든 답을 바르게 채점하기 위한 것이다. 그 효과는 7절 표와 14절 테스트로 확인했다.
- 일반 거래일 결과는 바뀌지 않는다(7절, `test_forecast_expected_day_unchanged_away_from_holidays`).

## 13. 평가 세트 v1·v2 불변 확인

| 파일 | SHA-256 | 상태 |
| --- | --- | --- |
| v1 `backend/evals/multilingual_cases_49.txt` | `29a21346ada5050b4ab570c889229a8460d965b44f817de833fa10cc1edb1484` | 49·50·51차 기록과 같음, 변경 없음 |
| v2 `backend/evals/multilingual_cases_49_v2.txt` | `3fe790cd2dde71a5adf4b3cafdac4a094127c040b1233327929751b21a3890a3` | 50·51차 기록과 같음, 변경 없음 |
| `quality_cases.jsonl`·`quality_dataset.json`·`quality_baseline.json`·`dataset.jsonl`·`multilingual_cases.txt` | 작업 전후 같음 | 변경 없음(`git diff` 없음) |

질문·기대 답·정답 데이터·임계값·통과 기준을 바꾸지 않았고, 문항을 빼지 않았다. 기준선 파일을 다시 쓰지 않았다(`--update-baseline` 쓰지 않음).

## 14. 신규 테스트와 전체 테스트 결과

`tests/test_krx_days_eval_57.py`(신규) 결과는 **77 통과**다. 다룬 범위:

| 범위 | 내용 |
| --- | --- |
| 날짜 판정 | 평일·토·일·휴장일·설날·특수 거래일·범위 밖 고정 공휴일·범위 밖 평일·미확정일·범위 시작 전 평일. 범위 밖·미확정은 오류(사유 포함) |
| 제품 달력과 대조 | 2023-01-01~2027-12-31 모든 날짜에서 상태·개장·마감이 같음 |
| 파일 오류 17종 | 파일 없음, JSON 오류, 그리고 형식 오류 15종: 휴장일 중복, 특수일 중복, 미확정=휴장일, 날짜 형식 2종, 주말 휴장일, 범위 밖 휴장일, 시각 형식, 24:00, 개장>=마감, 특수일=휴장일, 기본 마감 형식, 고정 공휴일 형식, 범위 없음, 시작>끝 |
| 잘못된 시각 입력 | `16:3`·`1630`·`24:00`·None |
| 거래일 수 | 5·20·60거래일(추석·개천절 대체·한글날·광복절 대체 포함), 주말 포함, 연속 휴장(2025-10-03~10-09), 연말·연초(2025-12-24~2026-01-05 = 6), 설(2026-02-09~02-20 = 7), 특수 거래일 포함 주(11-16~11-20 = 5) |
| 5·20·60 창 대조 | 2024-01~2026-09 매주 제품 달력과 대조 |
| 경계 | 주말 뒤 월요일 개장 전, 휴장일 당일, 휴장일 직후 거래일, 연속 휴장 마지막 날, 연속 휴장 뒤 첫 거래일, 수능일 15:30·16:29·16:30, 일반 15:29·15:30, 기간 끝이 휴장일 |
| 변경 전후 | 7절 표, 휴장일 없는 기간 동일 |
| 모듈 간 일관성 | 2025-09-01~2026-12-30 매일 7개 시각에서 `krx_days`·`quality_judges`·`forecast_check`·`run_quality._visible_rows`·제품이 같음. 지연 일수 `metrics`·`quality_judges`·`krx_days`·제품 `lag`가 같음 |
| 독립성 | `krx_days`·`quality_judges`가 `app`을 import하지 않음 |

나머지 테스트:

| 테스트 | 결과 |
| --- | --- |
| 기존 평가 테스트(`test_evals_quality`·`test_evals_metrics`·`test_evals_forecast_check`), 55·56차 테스트 | 통과(`test_evals_quality` 1줄 정책 변경 반영, 9절) |
| 단위 전체(`-m "not slow and not integration"`, `DATABASE_URL` 닫힌 포트) | **6,414 통과**(56차 6,337 + 신규 77), 실패 0 |
| 통합 전체(`-m "integration and not slow"`, 격리 DB `ai_investment_holiday57_it`) | **697 통과, 10 건너뜀**, 실패 0(건너뜀 9 = 마이그레이션 왕복 `MIGRATION_TEST_DATABASE_URL` 없음, 1 = `test_outlook_round25_devdb` 데이터 조건. 55·56차와 같다). `FC.status24`를 쓰는 `test_outlook_round24_devdb` 포함 |

격리 DB:
- `ai_investment_holiday57_it`를 새로 만들었다(같은 이름 없음 확인 → 개발 DB `pg_dump`를 `pg_restore`).
- 복원 직후 상태: `c7e2a9f4d158`, `stock_price` 251,455행, `market_index` 1,480행.
- 남겨 둔다.

## 15. 개발 DB 불변 확인

READ ONLY 연결, 55차와 같은 계산식:

| 항목 | 작업 전(55차 기록) | 작업 후 |
| --- | --- | --- |
| Alembic | `c7e2a9f4d158` | `c7e2a9f4d158` |
| `stock_price` 행 수 / md5 | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` |
| `market_index` 행 수 / md5 | 1,480 / `3ba6fedca411ebff119bef854f227f43` | 1,480 / `3ba6fedca411ebff119bef854f227f43` |
| `stock_price_session` | 없음 | 없음 |
| `users` | 2 | 2 |

개발 DB에 한 일은 모두 읽기뿐이다:
- 평가 실행: offline 2번, 저장 답 재채점 2번, v1 실답 1번. 모두 `default_transaction_read_only=on` 세션이다.
- READ ONLY 상태 조회, 격리 DB용 `pg_dump` 1번.

기존 시험 DB는 지우지 않았다: `_design53`·`_design53_it`·`_pipeline54`·`_pipeline54_it`·`_special_close55_it`·`_calendar56_it`·`ai_investment_verify`·`ai_investment_postcheck47`.

## 16. 남은 문제와 후속 조치

1. **합성 입력의 휴장일 행**: `quality_fixtures.trading_days`는 평일로 날짜를 만든다. 품질 평가의 시세 입력 53개 중 50개에 KRX 휴장일 날짜 행이 있다(304행, 예: 07-17·08-17·09-24).
   - 제품 경로와 정답 계산이 같은 행 목록을 순서로 읽으므로 판정은 어긋나지 않는다.
   - 끝 날짜·`now`가 휴장일인 문항은 없다.
   - 생성기를 바꾸면 평가 입력이 바뀌므로(정답 데이터 변경 금지) 그대로 두고 설명만 고쳤다.
   - 실제 거래일 날짜로 만들려면 입력 변경 승인과 기준선 재작성이 필요하다.
2. **2027 달력과 12-31 공지**: 범위 밖·미확정 평일에서 평가가 멈춘다(5절). 실행 날짜를 쓰는 판정(live `data_date_ok`)은 2026-12-31부터 멈출 수 있다. 12월 중순 연말 휴장 공지와 2027 휴장일·연초 개장 시각을 달력에 넣어야 한다.
3. **v2 "1개월 = 24"**: 실행일 고정값이라 오늘 실행하면 4문항이 실패로 나온다(10절). 실행일별 기대값을 정하는 것은 평가 세트 변경이라 하지 않았다.
4. **`rescore_routes.month_days`는 제품 `app.period_parser`를 import한다**(50차 도구, 실행일의 1개월 길이 경고용). 평가 판정이 아니라 경고라서 그대로 두었다.
5. **저장 답 재채점의 DB 의존**: `check_data24`는 지금 DB를 읽기 때문에 과거 답은 재현되지 않는다(10절). 답을 만든 시점의 DB 스냅샷 없이는 이전 채점을 다시 만들 수 없다.
6. **서버 재시작**(55·56차 반영): 여전히 승인 대기다. 11-19 09:00 전이 기한이다.

## 17. CI 결과

push 뒤 GitHub Actions 결과를 최종 보고에 적는다(이 문서를 담은 커밋이라 여기에 쓸 수 없다). 건너뛰는 job(`frontend`)이 있으면 그대로 보고한다.

## 18. 커밋 해시와 push 여부

- 커밋 대상:
  - `backend/evals/krx_days.py`(신규)·`quality_judges.py`·`forecast_check.py`·`run_quality.py`·`metrics.py`·`quality_fixtures.py`(설명만)·`README.md`
  - `backend/tests/test_krx_days_eval_57.py`(신규)·`test_evals_quality.py`(1줄 정책 변경)
  - 이 문서
- 바꾸지 않은 것:
  - 제품 코드(`backend/app/`)·`krx_calendar.json`
  - 평가 세트·기준선, 프런트엔드, `alembic/`(마이그레이션은 `pending`에 그대로), `.gitattributes`
- 커밋 해시·push 여부는 최종 보고에 적는다. main 병합은 하지 않는다.
