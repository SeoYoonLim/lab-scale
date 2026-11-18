# 프런트엔드 API 연동 안내 (60차, 2026-11-15)

백엔드 `feature/backend` `93e8f00` 기준. 대상은 이 저장소의 프런트엔드(`lab-scale-frontend/frontend`)를 만드는 사람이다.
**이번 작업에서 API 응답 필드는 하나도 바꾸지 않았다.** 아래 내용은 지금 응답을 바르게 쓰는 방법과, 합의가 필요한 제안이다.

- 상세 근거·검증 기록: `docs/BACKEND_API_RUNTIME_AUDIT_60.md`
- 필드 하나하나의 설명: `backend/API.md`
- 기계 확인용 계약: 이 문서 부록 A. 백엔드 테스트 `tests/test_api_contract_60.py`가 실제 OpenAPI와 같은지 검사한다.

## 1. 먼저 알아야 할 것 (요약)

1. **실제 서버로 확인한 공개 GET**: health, status, market/overview, companies, price-history, news, disclosures, event-reactions, discovery/trending, ETF holdings, disclaimer, oauth providers.
   - 37개 요청을 보냈고, 상태 코드와 스키마가 모두 맞았다.
   - 인증 GET은 토큰 없이 401만 확인했다. 로그인 후 응답 내용은 이번에 실제로 보지 않았다.
   - **실시간 시세(`realtime-price`)는 이번에 호출하지 않았다**(외부 시세 원천 호출 금지).
2. 화면이 쓰는 API 중 응답 형식이 바뀐 것은 없다. 대신 아래 **의미 정정**이 있다.
   - 차트의 종가는 공식 종가 기준이다(47차 교정).
   - 고가·저가는 2026-09-14 이후 주식에서 장 마감 뒤 애프터마켓 체결을 포함한다.
   - 거래량은 KRX 하루 기준이고 NXT 체결은 빠진다.
3. **거래시간은 날짜마다 다르다.** 일반 거래일은 09:00~15:30, **2026-11-19는 10:00~16:30**, 2026-12-31은 휴장이다.
   - 화면에 "15:30"을 고정으로 넣지 않는다.
   - 응답의 `expected_date`·`intraday_excluded`·`lag_business_days`가 이미 그날 시각을 반영한다.
4. 차트 기간 선택이 최대 90거래일이다. 백엔드는 **365거래일까지** 주고, 버튼을 켤지 판단할 값(`available_count`)도 준다(4.1절).
5. 장 단계(장 시작 전·장중·확정 대기)와 지수 종가 상태를 화면에 보이려면 필드 추가가 필요하다. **제안만** 있다(7절, 합의 필요).

## 2. 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| 주소 | 프런트는 `VITE_API_BASE_URL ?? '/api'`를 앞에 붙인다(`src/api/http.ts`). 개발 서버는 `http://127.0.0.1:8000` |
| 인증 | `Authorization: Bearer <access_token>`. 토큰이 없거나 틀리면 **401** + `{"detail": "인증이 필요합니다. 다시 로그인해주세요."}` |
| 검증 오류 | **422** + `{"detail": [{"type", "loc", "msg", "input", "ctx"?}]}`. `loc[0]`은 `query`·`path`·`body` |
| 없음 | **404** + `{"detail": "<한국어 문장>"}`. 예: 등록되지 않은 종목, ETF가 아닌 종목의 구성종목 |
| 과다 요청 | **429**(공개 GET 600회/분) |
| 서버·DB 장애 | **503** 또는 **500** + 고정 문구(내부 정보 없음) |
| 빈 결과 | 오류가 아니다. **200** + 빈 배열(`items: []`, `total: 0`). 예: 기간에 기사 없음, ETF 거래량 급증 없음, `offset`이 끝을 넘음 |
| 날짜 | `"YYYY-MM-DD"` 문자열(KST 날짜) |
| 시각 | ISO 8601에 오프셋이 붙는다. 응답 때 계산한 시각과 41차 공개 API(시장 요약·뉴스·공시 목록)의 시각은 KST `+09:00`이다. 다른 저장 시각(ETF `collected_at`, 계정·주문 등의 `created_at`)은 UTC `+00:00`이다(API.md 규칙). 오프셋을 읽어 KST로 바꿔 보인다 |
| 숫자 | 가격·등락률은 number, 거래량·건수는 integer. 값이 없으면 `null`이다. **0으로 그리지 않는다**("—") |
| 총 건수 | `/api/companies`는 배열 본문에 `X-Total-Count` 헤더를 붙인다. 목록 API(news·disclosures)는 본문의 `total`·`has_next`·`total_pages`를 쓴다 |

## 3. 화면이 쓰는 API와 확인 상태

`src/api/http.ts`에서 찾은 호출 목록이다(읽기만 했다). "실제 확인"은 이번에 실행 중인 서버로 보낸 요청이다.

| 화면 기능 | 요청 | 인증 | 이번 확인 |
| --- | --- | --- | --- |
| 종목 검색 | `GET /companies?q&limit&asset_type` | 없음 | **실제 확인**(이름·코드 검색, ETF, offset 초과 빈 배열, `X-Total-Count`, limit=0 422) |
| 종목 차트 | `GET /stocks/{ticker}/price-history?days` | 없음 | **실제 확인**(005930 90·365, 000660 5, ETF 069500, days 0·366 422, 없는 종목 404) |
| 사건 반응 카드 | `GET /stocks/{ticker}/event-reactions` | 없음 | **실제 확인**(005930, 000660 news만) |
| 급등·급락 | `GET /discovery/trending` | 없음 | **실제 확인**(gainers 주식, ETF 거래량 급증 = 빈 배열) |
| ETF 구성종목 | `GET /etf/{ticker}/holdings` | 없음 | **실제 확인**(069500, 주식 404) |
| 푸터 기준일·면책 | `GET /status`, `GET /disclaimer` | 없음 | **실제 확인**(ko·en) |
| 현재가 | `GET /stocks/{ticker}/realtime-price` | 없음 | **확인 안 함**(네이버 실시간 호출. 문서·스키마만 대조) |
| 로그인·회원 | `/auth/*` | 일부 | 토큰 없는 401만 |
| 리서치·관심종목·포트폴리오·진단·성과·노출·일일 브리핑·기록 검색 | 각 경로 | 필요 | 토큰 없는 401만(포트폴리오·리서치·관심종목·브리핑·일일 브리핑 current). 로그인 응답 내용은 이번에 보지 않았다 |

화면이 **쓰지 않는** 공개 API(백엔드에는 있고 실제 확인함):

| 요청 | 쓸 만한 곳 |
| --- | --- |
| `GET /market/overview` | 시장 카드. 지금 시장 카드는 일일 브리핑의 market 영역을 읽는다. 로그인 없이 지수·등락 분포·지연을 보이려면 이것을 쓴다 |
| `GET /stocks/{ticker}/news` | 종목 뉴스 목록(페이지네이션, 기간, 정렬) |
| `GET /stocks/{ticker}/disclosures` | 종목 DART 공시 목록(정정 공시 필터). ETF는 200 + `supported=false` + 안내 `message` |
| `GET /health` | 운영 확인용. 화면에는 필요 없다 |

## 4. 지금 응답을 바르게 쓰는 방법

### 4.1 종목 차트(price-history)

- `days`는 **달력 일이 아니라 최근 N거래일**이다. 1~365, 기본 90이다.
- 화면 기간 선택은 지금 최대 90이다(`PriceChartCard.tsx`의 `RANGES`).
  - 180·365를 넣으려면 응답의 `available_count`(그 종목의 저장 행 전체 수)를 보고 버튼을 켠다.
  - 예: 삼성전자 740, 2026-11-15 확인.
- 응답에는 41차에 추가된 `available_count`·`first_date`·`missing_trading_days`가 있다.
  - 프런트 스키마(`priceHistoryResponseSchema`)에는 이 셋이 없다. 그래서 파싱 때 조용히 버려진다(오류는 아님).
  - 쓰려면 스키마와 타입에 더해야 한다.
- 그리는 개수는 `count`다. `days`개를 가정하지 않는다. `count < days`는 데이터가 모자란 것이고 오류가 아니다.
- 각 값의 뜻(2026-09-14 이후 주식):

| 값 | 뜻 | 화면 문구 권장 |
| --- | --- | --- |
| `close` | 공식 종가(KRX 정규장 마감) x 원천 수정주가 비율 | "종가" |
| `open` | KRX 정규장 시가 | "시가" |
| `high`·`low` | KRX 하루 범위. **장 마감 뒤 애프터마켓 체결 포함**(ETF는 정규장) | 주식이고 날짜 >= 2026-09-14면 "고·저는 애프터마켓 체결 포함" |
| `volume` | KRX 하루 거래량(정규장·장후 종가매매·애프터마켓 포함, **NXT 제외**) | "KRX 거래량". "정규장 거래량"이라고 쓰지 않는다 |
| `change_pct` | 저장 종가끼리의 전일 대비(%) | "등락률" |

- `close`는 OpenAPI 타입이 `number | null`이다. 하지만 DB 열이 NOT NULL이라 실제로는 null이 나오지 않는다. 프런트 스키마의 `z.number()`(null 불허)도 지금 데이터에서는 문제가 없다.

### 4.2 데이터 기준일과 지연

| 보일 것 | 쓸 필드 | 주의 |
| --- | --- | --- |
| 화면 하단 "데이터 기준일" | `GET /status`의 `sources.stock_price.latest_date` | 지금 쓰는 방식이 맞다 |
| "마지막 갱신 시각" | `status.last_refresh.finished_at` | `refresh_data` 실행 기록뿐이다. 개별 수집 스크립트로 들어온 데이터는 반영되지 않는다. 그래서 기준일보다 이를 수 있다(2026-11-15 확인: 갱신 기록 11-03, 시세 11-12). 기준일 대신 쓰지 않는다 |
| 지수 값의 날짜 | `market/overview`의 `indices[].date` | "11-12 종가"처럼 날짜를 같이 보인다 |
| 지연 | `indices[].lag_business_days`, `freshness.status`(`latest`·`delayed`·`stale`·`no_data`) | 거래일 수로 센다(주말·휴장일 제외). 2026-11-15(일)에 11-12 지수는 기대 거래일 11-13 대비 1거래일 지연(`delayed`)이다 |
| 뉴스·공시 최신성 | 목록의 `freshness`(`last_collected_at`, `collection_lag_weekdays`, `stale`, `company_latest_at`, `last_collection`) | 공시 목록의 `last_collection.step`도 `"news"`다. 뉴스와 공시를 한 단계에서 같이 모은다. 단계 이름으로 분기하지 않는다 |

### 4.3 거래시간·장중 판정

백엔드가 날짜별 시각으로 계산해 응답에 넣는다. 화면은 값을 그대로 쓰고, 시각을 하드코딩하지 않는다.

| 날 | 정규장 | 기대 거래일이 그날로 바뀌는 시각 | 지수 확정 시도 |
| --- | --- | --- | --- |
| 일반 거래일 | 09:00~15:30 | 15:30 | 15:40부터, 네이버 일별과 값이 같을 때만 저장 |
| 2026-11-19(수능일) | 10:00~16:30 | 16:30 | 16:40부터, 같은 조건 |
| 2026-12-31 | 휴장(연말 휴장, 마지막 거래일 12-30) | — | — |
| 2027년 | KRX 공지 전이라 미확정(신정만 휴장) | 백엔드는 평일을 "열릴 수 있는 날"로 센다 | `freshness.calendar_unknown_days`가 0보다 크면 "달력으로 확인할 수 없는 날 포함"을 보인다 |

- `freshness.intraday_excluded`(시장 요약)와 일일 브리핑 `intraday`: **그날 정규장 마감 전이면 true**다. 장 시작 전도 포함한다(오늘 종가가 아직 없다는 뜻). "장중"이라는 이름이지만 "정규장 진행 중"만을 뜻하지 않는다.
- 지수는 장중 값을 저장하지 않는다. 화면에 보이는 지수는 항상 확정 종가다. 마감 직후 "오늘 값 확정 대기"와 "수집 실패"는 지금 응답으로 구분할 수 없다(7절 제안).

## 5. 오류·빈 결과 처리 확인 (실제 응답)

| 경우 | 응답 |
| --- | --- |
| 등록되지 않은 종목(`999999`) | 404 `{"detail": "'999999' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요."}` |
| `days=366` / `days=0` | 422, `loc: ["query", "days"]`, `type: less_than_equal` / `greater_than_equal` |
| 뉴스 `date_from > date_to` | 422, `loc: ["query", "date_from"]`, `msg: "date_from이 date_to보다 늦습니다."` |
| 뉴스 기간에 기사 없음 | 200, `items: []`, `total: 0`, `has_next: false`, `total_pages: 0` |
| ETF 공시 | 200, `supported: false`, `message`에 뉴스 목록 안내 |
| ETF가 아닌 종목의 구성종목 | 404 `{"detail": "삼성전자는 ETF가 아니라 구성종목 비중을 제공하지 않습니다."}` |
| `companies?offset=9999` | 200 `[]`, `X-Total-Count`는 그대로(전체 건수) |
| 토큰 없이 인증 경로 | 401 `{"detail": "인증이 필요합니다. 다시 로그인해주세요."}` |
| 소셜 로그인 공급자 | `{"providers": []}`(개발 서버에 키가 설정되지 않음). 버튼을 숨긴다 |

## 6. 발견한 불일치(프런트 확인 필요)

| # | 내용 | 영향 | 권장 |
| --- | --- | --- | --- |
| 1 | 차트 최대 90거래일. 백엔드는 365까지 | 긴 기간을 볼 수 없다 | `available_count`로 180·365 버튼 켜기(스키마에 3개 필드 추가) |
| 2 | `priceHistoryResponseSchema`에 `available_count`·`first_date`·`missing_trading_days` 없음 | 값이 버려진다 | 스키마·타입 추가 |
| 3 | 차트 툴팁 "고/저"에 애프터마켓 포함 표시 없음 | 고가·저가를 정규장 값으로 오해할 수 있다 | 4.1절 문구 |
| 4 | 시장 카드가 일일 브리핑(로그인 필요)을 읽음 | 로그인 전에는 시장 요약이 없다 | 공개 `market/overview` 사용 검토 |
| 5 | 일부 수집 시각이 UTC(`+00:00`) | 그대로 보이면 9시간 어긋난다 | 표시할 때 KST로 변환 |
| 6 | `event-reactions`의 `summary`는 OpenAPI에서 타입 없는 객체 | 자동 생성 타입(`docs/frontend-api-types.ts`)에서 `Record<string, unknown>` | 지금처럼 Zod로 필드를 정하는 방식 유지 |

## 7. 장 상태·지수 확정 상태 API 확장 제안 (합의 전, 구현하지 않음)

### 7.1 지금 백엔드 안에 있는 것

| 이름 | 어디 | 공개 여부 |
| --- | --- | --- |
| 장 단계 `non_trading_day`·`pre_market`·`intraday`·`closed` | `app.krx_calendar.market_phase(now)`(56차) | 내부 함수. 응답에 없음 |
| 지수 보류 사유 `pre_market`·`intraday`·`confirmation_pending`·`mismatch`·`unverified` | `app.index_confirm`(56차) | 수집 스크립트 출력에만 있음. **DB에도 저장되지 않는다** |
| `intraday_excluded`(bool), `freshness.status`, `lag_business_days` | 시장 요약 | 공개 |
| `last_collection.index.status`(`success`·`partial`·`failed`) | 시장 요약 | 공개 |

### 7.2 제안 필드

`GET /api/market/overview` 최상위에 `market_session`(객체), `indices[]`에 `close_status`(문자열)를 더한다. **선택 필드로 추가만 하고 기존 필드는 그대로 둔다.** 일일 브리핑 `content.sections.market`에도 같은 키를 넣는다(51차 제안과 같은 위치).

`market_session`:

| 키 | 타입 | 필수 | 값 | 의미 |
| --- | --- | --- | --- | --- |
| `date` | string(date) | 예 | KST 오늘 | 판정한 날 |
| `phase` | string | 예 | `non_trading_day`·`pre_market`·`intraday`·`closed` | 달력만으로 정한 장 단계. **종가 확정 여부가 아니다** |
| `regular_open` | string \| null | 예 | `"09:00"`, 수능일 `"10:00"`, 휴장일 `null` | 그날 정규장 개장 |
| `regular_close` | string \| null | 예 | `"15:30"`, 수능일 `"16:30"`, 휴장일 `null` | 그날 정규장 마감 |
| `confirmation_after` | string(datetime) \| null | 예 | 마감 + 10분(KST) | 지수 확정 검증을 시작할 수 있는 시각 |
| `special` | string \| null | 예 | 예: `"2027학년도 대학수학능력시험일(정규시장 10:00~16:30)"` | 특수 거래시간 날의 이름. 일반 거래일 `null` |
| `calendar_known` | boolean | 예 | 달력 범위 밖·미확정이면 false | false면 위 시각은 기본값 추정이다 |

`indices[].close_status`:

| 값 | 조건 | 화면 |
| --- | --- | --- |
| `confirmed` | `date == expected_date`(기대 거래일의 확정 종가가 저장됨) | 배지 없음 |
| `confirmation_pending` | 오늘이 거래일이고 지금이 `regular_close` 이상 `confirmation_after` 미만. 또는 그 뒤인데 마감 뒤 지수 수집이 아직 한 번도 없음 | "오늘 종가 확정 대기(전 거래일 종가 표시)" |
| `verification_held` | 마감 + 10분 뒤 지수 수집이 있었지만 오늘 행을 확정하지 못함(네이버와 값이 다르거나 대조 불가) | "오늘 종가 확인 중" |
| `collection_failed` | 마지막 지수 수집이 `failed` | "지수 수집 실패" |
| `delayed` | 위에 해당하지 않는데 `lag_business_days >= 1` | 기존 지연 배지 |

- **`confirmation_pending`은 실패가 아니다.** 시간 조건에서 생기는 정상 대기다. 수집 실패(`collection_failed`)·검증 보류(`verification_held`)와 섞지 않는다.
- 51차 제안의 `indices[].confirmation_pending`(bool)은 `close_status == "confirmation_pending"`과 같다. 두 안 중 하나로 정한다(`close_status`를 권장, 상태를 한 필드로 구분).
- `verification_held`를 서버가 알려면 수집 때 보류 사유를 저장해야 한다. 지금은 출력에만 있다. 수집 기록 표에 사유 열을 더하는 마이그레이션 또는 별도 기록이 필요하다(**DB 변경 승인 필요**). 저장하지 않으면 `verification_held`와 `confirmation_pending`을 구분할 수 없다.

예시(KS11, 수집이 제때 된다고 가정):

| 시각(KST) | `phase` | `expected_date` | 지수 `date` | `close_status` |
| --- | --- | --- | --- | --- |
| 2026-11-18(수) 08:50 | `pre_market` | 11-17 | 11-17 | `confirmed` |
| 11-18 14:00 | `intraday` | 11-17 | 11-17 | `confirmed` |
| 11-18 15:35 | `closed` | 11-18 | 11-17 | `confirmation_pending` |
| 11-18 16:10(수집 뒤, 네이버와 같음) | `closed` | 11-18 | 11-18 | `confirmed` |
| 11-18 16:10(수집 뒤, 네이버와 다름) | `closed` | 11-18 | 11-17 | `verification_held` |
| **2026-11-19(목) 09:30** | `pre_market`(10:00 개장) | 11-18 | 11-18 | `confirmed` |
| 11-19 16:10 | `intraday`(16:30 마감) | 11-18 | 11-18 | `confirmed` |
| 11-19 16:35 | `closed` | 11-19 | 11-18 | `confirmation_pending` |
| 2026-12-31(목) 12:00 | `non_trading_day` | 12-30 | 12-30 | `confirmed` |

호환성:
- 새 키만 더한다. 프런트 Zod 스키마는 strict가 아니라 모르는 키를 버리므로, 서버가 먼저 배포돼도 깨지지 않는다.
- 이미 저장된 일일 브리핑 스냅샷에는 새 키가 없다. 없으면 표시하지 않는다(브리핑은 불변 스냅샷이라 만든 시각의 상태로 남는다).
- `freshness.intraday_excluded`는 뜻을 바꾸지 않는다(장 시작 전 포함). 장 진행 중만 알려면 `phase == "intraday"`를 쓴다.

## 8. 프런트 담당자 확인 질문

1. 차트에 180·365거래일 선택을 넣을까? 넣는다면 `available_count` 기준으로 버튼을 끄는 방식이 맞나?
2. 시장 카드를 로그인 없이도 보일까? 그렇다면 공개 `GET /api/market/overview`로 바꿀까, 아니면 지금처럼 일일 브리핑을 쓸까?
3. 장 상태 표시가 필요한가? 필요하면 7.2의 `market_session`·`close_status` 안으로 할까, 51차 `confirmation_pending`(bool) 안으로 할까?
4. `verification_held`까지 구분할까? 구분하려면 백엔드 DB 변경(수집 보류 사유 저장)이 필요하다.
5. 고가·저가 애프터마켓 포함, KRX 거래량 문구를 차트에 넣을까(4.1절)?
6. 종목 뉴스·공시 목록 API를 종목 화면에 붙일 계획이 있나? 있으면 페이지 크기(기본 20)와 정렬을 정한다.
7. 일부 수집 시각이 UTC인 것을 화면에서 KST로 바꿔 보일까, 아니면 백엔드가 KST로 통일하길 원하나(응답 값 변경이라 합의 필요)?

## 부록 A. 공개 GET 계약(기계 확인용)

아래 JSON은 실행 중인 서버와 같은 코드의 OpenAPI에서 만든다(`python -m tests.test_api_contract_60`). 백엔드 테스트가 이 블록과 실제 OpenAPI가 같은지 검사한다.

표기:
- `fields`: 응답 필드 경로(`[]`는 배열 원소) → 타입
- 타입 뒤 `?`: null 허용, `!`: 응답에 항상 있음(필수)
- `object(any)`: 키가 정해지지 않은 객체
- `query`: 쿼리 파라미터의 기본값·최소·최대·허용값
- `errors`: OpenAPI에 선언된 오류 상태 코드

<!-- contract60:begin -->
```json
{
 "/api/companies": {
  "errors": [
   "422",
   "429",
   "503"
  ],
  "fields": {
   "[].asset_type": "enum(stock|etf)",
   "[].change_pct": "number?",
   "[].latest_close": "number?",
   "[].market": "string?",
   "[].name": "string!",
   "[].sector": "string?",
   "[].ticker": "string!"
  },
  "query": {
   "asset_type": {
    "enum": [
     "stock",
     "etf"
    ]
   },
   "limit": {
    "default": 50,
    "maximum": 300,
    "minimum": 1
   },
   "offset": {
    "default": 0,
    "maximum": 9223372036854775807,
    "minimum": 0
   },
   "order": {
    "default": "asc",
    "enum": [
     "asc",
     "desc"
    ]
   },
   "q": {
    "maxLength": 100
   },
   "sort": {
    "default": "id",
    "enum": [
     "id",
     "name",
     "ticker"
    ]
   }
  }
 },
 "/api/disclaimer": {
  "errors": [
   "422",
   "429",
   "503"
  ],
  "fields": {
   "text": "string!"
  },
  "query": {
   "locale": {
    "maxLength": 20
   }
  }
 },
 "/api/discovery/trending": {
  "errors": [
   "422",
   "429",
   "503"
  ],
  "fields": {
   "asset_type": "enum(stock|etf)",
   "category": "enum(gainers|losers|volume_surge)!",
   "items": "array!",
   "items[].avg_volume": "number?",
   "items[].change_pct": "number?",
   "items[].close_price": "number!",
   "items[].company_name": "string!",
   "items[].ticker": "string!",
   "items[].volume": "integer?",
   "items[].volume_ratio": "number?",
   "price_date": "string!",
   "window_days": "integer!"
  },
  "query": {
   "asset_type": {
    "default": "stock",
    "enum": [
     "stock",
     "etf"
    ]
   },
   "category": {
    "default": "gainers",
    "enum": [
     "gainers",
     "losers",
     "volume_surge"
    ]
   },
   "limit": {
    "default": 10,
    "maximum": 50,
    "minimum": 1
   }
  }
 },
 "/api/etf/{ticker}/holdings": {
  "errors": [
   "404",
   "422",
   "429",
   "503"
  ],
  "fields": {
   "as_of_date": "string?!",
   "asset_type": "string!",
   "collected_at": "string?!",
   "data_warnings": "array!",
   "data_warnings[].kind": "string!",
   "data_warnings[].message": "string!",
   "disclaimer": "string!",
   "holding_count": "integer!",
   "items": "array!",
   "items[].code": "string?!",
   "items[].name": "string!",
   "items[].rank": "integer!",
   "items[].weight_pct": "number!",
   "limit": "integer!",
   "message": "string?!",
   "name": "string!",
   "source": "string?!",
   "stale": "boolean!",
   "stale_notice": "string?!",
   "ticker": "string!",
   "top_weight_pct": "number?!",
   "total_weight_pct": "number?!"
  },
  "query": {
   "limit": {
    "default": 10,
    "maximum": 50,
    "minimum": 1
   }
  }
 },
 "/api/health": {
  "errors": [
   "429",
   "503"
  ],
  "fields": {
   "checks": "object(any)!",
   "status": "string!"
  },
  "query": {}
 },
 "/api/market/overview": {
  "errors": [
   "429",
   "503"
  ],
  "fields": {
   "as_of": "string?!",
   "breadth": "object?!",
   "breadth.by_market": "object(any)!",
   "breadth.change_missing": "integer!",
   "breadth.date": "string!",
   "breadth.down": "integer!",
   "breadth.down_ratio_pct": "number?!",
   "breadth.flat": "integer!",
   "breadth.flat_ratio_pct": "number?!",
   "breadth.lag_business_days": "integer!",
   "breadth.no_price": "integer!",
   "breadth.registered": "integer!",
   "breadth.total": "integer!",
   "breadth.universe": "const(collected_stocks)!",
   "breadth.up": "integer!",
   "breadth.up_ratio_pct": "number?!",
   "expected_date": "string!",
   "freshness": "object!",
   "freshness.calendar_unknown_days": "integer!",
   "freshness.dates_differ": "boolean!",
   "freshness.intraday_excluded": "boolean!",
   "freshness.lag_business_days": "integer?!",
   "freshness.status": "enum(latest|delayed|stale|no_data)!",
   "generated_at": "string!",
   "indices": "array!",
   "indices[].change": "number?!",
   "indices[].change_pct": "number?!",
   "indices[].change_unavailable_reason": "enum(no_previous_row|previous_trading_day_missing)?!",
   "indices[].close": "number?!",
   "indices[].code": "enum(KS11|KQ11)!",
   "indices[].date": "string?!",
   "indices[].first_collected_at": "string?!",
   "indices[].lag_business_days": "integer?!",
   "indices[].name": "string!",
   "indices[].previous_close": "number?!",
   "indices[].previous_date": "string?!",
   "last_collection": "object!",
   "last_collection.index": "object?!",
   "last_collection.index.error_code": "string?!",
   "last_collection.index.finished_at": "string?!",
   "last_collection.index.ok": "boolean!",
   "last_collection.index.status": "enum(success|partial|failed)!",
   "last_collection.index.step": "string!",
   "last_collection.prices": "object?!",
   "last_collection.prices.error_code": "string?!",
   "last_collection.prices.finished_at": "string?!",
   "last_collection.prices.ok": "boolean!",
   "last_collection.prices.status": "enum(success|partial|failed)!",
   "last_collection.prices.step": "string!",
   "missing": "array!",
   "note": "string!",
   "source": "object!",
   "source.per_row_source_recorded": "boolean!",
   "source.provider": "string!",
   "source.tables": "array!",
   "status": "enum(ok|partial|no_data)!",
   "unavailable": "array!"
  },
  "query": {}
 },
 "/api/status": {
  "errors": [
   "429",
   "503"
  ],
  "fields": {
   "generated_at": "string!",
   "is_realtime": "boolean!",
   "last_refresh": "object?!",
   "last_refresh.failed_steps": "array!",
   "last_refresh.finished_at": "string?!",
   "last_refresh.ok": "boolean!",
   "note": "string!",
   "sources": "object!",
   "sources.disclosure": "object!",
   "sources.disclosure.latest_date": "string?!",
   "sources.etf_price": "object!",
   "sources.etf_price.latest_date": "string?!",
   "sources.exchange_rate": "object!",
   "sources.exchange_rate.latest_date": "string?!",
   "sources.market_index": "object!",
   "sources.market_index.latest_date": "string?!",
   "sources.market_index.stale": "boolean!",
   "sources.news": "object!",
   "sources.news.latest_published_date": "string?!",
   "sources.stock_price": "object!",
   "sources.stock_price.latest_date": "string?!"
  },
  "query": {}
 },
 "/api/stocks/{ticker}/disclosures": {
  "errors": [
   "404",
   "422",
   "429",
   "503"
  ],
  "fields": {
   "asset_type": "enum(stock|etf)!",
   "company_name": "string!",
   "filters": "object!",
   "filters.correction": "enum(all|only|exclude)!",
   "filters.date_from": "string?!",
   "filters.date_to": "string?!",
   "filters.order": "enum(asc|desc)!",
   "freshness": "object!",
   "freshness.collection_lag_weekdays": "integer?!",
   "freshness.company_latest_at": "string?!",
   "freshness.freshness_state": "enum(fresh|stale|no_data)",
   "freshness.last_attempt_finished_at": "string?",
   "freshness.last_collected_at": "string?!",
   "freshness.last_collection": "object?!",
   "freshness.last_collection.error_code": "string?!",
   "freshness.last_collection.finished_at": "string?!",
   "freshness.last_collection.ok": "boolean!",
   "freshness.last_collection.status": "enum(success|partial|failed)!",
   "freshness.last_collection.step": "string!",
   "freshness.last_collection_status": "enum(success|partial|failed)?",
   "freshness.last_collection_step": "string?",
   "freshness.last_successful_collection_at": "string?",
   "freshness.latest_record_at": "string?",
   "freshness.record_lag_weekdays": "integer?",
   "freshness.stale": "boolean!",
   "has_next": "boolean!",
   "items": "array!",
   "items[].collected_at": "string?!",
   "items[].disclosed_at": "string?!",
   "items[].disclosed_date": "string?!",
   "items[].disclosure_type": "string?!",
   "items[].id": "integer!",
   "items[].is_correction": "boolean!",
   "items[].rcept_no": "string?!",
   "items[].source_url": "string?!",
   "items[].title": "string!",
   "limit": "integer!",
   "message": "string?!",
   "offset": "integer!",
   "supported": "boolean!",
   "ticker": "string!",
   "total": "integer!",
   "total_pages": "integer!"
  },
  "query": {
   "correction": {
    "default": "all",
    "enum": [
     "all",
     "only",
     "exclude"
    ]
   },
   "date_from": {},
   "date_to": {},
   "limit": {
    "default": 20,
    "maximum": 50,
    "minimum": 1
   },
   "offset": {
    "default": 0,
    "maximum": 9223372036854775807,
    "minimum": 0
   },
   "order": {
    "default": "desc",
    "enum": [
     "asc",
     "desc"
    ]
   }
  }
 },
 "/api/stocks/{ticker}/event-reactions": {
  "errors": [
   "404",
   "422",
   "429",
   "503"
  ],
  "fields": {
   "asset_type": "string!",
   "company_name": "string!",
   "disclaimer": "string!",
   "events": "array!",
   "events[].base_close": "number?!",
   "events[].base_date": "string?!",
   "events[].event_at": "string?!",
   "events[].event_date": "string?!",
   "events[].event_type": "enum(disclosure|news)!",
   "events[].note": "string?!",
   "events[].other_events_in_window": "integer!",
   "events[].reaction_start_date": "string?!",
   "events[].returns": "object!",
   "events[].returns.d1": "object!",
   "events[].returns.d1.available": "boolean!",
   "events[].returns.d1.excess_pct": "number?!",
   "events[].returns.d1.index_pct": "number?!",
   "events[].returns.d1.pct": "number?!",
   "events[].returns.d1.reason": "string?!",
   "events[].returns.d20": "object!",
   "events[].returns.d20.available": "boolean!",
   "events[].returns.d20.excess_pct": "number?!",
   "events[].returns.d20.index_pct": "number?!",
   "events[].returns.d20.pct": "number?!",
   "events[].returns.d20.reason": "string?!",
   "events[].returns.d5": "object!",
   "events[].returns.d5.available": "boolean!",
   "events[].returns.d5.excess_pct": "number?!",
   "events[].returns.d5.index_pct": "number?!",
   "events[].returns.d5.pct": "number?!",
   "events[].returns.d5.reason": "string?!",
   "events[].time_known": "boolean!",
   "events[].title": "string!",
   "events[].url": "string?!",
   "events[].volume_ratio": "number?!",
   "events[].volume_sample_days": "integer!",
   "market_index": "string?!",
   "price_as_of": "string?!",
   "summary": "object(any)!",
   "ticker": "string!"
  },
  "query": {
   "event_type": {
    "default": "all",
    "enum": [
     "disclosure",
     "news",
     "all"
    ]
   },
   "limit": {
    "default": 10,
    "maximum": 30,
    "minimum": 1
   },
   "since_days": {
    "default": 60,
    "maximum": 120,
    "minimum": 1
   }
  }
 },
 "/api/stocks/{ticker}/news": {
  "errors": [
   "404",
   "422",
   "429",
   "503"
  ],
  "fields": {
   "asset_type": "enum(stock|etf)!",
   "company_name": "string!",
   "filters": "object!",
   "filters.date_from": "string?!",
   "filters.date_to": "string?!",
   "filters.order": "enum(asc|desc)!",
   "freshness": "object!",
   "freshness.collection_lag_weekdays": "integer?!",
   "freshness.company_latest_at": "string?!",
   "freshness.freshness_state": "enum(fresh|stale|no_data)",
   "freshness.last_attempt_finished_at": "string?",
   "freshness.last_collected_at": "string?!",
   "freshness.last_collection": "object?!",
   "freshness.last_collection.error_code": "string?!",
   "freshness.last_collection.finished_at": "string?!",
   "freshness.last_collection.ok": "boolean!",
   "freshness.last_collection.status": "enum(success|partial|failed)!",
   "freshness.last_collection.step": "string!",
   "freshness.last_collection_status": "enum(success|partial|failed)?",
   "freshness.last_collection_step": "string?",
   "freshness.last_successful_collection_at": "string?",
   "freshness.latest_record_at": "string?",
   "freshness.record_lag_weekdays": "integer?",
   "freshness.stale": "boolean!",
   "has_next": "boolean!",
   "items": "array!",
   "items[].collected_at": "string?!",
   "items[].id": "integer!",
   "items[].is_relevant": "boolean?!",
   "items[].published_at": "string?!",
   "items[].related_tickers": "array!",
   "items[].source": "string?!",
   "items[].summary": "string?!",
   "items[].title": "string!",
   "items[].url": "string?!",
   "limit": "integer!",
   "offset": "integer!",
   "ticker": "string!",
   "total": "integer!",
   "total_pages": "integer!"
  },
  "query": {
   "date_from": {},
   "date_to": {},
   "limit": {
    "default": 20,
    "maximum": 50,
    "minimum": 1
   },
   "offset": {
    "default": 0,
    "maximum": 9223372036854775807,
    "minimum": 0
   },
   "order": {
    "default": "desc",
    "enum": [
     "asc",
     "desc"
    ]
   }
  }
 },
 "/api/stocks/{ticker}/price-history": {
  "errors": [
   "404",
   "422",
   "429",
   "503"
  ],
  "fields": {
   "asset_type": "enum(stock|etf)!",
   "available_count": "integer!",
   "company_name": "string!",
   "count": "integer!",
   "days": "integer!",
   "first_date": "string?!",
   "items": "array!",
   "items[].change_pct": "number?!",
   "items[].close": "number?!",
   "items[].date": "string!",
   "items[].high": "number?!",
   "items[].low": "number?!",
   "items[].open": "number?!",
   "items[].volume": "integer?!",
   "missing_trading_days": "integer!",
   "price_as_of": "string?!",
   "ticker": "string!"
  },
  "query": {
   "days": {
    "default": 90,
    "maximum": 365,
    "minimum": 1
   }
  }
 },
 "/api/stocks/{ticker}/realtime-price": {
  "errors": [
   "404",
   "422",
   "429",
   "503"
  ],
  "fields": {
   "as_of": "string!",
   "change_amount": "number?",
   "change_pct": "number?",
   "company_name": "string!",
   "corrected_from": "string?",
   "current_price": "number!",
   "is_realtime": "boolean!",
   "queried_at": "string!",
   "source": "enum(naver|fallback)!",
   "ticker": "string!"
  },
  "query": {}
 }
}
```
<!-- contract60:end -->
