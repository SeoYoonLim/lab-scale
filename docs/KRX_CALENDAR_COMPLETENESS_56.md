# 56차 거래시간 달력 적용 범위 마무리와 장 상태 판정 개선 (2026-11-15)

**개발 DB(`ai_investment`)는 바꾸지 않았다.** 마이그레이션·적재·재수집을 하지 않았고 운영 프로세스도 건드리지 않았다. 대기 마이그레이션 `0f6d0ec52ac3`은 `backend/alembic/pending/`에 그대로 있다.
**공개 API 응답의 필드와 값은 바뀌지 않았다.** 프런트엔드 변경도 없다.
**2026-11-19 실제 데이터는 아직 없다.** 11-19 결과는 시각을 주입한 단위 테스트다. 세션 일정(`krx_session_schedule.json`)의 11-19 상태는 `announced` 그대로다.

## 1. 시작 브랜치와 커밋

| 항목 | 값 |
| --- | --- |
| 브랜치 | `feature/backend`, `origin/feature/backend`와 같은 커밋, 작업 트리 깨끗함 |
| 기준 커밋 | `bd444a7`(55차) |
| 확인한 것 | `docs/KRX_SPECIAL_CLOSE_ALIGNMENT_55.md`, `krx_calendar.json`·`app/krx_calendar.py`, 55차 변경 코드·테스트, `krx_session_schedule.json`(54차), 52차 보존 도구 |
| 실행 중인 백엔드 | PID 28088(부모 25744), `127.0.0.1:8000`, 2026-11-15 15:12 시작, `--reload` 없음. 건드리지 않았다. 5173(프런트엔드)은 떠 있지 않았다 |

## 2. 남아 있던 시각 참조와 사용처

`15:30`·`15:40`·`16:30`·`09:00`·`(15, 30)`·`15 * 60`·`intraday`·`expected_trading_day`를 `app`·`scripts`·`evals`에서 찾았다. 각 참조가 어느 시장의 어떤 시각인지 분류했다.

| 위치 | 뜻·시장 | 55차 뒤 상태 | 56차 조치 |
| --- | --- | --- | --- |
| `app/krx_calendar.py` | KRX 정규장 개장·마감 | 마감만 있었음 | **개장 시각 추가**(6절) |
| `app/index_confirm.py` | KRX 지수 확정(마감 + 10분) | `intraday` 하나로 보류 | **보류 사유 분리**(8절), 개장 시각 사용 |
| `scripts/refresh_data.is_market_hours` | KRX 장중 경고 | 시작 09:00 고정 | **`open_time`으로 시작**(11-19 10:00) |
| `app/session_ohlcv.py` `SESSIONS` | 50차 감사용 분봉 세션 창(09:00~15:30 고정) | 11-19 분봉을 잘못 나눌 수 있음 | **특수 거래일은 판정 거부**(`special_session_day`) |
| `app/data_monitor.py` `MARKET_CLOSE`·`expected_weekday_close` | **환율** 평일 정책 | 15:30 평일 | 바꾸지 않음. 뜻을 주석으로 밝힘(3절) |
| `evals/forecast_check.py` | 평가기(24차 데이터 후속 답 정답 계산) | 15:30 고정 | **달력 파일의 그날 마감**(4절) |
| `evals/run_quality.py` `_visible_rows` | 평가기 장중 행 제외 | 15:30 고정 | **달력 파일의 그날 마감**(4절) |
| `evals/quality_judges.py` `expected_trading_day` | 평가기 기대 거래일 | 15:30 고정 | **달력 파일의 그날 마감**(4절) |
| `backend/API.md` 17곳 | 문서 | "평일 15:30" | **"그날 정규장 마감"으로 정리**(5절) |
| `app/briefing.py` `MARKET_CLOSE`, `app/official_close.py` `REGULAR_CLOSE`, `app/event_reaction.py` `MARKET_CLOSE`, `scripts/backfill_history.py` `MARKET_CLOSE` | 정규 일정 마감 값 | 55차부터 판정에 쓰지 않음(이름만 남음) | 그대로(다른 모듈이 import할 수 있는 상수) |
| `app/realtime_price.py`(NXT 15:40~, KRX 16:00~20:00) | 애프터마켓 설명 주석 | — | 그대로(판정 없음) |
| `app/session_bars.py`·`session_schedule.py` | 54차 분봉 세션 일정 | 일정 파일 기반 | 그대로 |
| `app/data_monitor.py` 수집 권장 시각 "거래일 16:30 KST 이후 1회" | 운영 권장 실행 시각(마감 1시간 뒤) | — | 그대로. 11-19는 마감 시각과 겹친다(14절 위험 4) |
| `app/display_dates.py`·`app/api/stocks.py`·`scripts/export_api_types.py`의 `+09:00`·`00:00~09:00` | 시간대 표기 | — | 해당 없음 |
| API.md 5060·5061행(50차 "이전 동작" 표), 3315행(43차 기록) | 과거 기록 | — | 그대로(그때의 사실) |

## 3. 환율 기대일 판정 조사 결과와 조치

### 3.1 조사(네트워크 요청 없음. 설치된 라이브러리 코드와 개발 DB READ ONLY 조회)

| 질문 | 결과 |
| --- | --- |
| 1. 공급자·갱신 | `fetch_and_save_exchange_rate`는 FinanceDataReader `DataReader("USD/KRW")`다. FDR 0.9.202 코드상 `USD/KRW`는 Yahoo `KRW=X` 일봉(`interval=1d`)으로 간다. 24시간 글로벌 외환 시세이고, 서울 외환시장(서울외국환중개) 값이 아니다. 갱신 시각을 원천이 공지하지 않는다 **[미확인]** |
| 2. 기준일 정의 | 저장 `price_date` = 원천 일봉 날짜. 개발 DB 153행(2026-04-02~11-02)의 요일 분포: 2026-04~10-24는 **일~목**(30개 일요일 행), 10-26부터 **월~금**. 영국 서머타임 종료(10-25)와 맞물려 날짜가 하루 이르게 붙던 것으로 보인다 **[추정]**. 따라서 날짜가 한국 거래일과 1:1로 대응하지 않는다 |
| 3. 주말·공휴일 | 주말 날짜 행 있음(일요일, 서머타임 기간). 한국 KRX 휴장일 10일 중 **6일에 값이 있다**(05-05·05-25·06-03·08-17·09-24·10-05), 4일은 없다(05-01·07-17·09-25·10-09) |
| 4. KRX 달력을 쓰는 곳 | 운영 보고서(`data_monitor`)의 환율 기대일은 KRX 달력을 **쓰지 않는다**(`fx_weekday_close`: 평일, 15:30 기준). 브리핑·일일 브리핑 화면은 환율 시계열에도 `drop_open_session`(KRX 달력)을 쓴다. 시세·지수와 같은 기준일로 보이려는 것이다(36차부터) |
| 5. KRX 달력 변경의 영향 | 운영 보고서 기대일: **영향 없음**(11-19 16:00에도 11-19 기대). 브리핑 화면: 11-19 15:30~16:30에 환율 오늘 행을 빼는 구간이 1시간 늘어난다(55차부터). 시세·지수와 같은 날짜를 보이므로 화면 일관성은 유지된다 |

### 3.2 조치

- **KRX 16:30을 환율에 적용하지 않았다.** 환율 원천은 KRX와 거래시간·휴장일·날짜 정의가 모두 다르다. 그래서 환율 기준을 KRX 달력으로 옮기면 오히려 틀린다.
- 코드 동작 변경은 없다. `data_monitor.MARKET_CLOSE`와 `expected_weekday_close`에 주석을 달았다: "환율 평일 정책 전용, KRX 정규장 마감이 아님, 특수 마감 미적용"과 근거.
- `app/krx_calendar.py` 모듈 설명에 환율과의 관계를 적었다(운영 보고서는 평일 정책, 브리핑 화면은 표시 일관성을 위해 장중 규칙).
- 테스트(`test_fx_expected_day_*`, `test_briefing_shows_fx_*`):
  - 11-19 16:00: 환율 기대 11-19, 주식 기대 11-18
  - 11-19 15:29: 환율 기대 11-18
  - 12-25(KRX 휴장, 금): 환율 기대 12-25, 주식 기대 12-24
  - 일반 거래일 4일: 15:29 직전 평일, 15:30 그날(변경 전과 같음)
- 개선안(근거 부족, 승인 필요):
  - 환율 날짜 정의를 정한다(원천 날짜를 그대로 쓸지, 한국 날짜로 바꿀지).
  - 서울 외환시장 종가가 필요하면 원천을 바꾼다(한국은행 ECOS 등). 이것은 데이터 교정 작업이라 이번 범위가 아니다.

## 4. `forecast_check.py` 개선 결과

| 구분 | 판단 |
| --- | --- |
| 용도 | 24차 데이터 후속 답의 "시세 기준일·지연 분류"를 제품과 따로 계산하는 평가기 정답(`_before_close24`·`_expected_day24`·`status24`) |
| 과거 결과 재현용 고정 시각인가 | 아니다. 답변에 적힌 생성 시각(`gen`)을 받아 그때의 기대 거래일을 계산한다. 시각 자체는 입력이다 |
| 특수 거래일 반영 필요 | 있다. 11-19 15:30~16:30에 만든 답이라면 제품은 11-18을 기대하는데 평가기는 11-19로 계산해, 맞는 답을 틀렸다고 판정한다 |

조치:
- 평가기는 제품 코드를 import하지 않는다(36차 원칙: 정답 계산은 독립 구현, 달력 데이터는 하나).
- `evals/quality_judges.krx_close_hhmm(d)`를 새로 두었다. `krx_calendar.json`의 `regular_close`·`special_sessions`를 직접 읽는다.
- 세 평가기가 이 함수를 쓴다:
  - `forecast_check._before_close24`·`status24`: SQL의 `time '15:30'` → 그날 마감 매개변수
  - `run_quality._visible_rows`
  - `quality_judges.expected_trading_day`
- 평가 세트 v1·v2의 질문·정답·기대값 파일은 바꾸지 않았다. 테스트 시각도 조정하지 않았다.
- 검증:
  - 달력 범위 안 모든 거래일에서 평가기 마감 = 제품 `close_time`
  - `quality_judges`가 `app` 모듈을 import하지 않음(AST 검사)
  - 11-19 16:00 → 11-18, 16:30 → 11-19
  - 일반 거래일 15:29 → 전날, 15:30 → 그날
  - 기존 평가기 테스트(`test_evals_quality`·`test_evals_forecast_check`) 통과
- **남은 차이(바꾸지 않음)**: `forecast_check._expected_day24`는 24차 기준 그대로 KRX 휴장일을 보지 않는다(평일만). 제품은 36차부터 휴장일을 본다. 그래서 휴장일 다음 날 오전 같은 경우 평가기와 제품의 기대일이 다를 수 있다. 이것은 평가 결과를 바꾸는 변경이라 따로 결정해야 한다(14절 위험 5).

## 5. API.md 문서 수정 결과

- "엔드포인트 요약" 앞에 **"거래시간 기준: 정규장 개장·마감 (2026-11-15)"** 절을 추가했다. 담은 내용:
  - 일반 거래일 09:00~15:30
  - 2026-11-19 10:00~16:30
  - 그 밖의 특수 등록일은 그날 값
  - 16:30은 고정 마감이 아님
  - `close`는 정규장 마감(시간외·애프터마켓·NXT 아님)
  - 장중 규칙은 장 시작 전 포함
  - 지수는 마감 + 10분부터 확정 "시도", 네이버 대조를 통과해야 저장
  - 보류 사유 이름(수집 출력용)
  - 환율은 평일 기준
  - 달력 변경 뒤 서버 재시작 필요
- "평일 15:30"이라고 적힌 17곳을 "그날 정규장 마감(평소 15:30, 특수 거래일은 달력 값)"으로 바꾸고 위 절에 연결했다.
  - 대상: 기대 거래일, 장중 규칙 4곳, 단순 시세 안내 문장(`장 마감(HH:MM)`), 뉴스 반응 기준일, `freshness.intraday_excluded` 설명(장 시작 전 포함을 명시), 수집 권장 시각, 공식 종가 정의, 시장 요약 확정 대기 임시 판단("마감 + 10분(평소 15:40, 11-19 16:40)~23:30") 등
- 응답 필드·형식·예시는 바꾸지 않았다. `docs/frontend-api-types.ts`도 그대로다.
- 프런트엔드 저장소의 `API.md` 사본은 수정 금지 범위라 바꾸지 않았다. 다음 동기화 때 반영하면 된다.

## 6. 개장 시각 달력 구조와 호환성

`app/data/krx_calendar.json`:

| 키 | 변경 |
| --- | --- |
| `regular_open` | **추가** `"09:00"`(최상위, `regular_close` 옆) |
| `special_sessions[2026-11-19]` | `"open": "10:00"` **추가**(close 16:30 그대로) |
| `special_sessions_note` | `open` 설명 추가 |
| `version` | `krx-2026-11-15` → `krx-2026-11-15.2` |
| 휴장일·미확정일·적용 범위·`regular_close` | 변경 없음 |

일반 거래일 항목은 하나도 바꾸지 않았다(일반 거래일은 기본값을 쓴다).

`app/krx_calendar.py`:
- 조회 함수:
  - `open_time(d)`(신규): 특수일 `open`, 없으면 `regular_open`(09:00)
  - `close_time(d)`(기존)
  - 시각은 이 두 함수에서만 읽는다
- `market_phase(now)`(신규, 내부용):
  - 값: `non_trading_day`·`pre_market`·`intraday`·`closed`
  - 달력만으로 정한다. "확정"은 정하지 않는다.
- `session_is_open(now)`는 **뜻을 바꾸지 않았다**: "마감 전(장 시작 전 포함)".
  - 공개 API `freshness.intraday_excluded`와 일일 브리핑 `intraday`가 이 값이다.
  - 장 시작 전에 False로 바꾸면 모든 일반 거래일 00:00~09:00의 응답 값이 바뀐다. 그래서 바꾸지 않았다.
- `Calendar`의 새 필드(`regular_open`·`special_open`)는 기본값이 있고 맨 뒤에 있다. 55차까지의 9개 위치 인자 생성과 `Calendar(**...)` 호출이 그대로 동작한다(테스트).
- 읽을 때 거부하는 경우(신규·기존):
  - `open` 형식 오류: `10:0`, 숫자, `10:00:00`
  - 그날 개장 >= 마감: `open`이 없으면 09:00과 비교
  - `regular_open`·`regular_close` 형식 오류, `regular_open >= regular_close`
  - 55차 규칙 유지: 중복 날짜, 주말·휴장일, 범위 밖, `close` 형식
- `open`이 없는 특수일은 09:00 개장이다. `regular_open`이 없는 파일도 09:00으로 읽는다.
- `info()`(운영 보고서 형식)는 키를 늘리지 않았다.

## 7. 11월 19일 개장 시각 판정 테스트

`tests/test_krx_calendar_completeness_56.py`(시각 주입). 결과는 모두 기대와 같다.

| 11-19 KST | `market_phase` | 지수 오늘 행 판정 | `refresh_data` 장중 경고 | `session_is_open` |
| --- | --- | --- | --- | --- |
| 08:59:59 | `pre_market` | `pre_market`(보류) | 없음(08:59) | True |
| 09:00:00 | `pre_market` | `pre_market`(보류) | **없음**(55차: 있음) | True |
| 09:59:59 | `pre_market` | `pre_market`(보류) | 없음(09:59) | True |
| 10:00:00 | `intraday` | `intraday`(보류) | 있음 | True |
| 16:29:59 | `intraday` | `intraday`(보류) | 있음 | True |
| 16:30:00 | `closed` | `confirmation_pending`(보류) | 있음(마감 분 포함, 50차 규칙) | False |
| 16:39:59 | `closed` | `confirmation_pending`(보류) | 없음(16:31부터) | False |
| 16:40:00 | `closed` | 대조 시도 → 네이버와 같으면 `confirmed` | 없음 | False |

- 기대 거래일: 09:30에도 11-18이다(장 시작 전도 오늘 종가 없음).
- 50차 감사 모듈 `session_ohlcv.compare_day`는 11-19를 `special_session_day`로 판정하지 않는다. 일반 거래일 판정은 그대로다.

## 8. 장 마감 및 확정 대기 상태 구분

기존 상태 정의를 먼저 확인했다:
- 공개 API: 시장 요약 `freshness.status` = `latest`·`delayed`·`stale`·`no_data`, `freshness.intraday_excluded`(bool), 일일 브리핑 `intraday`(bool), 시세 상태 `price_status` 5종
- 내부: 지수 확정 판정 `confirmed`·`older_than_latest`·`intraday`·`mismatch`·`unverified`. 수집 출력(`collect_market_index`의 "저장 보류 …(상태)")에만 보이고 DB·API에는 없다.

요청한 5단계와의 대응(내부):

| 요청 이름 | 구현 | 어디서 |
| --- | --- | --- |
| `pre_market` | 달력 `market_phase` = `pre_market`, 지수 판정 `pre_market`(신규) | `krx_calendar`, `index_confirm` |
| `intraday` | `market_phase` = `intraday`, 지수 판정 `intraday`(개장~마감 전으로 좁힘) | 같음 |
| `confirmation_pending` | 지수 판정 `confirmation_pending`(신규, 마감~마감 + 10분) | `index_confirm` |
| `confirmed` | 지수 판정 `confirmed`: **네이버 일별과 값이 같을 때만**. 시각 조건만으로는 나오지 않는다 | `index_confirm` |
| `delayed` | 지수 판정 `mismatch`·`unverified`(보류, 기존 값 유지). 화면은 기존 `freshness.status=delayed` | `index_confirm`, `market_snapshot` |

- `intraday(day, now)`(50차 함수)의 뜻은 그대로다: 시각 때문에 보류하면 True. 새 함수 `time_status`가 사유를 돌려준다.
- 보류 목록 `HELD`에 `pre_market`·`confirmation_pending`을 넣었다. 저장 경로는 같다: 보류한 날은 DB를 열지 않거나 쓰지 않는다.
  - 테스트: 09:30·12:00·16:31 수집 모두 저장 0, 사유별 `held_dates`
- `intraday` 문자열을 쓰는 호출부 확인:
  - `index_confirm` 상태값은 `collect_market_index` 출력에만 나온다.
  - `daily_briefing`의 `intraday`는 `session_is_open` 값이다(바꾸지 않음).
  - `app/api/market.py`의 `intraday_excluded`도 `session_is_open` 값이다(바꾸지 않음).

## 9. 공개 API 호환성

- 응답 필드 추가·삭제·이름 변경 없음. 값의 종류도 그대로다(`freshness.status` 4종, bool 2개).
- 값이 달라지는 경우는 2026-11-19(달력에 특수 시각이 있는 날)뿐이다(55차와 같음). 시각대별로:
  - 15:30~16:30: 장중으로 판정
  - 16:30부터: 기대 거래일이 그날
  - 개장 시각은 공개 API 값에 영향이 없다(`session_is_open`이 장 시작 전도 True라서)
- 확인 테스트:
  - `test_market_summary_and_daily_briefing_agree`: 11-16~11-21, 12-25 × 10개 시각에서 기대 거래일·장중 판정이 시장 요약·일일 브리핑·운영 보고서에서 모두 같다
  - `test_public_status_values_unchanged`: 11-19 09:30 시장 요약 `intraday_excluded=true`, 기대일 11-18
- 프런트엔드 영향: 없음. 화면에 장 단계를 따로 보이고 싶다면 필드 추가가 필요하다. 제안만 남긴다:
  - `freshness.market_phase`(선택, 기존 필드 유지)
  - 지수 보류 사유 `freshness.index_hold_reason`
  - 둘 다 계약 추가라 프런트엔드 합의 뒤에 넣는다

## 10. 일반 거래일 회귀 테스트

| 확인 | 결과 |
| --- | --- |
| 11-16·11-17·11-18·11-20 개장·마감 | 08:59:59 `pre_market`, 09:00 `intraday`, 15:29:59 `intraday`, 15:30 마감 도달, 15:40 대조 확정 |
| 지수 판정 | 08:59:59 `pre_market`, 09:00·15:29:59 `intraday`, 15:30·15:39:59 `confirmation_pending`, 15:40 `confirmed`(네이버 같음) |
| 지수 대조 실패 | `mismatch`·`unverified`, 저장 0, 기존 값 유지(통합 테스트 `test_held_row_keeps_existing_confirmed_value` 포함) |
| 기대 거래일 | 55차 테스트 그대로 통과(15:30 전환) |
| `refresh_data` 장중 경고 | 08:59 False, 09:00·15:30 True, 15:31 False(기존 `test_refresh_data` 그대로) |
| 적용 범위 모든 거래일 | 11-19를 빼면 (09:00, 15:30) |
| 휴장일·주말 | 11-21·11-22·12-25 `non_trading_day`, 장중 아님 |
| 미확정일(12-31)·범위 밖 | 기본 시각 |
| 환율 | 3.2절 |

## 11. 개발 DB 불변 확인

READ ONLY 연결(`transaction_read_only=on`), 같은 계산식(55차 12절):

| 항목 | 작업 전 | 작업 후 |
| --- | --- | --- |
| Alembic | `c7e2a9f4d158` | `c7e2a9f4d158` |
| `stock_price` 행 수 / md5 | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` |
| `market_index` 행 수 / md5 | 1,480 / `3ba6fedca411ebff119bef854f227f43` | 1,480 / `3ba6fedca411ebff119bef854f227f43` |
| `stock_price_session` | 없음 | 없음 |
| `users` | 2 | 2 |

- 개발 DB에 한 일: READ ONLY 조회(환율 날짜 분포, 수집 기록), 격리 DB를 만들기 위한 `pg_dump`(읽기) 1번.
- 환율 조회 중 없는 열 이름(`updated_at`, 격리 기록 표의 `created_at`)으로 오류가 2번 났다. 모두 SELECT였고 읽기 전용 연결이었다.

## 12. 실행 중인 서버 재시작 필요 여부

- **필요하다.** PID 28088은 15:12에 시작했다. 55차 커밋보다 먼저 시작했으므로 55·56차 변경이 모두 반영되지 않았다.
  - 달력은 `lru_cache`라 한 번만 읽는다.
  - 코드도 import된 상태다.
  - `--reload`가 없다.
- 지금 서버는 11-19를 09:00~15:30 거래일로 본다.
- 재시작은 **사용자 승인 사항**으로 남긴다. 11-19(목) 09:00 전이 기한이다.
- 재시작 뒤 확인 방법(쓰기 없음):
  1. `cd backend && .venv/Scripts/python.exe -c "from datetime import date; from app import krx_calendar as K; d=date(2026,11,19); print(K.calendar().version, K.open_time(d), K.close_time(d))"` → `krx-2026-11-15.2 10:00:00 16:30:00`(파일 확인용)
  2. 서버 프로세스 시작 시각이 재시작 시각인지 본다: `Get-CimInstance Win32_Process | ? CommandLine -match uvicorn`
  3. 11-19 15:30~16:30에 `GET /api/market/overview`의 `freshness.intraday_excluded=true`, `expected_date=2026-11-18`인지 본다. 16:30 뒤에는 `expected_date=2026-11-19`인지 본다.
- 스크립트(`refresh_data`·`collect_market_index`·`backfill_history`)는 실행마다 새 프로세스라 다음 실행부터 반영된다.

## 13. 11월 19일 실제 데이터 확보 후 남은 검증

52차 도구를 다시 확인했다(실행하지 않음):
- `fetch_session_minute`·`preserve_session_minute`는 세션 시각을 쓰지 않는다.
  - 거래일 여부만 KRX 달력으로 본다.
  - 완료 기준은 20:10 고정이다.
  - 11-19도 애프터마켓이 20:00에 끝나므로 20:10이 맞다.
- 이번 변경이 이 도구에 주는 영향은 없다. `backups/session_minute/`는 아직 없다(11-16 분봉은 11-16 밤에 생긴다).

### 13.1 11-16·11-17·11-18 매일 보존(일반 거래일)

각 날짜 `D`에 대해, 그날 **20:10 KST 이후** `backend` 폴더에서 52차 문서 2.2절 1)~5)를 실행한다.
- 날짜 변수 `D`만 바꾼다. 종목 목록은 고정 목록 `backups/session_audit_2026-11-06_2026-11-13_snapshot_20261114`를 쓴다.
- 받기 1)~2) → 보존 3)~4) → 독립 재검증 5)와 `sha256sum -c` 순서다.
- 완료 판정은 52차 2.4절 6항목이다(받음 350·실패 0·봉 없음 0, 보존 `결과: ok`, 재검증 351/351, `sha256sum OK`).
- 체크섬 목록은 `docs/session_minute/$D.sha256`로 저장소에 남긴다(LF 유지: `.gitattributes`의 `*.sha256 text eol=lf`).
- 원천은 약 7거래일만 보관한다. 11-16은 11-24~25경 사라지므로 늦어도 거래일 뒤 3거래일 안에 한다.
- 받은 날 바로 54차 `load_session_minute --validate-only --date $D --source backups/session_minute/$D`로 거부 목록을 확인해 둔다(DB 쓰기 없음).

### 13.2 11-19(수능일)

1. **20:10 KST 이후** 위와 같은 1)~5). 받기는 20:10 전이면 거부한다.
2. 52차 2.4절 7번 확인 항목은 이날 값이 다르다. 첫 봉은 `20261119090000`이 아니라 **`20261119100000`** 근처여야 한다. 마지막 봉은 `2026111919…`다.
3. 54차 `load_session_minute --validate-only --date 2026-11-19`로 확인한다:
   - 09시대 봉 0
   - 첫 봉 10:00(일부 10:0x)
   - 16:21~16:29 봉 0
   - **16:30 봉 = 종가 단일가**(시가=고가=저가=종가)
   - 16:31~16:39 봉 수(지연 종가 단일가 여부. 이날은 지연 창이 없어 있으면 그 종목-날짜를 거부한다)
4. `post_close`와 `after_market` 구분:
   - 16:40~16:59 봉이 모두 종가 단일가 가격(장후 종가매매)인가
   - 17:00 전 애프터마켓 봉이 0인가
   - 17:00~19:59 봉이 주식에만 있는가(ETF 17시 뒤 봉 0)
   - 두 세션의 거래량이 합쳐지지 않았는가(집계 결과 행이 따로 있음)
5. `stock_price`가 수집된 뒤(다음 거래일 기준가 확인 뒤) 공식 종가 대조:
   - 16:30 봉 종가 = 공식 종가인가
   - 지수: 16:40 뒤 수집 출력에서 `confirmation_pending`이 사라지고 네이버 대조로 `confirmed`가 되는가. `mismatch`·`unverified`면 다음 실행에서 다시 본다.
6. 세션 일정 `announced` → `data_verified` 조건(모두 만족해야 한다):
   - 보존 완료(52차 2.4절 6항목 + 2번)
   - 3·4번이 일정과 같음(거부가 284430 같은 기존 원인 말고 없음)
   - 공식 종가 대조 일치
   - 별도 커밋으로 상태만 바꾸고 근거(관찰 수치)를 적는다
7. 일정과 다르면 달력(`open`·`close`)과 세션 일정을 함께 고친다. `calendar_mismatches()`와 55·56차 테스트로 둘이 같은지 확인한다. 원본은 보존돼 있으므로 다시 처리할 수 있다.

## 14. 테스트와 CI 결과

| 테스트 | 결과 |
| --- | --- |
| `tests/test_krx_calendar_completeness_56.py`(신규) | **163 통과**(매개변수 포함): 달력 구조·호환성·거부, 장 단계, 지수 보류 사유, 운영 스크립트·50차 모듈, 환율 분리, 기준일 일치, 공개 값, 평가기 |
| `tests/test_krx_special_close_55.py` | 통과. 의도한 변경에 맞춰 3곳 고침: 특수일 항목에 `open`, 16:30·16:39:59의 지수 판정 `intraday` → `confirmation_pending`, 16:35 수집 보류 사유 |
| 단위 전체(`-m "not slow and not integration"`, `DATABASE_URL` 닫힌 포트) | **6,337 통과**(55차 6,174 + 신규 163), 실패 0 |
| 통합 전체(`-m "integration and not slow"`, 격리 DB `ai_investment_calendar56_it`) | **697 통과, 10 건너뜀**, 실패 0(건너뜀 9 = 마이그레이션 왕복 `MIGRATION_TEST_DATABASE_URL` 없음, 1 = `test_outlook_round25_devdb` 데이터 조건. 55차와 같다) |
| CI | push 뒤 결과를 최종 보고에 적는다 |

격리 DB:
- `ai_investment_calendar56_it`를 새로 만들었다(같은 이름 없음 확인 → `createdb` → 개발 DB `pg_dump -Fc | pg_restore`).
- 복원 직후 상태: `c7e2a9f4d158`, `stock_price` 251,455행, `market_index` 1,480행.
- 통합 테스트 시험 데이터가 들어 있다. 남겨 둔다.
- 기존 시험 DB는 쓰지 않았고 지우지 않았다: `_design53`·`_design53_it`·`_pipeline54`·`_pipeline54_it`·`_special_close55_it`·`ai_investment_verify`·`ai_investment_postcheck47`.

### 14.1 남은 위험

1. **서버 재시작 전까지 55·56차가 운영에 반영되지 않는다**(12절).
2. **근거 수준**: 11-19 시각은 증권사 공지·보도로 확인했다. 한국거래소 공지 원문은 확인하지 못했다.
3. **장전 시간외**(11-19 09:30~09:40)와 정규장 개장 전의 단계를 따로 나누지 않는다. 둘 다 `pre_market`이다.
4. **11-19 수집 권장 시각**:
   - `data_monitor`의 권장 문구 "거래일 16:30 KST 이후 1회"는 11-19에는 정규장 마감 시각과 겹친다. 그 시각에 수집하면 지수는 `confirmation_pending`으로 보류된다.
   - 11-19는 **17:00 이후** 실행을 권장한다. 문구 자체는 운영 보고서 출력이라 바꾸지 않았다.
5. **평가기 휴장일 차이**: 4절의 `forecast_check._expected_day24`. 별도 결정이 필요하다.
6. **환율 날짜 정의**: 원천 일봉 날짜가 서머타임 기간에 하루 이르게 붙는 것으로 보인다(3.1절, [추정]). 별도 데이터 작업이 필요하다.
7. **화면 장 단계 표시 없음**: 9절의 필드 추가 제안.
8. **2027년 달력**: 적용 범위가 2026-12-31까지다. 2027 연초 개장일(보통 10:00 개장)은 `open`으로 넣어야 한다.

## 15. 커밋과 push

- 커밋 대상:
  - `backend/app/data/krx_calendar.json`
  - `backend/app/krx_calendar.py`·`index_confirm.py`·`session_ohlcv.py`·`data_monitor.py`(주석만)
  - `backend/scripts/refresh_data.py`
  - `backend/evals/quality_judges.py`·`run_quality.py`·`forecast_check.py`
  - `backend/API.md`
  - `backend/tests/test_krx_calendar_completeness_56.py`·`test_krx_special_close_55.py`
  - `docs/DATA_QUALITY_AND_TRADING_CALENDAR.md`, 이 문서
- 바꾸지 않은 것:
  - `krx_session_schedule.json`(`announced` 유지)
  - `alembic/`(마이그레이션은 `pending`에 그대로)
  - 평가 세트 v1·v2, 프런트엔드, `docs/frontend-api-types.ts`
  - Ollama 설정, 보존 분봉, `.gitattributes`
- 커밋 해시·push·CI 결과는 최종 보고에 적는다(이 문서를 담은 커밋이라 여기에 쓸 수 없다). main 병합은 하지 않는다.
