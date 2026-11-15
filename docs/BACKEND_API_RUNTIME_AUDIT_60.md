# 60차 실행 중 백엔드 API 스모크 테스트와 프런트엔드 연동 계약 점검 (2026-11-15)

**읽기 전용 점검이다.** 실행 중인 서버에는 GET만 보냈고, 코드 조사로 부작용이 없다고 확인한 경로만 호출했다. API 응답 필드는 바꾸지 않았다.
바꾼 것은 문서(`backend/API.md` 설명 정정, 이 보고서, `docs/FRONTEND_API_HANDOFF_60.md`)와 계약 테스트(`backend/tests/test_api_contract_60.py`)뿐이다.
개발 DB·실행 중인 서버·프런트엔드 코드는 건드리지 않았다.

## 1. 실제 서버 PID와 코드 커밋

| 항목 | 값 |
| --- | --- |
| 브랜치·커밋 | `feature/backend`, `93e8f00`(2026-11-15 18:08 KST 커밋), 원격과 같음, 작업 트리 깨끗함 |
| 서버 | `127.0.0.1:8000` Listen, 소유 PID **40824**(인터프리터), venv 런처 PID 47932, 2026-11-15 19:28:25 KST 시작(커밋 뒤), 명령 `.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000` |
| 이전 PID | 28088(15:12 시작). 재시작 작업에서 정상 종료됨 |
| 프런트엔드 5173 | 떠 있지 않음 |
| `GET /api/health` | 200 `status: ok`(db·migrations·ollama·data 모두 ok, `data.latest_date` 2026-11-12) |
| 달력 | `krx-2026-11-15.3`. 서버가 읽은 파일은 `93e8f00`과 같은 내용이고, 마지막 수정 17:56 KST가 서버 시작보다 앞선다 |

거래시간 정책(55~58차): 일반 거래일 09:00~15:30, 2026-11-19 10:00~16:30, 2026-12-31 연말 휴장.

## 2. API 전체 목록

출처는 실행 중인 서버의 `GET /openapi.json`(205KB)이다. **경로 50개, 메서드 56개**다. 앱 라우트 목록과 하나씩 대조해 같았고, 37차 테스트(`test_route_inventory_is_what_the_handoff_documents`, 50·56·인증 33)와도 같다.

| 구분 | 경로(메서드) |
| --- | --- |
| 공개 GET | `/api/health`, `/api/status`, `/api/market/overview`, `/api/companies`, `/api/disclaimer`, `/api/discovery/trending`, `/api/etf/{ticker}/holdings`, `/api/stocks/{ticker}/price-history`·`news`·`disclosures`·`event-reactions`·`realtime-price`, `/api/auth/oauth/providers`·`{provider}/login`·`{provider}/callback` |
| 인증 GET | `/api/auth/me`·`preferences`, `/api/briefing`, `/api/briefings/daily/current`·`history`·`{id}`, `/api/history/search`, `/api/portfolio`·`trades`·`diagnosis/history`·`diagnosis/{id}`·`diagnosis/{id}/compare`·`exposure`·`performance`, `/api/research`·`research/{id}`, `/api/watchlist` |
| 쓰기(POST·PUT·DELETE) | 인증·계정 복구·소셜 로그인 교환, 리서치 생성·삭제, 포트폴리오 주문·초기화·진단, 일일 브리핑 생성, 관심종목, 백테스트, 회원 탈퇴 |

엔드포인트별 메서드·필수/선택 파라미터·응답 스키마·필수·nullable 필드·오류 응답은 `docs/FRONTEND_API_HANDOFF_60.md` 부록 A에 기계 확인용 JSON으로 적었다(공개 GET 12개). 인증 API는 `backend/API.md`가 기준이다.

## 3. 실제 호출한 GET API

스크립트는 세션 스크래치 `api60/smoke.py`다. 응답을 서버의 OpenAPI 스키마(`$ref`·`anyOf`·`enum`·`const`·필수·자료형·스키마 밖 필드)와 대조했다. 검사기가 필수 필드 누락·자료형 오류·스키마 밖 필드·허용값 밖 값을 잡는지는 응답을 일부러 망가뜨려 확인했다.

**37개 요청: 상태 코드 기대와 다름 0, 스키마 불일치 0**(2026-11-15 20:00 KST, 응답 1~88ms).

| 분류 | 요청 | 결과 |
| --- | --- | --- |
| 상태 | health, status | 200 |
| 시장 | market/overview | 200 |
| 종목 검색 | companies `q=삼성전자`·`q=000660`·`asset_type=etf`·`offset=9999` / `limit=0` | 200(`X-Total-Count` 2·2·50·2, offset 초과는 `[]`) / 422 |
| 가격 이력 | 005930 기본(90)·365, 000660 `days=5`, ETF 069500 `days=5` / `days=366`·`0` / `999999` | 200 / 422 / 404 |
| 뉴스 | 005930 `limit=5`, 000660 `order=asc`, 2020-01 기간(빈 결과) / `date_from > date_to` / `999999` | 200 / 422 / 404 |
| 공시 | 005930, 000660 `correction=only`, ETF 069500 | 200(ETF는 `supported=false`) |
| 사건 반응 | 005930 `limit=3`, 000660 `event_type=news` | 200 |
| 발굴 | trending gainers, ETF volume_surge | 200(ETF는 빈 배열) |
| ETF | holdings 069500 / 005930 | 200 / 404 |
| 기타 | disclaimer ko·en, oauth providers | 200(공급자 `[]`, 개발 서버 키 없음) |
| 인증 거부 | portfolio, research, briefing, briefings/daily/current, watchlist(토큰 없음) | 401 고정 문구 |

## 4. 호출하지 않은 API와 사유

| API | 사유 |
| --- | --- |
| `GET /api/stocks/{ticker}/realtime-price` | 네이버 실시간 시세를 호출한다(외부 원천 요청 금지). 문서·스키마·프런트 Zod만 대조했다 |
| `GET /api/auth/oauth/{provider}/login`·`callback` | 외부 인증 서버로 리디렉션하고 쿠키·레이트 리밋 상태를 만들며, 콜백은 토큰 교환을 한다 |
| 인증 GET의 실제 내용 | 토큰을 얻으려면 로그인 POST가 필요하다(이번 금지 범위). 401만 확인했다. `briefings/daily/current`는 코드상 조회만 한다(없으면 404이고 만들지 않음) |
| 모든 POST·PUT·PATCH·DELETE | 사용자 기록·포트폴리오·리서치(LLM) 변경·생성이라 금지 |

부작용 조사(호출 전):
- 공개 GET 핸들러와 그 아래 모듈에서 DB 쓰기(`commit`·`add`·`delete`·INSERT/UPDATE/DELETE)와 외부 호출(`requests`·`http_retry`·FDR·Ollama)을 찾았다. 다음 모듈이 대상이었다: `market_snapshot`, `stock_feed`, `price_history`, `event_reaction_service`, `companies`, `discovery`, `etf_holdings_db`, `data_status`, `health`, `company_resolver`.
- 결과:
  - 쓰기는 `etf_holdings_db.save_snapshot`(수집용, GET은 `holdings_view`만 씀)뿐이었다.
  - 외부 호출은 `health`의 로컬 Ollama 목록 조회(`/api/tags`·`/api/ps`, 모델을 올리지 않음)와 `realtime_price`뿐이었다.
  - 미들웨어는 메모리 안의 레이트 리밋과 보안 헤더뿐이다.

## 5. API별 응답 검증 결과

| API | 확인한 것 |
| --- | --- |
| health | 첫 호출 직후의 일시적 `degraded`(첫 DB 연결이 점검 상한 2초를 넘음)와 지속 오류를 구분했다. 재시작 직후 1~2회 `degraded` 뒤에는 계속 `ok`(0.03~0.06초). 재시작 전 서버도 같았다. `.env`의 `localhost`(IPv6 먼저 시도)로 추정한다. 설정은 바꾸지 않았다 |
| status | `sources`: 주식·ETF·지수 2026-11-12, 환율 2026-11-02, 뉴스 2026-11-03, 공시 2026-11-03. `last_refresh.finished_at` 2026-11-03 20:03 |
| market/overview | `as_of` 2026-11-12, `expected_date` 2026-11-13(일요일 기준 직전 거래일), `freshness.status=delayed`, `lag_business_days=1`, `intraday_excluded=false`, `calendar_unknown_days=0`. KS11 5,342.73(-32.63, -0.61%, 전일 11-11), KQ11 696.53(-8.57, -1.22%). KS11 11-12 값은 50차에 네이버 확정 종가로 확인한 값과 같다. `unavailable: ["trading_value", "market_volume"]` |
| companies | 이름·코드 검색, 별칭 포함, ETF 필터, 배열 본문 + `X-Total-Count` |
| price-history | 종목 코드 일치, 날짜 오름차순·중복 없음, `count == days`(005930 90·365), `available_count` 740, `first_date` 2023-10-30, `missing_trading_days` 0, `price_as_of` 2026-11-12 |
| news | 게시 시각 최신순(기본)·오름차순, `total`·`has_next`·`total_pages`, 빈 기간은 200 빈 배열, 출처 `url`·`source`, `is_relevant` |
| disclosures | 접수일 최신순, `rcept_no`·`source_url`(DART), 정정 필터, ETF `supported=false`와 안내 `message` |
| event-reactions | 1·5·20거래일 수익률, 지수 대비 초과, `summary`, 면책 문구 |
| trending | `price_date` 2026-11-12, `window_days` 20 |
| etf holdings | 기준일 2026-10-30, `stale=true`와 안내, 구성종목 201개, 합계 99.98% |

## 6. 응답 스키마와 문서 불일치

`API.md`의 해당 절 필드 표·JSON 예시와 OpenAPI 응답 필드를 기계적으로 비교하고(공개 GET 11개), 의미도 읽었다.

| # | 불일치 | 처리 |
| --- | --- | --- |
| 1 | 가격 이력 `items[].close`·`volume`·`open/high/low`가 "종가·거래량·시가, 고가, 저가"로만 적혀 있었다. 실제 값은 공식 종가 x 원천 비율이고, 고가·저가는 애프터마켓을 포함하며, 거래량은 NXT를 뺀 KRX 하루 거래량이다(47·50차) | `API.md` 표 3줄 정정 |
| 2 | "종가 정의(43차 확인, **미해결**)"가 47차 교정 뒤에도 그대로였다 | 47차 교정·기본값 `official`을 앞에 적고 옛 표기를 "당시 미해결 -> 47차 교정"으로 정정 |
| 3 | "`refresh_data --close-basis official`(기본은 여전히 `source`)". 실제 기본값은 `official`이다(`bde8883`) | 정정 |
| 4 | `status.last_refresh`·시장 요약 `last_collection`이 `refresh_data`만 반영한다는 점은 적혀 있었다. 다만 그 결과 데이터 기준일보다 이를 수 있다는 점(11-03 vs 11-12)이 없었다 | 두 줄에 관찰 사실 추가 |
| 5 | 시각 규칙 "저장 시각(`collected_at` 등)은 UTC"와 실제가 달랐다. 41차 공개 API(시장 요약 `first_collected_at`, 뉴스·공시 목록 시각)는 KST `+09:00`을 준다(ETF `collected_at`은 UTC) | 응답은 그대로 두고 문서에 예외 명시 |
| 6 | `event-reactions.summary`가 OpenAPI에 타입 없는 `dict`다(문서·실제 응답에는 16개 키) | 기록만(스키마 타입 변경은 응답 계약 문서화 작업이라 보류). 프런트는 Zod로 필드를 정해 쓰고 있다 |
| 7 | 가격 이력 `close`가 OpenAPI에서는 `number \| null`, 문서·프런트는 `number`다. DB 열이 NOT NULL이라 실제 null은 없다 | 문서에 명시 |

필드 이름 차이(문서에 없는 필드, 응답에 없는 문서 필드)는 없었다. 대조기가 처음 보고한 후보(`disclosures`의 `last_collection` 하위 키, ETF `data_warnings[].kind`, `event-reactions.summary`)는 문서가 다른 표기로 이미 설명하고 있었다.

## 7. 데이터 기준일과 지연 상태

| 데이터 | 기준일(2026-11-15 20:00) | 지연 |
| --- | --- | --- |
| 주가·ETF·지수 | 2026-11-12 | 기대 거래일 11-13 대비 1거래일(`delayed`) |
| 환율 | 2026-11-02 | 평일 정책. 원천 날짜가 Yahoo 일봉 날짜다(56차) |
| 뉴스 | 2026-11-03 | `collection_lag_weekdays` 8, `stale=true` |
| 공시 | 2026-11-03(삼성전자 최신 접수 10-30) | 같음 |
| ETF 구성종목 | 2026-10-30 | `stale=true` |
| 마지막 `refresh_data` | 2026-11-03 20:03 | 그 뒤 데이터는 개별 수집 스크립트·47차 교정으로 들어왔다 |

## 8. 거래시간 판정 정합성

| 확인 | 결과 |
| --- | --- |
| 실제 서버, 지금 시각(일요일 20:00) | 시장 요약 `expected_date=2026-11-13`, `intraday_excluded=false` → 주말에 직전 거래일 종가를 기대함(맞음) |
| 서버가 쓰는 함수의 시각 주입 판정 | 재시작 작업 때 같은 실행 파일·작업 폴더·환경에서 확인했다. 11-19: 09:59:59 장 시작 전, 10:00 장중, 16:30 마감·확정 대기, 16:40 대조 뒤 확정. 11-18: 09:00 장중, 15:30 확정 대기, 15:40 확정. 12-31은 휴장 |
| 테스트 | 55~58차 달력·지수 확정·기준일 테스트가 단위 전체에 포함되어 통과(14절). 지수 대조 실패 시 저장하지 않고 기존 값을 유지하는 경로(`test_held_row_keeps_existing_confirmed_value` 등)도 포함 |

- 실행 중인 서버에 시각을 넣어 물을 경로는 없다. 공개 API는 모두 현재 시각으로 계산한다.
- 11-19 당일 실제 서버 확인 방법: 15:30~16:30에 `GET /api/market/overview`를 호출해 `expected_date=2026-11-18`·`intraday_excluded=true`인지 본다. 16:30 뒤에는 `expected_date=2026-11-19`인지 본다.

## 9. 프런트엔드 실제 사용 API

`lab-scale-frontend/frontend/src/api/http.ts`를 읽기만 했다. 수정하지 않았다(그 저장소의 커밋은 `77504c3`, 추적되지 않는 파일 2개가 있었는데 건드리지 않음).

- 공개 API 사용: companies, price-history(20·60·90거래일), event-reactions, discovery/trending, ETF holdings, status(푸터 기준일), disclaimer, realtime-price.
- 인증 API 사용: auth·research·watchlist·portfolio·diagnosis·performance(benchmark)·exposure(look_through)·daily briefings·history search.
- 쓰지 않는 공개 API: market/overview, 종목 news·disclosures, health.
- Zod 스키마는 `z.object`(strict 아님)다. 모르는 키는 버린다. React Query 캐시는 status 5분, 가격 이력 60초, 면책 문구 무한이다.

| 우선 점검 항목 | 결과 |
| --- | --- |
| 1. 시장 요약 지수 기준일 | 프런트는 시장 요약 API를 쓰지 않는다. 시장 카드는 일일 브리핑 market 영역(로그인 필요)을 읽는다(51차 기록과 같음) |
| 2. 가격 이력 최대 기간 | 백엔드 365, 프런트 최대 90. `available_count` 등 3개 필드는 프런트 스키마에 없다 |
| 3. 고가·저가 기준 | 프런트 툴팁에 애프터마켓 포함 표시가 없다 |
| 4. 거래량 기준 | KRX 하루(NXT 제외). 프런트 문구는 "거래량"뿐이다 |
| 5. 지연·확정 여부 | 푸터는 `sources.stock_price.latest_date`(맞음)와 `market_index.stale`, `last_refresh.ok`를 쓴다. 확정 대기 표시는 없다 |
| 6. 장 상태 | 화면 표시가 없고 API에도 필드가 없다(제안: 10절) |

## 10. API 계약 변경 제안

`docs/FRONTEND_API_HANDOFF_60.md` 7절. 요약:
- 시장 요약(과 일일 브리핑 market)에 `market_session`(`phase`·`regular_open`·`regular_close`·`confirmation_after`·`special`·`calendar_known`)을 추가한다.
- `indices[].close_status`(`confirmed`·`confirmation_pending`·`verification_held`·`collection_failed`·`delayed`)를 추가한다. 선택 필드 추가뿐이고 기존 필드는 그대로다.
- `confirmation_pending`은 시간 조건에서 생기는 정상 대기다. 수집 실패·검증 보류와 구분한다.
- `verification_held`를 구분하려면 수집 때 지수 보류 사유를 저장해야 한다(지금은 출력에만 있음, DB 변경 승인 필요).
- 51차 `indices[].confirmation_pending`(bool) 제안과 둘 중 하나로 합의한다.

**이번에 구현하지 않았다**(합의 전).

## 11. 프런트엔드 담당자 확인 질문

`docs/FRONTEND_API_HANDOFF_60.md` 8절의 7개:
1. 차트 180·365 기간
2. 시장 카드 공개 API 전환
3. 장 상태 표시 여부와 방식
4. 검증 보류 구분(DB 변경 필요)
5. 고가·저가·거래량 문구
6. 뉴스·공시 목록 사용 계획
7. UTC 시각 표시 처리

## 12. 개발 DB 불변 확인

READ ONLY 연결로 작업 전후 같은 값이다:

| 항목 | 작업 전 | 작업 후 |
| --- | --- | --- |
| Alembic | `c7e2a9f4d158` | `c7e2a9f4d158` |
| `stock_price` 행 수 / md5 | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` |
| `market_index` 행 수 / md5 | 1,480 / `3ba6fedca411ebff119bef854f227f43` | 1,480 / `3ba6fedca411ebff119bef854f227f43` |
| `stock_price_session` | 없음 | 없음 |
| `users` | 2 | 2 |

- 실제 서버 호출은 모두 GET이고 조회만 했다.
- offline 평가는 개발 DB 읽기 전용 세션(`default_transaction_read_only=on`)으로 돌렸다.
- 통합 테스트는 새 격리 DB `ai_investment_api60_it`를 썼다(개발 DB `pg_dump` 1번).
- 기존 시험 DB는 지우지 않았다.

## 13. 테스트 및 CI 결과

| 테스트 | 결과 |
| --- | --- |
| `tests/test_api_contract_60.py`(신규 3개) | **3 통과** |
| 기존 API 계약 테스트 `test_api_contract_round37.py`(OpenAPI 타입 파일 최신 여부 포함) | 통과(73) |
| 단위 전체(`-m "not slow and not integration"`, `DATABASE_URL` 닫힌 포트) | **6,448 통과**(58차 6,445 + 신규 3), 실패 0 |
| 통합 전체(`-m "integration and not slow"`, 격리 DB `ai_investment_api60_it`) | **697 통과, 10 건너뜀**, 실패 0(건너뜀은 55~58차와 같다: 마이그레이션 왕복 9, 데이터 조건 1) |
| 품질 평가 `run_quality --mode deterministic` | **244/244**(제품 경로 142, 판정기 102). 58차와 같다 |
| offline 평가 `run_eval --mode offline`(개발 DB 읽기 전용) | **실패 11건**(33차부터 알려진 엄격 모드 거절 `ent_*`). 58차 결과와 문항·내용이 같다 |
| GitHub Actions | push 뒤 결과를 최종 보고에 적는다 |

신규 테스트가 검사하는 것:
1. 전달 문서 부록 A의 계약 JSON이 실제 OpenAPI와 같다(엔드포인트 12개의 필드·타입·null·필수·쿼리 범위·오류 코드). 필드를 바꾸면 실패한다. 문서 사본에서 필드를 바꿔 감지됨을 확인했다.
2. 공개 GET 12개가 있고 인증 의존성이 없다.
3. 가격 이력 `days` 1~365(기본 90)와 핵심 쿼리 상한.

## 14. 커밋 해시와 push 여부

- 커밋 대상:
  - `backend/API.md`(설명 정정, 응답 변경 없음)
  - `backend/tests/test_api_contract_60.py`
  - `docs/FRONTEND_API_HANDOFF_60.md`, 이 문서
- 커밋 해시·push·CI 결과는 최종 보고에 적는다(이 문서를 담은 커밋이라 여기에 쓸 수 없다). main 병합은 하지 않는다.
