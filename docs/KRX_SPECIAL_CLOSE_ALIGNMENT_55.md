# 55차 KRX 특수 마감 시각 반영과 기존 서비스 정합성 검증 (2026-11-15)

**개발 DB(`ai_investment`)는 바꾸지 않았다.** 마이그레이션·적재·재수집·`refresh_data` 실행을 하지 않았다. 대기 마이그레이션 `0f6d0ec52ac3`은 `backend/alembic/pending/`에 그대로 있다.
**2026-11-19 실제 분봉·지수는 아직 없다.** 이 문서의 11-19 결과는 시각을 주입한 단위 테스트다. 지수 값·네이버 값은 합성이다.
세션 일정(`krx_session_schedule.json`)의 11-19 상태는 **`announced` 그대로**다.

## 1. 시작 시 브랜치와 기준 커밋

| 항목 | 값 |
| --- | --- |
| 브랜치 | `feature/backend`, `origin/feature/backend`와 같은 커밋, 작업 트리 깨끗함 |
| 기준 커밋 | `0ca512a`(54차) |
| 확인한 문서 | 54차 보고서 전체, 50차 시장 데이터 품질 감사(지수 확정), 51차 세션 데이터 설계, 54차 세션 일정 구조(`app/session_schedule.py`) |
| 실행 중인 백엔드 | `uvicorn app.main:app --host 127.0.0.1 --port 8000`(PID 28088, 부모 25744, 2026-11-15 15:12 시작, `--reload` 없음). 종료·재시작하지 않았다 |

## 2. `krx_calendar.json` 기존 구조와 사용처

### 2.1 구조

`backend/app/data/krx_calendar.json`(읽기: `app/krx_calendar.py`):

| 키 | 내용 |
| --- | --- |
| `version`·`updated` | `krx-2026-11-08`·`2026-11-08`(변경 전) |
| `coverage` | 2023-10-30~2026-12-31 |
| `regular_close` | `"15:30"`(정규시장 마감) |
| `sources` | 근거 키: `db`·`statute`·`krx_notice` |
| `holidays` | 평일 휴장일 55일 |
| `unconfirmed` | 2026-12-31(연말 휴장 미확정) |
| `fixed_annual` | 범위 밖 해의 고정 공휴일 |
| `special_sessions` | **빈 목록**(변경 전). 형식은 `special_sessions_note`에 정해져 있다: `{"date", "close", "name", "source"}` |

- 로더는 `special_sessions`를 `{날짜: close}`로 읽는다.
- `close_time(d)`는 그 날짜가 있으면 그 값을, 없으면 `regular_close`를 돌려준다.
- 이 기능은 36차부터 있었다. 비어 있었을 뿐이다. **스키마를 넓히지 않았다.**

### 2.2 `close`의 의미 확인

모든 사용처에서 `close_time()`은 **정규시장 마감**(종가 단일가 체결 시각)으로 쓰인다. 다른 뜻(시간외·애프터마켓 종료)으로 쓰는 곳은 없다. 그래서 16:30을 넣어도 의미 충돌이 없다.
세션 일정 쪽 대조 함수 `session_schedule.calendar_mismatches()`도 `close_time`을 종가 단일가 봉 시각(`closing_at`)과 비교한다.

### 2.3 사용처와 경계값

| # | 기능 | 코드 | 경계값(변경 전) | 55차 |
| --- | --- | --- | --- | --- |
| 1 | 지수 종가 확정 검증 | `app/index_confirm.intraday` | `close_time(그날) + 10분` | JSON만으로 반영 |
| 2 | 장중 판정 | `krx_calendar.session_is_open` ← `briefing.session_is_open`·`drop_open_session` | `close_time(그날)` | JSON만으로 반영 |
| 3 | 기대 거래일(신선도) | `krx_calendar.expected_trading_day` ← `outlook.expected_trading_day`·`data_monitor.expected_close_date`·`market_snapshot` | `close_time(그날)` | JSON만으로 반영 |
| 4 | 직전·다음 거래일 | `last_/previous_/next_trading_day` | 날짜만(시각 무관) | 영향 없음 |
| 5 | 지연 일수 | `krx_calendar.lag`·`data_status.business_days_behind` | 날짜만 | 영향 없음(기대 거래일만 바뀜) |
| 6 | 일일 브리핑 기준일 | `daily_briefing`·`daily_briefing_db`(`session_is_open`·`expected_trading_day`) | 2·3과 같음 | JSON만으로 반영 |
| 7 | 시장 요약 API | `market_snapshot.cutoff_date`·`freshness.intraday_excluded` | 2·3과 같음 | JSON만으로 반영 |
| 8 | `/health`·`/api/status` | `data_status.is_stale` | 날짜만(마감 시각 무관) | 영향 없음 |
| 9 | 시세 지연 이유 | `outlook.price_status` | **15:30 상수**(`briefing.MARKET_CLOSE`) | `close_time(기대 거래일)`로 고침 |
| 10 | ETF 최신일 저장 | `official_close.official_records` | **15:30 상수**(`REGULAR_CLOSE`) | `close_time(오늘)`로 고침 |
| 11 | 뉴스 반응 기준일 | `event_reaction.resolve_base_and_start` | **15:30 상수**(`MARKET_CLOSE`) | `close_time(발행일)`로 고침 |
| 12 | 단순 시세 답 장중 안내 문장 | `code_answers.intraday_note` | 문장에 **"15:30" 고정** | 그날 마감 시각을 쓴다(일반 거래일 문장 그대로) |
| 13 | `refresh_data` 장중 경고 | `scripts/refresh_data.is_market_hours` | **09:00~15:30 고정** | 끝을 `close_time(오늘)`로 고침(시작 09:00 그대로) |
| 14 | `backfill_history` 기간 끝 | `scripts/backfill_history.period` | **15:30 상수** | `close_time(오늘)`로 고침 |
| — | 환율 기대일 | `data_monitor.expected_weekday_close` | 15:30(평일) | **바꾸지 않음**(KRX가 아닌 환율 정책, 14절) |
| — | 세션 분류 | `session_bars`·`session_schedule` | 세션 일정 파일 | 일정 파일은 바꾸지 않음. 달력과의 불일치 표시만 사라짐 |

9~14는 54차 보고서에 없던 경로다. 모두 JSON만 고쳐서는 11-19를 15:30 마감으로 계산한다.
- **일반 거래일**: `close_time()`이 15:30을 돌려주므로 결과가 같다(7절 회귀 테스트, 기존 테스트 전체 통과).
- **API 응답 필드**: 추가·변경 없음. `backend/API.md`도 바꾸지 않았다.
- 13·14는 운영 스크립트의 안전장치다. 실행하지 않았고 시각 주입 테스트만 했다.

## 3. 2026-11-19 마감 시각 변경 근거

| 근거 | 내용 | 이번 확인 |
| --- | --- | --- |
| 유안타증권 공지(2026-11-13) [링크](https://04.myasset.com/myasset/customer/notice/CU_0201000_P2.cmd?SEQ=202611131405580000000004&gubun=norNotice) | "대학수학능력시험일 매매시간 변경 안내(11/19)": 정규시장 09:00~15:30 → **10:00~16:30**, 장종료후 시간외 종가매매 15:40~16:00 → 16:40~17:00, 애프터마켓 16:00~20:00 → 17:00~20:00(ETF·ETN·상장형 수익증권 제외), 장개시전 시간외 종가 08:30~08:40 → 09:30~09:40 | 이번에 열어 확인 |
| 연합뉴스(2026-11-05) [링크](https://www.yna.co.kr/view/AKR20261105065500008) | 한국거래소 발표 보도 | 이 환경에서 열리지 않음(54차와 같음). 54차에서 같은 날 보도 5건으로 같은 내용 확인 |

- 한국거래소 공지 원문은 이번에도 확인하지 못했다. 증권사 공지 1건과 보도로 판단했다.
- `close`에는 정규시장 마감 16:30만 넣는다. 장후 시간외 종료(17:00)·애프터마켓 종료(20:00)는 넣지 않는다.
- NXT 거래시간과는 관계없다. 이 달력은 KRX 정규시장 달력이다.

## 4. 실제 변경한 JSON 항목

`backend/app/data/krx_calendar.json`(HEAD와 키 단위로 비교한 결과):

| 키 | 변경 |
| --- | --- |
| `special_sessions` | `[]` → `[{"date": "2026-11-19", "close": "16:30", "name": "2027학년도 대학수학능력시험일(정규시장 10:00~16:30)", "source": "krx_notice"}]` |
| `sources.krx_notice` | 기존 문장 뒤에 11-19 근거(연합뉴스·유안타증권 URL)를 덧붙임 |
| `version`·`updated` | `krx-2026-11-08`·`2026-11-08` → `krx-2026-11-15`·`2026-11-15`(데이터가 바뀌었으므로. `data_monitor` 보고서의 `trading_calendar.version`에 보인다) |
| `holidays`·`unconfirmed`·`regular_close`·`coverage`·`fixed_annual` | **변경 없음** |

- 날짜 항목은 11-19 하나뿐이다.
- 형식(`date`·`close`·`name`·`source`, 필드 순서)은 `special_sessions_note`를 따랐다. `source`는 기존 `sources` 키(`krx_notice`)다.
- 같은 정보를 다른 파일에 새로 등록하지 않았다. 세션 일정 파일은 54차 그대로다.

### 4.1 로더 검증 추가(`app/krx_calendar.py`)

- 변경 전에는 `special_sessions`를 검증하지 않았다. 같은 날짜가 두 번이면 뒤 값이 조용히 이겼다.
- 55차부터 다음은 읽을 때 `ValueError`로 거부한다. 휴장일 검증과 같은 방식이다.
  - 같은 날짜 두 번
  - 주말·휴장일
  - 적용 범위 밖
  - `close`가 `HH:MM`(00:00~23:59)이 아님(`16:3`·`24:00`·`16:30:00` 거부)

## 5. 지수 확정 검증 전후 동작

- 규칙: `index_confirm.intraday(day, now)` = `now < close_time(day) + 10분`(50차 규칙 그대로)
- 이 시각 전의 오늘 행은 `intraday`로 보류한다.
- 그 뒤에는 50차 대조 규칙을 그대로 적용한다:
  - 네이버 일별과 같으면 저장(`confirmed`)
  - 다르면 보류(`mismatch`)
  - 네이버에 없거나 읽지 못하면, 마지막 행은 보류(`unverified`)

변경 전 재현(같은 스크립트, 장중 값이 우연히 네이버와 같은 경우, 11-19):

| 시각 | 변경 전 | 변경 후 |
| --- | --- | --- |
| 15:39:59 | `intraday` | `intraday` |
| 15:40:00 | **`confirmed`(장중 값 저장)** | `intraday` |
| 16:00:00 | **`confirmed`** | `intraday` |
| 16:29:59 | **`confirmed`** | `intraday` |
| 16:30:00 | **`confirmed`** | `intraday`(마감 도달, 확정 대기) |
| 16:39:59 | **`confirmed`** | `intraday`(10분 확인 지연 안) |
| 16:40:00 | `confirmed` | 대조 시도 → `confirmed` |
| 16:41:00 | `confirmed` | 대조 시도 → `confirmed` |

같은 재현에서 바뀐 다른 판정(11-19):

| 판정 | 변경 전 15:30~16:29 | 변경 후 15:30~16:29 |
| --- | --- | --- |
| `session_is_open` | False | True |
| 기대 거래일 | 11-19 | 11-18 |
| 뉴스 반응(16:00 발행) | 기준 11-19, 시작 11-20 | 기준 11-18, 시작 11-19 |
| 세션 일정 불일치 | `[{2026-11-19, 16:30 vs 15:30}]` | `[]` |

## 6. 15:40~16:40 경계값 테스트 결과

`tests/test_krx_special_close_55.py::test_csat_index_confirmation_boundaries`(8개 시각, 시각 주입, 통과):
- 15:39:59~16:39:59의 7개 시각은 `intraday=True`이고 오늘 행은 저장 목록에 없다.
- 16:40:00·16:41:00은 `intraday=False`이고 대조로 판정한다.
- 전날(11-18) 행은 모든 시각에 대조로 `confirmed`다.

## 7. 16:40 이후 확정 조건 검증 결과

`test_after_1640_time_alone_never_confirms`(16:40·16:41·18:00·23:30, 통과). **시각만으로는 확정하지 않는다.**

| 조건 | 결과 |
| --- | --- |
| 수집 값 ≠ 네이버(4,012.00 vs 4,000.12) | `mismatch`, 보류, 대조값 기록 |
| 네이버에 11-19 없음 | `unverified`, 보류 |
| 네이버를 읽지 못함 | 마지막 행 `unverified` 보류, 앞선 날은 `older_than_latest` |
| 허용 오차(0.005) 안 | `confirmed` |
| 수집된 행 없음 | 판정·저장 없음 |

기존 데이터 보호(`test_held_csat_row_is_never_written`, `SessionLocal`을 열면 실패하도록 막은 테스트):
- 11-19 16:00·16:35(장중 보류) 수집은 DB를 열지 않는다. 저장 0, `held_dates`에 `intraday`가 남는다.
- 16:41에 네이버와 다르면 `mismatch` 보류, 저장 0.
- 기존 통합 테스트 `test_held_row_keeps_existing_confirmed_value`(보류된 날은 기존 확정값 유지)도 격리 DB에서 통과했다(13절).

## 8. 데이터 신선도 판정 결과

`test_csat_session_and_expected_trading_day`(통과). 같은 시각에서 아래 6개 판정이 서로 같게 나온다:
- `krx_calendar.session_is_open`
- `briefing.session_is_open`
- `krx_calendar`·`outlook`·`data_monitor`의 기대 거래일
- `market_snapshot.cutoff_date`
- `briefing.drop_open_session`

| 11-19 시각 | 장중 | 기대 거래일 | 오늘 행 |
| --- | --- | --- | --- |
| 09:30·15:30·15:31·16:29:59 | True | 11-18 | 뺀다 |
| 16:30·16:39:59·16:40·20:30 | False | 11-19 | 쓴다 |

- 11-18 종가까지 있는 데이터의 지연(`test_csat_freshness_lag_counts_today_only_after_16_30`): 16:10에는 0, 16:30부터 1거래일이다. 변경 전에는 15:30부터 1이었다.
- 시세 지연 이유(`test_price_status_counts_refresh_runs_only_after_the_real_close`):
  - 변경 후 11-19 16:00 수집 기록은 "장 마감 뒤 수집"이 아니다(`not_collected`). 변경 전 기준(15:30)이면 장중 수집을 "휴장일 추정"으로 잘못 분류할 수 있었다.
  - 16:45 기록은 장 마감 뒤 수집이다.
  - 일반 거래일 16:00은 그대로 장 마감 뒤다.
- ETF 최신일(`test_etf_latest_row_is_intraday_until_16_30_on_csat`): 11-19 16:00·16:29에는 저장하지 않고, 16:30에는 저장한다. 일반 거래일 15:31 저장은 그대로다.
- 단순 시세 답 문장(`test_intraday_note_names_the_days_close`): 11-19는 "장 마감(16:30) 전", 일반 거래일은 "장 마감(15:30) 전"(기존 문장과 같다).

### 8.1 요청한 5가지 상태를 현재 구조로 구분할 수 있는가

| 상태 | 현재 구분 방법 | 구분 가능 여부 |
| --- | --- | --- |
| 1. 정규장 진행 중 | `session_is_open=True`(시장 요약 `freshness.intraday_excluded=true`) | 가능 |
| 2. 정규장 마감 직후 | `session_is_open=False`, 지수 판정 `intraday`(마감~+10분) | 내부 판정만 가능. 지수 보류 사유가 3과 같은 `intraday`다 |
| 3. 확정 검증 대기 | 지수 `intraday`(+10분 전) 또는 다음 실행 대기 | 2와 같은 값 |
| 4. 종가 확정 완료 | 지수 저장(`confirmed`·`older_than_latest`), 시장 요약 `freshness.status=latest` | 가능 |
| 5. 수집 지연·검증 실패 | 지수 `mismatch`·`unverified`(수집 로그 `held_dates`), 화면은 `delayed`·`stale` | 내부는 가능, 화면은 "지연"으로만 보인다 |

- 2·3은 `intraday` 하나로 묶인다. 화면(API)은 5의 원인(검증 실패인지 수집 안 함인지)을 따로 보이지 않는다.
- 이번에는 API 필드를 추가하지 않았다.
- 개선 제안(승인 필요):
  - 지수 보류 판정을 `intraday`(마감 전)와 `awaiting_confirmation`(마감~+10분)으로 나눈다. 내부 로그만이면 계약 변경이 없다.
  - 시장 요약에 보류 사유를 선택 필드로 둔다(계약 추가).

## 9. 일반 거래일 회귀 테스트 결과

| 테스트 | 결과 |
| --- | --- |
| 11-16·11-17·11-18·11-20(`test_regular_days_keep_15_30`) | 마감 15:30. 15:29:59 장중·15:30 마감, 기대 거래일 전환 15:30, 지수 보류 15:39:59까지·15:40부터 대조, 15:40·16:00 대조 확정 |
| 적용 범위 안 모든 거래일(`test_every_other_trading_day_in_coverage_keeps_15_30`) | 11-19를 뺀 전부 15:30 |
| 휴장일·주말(12-25, 11-21) | 장중 아님. 주말의 기대 거래일 11-20 |
| 등록되지 않은 날(12-31 미확정, 2027-01-04 범위 밖) | 15:30 규칙 |
| 다른 특수 거래일 | 등록된 것이 없다(`special_close == {2026-11-19}`) |
| 직전·다음 거래일 | 11-18 → 11-19 → 11-20 → 11-23, 11-19는 거래일 |
| 운영 스크립트 | `is_market_hours` 11-19 16:00·16:30 True, 16:31 False, 11-18 15:31 False. `backfill_history.period` 끝: 11-19 16:00 → 11-18, 16:30 → 11-19, 11-18 15:30 → 11-18 |
| 기존 테스트 | `test_refresh_data`(장중 09:00·15:30 True, 15:31 False), `test_code_answers`(문장 그대로), `test_index_confirm_round50`, `test_official_close_round44`, `test_event_reaction`, `test_data_quality_calendar`, `test_backfill_history` 모두 통과 |

## 10. 세션 캘린더와 기존 달력의 정합성

`test_session_schedule_and_calendar_agree_on_csat`(통과):

| 항목 | 기존 달력 | 세션 일정(54차, 분봉 시작 시각·양 끝 포함) |
| --- | --- | --- |
| KRX 정규장 마감 | `close_time(11-19)` = **16:30** | 종가 단일가 봉 `closing_at` = **16:30**, 지연 창 없음 |
| 정규 연속매매 | — | 10:00~16:20 봉 |
| 장후 종가매매 | — | 16:40~16:59 봉 |
| KRX 애프터마켓 | — | 17:00~19:59 봉(주식만, ETF 없음) |
| 상태 | — | `announced`(바꾸지 않음) |

- 두 파일의 역할은 섞지 않았다:
  - 달력은 거래일과 정규장 마감 판정용이다.
  - 일정은 분봉 세션 분류용이다.
  - 같은 날의 정규장 마감은 서로 같다(`calendar_mismatches() == []`).
- 일반 거래일: 달력 15:30 = 기본 일정 `closing_at` 15:30.
- 54차 테스트 2개는 "불일치가 있다"를 전제로 했으므로 고쳤다(`tests/test_session_schedule_54.py`).
  - 실제 파일은 불일치가 없다.
  - 달력에서 11-19를 빼면 불일치와 `calendar_close_mismatch` 표시가 다시 나온다(55차 테스트로도 확인).

## 11. 실행 중인 백엔드에 반영하기 위한 조치

- `krx_calendar.calendar()`는 `@lru_cache(maxsize=1)`로 **프로세스에서 한 번만** 읽는다. 실행 중인 서버는 JSON을 바꿔도 이전 달력(11-19 = 15:30)을 계속 쓴다.
- 코드 변경(9~14번 경로)도 import된 모듈이라 재시작 전에는 반영되지 않는다.
- 서버(PID 28088)는 `--reload` 없이 실행 중이다. 파일 감시로 바뀌지 않는다.
- **필요한 조치: 2026-11-19 09:00 전에 백엔드를 재시작한다**(사용자 승인 필요, 이번에 하지 않음). 재시작 뒤 확인할 것:
  - `python -c "from app import krx_calendar as K; from datetime import date; print(K.calendar().version, K.close_time(date(2026,11,19)))"` → `krx-2026-11-15 16:30:00`
  - 운영 보고서(`refresh_data --status`)의 `trading_calendar.version`
- `refresh_data`·`backfill_history`·`collect_market_index` 같은 스크립트는 실행할 때마다 새 프로세스라 별도 조치가 없다. 다음 실행부터 반영된다.
- 프런트엔드 변경은 없다. 화면은 API 응답(`intraday_excluded`·`expected_date` 등)을 그대로 보인다.

## 12. 개발 DB 변경 없음 확인

READ ONLY 연결(`transaction_read_only=on`)로 작업 전후 같은 값을 확인했다:

| 항목 | 작업 전 | 작업 후 |
| --- | --- | --- |
| Alembic | `c7e2a9f4d158` | `c7e2a9f4d158` |
| `stock_price` 행 수 / md5(`company_id·price_date·close_price`) | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` |
| `market_index` 행 수 / md5(`index_code·price_date·close_price`) | 1,480 / `3ba6fedca411ebff119bef854f227f43` | 1,480 / `3ba6fedca411ebff119bef854f227f43` |
| 지수 최신일 | KS11·KQ11 2026-11-12 | KS11·KQ11 2026-11-12 |
| `stock_price_session` | 없음 | 없음 |
| `users` | 2 | 2 |

- md5는 이번에 정한 열 조합이다. 54차 md5(다른 열 조합)와 직접 비교하는 값이 아니다. 행 수는 54차와 같다.
- 개발 DB에 한 일: READ ONLY 조회, 격리 DB를 만들기 위한 `pg_dump`(읽기) 1번.
- 처음 조회 1번은 연결 뒤 `SET SESSION CHARACTERISTICS`를 써서 그 트랜잭션이 READ ONLY가 아니었다(`transaction_read_only=off`). SELECT만 실행했다. 그 뒤에는 연결 속성으로 READ ONLY를 걸어 다시 확인했다.

## 13. 테스트와 CI 결과

| 테스트 | 결과 |
| --- | --- |
| `tests/test_krx_special_close_55.py`(신규) | **48 통과**(매개변수 포함). 달력 JSON·로더 검증, 지수 경계·확정 조건·기존 값 보호, 장중·신선도·ETF·뉴스·안내 문장·스크립트, 일반 거래일 회귀, 세션 일정 정합성 |
| `tests/test_session_schedule_54.py`(2개 단언 수정) | 통과 |
| 단위 전체(`-m "not slow and not integration"`, CI처럼 `DATABASE_URL`을 닫힌 포트로) | **6,174 통과**(54차 6,126 + 신규 48), 실패 0 |
| 통합 전체(`-m "integration and not slow"`, 격리 DB `ai_investment_special_close55_it`) | **697 통과, 10 건너뜀**, 실패 0. 건너뜀 9 = 마이그레이션 왕복(`test_auth_revoke_devdb`, `MIGRATION_TEST_DATABASE_URL` 없음), 1 = `test_outlook_round25_devdb`(기준일 2026-11-03 DB 아님, 54차와 같음). 합계 707 = 54차(706+1)와 같다 |
| CI | push 뒤 GitHub Actions 결과를 최종 보고에 적는다 |

격리 DB:
- `ai_investment_special_close55_it`(새로 만듦). 만들기 전 같은 이름이 없음을 확인했다.
- 개발 DB `pg_dump -Fc` → `pg_restore`로 만들었다. 복원 직후 `c7e2a9f4d158`, `stock_price` 251,455행, `market_index` 1,480행, 세션 표 없음.
- 통합 테스트 시험 데이터가 들어 있다. 지우지 않고 남긴다.
- `MIGRATION_TEST_DATABASE_URL`은 주지 않았다(임시 컨테이너를 띄우지 않음). 마이그레이션 왕복 테스트는 건너뛰었다(이번 변경과 무관, CI에서 실행).
- 처음 한 번은 비밀번호를 읽지 못한 채 실행돼(시스템 Python에 `dotenv` 없음) 연결 실패 10건으로 끝났다. 같은 격리 DB를 가리켰고 인증에 실패했으므로 아무것도 쓰지 않았다. 다시 실행한 결과가 위 표다.
- 기존 시험 DB(`ai_investment_session_design53`·`_design53_it`·`_pipeline54`·`_pipeline54_it`·`ai_investment_verify`·`ai_investment_postcheck47`)는 쓰지 않았고 지우지 않았다.

## 14. 남은 위험

1. **실행 중인 서버는 재시작 전까지 11-19를 15:30 마감으로 본다**(11절). 11-19 장 시작 전 재시작이 필요하다(승인 필요).
2. **근거 수준**: 한국거래소 공지 원문은 확인하지 못했다(증권사 공지·보도). 거래소가 일정을 바꾸면 달력과 세션 일정을 함께 고쳐야 한다.
3. **개장 시각은 달력에 없다.** 달력은 마감만 담는다(`special_sessions_note`: "마감 시각만 최신성 계산에 쓴다").
   - 11-19 09:00~10:00은 장중(`session_is_open=True`)으로 본다. 오늘 행을 쓰지 않는 보수적인 쪽이라 잘못 확정하지 않는다.
   - `refresh_data` 장중 경고도 09:00부터 낸다.
4. **환율 기대일(`data_monitor.expected_weekday_close`)은 15:30 평일 규칙 그대로다.**
   - 환율은 KRX 달력이 아닌 평일 정책이라 이번 범위에서 뺐다.
   - 영향: 11-19 15:30~16:30에 환율 오늘 값을 기대해 "1일 지연"으로 보일 수 있다(운영 보고서만, 저장값 영향 없음).
5. **문서의 "15:30" 서술.**
   - `backend/API.md`의 장중 규칙·뉴스 반응 정의 등은 "평일 15:30"으로 적혀 있다. 계약 문서라 바꾸지 않았다.
   - 실제 동작은 "그날 KRX 정규장 마감(평소 15:30, 수능일 16:30)"이다. 프런트엔드 저장소의 문서 사본도 같다.
   - 문구 정리는 별도 승인으로 한다.
6. **평가 코드 `evals/forecast_check.py`의 15:30 상수**(`_MARKET_CLOSE_MIN`): 평가 판정용이다. 평가 세트를 바꾸지 않는 조건이라 그대로 두었다. 11-19 장중에 평가를 돌리면 15:30~16:30 판정이 서비스와 다를 수 있다.
7. **상태 구분 부족**(8.1절): 마감 직후와 확정 대기가 같은 `intraday`다. 화면에서 검증 실패와 수집 안 함을 구분할 수 없다.
8. **`refresh_data` 경고 문구**는 "평일 09:00~15:30"으로 고정돼 있다. 판정은 16:30까지 하지만, 11-19 16:00에 실행하면 경고 문장의 시각이 실제와 다르다(동작 영향 없음).
9. **앞으로의 특수 마감일**(2027 연초 개장일 등)은 달력이 2026-12-31까지라 등록돼 있지 않다. 2027 달력을 갱신할 때 함께 넣어야 한다.
10. 지수 확정은 네이버(비공식)와의 대조다. 11-19에 네이버가 16:30 뒤 확정 종가를 보이는지는 관찰 전이다(50차는 일반 거래일 15:30 뒤 관찰).

## 15. 11-19 실제 데이터 확보 뒤 확인할 것

장중·마감 판정(서버 재시작 뒤):
1. 11-19 15:30~16:30에 시장 요약 `freshness.intraday_excluded=true`, `expected_date=2026-11-18`인지 본다. 16:30 뒤에는 `expected_date=2026-11-19`인지 본다.
2. 16:40 뒤 지수 수집 로그:
   - `저장 보류 … (intraday)`가 사라지는가
   - 네이버 대조로 `confirmed`인가, 아니면 `mismatch`·`unverified` 보류인가
3. 저장된 11-19 지수가 네이버 일별 확정 종가와 같은지, 다음 날 다시 대조한다.
4. 네이버가 그날 확정 종가를 언제부터 보이는지 관찰해 기록한다(위험 10).

분봉 세션(54차 17절 그대로):
5. 52차 절차로 11-19 분봉을 보존한다(20:10 KST 뒤).
6. `load_session_minute --validate-only --date 2026-11-19`로 확인한다:
   - 첫 봉 10:00, 09시대 봉 0
   - 16:21~16:29 봉 0
   - 16:30 단일가 봉
   - 16:31~16:39 봉 수
   - 16:40~16:59 장후 종가매매
   - 17:00 전 애프터마켓 봉 0
   - ETF 17시 뒤 봉 0
7. `stock_price` 수집 뒤 공식 종가 대조가 맞으면, 세션 일정 상태를 `data_verified`로 올리는 커밋을 따로 한다. **그 전까지 `announced` 유지.**
8. 실제 거래가 일정과 다르면 달력(`close`)과 세션 일정을 함께 고친다. `calendar_mismatches()`와 이 문서의 테스트로 둘이 같은지 확인한다.

## 16. 커밋과 push

- 커밋 대상:
  - `backend/app/data/krx_calendar.json`
  - `backend/app/krx_calendar.py`
  - `backend/app/outlook.py`·`official_close.py`·`event_reaction.py`·`code_answers.py`
  - `backend/scripts/refresh_data.py`·`backfill_history.py`
  - `backend/tests/test_krx_special_close_55.py`·`test_session_schedule_54.py`
  - `docs/DATA_QUALITY_AND_TRADING_CALENDAR.md`, 이 문서
- 커밋 전 확인:
  - JSON 날짜 항목 1개
  - 기존 휴장일·마감 그대로
  - 마이그레이션 `alembic/pending/0f6d0ec52ac3_add_stock_price_session.py` 그대로(`alembic/versions` 변경 없음)
  - 대용량 백업 미포함
  - 개발 DB 리비전 그대로
- 바꾸지 않은 것: `krx_session_schedule.json`, `API.md`, `alembic/`, 프런트엔드, 평가 세트, Ollama 설정, 보존 분봉
- 커밋 해시·push·CI 결과는 최종 보고에 적는다(이 문서를 담은 커밋이라 여기에 쓸 수 없다). main 병합은 하지 않는다.
