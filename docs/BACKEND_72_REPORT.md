# 72차: 데이터 최신성 표시와 AI 답변 신뢰성 개선 (2026-11-18)

브랜치 `feature/backend`. 71차 커밋(`10a2608`, `7a61abd`)은 고치거나 되돌리지 않았다. DB 쓰기, 데이터 갱신, 외부 수집, 마이그레이션, 서버 재시작은 하지 않았다. 프런트 파일도 고치지 않았다(인수인계: `docs/FRONTEND_HANDOFF_72.md`).

| 완료 기준 | 결과 | 근거 |
| --- | --- | --- |
| 환율 기간 수익률 계산 오류 | 0건 | 9.1 A, 10.2(실데이터 3건 SQL 일치) |
| 종목 간 수치 혼동 | 0건 | 9.1 C, 10.1(복수 종목 답 2건) |
| 근거 없는 원인 생성 | 0건(코드 경로) | 3개월 초과·복수 종목·시장·주어 없는 원인 질문이 모두 LLM을 부르지 않는다(10.1) |
| 기준일 이후 근거 사용 | 0건 | 69차 정책 유지, 새 경로도 같은 `cutoff_at` 사용 |
| freshness 시간 의미 혼동 | 해결 | 5절, 실데이터 10.3 |
| 빈 sources 상태 설명 | 가능 | 6절 `source_status` |
| 종목명 임의 번역 / 섹터 임의 추정 | 0건 | 7절(DB 변경 없음) |
| 실시간 가격과 저장 종가 혼동 | 구분 필드 추가 | 8절 |
| 68·69·71차 회귀 | 유지 | 9.2(의도된 기대 변경 2건) |
| 품질 평가 기준선 | 244/244, 회귀 0 | 9.2 |
| DB 변경 | 0건 | 10.4 |

## 1. 발견 문제

1. 3개월(93일·63거래일)을 넘는 원인 질문은 LLM 경로였다. 71차 실검증에서 "삼성전자 100거래일 동안 왜 올랐어?"(실제 -39.97%)의 전제를 바로잡지 않았다.
2. 두 종목 이상 원인 질문도 LLM 경로였다. 종목별 수치·기사를 코드가 나누지 않았다.
3. 환율 답 본문을 LLM이 써서 11-02 하루 등락률 -0.93%를 "최근 5일간 -0.93%"로 붙였다(70차).
4. 뉴스·공시 목록의 `freshness.last_collected_at`(11-17)과 `last_collection.finished_at`(11-03)이 서로 다른 시점을 가리켰다(서윤 씨 통합 테스트).
5. 원인 질문에서 기준일 뒤 기사가 빠져 `sources=[]`가 돼도 이유가 API에 없었다("삼성전자 오늘 왜 올랐어?", 제외 993건).
6. 영문 종목명(예: `JYP Ent.`)과 `sector` null이 화면에 그대로 보였다.
7. 시세(11-12)·환율(11-02)이 오래됐는데 최신처럼 보일 수 있었다.

조사 중 추가로 찾은 문제

8. 종목도 직전 종목도 없는 원인 질문("최근 6개월 동안 상승한 이유가 뭐야?")과 시장 원인 질문("코스피 한 달 동안 왜 떨어졌어?")도 LLM 경로였다(원인 근거가 없는데 LLM이 씀).
9. "카카오랑 삼송전자 왜 떨어졌어?"는 찾은 종목(카카오)만 조용히 답했다(오타 종목이 빠진 것을 말하지 않음).
10. 환율 원천(FinanceDataReader USD/KRW)은 주말 날짜 행 30개를 주고, 그 대신 금요일 날짜가 자주 빠진다. 11-02 행의 원천 `change_pct`(-0.93%)는 저장된 직전 행(10-31) 기준 계산값(-0.72%)과 다르다.

## 2. 원인

| 문제 | 원인 |
| --- | --- |
| 1·2·8 | 71차 `is_period_cause_question`이 `too_long`이면 False, `_forecast_subject`가 종목 1개만 받음. 시장·주어 없는 질문은 처리하는 코드 경로가 없었다 |
| 3 | 환율 질문이 LLM 경로. `fx_tool` 결과의 `daily_change_pct`(하루)와 `period_change_pct`(기간)를 LLM이 바꿔 붙였다 |
| 4 | `data_status.last_attempt("news")`는 `refresh_data`의 `news` 단계만 본다. 63차 복구(`scripts/recover_news_disclosures.py`)는 `news_recovery`·`disclosure_recovery`로 기록한다(id 277·278, 2026-11-17 09:34 KST). `last_collected_at`은 테이블 `max(created_at)`이라 복구를 반영한다. 두 값 모두 정의대로는 맞지만 뜻이 다르다 |
| 5 | `sources`는 `news_tool` 등의 결과에서만 만들어진다. 제외 건수는 `_meta()`(응답에 안 나감)에만 있었다 |
| 6 | 종목명은 KRX 상장 목록 원천값. 업종은 수집 코드가 없다(7절) |
| 9 | `extract_from_text`는 찾은 종목만 돌려준다 |
| 10 | 원천 특성(`app/ingest_validation.check_fx` 주석: 한국 휴장일·일요일 날짜에도 값을 준다). `change_pct`는 수집 때 df 안의 직전 행 기준이라, 그 뒤 직전 행이 갱신되면 저장값끼리의 계산과 달라진다 |

## 3. 수정 파일과 함수

| 파일 | 함수·내용 |
| --- | --- |
| `backend/app/answer_cause.py` | `is_period_cause_question`(3개월 초과도 코드 경로), `period_window`(초과 기간은 시작 전 종가가 없으면 `short_history`), `period_cause_answer`(`short_history`·긴 기간 근거 문단), 새 함수 `long_price_rows`·`count_evidence`·`long_evidence_lines`·`cause_cutoff_info`·`multi_cause_answer`·`multi_too_many_answer`·`unmatched_names`·`unmatched_note`·`is_market_cause_question`·`market_codes`·`market_spec`·`market_cause_answer`, 상수 `MAX_CAUSE_COMPANIES=3`·`ASK_CAUSE_SUBJECT` |
| `backend/app/answer_fx.py`(새 파일) | 환율 코드 답: `is_fx_question`, `spec_of`, `compute`, `answer`, `load_rows`, `facts_line` |
| `backend/app/agent.py` | `_answer_forecast`에 복수 종목 분기·주어 되묻기·미확인 종목 표시. `_forecast_body`(종목 하나 본문 분리, 동작 동일), `_cause_mode`, `_cause_multi_subject`, `_answer_cause_multi`, `_with_unmatched`, `_answer_market_cause`, `_answer_fx`. `_answer_cause_period`(긴 기간 시세·건수, 기록에 `cause_cutoff`). 원인 단독·원인+전망 기록에 `cause_cutoff`. `ask_question`이 `source_status`를 붙인다 |
| `backend/app/answer_basis.py` | `drop_fx_mislabels`(하루 값을 기간 표현과 쓴 문장 삭제), `fx_basis`가 코드 계산 수치 줄(`facts_line`)을 붙인다(LLM 경로) |
| `backend/app/sources.py` | `build_sources`(같은 문서의 종목 이름 합침), `source_status`, `SOURCE_STATUS_MESSAGES` |
| `backend/app/data_status.py` | `FEED_COLLECTION_STEPS`, `collection_summary` |
| `backend/app/stock_feed.py` | `freshness`(새 키 7개), `news_page`·`disclosure_page`가 테이블 최신 시각·수집 요약을 넘긴다 |
| `backend/app/api/research.py` | `SourceStatus` 모델, `ResearchResponse`·`ReportDetail`에 `source_status`(선택) |
| `backend/app/reports.py` | `get_report`가 tool 기록으로 `source_status`를 복원 |
| `backend/app/api/stocks.py` | `FeedFreshness`에 새 키(기본값 있음) |
| `backend/app/portfolio.py`, `backend/app/api/portfolio.py` | 주문 응답 `price_source`·`price_is_realtime`·`price_as_of`, 보유 `price_source`·`price_as_of` |
| `backend/API.md` | 뉴스 freshness 표에 새 행, 맨 끝 "72차" 절 |
| 테스트 | 새 파일 `tests/test_answer_round72.py`(단위), `tests/test_answer_round72_sql.py`(integration). 기대 변경: 69·71차 각 1개(9.2), 응답 키를 정확히 고정한 계약 테스트 7개 파일에 새 키 추가 |

## 4. 코드 기반 수치 계산 방식

종목·지수(71차 규칙 그대로, 긴 기간 포함)
- 달력 기간: 질문 범위 안 KRX 거래일 창. 출발 = 창 첫날 직전 저장 거래일 종가, 끝 = 창 안 마지막 저장 거래일 종가. 끝까지 저장되지 않았으면 "끝나지 않은 기간" 표시.
- N거래일: 시세 기준일부터 KRX 거래일로 N일을 거슬러 간 창, 그 직전 거래일 종가와 비교.
- 3개월 초과: 시세는 저장분 전체(`long_price_rows`, 260행 한도 없음)로 계산한다. 출발 종가가 없으면 계산하지 않는다(`short_history`). 71차 3개월 이내는 저장 시작일부터 계산하고 그 사실을 밝히는 동작 그대로다.
- 지수: 종목 상장 시장(KOSPI→KS11, KOSDAQ·KOSDAQ GLOBAL→KQ11)의 같은 두 날짜 종가. 종목 줄·지수 줄을 따로 쓰고 지수 움직임을 원인으로 쓰지 않는다.
- 복수 종목: 종목마다 위 계산을 따로 한다. 요약 줄의 수익률 비교는 출발·끝 날짜가 같을 때만 한다.

환율(`app/answer_fx.py`)
- 환율 기록일 = 저장된 `exchange_rate` 행. KRX 달력을 쓰지 않고, 없는 날 값을 만들지 않는다. 원천이 주말 날짜로 준 행이 계산에 쓰이면 그 날짜를 밝힌다.
- 하루 변동률: 마지막 기록과 바로 앞 기록의 종가 비교(저장값으로 다시 계산). 원천 `change_pct`와 다르면 그 사실을 밝힌다.
- N거래일 변동률: 마지막 기록과 N개 앞 기록(N개 구간). 기본(기간 없음)은 5거래일.
- 달력 기간(지난주·한 달·N개월): 기간 시작 전 마지막 기록 → 기간 안 마지막 기록. 끝까지 기록이 없으면 "기간 전체가 아님", 시작 전 기록이 없으면 계산하지 않는다.
- 변동 폭: 원 단위 차이와 방향(상승·하락·보합).
- 하루·기간 변동률은 늘 다른 줄, 다른 이름이다. 오늘·어제 기록이 없으면 그날 변동을 계산하지 않고, 마지막 기록일 하루 값이라고 밝힌다.
- 원인·전망은 말하지 않는다(환율 원인 뉴스·지표와 예측 모델이 없다).
- 범위: 종목 없는 원/달러 수준·등락·흐름 질문. 종목·업종 영향 설명 질문은 LLM 경로이고, 답 끝에 코드 계산 수치 줄을 붙이며 하루 값을 기간 표현과 쓴 문장은 뺀다.

## 5. freshness 필드 정의

`GET /api/stocks/{ticker}/news`·`/disclosures`의 `freshness`. 기존 키의 뜻은 그대로다.

| 의미 | 키 | 정의 |
| --- | --- | --- |
| 데이터 자체의 최신 시점 | `latest_record_at`, `record_lag_weekdays`, `freshness_state` | 테이블 전체 `max(published_at)`(공시는 `max(disclosed_at)`). `freshness_state`: 평일 지연 2 이상이면 stale, 없으면 no_data |
| 마지막 성공 수집 | `last_successful_collection_at` | 뉴스는 `news`·`news_recovery`, 공시는 `news`·`disclosure_recovery` 단계 중 status=success인 마지막 종료 시각 |
| 마지막 수집 시도 | `last_attempt_finished_at`, `last_collection_status`, `last_collection_step` | 같은 단계들의 skipped를 뺀 마지막 기록. partial·failed는 성공으로 치지 않는다 |
| 마지막 저장(기존) | `last_collected_at` | 테이블 `max(created_at)` |
| refresh_data news 단계(기존) | `last_collection` | 뜻 그대로(복구 단계 제외) |

데이터 최신성(`freshness_state`)과 수집 성공(`last_collection_status`)은 따로 판단한다.

## 6. sources 빈 상태 처리

- 69차 기준 시점 정책은 그대로다. 빈 출처에 기사를 채우거나 출처를 만들지 않는다.
- `sources: []` 계약은 유지하고 `source_status`를 더했다. 상태는 `available`·`not_applicable`·`none_found`·`excluded_after_cutoff`·`long_period`·`error`다. `error`만 `is_error: true`다.
- 계산은 tool 기록만 쓴다. 코드 원인 경로는 `news_tool`·`disclosure_tool` 기록의 `cause_cutoff`(기준일, 마감 시각, 제외 건수, 정책)를 읽는다. LLM 경로는 69차 `filter_records`가 남긴 `cause_cutoff`를 읽는다. 그래서 저장된 리포트도 `tool_call_log`로 같은 값을 복원한다.
- 같은 종목의 뉴스·공시 기록은 같은 DB 전체 건수를 들고 있어 종목·기준일마다 한 번만 센다(복수 종목은 종목별 합).
- 필터를 우회하는 경로: 새 코드 경로는 모두 `collect`·`count_evidence`(기준 시점 SQL)만 쓰고, 출처는 그 결과 기록에서만 만든다. LLM 경로는 69차 `filter_records`가 그대로 거른다.

## 7. 종목명·섹터 데이터 현황(개발 DB 읽기 전용)

- 종목 350(주식 300: KOSPI 219·KOSDAQ 55·KOSDAQ GLOBAL 26, ETF 50). 종목코드 중복 0, 이름 중복 0.
- 한글이 없는 이름: 주식 23(`CJ`, `DL`, `F&F`, `GS`, `HDC`, `HLB`, `HMM`, `HPSP`, `ISC`, `JYP Ent.`, `KCC`, `KT`, `KT&G`, `LG`, `LS`, `LS ELECTRIC`, `NAVER`, `NC`, `NHN`, `RFHIC`, `SK`, `SKC`, `S-Oil`), ETF 7(`KODEX 200` 등 브랜드명).
  - 출처: `scripts/build_universe.py`의 FinanceDataReader `StockListing`(KRX 상장 목록) `Name`. KRX 약식 종목명이 영문인 종목이라 원천 그대로다. 번역하지 않았다.
  - 한글 별칭은 코드(`app/company_aliases.py`, 검색용)에 있다: NAVER→네이버, JYP Ent.→JYP엔터·JYP엔터테인먼트, NC→엔씨소프트·NC소프트, HLB→에이치엘비, HMM→현대상선, S-Oil→에스오일·에쓰오일, LS ELECTRIC→LS일렉트릭. 검색용 별칭이라 표시명 필드로 내보내지 않았다. 공식 한글 표시명(예: KRX 정식 한글 회사명)을 따로 수집해 검증하기 전에는 별칭을 표시명으로 쓰지 않는 것이 맞다고 판단했다.
- `sector`: 350/350 null(채움 0%). 업종을 수집하는 코드가 없다(`company.sector`를 쓰는 수집 스크립트 없음, CI 시드만 컬럼을 옮김). 근거 없이 채우지 않았다. 원천 후보는 FinanceDataReader `StockListing("KRX-DESC")`의 Sector·Industry(KRX 업종)다. 쓰려면 이용 조건 확인과 별도 승인(DB 쓰기)이 필요하다.
- 프런트는 null을 `—`로 보인다(인수인계 4절).

## 8. 실시간 가격과 저장 종가의 구분

| 가격 | 공급원 | 기준 시각 | API |
| --- | --- | --- | --- |
| 실시간 현재가 | 네이버 금융 비공식 폴링(`polling.finance.naver.com`), 정규장 OPEN·애프터마켓 아님일 때만 | `localTradedAt`(체결 시각) | `realtime-price`: `is_realtime`, `source: "naver"`, `as_of` |
| 저장 종가 | FinanceDataReader 일봉(공식 종가 기준, 44~47차) | 그 행의 거래일 | 시세·차트 API, 종목 목록 `latest_close` |
| 모의 주문 체결가 | `get_realtime_price_data`: 실시간, 실패하면 저장 종가 | 위 둘 중 하나 | 주문 응답 `price` + 새 `price_source`·`price_is_realtime`·`price_as_of` |

189,800원(장중 실시간 체결)과 208,000원(2026-11-12 저장 종가)은 기준 시점이 달라 생긴 차이다. 오류가 아니다. 이번에 주문·보유 응답에 출처와 기준 시각을 더해 화면이 둘을 구분할 수 있게 했다. `price` 값과 체결 로직은 바꾸지 않았다.

`price_source`의 타입은 `str | null`이다(보통 `"naver"` 또는 `"fallback"`). 처음(`e9a023b`)에는 `Literal["naver","fallback"]`로 두었다. 그런데 CI 통합 테스트에서 다른 값(테스트 대역 `"x"`, `"fake"`)이 오자, **체결이 커밋된 뒤 응답 검증에서 500**이 났다. 실제 체결된 주문이 오류로 보이면 안 되므로 `7126fe0`에서 문자열로 바꿨다.

## 9. 테스트

### 9.1 신규 테스트

`tests/test_answer_round72.py`(DB·Ollama 없음): 48개, 모두 통과

| 범주 | 내용 |
| --- | --- |
| A 환율 | 5거래일 = 5구간, 하루 값은 다른 줄·다른 이름, LLM이 하루 값을 "최근 5일간"에 붙인 문장 삭제(하루라고 밝힌 문장은 유지), 값이 같으면 건드리지 않음, 한 달 창 출발점·미완료 표시, 주말 날짜 행 표시·날짜 생성 없음, 한글날 공백 미보충, 데이터 부족·기간 전 시작 계산 거절, 기록 없는 기간·오늘 미저장, 원천 change_pct 불일치, 원인·전망 미제공, 코드 경로 범위 8개, 핸들러(LLM 없음, `fx_tool` 기록) |
| B 긴 기간 | 시작 전 종가 없으면 `short_history`, 그 답에 수치 없음, 건수만·목록 없음·저장 범위 표시·전제 교정, agent 경로(LLM 미호출, `long_period`), 100거래일, 주어 없는 질문 되묻기 |
| C 복수 종목 | 종목별 기사·지수(코스피/코스닥) 분리, 출처의 종목 이름, 면책 1회, 같은 기사 두 종목 이름, 날짜가 다르면 비교 안 함, 원인 단독 모드 종목별 본문, 4종목 거절, 미확인 종목 감지 6개와 답 맨 앞 표시 |
| 시장 원인 | 지수 등락·원인 특정 불가·전제 교정, 오늘 미저장이면 "오늘"이라 부르지 않음, 지수 선택 |
| D freshness | 네 시각 분리·기존 키 유지, 최신성과 수집 상태 분리, stale·no_data |
| E sources | 6가지 상태, 같은 종목 두 기록 중복 집계 없음, LLM 경로 합산, 기준 시점 정보, 응답 모델 |
| F 종목·가격 | 영문 공식명 그대로, sector null, 주문 응답 가격 출처(naver·fallback) |

`tests/test_answer_round72_sql.py`(integration, 롤백, 개발 DB에서는 건너뜀): 2개
- `collection_summary`: 복구 단계 인식, skipped 제외, 실패가 성공 시각을 바꾸지 않음, partial은 성공 아님, 뉴스·공시 단계 분리
- `count_evidence`: 창 안 건수(출발일 마감 뒤~기준일 마감 전, 관련 없음·자정 제외), 마감 뒤·시각 모름·기준일 뒤 건수, 공시 건수
- 로컬 결과: 평가 DB(`ai_investment_eval68`)에서 71차 SQL 2개와 함께 4개 통과.

### 9.2 회귀·전체

| 항목 | 결과 |
| --- | --- |
| 68차 `test_answer_quality_round68.py` | 통과(변경 없음) |
| 69차 `test_answer_cause_round69.py` | 통과. 기대 변경 1개: `test_two_companies_or_long_period_stay_on_llm_path` → `test_two_companies_and_long_period_routes`(4개월 질문이 LLM 경로 → 기간 코드 경로, 두 종목은 복수 종목 경로) |
| 71차 `test_answer_cause_round71.py` | 통과. 기대 변경 1개: `test_longer_than_three_months_stays_on_existing_path` → `..._now_on_code_path`(`too_long` 표시는 그대로, 이제 코드 경로) |
| 응답 키 고정 계약 테스트 | `test_api_contract_round38`·`test_i18n`·`test_event_reaction_agent`·`test_disclaimer`·`test_etf_api`·`test_portfolio_api`·`test_stock_feed_api`·`test_answer_quality`·`test_api`, 통합 `test_order_strict_devdb`·`test_history_devdb`·`test_api_contract_round37_devdb`의 기대 키·값에 72차 새 키만 더함(기존 키 비교는 그대로 엄격). `test_stock_feed_api::test_query_counts`는 수집 요약 쿼리 1개만큼 늘림(뉴스 7·공시 6·ETF 4, 페이지 크기와 무관). 생성 문서 `docs/frontend-api-types.ts`·`docs/FRONTEND_API_HANDOFF_60.md` 부록 A는 생성 스크립트로 다시 만듦(추가만) |
| 통합(CI 선택 `integration and not slow and not needs_collected_data`, 평가 DB) | **614 통과**, skip 9(마이그레이션 왕복, 별도 DB 주소 필요) |
| 전체 단위(CI 조건, `CI_FAIL_ON_SKIP=1`, 로컬 DATABASE_URL `0.0.0.0:1`) | **6,727 통과, 실패 0, skip 0**(71차 6,679 + 72차 48) |
| 품질 평가 `evals.run_quality`(평가 DB, Ollama 닫힌 포트) | **244/244**, 회귀 0, 새 문항 실패 0(영역 A~H 모두 100%) |
| 오프라인 평가 `evals.run_eval --mode offline --split all` | 59문항, 실패는 기준선과 같은 11건(`ent_08·14·15·17·21~27`, 엄격 모드의 의도된 거절). 게이트 통과(오매칭률 0) |
| 비밀값 검사 `scripts/secret_scan.sh` | 통과 |

전망 5·20·60거래일, ETF, 포트폴리오 진단, 일일 브리핑은 코드 경로를 바꾸지 않았다(`_forecast_body`는 본문을 함수로 옮긴 것). 해당 기존 테스트와 품질 평가 영역(B·D·E·F·G)이 그대로 통과한다.

## 10. 실제 데이터 검증(개발 DB, 읽기 전용, LLM 호출 시 실패하도록 막고 실행)

### 10.1 원인 질문

| 질문 | 결과 | SQL 대조 |
| --- | --- | --- |
| 삼성전자 지난 6개월 동안 왜 올랐어? | 05-15 270,500 → 11-12 208,000 -23.11%, 전제 교정, 코스피 7,493.18 → 5,342.73 -28.70%, 뉴스 60·공시 54건(목록 없음), 기사 저장 09-24부터·공시 06-24부터 표시, `long_period` | 일치 |
| 카카오 1년 동안 왜 떨어졌어? | 2025-11-17 60,700 → 31,700 -47.78%, 코스피 +30.65% | 일치 |
| 삼성전자 100거래일 동안 왜 올랐어? | 06-17 346,500 → 208,000 -39.97%(71차가 바로잡지 못한 질문), 전제 교정 | 일치 |
| 삼성전자 3년 동안 왜 올랐어? | 2023-11-17 72,500 → 208,000 +186.90%, 코스피 +116.32% | 일치 |
| 삼성전자와 SK하이닉스 최근 한 달 동안 왜 올랐어? | 종목별 단락. 삼성전자 -3.48%, SK하이닉스 -1.42%(둘 다 전제 교정), 기사·공시 종목별, 출처 20건 | 일치 |
| 카카오와 네이버 하락 원인을 비교해 줘. | 종목별 5거래일 단락(카카오 -1.55%, NAVER -1.68%) | 일치 |
| 카카오랑 삼송전자 왜 떨어졌어? | 맨 앞 "'삼송전자'는 등록된 종목에서 찾지 못해…", 카카오만 답 | - |
| 삼성전자 SK하이닉스 카카오 셀트리온 왜 떨어졌어? | 3종목까지 안내, 도구 호출 없음 | - |
| 최근 6개월 동안 상승한 이유가 뭐야? / 왜 떨어졌어?(직전 대화 없음) | 종목 되묻기 | - |
| 코스피 한 달 동안 왜 떨어졌어? | 10-16 5,668.05 → 5,342.73 -5.74%, 원인 특정 불가 | 일치 |
| 삼성전자 오늘 왜 올랐어? | 69차 답 그대로 + `source_status=excluded_after_cutoff`, 제외 993건 | 993 일치 |

### 10.2 환율(저장 기록 2026-04-02~11-02, 153개)

| 질문 | 결과 | SQL 대조 |
| --- | --- | --- |
| 최근 5거래일 동안 환율이 얼마나 변했어? | 10-27 1,324.77 → 11-02 1,325.52 +0.06% / 하루 10-31(토) 1,335.10 → 11-02 -0.72%(다른 줄) | 일치 |
| 한 달간 원달러 환율 흐름 알려줘. | 10-15 1,340.95 → 11-02 -1.15%, 미완료 기간·주말 날짜(10-18 일, 10-31 토) 표시 | 일치 |
| 원달러 환율 오늘 얼마나 올랐어? | 오늘 기록 없음, 마지막 기록일 하루 -0.72%, 11영업일 전 값 | 일치 |
| 최근 5일 환율 상승률과 하루 변동률을 비교해 줘. | '5일'(11-14~11-18)은 기록 없음이라 계산 안 함, 하루 값은 따로 | - |
| 70차 오류 문장 "최근 5일간 -0.93%" | 이제 그 문장 자체가 나오지 않는다(코드 답). LLM 경로에서 나오면 삭제 | - |

원천 `change_pct` -0.93%(11-02)는 저장된 직전 행(10-31 1,335.10) 기준 -0.72%와 다르다. 답은 저장값으로 계산하고 차이를 밝힌다.

### 10.3 freshness(삼성전자)

뉴스: `last_collection.finished_at` 11-03 19:54(news), `last_successful_collection_at` 11-17 09:34(`news_recovery`), `latest_record_at` 11-17 09:32, `freshness_state` fresh. 공시: `last_collection_step` `disclosure_recovery`, `latest_record_at` 11-17 00:00 KST(접수일 11-17).

### 10.4 DB 쓰기 여부

개발 DB 접근은 모두 `PGOPTIONS=-c default_transaction_read_only=on`(psql·파이썬 모두)이었다. SQL 통합 테스트는 평가 DB에서 롤백했다. 데이터 수집·갱신·마이그레이션·백엔드 재시작·Ollama 설정 변경은 하지 않았다.

## 11. 데이터 최신성 현황과 갱신 계획(갱신은 하지 않음)

2026-11-18 기준, 개발 DB 읽기 전용 조회.

| 데이터 | 최신 기준일 | 마지막 성공 수집(기록) | 지연 | 공급원 | 이용 조건 확인 |
| --- | --- | --- | --- | --- | --- |
| 주식·ETF 일봉 | 2026-11-12(350종목 모두) | `refresh_data` prices 11-03. 실제 행은 11-13 10:12 KST 수동 수집(기록 없음) | 3거래일(11-13·16·17) | FinanceDataReader(KRX·네이버 등 공개 데이터 래퍼) | 비공식 래퍼. 학습·시연 내부 사용, 재배포 안 함 |
| 지수 KS11·KQ11 | 2026-11-12 | index 11-03, 행은 11-13 수동 | 3거래일 | FinanceDataReader(실패 시 Yahoo 심볼 폴백) | 위와 같음 |
| 환율 USD/KRW | 2026-11-02 | fx 11-03 20:03 | 11영업일 | FinanceDataReader | 위와 같음. 주말 날짜 행 원천 특성 |
| 뉴스 | 게시 11-17 09:32 | news_recovery 11-17 09:34 | 1평일 | 네이버 뉴스 검색 API(공식 Open API, 키 필요) | 공식 API 약관. 기사 원문 재배포 안 함(제목·발췌·링크만) |
| 공시 | 접수 11-17(KST) | disclosure_recovery 11-17 09:34 | 1평일 | DART Open API(공식, 키 필요) | 공식 |
| 뉴스·공시 임베딩 | 뉴스 130,640/130,640, 공시 9,936/9,936 | embed 11-03, 66차 후처리 11-17 | 0 | 로컬 Ollama bge-m3 | 로컬 |
| ETF 구성 | TIGER 15종(10-29), KODEX 23종(10-30), 50개 중 38개 | 수집 10-29·10-31 | 약 3주 | 운용사 공시 PDF(미래에셋·삼성) | 운용사 공개 자료, 재배포 안 함 |
| 분기 실적 | 접수 최대 2026-10-30, 298종목 2,925행 | 11-03 | 다음 분기 공시 전까지 최신 | DART 단일회사 재무 API | 공식 |

갱신에 필요한 작업(승인 뒤): (1) DB 백업(덤프 + SHA-256 + 복구 확인), (2) `python -m scripts.refresh_data`(prices·index·fx·news·embed·content, advisory lock, 단계 기록이 남는다), (3) 검증: `refresh_data --status`, 품질 36종 점검, 시연 종목 종가 대조, 환율 기준일, freshness API 값 확인, `evals.run_quality`. ETF 구성은 운용사 PDF 수집 스크립트를 따로 실행한다.

## 12. 남은 위험과 제약

1. 실행 중인 백엔드(8000)는 69·71·72차 코드가 반영되지 않았다. 재시작은 승인 대상이다.
2. 시세 11-12, 환율 11-02에 멈춰 있다. 답은 지연을 밝히지만 시연 전 갱신(승인)이 필요하다.
3. 환율 원천의 주말 날짜 행: 답에 밝히지만 날짜 의미(어느 시장 세션인지)는 확인하지 못했다. 원천 날짜를 그대로 쓴다.
4. 긴 기간 원인 질문은 원인을 특정하지 않는다(의도). 기사 저장 범위가 대부분 2026-09-24 이후라 긴 기간 근거가 원래 부족하다.
5. 미확인 종목 감지는 접속 조사(랑·와·과·하고·및·쉼표) 바로 옆 단어만 본다. "카카오 그리고 삼송전자"처럼 떨어져 있으면 감지하지 못한다(그때도 다른 종목 수치를 만들지는 않는다).
6. 69차 원인 단독 단락은 지수 이름 대신 "같은 기간 지수"라고 쓴다(종목 상장 시장 지수, 69차 그대로).
7. 같은 사건 기사 여러 건이 근거 목록을 채우는 문제, 제목 규칙 기반 시세 기사 판별은 71차 그대로다.
8. 환율이 종목·업종에 미치는 영향 같은 설명 질문은 LLM 경로다. 수치 줄과 오표기 문장 삭제로 막지만, 문장 전체를 코드가 쓰지는 않는다.

## 13. 다음 단계 제안

1. 데이터 갱신(백업 → `refresh_data` → 검증), 승인 필요
2. 병합 전용 브랜치에서 `feature/backend` 병합 검토(분봉 workflow 제외), CI 전 job
3. 백엔드 재시작(승인) → health·API 확인
4. 프런트 최신 main + `VITE_USE_MOCK=false`로 실제 브라우저 확인, 시연 리허설

## 14. 커밋과 CI

| 커밋 | 내용 | CI |
| --- | --- | --- |
| `e9a023b` | fix(ai,api): round-72 code paths, source_status, freshness, price source | 실패(run 44250051817): `backend-integration`만 실패(개발 DB 계약 테스트 키 고정 10건 + `price_source` Literal 검증으로 체결 뒤 500 3건). 나머지 job 성공 |
| `7126fe0` | fix(api): price_source as plain string; devdb contract tests for added keys | **성공**(run 44252231778): secret-scan, backend-unit, backend-integration, eval-offline, detect-frontend 성공, frontend skip(프런트 변경 없음) |
| 이 보고서 | docs: round-72 report and frontend handoff | 최종 보고에 적는다 |
