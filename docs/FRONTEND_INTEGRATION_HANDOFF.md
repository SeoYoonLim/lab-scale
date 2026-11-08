# 프런트엔드 통합 연동 핸드오프

2026-11-08(37차) 백엔드 `feature/backend` 기준. 이 문서 하나로 연동을 시작하고, 기능별 세부는 링크한 문서를 본다.
예시 JSON은 개발 DB에서 임시 사용자로 실제 호출해 받은 응답을 줄인 것이다(배열은 앞 1~2개만, 긴 문장은 `...`). 필드를 추측해 만들지 않았다.
TypeScript 타입 전체는 OpenAPI에서 자동 생성한 **`docs/frontend-api-types.ts`**(139개 타입, 백엔드 테스트가 최신 여부를 확인)를 쓴다.

## 0. 먼저 읽을 것

1. **이미 연동된 API는 그대로 동작한다.** 프런트 `2fe2022`의 zod 스키마(`frontend/src/api/schemas.ts` 33개)가 요구하는 필드는 모두 백엔드 응답에 있다(37차 대조).
2. **새로 붙일 API**: 포트폴리오 성과·벤치마크, ETF 실질 노출(look-through), 일일 브리핑 4개, 통합 기록 검색, 계정 설정(언어·시간대), 백테스트, ETF 구성종목.
3. **오류는 두 모양뿐**: 422는 `{"detail": [{loc, msg, type, ...}]}`, 나머지는 `{"detail": "사용자에게 보여 줄 문장"}`.
4. **숫자는 계산하지 말고 그대로 쓴다.** 금액은 원(number), `*_pct`는 퍼센트 값(-3.48 = -3.48%), 날짜는 `YYYY-MM-DD`, 시각은 오프셋이 붙은 ISO 8601.
5. **시세는 종가 기준이고 늦을 수 있다.** 각 응답의 `stale`·`*_lag_business_days`·`stale_notice`·`market_data_status`를 화면에 보여 준다(7절).
6. **상태(`status`)가 있는 응답은 200이어도 "계산 못 함"일 수 있다.** 오류(4xx)와 상태값을 구분해 화면을 나눈다(8절).

## 1. 프로젝트 구조

| 위치 | 내용 |
|---|---|
| `backend/app/main.py` | FastAPI 앱, 공통 오류 처리·CORS·보안 헤더·요청 크기 제한, OpenAPI 오류 응답 선언 |
| `backend/app/api/*.py` | 라우터(인증 `auth`·`oauth`, `research`, `stocks`, `companies`, `discovery`, `watchlist`, `portfolio`, `performance`, `exposure`, `briefing`, `daily_briefing`, `history`, `backtest`, `etf`, `status`, `disclaimer`) |
| `backend/API.md` | 엔드포인트별 상세 명세(가장 자세함) |
| `docs/frontend-api-types.ts` | 응답·요청 TypeScript 타입(자동 생성) |
| `docs/FRONTEND_HANDOFF_*.md` | 기능별 핸드오프(브리핑·노출·성과·다국어/기록) |
| `docs/DATA_REFRESH_MONITORING.md`, `docs/DATA_QUALITY_AND_TRADING_CALENDAR.md` | 데이터 갱신·품질·거래일 달력(운영 문서, 화면에 필요한 부분은 7절에 요약) |

## 2. 로컬 실행 환경

| 항목 | 값 |
|---|---|
| 백엔드 | `http://localhost:8000` (uvicorn, `backend`) |
| 프런트 | `http://localhost:5173` (Vite). `vite.config.ts`의 프록시가 `/api` -> `http://localhost:8000` |
| DB | Docker `ai_investment_db`(PostgreSQL, 호스트 포트 15432) |
| AI 모델 | Ollama `127.0.0.1:13434`, `llama3.1:8b`(답변), `bge-m3`(임베딩). 백엔드 `.env`의 `OLLAMA_HOST` |
| API 문서 | 개발 환경에서 `http://localhost:8000/docs`, `/openapi.json`(운영 환경에서는 꺼짐) |
| CORS | 기본 허용 origin `http://localhost:5173`, `http://127.0.0.1:5173`(백엔드 `CORS_ALLOW_ORIGINS`). 쿠키를 쓰지 않는다. 노출 헤더 `X-Total-Count` |

## 3. Base URL과 공통 규칙

- 프런트 코드: `const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'` + 경로(`/portfolio` 등). 개발 서버에서는 프록시로 8000에 간다.
  프록시 없이 직접 부르려면 `VITE_API_BASE_URL=http://localhost:8000/api`.
- 요청·응답은 JSON(UTF-8). 인증이 필요한 요청은 `Authorization: Bearer <access_token>` 헤더.
- 응답 헤더 `Cache-Control: no-store`(인증 요청·`/api/auth`). 캐시하지 말고 React Query 등에서 상태로 관리한다.
- **null과 빈 배열**: 목록 필드는 데이터가 없어도 `[]`이고 `null`이 아니다(`holdings`, `items`, `companies`, `etfs`, `warnings` 등).
  값 필드의 `null`은 "계산할 수 없음/데이터 없음"이다(0과 다르다. 예: 수익률 `null`은 "-"로, `0.0`은 "0.00%"로).
- **숫자**: 금액은 원 단위 number(소수가 있을 수 있음: 평균가·평가액), 수량·건수는 정수 number, `*_pct`는 퍼센트 값, `*_pp`는 %포인트.
  거래량 배수(`volume_ratio`)는 배수(2.5 = 2.5배). 숫자를 문자열로 주는 필드는 종목코드(`ticker`, `code`)뿐이다(앞자리 0 보존).
- **날짜·시각**: 날짜는 `"2026-11-03"`. 시각은 `"2026-11-08T19:40:55.65+09:00"`(새로 계산한 `generated_at` 등, KST) 또는
  `"2026-11-08T10:40:55.08+00:00"`(저장 시각 `created_at`·`executed_at`·`added_at`, UTC). 둘 다 `new Date()`로 정확히 읽히므로 화면에서 사용자 시간대로 바꿔 보여 준다.
  기록 목록은 사용자 시간대 기준 `local_date`·`local_time`·`date_group`도 준다.
- **언어(`locale`)**: `ko-KR`(기본)·`en-US`·`ja-JP`·`zh-CN`. 요청 `locale` > 계정 설정(`/api/auth/preferences`) > `ko-KR`.
  GET에서 지원하지 않는 값(예: `fr-FR`)은 오류 없이 `ko-KR`로 처리하고 응답의 `locale`이 실제 언어다. 계정 설정 저장(PUT)만 422.
  구조는 언어와 관계없이 같고 문장 필드만 바뀐다. 종목명·기사/공시 제목은 원문이다.
- **레이트 리밋(429)**: 응답 헤더 `Retry-After`(초). 기본값: 모든 `GET /api/*` IP당 분당 600, AI 질문 사용자당 분당 6·동시 1, 주문 분당 30,
  일일 브리핑 생성 분당 3, 백테스트 분당 10, 로그인 IP당 분당 10, 가입 IP당 시간당 10.

## 4. 로그인과 인증

```http
POST /api/auth/signup   {"username": "alice01", "password": "..."}  -> 201
POST /api/auth/login    {"username": "alice01", "password": "..."}  -> 200
```

```json
{ "user": { "id": 34110, "username": "alice01" }, "access_token": "<JWT>", "token_type": "bearer" }
```

- 토큰 유효 시간 24시간. 만료·형식 오류·로그아웃·비밀번호 변경(모든 기존 토큰 폐기) 뒤에는 **401** `{"detail": "인증이 필요합니다. 다시 로그인해주세요."}`.
  401이면 토큰을 지우고 로그인 화면으로 보낸다(현재 프런트 `doFetch`가 이미 한다). 403은 쓰지 않는다.
- 다른 사용자의 자원(리포트·진단·브리핑 id)은 **404**다(존재 여부를 드러내지 않는다).
- 인증이 필요한 API 33개와 공개 API 목록은 5절 표와 `backend/API.md` 상단 표. 백엔드 테스트가 두 표와 실제 라우트를 대조한다.
- 소셜 로그인(구글·네이버): `GET /api/auth/oauth/providers`로 켜진 공급자를 보고, `/api/auth/oauth/{provider}/login`으로 브라우저를 이동시킨 뒤
  콜백이 프런트로 돌려준 1회용 코드를 `POST /api/auth/oauth/exchange`로 토큰과 바꾼다(`backend/API.md` "소셜 로그인").
- 계정 설정: `GET/PUT /api/auth/preferences` `{"preferred_locale": "en-US", "preferred_timezone": "America/New_York"}`.

## 5. API 전체 목록(47개 경로, 53개 메서드)

"프런트 연동"은 프런트 `2fe2022`의 `src/api/http.ts`가 이미 부르는지다. 오류 코드는 OpenAPI에 선언된 것(503은 모두 가능, 422는 파라미터가 있으면 가능).

| 메서드 | 경로 | 인증 | 용도 | 주요 오류 | 프런트 연동 |
|---|---|---|---|---|---|
| POST | `/api/auth/signup` | - | 가입 | 409·422·429 | 됨 |
| POST | `/api/auth/login` | - | 로그인 | 401·422·429 | 됨 |
| GET | `/api/auth/me` | 필요 | 내 정보(이메일 인증 상태 포함) | 401 | 됨 |
| DELETE | `/api/auth/me` | 필요 | 회원 탈퇴 요청(비밀번호 확인) | 400·401·429 | 아직 |
| GET·PUT | `/api/auth/preferences` | 필요 | 언어·시간대 설정 | 401·422 | 아직 |
| POST | `/api/auth/logout` | 필요 | 로그아웃(토큰 폐기) | 401·429 | 아직 |
| POST | `/api/auth/change-password` | 필요 | 비밀번호 변경(새 토큰 발급) | 400·401·429 | 아직 |
| PUT | `/api/auth/email` | 필요 | 이메일 등록·변경 | 400·401·409·429 | 아직 |
| POST | `/api/auth/resend-verification` | 필요 | 인증 메일 재발송 | 400·401·429 | 아직 |
| POST | `/api/auth/verify-email`, `/find-username`, `/forgot-password`, `/reset-password`, `/delete-account/confirm` | - | 계정 복구·탈퇴 확인 | 400·422·429 | 아직 |
| GET | `/api/auth/oauth/providers`, `/{provider}/login`, `/{provider}/callback`; POST `/exchange` | - | 소셜 로그인 | 400·404·429 | 아직 |
| GET | `/api/disclaimer?locale=` | - | 면책 문구 | - | 됨 |
| GET | `/api/status` | - | 데이터 기준일(푸터) | - | 됨 |
| GET | `/api/health` | - | 서버·DB·모델 상태 | - | 아직(운영용) |
| POST | `/api/research` | 필요 | AI 질문(리서치 에이전트) | 401·404·422·429·502·503 | 됨 |
| GET | `/api/research` | 필요 | 내 리포트 목록 | 401·422 | 됨 |
| GET·DELETE | `/api/research/{report_id}` | 필요 | 리포트 한 건(`view=record`면 번역 상태·원문) / 삭제 | 401·404 | 됨 |
| GET | `/api/stocks/{ticker}/realtime-price` | - | 실시간(장중)·종가 폴백 | 404 | 됨 |
| GET | `/api/stocks/{ticker}/price-history?days=` | - | 일별 시세(차트) | 404·422 | 됨 |
| GET | `/api/stocks/{ticker}/event-reactions` | - | 공시·뉴스 뒤 주가 반응 | 404·422 | 됨 |
| GET | `/api/companies` | - | 종목 검색·목록(`X-Total-Count`) | 422 | 됨 |
| GET | `/api/discovery/trending` | - | 상승·하락·거래량 급증 | 422 | 됨 |
| GET·POST | `/api/watchlist` | 필요 | 관심종목 목록·추가 | 401·404·409 | 됨 |
| DELETE | `/api/watchlist/{ticker}` | 필요 | 관심종목 삭제 | 401·404 | 됨 |
| GET | `/api/portfolio` | 필요 | 잔고·보유(첫 호출에 계좌 생성) | 401 | 됨 |
| GET | `/api/portfolio/trades` | 필요 | 체결 내역 | 401·404·422 | 됨 |
| POST | `/api/portfolio/orders` | 필요 | 모의 주문(종목 엄격 매칭) | 400·401·404·422·429 | 됨 |
| POST | `/api/portfolio/reset` | 필요 | 모의투자 초기화(`{"confirm": true}`) | 400·401 | 됨 |
| POST | `/api/portfolio/diagnosis` | 필요 | 포트폴리오 진단(LLM, 저장) | 400·401·503 | 됨 |
| GET | `/api/portfolio/diagnosis/history` | 필요 | 진단 이력 | 401·422 | 됨 |
| GET·DELETE | `/api/portfolio/diagnosis/{id}` | 필요 | 진단 한 건 / 삭제 | 401·404 | 됨 |
| GET | `/api/portfolio/diagnosis/{id}/compare` | 필요 | 이전 진단과 비교 | 401·404 | 됨 |
| GET | `/api/portfolio/performance` | 필요 | 성과(`view=benchmark`면 지수 비교) | 401·422 | **아직** |
| GET | `/api/portfolio/exposure` | 필요 | 노출(`view=look_through`면 ETF 실질 노출) | 401·422 | **아직** |
| POST | `/api/backtest` | 필요 | 백테스트 | 400·401·404·422·429 | 아직 |
| GET | `/api/briefing` | 필요 | (예전) 채팅용 브리핑 요약. 새 화면은 아래 일일 브리핑을 쓴다 | 401 | 아직 |
| POST | `/api/briefings/daily/generate` | 필요 | 오늘 브리핑 만들기(있으면 저장본) | 401·409·422·429 | **아직** |
| GET | `/api/briefings/daily/current` | 필요 | 오늘 브리핑(없으면 404) | 401·404 | **아직** |
| GET | `/api/briefings/daily/history` | 필요 | 날짜별 브리핑 목록 | 401·422 | **아직** |
| GET | `/api/briefings/daily/{briefing_id}` | 필요 | 저장된 브리핑 한 건 | 401·404 | **아직** |
| GET | `/api/history/search` | 필요 | 리서치·진단 통합 기록 검색 | 401·422 | 아직 |
| GET | `/api/etf/{ticker}/holdings` | - | ETF 구성종목·비중 | 404·422 | 아직 |

## 6. 오류·예외 응답 처리

| 상태 | 본문 | 뜻 | 화면 처리 |
|---|---|---|---|
| 400 | `{"detail": "잔고가 부족합니다(필요 22,200,000,000원, 보유 10,000,000원)."}` | 처리할 수 없는 요청(잔고·수량 부족, 잘못된 토큰·링크) | detail 그대로 토스트 |
| 401 | `{"detail": "인증이 필요합니다. 다시 로그인해주세요."}` | 토큰 없음·만료·폐기 | 토큰 삭제 -> 로그인 |
| 404 | `{"detail": "오늘의 브리핑이 아직 없습니다. POST /api/briefings/daily/generate로 만들 수 있습니다."}` | 없는 자원(남의 자원 포함), 없는 종목 | 빈 화면/안내. 브리핑 current는 "만들기" 버튼 |
| 409 | `{"detail": "..."}` | 이미 있음(관심종목·이메일), 하루 브리핑 버전 한도 | detail 표시 |
| 413 | `{"detail": "..."}` | 본문이 너무 큼 | 입력 줄이기 안내 |
| 422 | `{"detail": [{"type": "literal_error", "loc": ["query", "sort"], "msg": "Input should be 'id', 'name' or 'ticker'", "input": "xxx"}]}` | 파라미터·본문 형식 오류. `loc[0]`은 `query`·`body`·`path` | 개발 중 버그로 보고, 사용자 입력이면 `msg`를 필드 옆에. `/api/auth`의 422는 `input`을 빼고 준다(비밀번호 보호) |
| 429 | `{"detail": "..."}` + `Retry-After` | 요청 한도 | n초 뒤 다시 |
| 500 | `{"detail": "서버 오류가 발생했습니다."}` | 처리되지 않은 오류(내부 정보 없음) | 일반 오류 |
| 502 | `{"detail": "AI 모델 서버가 오류를 반환했습니다. 잠시 후 다시 시도해주세요."}` | Ollama 응답 오류 | 다시 시도 |
| 503 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` 또는 AI 모델 연결 실패 | 일시 장애 | 다시 시도 |

오류 본문에는 내부 경로·SQL·접속 정보·스택트레이스가 없다(백엔드 테스트 `test_unhandled_errors_do_not_leak_internals`).
AI 질문은 LLM이 느리거나 실패해도 대개 **200 + 대체 답**(코드가 만든 요약·지연 안내)이다. 프런트 타임아웃은 리서치 60초·진단 90초 이상을 권장한다.

**엄격한 종목 매칭**: 주문(`/api/portfolio/orders`)은 정확한 종목명·종목코드만 받는다. 오타·음차(`엘지전자`)는 404
`"정확한 종목을 찾지 못했습니다. '엘지전자'에 해당하는 종목명 또는 종목코드를 정확히 입력해주세요."`. 반면 관심종목 추가·시세 조회는 유사 이름을 받아
가장 가까운 종목으로 처리한다(`엘지전자` -> `LG전자`). **응답의 `company_name`을 화면에 보여 줘서 사용자가 확인하게 한다.**

## 7. 데이터 최신성 표시

시세는 실시간이 아니라 **DB에 저장된 종가**다(실시간은 `/realtime-price` 하나). 데이터 갱신이 늦으면 모든 숫자가 그 날짜 기준이다.
2026-11-08 개발 DB는 마지막 종가가 2026-11-03이라 기대 거래일 11-06 대비 3영업일 지연 상태다.

| 화면 | 필드 | 표시 |
|---|---|---|
| 공통 푸터 | `GET /api/status`의 `sources.*.latest_date`, `market_index.stale`, `is_realtime`, `note` | "데이터 기준일 2026-11-03(종가)". `stale`이면 노란 표시 |
| 성과 | `data_date`, `expected_date`, `data_lag_business_days`, `warnings[kind=data_delayed]` | lag ≥ 1이면 경고 문장(`warnings[].message`) |
| 노출(look-through) | `valuation_date`, `price_lag_business_days`, `stale`, `stale_notice`, `etf_holdings_stale`, `data_warnings[kind=older_price]` | `stale`이면 `stale_notice` 띠, 종목별 `older_price` 배지 |
| 일일 브리핑 | `data_as_of`, `market_data_status`(`latest`·`delayed`·`stale`·`no_data`), 목록의 `headline` | `delayed`·`stale`이면 headline을 경고 띠로 |
| AI 답변 | 답 본문 | 답에 "데이터 기준일 … 종가 … N영업일 지연" 문장이 들어 있다. 따로 처리하지 않아도 된다 |
| ETF 구성 | `as_of_date`, `stale`, `stale_notice` | 오래된 구성 배지 |

- **영업일은 KRX 거래일 달력 기준**(주말·공휴일·대체공휴일·연말 휴장 제외, 2026-11-08부터). 공휴일 다음 날의 정상 데이터는 지연이 아니다.
  달력은 2023-10-30~2026-12-31을 다루고 2026-12-31(연말 휴장)은 미확정이다. 범위 밖이 섞이면 답에 "KRX 휴장일 달력으로 확인할 수 없는 날 N일…" 문장이 붙는다.
- **기간 질문은 다르다**: "지난 한 달" 같은 AI 질문이 읽는 시세 행 수는 기존 평일 근사(공휴일 포함)를 그대로 쓴다. 최신성(지연) 판단은 거래일,
  기간 질문의 조회 범위는 평일이라 휴장일이 낀 기간은 그 수만큼 앞 거래일이 더 들어간다(37차 결정: 바꾸지 않음, 11절).
- 데이터 품질 격리(36차)는 사용자 API가 없다. 격리된 데이터는 저장되지 않으므로 화면에는 "그 날짜 데이터 없음/지연"으로만 보인다.
  `quality_quarantined`·`partial`(수집 단계 상태)은 운영 CLI(`python -m scripts.refresh_data --status`) 용어이고 API 응답에는 없다.

## 8. 상태값(정상·부분·계산 불가·대체) 처리

| API | 필드 | 값 | 처리 |
|---|---|---|---|
| 성과 | `status` | `ok`, `partial`(일부 종목 시세 없음), `no_account`, `no_trades`, `pending_only`(오늘 체결은 아직 종가 없음), `no_prices`, `history_unavailable`(보유와 체결 기록 불일치) | `ok`·`partial`만 숫자 표시, 나머지는 `message` 안내. `partial`이면 `warnings` |
| 성과 비교 | `comparison.status` | `ok`, `partial`, `no_data`, `insufficient_overlap`, `portfolio_unavailable` | `ok`·`partial`만 그래프 |
| 노출 | `status` | `ok`, `empty`(보유 없음), `no_prices` | `empty`·`no_prices`는 `message` |
| 노출 ETF | `etfs[].status` | `decomposed`, `not_decomposed`(구성 데이터 없음·미지원 운용사·비중 이상) | `not_decomposed`면 "구성 정보 없음" 배지, 금액은 `summary.undecomposed_etf_amount` |
| 일일 브리핑 | `section_status.*` | `ok`, `partial`, `no_data`, `empty` | 섹션별 빈 상태 |
| 일일 브리핑 | `summary_source` | `llm`(숫자 검증 통과한 AI 요약), `code`(코드 요약: LLM 실패·끔) | `content.summary.note` 표시 |
| AI 답변(다국어) | `GET /api/research/{id}?view=record`의 `translation_status` | `localized`(언어별 템플릿), `translated`, `fallback_summary`(번역 검증 실패: 그 언어 안내 + 데이터 요약), `fallback_original`(2026-11-07 이전 기록) | `fallback_summary`면 "원문(한국어) 보기"로 `original_answer`. **POST 응답에는 이 값이 없다(11절)** |

## 9. 페이지네이션·검색·정렬

| API | 방식 | 기본·최대 | 정렬(`sort`/`order`) | 검색·필터 | 응답 |
|---|---|---|---|---|---|
| `GET /api/research` | `limit`·`offset` | 20·100 | `created_at`\|`id`, `asc`\|`desc`(기본 desc) | `q`(50자), `date_from`·`date_to`, `timezone`, `analysis_type`, `view=record` | `{total, has_next, total_pages, items}` |
| `GET /api/portfolio/trades` | `limit`·`offset` | 50·200 | `executed_at`\|`amount`\|`price`\|`quantity`\|`ticker`, 기본 `executed_at desc` | `ticker` | `{total, limit, offset, has_next, total_pages, items}` |
| `GET /api/portfolio/diagnosis/history` | `limit`·`offset` | 20·100 | `created_at`\|`id`\|`total_asset`\|`holdings_count` | `q`, 날짜, `timezone`, `view=record` | `{total, limit, offset, has_next, total_pages, items}` |
| `GET /api/companies` | `limit`·`offset` | 50·300 | `id`\|`name`\|`ticker`, 기본 `id asc` | `q`(100자), `asset_type` | **배열** + 헤더 `X-Total-Count` |
| `GET /api/briefings/daily/history` | `page`·`size` | 1·20, size 최대 100 | 최신순 고정 | `date_from`·`date_to`, `timezone`, `latest_only`(기본 true) | `{items, page, size, total, has_next, total_pages, ...}` |
| `GET /api/history/search` | `page`·`size` | 1·20, size 최대 100 | `created_at,desc`\|`created_at,asc` | `type`(`all`·`research`·`diagnosis`), `keyword`, 날짜, `timezone` | `{..., total, total_pages, has_next, counts, items}` |
| `GET /api/discovery/trending` | `limit` | 10·50 | 분류 고정 | `category`(`gainers`·`losers`·`volume_surge`), `asset_type` | `{category, ..., items}` |
| `GET /api/etf/{ticker}/holdings` | `limit` | 10·50 | 비중 순 | - | `{..., items}` |

- 범위를 넘는 페이지·offset은 오류가 아니라 `items: []`, `has_next: false`(total은 그대로).
- 허용되지 않는 정렬·범위 밖 limit·잘못된 날짜(`2026-13-01`)·`date_from > date_to`·모르는 시간대는 422.
- 거래량 상위 순위(`volume_top`)는 AI 질문("거래량 많은 종목")에서만 쓰고 `/api/discovery/trending`의 category에는 없다(422).

## 10. 기능별 연동

### 10-1. ETF 실질 노출(look-through) — 세부 `docs/FRONTEND_HANDOFF_ETF_EXPOSURE.md`

`GET /api/portfolio/exposure?view=look_through&limit=10&locale=ko-KR` (기본 `view=default`는 예전 노출 응답)

```json
{
 "status": "ok", "message": null, "valuation_date": "2026-11-03", "expected_date": "2026-11-06", "price_lag_business_days": 3,
 "stale": true, "stale_notice": "평가 기준 종가는 2026-11-03이며 기대 거래일(2026-11-06)보다 3영업일 지연됐습니다.", "etf_holdings_stale": true,
 "summary": {"total_asset": 10000000.0, "cash_weight_pct": 91.3, "direct_amount": 666000.0, "via_etf_amount": 203666.79,
             "undecomposed_etf_amount": 0.0, "over_allocated_amount": 6.79, "look_through_top1": {"name": "삼성전자", "code": "005930", "total_weight_pct": 7.27}},
 "companies": [{"rank": 1, "name": "삼성전자", "ticker": "005930", "exposure_type": "both", "direct_weight_pct": 6.66, "via_etf_weight_pct": 0.61,
                "total_weight_pct": 7.27, "via_etfs": [{"ticker": "069500", "name": "KODEX 200", "constituent_weight_pct": 34.19, "asset_weight_pct": 0.61}]}],
 "etfs": [{"ticker": "069500", "name": "KODEX 200", "status": "decomposed", "holdings_as_of": "2026-10-30", "holdings_lag_business_days": 5, "holdings_stale": true}],
 "sector": {"status": "unavailable", "covered_weight_pct": 0.0, "items": [], "message": "업종 분류 데이터가 없어 업종별 집중도는 계산하지 않았습니다."},
 "flags": [],
 "data_warnings": [{"ticker": "069500", "name": "KODEX 200", "kind": "etf_holdings_stale", "date": "2026-10-30",
                    "message": "KODEX 200의 구성종목 기준일은 2026-10-30입니다. 현재 구성과 다를 수 있습니다."}]
}
```

- 비중(`*_weight_pct`)은 **총자산(현금 포함) 대비**다. `exposure_type`: `direct`(직접 보유만)·`etf`(ETF 통해서만)·`both`.
- 업종(`sector`)은 대부분 미제공이다(DB에 업종 분류가 거의 없다). `sector.status`로 "업종 정보 부족"을 보여 준다.
- 구성 데이터가 없는 ETF(KODEX·TIGER 외 운용사 포함)는 `etfs[].status = "not_decomposed"`, 금액은 `summary.undecomposed_etf_amount`.
- 진단 비교의 노출 변화(`exposure_changes`)는 `GET /api/portfolio/diagnosis/{id}/compare`(세부 문서 6절).

### 10-2. 포트폴리오 성과·벤치마크 — 세부 `docs/FRONTEND_HANDOFF_PORTFOLIO_PERFORMANCE.md`

`GET /api/portfolio/performance?view=benchmark&period=1m&benchmark=KS11&locale=ko-KR`
(`period`: `1m`·`3m`·`6m`·`1y`·`all`, `benchmark`: `KS11`·`KQ11`·`KOSPI`·`KOSDAQ`)

```json
{
 "status": "pending_only", "message": "체결된 주문이 아직 종가에 반영되지 않았습니다. 다음 거래일 종가가 수집되면 계산합니다.",
 "history_verified": true, "period": "1m", "start_date": null, "end_date": "2026-11-03", "data_date": "2026-11-03", "expected_date": "2026-11-06",
 "data_lag_business_days": 3, "benchmark_code": "KS11", "benchmark_name": "코스피",
 "summary": {"current_cash": 9130340.0, "start_value": null, "end_value": null, "profit_loss": null, "period_return_pct": null, "cumulative_return_pct": null},
 "metrics": {"max_drawdown": null, "annualized_volatility_pct": null, "trading_days": 0, "sample_rules": {"volatility_min_returns": 20, "annualized_return_min_returns": 252}},
 "series": {"points": [], "total_points": 0, "sampled": false, "max_points": 400},
 "comparison": {"status": "portfolio_unavailable", "points": [], "benchmark_name": "코스피", "message": "포트폴리오 성과를 계산하지 못해 지수와 비교하지 않았습니다."},
 "warnings": [{"kind": "data_delayed", "lag": 3, "message": "마지막 종가는 2026-11-03이며 기대 거래일(2026-11-06)보다 3영업일 지연됐습니다. 오늘 시장 상황이 아닙니다."},
              {"kind": "pending_trades", "count": 3, "message": "마지막 종가일(2026-11-03) 뒤에 체결된 주문 3건은 아직 반영하지 않았습니다."}],
 "disclaimer": "거래일 종가 기준의 가격 수익률입니다. ..."
}
```

- 수익률·초과수익률·공통 거래일 처리·`history_unavailable`의 정확한 의미와 `ok` 상태 예시는 세부 문서 2·4절. 계산 불가 상태에서도 200이다.
- 오늘 체결한 주문은 다음 거래일 종가가 수집될 때까지 `pending_only`다(시세가 종가 기준이라서).

### 10-3. 일일 브리핑 — 세부 `docs/FRONTEND_HANDOFF_DAILY_BRIEFING.md`

1. 화면 진입: `GET /api/briefings/daily/current` -> 404면 "오늘 브리핑 만들기" 버튼.
2. 만들기: `POST /api/briefings/daily/generate` `{"use_llm": true}` -> 새로 만들면 **201**(`created: true`), 이미 있으면 **200** 저장본(`created: false`).
   `{"regenerate": true}`면 새 버전(하루 한도를 넘으면 409). AI 요약이 몇 초 걸린다(`use_llm: false`면 코드 요약만, 빠름).
3. 지난 브리핑: `GET /api/briefings/daily/history?page=1&size=20` -> 항목의 `detail_path`(`/api/briefings/daily/{id}`)로 저장본 그대로 조회(다시 계산하지 않음).

```json
{
 "id": 259, "version": 1, "local_date": "2026-11-08", "timezone": "Asia/Seoul", "locale": "ko-KR", "generated_at": "2026-11-08T19:41:01.56+09:00",
 "data_as_of": "2026-11-03", "market_data_status": "delayed", "summary_source": "code",
 "section_status": {"risk": "ok", "market": "ok", "holdings": "ok", "checklist": "ok", "watchlist": "ok"},
 "tickers": [{"name": "삼성전자", "scope": "holding", "ticker": "005930"}],
 "sources": [{"kind": "news", "title": "코스피, 3.59% 하락…삼전 4.2%·하이닉스 5.4%↓", "date": "2026-11-03T19:18:00+09:00", "ticker": "005930", "url": "https://..."}],
 "content": {"title": "2026-11-08 일일 투자 브리핑", "summary": {"text": "2026-11-03 종가 기준 브리핑입니다. ...", "source": "code", "note": "코드가 계산 결과로 만든 요약입니다."},
             "sections": {"risk": {"status": "ok", "flags": [{"code": "CASH_HIGH", "text": "현금 비중이 91.3%로 기준(70%) 이상입니다.", "value": 91.3, "threshold": 70.0}]}, "...": "..."}},
 "original_content": null, "date_group": "today", "created": true
}
```

- `content`는 OpenAPI에서 형식이 열린 객체(`Record<string, unknown>`)다. 섹션 구조는 세부 문서 3·4절을 따른다.
- 사용자마다 분리된다(남의 브리핑 id는 404). 데이터가 일부 없으면 섹션별 `section_status`가 `no_data`·`empty`이고 브리핑 자체는 만들어진다.
- 예전 `GET /api/briefing`(채팅 화면용 요약)과 다르다. 새 화면은 위 4개를 쓴다.

### 10-4. AI 질문(리서치)

`POST /api/research` `{"question": "삼성전자 현재가 알려줘", "previous_report_id": null, "locale": "en-US"}`

```json
{
 "answer": "삼성전자: close KRW 222,000 on 2026-11-03. Daily change -3.48%, volume 13,338,444 shares.\n\nData as of: 2026-11-03 close (a stored closing price, not a real-time quote). The last price (2026-11-03) is 3 business days behind the expected trading day (2026-11-06), so it is not the latest price. ...",
 "used_tools": ["stock_tool"], "sources": [], "report_id": 40030, "previous_report_id": null,
 "disclaimer": "This service provides simulated trading and AI research ..."
}
```

- 응답 키는 이 6개로 고정이다(백엔드 테스트가 고정). `answer`는 마크다운 문단(줄바꿈 `\n\n`)이다.
- 목표주가·확정 수익·"무조건 오르냐" 같은 요청은 거절 안내 + 대신 볼 수 있는 질문 + 참고 시세로 답한다(35차).
- 후속 질문은 `previous_report_id`로 잇는다(본인 리포트만, 남의 id는 404).

## 11. TypeScript 타입

전체: `docs/frontend-api-types.ts`(자동 생성, `cd backend && python -m scripts.export_api_types`). 프런트 `src/types`로 복사하거나 필요한 것만 가져간다.
기본값이 있는 응답 필드는 OpenAPI 규칙상 `?`(선택)로 표시되지만 실제 응답에는 항상 들어 있다. 핵심 타입:

```ts
// 오류: 생성 파일의 ErrorResponse(422 외 모든 오류), HTTPValidationError { detail?: ValidationError[] }(422)
export interface ErrorResponse { detail: string }
export type Locale = 'ko-KR' | 'en-US' | 'ja-JP' | 'zh-CN'

export interface AuthResponse { user: { id: number; username: string }; access_token: string; token_type: string }

export type PerformanceStatus = 'ok' | 'partial' | 'no_account' | 'no_trades' | 'pending_only' | 'no_prices' | 'history_unavailable'
export type BenchmarkStatus = 'ok' | 'partial' | 'no_data' | 'insufficient_overlap' | 'portfolio_unavailable'
export type ExposureStatus = 'ok' | 'empty' | 'no_prices'
export type EtfDecomposeStatus = 'decomposed' | 'not_decomposed'
export type MarketDataStatus = 'latest' | 'delayed' | 'stale' | 'no_data'
export type SectionStatus = 'ok' | 'partial' | 'no_data' | 'empty'
export type TranslationStatus = 'localized' | 'translated' | 'fallback_summary' | 'fallback_original'

export interface Paged<T> { total: number; has_next: boolean; total_pages: number; items: T[] }   // limit/offset 목록(거래·진단 이력은 limit·offset도 준다)
export interface PagePaged<T> extends Paged<T> { page: number; size: number }                    // page/size 목록
```

상태값 합(union)은 백엔드 상수(`app/performance_compare.STATUSES`·`BENCHMARK_STATUSES`, `app/look_through`, `app/daily_briefing`)와 같다(백엔드 통합 테스트가 확인).

## 12. 기능별 기존 문서

| 문서 | 내용 |
|---|---|
| `backend/API.md` | 전체 명세(엔드포인트별 필드 표·예시·오류) |
| `docs/FRONTEND_HANDOFF_ETF_EXPOSURE.md` | look-through 계산·필드·데이터 누락·진단 비교·응답 사례 |
| `docs/FRONTEND_HANDOFF_PORTFOLIO_PERFORMANCE.md` | 성과·벤치마크 계산·상태·경고·응답 예시 |
| `docs/FRONTEND_HANDOFF_DAILY_BRIEFING.md` | 브리핑 생성·이력·섹션·지연 경고·다국어·응답 예시 |
| `docs/FRONTEND_HANDOFF_I18N_HISTORY.md` | 다국어·계정 설정·내 기록(리서치·진단)·통합 검색 |
| `docs/DATA_QUALITY_AND_TRADING_CALENDAR.md` | 거래일 달력·데이터 품질 격리(운영) |
| `docs/DATA_REFRESH_MONITORING.md` | 데이터 갱신·상태 보고서(운영) |

## 13. 백엔드 검증 범위(37차)

- `backend/tests/test_api_contract_round37.py`(DB 없음): 인증 API 33개 전부 토큰 없이 401, OpenAPI 오류 선언, API.md 인증 표 = 실제 라우트,
  잘못된 쿼리 23종 422(`loc`), 본문 시간대 422 위치, 처리되지 않은 오류·DB 오류에 내부 정보 없음, look-through 음수 0 없음, 타입 파일 최신.
- `backend/tests/test_api_contract_round37_devdb.py`(개발 DB·CI DB, 가짜 종목만): 새 사용자 빈 상태, 없는 종목·시세 없는 종목·엄격 주문,
  오래된 시세(`older_price`·`stale`), 구성 없는 ETF·오래된 구성, `history_unavailable`, 영어·미지원 언어, 휴장일 다음 날·달력 범위 밖 답,
  번역 실패 폴백 기록, 브리핑 생성·재조회·사용자 분리·생성 오류, 페이지네이션 경계, 금액·%·날짜 형식.

## 14. 프런트 담당자가 확인할 미해결 사항

1. **번역 상태가 POST 응답에 없다**: `POST /api/research`는 키 6개로 고정돼 있어 `translation_status`를 주지 않는다. "원문 보기"가 필요하면
   `GET /api/research/{report_id}?view=record`를 한 번 더 부르거나, 응답 `answer`가 `※`로 시작하는지로 판단한다.
   POST 응답에 선택 필드(`locale`, `translation_status`)를 더하는 것은 하위 호환 변경이라 가능하다. 원하면 백엔드에 요청(고정 테스트 5곳을 같이 바꿈).
2. **시각 오프셋이 섞여 있다**(UTC 저장 시각 / KST 계산 시각). 둘 다 ISO 8601이라 `Date`로 읽으면 같지만, 문자열을 잘라 쓰지 말 것.
3. **미지원 언어는 조용히 한국어**: GET에 `locale=fr-FR`을 보내도 200이다. 응답 `locale`을 보고 표시한다.
4. **관심종목은 유사 이름을 받는다**(주문은 엄격). 추가 응답의 `company_name`을 꼭 보여 준다.
5. **포트폴리오·주문의 현재가**는 장중에 네이버 실시간 시세(비공식)를 먼저 쓰고, 실패하거나 장 마감 뒤면 DB 종가(`is_realtime: false`)다.
   주문 체결가도 같은 값이다.
6. **기간 질문의 조회 범위는 평일 근사**(7절). 휴장일이 낀 기간의 AI 답은 휴장일 수만큼 이른 날부터 계산될 수 있다.
7. **거래일 달력은 2026-12-31까지**. 2027년 일정은 KRX 공지 뒤 백엔드가 갱신해야 한다(그전까지 답에 "확인할 수 없는 날" 문장).
8. **OpenAPI(`/docs`)는 개발 환경에서만** 켜진다. 타입은 `docs/frontend-api-types.ts`를 쓴다.
9. **새 사용자의 첫 `GET /api/portfolio`가 모의투자 계좌를 만든다**(초기 잔고 10,000,000원). 성과 API는 그전에는 `no_account`다.
