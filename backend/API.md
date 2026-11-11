# AI 투자 리서치 에이전트 API

프론트엔드 연동용 문서. 아래 예시 응답은 실제 서버(`uvicorn app.main:app --port 8000`)를 호출해 얻은 결과이며,
긴 필드(answer, sources)만 잘라서 표기했다.

- Base URL (로컬): `http://localhost:8000`
- 요청/응답은 모두 JSON(UTF-8). 리서치/관심종목/모의투자는 **로그인(Bearer 토큰)이 필요**하다(아래 "인증").
- 자동 생성 문서: `http://localhost:8000/docs` (Swagger UI)

## 인증 (2026-10-07부터)

아이디/비밀번호로 가입·로그인하면 **access token(JWT, HS256, 만료 24시간)** 을 받는다. 인증이 필요한 API는
모든 요청에 이 토큰을 `Authorization` 헤더로 보낸다.

```
Authorization: Bearer <access_token>
```

| 구분 | 엔드포인트 |
| --- | --- |
| **인증 필요** | `/api/auth/me`(GET/DELETE), `/api/auth/logout`(POST), `/api/auth/change-password`(POST), `/api/auth/email`(PUT), `/api/auth/resend-verification`(POST), `/api/briefing`(GET), `/api/research`(POST/GET), `/api/research/{report_id}`(GET/DELETE), `/api/watchlist`(GET/POST), `/api/watchlist/{ticker}`(DELETE), `/api/portfolio`(GET), `/api/portfolio/orders`(POST), `/api/portfolio/trades`(GET), `/api/portfolio/performance`(GET), `/api/backtest`(POST), `/api/portfolio/reset`(POST), `/api/portfolio/diagnosis`(POST), `/api/portfolio/diagnosis/history`(GET), `/api/portfolio/diagnosis/{id}`(GET/DELETE), `/api/portfolio/diagnosis/{id}/compare`(GET), `/api/history/search`(GET), `/api/auth/preferences`(GET/PUT), `/api/portfolio/exposure`(GET), `/api/briefings/daily/generate`(POST), `/api/briefings/daily/current`(GET), `/api/briefings/daily/history`(GET), `/api/briefings/daily/{id}`(GET) |
| 공개 | `/api/auth/signup`, `/api/auth/login`, `/api/auth/delete-account/confirm`(POST), `/api/auth/oauth/providers`(GET), `/api/auth/oauth/{provider}/login`·`/callback`(GET, 브라우저 이동), `/api/auth/oauth/exchange`(POST), `/api/auth/verify-email`, `/api/auth/find-username`, `/api/auth/forgot-password`, `/api/auth/reset-password`(모두 POST), `/api/disclaimer`, `/api/stocks/{ticker}/realtime-price`, `/api/companies`, `/api/discovery/trending`, `/api/status`, `/api/health`, `/api/stocks/{ticker}/event-reactions`, `/api/stocks/{ticker}/price-history`, `/api/etf/{ticker}/holdings`, `/api/market/overview`, `/api/stocks/{ticker}/news`, `/api/stocks/{ticker}/disclosures` |

- 데이터는 **사용자별로 분리**된다. 리서치 리포트 목록/조회/삭제/이어 쓰기(`previous_report_id`)는 본인 것만 대상이고,
  **남의 리포트 ID는 없는 리포트와 똑같이 404**다(403이 아니다 — 그 ID가 존재하는지 드러내지 않기 위해).
  관심종목/모의투자도 사용자마다 따로다.
- 로그인 도입 전에 만들어진 리포트(소유자 없음)와 `X-Device-Id`로 만든 관심종목/모의투자 데이터는 DB에 남아 있지만
  어떤 사용자에게도 보이지 않는다(새 계정으로 이전되지 않음).
- 서버는 세션/쿠키를 쓰지 않는다. 2026-10-29부터 `POST /api/auth/logout`과 `POST /api/auth/change-password`가 서버 측에서
  토큰을 폐기한다(**그 사용자의 모든 기기 토큰이 한꺼번에 무효**, 아래 "토큰 폐기"). 이전에는 프론트가 토큰을 지우는 것뿐이었다.

### 인증 에러 (401)

토큰이 없거나, `Bearer` 형식이 아니거나, 서명이 틀리거나(변조/다른 서버 키), 만료됐거나, 토큰의 사용자가 삭제됐거나,
**폐기된 토큰**이면(로그아웃·비밀번호 변경 이후, 2026-10-29) **전부 같은 401**이다(사유를 구분해 알려주지 않는다). 응답 헤더에 `WWW-Authenticate: Bearer`가 붙는다.

```json
{ "detail": "인증이 필요합니다. 다시 로그인해주세요." }
```

예전처럼 `X-Device-Id`만 보내면 이제 **422가 아니라 401**이다(헤더는 무시된다).

### 프론트가 할 일

1. **`X-Device-Id` 제거**: 헤더 생성/전송 코드와 localStorage의 디바이스ID를 지운다. 서버는 더 이상 읽지 않는다.
2. **가입/로그인 화면**: `POST /api/auth/signup`(201) 또는 `POST /api/auth/login`(200) 응답의 `access_token`을 저장한다.
   두 응답 형식은 같다(`{user: {id, username}, access_token, token_type: "bearer"}`).
3. **토큰 저장**: localStorage 또는 메모리. localStorage는 새로고침 후에도 유지되지만 XSS가 있으면 탈취될 수 있다
   (쿠키 기반 세션은 이번 범위 밖). 만료 시각은 토큰의 `exp`(초 단위 UNIX time)로 알 수 있지만, 서버가 401을 주면 그게 최종 판단이다.
4. **Authorization 헤더**: 인증 필요 API를 부를 때 모든 요청에 `Authorization: Bearer <access_token>`을 붙인다
   (공개 API에는 붙여도 무시된다).
5. **401 처리**: 어떤 API에서든 401을 받으면 저장된 토큰을 지우고 로그인 화면으로 보낸다. 재시도해도 같은 결과다.
   (로그인 API의 401은 "아이디 또는 비밀번호가 올바르지 않습니다" — 로그인 화면에서 그대로 보여주면 된다.)
   비밀번호 변경에서 **현재 비밀번호가 틀린 경우는 401이 아니라 422**라서 이 처리에 걸리지 않는다(로그인 상태 유지).
6. **앱 시작 시**: 저장된 토큰이 있으면 `GET /api/auth/me`로 유효한지 확인하고 사용자 이름을 표시한다(401이면 5번).
7. **로그아웃(2026-10-29)**: `POST /api/auth/logout`을 부른 뒤 저장한 토큰을 지운다. 응답이 401이어도(이미 무효) 토큰을 지우면 된다.
   이 계정으로 로그인한 **다른 기기도 함께 로그아웃**된다(다음 요청에서 401).
8. **비밀번호 변경(2026-10-29)**: `POST /api/auth/change-password` 성공 응답의 `access_token`으로 **저장한 토큰을 바꾼다**.
   기존 토큰은 이 기기 것도 무효가 되므로, 바꾸지 않으면 다음 요청에서 401이 된다.
9. **CORS**: `Authorization` 헤더를 보내면 브라우저가 preflight(OPTIONS)를 먼저 보낸다. 허용 origin(아래)에서는 그대로 통과한다.

## CORS

허용 origin은 환경변수 `CORS_ALLOW_ORIGINS`(쉼표 구분)로 정한다. 기본값은 아래 두 개다(2026-10-28부터).

- `http://localhost:5173` (Vite)
- `http://127.0.0.1:5173` (Vite, IP 주소로 열었을 때)

2026-10-28 전 기본값은 `http://localhost:5173`, `http://localhost:3000`이었다(**3000은 기본에서 빠짐**, 필요하면 `CORS_ALLOW_ORIGINS`에 추가).
지금 프론트는 Vite 개발 서버의 프록시(`/api` → `http://localhost:8000`)로 같은 출처 요청을 보내 CORS를 거치지 않는다.
인증은 Authorization 헤더(Bearer)라 credentials(쿠키)는 허용하지 않는다. 허용하지 않은 origin의 사전 요청(OPTIONS)은 400이다.
**프론트가 다른 포트나 도메인에서 뜬다면** `CORS_ALLOW_ORIGINS`에 추가해야 하고, 추가 전에는 브라우저가 CORS 오류로 요청을 막습니다(curl/Postman은 영향 없음).

브라우저 JS는 기본적으로 응답 헤더 몇 개만 읽을 수 있어서, `GET /api/companies`의 `X-Total-Count`는 `Access-Control-Expose-Headers`로 노출한다(2026-10-28).
노출할 헤더 목록은 환경변수 `CORS_EXPOSE_HEADERS`(쉼표 구분, 기본 `X-Total-Count`)로 바꿀 수 있다. 같은 출처 프록시(Vite `/api`)로 부르면 CORS와 상관없이 읽힌다.

## 목록 API 공통 규칙: 정렬·페이징·검색 (2026-10-28)

대상은 목록 API 4개다: `GET /api/research`, `GET /api/portfolio/trades`, `GET /api/portfolio/diagnosis/history`, `GET /api/companies`.
**하위 호환:** 기존 파라미터·기본값·기본 정렬·기존 응답 필드는 그대로이고, 아래는 전부 **추가**다(선택 파라미터, 응답 필드 2개, 응답 헤더 1개).

| 추가 | 어디에 | 설명 |
| --- | --- | --- |
| `sort` | 4개 전부 | 정렬 기준. API마다 허용 값 목록이 있고 그 밖의 값은 422. **대소문자를 구분한다** |
| `order` | 4개 전부 | `asc` 또는 `desc`(소문자만). 기본값은 지금까지의 정렬 방향이다 |
| `has_next` | research, trades, diagnosis history 응답 | `offset + limit < total`. 다음 페이지가 있는가 |
| `total_pages` | 위와 같음 | `ceil(total / limit)`. `total`이 0이면 0 |
| `q` | `GET /api/research` | 질문·요약·종목명·종목코드 부분 일치 검색 |
| `offset` | `GET /api/companies` | 건너뛸 건수(기본 0) |
| `X-Total-Count` 헤더 | `GET /api/companies` | `q`/`asset_type`에 맞는 전체 건수. 본문은 계속 배열 |

- **파라미터를 안 주면 순서가 예전과 같다.** 기본 정렬: research `created_at desc`, trades `executed_at desc`, diagnosis history `created_at desc`, companies `id asc`(수집 순서).
- **같은 값끼리는 `id`로 정한다.** 정렬 키가 같은 행(같은 시각의 체결 등)이 있어도 페이지를 넘길 때 중복이나 누락이 생기지 않도록 항상 `id`를 보조 키로 붙이고, `id`의 방향은 `order`를 따른다(`order=desc`면 `id`도 내림차순).
- **키 이름은 snake_case**(`has_next`, `total_pages`)로, 기존 `report_id`, `previous_report_id`와 같다.
- **검증 오류는 계속 422**(FastAPI 기본 형식, 400으로 바꾸지 않았다). 인증이 필요한 API는 **401이 검증(422)보다 먼저**다: 토큰이 없으면 `limit=0`처럼 잘못된 파라미터를 같이 보내도 401이다.

### 경계값별 동작 (4개 API 공통)

`max`는 API별 `limit` 상한이다(research 100, trades 200, diagnosis history 100, companies 300).

| 요청 | 결과 |
| --- | --- |
| `limit=1`, `limit=max` | 200 |
| `limit=0`, `limit=max+1`, `limit=-1` | 422 (`greater_than_equal`/`less_than_equal`) |
| `limit=abc`, `limit=1.5`, `limit=`(빈 값) | 422 (`int_parsing`) |
| `offset=0` | 200 |
| `offset=-1` | 422 |
| `offset=abc`, `offset=1.5` | 422 |
| `offset >= total` (예: `offset=total`, `offset=1000000`) | **200**, `items`가 빈 배열(companies는 `[]`). `total`은 그대로이고 `has_next`는 false |
| `offset=9223372036854775807`(= DB bigint 최대) | 200, 빈 `items` |
| `offset=9223372036854775808` 이상 | 422 (`less_than_equal`). 예전에는 DB 오류로 503이 났다 |
| `sort`가 허용 목록 밖(`bogus`, `CREATED_AT`, 빈 값, 다른 API의 필드, SQL 조각) | 422 (`literal_error`). 쿼리에 들어가지 않는다 |
| `order`가 `asc`/`desc` 밖(`up`, `DESC`, 빈 값) | 422 (`literal_error`) |
| `q`가 50자 초과 (`GET /api/research`) | 422 (`string_too_long`). companies의 `q`는 100자 |
| `q`가 공백뿐이거나 빈 값 | 200, 검색 조건을 적용하지 않는다(= `q` 없음) |
| 토큰 없음 + 잘못된 파라미터 | **401** (인증 필요 API만) |

다음 페이지는 `has_next`가 true인 동안 `offset += limit`로 이어서 부르면 중복·누락 없이 전체를 훑는다(통합 테스트가 동률 정렬 값 포함해 검증한다).

## 종목 유형: 주식과 ETF (2026-10-14 추가, 1단계)

ETF도 주식과 같은 `company` 한 행으로 다룬다. 둘의 구분은 응답의 **`asset_type`**(`"stock"` 또는 `"etf"`) 하나뿐이고, 검색·관심종목·
모의매매(매수/매도/초기화)는 주식과 똑같이 동작한다(종목코드나 이름으로 부르면 된다: `069500`, `KODEX 200`, `코덱스200`).
기존 필드는 하나도 바뀌지 않았고 `asset_type`만 **추가**됐다(주식은 항상 `"stock"`).

- **수집된 ETF**: 시가총액 상위 50개(KODEX 200, TIGER 미국S&P500, TIGER 미국나스닥100, KODEX 레버리지, KODEX 인버스 등 대표 5종 포함).
  일별 주가 최근 90일(2026-07-16~2026-10-14) + 뉴스(ETF 이름으로 검색). **공시는 없다**(ETF는 DART 공시 수집 대상이 아니다).
- **`asset_type`이 붙는 응답**: `GET /api/companies`의 항목, `GET /api/watchlist`의 `items[]`와 `POST /api/watchlist` 응답,
  `GET /api/portfolio`의 `holdings[]`, `GET /api/portfolio/trades`의 `items[]`, `GET /api/discovery/trending`(응답 최상위).
- **ETF 이름 입력**: 운용사 브랜드의 한글 표기를 영문으로 읽는다(`코덱스`=KODEX, `타이거`=TIGER, `에이스`=ACE, `쏠`=SOL,
  `케이비스타`=KBSTAR, `아리랑`=ARIRANG, `하나로`=HANARO, `라이즈`=RISE, `플러스`=PLUS). "코덱스200", "코덱스 레버리지", "타이거 미국나스닥100"처럼
  입력하면 된다. `타이거 나스닥`처럼 줄여 쓴 몇 가지(`TIGER 나스닥`, `TIGER S&P500`, `KODEX 나스닥100` 등)는 대표 상품으로 연결한다.
  KBSTAR는 RISE로, ARIRANG는 PLUS로 이름이 바뀐 브랜드라 그 이름으로 입력하면 현재 이름의 ETF를 찾는다.
  "에이스침대"처럼 브랜드와 겹치는 일반 이름은 그대로 주식으로 해석한다.

### 2단계에서 바뀐 것 (2026-10-14, 응답 필드는 추가만)

- **리서치(`POST /api/research`)의 ETF 질문**: 아래 "ETF 질문 처리 규칙" 참고. 가격 흐름과 뉴스를 함께 보고, 레버리지·인버스 ETF에는
  서버가 고정 설명 문구를 붙이고, 뉴스 기사 속 수치(다른 상품의 등락률 등)를 ETF의 것으로 쓰지 못하게 막는다.
- **포트폴리오 진단(`POST /api/portfolio/diagnosis`)**: ETF는 시장 쏠림 계산에서 빠지고(`holdings[].market`이 `"ETF"`),
  ETF를 보유하면 종목 수 부족 경고(`FEW_HOLDINGS`)를 하지 않으며, 레버리지·인버스 ETF 보유에 새 플래그 **`LEVERAGED_INVERSE_ETF`**가 붙는다.
  `holdings[]`에 `asset_type`이 추가됐다. 상세는 진단 절의 "플래그 규칙".
- **ETF 이름 속 숫자는 기간이 아니다**: "KODEX 200 주가 알려줘"를 200주(약 1000일)로 조회하던 문제를 고쳤다. 이름 뒤의 진짜 기간
  ("KODEX 200 3개월 추이")은 그대로 인식한다.
- **ETF 뉴스를 다시 수집했다**: 제목·본문에 그 ETF 이름이 직접 나오는 기사만 저장한다(504건, 이름이 나오는 비율 49.6% → 97.6%,
  공백·한글 브랜드 표기까지 치면 100%). "KODEX 2차전지산업레버리지"처럼 이름 뒤에 다른 상품명이 이어지는 기사는 다른 상품의 기사로 보고 뺀다.

### 알려진 한계 (확인된 사실만)

- **ETF는 `market`/`sector`가 `null`이다**(KRX 목록에 시장 구분이 없다). **시장 지수(코스피/코스닥)와의 비교는 하지 않는다** -
  리서치의 시장 비교 tool은 ETF에 "ETF라서 상장 시장 구분이 없어 시장 지수와 비교하지 않습니다"라고 답한다.
- **공시·실적·재무 정보가 없다**: ETF는 DART 공시 수집 대상이 아니다. 리서치의 공시 tool은 ETF에 "ETF는 개별 기업이 아니라서 공시를
  수집하지 않습니다"라고 답한다.
- **ETF 상품 정보 중 구성종목·비중만 일부 ETF에 대해 수집한다**(2026-10-30, 아래 `GET /api/etf/{ticker}/holdings`). 추종지수, 운용보수, 순자산,
  괴리율은 수집하지 않는다. 그래서 리서치 답변은 이런 내용을 말하지 않도록 막았다(모르는 것을 지어내지 않게).
  레버리지·인버스 구조 설명은 이름으로 판별한 고정 문구뿐이다.
- **`/api/discovery/trending`의 기준 날짜는 유형별로 따로 계산한다**: 2026-10-14 갱신 이후 주식·ETF 모두 10/14가 최신이지만, 갱신 시점이 어긋나면 달라질 수 있다.
  `price_date`로 확인할 수 있다.
- **"한 달"처럼 숫자가 없는 기간 표현은 서버가 값으로 바꾸지 못한다**(주식과 같다). 모델이 추론한 값(보통 30거래일)으로 조회한다.
- 레버리지·인버스 판별은 **이름**("레버리지", "인버스", "2X" 등)으로 한다. 이름에 그 표시가 없는 파생형 상품은 잡지 못한다.
- **원인 분석·위험요인 문장은 여전히 모델이 쓴다**(가격·뉴스 섹션은 서버가 쓴다). 수치·방향·출처 없는 상품 설명은 서버가 걸러내지만,
  수치가 없는 서술이나 라벨은 검사하지 못한다. 최종 실측(2026-10-14, ETF 질문 10개 x 3회)에서 모델이 쓴 문장 중 사실과 다른 것이
  30회 중 3회 있었다: 다른 상품 기사의 내용을 원인으로 든 문장("미국 금리 상승으로 채권 매수 수요 확대"), 마지막 거래일 거래량을
  "20거래일 전 거래량"이라고 쓴 문장, 출처가 불분명한 정책 서술. 서버가 쓰는 가격·뉴스 섹션에는 오류가 없었다.

---

## 엔드포인트 요약

| 메서드 | 경로 | 인증 | 설명 |
| --- | --- | --- | --- |
| POST | `/api/auth/signup` | 공개 | 회원가입 → 201 + access token |
| POST | `/api/auth/login` | 공개 | 로그인 → 200 + access token |
| GET | `/api/auth/me` | 필요 | 토큰의 사용자 정보(2026-10-30부터 `email`, `email_verified` 추가) |
| POST | `/api/auth/logout` | 필요 | 이 사용자의 모든 토큰 폐기(전체 기기 로그아웃) → 200 `{"ok": true}` |
| POST | `/api/auth/change-password` | 필요 | 비밀번호 변경 + 기존 토큰 폐기 → 200 + 새 access token |
| PUT | `/api/auth/email` | 필요 | 이메일 등록·변경(**현재 비밀번호 필수**) + 인증 메일, 이전 인증 주소로 변경 알림(2026-10-30, 아래 "계정 복구") |
| POST | `/api/auth/resend-verification` | 필요 | 인증 메일 재발송 |
| POST | `/api/auth/verify-email` | 공개 | 메일 링크의 토큰으로 이메일 인증 |
| POST | `/api/auth/find-username` | 공개 | 인증된 이메일로 아이디를 메일 발송(응답에는 아이디 없음, 항상 같은 200) |
| POST | `/api/auth/forgot-password` | 공개 | 인증된 이메일로 재설정 링크 발송(항상 같은 200) |
| POST | `/api/auth/reset-password` | 공개 | 재설정 토큰 + 새 비밀번호 → 모든 기존 토큰 폐기 |
| GET | `/api/auth/oauth/providers` | 공개 | 켜진 소셜 로그인 공급자 목록(2026-10-30, 아래 "소셜 로그인") |
| GET | `/api/auth/oauth/{provider}/login` | 공개 | 브라우저 이동: 구글·네이버 인증 페이지로 302 |
| GET | `/api/auth/oauth/{provider}/callback` | 공개 | 공급자가 돌려보내는 곳 → 프론트 `/oauth/callback#code=...` 또는 `/login?error=...`로 302 |
| POST | `/api/auth/oauth/exchange` | 공개 | 60초 1회용 코드 → 로그인과 같은 응답(access token) |
| DELETE | `/api/auth/me` | 필요 | 회원 탈퇴: 모든 데이터 삭제(2026-10-30, 아래 "회원 탈퇴") |
| POST | `/api/auth/delete-account/confirm` | 공개 | 소셜 계정의 탈퇴 확인 메일 링크로 탈퇴 확정 |
| POST | `/api/research` | 필요 | 질문을 보내 AI 답변 생성(+리포트 저장). `previous_report_id`로 내 직전 보고서를 이어서 후속 질문 가능 |
| GET | `/api/research` | 필요 | 내 리포트 목록(기본 최신순). `limit`/`offset` 페이지네이션(`has_next`, `total_pages`), `sort`/`order` 정렬, `q` 검색. 2026-11-05: `date_from`/`date_to`/`timezone`/`analysis_type` 필터, `view=record`면 기록 관리 필드(아래 "기록 관리") |
| GET | `/api/research/{report_id}` | 필요 | 내 리포트 한 건 조회. `view=record`면 종목 목록·분석 유형·기준일(`data_basis`)·날짜 그룹 추가 |
| DELETE | `/api/research/{report_id}` | 필요 | 내 리포트 한 건 삭제(tool 호출 이력 포함) |
| GET | `/api/stocks/{ticker}/realtime-price` | 공개 | 종목 현재가(비공식 소스 기반 실시간 시세, 장외/장애 시 자동 폴백) |
| GET | `/api/companies` | 공개 | 종목 목록/검색(`q`로 종목명·티커 부분 일치, 별칭 포함) + 최근 종가·등락률. `asset_type=stock\|etf`로 주식/ETF만. `offset`, `sort`/`order`, 전체 건수는 `X-Total-Count` 헤더 |
| GET | `/api/discovery/trending` | 공개 | 급등/급락/거래량 급증 종목(기본은 주식만, `asset_type=etf`로 ETF) |
| GET | `/api/watchlist` | 필요 | 내 관심종목 목록 |
| POST | `/api/watchlist` | 필요 | 관심종목 추가 |
| DELETE | `/api/watchlist/{ticker}` | 필요 | 관심종목 삭제 |
| GET | `/api/portfolio` | 필요 | 내 모의투자 잔고 + 보유 종목(첫 호출 시 계좌 자동 생성) |
| POST | `/api/portfolio/orders` | 필요 | 모의투자 매수/매도 주문 |
| GET | `/api/portfolio/trades` | 필요 | 내 체결 내역(기본 최신순, `limit`/`offset` 페이지네이션(`has_next`, `total_pages`), `sort`/`order` 정렬, `ticker` 필터) |
| GET | `/api/portfolio/performance` | 필요 | 거래일 종가 기준 자산 곡선과 성과 지표(수익률·MDD·변동성·승률·종목 기여도·코스피 대비), LLM 없음, 읽기 전용 (2026-10-29) |
| POST | `/api/backtest` | 필요 | 과거 종가로 buy_and_hold / sma_cross / dca 백테스트(1~5종목, 최대 5년, t일 신호 → t+1일 종가 체결), LLM 없음, 저장 안 함 (2026-10-29) |
| POST | `/api/portfolio/reset` | 필요 | 내 모의투자 초기화(보유·체결 내역 삭제 + 잔고 초기값). 본문 `{"confirm": true}` 필수 |
| POST | `/api/portfolio/diagnosis` | 필요 | 내 포트폴리오 AI 진단(지표·플래그는 코드 계산, 설명은 LLM 또는 규칙 기반 폴백). 결과를 저장하고 `diagnosis_id`·`created_at`을 붙인다 |
| GET | `/api/portfolio/diagnosis/history` | 필요 | 내 진단 이력 목록(기본 최신순, `limit`/`offset` 페이지네이션(`has_next`, `total_pages`), `sort`/`order` 정렬, 최근 50건 보관). 2026-11-05: `q`/`date_from`/`date_to`/`timezone`, `view=record` |
| GET | `/api/portfolio/diagnosis/{id}` | 필요 | 내 진단 한 건(저장된 진단 응답 그대로) |
| GET | `/api/portfolio/diagnosis/{id}/compare` | 필요 | 이전 진단과 비교(코드로만 계산). `base`로 기준 진단 지정. `view=record`면 기준 시점(`basis`)·지표 변화(`metric_changes`) 추가 |
| GET | `/api/auth/preferences` | 필요 | 내 언어·시간대 설정(2026-11-05, 아래 "다국어") |
| PUT | `/api/auth/preferences` | 필요 | 내 언어·시간대 설정 변경(지원하지 않는 값은 422) |
| GET | `/api/history/search` | 필요 | 내 리서치·진단 기록 통합 검색(2026-11-05, 아래 "기록 관리"). `type`/`keyword`/`date_from`/`date_to`/`timezone`/`page`/`size`/`sort` |
| DELETE | `/api/portfolio/diagnosis/{id}` | 필요 | 내 진단 한 건 삭제 |
| GET | `/api/disclaimer` | 공개 | 서비스 면책 문구(`{"text": "..."}`) |
| GET | `/api/status` | 공개 | 데이터 기준일(소스별 최신 날짜, 지수 stale 여부, 마지막 갱신 결과). 푸터의 "데이터 기준일" 표시용 |
| GET | `/api/health` | 공개 | 서비스 상태(DB·마이그레이션·Ollama·데이터 기준일, 항상 200, `ok`/`degraded`) |
| GET | `/api/stocks/{ticker}/price-history` | 공개 | 주가 추이 차트용 일별 시세(종가·거래량·등락률·시가·고가·저가). `days`는 1~365(기본 90, 2026-11-10에 상한 90에서 확장)이고 **최근 N거래일**(달력 일 아님). 날짜 오름차순. 데이터가 모자라면 `count < days`이고 저장 행 전체 수는 `available_count` |
| GET | `/api/market/overview` | 공개 | 시장 요약(2026-11-10): 코스피·코스닥 종가와 직전 거래일 대비 등락폭·등락률, 수집 대상 주식의 상승·하락·보합 수, 신선도(`latest`/`delayed`/`stale`/`no_data`), 마지막 수집 시도. 브리핑을 만들지 않아도 된다 |
| GET | `/api/stocks/{ticker}/news` | 공개 | 종목 뉴스 목록(2026-11-10): 게시 시각 최신순, `limit`(1~50)/`offset`/`date_from`/`date_to`/`order`, 같은 기사에 연결된 다른 종목(`related_tickers`), 신선도 |
| GET | `/api/stocks/{ticker}/disclosures` | 공개 | 종목 DART 공시 목록(2026-11-10): 접수일 최신순, 정정 공시 표시·필터(`correction`), 접수번호, 같은 페이지 규칙. ETF는 200 + `supported=false` |
| GET | `/api/briefing` | 필요 | 오늘의 개인화 브리핑(보유·관심종목 기준 시장 요약, 종목별 등락률·거래량 배수·뉴스·공시, flag, 포트폴리오 요약). LLM 없이 코드가 계산하고 읽기 전용(계좌를 만들지 않음). `news_limit` 1~5 |
| POST | `/api/briefings/daily/generate` | 필요 | 개인화 일일 투자 브리핑 만들기(5개 영역, 날짜별 저장·버전). 같은 날 이미 있으면 저장본 그대로(200), `regenerate`면 새 버전(201), 하루 5개 |
| GET | `/api/briefings/daily/current` | 필요 | 오늘(사용자 시간대) 브리핑의 마지막 버전, 없으면 404 |
| GET | `/api/briefings/daily/history` | 필요 | 내 일일 브리핑 목록(날짜 최신순, `latest_only`, 날짜 범위, 페이지) |
| GET | `/api/briefings/daily/{id}` | 필요 | 저장된 브리핑 그대로(다시 계산하지 않음), 남의 것은 404 |
| GET | `/api/stocks/{ticker}/event-reactions` | 공개 | 뉴스·공시 이후 주가 반응(1/5/20거래일 수익률, 지수 대비 초과수익률, 거래량 비율)을 코드로 계산. `event_type`/`limit`(1~30, 유형별)/`since_days`(최대 120) 쿼리. 시간적 선후관계일 뿐 인과가 아님 |

아래 각 엔드포인트의 에러 표에는 401을 반복해 적지 않았다. **인증 필요 API는 모두 위 "인증 에러 (401)"가 공통으로 적용된다.**

---

## POST /api/auth/signup

회원가입. 성공하면 바로 로그인된 상태(토큰)를 돌려준다.

### 요청

```json
{ "username": "alice_01", "password": "correct-horse-battery" }
```

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `username` | string | 필수. **3~20자, 영문 소문자/숫자/밑줄(`[a-z0-9_]`)**. 대문자는 소문자로 바꿔 저장한다(`Alice`로 가입하면 `alice`). 위반 시 422 |
| `password` | string | 필수. **8~128자**, 흔한 비밀번호(내장 목록)·한 문자 반복·아이디 또는 이메일 앞부분(3자 이상)이 들어간 비밀번호는 거부(2026-10-30, 가입·변경·재설정 공통). 위반 시 422(입력값은 되돌려주지 않는다) |
| `email` | string \| null | **선택(2026-10-30 추가, 없으면 이전과 같다)**. 앞뒤 공백 제거·소문자로 저장, 최대 254자, 형식 위반 422. 다른 계정이 쓰면 409. 주면 인증 메일을 보낸다(아래 "계정 복구"). 응답 형식은 같다 |

```bash
curl -X POST http://localhost:8000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"username": "alice_01", "password": "correct-horse-battery"}'
```

### 응답 201

```json
{
  "user": { "id": 1, "username": "alice_01" },
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9....",
  "token_type": "bearer"
}
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 409 | 이미 있는 아이디(대소문자 무시) | `{"detail": "이미 사용 중인 아이디입니다."}` |
| 409 | 이미 쓰는 이메일(대소문자 무시, `email`을 줬을 때만) | `{"detail": "이미 사용 중인 이메일입니다."}` |
| 422 | 아이디/비밀번호/이메일 규칙 위반, 필드 누락, 잘못된 JSON | 검증 오류 형식. 단 `/api/auth/*`의 422에는 **`input` 키가 없다**(입력한 비밀번호를 되돌려주지 않기 위해) |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## POST /api/auth/login

### 요청

```json
{ "username": "alice_01", "password": "correct-horse-battery" }
```

`username`은 대소문자를 구분하지 않는다(가입 때처럼 소문자로 바꿔서 찾는다). 길이 상한(아이디 100자, 비밀번호 128자)을 넘으면 422.

### 응답 200

`POST /api/auth/signup`의 201 응답과 같은 형식(`user`, `access_token`, `token_type`).

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 아이디가 없거나 비밀번호가 틀림(**두 경우 응답이 완전히 같다**) | `{"detail": "아이디 또는 비밀번호가 올바르지 않습니다"}` |
| 422 | 필드 누락/빈 문자열/길이 상한 초과 | 검증 오류 형식(`input` 없음) |
| 503 | DB 연결 불가 | 위와 같음 |

아이디가 없을 때도 서버가 비밀번호 해시 검증을 한 번 수행하므로, 응답 시간으로도 아이디 존재 여부를 구분하기 어렵다.

---

## GET /api/auth/me

```bash
curl http://localhost:8000/api/auth/me -H "Authorization: Bearer $TOKEN"
```

### 응답 200

```json
{ "id": 1, "username": "alice_01", "email": "alice@example.com", "email_verified": true }
```

`email`(string | null)과 `email_verified`(boolean)는 2026-10-30에 추가됐다(기존 `id`·`username`은 그대로). 본인에게는 이메일을 가리지 않는다.
이메일을 등록하지 않았으면 `null` / `false`. 가입·로그인 응답의 `user`(`id`, `username`)는 바뀌지 않았다.

### 에러

401(토큰 없음/만료/변조/삭제된 사용자/폐기된 토큰, 위 "인증 에러" 참고).

---

## 토큰 폐기 (`tv` 클레임, 2026-10-29)

- `users.token_version`(정수, 기본 0, 마이그레이션 `e2a9c4f71b03`)을 두고, 발급하는 토큰에 그 값을 **`tv` 클레임**으로 넣는다
  (claims: `sub`, `iat`, `exp`, `tv`).
- 인증할 때 토큰의 `tv`와 DB의 `token_version`이 **같을 때만** 통과한다. 다르면 401(다른 401과 같은 응답).
  비교는 원래 하던 사용자 조회 결과로 하므로 요청당 DB 조회는 늘지 않았다(여전히 1회).
- 로그아웃·비밀번호 변경이 `token_version`을 1 올리면, 그 전에 발급된 그 사용자의 토큰은 **기기와 상관없이 전부** 무효다.
  다른 사용자의 토큰은 영향이 없다. 한 기기만 로그아웃하는 기능은 없다(한계).
- **`tv`는 필수다(2026-10-30 변경)**: 2026-10-29까지는 `tv`가 없는 옛 토큰을 `tv=0`으로 받았지만, 그런 토큰은 수명(24시간)이 지나 남아 있지 않아
  이제 `tv`가 없으면 401이다. `tv`가 0 이상의 정수가 아니면(문자열, 음수, true 등) 401. 비밀번호 재설정과 회원 탈퇴도 토큰을 폐기한다.
- 로그인·가입 응답 형식은 그대로다(`{user, access_token, token_type}`).

---

## POST /api/auth/logout

인증 필요. 본문 없음. 이 사용자의 `token_version`을 1 올려 **모든 기기의 기존 토큰을 무효**로 만든다.

```bash
curl -X POST http://localhost:8000/api/auth/logout -H "Authorization: Bearer $TOKEN"
```

### 응답 200

```json
{ "ok": true }
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조/**이미 폐기된 토큰**(로그아웃을 두 번 부른 경우 포함) | 위 "인증 에러"와 같음. `token_version`은 바뀌지 않는다 |
| 429 | 사용자당 분당 10회 초과 | 레이트 리밋 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## POST /api/auth/change-password

인증 필요. 현재 비밀번호를 확인하고 바꾼다. 성공하면 `token_version`을 올려 기존 토큰(이 기기 것 포함)을 전부 무효로 하고,
**새 토큰을 응답으로 돌려줘** 현재 세션을 이어 가게 한다. 비밀번호 해시는 가입과 같은 argon2id다.

### 요청

```json
{ "current_password": "correct-horse-battery", "new_password": "another-long-passphrase" }
```

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `current_password` | string | 필수. 1~128자 |
| `new_password` | string | 필수. **가입과 같은 규칙**(8~128자, 흔한 비밀번호·아이디·이메일 앞부분 거부). 위반 시 422. `current_password`와 같으면 422(`"type": "same_password"`) |

### 응답 200

로그인 응답과 같은 형식(`user`, `access_token`, `token_type`). 이 `access_token`으로 저장한 토큰을 바꿔야 한다.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조/폐기됨, 또는 처리 중에 다른 기기에서 로그아웃됨 | 위 "인증 에러"와 같음. 본문 검사보다 먼저다(토큰 없이는 본문 규칙도 알려주지 않는다) |
| 422 | **현재 비밀번호가 틀림** | `{"detail": [{"type": "invalid_current_password", "loc": ["body", "current_password"], "msg": "현재 비밀번호가 올바르지 않습니다."}]}` |
| 422 | 새 비밀번호 규칙 위반, 현재와 같음, 필드 누락 | 검증 오류 형식(`input` 없음) |
| 429 | 사용자당 시간당 10회 초과, 또는 현재 비밀번호 연속 5회 오류(5분 잠금) | 레이트 리밋 형식 |
| 503 | DB 연결 불가 | 위와 같음 |

**현재 비밀번호 오류가 401이 아니라 422인 이유**: 이 API에서 인증 필요 라우트의 401은 "토큰이 무효하니 다시 로그인하라"는 뜻이고,
프론트는 401을 받으면 로그인 상태를 지운다(위 "프론트가 할 일" 5번). 여기서는 토큰이 유효하고 입력 하나가 틀린 것이라 입력 오류로
돌려준다. 형식은 검증 오류(422)와 같은 `detail` 배열이라 프론트가 따로 분기하지 않아도 `msg`가 그대로 보인다.
로그인 실패 401과 같은 원칙은 지킨다: 오류 응답은 하나로 고정돼 있고 어떤 비밀번호도 응답·로그·예외 메시지에 넣지 않는다.
계정은 토큰으로 이미 특정돼 있어 계정 존재 여부가 새는 경로는 없다. 새 비밀번호가 현재와 같은지는 요청의 두 값끼리만 비교하므로
저장된 비밀번호에 대해 아무것도 드러내지 않는다. 규칙 위반 422는 현재 비밀번호를 확인하기 전에 끝나며 실패 횟수에 세지 않는다.

---

## 계정 복구: 이메일 등록·인증, 아이디 찾기, 비밀번호 재설정 (2026-10-30)

계산·DB는 `app/account_recovery.py`(순수: 토큰·이메일·메일 본문), `app/account_recovery_db.py`(DB와 발송 작업), 발송기는 `app/mailer.py`.
에러 형식은 다른 API와 같은 `{"detail": ...}`이고 `/api/auth/*`의 422에는 입력값(`input`)이 들어가지 않는다.

### 원칙

- **복구는 인증된 이메일로만 동작한다.** 아이디 찾기·비밀번호 재설정 메일은 `email_verified_at`이 있는 이메일에만 보낸다.
  인증 없이 보내면, 가입·등록 때 오타로 남의 주소를 넣은 경우 그 주소의 주인이 재설정 링크를 받아 계정을 가져갈 수 있다.
  링크를 열어 인증한 주소만 "이 계정의 주인이 받는 주소"로 믿는다.
- **사용자 열거 방지**: `find-username`과 `forgot-password`는 이메일이 있든, 없든, 인증 전이든 **항상 같은 200과 같은 본문**이다.
  사용자 조회·토큰 발급·메일 발송은 모두 응답을 보낸 뒤 `BackgroundTasks`로 처리해서 응답 시간도 이메일 존재 여부와 상관없다. 응답에 아이디를 넣지 않는다.
- **토큰**: `secrets.token_urlsafe(32)`(256비트). DB(`account_token.token_hash`)에는 **SHA-256 hex만** 저장하고 원문은 메일 링크에만 있다.
  1회용이고, 만료는 이메일 인증 24시간·비밀번호 재설정 30분. 같은 목적의 새 토큰을 발급하면 이전 미사용 토큰은 무효가 된다
  (이메일 변경·인증 재발송·재설정 메일 재요청). 잘못됐거나 만료됐거나 이미 썼거나 다른 목적의 토큰은 **모두 같은 400**이다.
- **동시 사용**: 토큰 사용은 조건부 `UPDATE ... WHERE used_at IS NULL AND expires_at > now RETURNING`이라 같은 토큰으로 동시에 두 번 요청해도 한 번만 성공한다.

### 프론트가 만들 화면 두 개 (링크 형식)

메일 링크는 `{FRONTEND_BASE_URL}/verify-email#token=...`, `{FRONTEND_BASE_URL}/reset-password#token=...`이다(기본 `http://localhost:5173`).
**토큰은 쿼리(`?token=`)가 아니라 fragment(`#token=`)에 있다.** fragment는 브라우저가 서버로 보내지 않아서 프론트 서버의 접근 로그나 외부로 가는 `Referer`에 남지 않는다.

- `/verify-email`: `location.hash`에서 `token`을 읽어 `POST /api/auth/verify-email {"token": ...}`. 200이면 "인증 완료", 400이면 "링크가 만료됐거나 이미 쓰였다 → 재발송" 안내.
- `/reset-password`: `token`을 읽고 새 비밀번호 입력 폼을 보여준 뒤 `POST /api/auth/reset-password {"token": ..., "new_password": ...}`. 200이면 로그인 화면으로(저장된 토큰은 지운다: 모두 폐기됨). 422는 비밀번호 규칙, 400은 링크 문제.
- 두 화면 모두 토큰을 읽은 뒤 `history.replaceState`로 주소창의 fragment를 지우고, 외부 리소스·분석 스크립트에 토큰을 넘기지 않는 것을 권장한다.

### PUT /api/auth/email (인증 필요)

```json
{ "email": "Alice@Example.com", "current_password": "correct-horse-battery" }
```

**요청 계약(2026-10-30 보강): `current_password`는 필수다. 처음 등록할 때도 똑같이 요구한다.** 프론트는 아직 이 엔드포인트를 연동하지 않았으므로
필수 필드 추가가 기존 클라이언트를 깨지 않는다. 응답 형식과 필드는 바뀌지 않았다.

이메일을 등록하거나 바꾼다. 저장값은 앞뒤 공백 제거·소문자(`alice@example.com`). 현재 비밀번호 확인과 변경은 사용자 행을 잠근 한 트랜잭션에서 한다. 바꾸면:

- `email_verified`가 `false`가 되고, 새 주소로 인증 메일을 보낸다.
- 그 사용자의 **미사용 인증 링크와 미사용 비밀번호 재설정 링크를 모두 무효**로 한다. 옛 주소로 간 재설정 링크가 변경 뒤에도 살아 있지 않게 한다.
- **이전 주소가 인증돼 있었으면** 그 주소로 "계정 이메일이 변경되었습니다" 알림을 보낸다. 알림은 응답 뒤 best-effort라 실패해도 응답이 같다.
  알림에는 링크·토큰이 없고 새 주소는 가려서 쓴다(`ne***@ex***.org`). 이메일 없이 처음 등록할 때와 이전 주소가 인증 전이었을 때는 알림이 없다.

지금과 같은 주소면 아무것도 바꾸지 않는다(`verification_sent: false`, 비밀번호는 확인한다).

**판단 근거**: 비밀번호를 묻지 않으면, 로그인 토큰(JWT, 24시간)을 훔친 사람이 자기 주소를 등록·인증한 뒤 비밀번호 찾기로 계정을 영구히 가져갈 수 있다.
이메일은 복구 경로이므로 비밀번호 변경과 같은 수준으로 보호한다. 이전 주소 알림은 비밀번호까지 털린 경우에도 원래 주인이 알 수 있게 한다.

```json
{ "email": "alice@example.com", "email_verified": false, "verification_sent": true, "message": "인증 메일을 보냈습니다. 메일의 링크를 열면 인증이 끝납니다." }
```

| 상태 | 언제 |
| --- | --- |
| 400 | 비밀번호가 없는 계정(`password_hash`가 비어 있음: 이후 소셜 로그인 계정). `{"detail": "비밀번호가 설정되지 않은 계정은 여기서 이메일을 바꿀 수 없습니다. 이 계정의 이메일은 로그인에 쓴 외부 계정(소셜 로그인)에서 관리됩니다."}`. 지금은 `users.password_hash`가 NOT NULL이라 실제로는 생기지 않지만, 확인 없이 통과시키지 않게 코드가 막는다(단위 테스트로 보장) |
| 401 | 토큰 없음·무효(본문 검증보다 먼저), 또는 처리 중 그 토큰이 폐기됨(다른 기기의 로그아웃) |
| 409 | 다른 계정이 쓰는 이메일(대소문자 무시) `{"detail": "이미 사용 중인 이메일입니다."}` |
| 422 | 이메일 형식 위반(최대 254자, ASCII 주소만), `current_password` 누락·빈 값·128자 초과, **현재 비밀번호 틀림**. 틀림은 비밀번호 변경과 같은 형식 `{"detail": [{"type": "invalid_current_password", "loc": ["body", "current_password"], "msg": "현재 비밀번호가 올바르지 않습니다."}]}`이다. 401이 아닌 이유: 프론트는 401을 받으면 로그아웃하는데 토큰은 유효하고 입력 하나가 틀린 것이다 |
| 429 | 사용자당 시간당 인증 메일 5회 초과(재발송과 합산), 또는 **현재 비밀번호 연속 5회 오류로 5분 잠금**. 잠금은 `POST /api/auth/change-password`와 **같은 사용자 키를 공유**해서, 두 엔드포인트를 번갈아 써도 시도 횟수가 늘지 않는다(`PASSWORD_CHANGE_LOCK_*`) |

### POST /api/auth/resend-verification (인증 필요, 본문 없음)

인증 메일을 다시 보낸다(이전 인증 링크는 무효). 200 `{"ok": true, "message": "인증 메일을 다시 보냈습니다. ..."}`.
400 `"등록된 이메일이 없습니다. 먼저 이메일을 등록해주세요."` / `"이미 인증된 이메일입니다."`(본인 계정 상태만 알린다), 401, 429(위와 같은 한도).
**현재 비밀번호는 요구하지 않는다**: 이미 등록된(비밀번호 확인을 거쳐 저장된) 주소로만 보내고 주소를 바꾸지 않으므로, 토큰을 훔친 쪽이 이것으로 복구 경로를 얻지 못한다.
남용(메일 폭탄)은 사용자당 시간당 5회 한도로 막는다.

### POST /api/auth/verify-email (공개)

```json
{ "token": "메일 링크 fragment의 token" }
```

200 `{"ok": true, "message": "이메일 인증이 끝났습니다."}`. 잘못됐거나 만료됐거나 이미 쓴 토큰, 비밀번호 재설정 토큰은 400
`{"detail": "링크가 올바르지 않거나 만료됐습니다. 메일을 다시 요청해주세요."}`. `token`이 비었거나 128자를 넘으면 422. IP당 분당 10회 초과 429.

### POST /api/auth/find-username, POST /api/auth/forgot-password (공개)

```json
{ "email": "alice@example.com" }
```

항상(형식이 맞으면) **200과 같은 본문**:

```json
{ "ok": true, "message": "입력한 이메일이 인증된 계정의 이메일이면 안내 메일을 보냈습니다. 몇 분 안에 받지 못했다면 스팸함을 확인하거나, 로그인한 뒤 이메일 등록·인증 상태를 확인해주세요." }
```

- `find-username`: 인증된 이메일이면 그 계정의 아이디를 메일로 보낸다(응답에는 절대 넣지 않는다).
- `forgot-password`: 인증된 이메일이면 재설정 링크(30분, 1회용)를 보낸다. 다시 요청하면 이전 링크는 무효.
- 422: 이메일 형식 위반. 429: IP당 분당 5회(엔드포인트별) 또는 같은 이메일 시간당 3회(엔드포인트별, 메일 폭탄 방지) 초과. 429는 이메일이 있든 없든 같은 기준이라 존재 여부를 드러내지 않는다.

### POST /api/auth/reset-password (공개)

```json
{ "token": "메일 링크 fragment의 token", "new_password": "new-correct-horse" }
```

200 `{"ok": true, "message": "비밀번호를 바꿨습니다. 모든 기기에서 로그아웃됐으니 새 비밀번호로 로그인해주세요."}`(로그인 토큰은 주지 않는다).
성공하면 한 트랜잭션에서: 토큰 사용 처리 → 비밀번호 해시 교체 → **`token_version` 증가(그 사용자의 기존 JWT 전부 401)** → 그 사용자의 남은 재설정 토큰 무효.
그 뒤 그 아이디의 로그인 실패 잠금(모든 IP)과 비밀번호 변경 잠금을 푼다.

| 상태 | 언제 |
| --- | --- |
| 400 | 잘못됐거나 만료됐거나 이미 쓴 토큰, 이메일 인증 토큰(위와 같은 일반 메시지) |
| 422 | 새 비밀번호 규칙 위반(가입과 같은 규칙, 그 계정의 아이디·이메일 앞부분 포함도 거부). 토큰은 쓰이지 않고 남는다 |
| 429 | IP당 분당 10회 초과(토큰 무차별 대입 방지) |

### 메일 발송과 개발 모드 확인

| 환경변수 | 기본 | 뜻 |
| --- | --- | --- |
| `SMTP_HOST` | (없음) | 있으면 SMTP로 보낸다. **없으면 개발용 파일 발송기**를 자동으로 쓴다 |
| `SMTP_PORT` | `587` | STARTTLS(서버 인증서 검증) |
| `SMTP_USER` / `SMTP_PASSWORD` | (없음) | 있으면 로그인한다. 비밀값이므로 `.env`에만 둔다 |
| `MAIL_FROM` | `SMTP_USER` | 보내는 주소 |
| `FRONTEND_BASE_URL` | `http://localhost:5173` | 메일 링크의 앞부분 |

- SMTP는 연결·전송 timeout 10초, 재시도 없음. 실패해도 API 응답은 같다(이미 응답을 보낸 뒤다). 로그에는 마스킹한 수신자(`al***@ex***.com`)와 예외 클래스 이름만 남기고 비밀번호·본문·토큰은 남기지 않는다.
- **개발 모드**(`SMTP_HOST` 없음): 메일 전체(링크 원문 포함)가 `backend/backups/dev_mail.log`에 덧붙는다(`backups/`는 gitignore 대상). 콘솔에는 `dev mail 기록됨: al***@ex***.com (dev_mail.log)`만 찍힌다.
  로컬에서 흐름을 확인하려면 가입·이메일 등록 후 이 파일의 마지막 링크를 열면 된다. 이 파일에는 유효한 토큰이 있으므로 공유하지 않는다.
- 테스트는 메모리 발송기를 쓴다(`tests/conftest.py`의 `mail_outbox`, 실제 파일·SMTP를 쓰지 않는다).

### 실제 흐름 (2026-10-30, dev DB, TestClient, 메모리 발송기, 토큰은 앞 8자만)

| 단계 | 결과 |
| --- | --- |
| 가입(이메일 없음, 기존 형식) | 201 `{"user": {"id": ..., "username": "captest_..."}, "access_token": "eyJhbGci...", "token_type": "bearer"}` |
| `PUT /email` (`CAPTEST-DEMO...@EXAMPLE.COM`, 보강 전 측정: 지금은 `current_password`도 보내야 한다) | 200 `email`은 소문자로 저장, `email_verified: false`, `verification_sent: true`. 메일 링크 `http://localhost:5173/verify-email#token=bYUJXpOj...` |
| `verify-email` | 200 → `/me`의 `email_verified: true`. 같은 토큰 다시 쓰면 400 |
| `find-username`(인증된 이메일 / 없는 이메일) | 둘 다 200 + 같은 본문. 아이디는 메일에만 |
| `forgot-password` | 200(같은 본문), 메일 링크 `.../reset-password#token=I_UL3Y4U...` |
| `reset-password`(짧은 비밀번호) | 422, 토큰은 남음 |
| `reset-password` | 200. 같은 토큰 다시 쓰면 400 |
| 기존 JWT로 `/me` / 옛 비밀번호 로그인 / 새 비밀번호 로그인 | 401 / 401 / 200 |
| `account_token` 행 | `verify_email`·`reset_password` 두 행, 해시 64자, 둘 다 사용됨. 원문으로 찾으면 0행 |

이메일 변경 보강 뒤(2026-10-30, 같은 조건):

| 단계 | 결과 |
| --- | --- |
| `PUT /email`, `current_password` 없음 | 422 `{"detail": [{"type": "missing", "loc": ["body", "current_password"], "msg": "Field required"}]}` |
| `PUT /email`, 현재 비밀번호 틀림 | 422 `{"detail": [{"type": "invalid_current_password", "loc": ["body", "current_password"], "msg": "현재 비밀번호가 올바르지 않습니다."}]}` |
| `PUT /email`, 처음 등록(맞음) → `verify-email` | 200 `verification_sent: true`(알림 없음) → 200 |
| 옛 주소로 `forgot-password` | 재설정 링크 `...#token=ZuBLtEpO...` 대기 중 |
| `PUT /email`, 인증된 주소를 새 주소로(맞음) | 200 `email_verified: false`. 메일 두 통: 새 주소엔 인증 링크, 옛 주소엔 "계정 이메일이 변경되었습니다"(링크 없음, 새 주소는 `ca***@ex***.com`) |
| 변경 전에 받은 재설정 링크 사용 | 400(무효) |

### 한계

- **이메일 중복 409는 이메일 존재를 드러낸다**(가입과 `PUT /email`). 같은 주소를 두 계정에 쓰지 못하게 하려는 선택이다. 가입은 IP당 시간당 10회, `PUT /email`은 사용자당 시간당 5회로 묶여 대량 확인은 어렵다.
  복구 API(`find-username`, `forgot-password`)는 이 정보를 드러내지 않는다.
- **레이트 리밋은 메모리 기반**이다(서버 재시작 시 초기화, 여러 워커·인스턴스면 따로 센다, 프록시 뒤에서는 모든 요청이 같은 IP). 위 "레이트 리밋" 참고.
- **이메일 변경 보호(2026-10-30 보강)**: 이제 현재 비밀번호가 필수이고, 이전 인증 주소로 변경 알림이 가고, 남은 재설정 링크가 무효가 된다(위 `PUT /api/auth/email`).
  남은 위험: 비밀번호와 토큰이 **둘 다** 털리면 여전히 이메일을 바꿀 수 있다(알림으로 알 수만 있다). 알림 메일에는 "되돌리기" 링크가 없어서,
  원래 주인은 로그인해 직접 되돌려야 한다. 변경 직후 일정 시간 재설정 금지나 되돌리기 링크는 아직 없다.
- **기존 계정은 로그인한 뒤 이메일을 등록·인증해야 복구할 수 있다.** 이메일이 없거나 인증 전인 계정은 아이디·비밀번호를 잊으면 복구할 수 없다.
- 이메일 형식은 보수적인 ASCII 규칙이다(국제화 도메인, 따옴표가 있는 로컬파트는 422).
- 메일은 응답 뒤 같은 프로세스에서 보낸다(큐 없음). 서버가 그 사이에 멈추면 메일이 가지 않을 수 있고 재시도하지 않는다. SMTP가 느리면(최대 10초) 그 작업이 워커 스레드를 잡는다.
- 비밀번호 재설정은 새 비밀번호가 이전과 같아도 막지 않는다(옛 해시와 비교하지 않는다).
- DB를 백업에서 복원하면 `token_version`과 토큰 사용 기록이 되돌아갈 수 있다(위 "토큰 폐기"의 주의와 같다).

---

## 소셜 로그인: 구글·네이버 (2026-10-30)

Authorization Code 방식이고 **서버가 code를 받아 처리**한다. 기존 아이디/비밀번호 로그인과 응답 형식은 그대로이고, 소셜 로그인도 마지막에 같은 형식
(`{"user": {"id", "username"}, "access_token", "token_type": "bearer"}`)을 받는다. 코드: `app/oauth.py`(순수: state·쿠키 서명, PKCE, 계정 결정),
`app/oauth_providers.py`(공급자 통신), `app/oauth_db.py`(DB), `app/api/oauth.py`(라우트).

### 흐름과 프론트가 할 일

1. 로그인 화면이 `GET /api/auth/oauth/providers`로 켜진 공급자를 받아 버튼을 보인다. 버튼은 **브라우저를 이동**시킨다(fetch가 아니다):
   `location.href = "http://localhost:8000/api/auth/oauth/google/login"`(백엔드 주소 + `login_path`).
2. 백엔드가 공급자 로그인·동의 화면으로 보낸다. 사용자가 동의하면 공급자가 백엔드 콜백으로 돌려보낸다.
3. 백엔드가 확인을 끝내면 `{FRONTEND_BASE_URL}/oauth/callback#code=...`로 보낸다. **코드는 쿼리가 아니라 fragment(`#code=`)**에 있다
   (프론트 서버 로그와 Referer에 남지 않게 하려는 것으로, 이메일 인증 링크와 같다).
4. 프론트 `/oauth/callback` 화면이 `location.hash`에서 `code`를 읽고, `history.replaceState`로 주소창에서 지운 뒤
   `POST /api/auth/oauth/exchange {"code": ...}`를 보낸다. 200 응답의 `access_token`을 지금처럼 저장하면 로그인이 끝난다(400이면 "다시 로그인").
   **코드는 60초 1회용**이므로 화면이 열리자마자 한 번만 보낸다.
5. 실패하면 `{FRONTEND_BASE_URL}/login?error=...`로 온다. 로그인 화면은 `error`를 읽어 안내한다.

| `error` | 뜻 | 프론트 안내 예 |
| --- | --- | --- |
| `oauth_denied` | 사용자가 공급자 화면에서 동의를 거부했다 | "로그인을 취소했습니다." |
| `oauth_failed` | state·쿠키 검증 실패(오래된 탭, 다른 브라우저, 위조), 공급자 통신 실패·시간 초과, 계정 처리 실패 | "로그인에 실패했습니다. 다시 시도해주세요." |
| `oauth_unavailable` | 그 공급자의 키가 서버에 설정되지 않았다 | "지금은 이 방법으로 로그인할 수 없습니다." |

상세 원인은 URL에 넣지 않는다(서버 로그에 사유 코드만 남는다).

### GET /api/auth/oauth/providers (공개)

```json
{"providers": [{"id": "google", "name": "Google", "login_path": "/api/auth/oauth/google/login"},
               {"id": "naver", "name": "네이버", "login_path": "/api/auth/oauth/naver/login"}]}
```

client id와 secret이 **둘 다** 설정된 공급자만 나온다. 키 값(client id 포함)은 내보내지 않는다. 키가 하나도 없으면 `{"providers": []}`.

### GET /api/auth/oauth/{provider}/login (공개, 브라우저 이동)

`provider`는 `google` | `naver`(그 밖은 404 `{"detail": "지원하지 않는 로그인 방식입니다."}`).

- 키가 없으면 **503이 아니라** `302 {FRONTEND_BASE_URL}/login?error=oauth_unavailable`이다. 브라우저 이동이라 JSON을 보여줄 화면이 없기 때문이다.
- 있으면 302로 공급자 인증 페이지로 보낸다. 이때 서명한 `state`(공급자, 무작위 nonce, 만료 10분, 서버 시크릿으로 HMAC)를 싣는다.
  같은 nonce를 담은 쿠키 `oauth_state`도 심는다(`HttpOnly; SameSite=Lax; Path=/api/auth/oauth; Max-Age=600`, `OAUTH_REDIRECT_BASE`가 https면 `Secure`).
- **구글**: `scope=openid email profile`, PKCE(`code_challenge_method=S256`), OIDC `nonce`, `prompt=select_account`.
  PKCE verifier는 **state가 아니라 쿠키에만** 있다. state는 공급자 URL과 콜백 URL에 실리므로, 거기에 verifier가 있으면 code를 가로챈 쪽이 verifier도 얻는다.
- **네이버**: 요청 항목(이름·이메일)은 URL이 아니라 네이버 개발자 센터에서 고른다.
- 응답 헤더 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`. 레이트 리밋: IP당 분당 20회(429).

### GET /api/auth/oauth/{provider}/callback (공개, 공급자가 브라우저를 보냄)

1. `error`가 있으면 → `access_denied`면 `oauth_denied`, 그 밖은 `oauth_failed`.
2. `code`·`state`가 없거나 너무 길면 `oauth_failed`.
3. `state` 서명·만료·종류, 쿠키 서명·만료·종류, 둘의 공급자·nonce가 모두 맞아야 한다. 아니면 `oauth_failed`.
   공격자가 자기 브라우저에서 받은 state·code로 피해자를 공격자 계정에 로그인시키는 로그인 CSRF를 막는다. 쿠키는 그 브라우저에서 시작한 로그인에만 있다.
4. 서버가 client secret으로 code를 토큰으로 바꾸고 프로필을 얻는다. 공급자 통신은 timeout (5, 10)초, 일시 오류(연결·timeout·5xx·429)만 1번 재시도, 합계 15초 상한이다(`app.http_retry`).
   실패·시간 초과는 `oauth_failed`이고, 로그에는 사유 코드(`token_http`, `token_error`, `id_token_invalid`, `profile_http`, `profile_invalid`)만 남는다.
   - **구글**: 토큰 엔드포인트에서 **서버끼리 TLS로 직접 받은** `id_token`의 클레임을 쓴다. `iss`(accounts.google.com), `aud`(우리 client id), `exp`, `nonce`(이번 로그인의 nonce)를 반드시 확인한다.
     서명(JWKS) 검증은 하지 않는다. 직접 받은 토큰은 TLS 서버 인증으로 발급자를 확인할 수 있기 때문이다(OpenID Connect Core 3.1.3.7 6번). JWKS를 받아 캐시하는 외부 호출도 필요 없다.
     브라우저를 거쳐 온 id_token은 받지 않는다. 쓰는 값은 `sub`, `email`, `email_verified`, `name`이다.
   - **네이버**: `POST https://nid.naver.com/oauth2.0/token`(client secret은 URL이 아니라 본문에 담는다), `GET https://openapi.naver.com/v1/nid/me`로 `id`, `email`, `name`을 받는다.
     네이버는 실패도 200 + `{"error": ...}`로 줄 수 있어 본문으로 판정한다. 네이버가 준 이메일은 검증된 것으로 취급한다.
5. 계정을 정한다(아래 "계정 연결 규칙"). 60초 1회용 코드를 만들어 `302 {FRONTEND_BASE_URL}/oauth/callback#code=...`로 보내고 쿠키를 지운다. 레이트 리밋: IP당 분당 20회(login과 따로 센다).

### POST /api/auth/oauth/exchange (공개)

```json
{ "code": "콜백 fragment의 code" }
```

200은 아이디/비밀번호 로그인과 같은 형식이다. 잘못됐거나 만료됐거나(60초) 이미 쓴 코드는 모두 같은 400 `{"detail": "로그인 코드가 올바르지 않거나 만료됐습니다. 다시 로그인해주세요."}`이다.
`code`가 비었거나 128자를 넘으면 422. IP당 분당 10회 초과는 429.
코드는 `account_token`(purpose `oauth_login`)에 **SHA-256만** 저장한다. 사용은 조건부 UPDATE 한 문장이라 동시에 두 번 보내도 한 번만 성공한다.
메모리가 아니라 DB에 두므로 워커가 여럿이거나 재시작해도 맞다. 이메일 인증·재설정 토큰은 목적이 달라 이 코드로 쓸 수 없다.

### 계정 연결 규칙 (`app.oauth.decide_account`)

| 상황 | 결과 |
| --- | --- |
| (provider, 공급자 사용자 id)가 이미 연결돼 있음 | 그 사용자로 로그인. 이메일은 보지 않는다(공급자 쪽 이메일이 바뀌어도 같은 사용자) |
| 공급자 이메일이 검증됐고, 같은 이메일의 로컬 계정이 있고, 그 계정의 이메일도 **인증됨** | 그 계정에 연결(로그인). 둘 다 같은 주소의 소유를 확인한 경우만 같은 사람으로 본다 |
| 같은 이메일의 로컬 계정이 있지만 어느 쪽이든 미검증 | **연결하지 않는다.** 새 계정을 만들고 이메일은 비운다. 미인증 주소를 이용한 계정 탈취를 막고 이메일 유일성도 지킨다 |
| 충돌 없음 | 새 계정. 사용자명 `google_` / `naver_` + 무작위 8자리 hex(아이디 규칙 안, 충돌 시 다시 뽑음). 검증된 이메일이면 `email`과 인증 상태를 채운다. **비밀번호 없음** |

구글은 `email_verified`가 true일 때만 검증된 것으로 본다. 미검증 이메일은 새 계정에도 저장하지 않는다.

### 비밀번호 없는 계정 (소셜로 만든 계정)

`users.password_hash`는 이제 NULL을 허용한다(마이그레이션 `c7a2e5f81d30`).

- **아이디/비밀번호 로그인**: 항상 실패한다. 없는 아이디와 같은 401이고, 더미 해시 검증을 해서 응답 시간도 같다.
- **`POST /api/auth/change-password`**: 400 `{"detail": "소셜 계정은 비밀번호가 없습니다. 인증된 이메일이 있으면 비밀번호 찾기로 새 비밀번호를 정할 수 있습니다."}`.
- **`PUT /api/auth/email`**: 400(현재 비밀번호를 확인할 수 없음, 위 "계정 복구").
- **비밀번호 설정**: 인증된 이메일이 있으면 `forgot-password` → `reset-password`로 비밀번호를 정할 수 있다. 그 뒤에는 아이디/비밀번호 로그인도 된다.

### 환경변수와 공급자 콘솔 설정

| 환경변수 | 기본 | 뜻 |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | (없음) | 둘 다 있어야 구글 버튼이 켜진다 |
| `NAVER_LOGIN_CLIENT_ID` / `NAVER_LOGIN_CLIENT_SECRET` | (없음) | 둘 다 있어야 네이버 버튼이 켜진다. **`NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`(뉴스 검색 API 키)과 다른 이름이다.** 뉴스 키가 로그인 키로 잘못 쓰여 버튼이 켜지지 않게 나눴다 |
| `OAUTH_REDIRECT_BASE` | `http://localhost:8000` | 백엔드 공개 주소. 콜백은 이 값 + `/api/auth/oauth/{provider}/callback`. https면 쿠키에 Secure |
| `FRONTEND_BASE_URL` | `http://localhost:5173` | 성공·오류 리디렉션 앞부분(계정 복구 메일 링크와 공유) |
| `RATE_LIMIT_OAUTH_PER_MIN` / `RATE_LIMIT_OAUTH_EXCHANGE_PER_MIN` | `20` / `10` | IP당 분당 login·callback(각각) / exchange |

공급자 콘솔에 등록할 **리디렉션 URI**(로컬 기준, 배포하면 `OAUTH_REDIRECT_BASE`에 맞춰 바꾼다):

- 구글(Google Cloud Console > API 및 서비스 > 사용자 인증 정보 > OAuth 클라이언트 ID, 유형 "웹 애플리케이션"): 승인된 리디렉션 URI `http://localhost:8000/api/auth/oauth/google/callback`.
  OAuth 동의 화면이 "테스트" 상태면 등록한 테스트 사용자만 로그인할 수 있다.
- 네이버(네이버 개발자 센터 > 애플리케이션 등록, 사용 API "네이버 로그인", 제공 정보 이름·이메일): 서비스 URL `http://localhost:5173`, Callback URL `http://localhost:8000/api/auth/oauth/naver/callback`.
  **"개발 중" 상태에서는 애플리케이션의 멤버 관리에 등록한 네이버 아이디만 로그인할 수 있다.** 다른 사람은 검수를 받아 "서비스 적용" 상태가 돼야 한다.

### 한계

- **실제 키로 한 확인은 아직 없다.** 모든 테스트는 가짜 공급자 응답이다. 키를 설정한 뒤 직접 눌러 확인해야 한다.
  구글·네이버 응답 형식이 바뀌면 `app/oauth_providers.py`의 파싱을 고쳐야 한다.
- 구글 id_token의 서명은 검증하지 않는다(TLS로 직접 받은 응답이라는 근거, 위 4번). 토큰 엔드포인트 주소를 바꿔치기하는 설정 실수는 막지 못한다.
- **uvicorn 기본 접근 로그는 콜백 URL의 쿼리(공급자 `code`, `state`)를 남긴다**(앱의 요청 로그는 쿼리를 남기지 않는다). 공급자 code만으로는 client secret 없이 토큰을 받을 수 없고(구글은 PKCE verifier도 필요), 한 번만 쓸 수 있다.
  운영에서는 접근 로그 쿼리를 끄거나 가리는 것을 권장한다.
- 1회용 코드는 브라우저에 묶여 있지 않다(60초 안에 코드를 손에 넣은 쪽이 먼저 바꿀 수 있다). fragment라 서버 로그·Referer에는 남지 않는다.
- 레이트 리밋은 메모리 기반이다(위 "레이트 리밋").
- 계정 연결 해제, 이미 로그인한 계정에 소셜 계정을 수동으로 연결하는 기능, 소셜 계정 탈퇴는 없다.
- 공급자 계정이 지워지거나 동의를 철회해도 우리 쪽 연결은 남는다(다음 로그인에서 실패할 뿐이다).
- 마이그레이션을 내리면 소셜 연결과 1회용 코드는 사라진다. 비밀번호 없는 계정에는 검증되지 않는 표식 값(`!no-password`)이 들어가 로그인할 수 없게 되고, 비밀번호 찾기로 다시 정해야 한다.

---

## 회원 탈퇴 (2026-10-30)

### DELETE /api/auth/me (인증 필요)

그 사용자의 계정과 **모든 데이터**를 지운다(되돌릴 수 없음). 지우는 것: 관심종목, 모의투자 계좌·보유·체결, 리서치 리포트와 tool 호출 이력, 포트폴리오 진단,
계정 토큰(이메일 인증·재설정·로그인 코드), 소셜 로그인 연결, 사용자 행. 다른 사용자의 데이터는 건드리지 않는다. 지운 뒤 그 사용자의 모든 JWT는 401.
본문(JSON, DELETE에도 본문을 보낸다):

```json
{ "current_password": "...", "confirm_username": "..." }
```

본인 확인은 계정 종류에 따라 다르다.

| 계정 | 필요한 것 | 결과 |
| --- | --- | --- |
| 비밀번호가 있는 계정 | `current_password`(필수, 없으면 422 `missing`) | 맞으면 **200** `{"ok": true, "status": "deleted", "message": "회원 탈퇴가 끝났습니다. 계정과 모든 데이터를 지웠습니다."}`. 틀리면 422(비밀번호 변경과 같은 `invalid_current_password`), 연속 5회 오류면 비밀번호 변경과 같은 잠금(429) |
| 비밀번호 없는 소셜 계정 + 인증된 이메일 | 없음(본문 `{}`) | **202** `{"ok": true, "status": "confirmation_sent", ...}`. 그 주소로 30분 1회용 확인 링크 `{FRONTEND_BASE_URL}/delete-account#token=...`(fragment)를 보낸다. 링크를 열어 확정해야 지워진다(재설정과 같은 메일 소유 확인) |
| 비밀번호도 인증된 이메일도 없는 소셜 계정 | 10분 안에 로그인해 받은 토큰 + `confirm_username`(아이디, 대소문자 무시) | 맞으면 200 deleted. 아니면 400 `"이 계정은 비밀번호와 인증된 이메일이 없어 본인 확인을 위해 방금(10분 안) 다시 로그인한 뒤, 확인용으로 아이디를 함께 보내야 탈퇴할 수 있습니다."` |

401(토큰 없음·무효, 본문 검증보다 먼저), 429(사용자당 시간당 5회 `RATE_LIMIT_ACCOUNT_DELETE_PER_HOUR`). 응답은 `Cache-Control: no-store`.

### POST /api/auth/delete-account/confirm (공개)

```json
{ "token": "확인 메일 링크 fragment의 token" }
```

200 deleted. 잘못됐거나 만료(30분)됐거나 이미 쓴 토큰, 다른 목적의 토큰(재설정 등)은 같은 400 `{"detail": "링크가 올바르지 않거나 만료됐습니다. 메일을 다시 요청해주세요."}`. IP당 분당 10회.
**프론트가 할 일**: 설정 화면에 "회원 탈퇴"(비밀번호 입력 → `DELETE /api/auth/me`), 202면 "메일을 확인하세요" 안내, `/delete-account` 화면이 `location.hash`의 `token`을 읽어
`history.replaceState`로 지운 뒤 이 API로 보낸다. 탈퇴가 끝나면 저장된 토큰을 지운다.

## 보안 통제 (2026-10-30)

전체 위협 모델·점검 결과·운영 체크리스트는 [SECURITY.md](SECURITY.md).

- **보안 헤더**(모든 응답): `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`.
  `/api/auth/*`와 `Authorization` 헤더가 있는 요청의 응답은 `Cache-Control: no-store`(이미 지정된 값은 덮지 않는다). `SECURITY_HSTS=1`이면 `Strict-Transport-Security: max-age=31536000; includeSubDomains`(HTTPS 전용).
  CORS 설정은 그대로다.
- **요청 본문 크기**: 1MB(`MAX_REQUEST_BODY_BYTES`)를 넘으면 413 `{"detail": "요청 본문이 너무 큽니다."}`. Content-Length와 길이를 밝히지 않은(chunked) 본문 모두.
- **공개 GET 거친 레이트 리밋**: 모든 `GET /api/*`를 IP당 분당 600회(`RATE_LIMIT_PUBLIC_GET_PER_MIN`)로 센다(로그인 여부와 상관없이). 넘으면 429 + `Retry-After`.
- **메일 수신 주소 상한**: 같은 주소로 보내는 메일(인증·아이디·재설정·변경 알림·탈퇴 확인 합산) 시간당 10통(`RATE_LIMIT_MAIL_PER_RECIPIENT_PER_HOUR`). 넘으면 보내지 않는다(응답은 같다).
- **API 문서**: `APP_ENV=production`이면 `/docs`, `/redoc`, `/openapi.json`을 끈다(개발 기본은 그대로).
- **비밀번호 정책**: 8~128자 + 흔한 비밀번호·한 문자 반복·아이디/이메일 앞부분 포함 거부. 로그인 성공 시 argon2 파라미터가 오래된 해시는 재해시한다.
  아이디·이메일 포함은 가입·변경은 `"type": "password_context"`, 흔한 비밀번호는 가입에서 `"type": "value_error"`로 422다.
- **JWT**: `tv` 클레임이 필수다(2026-10-30, 아래 "토큰 폐기"). `JWT_SECRET`이 `.env.example`의 `<...>` 자리표시 그대로면 모든 모드에서 기동하지 않는다.
- **접근 로그**: 앱 접근 로그와 uvicorn 접근 로그 모두 쿼리 문자열을 남기지 않는다(소셜 로그인 콜백의 code·state 등). 실행 명령은 그대로다.
- **보안 이벤트 로그**: 로거 `app.security`, 한 줄 `event=<signup|login|login_locked|logout|password_change|password_reset|email_change|email_verify|oauth_login|account_delete|account_delete_requested> user=<id|-> result=<...> ip=<IPv4 /24, IPv6 /48>`.
  아이디·이메일·비밀번호·토큰은 남기지 않는다.
- **답변 링크**: AI 답변에서 근거 링크(도구 결과의 url)가 아닌 URL이 든 문장은 지운다(프롬프트 인젝션 피싱 링크 방지).

---

## POST /api/research

질문에 필요한 tool(주가/뉴스/공시/RAG 검색)을 모델이 스스로 골라 호출하고, 그 결과로 답변을 만든다.
**응답 시간은 보통 2~25초**(로컬 LLM)이므로 프론트에서 로딩 상태와 넉넉한 타임아웃(60초 이상)을 두세요.

### 요청

```json
{ "question": "현대차 오늘 왜 올랐어? 최근 주가랑 관련 뉴스 같이 확인해줘." }
```

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `question` | string | 필수. 앞뒤 공백 제거 후 1자 이상, **최대 1000자**. 위반 시 422 |
| `previous_report_id` | int 또는 null | 선택. 이어서 물을 **바로 직전 보고서**의 ID(1 이상, **내 리포트만**). 생략하거나 null이면 독립 질문. 없는 ID나 남의 리포트 ID면 404, 정수가 아니거나 1 미만이면 422 |
| `locale` | string 또는 null | 선택(최대 20자, 2026-11-05). `ko-KR`·`en-US`·`ja-JP`·`zh-CN`(별칭 `ja`·`en_us` 등 허용). 없거나 공백이면 계정 설정, **지원하지 않는 값은 오류 없이 `ko-KR`**. 아래 "다국어" 절 |

#### ETF 질문 처리 규칙 (2026-10-14, 응답 필드는 그대로)

질문에 ETF가 나오면(`asset_type`이 `etf`인 종목, "코덱스200"처럼 한글 브랜드로 써도 된다) 서버가 아래를 보정한다.
주식만 나오는 질문은 이전과 같다(아래 "빈 섹션 정리"만 공통).

- **가격 흐름 + 뉴스를 함께 본다**: 모델이 일부만 호출했어도 질문 속 종목마다 주가 조회(`stock_tool`), ETF마다 뉴스(`news_tool`)를
  덧붙인다. 모델이 tool 없이 바로 답하려 해도("KODEX 인버스는 어떤 상품이야?") 조회한 뒤 답한다. 그래서 ETF 질문의 `used_tools`에는
  보통 `stock_tool`과 `news_tool`이 함께 들어간다.
- **기간**: 기간을 말하지 않았으면 최근 **20거래일**을 본다("KODEX 200 주가 알려줘"도 20거래일 흐름 + 마지막 하루 등락률). 기간을 말하면
  그 기간("KODEX 200TR 3개월 추이"). ETF와 다른 종목을 비교하는 질문("KODEX 200이랑 삼성전자 중에 최근 어느 게 더 올랐어?")은 두 종목을
  **같은 기간**으로 조회하고, 답변 끝에 서버가 계산한 비교 문장을 붙인다(예: `※ 조회 결과로 계산한 비교: KODEX 200은 2026-09-10~2026-10-14
  20거래일간 -13.85%, 삼성전자는 2026-09-07~2026-10-08 20거래일간 -2.59%입니다. 등락률은 삼성전자가 더 높습니다. 다만 종목마다 저장된 마지막
  거래일이 달라 같은 날짜 구간의 비교는 아닙니다.`).
- **뉴스 수치를 쓰지 않는다**: ETF 뉴스에는 같은 계열 다른 상품의 수치가 섞여 있다("반도체 레버리지 20% 급락…KODEX 레버리지는 12.45% 하락").
  그래서 (1) `news_tool`은 ETF 이름이 직접 나오는 기사만 돌려주고, (2) 모델에는 기사 속 수치를 가린 사본을 넘기며(`sources`에는 원문 그대로),
  (3) 답변을 주가 조회 결과와 **문장 단위로 대조해 맞지 않는 문장을 서버가 지운다**: 문장에 나온 등락률(%)·가격(원)·거래량(주)이 그 문장이
  가리키는 종목의 조회 결과에 없으면(다른 종목의 수치를 붙였거나 결과에 없는 평균값 등을 계산한 경우), "20거래일간 상승"처럼 기간 등락 방향이
  실제와 반대면, 추종지수·구성종목·보수·순자산·구조를 기사 근거 없이 단정하면, 질문에 없는 다른 종목 이름이나 시장 지수(코스피·코스닥)에
  수치를 붙이거나 지수 등락을 기사 근거 없이 단정하면, 수집하지 않는 거래대금 수치나 가격 없는 "최고·최저" 주장을 쓰면,
  한글 단위 수치("1만 8천만 주")를 쓰면, 주가를 조회했는데 "조회하지 못했다"고 쓰면 그 문장을 지운다.
- **가격과 뉴스는 서버가 쓴다**: ETF 답변의 `## 주가 동향`은 주가 조회 요약을 그대로, `## 관련 뉴스`는 그 ETF 이름이 직접 나오는 기사
  제목(출처·날짜, 최대 5건)을 그대로 넣는다. 모델이 쓴 가격·뉴스 섹션은 버리고, 모델에게서는 그 밖의 섹션(원인 분석·위험요인 등)만 위
  대조를 거쳐 남긴다. 모델에게 뉴스를 옮겨 쓰게 했을 때 다른 상품 기사를 이 ETF의 일처럼 쓰거나(분배금, 순매수 순위) 조회한 주가를
  "조회하지 못했다"고 쓰는 일이 반복돼서 바꿨다. 기사 제목은 인용이라 제목 속 수치(다른 상품의 등락률 등)가 그대로 보일 수 있다.
- **ETF를 회사처럼 쓰지 않는다**: 최종 답변 단계에 규칙을 추가했다(실적·재무·공시 언급 금지, 조회 결과에 없는 구성종목·추종지수·보수 단정 금지,
  매수/매도 권유 금지).
- **레버리지·인버스 고정 문구**: 이름에 레버리지·인버스·2X 등이 있는 ETF면 답변 끝에 서버가 아래 문구를 붙인다(LLM이 쓰지 않는다):
  `※ {ETF명}은/는 이름으로 보아 {레버리지|인버스|인버스 레버리지} ETF입니다. 레버리지·인버스 ETF는 하루 동안의 기초지수 수익률을 정해진
  배수(인버스는 반대 방향)로 따라가도록 설계돼 있어, 여러 날 보유하면 복리 효과 때문에 같은 기간 기초지수 수익률에 배수를 곱한 값과 결과가
  달라질 수 있습니다. 시장이 오르내림을 반복할수록 그 차이가 커질 수 있습니다.`
- **시장 비교·공시 tool**: ETF에 호출되면 "ETF라서 비교하지 않는다/공시를 수집하지 않는다"는 사유를 결과에 담는다(서버가 덧붙이지는 않는다).
- **빈 섹션 정리(모든 답변)**: 결과가 없는 `## 섹션`에 "(이 섹션은 … 빈칸입니다.)", "해당 없음" 같은 자리표시만 쓴 경우 그 섹션을 지운다.
  원인은 최종 답변 안내가 쓸 수 있는 섹션 다섯 개를 나열해서 모델이 결과가 없는 섹션도 제목을 쓰는 것이다(안내 문구는 주식 답변에 영향을
  주지 않도록 그대로 두고 출력만 정리한다).

#### 사건(뉴스·공시) 이후 주가 반응 질문 (2026-10-14, 응답 필드는 그대로)

"삼성전자 공시 나온 뒤 주가 어땠어?", "KT 뉴스 이후 주가 반응은?"처럼 **사건 이후의 주가 반응**을 물으면, 답의 수치는
`GET /api/stocks/{ticker}/event-reactions`와 **같은 계산 함수**의 결과로 서버가 쓴다. 이 질문 유형이 아닌 질문은 프롬프트·tool 선택·기간 처리가
이전과 완전히 같다(기능 추가 전후로 벤치마크·합성 질문 685개의 1차 요청이 바이트 단위로 같음을 확인).

- **언제 이 경로를 타나(게이트)**: 질문에 사건어(공시·뉴스·기사·보도·발표·실적·소식, 유상증자·자기주식 등)가 있고 **그 뒤에** 시간 관계어
  (이후, 직후, 나온 뒤, 발표 후/다음날, "~했을 때 주가", 동사 없이 "공시 후 주가"처럼 바로 뒤에 주가·흐름·반응이 오는 경우)가 오거나, "반응"이
  사건어와 함께 있을 때. 그리고 질문(후속 질문이면 직전 질문)에서 **종목이 정확히 하나** 특정될 때. "삼성전자 최근 뉴스 알려줘", "삼성전자 공시
  알려줘", "뉴스랑 주가 움직임 같이 알려줘", "9월 이후 뉴스"(기간이 사건어 앞)는 걸리지 않는다. 종목이 없거나 둘 이상이면 기존 경로로 간다.
- **무엇을 조회하나**: `event_reaction_tool`(LLM에는 tool 목록으로 노출하지 않고 서버가 실행한다). 사건 종류는 질문 낱말로 정한다(공시 쪽 낱말만
  있으면 공시, 뉴스·기사·보도·소식만 있으면 뉴스, 그 밖에는 둘 다). 질문에 실적·영업·유상증자·무상증자·합병·분할·배당·자기주식(자사주)·공급계약·
  소송 중 하나가 있으면 제목에 그 낱말이 든 사건만 고른다. 최근 120일, 유형별로 **5개 항목**을 보여준다(뉴스는 아래처럼 같은 날짜를 묶은 항목 5개,
  그래서 묶기 전 사건은 유형별 15건까지 조회한다). 뉴스 사건은 REST와 같은 종목 관련도 필터를 거친다. ETF는 뉴스만이고 초과수익률은 없다.
  이 경로에서는 모델의 1차 tool 선택 호출을 하지 않는다(주가·뉴스 tool 결과의 무관한 기간 수치와 기사 본문 주장이 해석에 섞이는 것을 막기 위해).
- **서버가 쓰는 부분**(`## 사건 이후 주가 반응`): 공시는 건별로 접수일·제목, **뉴스는 반응 시작일이 같은 것끼리 한 항목**으로 묶어
  "YYYY-MM-DD · 뉴스 N건"(시각 없이 날짜만) 아래에 제목 최대 3개와 "외 K건", 그리고 한 번의 수치를 쓴다(같은 반응 구간의 사건은 기준일이 같아
  수치가 같다). 수치는 기준 종가와 기준일, 1/5/20거래일 수익률과 지수 수익률·초과수익률, 거래량 비율, "같은 기간 다른 사건 N건"이다.
  값이 없으면 사유를 그대로 쓴다("아직 20거래일이 지나지 않아 계산할 수 없음"). 묶기는 답변 구성에서만 하고 REST 응답은 건별 그대로다.
  공시가 있으면 "공시는 접수일 기준이며 시각 정보가 없어 장중 반응 여부는 알 수 없습니다"를 한 번 쓴다. 가격 조정(분할 등) 경고가 있는 사건은
  수익률을 표시하지 않고 경고만 쓴다. 이어서 요약 문장("조회한 사건 N건(날짜 M개) 중 5거래일 수익률은 K개 날짜에서 계산됐고 평균 X%…")을 쓴다.
  표본은 **날짜 단위**다(같은 날짜의 사건은 수치가 같아 하나로 센다. 같은 날 뉴스 5건이 표본 5건이 되지 않게), 표본(날짜)이 3개 미만인 기간은
  "일반화하기 어렵습니다"를 붙인다. 그리고 답변 끝에 이 기능 전용 면책 문구를 그대로 붙인다(`※ 사건 이후의 주가 움직임은 시간적 선후관계일 뿐
  사건이 원인이라는 뜻이 아닙니다. …`). 대상 사건이 없거나 계산된 수익률이 하나도 없으면(사건이 모두 너무 최근) 모델을 부르지 않고 서버가 쓴 섹션만으로 답한다.
- **LLM 해석은 기본 꺼짐(2026-10-14)**: 환경변수 `EVENT_REACTION_LLM_INTERPRETATION`이 `1`/`true`/`on`/`yes`일 때만 아래 "모델이 쓰는 부분"을 만든다. 꺼져 있으면 이 경로에서 Ollama를 부르지 않고 `## 사건 이후 주가 반응`, 요약 문장, 면책 문구만으로 답한다(응답이 즉시 나오고 Ollama가 꺼져 있어도 답이 나온다; 질문 이해와 tool 선택은 코드 게이트가 한다). 이유: 검증을 통과한 해석이 30회 중 3~6회만 남고 남아도 주의 문구 수준이며, 어색한 문장이 검증을 통과하는 일도 있었다. 해석 생성·검증 코드는 그대로 두었다.
- **모델이 쓰는 부분**(플래그를 켠 경우만, `## 해석`, 한두 문장): 인과 표현 금지, 결과에 있는 수치·날짜만, 기간은 1/5/20거래일로만, null 추정 금지, 미래 일반화·
  매매 권유 금지, **표에 이미 있는 수치를 되풀이하지 말고 사건들 사이의 비교나 주의할 점만 쓰고 쓸 말이 없으면 비우라**, 초과수익률은 %p·수익률은 %로
  구분하라고 최종 답변 단계에 지시한다. 모델에는 사건 **제목만** 넘긴다(기사 본문의 수치·주장은 넘기지 않는다).
- **해석 문장 검증**: 서버가 문장 단위로 결과와 대조해 다음을 지운다. 주가 움직임을 사건에 원인으로 연결하는 표현("때문에", "영향으로",
  "덕분에", "탓에", "원인은", "~로 인해", "반영됐다", "뉴스에 반응했다" 등. 단 "단정할 수 없다"고 밝힌 문장은 남긴다), 결과에 없는 수치
  (%·%p·원·배·건·거래일, 한글 단위 수치), 결과에 없는 날짜와 기간("한 달", "일주일", "3개월"), 상승/하락 방향이 결과와 반대인 서술, 계산되지
  않은 기간을 추정한 문장, 미래 예측·일반화·매수/매도 권유, 다른 종목에 수치를 붙인 문장, 영어 낱말이 섞인 문장, 내용 없는 안내 문장("다음과
  같습니다"), 번호를 매겨 사건 제목을 다시 나열한 줄, "관련이 없다/무관" 같은 관계 단정, null을 "반응이 없었다"로 메운 문장, 템플릿 잔재. 추가된 규칙(2026-10-14): **표의 수치·날짜·사유를 되풀이할 뿐 새 비교나 관찰이 없는 문장**("최근 1일간 주가는 -6.77% 떨어졌습니다."), **기간과 사유를
  섞은 문장**("5일간 주가는 20거래일 경과 전이므로…"), **%와 %p를 혼동한 문장**(초과수익률 5.16%p를 "5.16%"로), **내용 없는 마무리**("위와
  같이 나타났습니다", "시간 순서를 따랐습니다")를 지우고, 사건 비교·겹치는 사건·시장 전체 흐름·표본 주의 같은 **비교·주의 표지가 하나도 없는 문장**은
  남기지 않는다. 그래서 해석은 대부분 비게 된다(실측 30회 중 3~6회만 남음). 다 지워지면 **`## 해석` 제목째 생략**하고 서버가 쓴 섹션만으로 답한다
  (빈 제목·대체 문구 없음). Ollama가 응답하지 않아도 같다(이 경로만 503 대신 해석 없는 답).
- **저장과 근거**: `used_tools`는 `["event_reaction_tool"]`, `sources`는 사건마다 하나(기존 형식, `type`은 `news`/`disclosure`, `tool`은
  `event_reaction_tool`). 리포트 저장·후속 질문은 다른 질문과 같다.
- **한계**: 사건 이후의 움직임은 인과가 아니다(시장·다른 사건의 영향을 분리하지 못한다). 공시는 시각을 모른다. 달력은 종목 시세 기준이다.
  뉴스는 최신 기사 위주로만 수집돼 오래된 사건이 적고, 최근 사건은 5·20거래일 값이 아직 없어 표본이 적다. 게이트는 낱말 규칙이라 "주가가 공시에
  어떻게 반응했나"처럼 어순이 다르면 놓칠 수 있다("반응"+사건어 규칙으로 대부분 잡힌다).
- **측정(실제 llama3.1:8b, 질문 10개 x 3회)**: 최종 답변을 tool 결과와 대조해 (1) 결과에 없는 수치 (2) 인과 단정 (3) null 추정 (4) 템플릿 잔재
  (5) 고정 문구 누락 (6) 수치 되풀이만 있는 해석 (7) %/%p 단위 혼동 (8) 제목에 종목 이름이 없는 뉴스 사건 포함을 셌다. 코드를 고치면서 나온 사례를
  검증기에 반영했고, 최종 코드에서 30회씩 연속 두 번(60회) 8개 항목 모두 0건이었다(`tests/test_event_reaction_slow.py`, `pytest -m slow`).
  판단 근거는 서버가 쓴 섹션이고, 해석은 있어도 한두 문장의 주의 문구 수준이다.

#### 후속 질문 (`previous_report_id`)

"그럼 최근 뉴스는?", "그건 코스피랑 비교하면 어때?"처럼 종목명이 빠진 질문도 직전 보고서의 종목을 이어받아 답한다.
서버가 그 보고서의 **질문과 답변 텍스트**를 대화 기록으로 모델에 함께 넘기는 방식이다.

```json
{ "question": "그럼 최근 뉴스는?", "previous_report_id": 48 }
```

- **바로 직전 1개만** 잇는다. 직전 보고서가 다시 이어받은 더 앞선 보고서까지는 따라가지 않는다(3턴째 질문은 2턴째 보고서 ID만 넘기면 된다).
- 직전 답변이 800자를 넘으면 앞 800자만 맥락으로 쓰인다(응답과 저장된 보고서는 그대로).
- 이번 질문에 다른 종목명이 있으면(예: "카카오는 어때?") 직전 종목이 아니라 이번 질문의 종목을 따른다.
- 새 보고서에는 `previous_report_id`가 저장되고, 응답과 `GET`(목록/상세)에 그대로 나온다. 프론트는 이 값으로 대화 스레드를 이을 수 있다.
- 직전 보고서를 나중에 `DELETE`해도 후속 보고서는 남고, 그 `previous_report_id`만 `null`이 된다.

### 응답 200

```json
{
  "answer": "현대차의 최근 뉴스는 다음과 같습니다.\n\n*   아반떼 가격 논란 속 더 커져 돌아온 투싼…5000만 원 넘어서나\n ... \n\n 주가는 전일比 1.38% 하락했습니다.",
  "used_tools": ["news_tool", "stock_tool"],
  "sources": [
    {
      "tool": "news_tool",
      "type": "news",
      "title": "아반떼 가격 논란 속 더 커져 돌아온 투싼…5000만 원 넘어서나",
      "company_names": ["현대차"],
      "company_filter": null,
      "url": "https://n.news.naver.com/mnews/article/011/0004665230?sid=101"
    }
  ],
  "report_id": 18,
  "previous_report_id": null
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `answer` | string | 최종 답변(한국어, 줄바꿈 `\n` 포함, 마크다운 목록 문법이 섞여 올 수 있음) |
| `used_tools` | string[] | 실제로 실행된 tool 이름(호출 순서). `stock_tool`, `news_tool`, `disclosure_tool`, `rag_search_tool`, `market_tool`(시장 지수 비교 질문일 때, 또는 등락 원인 질문에서 지수 데이터가 최신일 때) 중. 개념 질문 등은 **빈 배열**일 수 있다 |
| `sources` | object[] | 답변의 근거 문서(뉴스/공시). 주가·시장 지수만 조회했거나 tool을 안 썼으면 **빈 배열** |
| `sources[].tool` | string | 이 근거를 가져온 tool |
| `sources[].type` | `"news"` \| `"disclosure"` | 근거 종류 |
| `sources[].title` | string | 기사/공시 제목 |
| `sources[].company_names` | string[] | 관련 종목명 |
| `sources[].company_filter` | string \| null | rag 검색에서 종목으로 범위를 좁힌 경우 그 종목명, 아니면 null |
| `sources[].url` | string \| null | 원문 링크. 없으면 null |
| `report_id` | int \| null | 저장된 리포트 ID. `GET /api/research/{report_id}`로 다시 조회 가능. **저장에 실패하면 null**(답변 자체는 정상 반환) |
| `previous_report_id` | int 또는 null | 요청으로 이어받은 직전 보고서 ID. 후속 질문이 아니면 null |
| `disclaimer` | string | 면책 문구(`GET /api/disclaimer`의 `text`와 같은 값). 답변과 함께 화면에 보여주면 된다. **2026-10-07 추가된 필드**로, 위 예시 JSON에는 생략했다 |
| `locale` | string | **2026-11-09(38차) 추가.** 답변이 실제로 쓰인 언어(요청 `locale` > 계정 설정 > `ko-KR`, 지원하지 않는 값을 보냈으면 `"ko-KR"`). 위 예시 JSON에는 생략했다(`"locale": "ko-KR"`) |
| `translation_status` | string \| null | **2026-11-09(38차) 추가.** `locale`이 `ko-KR`이면 `null`(번역 안 함). 그 밖에는 `"translated"` \| `"localized"` \| `"fallback_summary"`(아래 "다국어" 절). `GET /api/research/{id}?view=record`에 저장되는 값과 같다. 위 예시에는 생략했다(`"translation_status": null`) |

참고: 종목이 DB에 없으면 오류가 아니라 200 응답의 `answer`에 "찾지 못했다"는 안내가 담긴다.

#### 최근 N거래일 주가 흐름 질문: 코드가 계산해 답한다 (2026-11-06, 응답 필드는 그대로)

예: "삼성전자의 최근 20거래일 주가 흐름을 분석해 줘. 1일·5일·20일 수익률, 5일·20일 이동평균, 최근 거래량과 20거래일 평균 거래량을 비교해 줘. 어떤 국면인지 판단해 줘."

전에는 이 질문이 LLM 경로로 가서 기간 해석이 "1일·5일·20일 수익률"의 "1일"을 조회 기간으로 읽었고, 1일 시세만 조회했다. LLM이 쓴 5·20일 값은 조회 결과에 없어서
숫자 검증이 1일 요약(종가·등락률·거래량)으로 바꿨고, 요청한 항목이 모두 빠졌다. 지금은 `app/price_flow.py`가 LLM 없이 답한다.

- 받는 질문: 종목이 정확히 하나이고, 이동평균(이동평균·이평선·N일선)과 함께 수익률·거래량·국면·추세·흐름·분석 중 하나를 묻거나, 수익률 창을 둘 이상 나열한 질문("1일·5일·20일 수익률").
  원인 질문(왜·이유·원인), 전망 질문(오를까 등), 주문 문장은 받지 않는다. 종목이 둘 이상이면 기존 경로다.
- 창: 질문에 쓴 값을 쓴다(수익률 "1일·5일·20일", 이동평균 "5일·20일"·"20일선", 평균 거래량 "60일 평균 거래량", 범위 "최근 60거래일", 최대 120). 말하지 않으면 수익률 1·5·20거래일,
  5·20일선, 직전 20거래일 평균 거래량, 최근 20거래일. 모두 행(거래일) 수 기준이다.
- 답 순서: 데이터 기준일·신선도·지연 경고(전망 경로의 `data_check`와 같은 문장) → 수익률(비교한 날짜·종가) → 이동평균(종가와의 차이, 단기선·장기선 위치) →
  거래량(기준일 거래량, 기준일을 뺀 직전 N거래일 평균과 배수) → 최근 N거래일 범위(기간 수익률, 최고·최저 종가) → 국면 판단 → 예측·권유가 아니라는 안내.
- 국면: 전망 경로와 같은 코드 규칙(`classify_regime`, 아래 "국면 규칙")이다. 제목은 질문의 이름으로 바꿔 쓴다: 단기 반등 시도 / 상승 추세 / 상승 추세(조정 중) / 하락 추세 /
  횡보(방향 불분명) / 추세 미확정. 추세 미확정은 데이터 경고(5영업일 초과 지연·거래일 누락), 60거래일 시세 부족, 지표 묶음 부족 중 하나일 때이며 그 이유를 적는다.
  판정 근거 문장과 판정에 쓴 값(종가, 20·60일선, 5·20·60거래일 수익률, 20거래일 최저 종가 대비)을 함께 보인다.
- 기록: `used_tools` = `["stock_tool"]`, 근거 자료는 "주가 조회". tool 기록의 `called_via`는 `price_flow`이고 결과에 계산값(`flow`)·지표·데이터 점검이 들어 있다.

#### 전망·후속 결론 질문 (2026-11-03, 응답 필드는 그대로)

"SK하이닉스 일주일 안에 오를까?", "에코프로 지금 사도 될까?" 같은 전망·방향·매수 가부 질문과, 이어지는 "그래서 결론이 뭐야?", "니 생각에 오를거 같냐", "하락 위험은 어떤데?" 같은 후속 질문은 **LLM을 부르지 않고** 서버 코드(`app/outlook.py`)가 DB에서 지표를 계산하고 문장도 만든다. 응답 모양(`answer`, `used_tools`, `sources`, `report_id`, `previous_report_id`)은 그대로다. 새 라우트는 없다.

**의도 분류 우선순위**(`app/agent.py` `_answer`): 주문 실행 문장(아래 "전망 2차") > 채팅 브리핑("내 보유종목 오늘 어때?") > **전망·후속 결론** > 사건 반응 > 용어 사전 > 보유·기간 수익률·단순 시세 > 일반 도구(LLM). 모의 주문은 채팅이 아니라 `POST /api/portfolio/orders`(엄격 매칭)이고, 주문 문장("삼성전자 10주 사줘", "매수 주문 넣어줘", "N주 매수해줘")은 전망 경로로 가지 않는다. 원인·실적·뉴스·공시·용어("왜", "실적", "뉴스", "뜻")를 묻는 질문도 전망 경로가 아니다.

| 분류 | 예 | 종목 |
| --- | --- | --- |
| 전망 | "다음 주 주가 전망", "일주일 안에 오를까?", "지금 사도 돼?", "이번 주에 매수하는 게 위험할까?" | 질문에서 정확히 1개. 없으면 직전 리포트의 종목. 여러 개면 기존 흐름(임의 선택 없음) |
| 후속 결론 | "그래서 결론이 뭐야?", "올라 아니면 내려?", "네 판단으로는 상승이야 하락이야?" | 직전 리포트의 종목·기간 |
| 후속 확률 | "하락 가능성은 얼마나 돼?" | 같음. 검증된 확률이 없다고 밝히고 신호 개수로 답한다 |
| 후속 위험 | "하락 위험은 어떤데?" | 같음. 하락 쪽 신호, 변동성, 최고 종가 대비 하락률, 확인할 가격대 |

- 직전 종목: 프론트는 대화 이력 대신 `previous_report_id`(직전 리포트 하나)를 보낸다. 서버는 그 리포트의 종목(`research_report.company_id`, 도구 결과 종목이 정확히 1개일 때 저장)을 이어받는다. 후속 답변도 같은 종목의 `stock_tool` 기록을 남기므로 3턴 이상 이어진다. 기간은 답변 첫 항목 `- 이름(종목코드), 분석 기간 1주일(5거래일): 라벨`에서 이어받는다.
- 직전 종목이 없으면 "질문에서 종목을 찾지 못했습니다. 어떤 종목에 대한 질문인지 …" 한 줄로 되묻는다(`used_tools` 빈 배열). 시장·지수·환율·포트폴리오·업종·추천을 묻는 말이 있으면 되묻지 않고 기존 흐름이다.
- 용어 사전은 후속·판단 표현("그래서/그럼/결론/올라/내려/사도/팔아/전망/어때/생각/판단")이 있으면 정의 경로로 가지 않는다. "사전에 없는 용어" 안내는 용어를 묻는 질문일 때만 나온다. "공매도 뜻 알려줘"처럼 "뜻 알려줘"도 정의 질문으로 본다.

**분석 기간**(질문에 없으면 1주일, 후속 질문은 직전 기간 유지):

| 기간 | 질문 표현 | 판정 기간 | 수익률 창 | 이동평균 | 고점·저점 조회 | 근거 뉴스·공시 |
| --- | --- | --- | --- | --- | --- | --- |
| 1주일 | 일주일, 1주, 다음 주, 이번 주 | 5거래일 | 5·20거래일 | 5·20일 | 최근 20거래일 | 최근 14일 |
| 1개월 | 한 달, 1개월, 다음 달 | 20거래일 | 20·60거래일 | 20·60일 | 최근 60거래일 | 최근 30일 |
| 3개월 | 3개월, 석 달, 분기 | 60거래일 | 60·120거래일 | 60·120일 | 최근 120거래일 | 최근 90일 |

**계산식**(기준일 = 장중 행을 뺀 마지막 시세일, 종가 기준, `stock_price` 최근 260거래일):
- N거래일 수익률 = (기준일 종가 ÷ N거래일 전 종가 - 1) × 100. 기준 종가의 날짜를 같이 쓴다.
- 이동평균 = 기준일 포함 최근 n일 종가의 단순평균. 교차 = 판정 기간 안에서 단기선 > 장기선 여부가 바뀐 날(`app.backtest.sma_target`, 백테스트 `sma_cross`와 같은 판정).
- RSI(14) = 최근 14거래일 종가 변화의 상승폭 평균 ÷ (상승폭 평균 + 하락폭 평균) × 100(단순평균).
- MACD(12,26,9): MACD = EMA12 - EMA26, 시그널 = MACD의 EMA9(EMA 평활 계수 2/(n+1), 첫 값으로 시작).
- 거래량 비율 = 기준일 거래량 ÷ 직전 20거래일 평균 거래량. 변동성 = 최근 20거래일 일간 수익률(%)의 표본표준편차.
- 최고 종가 대비 = (기준일 종가 ÷ 조회 구간 최고 종가 - 1) × 100. **수익률이 아니다**(답변에 항상 "최고 종가 대비"와 조회 기간을 쓴다).
- 지수 대비 = 판정 기간 동안 종목과 상장 시장 지수(코스피/코스닥)의 등락률 차이(`market_tool.compare_returns`). ETF는 비교하지 않는다. 원/달러 환율은 `exchange_rate`에 있는 범위만 보여 주고 신호로 쓰지 않는다. 업종·실적·HBM 데이터는 DB에 없어 "데이터 없음"으로 쓴다.

**신호 규칙**(`app/outlook.py` `classify_signals`, 같은 입력이면 같은 결과):

| 지표 | 상승 쪽 | 하락 쪽 | 그 밖 |
| --- | --- | --- | --- |
| 종가 vs 단기 이동평균 | 종가 > 이동평균 | 종가 < 이동평균 | 같으면 중립 |
| 종가 vs 장기 이동평균 | 종가 > 이동평균 | 종가 < 이동평균 | 같으면 중립 |
| 이동평균 배열 | 단기선 > 장기선 | 단기선 ≤ 장기선 | |
| 최근 교차(판정 기간 안) | 골든크로스 | 데드크로스 | 교차 없음 = 중립 |
| 판정 기간 수익률 | > +1% | < -1% | 그 사이 중립 |
| RSI(14) | ≤ 30(과매도) | ≥ 70(과열) | 그 사이 중립 |
| MACD | MACD > 시그널 | MACD < 시그널 | 같으면 중립 |
| 거래량 | 평균의 1.5배 이상 + 기준일 상승 | 평균의 1.5배 이상 + 기준일 하락 | 그 밖 중립 |
| 지수 대비 | +2%p 이상 | -2%p 이하 | 그 사이 중립 |

**최종 라벨**(답변 맨 앞): 상승·하락 신호 개수 차이가 2개 이상이면 `상승 우위`/`하락 우위`, 미만이면 `중립`. 계산한 신호 지표가 5개 미만이거나, 마지막 시세가 기대 거래일(Asia/Seoul, 평일 15:30 전이면 직전 평일)보다 **5영업일을 넘게** 늦거나, 계산 구간에 코스피 지수에는 있는 거래일의 시세가 빠졌으면 `판단 유보`. 1~5영업일 늦으면 경고 문장을 붙이고 계산한다. 라벨은 현재 신호의 종합이며 예측이 아니라고 답변에 밝힌다. 단정("오를 것입니다"), 목표가, 확률·기대수익률, 매수·매도 권유는 쓰지 않는다.

**답변 섹션**(프론트 `AnswerSections`가 `## 제목`마다 번호를 붙인다): 최종 신호 판단 / 판단 근거 요약 / 상승 쪽 신호 / 하락 쪽 신호 / 기술적 지표 / 뉴스·공시 / 시장 상황 / 확인할 가격대 / 불확실성과 한계 / 데이터 기준일·출처. 근거가 없는 항목은 한 줄("관련 뉴스를 찾지 못했습니다")로 쓰고, ETF에 뉴스가 없으면 뉴스·공시 섹션을 생략한다. 레버리지·인버스 ETF는 고정 설명을 붙인다. 후속 결론 답변은 최종 신호 판단·판단 근거 요약·불확실성과 한계·데이터 기준일·출처(위험 질문이면 하락 쪽 신호·위험 지표·확인할 가격대 추가)만 쓴다.

**`used_tools`·`sources`**: 전망 답변은 `stock_tool`(지표 계산), 지수 대비가 있으면 `market_tool`, 주식이면 `news_tool`·`disclosure_tool`(ETF는 뉴스가 있을 때만 `news_tool`). `sources`는 답변에 인용한 뉴스·공시뿐이다.

#### 전망 2차: 신호 묶음·과거 검증·간접 뉴스·실적·주문 문장 (2026-11-03, 응답 필드는 그대로)

위 "전망·후속 결론 질문"의 판정·섹션은 이 절이 우선한다. 이 기능은 **규칙 기반 방향성 신호 분석**이며 예측 모델이 아니다.

**신호 묶음(판정은 묶음당 1표)**: 지표마다 1표로 세면 서로 거의 같은 지표(종가 vs 5일선, 종가 vs 20일선, 5일선 vs 20일선)가 독립 근거 세 개처럼 세졌다. 묶음은 학습 구간(신호일과 수익률 구간 끝이 2025-12-31 이하)의 전 종목 일별 신호(-1/0/+1) 피어슨 상관을 세 기간에 걸쳐 절댓값 평균하고, 평균 연결 군집을 |r| 0.5에서 끊어 정했다(`scripts/backtest_outlook.py --analyze`). 단일 연결은 거래량을 뺀 전부가 사슬로 한 묶음이 되어 쓰지 않았다.

| 묶음 | 지표 | 근거(학습 구간 상관) |
| --- | --- | --- |
| 단기 흐름 | 종가 vs 단기 이동평균, 기간 수익률, 지수 대비 | 기간 수익률-지수 대비 +0.70, 단기선-기간 수익률 +0.63~0.65 |
| 중기 추세 | 종가 vs 장기 이동평균, 이동평균 배열 | +0.54~0.74 |
| 이동평균 교차 / RSI(14) / MACD / 거래량 | 각각 단독 | 다른 묶음과 평균 상관 0.5 미만. RSI는 추세와 음의 상관(과열 = 하락 쪽) |

- 묶음 표: 묶음 안 상승 쪽이 많으면 상승 1표, 하락 쪽이 많으면 하락 1표, 같으면 중립. 상승·하락 표 차이 2표 이상이면 우위, 미만이면 중립, 계산한 묶음이 3개 미만이거나 데이터 점검이 유보면 판단 유보(`app/outlook.py` `group_votes`·`judge`).
- 기준값(표 차이 2): 학습 구간에서 1과 2를 비교했지만 둘 다 예측력이 없어(상승 우위의 이후 상승 비율이 전체보다 1~3%p 낮음, 95% 구간 0 포함) 기존 값 2를 유지했다.
- 답변의 "판단 근거 요약"은 묶음 표 집계, 묶음마다 구성 지표와 그 쪽(상승 쪽/하락 쪽/중립), 개별 지표 개수, 과거 검증 한 줄을 보인다. 상승·하락 쪽 신호 줄 앞에는 `[묶음]`이 붙는다.

**과거 검증**(`scripts/backtest_outlook.py`, 결과 `app/data/outlook_backtest.json`):
- 방법: DB의 전 종목(350)·전 기간 종가로 매 거래일 답변과 같은 함수(`compute_indicators`·`classify_signals`·묶음 판정)를 신호일까지의 260거래일에만 적용하고, 기간별 판정을 그 기간 뒤(1주일 → 5거래일, 1개월 → 20, 3개월 → 60) 종가 수익률 > 0 여부와 비교한다. 미래 시세는 신호 계산에 들어가지 않는다(테스트로 확인).
- 구간(측정 전에 정함): 학습 = 신호일과 수익률 구간 끝이 모두 2025-12-31 이하, 검증 = 신호일 2026-01-02 이후. 묶음·기준값은 학습 구간에서만 정했고 검증 구간은 한 번만 쟀다.
- 95% 구간: 같은 날 여러 종목이 겹치고 수익률 구간도 서로 겹쳐서, 거래일을 h개(5·20·60)씩 묶은 블록을 군집으로 보는 군집 강건 분산으로 "라벨 상승 비율 - 같은 구간 전체 상승 비율"의 구간을 낸다.
- 결과(검증 구간, 묶음 판정): 세 기간 모두 어떤 라벨도 전체 평균과 유의한 차이가 없었다.

| 기간 | 신호일 | 상승 우위 | 하락 우위 | 중립 | 전체 |
| --- | --- | --- | --- | --- | --- |
| 1주일(5거래일 뒤) | 2026-01-02~10-27 | 51.4%(+1.6%p, -1.8~+5.0, 23,150) | 48.5%(-1.4%p, -5.0~+2.3, 23,660) | 49.7%(-0.2%p, 21,189) | 49.9% |
| 1개월(20거래일 뒤) | 2026-01-02~10-02 | 51.1%(+1.5%p, -4.5~+7.4, 19,665) | 49.5%(-0.2%p, 16,146) | 48.7%(-1.0%p, 27,028) | 49.6% |
| 3개월(60거래일 뒤) | 2026-01-02~08-04 | 47.4%(+1.1%p, -4.8~+7.1, 17,164) | 44.8%(-1.5%p, -15.2~+12.2, 10,371) | 46.1%(-0.2%p, 21,558) | 46.3% |

- 답변에는 기간·라벨에 맞는 한 줄을 그대로 보인다: "과거 검증(검증 구간 신호일 …, n거래일 뒤): 같은 라벨(…)일 때 상승 비율 X%(같은 기간 전체 Y%, 표본 N, 차이 D%p, 95% 구간 a~b%p). 과거에 관측된 빈도이며 확률이나 예측이 아닙니다." 95% 구간이 0을 포함하면 "과거 검증에서 이 신호 조합은 전체 평균과 뚜렷한 차이가 없었습니다."를 붙인다. 판단 유보는 검증 대상이 아니라고 쓴다.
- 한계: 현재 상장 350종목만 있다(생존 편향). 거래비용·세금 미반영. 검증 구간이 약 10개월이라 3개월 기간은 독립 블록이 4개뿐이어서 구간이 넓다. 데이터 지연·누락에 따른 판단 유보는 과거에 적용하지 않았다. 결과 파일은 데이터가 늘면 스크립트로 다시 만든다(`--collect` → `--final`, 규칙을 바꾸면 학습 구간부터 다시).

**뉴스 섹션 구분**:
- `## 뉴스·공시(직접 관련)`: 제목에 요청 종목 이름(별칭·줄임말 포함)이 있는 기사만(`app.news_relevance.evidence_news`). 이름 비교는 전각/반각(NFKC)·대소문자를 무시하고, 영문·숫자와 한글 사이 공백 유무("SK 하이닉스")와 ETF 이름의 띄어쓰기("TIGER미국S&P500")를 같게 본다. 별칭 추가: `SK하닉`, `삼바`, `엘지엔솔`, `현차`(기존 `하닉`·`삼전` 포함). 두 종목에 걸리는 줄임말(`셀트` = 셀트리온·셀트리온제약)은 넣지 않았다.
- `## 업종·산업 뉴스(간접)`(주식) / `## 기초지수 관련 뉴스(간접)`(지수형 ETF): 이 종목 기사가 아니며 신호 판정에 쓰지 않는다고 첫 줄에 밝힌다. 직접 관련이 0건이어도 이 섹션으로 채우지 않는다. `sources`(근거 링크)에는 직접 관련만 들어간다.
  - 업종 매핑(`app/related_news.py`): `company.sector`가 모두 비어 있어, DB에 있는 업종 ETF 구성종목(운용사 공시)으로 만든다. 반도체 = KODEX 반도체·TIGER 반도체TOP10·KODEX AI반도체TOP2플러스(25종목), 2차전지 = KODEX 2차전지산업(17), 전력설비 = KODEX AI전력핵심설비(11). 그 밖은 업종 미분류라 간접 섹션이 없다. 업종 키워드(반도체: 반도체·HBM·메모리·D램·낸드·파운드리 등)가 제목에 있는 최근 기사(기간별 14·30·90일) 최대 3건.
  - 기초지수: ETF 이름에서 나스닥100·S&P500·필라델피아반도체·다우존스·코스닥150·코스피200(KODEX/TIGER 200, 레버리지·인버스)을 읽고, 그 이름이 제목에 있는 기사.
- 채팅 뉴스 도구(`news_tool`)도 같은 함수를 쓴다. 직접 관련 기사는 `news`, 간접 기사는 `indirect_news`와 `indirect_note`(이 종목 기사가 아니라는 설명, 최근 30일)로 따로 준다. 예: TIGER 미국나스닥100은 `found: false`(제목에 이 ETF 이름이 있는 기사 없음)이면서 나스닥100 관련 간접 기사가 붙는다.
- 간접 기사의 `published_at`은 KST(`+09:00`) ISO 문자열이다(2026-11-11 42차, 직접 기사와 같은 `app.display_dates.kst_iso`). 전에는 UTC(`+00:00`)로 나가 일일 브리핑 `holdings.items[].indirect_news.items[].published_at`·`sources[].date`와 `news_tool`의 간접 기사 날짜(앞 10자)가 KST 기준과 하루 어긋날 수 있었다.

**실적(참고, 신호 판정에 쓰지 않음)**: `## 실적(참고)`에 최근 보고서 2개를 보인다. 매출액·영업이익·당기순이익(억 원), 전년 동기 대비 증감률(같은 보고서에 적힌 전기 금액 기준, 당기나 전기가 적자면 증감률 생략), 연결/별도, 접수일, DART 접수번호. 분기·반기보고서는 그 분기(3개월) 금액, 사업보고서는 연간 금액이다. ETF는 섹션이 없다. 데이터: `quarterly_earnings`(마이그레이션 `b7e3d91a4c58`), DART 단일회사 주요계정(`fnlttSinglAcnt`), 연결 우선·없으면 별도, 2024~2026 사업연도 보고서(최근 8개 이상). 수집: `python -m scripts.refresh_data --only fx --earnings`(기본 단계에 넣지 않음. 분기마다 한 번, 종목당 최대 12회 요청, 요청 간격 0.15초, DART 사용 한도 초과 코드 020이면 즉시 중단, `data_refresh_log`에는 기록하지 않음).

**주문 문장**: 채팅에는 주문 경로가 없다(주문은 `POST /api/portfolio/orders`, 엄격 매칭). "삼성전자 10주 사줘", "하이닉스 팔아줘", "N주 시장가로 사줘", "보유분 전부 매도해줘" 같은 주문 실행 요청은 LLM에 보내지 않고 "채팅에서는 주문을 실행하지 않습니다. 모의투자 화면에서 종목과 수량을 확인한 뒤 주문할 수 있어요."와, 질문에서 종목이 정확히 하나(정확 이름·별칭)면 단순 시세와 같은 방식의 참고 시세 한 줄로 답한다(`used_tools: ["stock_tool"]`). 의도 우선순위의 맨 앞이다. "사도 될까?"(전망), "10주 사면 얼마야?", "주문 방법 알려줘"는 주문 문장이 아니다.

#### 전망 6차: 간결·상세 모드·첫 줄 방향·근거와 묶음 일치·뉴스·공시 날짜 (2026-11-04 밤, 응답 필드는 그대로)

아래 "전망 5차"의 방향·국면·신뢰도 규칙은 그대로다. 이 절은 답변 배치와 모드, 근거 생성, 날짜 표시를 바꾼다. 평가 기준은 `evals/forecast_check.py` 26차 절.

**간결 전망과 상세 리서치**(`outlook.wants_detail`, `agent._forecast_mode`)

| 모드 | 질문 예 | 답 |
| --- | --- | --- |
| 간결(brief) | 이 종목 오를 것 같아? 일주일 안에 상승할까 하락할까? 다음 주 전망 알려줘. 지금 방향이 어때? 앞으로 한 달은 어떨까? | `outlook.brief_answer`, 빈 줄 빼고 8줄 이하, 섹션 없음 |
| 상세(forecast) | 자세히·자세하게·상세·전체·리포트·보고서·기술적 지표·지표까지·모두 반영·포함해서·…까지 반영/포함/분석/확인·심층·분석해줘 | 기존 13개 섹션 리서치 리포트 |

- 상세 요청은 "뉴스·공시·실적"이 들어 있어도 전망 경로다("삼성전자 뉴스와 공시까지 모두 반영해서 다음 주 전망 분석해줘", "LG전자 실적까지 포함해서 한 달 전망 리포트로 써줘"). "삼성전자 실적 알려줘", "최근 뉴스 알려줘"는 기존 흐름이다.
- 직전 분석에 이어 "전체적으로 자세히 분석해 줘", "기술적 지표까지 확인해줘", "자세히 보여줘"는 직전 종목·기간의 상세 리포트다(종목명 없이도). "실적까지 반영해서 다시 판단해줘"는 4차의 실적 후속 답이다.
- 두 모드는 같은 계산(`analyze`)·같은 방향(`direction_of`)·같은 근거(`group_evidence`)를 쓴다. 간결 답에서 상세로 넘어가도 방향과 근거 묶음은 같다.

**간결 답**

```
SK하이닉스(000660) · 향후 5거래일 · 방향: 상승 쪽(신뢰도 낮음)

상승 쪽입니다. 다만 신호가 약하고(5개 묶음 중 상승 2·하락 0·중립 3), 추세는 아직 하락 후 반등 시도(추세 전환 미확인) 단계이며, …신뢰도는 낮습니다.
- 상승 근거: [단기 흐름] 최근 5거래일 수익률 +2.91%(…); 같은 5거래일 코스피 대비 +2.92%p (같은 묶음의 반대 신호: 종가가 5일선 아래); [MACD] …
- 하락 위험: 하락 쪽으로 판정된 묶음은 없습니다. 엇갈린 묶음: [중기 추세] 5일선이 20일선 위, 골든크로스(상승)지만 종가가 20일선 아래(하락)라 묶음은 중립입니다. 추세 참고(신호 판정 표 밖): 20거래일 수익률 -23.24%(…); 종가가 60일선 아래.
- SK하이닉스(000660), 분석 기간 1주일(5거래일): 상승 우위
- 과거 검증: 과거 검증(검증 구간 …): 같은 라벨(상승 우위)일 때 상승 비율 51.8%(같은 기간 전체 49.9%, 표본 22,176, …). 과거에 관측된 빈도이며 확률이나 예측이 아닙니다. … 이 신호 조합의 방향 예측력은 확인되지 않았습니다.
- 시장 국면: 현재는 단기 반등 시도, 추세 전환은 미확인 (기준일 2026-11-03 종가) · 기준일(최신 거래일): 2026-11-03, 답변 생성 … KST · 데이터 경고: …
- 과거 시세 신호를 요약한 것이며 투자 권유가 아닙니다. 기술적 지표·뉴스·공시·실적까지 보려면 "자세히 분석해줘"라고 물어보세요.
```

**상세 답 머리말**: 제목(방향·신뢰도) → 방향 한 문장(`direction_headline`: "상승 쪽입니다. 다만 신호가 약하고 신뢰도는 낮습니다(5개 묶음 중 …).") → 데이터 경고 → 국면 줄 → `## 종합 판단` 이하. 5차의 "근거 요약" 줄은 종합 판단·신호 판정 상세와 겹쳐 뺐다. 종합 판단의 과거 검증 문장은 숫자 없이 "방향 예측력은 확인되지 않았습니다(아래 과거 검증)"이고, 숫자(상승 비율·표본·95% 구간)는 "과거 검증" 섹션 한 곳에만 있다. 데이터 경고 문장은 머리말에만 쓰고 "데이터 기준일·출처"에는 시세 상태·신선도만 쓴다.

**근거와 묶음 판정**(`outlook.group_evidence`, 5차까지의 `evidence_items` 대체)

| 구분 | 원천 | 표시 |
| --- | --- | --- |
| 상승 근거 | 상승 쪽 묶음 안의 상승 신호 | `[묶음] 문장`(같은 묶음의 반대 신호는 괄호로) |
| 하락 위험 | 하락 쪽 묶음 안의 하락 신호 | `[묶음] 문장`, 없으면 "하락 쪽으로 판정된 묶음은 없습니다." |
| 엇갈린 묶음 | 상승·하락 신호가 섞여 중립인 묶음 | "엇갈린 묶음: [중기 추세] …(상승)지만 …(하락)라 묶음은 중립입니다" |
| 추세 참고 | 신호 판정 표에 넣지 않는 20·60거래일 수익률, 60일선 위치, 큰 하루 등락 | "추세 참고(신호 판정 표 밖): …" |

교차(골든·데드크로스)는 같은 사실인 이동평균 배열 문장 안에만 쓰고 따로 세우지 않는다(SAME_FACT_AS 그대로). 상승 근거 항목 수 = 상승 묶음 수다(개별 신호 합과 섞지 않는다).

**새 후속 답**

| 종류 | 질문 예 | 답 |
| --- | --- | --- |
| filing | 3분기 잠정실적 공시는 언제 접수됐어? | 첫 문장에 최신 실적 공시의 접수일·DART 접수번호(거래소 공시는 "접수일이 곧 발표일"), 그 공시의 실적 줄, 정기보고서 접수 여부, 정정공시 여부 |
| signal | 골든크로스가 무슨 뜻이야? MACD가 시그널 위라는 게 무슨 뜻이야? | 정의, 지금 값과 이 분석의 기준, "개별 신호로는 상승 쪽이고, 이 신호가 속한 중기 추세 묶음은 중립입니다" |

**날짜**(`outlook.kst_date`, `outlook.disclosure_date`)

- 공시 날짜 = DART 접수번호 앞 8자리의 날짜(없으면 한국 시간 접수일). 공시 시각은 한국 시간 자정(UTC 전날 15:00)으로 저장돼 있어, 5차까지는 UTC 문자열 앞 10자를 써서 모든 공시가 하루 이르게 표시됐다(SK하이닉스 잠정실적 20261028800036이 "2026-10-27"). 이제 같은 공시는 종합 판단·뉴스·공시·실적 어디서나 같은 날짜다.
- 뉴스 날짜 = 한국 시간 발행일(UTC 날짜와 다른 기사 2,829건).
- 실적 줄의 거래소 잠정실적은 "접수·발표일"(거래소 공시는 접수가 곧 발표다). 정기보고서는 "접수일".
- 점검: `python -m scripts.check_source_dates`(DB 읽기만). 2026-11-04: 공시 8,722건 중 접수번호 날짜와 한국 시간 접수일이 다른 5건(정정·주요사항보고서, 수집 접수일이 하루 늦음, 외부 원본 확인 불가), 잠정실적 444건은 모두 일치.

#### 전망 5차: 단기 방향과 국면 분리·첫 문장 방향·짧은 후속 답·시세 신선도 문구 (2026-11-04, 응답 필드는 그대로)

아래 "전망 4차" 절의 **현재 판단 규칙**(묶음 판정과 국면이 같은 방향일 때만 상승·하락, 그 밖은 "중립(굳이 고르면 …)")과 후속 답 둘째 줄 형식은 이 절이 대체한다. 잠정실적·시세 상태·실적 줄은 4차 그대로다. 평가 기준은 `evals/forecast_check.py` 25차 절이 이 절을 따로 구현한다.

**원인**(24차): `current_judgment`가 국면을 방향의 필수 일치 조건으로 썼다. SK하이닉스 2026-11-03은 묶음 판정이 상승 우위(5개 묶음 중 상승 2·하락 0·중립 3)인데 국면이 "단기 반등 시도, 추세 전환은 미확인"이라 "현재 판단: 중립(굳이 고르면 상승 쪽)"이 됐다. "중립인데 굳이 고르면 상승"은 모순이고, 첫 질문에서는 방향을 말하지 않았다.

**세 값을 따로 둔다**

| 값 | 정하는 법(`app.outlook`) | 표시 |
| --- | --- | --- |
| 방향(분석 기간, 향후 5·20·60거래일) | `direction_of(label)`: 묶음 판정 그대로. 상승 우위 → 상승 쪽, 하락 우위 → 하락 쪽, 표 차이 2표 미만 → 중립, 묶음 3개 미만·시세 5영업일 초과 지연·거래일 누락 → 판단 유보. 국면은 보지 않는다(`DIRECTION_RULE`) | 제목 줄 `… · 향후 5거래일 · 방향: 상승 쪽(신뢰도 낮음)`, 첫 내용 문장 |
| 국면(추세) | 기존 `classify_regime` 그대로(6종) | 상태 줄 `현재는 단기 반등 시도, 추세 전환은 미확인 (기준일 … 종가)` |
| 신뢰도(상승 쪽·하락 쪽일 때) | `confidence_of`: 과거 검증이 유의하지 않거나 없으면 낮음, 유의하지만 우세한 쪽 묶음이 절반 미만이거나 시세 2영업일 이상 지연이면 보통, 그 밖은 높음(`CONFIDENCE_RULE`). 낮아도 방향을 중립으로 바꾸지 않는다 | 제목 줄 괄호, 방향 문장 "신뢰도는 낮습니다" |

예: 단기 상승 우위 + 하락 후 반등 시도 → 상승 쪽(국면은 반등 시도), 단기 하락 우위 + 상승 추세 중 조정 → 하락 쪽(국면은 조정), 단기 중립 + 상승 추세 지속 → 중립(국면은 상승 추세), 데이터 부족 → 판단 유보. 검증 구간에서 어떤 라벨도 유의하지 않아 지금 상승 쪽·하락 쪽의 신뢰도는 모두 "낮음"이다.

**방향 문장**(`direction_sentence`, 첫 문장이 방향이다)

- 상승 쪽: `상승 쪽입니다. 다만 신호가 약하고(5개 묶음 중 상승 2·하락 0·중립 3), 추세는 아직 하락 후 반등 시도(추세 전환 미확인) 단계이며, 과거 검증에서 이런 신호의 방향 예측력이 확인되지 않아 신뢰도는 낮습니다(2026-11-03 종가 기준).` 추세가 같은 방향이면 "추세(하락 추세 지속)도 같은 방향이지만".
- 중립: `현재는 상승과 하락 중 어느 쪽도 뚜렷하게 우세하지 않습니다(5개 묶음 중 상승 1·하락 2·중립 2, 하락 쪽 묶음이 1개 더 많지만 기준인 2개 차이에 못 미칩니다). 추세는 …입니다.`
- 판단 유보: `데이터가 부족해 현재는 방향 판단을 보류하겠습니다(마지막 시세가 8영업일 지연, …).`
- "굳이 하나만 고르면": 상승 쪽·하락 쪽이면 `하나만 고르면 상승 쪽입니다. …`, 중립·유보면 `하나만 고르라고 해도 …`(억지로 고르지 않는다). "중립인데 굳이 고르면" 표현은 쓰지 않는다.

**단독 전망**: 제목 줄(방향·신뢰도) → 상태 줄(국면) → 데이터 경고 → 방향 문장 → `근거 요약: 5개 묶음 중 …(약한 상승 우위) · 과거 검증에서 방향 예측력은 확인되지 않았습니다(표본 …, 95% 구간 …)` → `## 종합 판단` 이하(3차 구조 그대로). "신호 판정 상세"에 방향 판단 기준·신뢰도 기준 줄.

**후속 답**(첫 줄 = 제목·방향 · 국면 · `시세: 1영업일 이전 시세(미수집), … 종가 기준`, 둘째 줄 첫 문장 = 방향)

| 종류 | 질문 예 | 줄 수(빈 줄 제외) | 내용 |
| --- | --- | --- | --- |
| verdict | 오를 것 같아 내릴 것 같아? 상승할까 하락할까? 판단은? 현재 방향은 중립이야 상승이야? 상승 추세인데 다음 주 조정받을까? | 4 | 방향 문장, 근거/위험 한 줄, 과거 검증(과거 빈도, 미래 확률 아님) |
| pick | 굳이 하나만 고르면? 둘 중 하나만 선택해 봐 | 2 | `하나만 고르면 상승 쪽입니다. 다만 …` |
| why | 왜? 왜 상승 쪽이라고 판단했어? | 5 | `상승 쪽입니다. 그렇게 본 이유는 상승 쪽 묶음이 2개(단기 흐름, MACD)이고 하락 쪽 묶음은 없으며 중립이 3개로, 상승·하락 묶음 차이 2개가 판정 기준(2개)에 이르기 때문입니다(…).` 상승 근거, 반대 근거, 추세(따로 봄) |
| oneline | 한 줄로만 답해 | 1 | `… · 방향: 상승 쪽(신뢰도 낮음) · 상승 쪽입니다(5개 묶음 중 …, 약한 신호, 신뢰도 낮음). 현재는 … (기준일 … 종가).` |
| buy | 지금 사면 일주일 뒤 오를까? 살 만하다는 거야? | 5 | 방향 문장, 매수·매도 권하지 않음, 근거/위험, 과거 검증 |
| probability | 하락 추세인데 단기 반등할 가능성은? | 6 | 방향 문장, 검증된 확률 없음, 과거 빈도(과거에 관측된 빈도이며 미래 확률이 아님), 근거/위험, 가격대 |
| compare·risk·earnings·data | (4차와 같음) | 6 이하 | 둘째 줄이 방향 문장(data는 생성 시각·기준일이 먼저, 방향은 마지막 줄) |

- 종목 없이 직전 분석과 같은 기간을 다시 묻는 방향 질문("상승할까 하락할까?", "지금 사면 일주일 뒤 오를까?")은 전체 분석을 다시 찍지 않고 짧은 후속 답이다. 기간이 다르면 그 기간의 전체 분석이다.
- "왜?"·"한 줄로"는 직전 답에 표식이 있을 때만 이 경로다("왜 떨어졌어?" 같은 원인 질문은 기존 흐름).
- 같은 데이터·기간이면 후속 답의 방향은 바뀌지 않는다(같은 계산, 통합 테스트·평가 `check_stable25`).

**시세 신선도 문구**(`data_check`, `freshness_label`)

| 지연 | 경고 | 판단 |
| --- | --- | --- |
| 0 | 없음(최신 시세) | 그대로 |
| 1영업일 | `마지막 시세(2026-11-03)가 기대 거래일(2026-11-04)보다 1영업일 이전 데이터입니다(미수집: …). 이 분석은 … 종가 기준이며 최신 거래일 전망이 아닙니다.` | 그대로(유보하지 않음) |
| 2~5영업일 | `…보다 N영업일 지연된 데이터입니다(…)` | 그대로, 신뢰도 보통 이하 |
| 5영업일 초과 | `…보다 N영업일 지연(5영업일 초과)되어 데이터가 부족합니다. 판단을 유보합니다.` | 판단 유보 |
| 거래일 누락 | `가격 데이터 부족: 계산 구간에 … 시세가 없습니다(…). 판단을 유보합니다.` | 판단 유보 |

수집 상태(미수집·수집 실패·휴장일 추정·종목만 누락)는 4차 그대로 괄호에 쓴다. 기준일이 코스피 지수 거래일 목록에 없으면 "실제 거래일인지 확인하지 못했습니다" 경고를 붙인다. "N영업일 늦습니다"는 쓰지 않는다(마지막 시세일은 기대 거래일보다 과거다).

**과거 검증 통계 검토**(2026-11-04, 결과 파일 `app/data/outlook_backtest.json`은 그대로): 95% 구간은 신호일을 독립으로 보지 않고 거래일 h개 블록을 군집으로 본 군집 강건 분산(선형화)이다. 같은 날 여러 종목과 겹치는 h거래일 수익률을 블록으로 묶는다. 다만 군집 수가 1주일 41, 1개월 10, 3개월 4라 정규 근사(1.96)는 1개월·3개월 구간을 좁게 잡는다(t 분포, 2h 블록, 이중 군집으로는 더 넓다). 다시 계산한 방식 모두에서 9개 라벨 결과가 유의하지 않다는 결론은 같고, 3개월은 군집 4개라 어떤 방식으로도 구간을 신뢰하기 어렵다. 종목만 군집으로 보면(날짜 상관 무시) 유의한 결과가 생기는데 이는 독립 가정의 오류다. 표시 방식 변경은 하지 않았다(`evals/reports/forecast_round5_20261104.md` 6절).

#### 전망 4차: 현재 판단 먼저·신호 강도·잠정실적·시세 지연 구분 (2026-11-04, 응답 필드는 그대로)

아래 "전망 3차" 절의 후속 답 형식과 실적 줄 형식은 이 절이 우선한다. 문장은 모두 코드 템플릿이다(LLM 없음). 평가 기준은 `evals/forecast_check.py` 24차 절이 이 절을 따로 구현한다.

**현재 판단 규칙**(`app.outlook.current_judgment`, 상수 `JUDGMENT_RULE`). 묶음 판정(기술적 신호의 우위)과 국면(추세)을 합쳐 상승·하락·중립·판단 유보 중 하나를 먼저 말한다.

| 묶음 판정 | 국면 | 현재 판단 | 굳이 고르면 |
| --- | --- | --- | --- |
| 상승 우위 | 상승 추세 지속 | 상승 | - |
| 하락 우위 | 하락 추세 지속 | 하락 | - |
| 상승 우위 / 하락 우위 | 그 밖(반등 시도·조정·횡보·반대 추세) | 중립 | 묶음 판정 쪽 |
| 중립 | 상승 추세 지속·상승 추세 중 조정 / 하락 추세 지속 | 중립 | 상승 / 하락(추세 쪽) |
| 중립 | 반등 시도·횡보 | 중립 | 고르지 않음(근거 부족이라고 말한다) |
| 판단 유보(데이터) | 또는 국면 판단 유보 | 판단 유보 | - |

- 신호 강도: "5개 묶음 중 상승 2·하락 0·중립 3"처럼 계산한 묶음 수와 비중을 함께 쓰고, 우세한 쪽 묶음이 계산한 묶음의 절반 미만이면 "약한 상승 우위"라고 쓴다. 판정 규칙(차이 2표)은 그대로다.
- 예측력: 과거 검증의 표본 수·차이·95% 구간을 쓰고, 유의하지 않으면 "차이가 통계적으로 유의하지 않아 방향 예측력은 확인되지 않았습니다"라고 쓴다. 상승·하락 확률은 만들지 않는다(검증된 확률 없음).
- 단독 전망: 상태 줄(과 데이터 경고) 다음에 `현재 판단: 중립(굳이 고르면 상승 쪽) · 5개 묶음 중 상승 2·하락 0·중립 3(약한 상승 우위) · 과거 검증에서 방향 예측력은 확인되지 않았습니다(표본 22,176, 차이 +1.9%p, 95% 구간 -1.4~+5.3%p)` 한 줄. "신호 판정 상세"에 신호 강도·현재 판단 규칙 줄, "데이터 기준일·출처"에 `답변 생성: YYYY-MM-DD HH:MM KST(시장 데이터 기준일 … 종가와 다릅니다)`와 시세 상태.

**후속 답**(직전 답의 제목 줄이 종목·기간 표식, 빈 줄 빼고 6줄 이하, 둘째 줄이 결론, 규칙 설명·섹션 없음, 이전 분석을 다시 찍지 않는다)

| 종류 | 질문 예 | 둘째 줄(결론) | 다음 줄 |
| --- | --- | --- | --- |
| verdict | 그래서 오르냐 내리냐? 니 판단은? 상승이야 하락이야? | `현재 판단: …. ` + 신호·국면 문장 | 예측력, 가장 큰 상승 근거·하락 위험, 확인할 가격대 |
| pick | 굳이 하나만 고르면? 하나만 골라봐 | `굳이 하나를 고르면 상승 쪽입니다(현재 판단: 중립).` 또는 `굳이 하나를 고를 근거가 부족합니다(현재 판단: 중립).` | 같음 |
| compare | 상승 근거와 하락 위험 중 어느 쪽이 더 강해? | `현재 판단: …. 묶음 표로는 상승 근거가 더 많습니다(…), 국면은 '…'입니다.` | 같음 |
| buy | 그래서 살 만하다는 거야? 사도 된다는 거야? | `현재 판단: …. 매수·매도 여부는 권하지 않습니다. …` | 같음 |
| probability | 상승 가능성이 더 커, 하락 가능성이 더 커? | `현재 판단: … — 묶음 비중, 국면. 검증된 상승·하락 확률은 없어 가능성을 숫자로 말하지 않습니다.` | 과거 빈도(표본·구간), 현재 상태, 확인할 가격대 |
| risk | 하락 위험은 어때? | `현재 판단: … — 묶음 비중, 국면.` | 하락 위험 최대 3개, 확인할 가격대 |
| earnings | 최신 실적까지 반영해서 다시 판단해줘, 3분기 실적이 전망에 어떤 영향을 주는가? | `현재 판단: …. 실적은 신호 판정 표에 넣지 않아 최신 실적을 반영해도 현재 판단은 그대로입니다(…).` | 최신 실적(출처), 직전 실적 또는 물은 분기(없으면 "아직 공시(잠정·확정)가 없습니다"), 발표 뒤 주가(발표일 직전 거래일 종가 → 기준일 종가), 예측력 |
| data | 최신 주가 데이터가 없으면 어떻게 답하는가? 데이터 기준일이 언제야? | `이 답변은 YYYY-MM-DD HH:MM KST에 만들었고, 시세 기준일은 … 종가입니다.` | 지금 상태(시세 상태와 이유), 답하는 방식, 현재 판단 |

- earnings·data는 직전 답에 표식이 있을 때만 이 경로다(`special_followup_kind`). "2분기", "3분기"처럼 숫자가 붙은 분기는 기간(3개월)으로 읽지 않는다. 물은 분기에 연도가 없으면 이미 끝난 가장 최근 그 분기다(11월의 "4분기"는 지난해).
- 직전 종목이 없으면 23차와 같이 종목을 되묻는다.

**잠정실적**(`app/preliminary.py`, 점검 `python -m scripts.check_preliminary`, DB 읽기만)

- 누락 원인: 실적은 DART 정기보고서 API(`fnlttSinglAcnt`)만 읽었다. 2026년 3분기 보고서는 아직 제출 전(분기 종료 후 45일)이라 SK하이닉스가 2026-10-28 낸 "연결재무제표기준영업(잠정)실적(공정공시)"(접수번호 20261028800036, 거래소 공시)는 실적에 없었다. 공시 수집기(`app/dart_documents.py`)가 그 원문 앞 2,000자를 `disclosure.content`에 이미 저장하고 있어 새 외부 요청 없이 표를 읽는다(잠정실적 444건 모두 당기순이익 줄까지 저장돼 있음).
- 분기(3개월) 실적만 쓴다: 당기실적 기간이 분기 첫날~그 분기 말일. 당해실적(그 분기)·누계실적(연초부터)·전년동기실적(같은 공시, 같은 기준)을 따로 읽는다. 단위는 조원·억원·백만원·천원·원.
- 쓰지 않는 공시: 자회사의 주요경영사항(다른 회사 실적), 정정공시(표 형식이 다름. 원 공시 뒤에 정정이 있으면 원 공시 수치 대신 정정이 있었다고만 쓴다), 월별·판매 실적, 기준(연결·별도) 미표기.
- 연결·별도: 제목·표 머리 → 공시가 자기를 설명하는 문장("별도재무제표기준 영업(잠정)실적입니다") 순서로 정한다. 같은 날 연결·별도 잠정치를 따로 내는 회사(현대제철), 별도 잠정치 공시에 연결 실적 안내 문장이 있는 회사(한화)가 있다.
- 답변에 쓰는 조건: 같은 분기의 정기보고서가 없을 것(나오면 확정치로 바뀐다), 최근 정기보고서와 같은 기준일 것, 2~4분기는 직전 분기 정기보고서 매출 누적 + 이번 분기 당해실적 = 잠정 누계(0.5% 또는 공시 단위 2개 허용). 같은 분기·같은 기준이 여러 건이면 나중 공시.
- 실적 줄: `2026년 3분기(연결, 잠정): 매출액 1,046,155억 원, 전년 동기 대비 +327.9%; … (잠정실적 공정공시, 발표일 2026-10-28, DART 접수번호 20261028800036, 감사 전 잠정치)`, 확정은 `2026년 2분기(연결, 확정): … (반기보고서, 접수일 2026-08-14, DART 접수번호 …)`(4분기는 `확정, 연간 - 3분기 누적`, 사업보고서).
- 2026-11-04 점검(211종목, 444건): 분기 표 350건(제외: 자회사 실적 47, 월별·판매 35, 정정 12). 기준을 가리지 않으면 정기보고서가 나온 분기와 불일치 15개 분기였고, 답변 규칙으로는 186개 분기 중 4건(영업이익 1~4% 차이, 감사 뒤 수정)이다. 규칙으로 뺀 잠정치는 기준 미표기 18·기준 다름 5·누적 불일치 5. 최신 분기가 잠정치인 종목 103개.

**시세 기준일**(`app.outlook.price_status`). 기대 거래일(거래일 15:30 이후면 오늘, 그 전이면 직전 거래일. 2026-11-08부터 KRX 거래일 달력 기준, 그 전에는 평일)에 시세가 없을 때 이유를 수집 기록으로 나눈다.

| 상태 | 조건 |
| --- | --- |
| 최신 | 기준일 = 기대 거래일 |
| 종목만 누락 | 코스피 지수에는 기준일 뒤 날짜가 있다(거래정지이거나 이 종목만 수집 안 됨) |
| 수집 실패 | 기대 거래일 장 마감 뒤 시세 수집(`data_refresh_log` step prices) 마지막 기록이 failed |
| 휴장일 추정 | 장 마감 뒤 시세 수집이 성공했는데 시장 전체에 그날 시세가 없다 |
| 미수집 | 장 마감 뒤 시세 수집 기록이 없다 |

데이터 경고는 `마지막 시세일(2026-11-03)이 기대 거래일(2026-11-04)보다 1영업일 늦습니다(미수집: 장 마감 뒤 시세 수집 기록 없음, 마지막 시세 수집 2026-11-03 19:45). 이 분석은 2026-11-03 종가 기준이며 최신 거래일 전망이 아닙니다.` 5영업일을 넘거나 거래일이 빠지면 판단 유보(그대로).

**화면**: 프론트 `AnswerSections`의 섹션 번호를 "1. 종합 판단"처럼 번호 뒤 마침표·공백으로 표시한다(동그라미 배지 안 번호와 제목이 "1종합 판단"으로 붙어 읽히던 문제, `feature/frontend`).

#### 전망 3차: 결론 먼저 답변·국면·짧은 후속 답 (2026-11-04, 응답 필드는 그대로)

위 두 절의 답변 섹션·후속 답 형식·묶음 표·과거 검증 표는 이 절이 우선한다. 문장은 모두 코드 템플릿이다(LLM 없음). 숫자는 코드 계산, 다른 종목 근거 0, 단정·목표가·검증 안 된 확률·매수 권유 금지는 그대로다. 새 라우트는 없다.

**답변 구조**(전망 질문). 첫 `## ` 앞의 두 줄은 프론트 `AnswerSections`가 번호 없는 머리 문단(`answer-intro`)으로 그린다.

```
SK하이닉스(000660) · 향후 5거래일                      ← 제목 줄(기간: 향후 5·20·60거래일)

현재는 단기 반등 시도, 추세 전환은 미확인 (기준일 2026-11-03 종가)   ← 상태 줄(국면)

## 종합 판단            2~3문장 + "신호 판정에 넣지 않은 최근 사실"(공시·기사·실적)
## 상승을 뒷받침하는 근거   숫자·날짜가 있는 짧은 항목(같은 사실은 한 항목)
## 하락 위험 근거
## 앞으로 확인할 사항     이동평균 회복·지지 가격, 분석 기간 최저·최고 종가, 거래량, 업종 뉴스·실적 공시
## 과거 검증            과거 검증 한 줄(아래 표)
## 기술적 지표 / 뉴스·공시(직접 관련) / 업종·산업 뉴스(간접) / 실적(참고) / 시장 상황
## 신호 판정 상세        표식 줄·묶음 표·개별 지표와 규칙·판정 규칙·국면 규칙(규칙 설명은 여기에만)
## 불확실성과 한계        면책 문구는 여기 한 번만
## 데이터 기준일·출처
```

- 후속 질문이 기간을 이어받는 표식은 제목 줄(`이름(종목코드) · 향후 N거래일`)과 "신호 판정 상세"의 `- 이름(종목코드), 분석 기간 1주일(5거래일): 라벨` 줄이다(`app.outlook.marker_of`).
- 우위 라벨(상승 우위/하락 우위)은 제목·상태 줄에 쓰지 않는다. 종합 판단에서는 과거 검증 결과와 함께만 쓴다(검증 구간에서 어떤 라벨도 방향을 전체 평균보다 잘 맞히지 못했다).

**국면 규칙**(`app/outlook.py` `classify_regime`, 분석 기간과 관계없이 같은 입력: 5·20·60거래일 수익률, 5·20·60일선, 최근 20거래일 최저 종가(기준일 포함 20개 종가, 42차)). 위에서부터 첫 일치.

| 순서 | 국면 | 조건 |
| --- | --- | --- |
| 1 | 판단 유보(데이터 부족) | 판정이 판단 유보이거나 60거래일 시세가 없음 |
| 2 | 단기 반등 시도, 추세 전환은 미확인 | 큰 하락 뒤(20거래일 ≤ -10% 또는 60거래일 ≤ -15%) + 반등 신호(5거래일 > 0, 5일선 > 20일선, 20거래일 최저 종가 대비 ≥ +5% 중 하나) + 종가 < 20일선 또는 종가 < 60일선 |
| 3 | 하락 추세 지속 | 종가 < 20일선 < 60일선이고 (20거래일 ≤ -5% 또는 60거래일 ≤ -10%) |
| 4 | 상승 추세 중 조정 | 60거래일 ≥ +10%, 종가 > 60일선, 20일선 > 60일선이고 (종가 < 20일선 또는 5거래일 ≤ -3%) |
| 5 | 상승 추세 지속 | 종가 > 20일선 > 60일선이고 (20거래일 ≥ +5% 또는 60거래일 ≥ +10%) |
| 6 | 횡보, 방향 불분명 | 그 밖 |

예: SK하이닉스 2026-11-03 = 20거래일 -23.24%, 5거래일 +2.91%, 종가 1,414,000원 < 20일선 1,421,300원 → 단기 반등 시도, 추세 전환은 미확인(테스트로 고정).

**종합 판단 템플릿**(`signal_sentence` + `backtest_sentence`): 묶음 판정이 한쪽이고 국면이 같은 쪽(상승 우위 + 상승 추세 지속, 하락 우위 + 하락 추세 지속)일 때만 "현재 신호는 상승(하락) 쪽이 우세합니다(묶음 표 상승 a, 하락 b)."라고 쓴다. 엇갈리면 그 사실을 쓴다. 예: "단기 신호는 상승 쪽이 많지만(묶음 표 상승 2, 하락 0), 20거래일 -23.24% 급락 뒤의 반등이고 주가가 5일선·20일선·60일선 아래에 있어 추세 전환은 확인되지 않았습니다." 이어서 과거 검증 문장: "과거 검증에서 이 신호 조합(상승 우위)은 1주일 뒤 방향을 전체 평균보다 잘 맞히지 못했습니다(상승 비율 51.8% vs 전체 49.9%)." 뉴스·공시·실적은 판정 표에 넣지 않고 "신호 판정에 넣지 않은 최근 사실"에 쓴다: 실적·배당·자기주식·합병 등 공시 1건, 제목에 실적·컨콜·자사주·수주·HBM·수율 같은 말이 있는 직접 관련 기사 최대 2건(`"…"라는 기사가 있었습니다.`), 최근 분기 영업이익과 전년 동기 대비. 이런 기사는 직접 관련 뉴스 목록에서도 먼저 고른다.

**같은 사실은 1표**: 교차(골든·데드크로스)는 단기선·장기선 위아래가 바뀐 사건이라 교차가 있으면 지금의 이동평균 배열과 항상 같은 쪽이다. 그래서 교차를 `중기 추세` 묶음에 넣고(학습 구간 상관: 이동평균 배열-교차 +0.54/+0.58/+0.66), 같은 묶음에 이동평균 배열이 있으면 교차는 보이기만 하고 세지 않는다(`SAME_FACT_AS`, 답변에 "'5일선이 20일선 위'와 같은 사실이라 세지 않음"). 묶음은 단기 흐름 / 중기 추세(장기 이동평균 위치·이동평균 배열·교차) / RSI / MACD / 거래량 5개다. 이 개정은 검증 구간 결과를 본 뒤라 다시 잰 검증 구간은 엄밀한 홀드아웃이 아니다.

| 기간(검증 구간, 2026-11-04 다시 잼) | 신호일 | 상승 우위 | 하락 우위 | 중립 | 전체 |
| --- | --- | --- | --- | --- | --- |
| 1주일(5거래일 뒤) | 2026-01-02~10-27 | 51.8%(+1.9%p, -1.4~+5.3, 22,176) | 48.2%(-1.6%p, -5.3~+2.1, 22,776) | 49.6%(-0.3%p, 23,047) | 49.9% |
| 1개월(20거래일 뒤) | 2026-01-02~10-02 | 52.0%(+2.4%p, -3.5~+8.2, 19,031) | 48.0%(-1.6%p, -7.3~+4.1, 15,173) | 48.9%(-0.7%p, 28,635) | 49.6% |
| 3개월(60거래일 뒤) | 2026-01-02~08-04 | 48.9%(+2.6%p, -4.5~+9.7, 15,518) | 45.8%(-0.4%p, -14.4~+13.6, 8,318) | 44.8%(-1.5%p, -3.1~+0.1, 25,257) | 46.3% |

세 기간 모두 어떤 라벨도 전체 평균과 유의한 차이가 없다(95% 구간이 0을 포함).

**후속 답**(보고서를 다시 찍지 않는다, 빈 줄을 빼고 6줄 이하, 섹션·규칙 설명 없음). 첫 줄은 `제목 · 상태 (기준일 … 종가)`다.

| 질문 | 형식 |
| --- | --- |
| "그래서 결론이 뭐야", "니 판단은", "오르냐 내리냐" | 첫 문장이 바로 답한다: "지금 데이터로는 1주일 뒤 방향을 믿을 만하게 판단할 수 없습니다. 과거 검증에서 이런 신호 조합(라벨)은 방향을 전체 평균보다 잘 맞히지 못했습니다(…)." + 국면 문장, `- 가장 큰 상승 근거:`, `- 가장 큰 하락 위험:`, `- 확인할 가격대:`, "자세한 근거는 바로 위 분석에 있습니다." |
| "하락 위험은", "어떤 지표가 위험해" | `- 하락 위험:` 최대 3개 + `- 확인할 가격대:` 20일선(종가가 위/아래), 분석 기간 최저 종가, 52주 최저 종가(각각 날짜·조회 기간) |
| "확률은", "몇 퍼센트야" | "검증된 상승·하락 확률은 없습니다." + `- 과거 빈도:`(검증 구간, 같은 신호 상태였던 날의 N거래일 뒤 상승 비율과 전체, 표본) + `- 현재 상태:` |

직전 종목·기간 유지와, 직전 종목이 없으면 되묻기는 그대로다.

**최고·최저 종가는 이름과 조회 기간을 붙인다**: "분석 기간 기준 최근 N거래일"(1주일 20, 1개월 60, 3개월 120)과 "52주 기준 최근 250거래일"을 따로 보이고, 값마다 조회 기간(날짜 범위)이나 "최근 N거래일 기준"을 쓴다. "지표 계산에 쓴 가격 범위"(260거래일)는 이동평균·MACD·RSI용이라고 밝힌다. 1주일·3개월은 국면 판단용 60거래일 수익률과 60일 이동평균도 기술적 지표에 보인다.

**지수 대비가 없을 때**: 원인은 수집 순서였다(2026-11-03 19:45~19:48 갱신에서 종목 시세가 지수보다 먼저 들어가, 그 사이 질문은 종목 기준일보다 지수 마지막 날짜가 이르러 비교하지 않았다). 이제 "지수 대비: 코스피 데이터가 2026-10-31까지만 있어 기준일(2026-11-03) 기준으로 비교하지 못했습니다."처럼 지수 마지막 날짜를 쓴다. 지수 행이 아예 없으면 "데이터가 없어", 상장 시장 정보가 없으면 그 이유를 쓴다.

**실적(참고) 수정**: 분기·반기보고서의 당기 금액(`thstrm_amount`, 그 분기 3개월)과 함께 사업연도 누적(`thstrm_add_amount`)을 저장한다(마이그레이션 `c4d2e8f61a93`, `*_cum` 컬럼). 4분기 = 사업보고서 연간 - 같은 해 3분기 누적(누적이 없으면 1~3분기 합, 같은 해 보고서가 연결·별도로 섞이면 만들지 않음). 전년 동기는 같은 기준끼리만 비교한다(1~3분기는 보고서의 전기 금액, 4분기는 전년 4분기 계산값). 최근 분기의 매출액·영업이익을 인식하지 못했거나(금융사처럼 계정 이름이 다른 경우) 영업이익이 매출액보다 크면(지주사가 하위 매출 계정을 잡은 경우, 예: SK스퀘어) 섹션을 생략한다. 당기순이익이 매출액보다 큰 분기는 DART 원본 값이며(1분기 + 2분기 = 반기 누적으로 확인) 그렇다고 한 줄 밝힌다. 점검: `python -m scripts.check_earnings`(이상치 목록: |당기순이익| > 매출액, 영업이익 > 매출액, 분기 값 > 누적(앞 분기가 모두 0 이상일 때), 1~n분기 합 ≠ 누적, 보고서 전기 금액 ≠ 전년 행, 전년 동기 대비 ±500% 초과), `--compare 종목명…`(DART 원본 대조, 외부 요청). 다시 채우기: `python -m scripts.refresh_data --earnings-only`(다른 단계 실행·기록 없음). 2026-11-04 장 마감 뒤 고친 코드로 다시 채웠다(300종목, 요청 3,600건, 오류 0건, 행 수 2,925 그대로). 5종목(SK하이닉스·삼성전자·NAVER·현대차·LG화학) 최근 4분기 × 3계정 60건이 DART 원본과 모두 일치했고, 이상치 목록은 `evals/reports/forecast_round3_20261104.md` 6절에 있다.

#### 근거 뉴스 정합성 (2026-11-03, 응답 필드는 그대로)

채팅 뉴스 도구, 종목을 지정한 RAG 결과, 전망 답변의 뉴스는 같은 후처리(`app.news_relevance.evidence_news`)를 거친다: **제목에 그 종목 이름**(별칭·뉴스 표기 포함, ETF는 그 ETF 이름이며 더 긴 다른 ETF 이름의 일부는 제외)이 있는 기사만 남기고, 정규화한 제목이 거의 같은 기사(여러 언론사 중복, 유사도 0.9 이상)는 하나만 남긴다. 전망 답변은 기간별 발행일 필터(위 표)와 목표가·확률 제목 제외를 더한다. 걸러진 뒤 기사가 없으면 다른 회사 기사로 채우지 않고 "관련 뉴스를 찾지 못했습니다"다. 공시는 그 종목(`company_id`)의 문서만 쓴다.

- 바뀐 점: 2026-11-02까지 뉴스 도구는 제목 일치 기사를 앞에 두고 나머지(본문에만 이름이 나오는 다른 회사 기사)도 채웠다. 예를 들어 SK하이닉스로 저장된 최근 25건 중 제목에 SK하이닉스가 있는 기사는 2건이었고, 나머지에는 "삼성전자 성과급 갈등" 칼럼 같은 기사가 있었다. 이제 이런 기사는 근거에서 빠지고 `news_tool` 결과에 `excluded_unrelated`(뺀 기사 수)가 붙는다. 읽는 기사 수는 limit의 8배다.
- 후속 질문에서 직전 질문에도 종목이 없으면(예: "그래서 결론이 뭐야?" 다음 질문) 직전 리포트의 종목을 도구 인자로 쓰고, 종목 없이 부른 RAG 검색에도 그 종목을 넣는다(종목 없는 의미 검색이 다른 회사 기사를 근거로 붙였다).

#### 답변 품질 보정 (2026-10-11, 응답 필드는 그대로)

LLM이 tool을 고른 뒤 서버 코드가 아래 네 가지를 보정한다. 요청/응답 형식은 바뀌지 않았다.

- **역할 접두어 제거**: `answer`가 `assistant\n\n`으로 시작하던 문제(모델의 역할 헤더가 본문으로 샌 것)를 서버에서
  떼어낸다. 프론트에서 따로 처리할 필요 없다. 이미 저장된 예전 리포트의 `answer`는 그대로다.
- **흐름 질문의 기본 기간**: 기간을 말하지 않고 주가의 흐름/추이/추세/동향/움직임 또는 "최근(요즘) 주가·상황"을
  묻는 질문은 최근 **20거래일**을 조회한다(예: "삼성전자 최근 주가 흐름 알려줘"). 질문에 기간이 있으면
  ("지난주", "최근 3일", "한 달", "5거래일") 기본값을 쓰지 않고 그 기간을 따른다("지난주"·"최근 3일"·"한 달"·"일주일"처럼 서버가
  인식하는 표현은 서버가 계산한 거래일 수, "5거래일"·"며칠"처럼 인식하지 못하는 표현은 모델이 추론한 값. "한 달"·"일주일"·"반년"
  등 고유어 기간은 2026-10-27부터 서버가 인식한다). 여러 거래일을 조회한 답변에는 기간 등락률
  (구간 직전 거래일 종가 대비)과 마지막 하루 등락률이 구분되어 나온다.
- **이름으로 요청한 정보**: 질문이 주가·뉴스·공시 중 둘 이상을 이름으로 요청하면(종목 상세 화면의 [리서치하기]
  버튼 질문 "… 주가, 뉴스, 공시를 같이 확인해줘.") 모델이 일부만 호출했더라도 빠진 tool을 서버가 덧붙여 실행한다.
  그래서 이런 질문의 `used_tools`에는 `stock_tool`, `news_tool`, `disclosure_tool`이 모두 들어가고 `sources`가
  채워진다. "뉴스 말고 주가만"처럼 일부를 빼달라는 질문, 종목이 둘 이상인 질문에는 덧붙이지 않는다.
- **등락 원인 질문의 주가 조회**: 질문에 원인 표현(왜/이유/원인/때문)과 등락 표현(올랐/상승/급등/떨어졌/하락/급락/폭락 등)이
  함께 있으면(예: "카카오는 왜 떨어졌어?"), 모델이 `news_tool`만 호출했더라도 서버가 같은 종목의 `stock_tool`을
  덧붙여 실행한다. 기간을 말하지 않았으면 최근 20거래일이다. 그래서 이런 질문의 `used_tools`는 보통
  `["news_tool", "stock_tool"]`이고, 답변에 실제 기간 등락률이 들어간다(전에는 주가를 조회하지 않아 "왜 올랐어?"라는
  질문의 전제를 그대로 받아 답했다). `sources`는 그대로 뉴스·공시 문서만 담는다.
  - `market_tool`(코스피/코스닥 지수 대비)은 **지수 데이터가 그 종목의 최신 주가 날짜까지 수집돼 있을 때만** 함께
    덧붙인다. 지수는 2026-10-14 갱신으로 10/14까지 받아 두었고, 소스가 멈추면 `GET /api/status`의 `market_index.stale`이 true가 된다.
  - 모델이 종목을 하나로 특정한 tool 호출을 했을 때만 덧붙인다. 원인 표현만 있거나("PER이 왜 중요해?") 등락 표현만
    있는 질문("오늘 많이 오른 종목 알려줘"), 종목이 둘 이상인 질문은 이전과 같다.

#### 서버가 직접 답하는 질문 (2026-10-27, 응답 필드는 그대로)

평가(backend/evals)에서 모델이 숫자나 정의를 지어낸 유형은 모델을 부르지 않고 서버 코드가 답한다. 아래에 해당하지 않는 질문은 이전과 같다
(사건 이후 주가 반응 질문이 이보다 먼저 처리된다).

- **보유 시세보다 앞선 기간**: 질문에 기간(연도 `2024년`, 연+월 `2024년 3월`, 날짜 `2026년 3월 15일`·`2026-03-15`, `작년`, `작년 이맘때`,
  `3년 전`, 연도 없는 `6월`, 또는 `1년`·`반년`·`올해` 같은 상대 기간)이 있고 종목이 하나인 시세(주가·종가·거래량·등락률·흐름 등) 질문에서,
  그 기간이 그 종목의 보유 시세 시작일보다 앞서면:
  - **전부 앞서면**: `"요청하신 기간(2024년 3월: 2024-03-01~2024-03-31)의 삼성전자 시세 데이터는 제공하지 않습니다. 보유한 데이터는
    2026-06-26부터 2026-10-14까지입니다."`와, 참고로 보유 데이터 마지막 거래일의 종가(“요청하신 기간의 값이 아닙니다” 명시).
  - **일부만 겹치면**: 데이터가 없는 구간을 밝히고 겹치는 구간의 요약(종가·기간 등락률·최고/최저, 서버 계산)만 답한다.
  - `used_tools`는 `["stock_tool"]`, `sources`는 빈 배열. 저장된 tool 기록의 `result.called_via`는 `"period_scope"`.
  - 보유 범위 **뒤**(아직 수집 안 된 최근 날짜, 예: "오늘 주가")는 판단하지 않는다 — 이전처럼 가장 최근 데이터로 답한다.
- **ETF 구성종목·편입 비중**: ETF가 있는 질문에서 구성종목/편입종목/보유종목/상위 종목/비중을 물으면(2026-10-30 변경):
  - **`etf_holding`에 수집 데이터가 있는 ETF**(삼성 KODEX·미래에셋 TIGER 중 수집에 성공한 것)는 LLM을 부르지 않고 **코드 템플릿**으로 답한다:
    출처와 기준일("… 2026-10-29 기준"), 비중이 큰 상위 구성종목(기본 10개, 질문에 "상위 5개"처럼 있으면 그 수, 종목명·코드·비중 %), 그 상위 N개의 합계와 전체 합계,
    "운용사가 영업일마다 공시하며 수동 수집이라 늦을 수 있다"는 안내, 투자 권유 아님. 기준일이 최근 영업일보다 2영업일 이상 뒤처지면 stale 안내가 붙는다(`/api/status`와 같은 규칙). 레버리지·인버스 ETF는 기존 고정 설명이 이어 붙는다.
    ETF를 3개까지 한 번에 묻는 것도 템플릿으로 답한다. 모델이 종목명이나 비중을 쓰지 않으므로 지어낼 수 없다.
  - **데이터가 없는 ETF**(RISE·ACE·SOL·HANARO·PLUS·TIME 등 미지원 운용사, 또는 KODEX·TIGER인데 아직 수집 전/수집 실패)는 이전 그대로
    "데이터는 이 서비스에서 제공하지 않으며 운용사 상품 페이지(구성종목/PDF)나 공시에서 확인하라"고 답한다. 레버리지·인버스 ETF면 기존 고정 설명이 붙는다.
  - DB 오류로 조회에 실패해도 같은 "제공하지 않음" 답변으로 되돌아간다(5xx로 만들지 않는다).
  - 위 두 경우 모두 `used_tools`·`sources`는 빈 배열이다.
  - "ETF 이름 + 보유종목/구성종목 + 비중" 질문(예: "KODEX 200 보유종목 비중 알려줘")은 내 보유종목 브리핑이 아니라 ETF 구성종목으로 답한다.
    "내/나의/제/우리"가 들어가면("TIGER 200 내 보유종목 어때") 이전처럼 브리핑이다.
- **용어 정의**: 종목이 없는 정의 질문("PER이 뭐야?", "배당락이 무슨 뜻이야?")은 내장 용어 사전(`app/glossary.py`, 33개)으로 답한다.
  사전에 없는 용어면 "내장 용어 사전에 없는 용어"라고 답한다(정의를 지어내지 않는다). 종목이 들어간 질문("삼성전자 PER은 얼마야?")과
  판단·비교 질문("PER이 높으면 좋아?", "PER과 PBR 차이")은 이전처럼 모델이 답한다. `used_tools`·`sources`는 빈 배열.
- **종목을 찾지 못함**: 모델이 고른 tool 호출이 모두 종목을 받는 호출인데 그 종목 인자가 하나도 해석되지 않으면(예: "롯데칠성", "애플",
  깨진 문자열) tool을 실행하지 않고 `"해당 종목을 찾지 못했습니다. 종목명 또는 종목코드를 정확히 입력해주세요."`(후보가 있으면
  "혹시 A, B 중 하나인가요?")로 답한다. `used_tools`·`sources`는 빈 배열.

#### 응답 안정성·숫자 검증·데이터 기준일 (2026-10-27 2차, 응답 필드는 그대로)

- **LLM 지연·연결 오류**: 모델 호출마다 timeout(호출당 최대 45초, 질문당 LLM 호출 합계 90초)과 생성 길이 제한(`num_predict` 1024)을
  둔다. timeout이나 Ollama 연결 오류가 나면 **예외(503/500)를 내지 않고 200 응답**으로 답한다: 이미 조회한 tool 결과가 있으면 서버가
  만든 요약(주가·등락률·거래량, 뉴스·공시 제목), 없으면 `"응답 생성이 지연되어 답변을 만들지 못했습니다. 잠시 후 다시 시도해주세요."`.
  서버 로그에 사유(timeout/connection/question_budget)가 남는다. 모델이 없는 등 Ollama가 오류를 돌려주면(`ResponseError`) 이전처럼 502다.
- **답변 숫자 검증**: 모델이 쓴 최종 답변의 숫자가 tool 결과·질문·tool 결과의 단순 계산(두 시점 값의 차이와 변화율)으로 설명되지
  않으면, 그 답변 대신 tool 결과로 서버가 만든 요약을 내보낸다(예: NAVER 179,200원을 "1,792,000원"으로 옮긴 답변). tool 결과가 없는
  답변은 설명되지 않는 값(원·%·주)이 든 문장만 뺀다. 날짜·목록 번호·거래일 수 등은 검사하지 않는다(평가와 같은 규칙,
  backend/evals/README.md). 뉴스·공시 문장은 숫자만 보고 내용의 사실 여부는 보지 않는다.
- **단순 시세 질문**: 종목 하나의 현재가·등락률·거래량만 묻는 질문("NAVER 주가 얼마야?", "기아 주가와 거래량 알려줘")은 모델 없이
  `stock_tool` 결과로 서버가 답한다: `"NAVER의 2026-10-14 종가는 179,200원입니다. 전일 대비 등락률은 +1.30%, 거래량은 592,942주입니다."`
  원인·흐름·기간·비교·뉴스가 섞인 질문은 이전과 같다. `used_tools`는 `["stock_tool"]`.
- **데이터 기준일**: "오늘·지금·현재·최근·요즘" 같은 현재 시점 표현이 있는 시세 질문에서, 답에 쓴 마지막 시세 날짜가 최근 거래일보다
  영업일 기준 2일 이상 오래됐으면(`GET /api/status`와 같은 `app/data_status.is_stale`) 답 끝에 `"마지막 데이터는 2026-10-14 기준입니다."`를
  붙인다. 최신이면 붙이지 않는다. 기간을 명시한 질문("최근 3개월")과 보유 범위 밖 기간 답변에는 붙이지 않는다.
- **개념 질문 보강**: 두 용어 비교("PER과 PBR 차이", "ROE랑 ROA는 뭐가 달라?")는 사전의 두 정의를 나란히, 용어 하나의 해석 질문
  ("PBR이 1보다 낮으면 저평가야?", "배당수익률 높으면 좋아?")은 정의와 함께 "이 지표 하나만으로 판단하기 어렵다"는 고정 문구로 답한다
  (모델 설명·투자 권유 없음). 모델이 용어를 종목 인자로 넘긴 경우(`stock_tool(ticker="PBR")`)도 "종목을 찾지 못함" 대신 사전으로 답한다.
  시점·종목·추천이 섞인 질문("PER 낮은 종목 추천해줘")은 이전과 같다.

#### 종목명 해석 규칙 (2026-10-27)

리서치·시세·종목 상세·관심종목 등 **주문을 뺀** API의 종목명 해석(`company_resolver`, 기본 모드)에 규칙 세 가지가 추가됐다.
정확 일치 → 공백·대소문자 무시 → 별칭 → **한글 음차** → **앞부분 약칭** → **자모 한 글자 오타** → 유사 이름 보정 순이다.

| 규칙 | 예 | 채택 조건 |
| --- | --- | --- |
| 한글 음차 | `엘지화학` → LG화학, `케이비금융` → KB금융, `에스케이텔레콤` → SK텔레콤 | 앞부분의 영문 약어 음차(엘지·케이비·에스케이·지에스·씨제이·케이티·에이치디·디비 등)를 영문으로 바꾼 이름이 등록명/별칭과 정확히 같을 때 |
| 앞부분 약칭 | `JB금융` → JB금융지주, `한미반도` → 한미반도체 | 공백 제외 4자 이상이고 그 이름으로 시작하는 종목이 **하나뿐**일 때. 둘 이상이면(`SK바이오`) 후보 안내 |
| 자모 오타 | `셀트리언` → 셀트리온, `현대모비수` → 현대모비스 | 한글 3자 이상, 한 글자의 초성·중성·종성 중 한 자리만 다른 종목이 **하나뿐**일 때. 둘 이상이면 후보 안내 |

주문(`POST /api/portfolio/orders`)의 엄격 매칭은 바뀌지 않았다(아래 "종목 엄격 매칭"): 이 세 규칙을 쓰지 않고 후보 안내만 한다.

2026-11-03: 자모 오타·유사 이름 보정에서 한 글자를 더하거나 빼면 되는 다른 종목이 있으면 고르지 않고 후보를 안내한다. 감사(350종목의 이름 한 글자를 뺀 입력 2,108개)에서 다른 종목 자동 선택이 27건(`현대로비스` → 현대모비스, `현대아` → 현대차, `ODEX코스닥150레버리지` → KODEX 코스닥150 등)에서 1건(`에스케이` → SK, 음차 규칙)으로 줄었다. 이름·종목코드·별칭 66개는 기본·엄격 모드 모두 자기 자신으로 해석된다.

#### 채팅의 기간 해석 규칙 (2026-10-31, 응답 필드는 그대로)

해석한 기간은 코드 답변에 항상 날짜 범위로 밝힌다("요청하신 기간(올해 1분기: 2026-01-01~2026-03-31) 기준입니다.").

| 표현 | 해석 | 예(오늘 2026-10-31) |
| --- | --- | --- |
| 올해·작년·재작년·지난해 + 월 / 월일 / 분기 / 상·하반기 | 그 해의 그 구간 | "올해 1분기" 01-01~03-31, "작년 3월" 2025-03, "작년 4분기" 2025-10-01~12-31 |
| 연도 없는 분기·반기·월·날짜 | 올해. 아직 시작 전이면 작년 | "3분기" 2026 3분기, "11월" 2025-11 |
| 진행 중인 구간 | 끝은 오늘 | "올해 4분기" 10-01~10-31 |
| "올해"를 명시한 미래 구간 | 그대로 두고 데이터 없음을 안내 | "올해 12월": 아직 없습니다 + 보유 범위 |
| 두 자리 연도 + 월·분기·반기 | 언제나 20YY. 올해를 넘어도 19YY로 고치지 않는다(미래로 안내) | "24년 3월" 2024-03, "27년 3월" 2027-03(데이터 없음) |
| 숫자만 있는 두 자리 연도 | 20~올해 연도(YY)이면 그 해, 그 밖은 기간 | "24년 주가" 2024년, "5년 주가"·"30년 주가" 최근 N년 |
| 수량으로 쓴 두 자리 | 기간 | "최근 24년", "24년간" |
| 월일 | 날짜(N일간이 아니다) | "3월 15일" |

- 시장 비교: "2024년 코스피 대비 삼성전자 수익률"은 코드가 답한다(`used_tools` = `["market_tool"]`). 종목과 지수가 **같은 날짜 구간**이고(구간 직전 공통 거래일 종가 대비) 60거래일로 자르지 않는다. 보유 데이터가 구간을 다 덮지 못하면 실제 비교 구간을 밝힌다. 구간 끝이 휴장일인 것은 공백으로 안내하지 않는다. 범위 밖이면 데이터 없음이다. LLM 경로(뉴스·원인을 함께 묻는 경우)의 `market_tool`에도 `stock_tool`과 같은 구간을 넘긴다.
- 문서 검색: 질문의 종목이 하나인데 모델이 종목 필터를 빠뜨리면 그 종목을 채우고, 종목이 여럿이면 종목별로 나누거나 빠진 종목을 덧붙인다.
- 언어 혼입: 한국어 질문의 LLM 답변에서 질문·도구 결과에 없는 히라가나·가타카나·한자가 든 문장을 지운다(남는 내용이 없으면 코드 요약). 뉴스 제목의 "中", 영문 티커, 괄호 안 영문명은 그대로다. 코드 답변은 해당 없다.
- 숫자 검증: "주"가 붙은 값은 거래량 차이만 계산 근거로 인정한다(종가 차이가 주식 수로 통과하지 않게). 모델이 호출 JSON을 본문에 쓰면 도구 결과가 있을 때 코드 요약으로 답한다.

#### 두 날짜 사이 수익률 (2026-11-02, 응답 필드는 그대로)

"삼성전자를 2025년 1월 2일에 사서 2026년 1월 2일까지 들고 있었으면?", "현대차 2025년 2월 3일부터 2025년 8월 1일까지 얼마나 올랐어?", "X 대비 Y"는 LLM 없이 코드가 답한다(`used_tools` = `["stock_tool"]`).

- 시작일이 휴장이면 다음 거래일, 종료일이 휴장이거나 데이터가 없으면 직전 거래일 종가를 쓰고 실제 날짜를 답변에 밝힌다.
- 시작일이 보유 범위 밖이면 보유 기간을 안내하며 거절하고, 역순·같은 날·미래 날짜도 거절한다.
- 답변은 시작·끝 종가와 수익률이고, 주가 기준이며 배당·수수료·세금은 반영하지 않는다.
- 원인·뉴스·거래량·최고가·지수 비교가 함께 있거나 종목이 셋 이상이면 기존 흐름이다.

#### 뉴스 관련성 필터 (2026-11-01, 응답 필드는 그대로)

저장된 뉴스 중 그 종목 기사가 아닌 것(`news.is_relevant = false`)은 채팅 뉴스 도구, RAG 검색, 브리핑(`GET /api/briefing`과 채팅 브리핑)의 뉴스, 사건 반응에서 빠진다. 미판정(NULL)은 전처럼 쓴다. 행은 지우지 않는다.

- 영향: 이름이 짧거나 다른 말의 일부인 종목의 뉴스 수가 줄어든다(SK 0건, GS 2건, NAVER 3건, LG 4건, 태성 16건 안팎). 브리핑 응답 필드와 쿼리 수는 같다.
- 그 종목 뉴스가 하나도 남지 않으면 채팅 뉴스 도구는 `found: false`와 "'SK' 관련 뉴스를 찾지 못했습니다."를 돌려준다(수집된 뉴스 자체가 없을 때의 안내와 다르다).
- Ollama가 생성을 중단하면(`token repeat limit reached`) 500이 아니라 도구 결과의 코드 요약으로 답한다.
- 판정 기준과 측정은 `backend/evals/README.md`의 "7차 라운드".

#### 뉴스 정렬 (2026-11-02, 응답 필드는 그대로)

채팅 뉴스 도구(`sources`의 기사), `GET /api/briefing`의 `news`, 종목을 지정한 RAG 결과는 제목에 회사 이름이 있는 기사가 먼저 오고, 그 안에서 최신순이다. 따라서 `news` 배열이 항상 전체 최신순은 아니다. 프론트가 날짜순을 전제로 쓰고 있다면 `published_at`으로 다시 정렬한다.

#### 근거 우선 설명형 답변 (2026-11-02, 기본 꺼짐)

환경변수 `EXPLAIN_EVIDENCE_FIRST=1`이면 설명형·원인형 질문의 답변 형식이 `## 주가 동향`, `## 관련 뉴스·공시`(번호 목록), `## 근거 요약`으로 바뀐다. 응답 필드는 그대로다. 사전 조건 미달로 기본은 꺼져 있어 프론트가 할 일은 없다.

#### 설명형 답변의 문장 검증 (2026-11-01, 응답 필드는 그대로)

LLM이 쓴 답변에서 대상·맥락이 어긋난 문장만 지운다. 숫자·날짜 위반의 전체 코드 요약 대체는 전과 같다.

- 지우는 문장: 질문 종목 이름에 금융회사 접미어가 이어진 다른 회사("태성" 질문의 "태성자산운용", 질문에 그 이름이 있으면 제외), 날짜·기간 없이 쓴 기사 속 시세, 돈 지표에 쓴 "주" 단위 값, "X원 하락한 Y원" 짝이 시세 행과 어긋남, 주가가 주어인 문장 속 재무 숫자, 재무 숫자가 다른 회사 문서에만 있음, 직접 매수·매도 지시.
- 거래량 단위 "건"은 값이 시세와 맞으면 "주"로 고친다.
- 지운 뒤 소제목·안내를 뺀 글이 40자 미만이면 도구 결과로 만든 코드 요약으로 답한다.
- 모델이 도구 호출 JSON을 글로 쓰면 인자 값("기아")을 답으로 쓰지 않고, 도구 결과가 있으면 코드 요약으로 답한다.
- 뉴스·공시 내용의 사실 여부와 숫자 없는 인과 서술은 검증하지 않는다. 측정과 한계는 `backend/evals/README.md`의 "6차 라운드".

#### 과거 기간 질문: 날짜로 조회해 코드가 답한다 (2026-10-31, 응답 필드는 그대로)

3년치 시세 백필(2023-10-30~) 뒤 범위 안이 된 과거 기간 질문이 LLM으로 넘어가, 최근 값에 과거 날짜를 붙이거나 있는 데이터를 없다고 답했다. 그래서 아래처럼 바꿨다. 응답 모양은 같고 **답변 문장을 만드는 방법**만 다르다.

| 질문 | 답(LLM 호출 없음, `used_tools`는 `["stock_tool"]`) |
| --- | --- |
| 특정 날짜 종가 "삼성전자 2025년 3월 4일 종가", "카카오 3년 전 주가", "어제 주가" | 그날 종가·전일 대비 등락률·거래량. 시가·고가·저가를 물으면 덧붙인다 |
| 휴장일 "현대차 2026년 3월 15일 종가" | "2026-03-15(일)은 주말이라 휴장이고 시세 데이터가 없습니다. 직전 거래일(2026-03-13) 현대차 종가는 X원입니다." 평일 휴장일(그날 시세가 있는 종목이 하나도 없음)도 같은 형식이다. 다른 종목은 있는데 이 종목만 없으면 거래정지 가능성을, 데이터 마지막 날 뒤의 평일이면 아직 수집되지 않았음을 밝힌다 |
| 기간 등락률·흐름·최고/최저 "기아 2024년 등락률", "2025년 3분기 수익률", "작년 같은 달", "연초 대비", "2024년 3월부터 6월까지", "작년" | 요청 기간(시작~끝)과, 구간 직전 거래일 종가 대비 마지막 종가의 등락률, 기간 종가 최고·최저(날짜). 끝이 데이터 뒤면 "마지막 데이터는 YYYY-MM-DD 기준입니다."를 붙인다 |
| 기간 거래량 "SK하이닉스 2025년 1월 거래량은?" | 위 문장 + 일평균 거래량, 최대 거래량(날짜) |
| "역대 최고가", "상장 이후", "전체 기간" | 보유 데이터 전체 기준임을 밝히고 답한다(상장 이후 전체가 아니다) |
| 두 종목 "현대차와 기아 2025년 수익률 비교" | 같은 날짜 창의 비교 문장 |
| 보유 범위 밖·걸침 | 이전과 같은 범위 안내("2022년 주가": 제공하지 않음, "2023년 주가": 없는 구간을 밝히고 있는 구간만). 두 종목 질문도 같다 |

- 뉴스·원인·전망·실적을 함께 묻거나, 끝이 오늘인 최근 창("최근 3개월 주가 흐름"), 짧은 과거 창("지난주 주가 흐름"), 종목이 3개 이상이면 이전처럼 LLM이 답한다. 이때 `stock_tool`이 달라졌다.
  **기간을 조용히 자르지 않는다**(이전에는 60거래일로 잘렸다): 요약은 요청 기간 전체로 계산하고 LLM에 넘기는 일별 행만 최근 60개로 줄인다. 과거 기간("2024년 3월 주가와 뉴스", "지난주")은 코드가 날짜 범위를 넘겨 그 구간을 읽고, 시세가 없으면 다른 구간으로 답하지 않는다. "지난주 수익률"처럼 코드가 답하는 기간 수익률도 창의 끝을 지킨다(이전에는 이번 주까지 들어갔다).
- **답변 날짜 검증**(LLM 답변): 숫자 검증과 같은 방식으로, 어긋나면 코드 요약으로 바꾼다. (1) 연도를 쓴 날짜는 연도까지 본다("2024-10-30 종가" - 결과에는 2026-10-30뿐). (2) 그 날짜의 행이 없는데 값이 다른 날짜 행의 것이면 위반이다. (3) 날짜 없이 기간 라벨을 쓴 문장("2024년 등락률은 -12.71%")의 값이 그 기간에 행이 없는 결과에서 왔으면 위반이다.
- "기아차"를 별칭에 추가했다(정확 일치만, 주문의 엄격 매칭에는 정확히 이 입력만 더해진다). 모델이 종목을 한 인자에 묶으면("현대차, 기아") 코드가 종목별 호출로 나눈다.

#### 3차 라운드: 코드 답변 범위·숫자 검증·채팅 브리핑 (2026-10-28, 응답 필드는 그대로)

응답 모양(`answer`, `used_tools`, `sources`, `report_id`, ...)은 바뀌지 않았다. 아래 질문의 **답변 문장을 만드는 방법**만 바뀌었다.

**코드가 답하는 질문(LLM 호출 없음, 0.1초 안팎)**

| 질문 유형 | 예 | 답 | `used_tools` |
| --- | --- | --- | --- |
| 단순 시세(이전부터) | "삼성전자 지금 주가 얼마야?" | 최근 종가·등락률·거래량 | `["stock_tool"]` |
| 기간 수익률(신규) | "삼성전자 3개월 수익률 알려줘" | 기간 시작일 직전 거래일 종가 대비 마지막 종가의 등락률 + 기간 종가 최고·최저(날짜) | `["stock_tool"]` |
| 기간 최고·최저가(신규) | "SK하이닉스 최근 한 달 최고가와 최저가는?" | 위와 같은 문장 + "최고·최저는 종가 기준이며 장중 고가·저가가 아닙니다." | `["stock_tool"]` |
| 두 종목 비교(신규) | "삼성전자와 SK하이닉스 중 최근 한 달 어디가 더 올랐어?" | 같은 날짜 창의 종목별 문장 2개 + "같은 기간 등락률은 A가 B보다 X%p 높았습니다." | `["stock_tool", "stock_tool"]` |
| 개인화 브리핑(신규) | "오늘 브리핑 해줘", "내 보유종목 오늘 어때?", "관심종목 뭐 달라졌어?" | 아래 "채팅 브리핑" | `["briefing"]` |

- 판별(`app/code_answers.classify`): 질문에서 기간을 인식했고(`parse_period`), 기존 라우팅 게이트가 원인·흐름·시장 비교·사건 반응·
  시장 탐색·환율·도구 이름 요청으로 보지 않고, 뉴스·전망·실적·거래량·투자 판단 같은 다른 정보를 함께 묻지 않을 때만이다.
  종목이 3개 이상이거나 두 종목의 시세 날짜가 맞지 않거나 애매하면 이전처럼 LLM이 답한다("삼성전자 한 달 흐름 어때?"는 LLM).
- 기간 창은 **날짜 기준**이다: 질문 기간의 시작일 이후 행을 쓰고 시작일 직전 거래일 종가를 기준으로 삼는다("N거래일"은 실제 행 수).
  보유 시세보다 앞선 기간("2024년 3월")은 이전처럼 범위 밖 안내가 먼저다(period_scope).
- **장중 규칙**: 평일 15:30(KST) 전에는 오늘 날짜의 시세 행(장중에 수집한 값)을 종가로 쓰지 않고 직전 거래일 종가로 답한다
  (`GET /api/briefing`과 같은 규칙). 적용 범위는 **코드가 답하는 시세 질문(단순 시세·기간·비교·브리핑)뿐**이다.
  LLM이 도구를 골라 답하는 경로의 `stock_tool`은 바꾸지 않았다(장중에 수집한 행이 있으면 그 값을 최신 값으로 본다).
  오늘 행을 뺐으면 답 끝에 "오늘(YYYY-MM-DD)은 장 마감(15:30) 전이라 직전 거래일 YYYY-MM-DD 종가 기준입니다."를 붙인다.
- 현재 시점 질문의 데이터 기준일 안내("마지막 데이터는 YYYY-MM-DD 기준입니다.")는 이전과 같은 규칙으로 단순 시세 답에 붙는다.
  기간을 말한 질문은 답 문장에 시작·끝 날짜가 들어가므로 붙이지 않는다.

**채팅 브리핑**

- 로그인 사용자(`POST /api/research`는 원래 인증 필요)의 브리핑 질문이면 `GET /api/briefing`과 **같은 계산**(소유자 `user:{id}`,
  읽기 전용, 계좌를 만들지 않음)을 코드가 문장으로만 바꿔 답한다(`app/briefing_text.py`). 문장에는 기준일, 시장(코스피·코스닥·원/달러),
  모의투자 계좌 요약(총자산·손익·전일 대비 평가손익), 종목별 줄, 눈에 띄는 변화(highlights 문구), 면책 문구가 들어간다.
  숫자는 전부 브리핑 값을 형식만 바꾼 것이고 새로 계산한 숫자는 없다.
- 범위: "관심종목"만 말하면 관심종목만, "보유종목·포트폴리오·계좌"만 말하면 계좌와 보유종목만, 그 밖("브리핑")은 전부.
- 판별(`routing.needs_briefing`): "브리핑"이라는 말, 또는 (내/나의 + 보유종목·관심종목·포트폴리오·계좌) + (어때·달라·변화·요약·현황 등).
  관심종목 추가·삭제·주문·진단 요청과 종목 질문("삼성전자 오늘 어때?"), 개념 질문("포트폴리오 분산이 뭐야?")은 해당하지 않는다.
- 내부 호출에서 `user_id`가 없으면(인증 없는 경로) "로그인하면 개인화 브리핑을 볼 수 있습니다."만 답한다.
- 한계: 브리핑의 한계(`GET /api/briefing` 절)가 그대로다. 대화 맥락(후속 질문)으로 범위를 좁히지 않는다.

**숫자 검증 보강**(`app/answer_guard`, LLM이 답한 경우): 이전의 "도구 결과에 없는 숫자" 외에 아래도 위반으로 보고 코드 요약으로 바꾼다.
(1) 답변의 날짜가 도구 결과·질문·오늘에 없음, 날짜와 짝인 값이 그 날짜의 시세 행에 없음("10월 24일 종가는 225,000원" - 다른 날의 값),
(2) 한 종목만 말한 문장의 숫자가 그 종목이 아니라 다른 종목의 시세 결과에만 있음, (3) 하루 등락률과 기간 등락률의 혼동.
반대로 "137만 4천원" 같은 '천' 합성 표기와 종목 간 같은 지표의 차이("1.87%p 높았다")는 이제 정상으로 본다.
검증기 평가 방법과 결과는 `backend/evals/README.md`의 "숫자 검증기 독립 평가".

### 예시 curl

```bash
curl -X POST http://localhost:8000/api/research \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "삼성전자 최근 3일 등락률이랑 거래량 알려줘."}'
```

후속 질문(앞 호출의 `report_id`가 48일 때). 이번 질문에 종목명이 없어도 삼성전자 뉴스를 조회한다:

```bash
curl -X POST http://localhost:8000/api/research \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "그럼 최근 뉴스는?", "previous_report_id": 48}'
```

```json
{
  "answer": "삼성전자 최근 뉴스는 다음과 같습니다.\n\n*   \"성과급 6억\" 삼성 반도체, 추석 뒤 실제 지급 기준 나온다\n ... ",
  "used_tools": ["news_tool"],
  "sources": [
    {
      "tool": "news_tool",
      "type": "news",
      "title": "'성과급 6억' 삼성 반도체, 추석 뒤 실제 지급 기준 나온다",
      "company_names": ["삼성전자"],
      "company_filter": null,
      "url": "https://www.straightnews.co.kr/news/articleView.html?idxno=311947"
    }
  ],
  "report_id": 49,
  "previous_report_id": 48
}
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 404 | `previous_report_id`에 해당하는 리포트가 없거나 남의 리포트(이 경우 LLM은 호출하지 않음) | `{"detail": "report_id=999999 리포트를 찾을 수 없습니다."}` (`GET /api/research/{report_id}`의 404와 같은 형식) |
| 422 | `question` 누락 / 공백뿐 / 1000자 초과 / `previous_report_id`가 정수가 아니거나 1 미만·BIGINT 초과 / 잘못된 JSON | 아래 "검증 오류(422)" 형식 |
| 502 | Ollama가 응답은 했으나 오류 반환(모델 미설치 등) | `{"detail": "AI 모델 서버가 오류를 반환했습니다. 잠시 후 다시 시도해주세요."}` |
| 503 | Ollama 서버에 연결 불가(**2026-10-27부터 리서치 답변 생성 중의 연결 오류·timeout은 503 대신 200 + 대체 답변**, 위 "응답 안정성" 참고. 이 행은 그 밖의 경로에서 연결 오류가 밖으로 나온 경우) | `{"detail": "AI 모델 서버(Ollama)에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |
| 500 | 그 외 예상 못 한 서버 오류 | 본문이 JSON이 아닌 plain text `Internal Server Error` |

---

## GET /api/research

내 리포트 목록(기본 최신순). `q`를 주면 검색 결과만이고 `total`도 그 건수다(`q`가 없으면 내 리포트 수).

> **Breaking change:** 응답이 배열이 아니라 `{"total": int, "items": [...]}` envelope이다.
> 예전처럼 응답을 바로 배열로 다루던 코드는 `response.items`로 바꿔야 한다.

### 쿼리 파라미터

| 이름 | 기본값 | 범위 | 설명 |
| --- | --- | --- | --- |
| `limit` | 20 | 1~100 | 한 페이지 건수 |
| `offset` | 0 | 0 이상, 9223372036854775807 이하 | 건너뛸 건수 |
| `sort` | `created_at` | `created_at`, `id` | 정렬 기준 |
| `order` | `desc` | `asc`, `desc` | 정렬 방향. 기본(`created_at desc`)이 지금까지의 최신순이다 |
| `q` | 없음 | 최대 50자 | 질문·요약·종목명·종목코드에서 **부분 일치**(대소문자 무시). 공백만이면 무시. 앞뒤 공백은 잘라서 검색 |

범위를 벗어나면 422. 경계값은 위 "경계값별 동작" 표.

**`q` 검색 규칙** (리포트의 `question`, `summary`, 그 리포트에 연결된 종목의 이름과 종목코드 중 하나라도 들어 있으면 매칭):

- 대소문자를 구분하지 않는다(`SNAKE_CASE`로 `snake_case`를 찾는다).
- `%`, `_`, `\`는 **와일드카드가 아니라 글자 그대로**다. `q=%`는 `%`가 든 리포트만, `q=sna_e`는 `snake`에 안 걸린다. 작은따옴표·큰따옴표도 글자 그대로이고 SQL로 해석되지 않는다.
- **내 리포트만** 검색한다. 다른 사용자의 리포트는 어떤 검색어로도 나오지 않는다.
- 답변 전문(`content`)은 검색하지 않는다(목록에 보이는 필드 위주).
- 종목명/종목코드는 `company_name`이 채워지는 경우(질문에서 다룬 종목이 정확히 1개)에만 걸린다.
- `sort=company_name` 같은 종목명 정렬은 지원하지 않는다(DB 정렬 규칙에 따라 한글 순서가 달라져서 뺐다).

### 응답 200

```json
{
  "total": 16,
  "has_next": false,
  "total_pages": 1,
  "items": [
    {
      "report_id": 17,
      "previous_report_id": null,
      "question": "SK하이닉스 HBM 관련 근거 찾아줘",
      "summary": "SK하이닉스 HBM 관련 근거는 다음과 같습니다. * SK하이닉스가 HBM(...",
      "company_name": "SK하이닉스",
      "created_at": "2026-09-25T03:34:56.387513+00:00",
      "used_tools": ["rag_search_tool"]
    }
  ]
}
```

| 필드 | 설명 |
| --- | --- |
| `total` | 조건에 맞는 전체 리포트 수(`limit`/`offset`과 무관, `q`가 있으면 검색 결과 수). 페이지 수 계산용 |
| `has_next` | `offset + limit < total`. 다음 페이지가 있는가 (2026-10-28 추가) |
| `total_pages` | `ceil(total / limit)`, `total`이 0이면 0 (2026-10-28 추가) |
| `items[].report_id` | 리포트 ID |
| `items[].previous_report_id` | 이어받은 직전 리포트 ID. 독립 질문이거나 직전 리포트가 삭제됐으면 null |
| `items[].question` | 원래 질문 |
| `items[].summary` | 답변 앞부분 미리보기(약 200자, 공백 정리됨). null일 수 있음 |
| `items[].company_name` | 질문에서 다룬 종목이 **정확히 1개일 때만** 채워지고, 0개나 2개 이상이면 null |
| `items[].created_at` | ISO 8601(UTC, `+00:00`) |
| `items[].used_tools` | 사용한 tool 이름 목록 |

목록에는 `answer`와 `sources`가 없고 `disclaimer`도 없다(상세 조회에서 제공). 다음 페이지: `has_next`가 true인 동안 `offset += limit`.
`offset`이 `total` 이상이면 `items`가 빈 배열인 200이다. 응답 본문에는 `limit`/`offset`을 싣지 않는다(예전 그대로).

```bash
curl "http://localhost:8000/api/research?limit=2&offset=1" -H "Authorization: Bearer $TOKEN"
curl "http://localhost:8000/api/research?sort=created_at&order=asc" -H "Authorization: Bearer $TOKEN"   # 오래된 순
curl -G "http://localhost:8000/api/research" --data-urlencode "q=100%" -H "Authorization: Bearer $TOKEN"  # %가 든 리포트만
```

### 에러

- 401: 토큰 없음/만료/변조 (위 "인증 에러"). 잘못된 파라미터를 같이 보내도 401이 먼저
- 422: `limit`/`offset`이 범위 밖이거나 정수가 아님, `sort`/`order`가 허용 값이 아님, `q`가 50자 초과 (검증 오류 형식)
- 503: DB 연결 불가 (위와 동일한 body)

---

## GET /api/research/{report_id}

내 리포트 한 건(전체 답변 + sources).

### 응답 200

```json
{
  "report_id": 18,
  "previous_report_id": null,
  "question": "현대차 오늘 왜 올랐어? 최근 주가랑 관련 뉴스 같이 확인해줘.",
  "answer": "현대차의 최근 뉴스는 다음과 같습니다. ...",
  "summary": "현대차의 최근 뉴스는 다음과 같습니다. * 아반떼 가격 논란 ...",
  "company_name": "현대차",
  "created_at": "2026-09-25T03:35:18.493290+00:00",
  "used_tools": ["news_tool", "stock_tool"],
  "sources": [
    {
      "tool": "news_tool",
      "type": "news",
      "title": "아반떼 가격 논란 속 더 커져 돌아온 투싼…5000만 원 넘어서나",
      "company_names": ["현대차"],
      "company_filter": null,
      "url": "https://n.news.naver.com/mnews/article/011/0004665230?sid=101"
    }
  ]
}
```

`answer`, `used_tools`, `sources` 의미는 POST 응답과 같고, 나머지 필드는 목록 항목과 같다.

```bash
curl http://localhost:8000/api/research/18
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 404 | 해당 ID의 리포트가 없거나 남의 리포트(둘을 구분하지 않음) | `{"detail": "report_id=999999 리포트를 찾을 수 없습니다."}` |
| 422 | ID가 정수가 아니거나 1 미만/BIGINT 초과 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## DELETE /api/research/{report_id}

리포트 한 건을 삭제한다. 그 리포트의 tool 호출 이력(`tool_call_log`)도 함께 지워진다(DB의 `ON DELETE CASCADE`).
**되돌릴 수 없다.** 리포트는 생성 후 수정하지 않는 것이 설계 원칙이라 수정(PATCH/PUT)은 제공하지 않고, 삭제만 지원한다.

### 요청

본문 없음.

```bash
curl -X DELETE http://localhost:8000/api/research/18 -H "Authorization: Bearer $TOKEN" -i
```

### 응답 204

```
HTTP/1.1 204 No Content
```

본문이 비어 있으므로 `response.json()`을 호출하면 안 된다. 삭제 후 같은 ID로 `GET`하면 404다.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 404 | 해당 ID의 리포트가 없거나(이미 삭제된 경우 포함) 남의 리포트. 남의 리포트는 삭제되지 않는다 | `{"detail": "report_id=999999 리포트를 찾을 수 없습니다."}` |
| 422 | ID가 정수가 아니거나 1 미만/BIGINT 초과 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

404/503 body는 `GET /api/research/{report_id}`와 같다. 같은 ID를 두 번 삭제하면 두 번째는 404이므로,
프론트에서 "이미 없음"을 성공처럼 다룰지 정해서 처리하세요.

---

## GET /api/stocks/{ticker}/realtime-price

종목의 현재가/전일대비/등락률을 돌려준다.

> ⚠️ **비공식 소스 기반이라 장애 가능성 있음.** 네이버 금융(finance.naver.com) 종목 페이지가 장중에 자기 페이지에서
> 쓰는 공개 폴링 API를 백엔드가 대신 호출하는 방식이라(계좌/인증 불필요), 네이버가 막거나 응답 형식을 바꾸면 언제든
> 실패할 수 있다. 그래서 이 API는 **실패해도 500/502 에러를 내지 않고** 항상 200으로, 대신 DB에 저장된 최근 종가로
> 자동 폴백하면서 `is_realtime: false`, `source: "fallback"`로 "지연된 데이터"임을 표시한다. 프론트는 이 두 필드로
> 실시간/지연 여부를 구분해서 UI에 보여주는 것을 권장한다(예: 지연 데이터면 "종가 기준" 배지 표시).

### 요청

| 파라미터 | 위치 | 설명 |
| --- | --- | --- |
| `ticker` | path | 종목코드(예: `005930`) 또는 종목명(별칭/유사 종목명 포함, `/api/research`와 같은 `company_resolver` 사용) |

```bash
curl http://localhost:8000/api/stocks/005930/realtime-price
```

### 응답 200 (장중, 실시간 성공)

```json
{
  "ticker": "005930",
  "company_name": "삼성전자",
  "current_price": 268000.0,
  "change_amount": -500.0,
  "change_pct": -0.19,
  "as_of": "2026-10-01T11:14:11.124728+09:00",
  "queried_at": "2026-10-01T02:14:12.001000+00:00",
  "is_realtime": true,
  "source": "naver",
  "corrected_from": null
}
```

### 응답 200 (장외/상위 소스 실패 — 폴백)

장 마감 후·주말·공휴일이거나 네이버 쪽 호출이 실패하면 자동으로 이 형태가 된다(상태 코드는 그대로 200).

```json
{
  "ticker": "005930",
  "company_name": "삼성전자",
  "current_price": 71000.0,
  "change_amount": null,
  "change_pct": 1.23,
  "as_of": "2026-09-23",
  "queried_at": "2026-10-01T02:14:12.001000+00:00",
  "is_realtime": false,
  "source": "fallback",
  "corrected_from": null
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `ticker` | string | 종목코드(DB 등록 기준) |
| `company_name` | string | 종목명(DB 등록 기준) |
| `current_price` | number | 현재가(실시간) 또는 최근 종가(폴백) |
| `change_amount` | number \| null | 전일 종가 대비 금액. **폴백일 때는 항상 null**(저장된 데이터에 전일대비 금액이 없음) |
| `change_pct` | number \| null | 등락률(%). 폴백이어도 `stock_price.change_pct`가 있으면 채워진다 |
| `as_of` | string | 가격의 기준 시각. 실시간이면 네이버가 보낸 체결 시각(ISO 8601, KST), 폴백이면 그 종가의 날짜(`YYYY-MM-DD`) |
| `queried_at` | string | 이 응답을 만든 시각(ISO 8601, UTC). 서버 캐시로 값이 재사용됐어도 호출마다 새로 채워진다 |
| `is_realtime` | boolean | `true`면 네이버 실시간 조회 성공, `false`면 폴백 |
| `source` | `"naver"` \| `"fallback"` | 값의 출처 |
| `corrected_from` | string \| null | 입력이 별칭/유사 종목명이라 보정됐을 때만 원래 입력값, 아니면 null |

### 캐싱

종목별로 **서버에서 3~5초(`CACHE_TTL_SECONDS`, 현재 4초) 캐싱**한다. 같은 종목을 그 안에 여러 번 호출해도 상위
소스(네이버)는 한 번만 불린다 — 프론트가 짧은 주기(예: 2~3초)로 폴링해도 안전하다. 폴백 결과도 같은 TTL로
캐싱되므로, 장 마감 후처럼 매번 폴백으로 끝나는 상황에서도 매 요청마다 네이버를 다시 두드리지 않는다.
(지금은 프로세스 메모리 캐시라 워커를 여러 개 띄우면 워커별로 따로 캐싱된다.)

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 404 | 종목을 찾지 못함(`company_resolver`가 등록명/티커/별칭/유사명 어디에도 못 맞춘 경우) | `{"detail": "'...' 종목을 찾지 못했습니다. ..."}` |
| 503 | 네이버 조회도 실패하고 DB에 그 종목의 저장된 주가도 없음(둘 다 없을 때만) | `{"detail": "'...'의 시세를 가져올 수 없습니다. ..."}` |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## GET /api/companies

새 FR이 아니라, FR-02로 이미 수집된 `company`/`stock_price` 데이터를 프론트 "종목" 페이지가 질문 없이 직접 조회하는
용도다. `q`로 종목명 또는 티커를 부분 일치 검색한다(대소문자/공백 무시). `app/company_aliases.py` 별칭 사전도 반영돼서
"네이버"로 검색해도 공식 등록명인 "NAVER"가 나온다. 로그인 불필요(공개).

### 쿼리 파라미터

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `q` | string | 선택. 종목명 또는 티커 부분 일치 검색어. 생략하면 상위 `limit`개 반환. 최대 100자 |
| `limit` | int | 선택(기본 50). 1~300 |
| `asset_type` | string | 선택. `stock`(주식만) 또는 `etf`(ETF만). **생략하면 전체**(주식 + ETF). 다른 값이면 422 |
| `offset` | int | 선택(기본 0). 0 이상, 9223372036854775807 이하. 정렬한 결과에서 건너뛸 건수 (2026-10-28 추가) |
| `sort` | string | 선택(기본 `id`). `id`(수집 순서, 예전과 같음), `name`(종목명), `ticker`(종목코드). 이름·코드는 문자 코드 순서라 한글은 가나다순이고 영문 대문자가 한글보다 앞이다 |
| `order` | string | 선택(기본 `asc`). `asc` 또는 `desc` |

**본문은 계속 배열**이고, `q`/`asset_type`에 맞는 **전체 건수는 `X-Total-Count` 응답 헤더**로 내려온다(`limit`/`offset`과 무관).
다음 페이지가 있는지는 `offset + 받은 개수 < X-Total-Count`로 판단한다. 브라우저에서 이 헤더를 읽으려면 CORS 노출이 필요하고 서버가 이미 노출한다(위 "CORS").
`offset >= X-Total-Count`이면 `[]`인 200이다. 같은 값(같은 이름 등)은 `id`로 순서를 정한다.

```bash
curl "http://localhost:8000/api/companies?q=삼성전자"
curl "http://localhost:8000/api/companies?q=네이버"   # 별칭 -> NAVER
curl "http://localhost:8000/api/companies?limit=10"  # q 없이 상위 10개
curl "http://localhost:8000/api/companies?q=코덱스200&asset_type=etf"   # ETF만, 한글 브랜드 표기도 된다
curl -i "http://localhost:8000/api/companies?limit=3&offset=2"             # 응답 헤더 X-Total-Count: 350
curl "http://localhost:8000/api/companies?sort=name&order=desc&limit=3"    # 종목명 내림차순
```

### 응답 200

```json
[
  {
    "ticker": "005930",
    "name": "삼성전자",
    "market": "KOSPI",
    "sector": null,
    "asset_type": "stock",
    "latest_close": 286500.0,
    "change_pct": 3.24
  }
]
```

ETF 예시(2026-10-14 실제 호출, `q=코덱스200&asset_type=etf&limit=2`). ETF는 `market`/`sector`가 `null`이다:

```json
[
  {
    "ticker": "069500",
    "name": "KODEX 200",
    "market": null,
    "sector": null,
    "asset_type": "etf",
    "latest_close": 96370.0,
    "change_pct": 1.18
  },
  {
    "ticker": "278530",
    "name": "KODEX 200TR",
    "market": null,
    "sector": null,
    "asset_type": "etf",
    "latest_close": 34905.0,
    "change_pct": 0.9
  }
]
```

`asset_type`은 항상 들어 있다(`"stock"` 또는 `"etf"`). `q`는 부분 일치라서 `코덱스200`에는 `KODEX 200TR`처럼 이름이 이어지는 ETF도 같이 나온다.
`asset_type`을 생략한 검색은 이름에 검색어가 든 주식과 ETF를 id 순으로 함께 돌려준다(`q=삼성전자` → 삼성전자(주식), RISE 삼성전자SK하이닉스채권혼합50(ETF)).

`latest_close`/`change_pct`는 DB에 저장된 **가장 최근 종가**(일별, 실시간 아님) 기준이다 - 실시간 시세가 필요하면
`GET /api/stocks/{ticker}/realtime-price`를 따로 호출하세요. 그 종목에 저장된 주가가 아직 없으면 둘 다 `null`.
검색 결과가 없으면 `[]`(에러 아님).

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 422 | `limit`이 1~300 범위 밖, `offset`이 음수이거나 상한 초과, `q`가 100자 초과, `asset_type`이 `stock`/`etf`가 아님, `sort`/`order`가 허용 값이 아님 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## GET /api/discovery/trending

질문 없이 바로 호출하는 시장 전체 스크리닝(FR-13). 저장된 `stock_price`만으로 계산하는 순수 집계다. 로그인 불필요(공개).

| 쿼리 | 기본값 | 규칙 |
| --- | --- | --- |
| `category` | `gainers` | `gainers`(급등) / `losers`(급락) / `volume_surge`(거래량 급증). 다른 값이면 422 |
| `limit` | 10 | 1~50. 벗어나면 422 |
| `asset_type` | `stock` | `stock`(주식) 또는 `etf`. **기본은 주식만**이라 ETF가 섞이지 않는다. 다른 값이면 422 |

```bash
curl "http://localhost:8000/api/discovery/trending?category=gainers&limit=2"
curl "http://localhost:8000/api/discovery/trending?category=gainers&limit=2&asset_type=etf"
```

### 응답 200

기본(주식, 2026-10-14 실제 호출):

```json
{
  "category": "gainers",
  "asset_type": "stock",
  "price_date": "2026-10-08",
  "window_days": 20,
  "items": [
    {
      "ticker": "131970",
      "company_name": "두산테스나",
      "close_price": 123300.0,
      "change_pct": 12.3,
      "volume": 1021196,
      "avg_volume": 384287.45,
      "volume_ratio": 2.66
    },
    {
      "ticker": "229640",
      "company_name": "LS에코에너지",
      "close_price": 72900.0,
      "change_pct": 6.89,
      "volume": 425862,
      "avg_volume": 261817.3,
      "volume_ratio": 1.63
    }
  ]
}
```

`asset_type=etf`(2026-10-14 실제 호출):

```json
{
  "category": "gainers",
  "asset_type": "etf",
  "price_date": "2026-10-14",
  "window_days": 20,
  "items": [
    {
      "ticker": "305720",
      "company_name": "KODEX 2차전지산업",
      "close_price": 15790.0,
      "change_pct": 2.7,
      "volume": 5453150,
      "avg_volume": 4797701.2,
      "volume_ratio": 1.14
    },
    {
      "ticker": "122630",
      "company_name": "KODEX 레버리지",
      "close_price": 83710.0,
      "change_pct": 2.13,
      "volume": 12056891,
      "avg_volume": 15424826.35,
      "volume_ratio": 0.78
    }
  ]
}
```

- `asset_type`은 요청한 유형을 그대로 돌려준다(2026-10-14 추가, 기존 필드는 그대로).
- **최신 거래일(`price_date`)과 평균 거래량의 직전 거래일은 그 유형의 종목끼리만 계산한다.** 위 예시처럼 주식은 2026-10-08, ETF는
  2026-10-14가 최신이다(전체의 최신 날짜를 쓰면 ETF만 더 최근까지 수집된 날 주식 결과가 비어 버린다).
- 거래량 급증은 배수 2.0 이상만 나오므로 `volume_surge`는 `items`가 `[]`일 수 있다(에러 아님).

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 422 | `category`/`asset_type`이 허용 값이 아니거나 `limit`이 범위 밖 | 검증 오류 형식 |
| 503 | 그 유형의 주가 데이터가 아직 없음, DB 연결 불가 | `{"detail": "..."}` |

---

## GET /api/watchlist

로그인 사용자의 관심종목 전체(등록 최신순). 종목별 **최근 종가**(DB에 저장된 일별 종가, 실시간 아님)를 같이 준다 -
실시간 시세가 필요하면 `GET /api/stocks/{ticker}/realtime-price`를 따로 호출하세요.

```bash
curl http://localhost:8000/api/watchlist -H "Authorization: Bearer $TOKEN"
```

### 응답 200

```json
{
  "items": [
    {
      "company_id": 1,
      "ticker": "005930",
      "company_name": "삼성전자",
      "asset_type": "stock",
      "added_at": "2026-10-01T02:29:00.729024+00:00",
      "latest_close": 286500.0,
      "latest_close_date": "2026-09-23",
      "change_pct": 3.24
    }
  ]
}
```

종목에 저장된 주가가 아직 없으면 `latest_close`/`latest_close_date`/`change_pct`가 `null`이다. 관심종목이 없으면 `items: []`.
`asset_type`(`"stock"`/`"etf"`)은 2026-10-14에 추가됐다. ETF도 같은 방식으로 등록·삭제한다.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## POST /api/watchlist

관심종목에 종목을 추가한다.

### 요청

```json
{ "ticker": "삼성전자" }
```

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `ticker` | string | 필수. 종목코드 또는 종목명(별칭/유사 종목명 포함, `/api/research`와 같은 `company_resolver` 사용). 1~50자 |

```bash
curl -X POST http://localhost:8000/api/watchlist \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "삼성전자"}'
```

### 응답 201

```json
{
  "company_id": 1,
  "ticker": "005930",
  "company_name": "삼성전자",
  "asset_type": "stock",
  "added_at": "2026-10-11T08:36:47.116856+00:00",
  "latest_close": 263000.0,
  "latest_close_date": "2026-10-08",
  "change_pct": -2.23
}
```

응답은 `GET /api/watchlist`의 `items[]` 항목과 **같은 형식, 같은 값**이다(2026-10-11부터 최근 종가 3개 필드도 채운다.
그 전에는 항상 `null`이었다). 추가 직후 GET을 다시 부르지 않고 응답을 목록에 바로 넣어도 된다. 저장된 주가가 아직
없는 종목이면 세 필드는 `null`이다.

ETF를 추가한 실제 응답(2026-10-14, 요청 `{"ticker": "코덱스200"}`):

```json
{
  "company_id": 303,
  "ticker": "069500",
  "company_name": "KODEX 200",
  "asset_type": "etf",
  "added_at": "2026-10-14T04:12:48.355657+00:00",
  "latest_close": 96370.0,
  "latest_close_date": "2026-10-14",
  "change_pct": 1.18
}
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 404 | 종목을 찾지 못함(`company_resolver` 기준) | `{"detail": "'...' 종목을 찾지 못했습니다. ..."}` |
| 409 | 이미 등록된 종목(사용자+종목 조합 중복) | `{"detail": "'...'는 이미 관심종목에 등록되어 있습니다."}` |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 422 | `ticker` 누락/공백/50자 초과 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## DELETE /api/watchlist/{ticker}

관심종목에서 종목을 뺀다. `ticker`는 종목코드 또는 종목명(별칭/유사 종목명 포함).

```bash
curl -X DELETE http://localhost:8000/api/watchlist/005930 -H "Authorization: Bearer $TOKEN" -i
```

### 응답 204

본문 없음.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 404 | 종목을 찾지 못함, 또는 종목은 있지만 내 관심종목에 등록돼 있지 않음 | `{"detail": "'...' 종목을 찾지 못했습니다. ..."}` 또는 `{"detail": "'...'는 관심종목에 등록되어 있지 않습니다."}` |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## GET /api/portfolio

모의투자 잔고 + 보유 종목(현재가·평가손익 포함). **사용자의 첫 호출이면 초기 잔고(1,000만원)로 계좌가 자동 생성된다**
(별도 "계좌 개설" API 없음). 현재가는 `GET /api/stocks/{ticker}/realtime-price`와 같은 소스(`get_realtime_price_data`)를
쓰므로 실시간 실패 시 DB 최근 종가로 폴백되는 동작도 동일하다.

```bash
curl http://localhost:8000/api/portfolio -H "Authorization: Bearer $TOKEN"
```

### 응답 200

```json
{
  "cash_balance": 8650000.0,
  "holdings": [
    {
      "ticker": "005930",
      "company_name": "삼성전자",
      "asset_type": "stock",
      "quantity": 5,
      "avg_price": 270000.0,
      "current_price": 270000.0,
      "is_realtime": true,
      "eval_amount": 1350000.0,
      "profit_loss": 0.0,
      "profit_loss_pct": 0.0
    }
  ],
  "total_eval_amount": 1350000.0,
  "total_asset": 10000000.0,
  "note": null
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `cash_balance` | number | 현금 잔고 |
| `holdings[].asset_type` | string | `"stock"` 또는 `"etf"`(2026-10-14 추가). ETF 보유도 같은 항목 형식이다 |
| `holdings[].avg_price` | number | 평균 매입 단가(가중평균, 매도로는 바뀌지 않음) |
| `holdings[].current_price` | number \| null | 현재가. 실시간+DB 폴백 모두 실패하면 `null`(이 종목은 평가손익 계산에서 제외되고 `note`에 안내) |
| `holdings[].is_realtime` | boolean | `current_price`가 실시간 조회 성공값이면 `true`, DB 폴백이면 `false` |
| `holdings[].eval_amount` / `profit_loss` / `profit_loss_pct` | number \| null | 평가금액 / 평가손익(금액) / 평가손익률(%). `current_price`가 `null`이면 같이 `null` |
| `total_eval_amount` | number | 보유 종목 평가금액 합(가격을 못 구한 종목은 0으로 취급돼 빠짐) |
| `total_asset` | number | `cash_balance + total_eval_amount` |
| `note` | string \| null | 가격을 못 구한 종목이 있으면 안내 문구, 없으면 `null` |

ETF를 2주 매수한 뒤의 실제 `holdings`(2026-10-14):

```json
[
  {
    "ticker": "069500",
    "company_name": "KODEX 200",
    "asset_type": "etf",
    "quantity": 2,
    "avg_price": 96350.0,
    "current_price": 96350.0,
    "is_realtime": true,
    "eval_amount": 192700.0,
    "profit_loss": 0.0,
    "profit_loss_pct": 0.0
  }
]
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## POST /api/portfolio/orders

매수/매도 주문. **현재가로 즉시 체결되는 것으로 단순화**했다 - 호가 단위, 장 시간 체크(장외에도 체결됨), 수수료/세금은
범위 밖이다. 가격은 `GET /api/stocks/{ticker}/realtime-price`와 같은 소스를 쓴다(실시간 실패 시 DB 최근 종가로 체결).

### 요청

```json
{ "ticker": "삼성전자", "side": "buy", "quantity": 5 }
```

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `ticker` | string | 필수. 종목코드 또는 종목명. 1~50자. **엄격 매칭**(아래): 정확한 코드/종목명, 공백·대소문자만 다른 이름, 등록된 별칭만 주문된다 |
| `side` | string | 필수. `"buy"` 또는 `"sell"` |
| `quantity` | int | 필수. 1 이상, **최대 100,000주**(비현실적으로 큰 값으로 평균단가 계산이 깨지는 걸 막는 안전장치일 뿐, 실제 유동성 제한은 아님) |

```bash
curl -X POST http://localhost:8000/api/portfolio/orders \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "삼성전자", "side": "buy", "quantity": 5}'
```

### 응답 200

```json
{
  "ticker": "005930",
  "company_name": "삼성전자",
  "side": "buy",
  "quantity": 5,
  "price": 270000.0,
  "executed_at": "2026-10-01T02:29:00.776434+00:00",
  "cash_balance": 8650000.0,
  "holding": { "quantity": 5, "avg_price": 270000.0 }
}
```

`holding`은 이 주문 체결 뒤의 보유 상태다. **매도로 전량 청산됐으면 `null`.**
`asset_type`(`"stock"` \| `"etf"`, 2026-11-09 40차 추가)은 주문한 종목의 자산 유형으로 보유·체결 내역의 `asset_type`과 같은 종목 마스터 값이다(위 예시에는 생략, 실제로는 `"asset_type": "stock"`). 값이 없으면 주식으로 가정하지 않고 `null`이지만, 종목 마스터가 NOT NULL이라 실제로는 항상 있다.

#### 종목 엄격 매칭 (2026-10-27)

주문은 잘못된 종목이 체결되면 되돌릴 수 없으므로 **유사 이름 보정을 쓰지 않는다**(리서치·시세·종목 상세·관심종목 등 다른 API는 기존처럼 보정).
허용: 정확한 종목코드, 정확한 종목명, 공백·대소문자만 다른 이름(`삼성 전자`, `sk하이닉스`), 등록된 별칭(`네이버` → NAVER, `코덱스200` → KODEX 200).
그 밖에 **비슷한 이름만 있으면 주문하지 않고 404**를 돌려주며, 안내 문구에 후보(최대 3개)가 들어간다. 응답 형식은 기존 404와 같고 `detail` 문구만 다르다.

| 입력 | 결과 |
| --- | --- |
| `삼성SD1` | 404 `{"detail": "정확한 종목을 찾지 못했습니다. 혹시 삼성SDI인가요?"}` |
| `현대모비쓰` | 404 `{"detail": "정확한 종목을 찾지 못했습니다. 혹시 현대모비스인가요?"}` |
| `SK하이닉쓰` | 404 `{"detail": "정확한 종목을 찾지 못했습니다. 혹시 SK하이닉스, SK 중 하나인가요?"}` |
| `삼성전자우`(우선주가 DB에 없을 때) | 404 `{"detail": "정확한 종목을 찾지 못했습니다. 혹시 삼성전자인가요?"}` |
| 비슷한 이름도 없음 | 404 `{"detail": "정확한 종목을 찾지 못했습니다. '...'에 해당하는 종목명 또는 종목코드를 정확히 입력해주세요."}` |

프론트는 404의 `detail`을 그대로 보여주면 되고, 후보를 눌러 다시 주문하게 하려면 후보 이름으로 다시 `POST`하면 된다(후보 이름은 정확한 종목명이다).

#### 동시 주문 보호 (2026-10-27)

같은 계정의 주문·초기화는 계좌 행을 `SELECT ... FOR UPDATE`로 잠근 뒤 보유 종목을 읽고 쓴다. 따라서 잔고 100만 원에서 80만 원짜리 매수 2건이 동시에 와도
정확히 1건만 체결되고 나머지는 400(잔고 부족)이다. 보유 수량 초과 매도도 마찬가지이며, 첫 요청이 동시에 와도 계좌는 1개만 만들어진다.
잔고 검증·계좌 갱신·보유 갱신·체결 내역 저장은 한 트랜잭션이라 실패하면 전부 롤백된다. 같은 계정의 요청만 순서대로 처리되고(다른 계정은 서로 막지 않는다),
가격 조회는 잠금 전에 끝내서 외부 호출이 잠금 시간을 늘리지 않는다. 응답 형식은 그대로다.

#### 평균단가 계산

- **매수**: 기존 보유가 없으면 평균단가 = 체결가. 있으면 가중평균 `(기존수량×기존평균 + 이번수량×체결가) / 합산수량`으로 다시 계산한다.
- **매도**: 평균단가는 바뀌지 않는다(남은 수량의 매입 단가는 그대로). 전량 매도되면 보유 레코드 자체를 지운다 -
  그래야 그 종목을 다시 사기 시작할 때 과거 평균단가에 영향받지 않고 새 체결가로 시작한다.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 400 | 매수 시 잔고 부족 | `{"detail": "잔고가 부족합니다(필요 ...원, 보유 ...원)."}` |
| 400 | 매도 시 보유 수량 초과 | `{"detail": "보유 수량이 부족합니다(매도 요청 ...주, 보유 ...주)."}` |
| 404 | 정확한 종목을 찾지 못함(엄격 매칭). 비슷한 이름이 있으면 후보 안내 포함 | `{"detail": "정확한 종목을 찾지 못했습니다. 혹시 A, B 중 하나인가요?"}` (후보가 없으면 `... 종목명 또는 종목코드를 정확히 입력해주세요.`) |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 422 | `ticker`/`side`/`quantity` 형식 위반 | 검증 오류 형식 |
| 503 | 실시간 소스와 DB 폴백 모두 가격을 못 구함(둘 다 없을 때만) | `{"detail": "'...'의 가격을 구하지 못해 주문을 체결할 수 없습니다."}` |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## GET /api/portfolio/trades

내 모의투자 체결 내역(기본 최신순: 체결 시각 내림차순, 같으면 id 내림차순). 로그인한 사용자 본인 것만 나온다.

| 쿼리 | 기본값 | 규칙 |
| --- | --- | --- |
| `limit` | 50 | 1~200. 범위를 벗어나면 422 |
| `offset` | 0 | 0 이상, 9223372036854775807 이하. 벗어나면 422 |
| `sort` | `executed_at` | `executed_at`(체결 시각), `amount`(체결가×수량), `price`(체결가), `quantity`(수량), `ticker`(종목코드). 그 밖은 422 |
| `order` | `desc` | `asc` 또는 `desc`. 기본(`executed_at desc`)이 지금까지의 최신순이다. 같은 값은 `id`가 `order` 방향으로 정한다 |
| `ticker` | 없음 | 선택. 종목코드 또는 종목명(별칭 포함)으로 그 종목의 체결만. 종목을 못 찾으면 404, 빈 문자열이면 422 |

```bash
curl "http://localhost:8000/api/portfolio/trades?limit=20&ticker=005930" -H "Authorization: Bearer $TOKEN"
```

### 응답 200

```json
{
  "total": 2,
  "limit": 50,
  "offset": 0,
  "has_next": false,
  "total_pages": 1,
  "items": [
    {
      "id": 12,
      "ticker": "005930",
      "company_name": "삼성전자",
      "asset_type": "stock",
      "side": "sell",
      "quantity": 1,
      "price": 1000.0,
      "amount": 1000.0,
      "executed_at": "2026-10-07T06:10:11.123456+00:00"
    },
    {
      "id": 11,
      "ticker": "005930",
      "company_name": "삼성전자",
      "asset_type": "stock",
      "side": "buy",
      "quantity": 3,
      "price": 1000.0,
      "amount": 3000.0,
      "executed_at": "2026-10-07T06:10:09.987654+00:00"
    }
  ]
}
```

| 필드 | 설명 |
| --- | --- |
| `total` | 조건(`ticker` 포함)에 맞는 전체 체결 수(`limit`/`offset`과 무관). 페이지 수 계산용 |
| `limit` / `offset` | 요청에 쓰인 값(기본값 포함) |
| `items[].asset_type` | `"stock"` 또는 `"etf"`(2026-10-14 추가) |
| `items[].side` | `"buy"` 또는 `"sell"` |
| `items[].price` | 체결가 |
| `items[].amount` | 체결 금액 = `price × quantity`(서버가 계산, trade 테이블에는 없는 값) |
| `items[].executed_at` | ISO 8601(UTC, `+00:00`) |

ETF 매수 체결 한 건의 실제 `items[]` 항목(2026-10-14):

```json
[
  {
    "id": 1723,
    "ticker": "069500",
    "company_name": "KODEX 200",
    "asset_type": "etf",
    "side": "buy",
    "quantity": 2,
    "price": 96350.0,
    "amount": 192700.0,
    "executed_at": "2026-10-14T04:12:50.939536+00:00"
  }
]
```

`has_next`(`offset + limit < total`)와 `total_pages`(`ceil(total / limit)`, `total`이 0이면 0)는 2026-10-28에 추가된 필드다. `ticker`를 주면 `total`도 그 종목 기준이다.
체결이 없으면 오류가 아니라 `{"total": 0, "limit": 50, "offset": 0, "has_next": false, "total_pages": 0, "items": []}`인 200이다.
다음 페이지는 `has_next`가 true인 동안 `offset += limit`이고, `offset >= total`이면 `items`가 빈 배열인 200이다.

```bash
curl "http://localhost:8000/api/portfolio/trades?sort=amount&order=desc&limit=2" -H "Authorization: Bearer $TOKEN"   # 큰 거래 순
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 404 | `ticker`로 종목을 찾지 못함 | `{"detail": "'...' 종목을 찾지 못했습니다. ..."}` |
| 422 | `limit`/`offset` 범위 밖이거나 정수가 아님, `sort`/`order`가 허용 값이 아님, `ticker`가 빈 문자열/50자 초과 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## POST /api/portfolio/reset

내 모의투자를 처음 상태로 되돌린다: **보유 종목과 체결 내역을 모두 삭제하고 잔고를 초기값(1,000만원)으로 복구**한다.
**되돌릴 수 없다.** 한 트랜잭션으로 처리되어 중간에 실패하면 아무것도 바뀌지 않는다. 관심종목과 리서치 기록은 그대로 두고,
다른 사용자의 데이터에는 영향이 없다.

### 요청

실수로 호출하는 것을 막기 위해 본문이 정확히 `{"confirm": true}`여야 한다(불리언 `true`만 인정, `"true"`나 `1`은 안 됨).

```bash
curl -X POST http://localhost:8000/api/portfolio/reset   -H "Authorization: Bearer $TOKEN"   -H "Content-Type: application/json"   -d '{"confirm": true}'
```

### 응답 200

초기화된 포트폴리오 상태로, `GET /api/portfolio`와 같은 형태다.

```json
{
  "cash_balance": 10000000.0,
  "holdings": [],
  "total_eval_amount": 0.0,
  "total_asset": 10000000.0,
  "note": null
}
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 400 | 본문이 없거나 `{"confirm": true}`가 아님(`false`, 문자열, 다른 키 등). 아무것도 바뀌지 않는다 | `{"detail": "초기화하려면 본문에 {\"confirm\": true}를 보내야 합니다."}` |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 422 | 본문이 JSON이 아님 | 검증 오류 형식 |
| 503 | DB 연결 불가(이 경우 롤백되어 데이터는 그대로) | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## POST /api/portfolio/diagnosis

내 모의투자 포트폴리오 AI 진단. **숫자와 판단(지표·플래그)은 서버 코드가 계산**하고, 종목명과 숫자가 들어가는
**강점(`strengths`)·위험 요인(`risks`) 문장도 서버 코드가 템플릿으로 만든다**. LLM(llama3.1:8b)은 전체 요약(`summary`)과
제안(`suggestions`)만 쓴다. LLM 설명이 검증을 통과하지 못하거나 Ollama가 응답하지 않으면 네 필드 모두 규칙 기반 문장으로
대신한다(`source`로 구분). 호출할 때마다 새로 계산하고, 성공하면 결과를 **저장**한다(아래 "진단 이력"). 본문 없음.

### 저장 필드 (2026-10-14 추가)

성공한 진단은 사용자당 **최근 50건**까지 저장한다(51번째를 저장하면 가장 오래된 것이 지워진다). 응답 맨 끝에 필드 두 개가 **추가**됐고
기존 필드는 하나도 바뀌지 않았다.

| 필드 | 설명 |
| --- | --- |
| `diagnosis_id` | 저장된 진단의 id(`GET /api/portfolio/diagnosis/{id}`, `/compare`, `DELETE`에 쓴다). **저장에 실패하면 `null`** |
| `created_at` | 저장 시각(ISO 8601, UTC). 저장에 실패하면 `null`. `generated_at`(진단을 계산한 시각)과는 수 밀리초 차이가 난다 |

- 저장이 실패해도(DB 오류 등) 진단 응답은 정상(200)이고 두 필드만 `null`이다. 프론트는 `diagnosis_id`가 `null`이면 "이력에 저장되지 않았어요" 정도로 처리하면 된다.
- 보유 종목이 없어 400이 나는 경우는 저장하지 않는다.
- 실제 호출 결과(2026-10-14, 응답 끝부분): `"diagnosis_id": 1012, "created_at": "2026-10-14T03:26:06.065824+00:00"`

**응답 시간**: LLM 경로 실측 약 4~6초(로컬 Ollama, 모델이 메모리에 올라와 있을 때. 첫 호출은 모델 로딩 때문에 30초 이상
걸린 적이 있다 — 2026-10-11 실측 35.9초). 검증에 걸려 재시도하면 7~8초다. LLM 호출 타임아웃은 60초이고, 검증 실패 시
1회 재시도하므로 최악의 경우 그 이상 걸릴 수 있다 — 프론트는 로딩 상태와 넉넉한 타임아웃(90초 이상)을 두세요.

```bash
curl -X POST http://localhost:8000/api/portfolio/diagnosis -H "Authorization: Bearer $TOKEN"
```

### 응답 200 (실제 호출 결과, 2026-10-11, 삼성전자 2주·카카오 3주·에코프로비엠 5주 매수 직후라 손익이 0)

```json
{
  "generated_at": "2026-10-11T08:36:37.023809+00:00",
  "source": "llm",
  "model": "llama3.1:8b",
  "metrics": {
    "total_asset": 10000000.0,
    "cash_balance": 8744300.0,
    "cash_weight_pct": 87.44,
    "stock_eval_amount": 1255700.0,
    "holding_count": 3,
    "priced_holding_count": 3,
    "top1_weight_pct": 6.33,
    "top3_weight_pct": 12.56,
    "herfindahl_index": 0.4352,
    "market_weights_pct": {
      "KOSDAQ": 50.37,
      "KOSPI": 49.63
    },
    "total_profit_loss": 0.0,
    "total_profit_loss_pct": 0.0,
    "best_holding": {
      "ticker": "005930",
      "company_name": "삼성전자",
      "profit_loss_pct": 0.0
    },
    "worst_holding": {
      "ticker": "005930",
      "company_name": "삼성전자",
      "profit_loss_pct": 0.0
    }
  },
  "holdings": [
    {
      "ticker": "005930",
      "company_name": "삼성전자",
      "market": "KOSPI",
      "quantity": 2,
      "avg_price": 263000.0,
      "current_price": 263000.0,
      "is_realtime": false,
      "price_unavailable": false,
      "eval_amount": 526000.0,
      "weight_pct": 5.26,
      "profit_loss": 0.0,
      "profit_loss_pct": 0.0,
      "return_20d_pct": -2.59
    },
    {
      "ticker": "035720",
      "company_name": "카카오",
      "market": "KOSPI",
      "quantity": 3,
      "avg_price": 32400.0,
      "current_price": 32400.0,
      "is_realtime": false,
      "price_unavailable": false,
      "eval_amount": 97200.0,
      "weight_pct": 0.97,
      "profit_loss": 0.0,
      "profit_loss_pct": 0.0,
      "return_20d_pct": -11.35
    },
    {
      "ticker": "247540",
      "company_name": "에코프로비엠",
      "market": "KOSDAQ",
      "quantity": 5,
      "avg_price": 126500.0,
      "current_price": 126500.0,
      "is_realtime": false,
      "price_unavailable": false,
      "eval_amount": 632500.0,
      "weight_pct": 6.33,
      "profit_loss": 0.0,
      "profit_loss_pct": 0.0,
      "return_20d_pct": 18.56
    }
  ],
  "flags": [
    {
      "code": "CASH_HIGH",
      "message": "현금 비중이 87.44%로 기준(70%) 이상입니다.",
      "value": 87.44,
      "threshold": 70.0,
      "ticker": null,
      "company_name": null,
      "market": null
    }
  ],
  "summary": "포트폴리오의 총자산은 10,000,000원이며, 현금 비중은 87.44%로 기준(70%) 이상입니다.",
  "strengths": [
    "상위 1종목 비중이 6.33%로 한 종목에 크게 쏠려 있지 않습니다.",
    "3개 종목에 나눠 보유하고 있습니다.",
    "에코프로비엠은 총자산의 6.33%이고, 최근 20거래일 주가 등락률은 +18.56%입니다."
  ],
  "risks": [
    "현금 비중이 87.44%로 기준(70%) 이상입니다.",
    "카카오는 총자산의 0.97%이고, 최근 20거래일 주가 등락률은 -11.35%입니다.",
    "삼성전자는 총자산의 5.26%이고, 최근 20거래일 주가 등락률은 -2.59%입니다."
  ],
  "suggestions": [
    "현금 비중이 기준 이상인 점을 검토해볼 수 있습니다."
  ],
  "notes": [
    "삼성전자, 카카오, 에코프로비엠는 실시간 시세 대신 DB에 저장된 최근 종가로 계산했습니다."
  ],
  "disclaimer": "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
}
```

같은 포트폴리오에서 LLM 설명을 쓰지 못했을 때(폴백)의 설명 부분. 폴백에는 종목별 문장이 붙지 않는다:

```json
{
  "source": "rule_based",
  "model": null,
  "summary": "총자산 10,000,000원 중 현금이 8,744,300원(비중 87.44%)이고, 보유 종목은 3개입니다. 총 평가손익은 0원(0.0%)입니다. 규칙 점검에서 1개 항목이 확인됐습니다.",
  "strengths": [
    "상위 1종목 비중이 6.33%로 한 종목에 크게 쏠려 있지 않습니다.",
    "3개 종목에 나눠 보유하고 있습니다."
  ],
  "risks": [
    "현금 비중이 87.44%로 기준(70%) 이상입니다."
  ],
  "suggestions": [
    "현금 비중이 높은 이유와 투자 계획을 점검해볼 수 있습니다."
  ],
  "notes": [
    "삼성전자, 카카오, 에코프로비엠는 실시간 시세 대신 DB에 저장된 최근 종가로 계산했습니다.",
    "AI 설명을 생성하지 못해 규칙 기반 문장으로 대신했습니다."
  ]
}
```

| 필드 | 설명 |
| --- | --- |
| `generated_at` | 진단 시각(ISO 8601, UTC) |
| `source` | `"llm"`(`summary`·`suggestions`를 LLM이 썼고 검증을 통과) 또는 `"rule_based"`(네 필드 모두 폴백 문장) |
| `model` | `source`가 `llm`이면 모델명, 아니면 `null` |
| `metrics.total_asset` | 총자산 = 현금 + 현재가를 구한 종목의 평가금액 |
| `metrics.cash_weight_pct` | 총자산 대비 현금 비중(%) |
| `metrics.stock_eval_amount` | 주식 평가금액 합계(현재가를 못 구한 종목 제외) |
| `metrics.holding_count` / `priced_holding_count` | 보유 종목 수 / 그중 현재가를 구한 종목 수 |
| `metrics.top1_weight_pct` / `top3_weight_pct` | 평가금액 상위 1개 / 3개 종목 합계 비중(총자산 대비 %) |
| `metrics.herfindahl_index` | 허핀달 지수(0~1). 현재가를 구한 종목끼리의 비중(주식 평가금액 대비)의 제곱합. 1이면 한 종목뿐 |
| `metrics.market_weights_pct` | 주식 평가금액 대비 시장별 비중(%). `KOSPI` / `KOSDAQ`(KOSDAQ GLOBAL 포함) / `OTHER`. **ETF는 빼고 계산한다**(2026-10-14). 주식이 없으면 `{}` |
| `metrics.total_profit_loss` / `total_profit_loss_pct` | 총 평가손익(원) / 매입금액 합계 대비 손익률(%) |
| `metrics.best_holding` / `worst_holding` | 손익률 최고/최저 종목. 손익률을 계산할 종목이 없으면 `null` |
| `holdings[].weight_pct` | 총자산 대비 비중(%) |
| `holdings[].asset_type` | `"stock"` 또는 `"etf"`(2026-10-14 추가) |
| `holdings[].market` | `KOSPI` / `KOSDAQ` / `OTHER`, ETF는 `"ETF"`(2026-10-14, 이전에는 `OTHER`) |
| `holdings[].profit_loss_pct` | 평균 매입가 대비 손익률(%) |
| `holdings[].return_20d_pct` | 최근 20거래일 등락률(%) = DB 최신 종가 / 20거래일 전 종가 - 1. **실시간이 아니라 저장된 일별 종가 기준**이고, 저장된 주가가 21일치 미만이면 `null` |
| `holdings[].price_unavailable` | 현재가(실시간+DB 폴백)를 못 구한 종목. 이 경우 `eval_amount`/`weight_pct`/`profit_loss*`가 `null`이고 합계에서 빠지며 `notes`에 안내가 붙는다 |
| `flags[]` | 규칙 기반 점검 결과. `code`, `message`(그대로 보여줘도 되는 문장), `value`(실제 값), `threshold`(기준값), 종목/시장 관련이면 `ticker`·`company_name` 또는 `market` |
| `summary` | 전체 요약 문장. `source`가 `llm`이면 LLM이 쓴 문장 |
| `strengths` | 강점 문장(항상 1개 이상, **항상 서버 코드가 만든다**). 포트폴리오 문장(상위 1종목 비중, 보유 종목 수, 손익률 최고 종목, 현금 비중) + `source`가 `llm`이면 최근 20거래일 주가가 **오른** 종목의 문장(등락률이 큰 순, 최대 3개) |
| `risks` | 위험 요인 문장(항상 1개 이상, **항상 서버 코드가 만든다**). 플래그의 `message`(플래그가 없으면 "규칙 기준으로 확인된 위험 항목은 없습니다.") + `source`가 `llm`이면 최근 20거래일 주가가 **내린** 종목의 문장(하락 폭이 큰 순, 최대 3개) |
| `suggestions` | 제안 문장(항상 1개 이상). `source`가 `llm`이면 LLM이 쓴 문장 중 숫자가 없는 것만 남긴다(전부 걸러지면 규칙 기반 제안) |
| `notes` | 현재가 조회 실패, DB 종가로 대체, 20거래일 수익률 계산 불가, 폴백 사용 등 안내 문장 |
| `disclaimer` | 면책 문구(`GET /api/disclaimer`와 같은 값). 진단 화면에 반드시 함께 보여주세요 |

#### 플래그 규칙 (임계값은 `backend/app/diagnosis.py` 상단 상수)

| `code` | 조건 |
| --- | --- |
| `CONCENTRATION_HIGH` | 상위 1종목 비중(총자산 대비) ≥ 40% |
| `FEW_HOLDINGS` | 보유 종목 수 < 3. **ETF를 하나라도 보유하면 걸지 않는다**(2026-10-14): ETF는 한 상품 안에서 여러 자산에 나눠 투자해서 종목 수로 분산 정도를 볼 수 없다. 대신 `strengths`에 "보유 종목은 N개지만 ETF가 M개 포함돼 있습니다. …" 안내 문장이 들어간다. 한 종목 쏠림은 `CONCENTRATION_HIGH`가 그대로 본다 |
| `CASH_HIGH` | 현금 비중 ≥ 70% |
| `CASH_LOW` | 현금 비중 < 5% |
| `BIG_LOSS` | 종목 손익률 ≤ -10% (해당 종목마다 1개) |
| `MARKET_SKEW` | 한 시장 비중(주식 평가금액 대비, **ETF 제외**) ≥ 80% |
| `LEVERAGED_INVERSE_ETF` | **2026-10-14 추가.** 이름으로 판별한 레버리지·인버스 ETF를 보유(해당 ETF마다 1개). `value` = 그 ETF의 총자산 대비 비중(%, 현재가를 못 구했으면 0), `threshold` = 0, `ticker`·`company_name` 포함. `message` 예: "KODEX 레버리지는 레버리지 ETF로 총자산의 2.47%입니다. 하루 수익률을 기준으로 움직여 오래 보유하면 기초지수 수익률과 달라질 수 있습니다." 진단 이력 비교(`/compare`)에서 다른 플래그처럼 `(code, ticker)`로 신규/해소가 잡힌다 |

#### 종목 문장 템플릿 (`strengths`/`risks`의 종목별 문장)

조사(은/는, 이/가)는 2026-10-14부터 읽는 소리 기준으로 붙인다("KODEX 200은", "에코프로비엠은", "NAVER는"). 예전에는 숫자·영문으로
끝나는 이름에 모두 '는'을 붙여 "KODEX 200는", "S-Oil는"처럼 틀렸다(`notes`의 종목 목록 문장도 같다).

`{종목명}은/는 총자산의 {weight_pct}%이고, 최근 20거래일 주가 등락률은 {return_20d_pct}%입니다.` 한 문장의 숫자는 모두 그
종목의 `holdings[]` 값이다(등락률은 부호를 붙인다: `+18.56%`, `-11.35%`). 등락률이 `null`이거나 0인 종목은 나오지 않고,
현재가를 못 구한 종목은 비중 부분이 빠진다. 2026-10-11 전에는 이 두 목록도 LLM이 썼는데, 숫자도 종목명도 입력에 있는 값인데
짝이 틀린 문장("비중 상위 1개 종목은 삼성전자로 6.33%입니다" — 실제 1위는 에코프로비엠)이 3회 호출 중 3회 나와서 바꿨다.

#### LLM 설명 검증과 폴백

- LLM 입력은 지표·플래그·종목별 숫자뿐이다(아이디, 사용자 ID 등 식별 정보는 넣지 않는다). 종목은 비중이 큰 순으로 넣는다.
- 출력은 JSON(`summary`, `suggestions`)이어야 하고(다른 키는 무시한다), 아래 중 하나라도 걸리면 **1회 재시도** 후에도 실패하면 폴백한다.
  - JSON 파싱 실패 / `summary`·`suggestions` 누락·형식 오류 / `suggestions`가 빈 배열
  - **입력에 없는 숫자**: 허용되는 숫자는 입력에 나온 숫자(소수 0~2자리 반올림, 천·만·억 단위 환산 표기 포함)와 1~10 정수뿐
  - **종목명과 맞지 않는 숫자**: 보유 종목명이 나온 문장의 숫자는 그 종목의 숫자(`holdings[]` 항목, 그 종목의 플래그 값·기준, 기간 20)여야 한다.
    다른 종목의 숫자나 포트폴리오 전체 숫자를 같은 문장에 쓰면 실패다
  - **보유하지 않은 종목명**(대표 종목의 통칭·등록명 약 100개 사전 기준) / **비중 1위 종목을 틀리게 말한 문장**("상위 1개 종목", "비중이 가장 큰" 등)
  - "매수하세요", "매도하세요" 같은 직접 매매 지시 / 입력에 없는 지표 이름("이동평균")
- 숫자가 들어간 제안 문장은 실패로 치지 않고 **버린다**(지표를 다시 읊거나 없는 기준을 지어낸 문장이라서). 전부 버려지면 규칙 기반 제안을 쓴다.
- Ollama 연결 실패·타임아웃·모델 오류는 **재시도 없이 바로 폴백**한다(503/502가 아니라 200 + `source: "rule_based"`).
- 검증은 LLM이 쓴 `summary`·`suggestions`의 의미까지는 잡지 못한다(예: 숫자 없이 사실과 다른 평가를 하는 문장).
  수치 자체는 `metrics`/`holdings`/`flags`와 코드가 만든 `strengths`/`risks`를 기준으로 보여주세요.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 400 | 보유 종목이 없음(LLM을 호출하지 않고 저장도 하지 않음) | `{"detail": "보유 종목이 없습니다. 모의투자 주문 후 진단할 수 있어요."}` |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 503 | 진단에 필요한 DB 조회가 불가(저장만 실패한 경우는 에러가 아니라 200 + `diagnosis_id: null`) | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

LLM 실패는 에러가 아니라 폴백(200)이다.

---

## 진단 이력 (2026-10-14 추가)

`POST /api/portfolio/diagnosis` 결과를 저장해 두고 다시 보거나 이전 진단과 비교한다. 아래 4개 엔드포인트는 모두 **로그인 필요**이고
**본인 진단만** 대상이다. 남의 진단 id는 **없는 id와 똑같이 404**다(403이 아니다 — 그 id가 존재하는지 드러내지 않기 위해).
사용자당 최근 50건만 보관하므로, 오래된 진단은 새 진단이 저장될 때 사라진다(그 id로 조회하면 404). 아래 예시는 dev DB에서 임시
계정으로 실제 호출한 결과이고(2026-10-14), 두 번째 진단 전에 시세를 일부러 바꿨다(삼성전자 270,000→283,500원, 카카오 40,000→34,000원,
에코프로비엠 8주 신규 매수).

---

## GET /api/portfolio/diagnosis/history

내 진단 이력(기본 최신순). 이력이 없으면 `total: 0`, `items: []`.

### 쿼리 파라미터

| 이름 | 기본값 | 규칙 |
| --- | --- | --- |
| `limit` | 20 | 1~100. 범위 밖이면 422 |
| `offset` | 0 | 0 이상, 9223372036854775807 이하. 음수이거나 상한 초과면 422 |
| `sort` | `created_at` | `created_at`, `id`, `total_asset`(총자산), `holdings_count`(보유 종목 수). 그 밖은 422 |
| `order` | `desc` | `asc` 또는 `desc`. 기본(`created_at desc`)이 지금까지의 최신순이다. 같은 값은 `id`가 `order` 방향으로 정한다 |

```bash
curl "http://localhost:8000/api/portfolio/diagnosis/history?limit=20&offset=0" -H "Authorization: Bearer $TOKEN"
```

### 응답 200

```json
{
  "total": 2,
  "limit": 20,
  "offset": 0,
  "has_next": false,
  "total_pages": 1,
  "items": [
    {
      "id": 1012,
      "created_at": "2026-10-14T03:26:06.065824+00:00",
      "source": "llm",
      "total_asset": 10015000.0,
      "holdings_count": 3,
      "flags": [
        {
          "code": "BIG_LOSS",
          "ticker": "035720",
          "company_name": "카카오",
          "market": null
        }
      ],
      "summary_preview": "포트폴리오의 총자산은 10,015,000원이며, 현금 비중은 52.92%입니다. 가장 큰 비중을 차지하는 종목은 삼성전자로, 비중은 28.31%"
    },
    {
      "id": 1011,
      "created_at": "2026-10-14T03:26:02.006317+00:00",
      "source": "llm",
      "total_asset": 10000000.0,
      "holdings_count": 2,
      "flags": [
        {
          "code": "FEW_HOLDINGS",
          "ticker": null,
          "company_name": null,
          "market": null
        },
        {
          "code": "MARKET_SKEW",
          "ticker": null,
          "company_name": null,
          "market": "KOSPI"
        }
      ],
      "summary_preview": "포트폴리오 총자산은 10,000,000 원이며, 현금 비중은 65%, 주식 비중은 35%입니다. 삼성전자와 카카오가 비중 상위 2개 종목입니다."
    }
  ]
}
```

| 필드 | 설명 |
| --- | --- |
| `total` | 내 저장된 진단 전체 수(최대 50). `limit`/`offset`은 요청값 그대로 |
| `has_next` | `offset + limit < total` (2026-10-28 추가) |
| `total_pages` | `ceil(total / limit)`, `total`이 0이면 0 (2026-10-28 추가) |
| `items[].id` | 진단 id(`POST` 응답의 `diagnosis_id`와 같은 값) |
| `items[].created_at` | 저장 시각(ISO 8601, UTC) |
| `items[].source` | `"llm"` 또는 `"rule_based"` |
| `items[].total_asset` | 그 시점의 총자산(원). 상세의 `metrics.total_asset`과 같은 값 |
| `items[].holdings_count` | 그 시점의 보유 종목 수(`metrics.holding_count`) |
| `items[].flags[]` | 그 진단에서 걸린 플래그 요약: `code`, 그리고 종목/시장 플래그면 `ticker`·`company_name`(BIG_LOSS) 또는 `market`(MARKET_SKEW), 해당 없으면 `null`. 메시지·값·기준은 상세/비교에서 본다. **플래그에는 심각도 구분이 없다**(진단 규칙이 심각도를 계산하지 않는다) |
| `items[].summary_preview` | `summary`의 앞 80자(잘랐다는 표시는 붙이지 않는다. 80자 이하면 전체) |

목록 응답에는 `disclaimer`가 없다(`GET /api/research` 목록과 같은 관례). 상세·비교에는 있다.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 422 | `limit`/`offset`이 범위 밖이거나 숫자가 아님, `sort`/`order`가 허용 값이 아님 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

`/api/portfolio/diagnosis/history`는 `/api/portfolio/diagnosis/{id}`보다 먼저 정의돼 있어서 `history`가 id로 해석되지 않는다.

---

## GET /api/portfolio/diagnosis/{id}

내 진단 한 건. **저장된 진단 응답 본문 그대로**(`POST /api/portfolio/diagnosis` 응답에서 `diagnosis_id`·`created_at`을 뺀 것)에
`id`와 `created_at`을 더한 모양이다. 그래서 진단 결과 화면을 `POST` 응답과 같은 코드로 그릴 수 있다. 저장 당시의 값이라 이후에 보유 종목이
바뀌어도 변하지 않는다. 면책 문구(`disclaimer`)도 저장본에 들어 있다.

```bash
curl http://localhost:8000/api/portfolio/diagnosis/1012 -H "Authorization: Bearer $TOKEN"
```

### 응답 200

필드는 `POST /api/portfolio/diagnosis`의 "응답 200" 표와 같다. 다른 점은 맨 끝의 두 필드뿐이다(`POST`의 `diagnosis_id` 자리에 `id`).

| 필드 | 설명 |
| --- | --- |
| `id` | 진단 id |
| `created_at` | 저장 시각(ISO 8601, UTC) |

실제 호출 결과의 맨 끝부분(나머지는 같은 호출의 `POST` 응답과 동일):

```json
  ...
  "disclaimer": "...",
  "id": 1012,
  "created_at": "2026-10-14T03:26:06.065824+00:00"
}
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 404 | 없는 id이거나 **남의 진단**(구분하지 않는다) | `{"detail": "diagnosis_id=999999999 진단을 찾을 수 없습니다."}` |
| 422 | `id`가 1 이상의 정수가 아님 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## GET /api/portfolio/diagnosis/{id}/compare

`{id}` 진단(현재)을 이전 진단과 비교한다. **숫자는 전부 서버 코드가 저장된 두 진단에서 계산**하고 LLM은 쓰지 않는다(그래서 빠르다).

| 쿼리 | 설명 |
| --- | --- |
| `base` | 비교 기준이 될 내 다른 진단의 id. 생략하면 `{id}` **바로 이전 진단**(같은 사용자, `created_at` 기준, 같으면 id 기준)을 쓴다. `{id}`보다 새로운 진단을 줘도 되고(변화 방향이 거꾸로 계산된다), 같은 id를 줘도 된다(변화 0). 1 이상의 정수가 아니면 422 |

```bash
curl http://localhost:8000/api/portfolio/diagnosis/1012/compare -H "Authorization: Bearer $TOKEN"
curl "http://localhost:8000/api/portfolio/diagnosis/1012/compare?base=1011" -H "Authorization: Bearer $TOKEN"
```

### 응답 200

```json
{
  "current": {
    "id": 1012,
    "created_at": "2026-10-14T03:26:06.065824+00:00",
    "source": "llm",
    "total_asset": 10015000.0,
    "holdings_count": 3,
    "flags": [
      {
        "code": "BIG_LOSS",
        "ticker": "035720",
        "company_name": "카카오",
        "market": null
      }
    ],
    "summary_preview": "포트폴리오의 총자산은 10,015,000원이며, 현금 비중은 52.92%입니다. 가장 큰 비중을 차지하는 종목은 삼성전자로, 비중은 28.31%"
  },
  "previous": {
    "id": 1011,
    "created_at": "2026-10-14T03:26:02.006317+00:00",
    "source": "llm",
    "total_asset": 10000000.0,
    "holdings_count": 2,
    "flags": [
      {
        "code": "FEW_HOLDINGS",
        "ticker": null,
        "company_name": null,
        "market": null
      },
      {
        "code": "MARKET_SKEW",
        "ticker": null,
        "company_name": null,
        "market": "KOSPI"
      }
    ],
    "summary_preview": "포트폴리오 총자산은 10,000,000 원이며, 현금 비중은 65%, 주식 비중은 35%입니다. 삼성전자와 카카오가 비중 상위 2개 종목입니다."
  },
  "changes": {
    "total_asset": {
      "previous": 10000000.0,
      "current": 10015000.0,
      "change_amount": 15000.0,
      "change_pct": 0.15
    },
    "holdings": [
      {
        "ticker": "247540",
        "company_name": "에코프로비엠",
        "status": "added",
        "previous_weight_pct": null,
        "current_weight_pct": 11.98,
        "weight_change_pp": 11.98
      },
      {
        "ticker": "005930",
        "company_name": "삼성전자",
        "status": "kept",
        "previous_weight_pct": 27.0,
        "current_weight_pct": 28.31,
        "weight_change_pp": 1.31
      },
      {
        "ticker": "035720",
        "company_name": "카카오",
        "status": "kept",
        "previous_weight_pct": 8.0,
        "current_weight_pct": 6.79,
        "weight_change_pp": -1.21
      }
    ],
    "flags": {
      "added": [
        {
          "code": "BIG_LOSS",
          "message": "카카오의 손익률이 -15.0%로 기준(-10%) 이하입니다.",
          "value": -15.0,
          "threshold": -10.0,
          "ticker": "035720",
          "company_name": "카카오",
          "market": null
        }
      ],
      "resolved": [
        {
          "code": "FEW_HOLDINGS",
          "message": "보유 종목이 2개로 기준(3개) 미만입니다.",
          "value": 2.0,
          "threshold": 3.0,
          "ticker": null,
          "company_name": null,
          "market": null
        },
        {
          "code": "MARKET_SKEW",
          "message": "주식 평가금액의 100.0%가 KOSPI에 몰려 있습니다(기준 80%).",
          "value": 100.0,
          "threshold": 80.0,
          "ticker": null,
          "company_name": null,
          "market": "KOSPI"
        }
      ]
    },
    "returns_20d": [
      {
        "ticker": "005930",
        "company_name": "삼성전자",
        "previous_return_20d_pct": -2.59,
        "current_return_20d_pct": -2.59,
        "change_pp": 0.0
      },
      {
        "ticker": "035720",
        "company_name": "카카오",
        "previous_return_20d_pct": -11.35,
        "current_return_20d_pct": -11.35,
        "change_pp": 0.0
      }
    ]
  },
  "disclaimer": "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
}
```

| 필드 | 설명 |
| --- | --- |
| `current` / `previous` | 비교한 두 진단의 요약. 이력 목록의 항목과 같은 모양(`id`, `created_at`, `source`, `total_asset`, `holdings_count`, `flags`, `summary_preview`) |
| `changes.total_asset` | `previous`, `current`(각 진단의 `metrics.total_asset`), `change_amount`(현재 - 이전, 원), `change_pct`(이전 대비 %, 이전 총자산이 0이면 `null`) |
| `changes.holdings[]` | 종목별 비중 변화. 두 진단에 나온 모든 종목이 한 번씩 들어 있고, **변화 폭(절댓값)이 큰 순**이다(변화를 모르는 종목은 맨 뒤) |
| `changes.holdings[].status` | `"added"`(이전에 없던 **신규 편입**) / `"removed"`(이번에 없는 **전량 제외**) / `"kept"`(양쪽 모두 보유) |
| `changes.holdings[].previous_weight_pct` / `current_weight_pct` | 각 진단의 총자산 대비 비중(%). 그 진단에 없던 종목이면 `null` |
| `changes.holdings[].weight_change_pp` | 비중 변화(%p, 현재 - 이전). `added`는 현재 비중(이전을 0으로 봄), `removed`는 -이전 비중. 현재가를 못 구해 비중이 `null`인 쪽이 있으면 `null` |
| `changes.flags.added` | **신규** 플래그: 현재 진단에만 있는 것. 항목은 `POST` 응답의 `flags[]`와 같다(`code`, `message`, `value`, `threshold`, `ticker`, `company_name`, `market`) |
| `changes.flags.resolved` | **해소**된 플래그: 이전 진단에만 있던 것(메시지·값은 이전 진단 기준). 양쪽에 모두 있는 플래그는 어느 쪽에도 나오지 않는다 |
| `changes.returns_20d[]` | 20거래일 수익률 변화. **양쪽 진단에 모두 있고 두 값 다 `null`이 아닌 종목만**(신규 편입/전량 제외 종목은 없다). `previous_return_20d_pct`, `current_return_20d_pct`, `change_pp`(%p, 현재 - 이전). 변화 폭이 큰 순 |
| `disclaimer` | 면책 문구. 비교 화면에 함께 보여주세요 |

- 플래그가 "같은 플래그"인지는 `(code, ticker, market)`으로 가른다: `BIG_LOSS`는 종목마다, `MARKET_SKEW`는 시장마다 따로다
  (카카오의 BIG_LOSS가 해소되고 삼성전자의 BIG_LOSS가 새로 생기면 각각 해소/신규다).
- `returns_20d`는 **저장된 일별 종가 기준**이라(실시간이 아니다) 두 진단 사이에 새 종가가 쌓이지 않았으면 변화가 0이다(위 예시처럼).
- 총자산·비중은 현재가를 구한 종목만 포함한 값이다. 어느 한쪽 진단에서 현재가를 못 구한 종목이 있으면(`price_unavailable`) 그만큼 총자산 변화가 실제와 다를 수 있다.
- 두 진단은 **보유 상태의 스냅샷 비교**다. 비중 변화에는 주문(수량 변화)과 시세 변화가 함께 들어 있고, 둘을 구분해 주지는 않는다.

### 이전 진단이 없을 때 (첫 진단이거나, 이전 것이 이미 삭제/정리됨)

에러가 아니라 **200**이고 `previous`와 `changes`가 `null`이다(`disclaimer`는 그대로). 실제 호출 결과(첫 진단 `1011`):

```json
{
  "current": {
    "id": 1011,
    "created_at": "2026-10-14T03:26:02.006317+00:00",
    "source": "llm",
    "total_asset": 10000000.0,
    "holdings_count": 2,
    "flags": [
      {
        "code": "FEW_HOLDINGS",
        "ticker": null,
        "company_name": null,
        "market": null
      },
      {
        "code": "MARKET_SKEW",
        "ticker": null,
        "company_name": null,
        "market": "KOSPI"
      }
    ],
    "summary_preview": "포트폴리오 총자산은 10,000,000 원이며, 현금 비중은 65%, 주식 비중은 35%입니다. 삼성전자와 카카오가 비중 상위 2개 종목입니다."
  },
  "previous": null,
  "changes": null,
  "disclaimer": "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
}
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 404 | `{id}`가 없거나 남의 진단 / **`base`가 없거나 남의 진단**(구분하지 않는다) | `{"detail": "diagnosis_id=<id> 진단을 찾을 수 없습니다."}` (`<id>`는 못 찾은 쪽의 id) |
| 422 | `{id}` 또는 `base`가 1 이상의 정수가 아님 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## DELETE /api/portfolio/diagnosis/{id}

내 진단 한 건을 삭제한다(**되돌릴 수 없다**). 이미 삭제됐거나 없거나 남의 진단이면 404다(그래서 같은 요청을 두 번 보내면 두 번째는 404).
삭제하면 그 진단은 이력 목록과 "바로 이전 진단" 계산에서도 빠진다.

```bash
curl -X DELETE http://localhost:8000/api/portfolio/diagnosis/1011 -H "Authorization: Bearer $TOKEN"
```

### 응답 204

본문 없음(`DELETE /api/research/{report_id}`와 같은 관례).

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 404 | 없는 id이거나 남의 진단(구분하지 않는다). 아무것도 지워지지 않는다 | `{"detail": "diagnosis_id=<id> 진단을 찾을 수 없습니다."}` |
| 422 | `id`가 1 이상의 정수가 아님 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## GET /api/status

데이터 기준일. 로그인 없이 호출할 수 있고 읽기 전용(집계 MAX 쿼리 7개)이다. 화면 푸터에 "데이터 기준일"을 보여주는 용도다.
기존 API 응답은 바뀌지 않았고 이 엔드포인트만 추가됐다(2026-10-14).

```bash
curl http://localhost:8000/api/status
```

```json
{
  "generated_at": "2026-10-14T17:25:31+09:00",
  "sources": {
    "stock_price":   {"latest_date": "2026-10-14"},
    "etf_price":     {"latest_date": "2026-10-14"},
    "market_index":  {"latest_date": "2026-10-14", "stale": false},
    "exchange_rate": {"latest_date": "2026-10-12"},
    "news":          {"latest_published_date": "2026-10-14"},
    "disclosure":    {"latest_date": "2026-10-14"}
  },
  "last_refresh": {"finished_at": "2026-10-14T18:05:00+09:00", "ok": true, "failed_steps": []},
  "is_realtime": false,
  "note": "주가는 종가 기준이며, 실시간 시세는 별도 엔드포인트로 제공됩니다."
}
```

(위 값은 형태를 보여주는 예시다. 실제 갱신 직후 값은 README "데이터 현황" 참고.)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `generated_at` | string | 응답 생성 시각(KST, ISO 8601) |
| `sources.stock_price.latest_date` | date \| null | 주식 주가의 가장 최근 날짜 |
| `sources.etf_price.latest_date` | date \| null | ETF 주가의 가장 최근 날짜 |
| `sources.market_index.latest_date` | date \| null | 코스피/코스닥 중 더 뒤처진 쪽의 최신 날짜 |
| `sources.market_index.stale` | boolean | 최근 거래일 대비 **2영업일 이상** 뒤처졌거나 데이터가 없으면 `true`. 2026-11-08부터 KRX 거래일 달력 기준(주말·공휴일·연말 휴장을 세지 않는다, `docs/DATA_QUALITY_AND_TRADING_CALENDAR.md`). 필드는 그대로다 |
| `sources.exchange_rate.latest_date` | date \| null | 환율(USD/KRW)의 최신 날짜 |
| `sources.news.latest_published_date` | date \| null | 뉴스 발행일 중 가장 최근(KST 기준 날짜) |
| `sources.disclosure.latest_date` | date \| null | 공시일 중 가장 최근(KST 기준 날짜) |
| `last_refresh` | object \| null | 마지막 `scripts/refresh_data.py` 실행 요약. 기록이 없으면 `null` |
| `last_refresh.finished_at` | string \| null | 그 실행의 마지막 단계 종료 시각(KST) |
| `last_refresh.ok` | boolean | 실패한 단계가 없으면 `true`(건너뛴 단계는 실패로 보지 않는다) |
| `last_refresh.failed_steps` | string[] | 실패한 단계 이름(`prices`, `index`, `fx`, `news`, `embed`, `content`) |
| `is_realtime` | boolean | 항상 `false`. 이 값들은 종가 기준 DB 값이다 |
| `note` | string | 화면에 그대로 써도 되는 안내 문장 |

`503`(DB 연결 불가)은 다른 API와 같은 형식이다. 날짜 필드는 데이터가 아예 없으면 `null`일 수 있다.

### API 통합 점검과 프런트엔드 연동(2026-11-08, 37차)

**프런트 연동 진입점: `docs/FRONTEND_INTEGRATION_HANDOFF.md`**(전체 목록·인증·오류·최신성·상태값·페이지네이션·기능별 연동·미해결 사항).
TypeScript 타입: `docs/frontend-api-types.ts`(OpenAPI에서 자동 생성, `python -m scripts.export_api_types`, 테스트가 최신 여부 확인).

- 응답 구조는 바꾸지 않았다. 바뀐 것:
  - OpenAPI(`/openapi.json`)에 오류 응답을 선언했다(동작 변화 없음): 인증 API의 401, 경로 파라미터 API의 404, `GET /api/*`의 429(IP당 공개 GET 한도),
    엔드포인트·같은 모듈 도우미 함수가 실제로 내는 400·404·409·429, 본문이 있으면 413, 모두 503. 오류 본문 스키마 `ErrorResponse {detail: string}`.
  - `GET /api/portfolio/exposure?view=look_through`의 `summary.over_allocated_amount`가 `-0.0`(음수 0)으로 나오던 것을 `0.0`으로 고쳤다.
  - `POST /api/briefings/daily/generate` 본문의 잘못된 `timezone` 422가 `loc: ["query", "timezone"]`이던 것을 `["body", "timezone"]`으로 고쳤다.
  - 위 "인증" 표에 빠져 있던 `/api/portfolio/exposure`, `/api/briefings/daily/*` 4개, 공개 `/api/etf/{ticker}/holdings`를 넣었다(테스트가 실제 라우트와 대조).
- 테스트: `tests/test_api_contract_round37.py`(DB 없음), `tests/test_api_contract_round37_devdb.py`(개발 DB·CI DB, 가짜 종목만).
  테스트 전체에서 `requests`의 실제 HTTP를 막았다(`tests/conftest.py`의 `block_external_http`): 포트폴리오·주문은 네이버 실시간 시세를 먼저 부르는데,
  이를 가짜로 바꾸지 않은 테스트가 실제로 외부에 요청하고 있었다. 막으면 설계대로 DB 종가 폴백을 쓴다.

### 거래일 달력·저장 전 검증(2026-11-08, 36차)

API 응답 키는 바뀌지 않았다(새 엔드포인트 없음). 바뀐 것은 값의 기준과 일부 답 문장이다.

- **영업일 기준**: `market_index.stale`(위 표), 전망·주가 흐름·단순 시세·주문 참고 시세의 "N영업일 지연", look-through·노출의 `holdings_stale`·`lag`,
  성과 비교의 지연, 브리핑의 `stale`·장중 판정이 모두 KRX 거래일 달력 기준이다. 공휴일 다음 날 정상 데이터가 더 이상 "1영업일 이전/지연"으로 나오지 않는다
  (예: 한글날 다음 월요일 장중의 목요일 종가는 지연 0).
- **새 문장**: 지연 계산에 달력으로 확인할 수 없는 날(달력 범위 밖, 미확정 2026-12-31)이 들어 있으면 단순 시세·전망 답에
  "지연 계산에 KRX 휴장일 달력으로 확인할 수 없는 날 N일이 들어 있어 실제 지연은 이보다 짧을 수 있습니다."(4개 언어)가 붙는다. 화면 처리는 필요 없다(본문 문장).
- **휴장일 하루 질문**: 기간의 행 수(평일 근사)는 그대로다. 마지막 데이터 뒤의 휴장일 하루를 물으면 "아직 수집되지 않았습니다" 대신 "휴장일이라 시세 데이터가 없습니다".
- **데이터 품질**: 수집 데이터는 저장 전에 검증되고 문제 레코드는 격리된다(사용자 API 없음, 운영 CLI `python -m scripts.quarantine`). 격리된 날짜는 저장되지 않으므로
  화면에서는 기존과 같은 "데이터 없음/지연"으로 보인다. 자세한 내용은 `docs/DATA_QUALITY_AND_TRADING_CALENDAR.md`.

### 갱신 스크립트와 기록 테이블

데이터는 `python -m scripts.refresh_data`(backend 디렉터리)로 갱신한다. 프로젝트에 스케줄러는 설치돼 있지 않다: 운영자가 Windows 작업 스케줄러나
cron에 직접 등록한다(등록 방법·권장 시각·종료 코드·상태 조회·복구는 **`docs/DATA_REFRESH_MONITORING.md`**, 2026-11-07). 평일 **15:30(KST) 이후** 실행을 권장한다.
장중에 실행하면 당일 행이 장중 값일 수 있어 경고를 출력하며, 15:30 이후 다시 실행하면 같은 날짜가 종가로 덮어써진다(upsert).

| 옵션 | 설명 |
| --- | --- |
| `--only prices,index,fx,news,embed,content` | 쉼표로 구분한 단계만 실행 |
| `--skip ...` | 같은 형식으로 단계를 제외(`--only`와 함께 쓰면 그 결과에서 뺀다) |
| `--days N` | 주가·지수·환율 조회 기간(달력 일), 기본 10 |
| `--timeout-min N` | 단계별 제한 시간(분), 기본 30. 넘으면 그 단계만 `failed`(`StepTimeout`)로 기록하고 다음 단계로 진행 |
| `--dry-run` | 실제 호출·DB 기록 없이 계획만 출력 |
| `--news-only` | news 단계에서 공시를 수집하지 않는다 |
| `--news-state 파일` | news 단계의 이어하기 기록. 다시 실행하면 끝난 종목을 건너뛴다. 네이버가 401·403·429를 돌려주면 즉시 멈춘다 |
| `--all` | 기본 6단계 전부(옵션 없음과 같다, 2026-11-07) |
| `--source stock_price,etf_price,market_index,exchange_rate,news,dart,embeddings,disclosure_content,etf_holdings` | 데이터 이름으로 단계 선택. `dart`(=`disclosure`)는 news 단계(뉴스와 함께 수집). `kospi200`·`interest_rate`·`overseas_stock`·`earnings`는 사용법 오류(지원 안 함/따로 실행) |
| `--etf-holdings` | 기본 단계 뒤에 ETF 구성종목(`etf_holdings`, KODEX·TIGER)을 더한다. 기본 단계가 아니다 |
| `--retry-failed` | 마지막 시도가 `failed`·`partial`인 단계만 다시 실행(성공한 단계는 다시 받지 않는다) |
| `--step-retries N` | 일시적 오류(timeout·연결·5xx)로 실패한 단계의 재시도 횟수, 기본 1(60초, 120초 … 대기). 401·403·429·형식 오류는 다시 하지 않는다 |
| `--status [--json]` | 수집 없이 소스별 상태 보고서(마지막 실행·마지막 성공·데이터 기준일·지연·품질). 종료 코드 0 정상, 3 확인 필요, 5 DB 오류 |
| `--report-dir 폴더` / `--no-report` | 상태 보고서 `refresh_status.json`·`.md` 저장 폴더(기본 `backend/logs/refresh`, 커밋 안 됨) / 저장 안 함 |
| `--prune-logs [--keep-days 180] [--dry-run]` | 오래된 갱신 기록 정리. 단계별 마지막 성공·마지막 실패는 남긴다. 수집 데이터는 지우지 않는다 |
| `--trigger scheduler` | 실행 기록(`data_refresh_run.trigger`)에 스케줄러 실행이라고 남긴다 |

한 단계가 실패해도 다음 단계로 계속 진행하고, 마지막에 단계별 결과 표를 출력한다. 종료 코드(2026-11-07): 0 전부 성공·품질 error 없음,
1 실패 단계 있음, 2 사용법 오류, 3 실패는 없지만 부분 성공 단계 또는 품질 검사 error, 4 다른 갱신이 실행 중(Postgres advisory lock, 이번 실행은 아무것도 안 함),
5 DB 연결 불가. 종목별 수집 실패가 절반 미만이면 그 단계는 `partial`(2026-11-07 이전에는 경고가 붙은 `success`), 절반 이상이면 `failed`다.
`GET /api/status`의 `last_refresh` 응답 필드는 그대로다(`partial`은 예전처럼 `ok`에 영향을 주지 않는다. 부분 성공은 `--status`에서 본다).

`data_refresh_log` 테이블(마이그레이션 `c5d8f3a07b14`)에는 실행마다 **6단계 모두 한 행씩**(건너뛴 단계는 `skipped`) 기록한다:
`id`, `step`, `status`(`success`/`failed`/`skipped`), `started_at`, `finished_at`, `rows_added`(실행 전후 행 수 차이, 임베딩·본문 단계는 채워진 건수), `latest_date`, `error_type`(예외 클래스 이름만, 메시지는 저장하지 않는다).
2026-11-07(마이그레이션 `b8d4f2a6c913`): `status`에 `partial`, 열 `run_id`(→ `data_refresh_run`), `duration_ms`, `retry_count`, `quality_status`(`ok`/`warning`/`error`),
`warning_count`, `error_code`(`timeout`/`network`/`server_error`/`rate_limited`/`auth`/`bad_data`/`failure_ratio`/`collector_error`/`script_exit`/`http_error`/`unknown`)를 더했다(모두 null 허용, 기존 행 그대로).
`data_refresh_run`은 실행 한 건(`running` → `success`/`partial`/`failed`, 끝나지 못한 실행은 다음 실행이 `aborted`로 바꾼다). 선택 단계 `etf_holdings`는 고른 실행에만 행을 남긴다.
관리자 권한 체계가 없어 상태 조회 API는 새로 만들지 않았다(CLI `--status`만).

---

## GET /api/stocks/{ticker}/event-reactions

뉴스·공시가 나온 시점 이후 주가가 실제로 어떻게 움직였는지를 **코드로 계산**해서 돌려준다(2026-10-14 추가, 응답 추가만이고 기존 API는 그대로).
로그인 없이 호출할 수 있다(시세 데이터와 같은 성격). LLM은 쓰지 않는다. `{ticker}`는 티커 또는 종목명(별칭 포함)이다.

**이 값은 시간적 선후관계일 뿐 인과관계가 아니다.** 같은 기간의 시장 전체 흐름이나 다른 사건이 영향을 줬을 수 있고 표본이 적을 수 있다.
화면에 보여줄 때는 응답의 `disclaimer`를 함께 보여준다(`GET /api/disclaimer`의 서비스 면책 문구와는 별개의 고정 문구다).

```bash
curl "http://localhost:8000/api/stocks/005930/event-reactions?event_type=disclosure&limit=5&since_days=120"
```

| 쿼리 | 기본 | 설명 |
| --- | --- | --- |
| `event_type` | `all` | `disclosure` / `news` / `all` |
| `limit` | 10 | 1~30. **유형별** 최대 건수다(`all`이면 공시 최신 `limit`건 + 뉴스 최신 `limit`건, 합쳐서 최신순) |
| `since_days` | 60 | 1~120. 이 기간 안의 사건만(KST 기준 오늘부터 역산) |

사건 선택: 공시는 접수일 최신순, 뉴스는 발행 시각 최신순이고, **같은 날 같은 제목은 하나만** 남긴다. 뉴스는 기존 수집 규칙(비금융 도메인 제외 등)을
그대로 따르고, 여기에 **종목 관련도 필터**를 더한다: 뉴스 사건은 **제목에 종목 이름(또는 `company_aliases`의 별칭, 예: 삼성전자의 "삼전")이 독립된
이름으로 들어 있는 기사만** 쓴다(ETF 뉴스에 쓰는 이름 판별 `mentions_etf_name`을 그대로 재사용: 앞뒤에 다른 글자가 붙은 "삼성전자우",
더 긴 다른 종목 이름의 일부인 "SK하이닉스"의 "SK"는 아니다). 수집은 종목명 검색이라 시황 기사나 다른 종목 기사가 섞여 있었기 때문이다.
공시는 거르지 않는다. 이 필터는 **이 REST 응답에도 적용된다**(2026-10-14 이전 응답과 달리 뉴스 건수가 줄고 `other_events_in_window`도 필터 후 기준으로
센다; 아직 프론트가 쓰지 않는다). `limit`보다 적게 남는 것은 정상이고, 하나도 없으면 `events`는 빈 배열이다. **ETF는 뉴스만** 대상이고(공시 없음)
초과수익률은 `null`이다.

실제 응답(2026-10-14, 삼성전자 공시 1건만 발췌, `summary`는 뉴스 관련도 필터 적용 후 값으로 이 모양):

```json
{
  "ticker": "005930",
  "company_name": "삼성전자",
  "asset_type": "stock",
  "price_as_of": "2026-10-14",
  "market_index": "KS11",
  "events": [
    {
      "event_type": "disclosure",
      "title": "특수관계인에대한출자",
      "url": "https://dart.fss.or.kr/...",
      "event_at": null,
      "event_date": "2026-07-30",
      "time_known": false,
      "base_date": "2026-07-29",
      "base_close": 208500.0,
      "reaction_start_date": "2026-07-30",
      "returns": {
        "d1":  {"pct": -0.72, "index_pct": -1.23, "excess_pct": 0.51, "available": true, "reason": null},
        "d5":  {"pct": 17.99, "index_pct": 16.51, "excess_pct": 1.48, "available": true, "reason": null},
        "d20": {"pct": 27.58, "index_pct": 22.06, "excess_pct": 5.52, "available": true, "reason": null}
      },
      "volume_ratio": 1.55,
      "volume_sample_days": 20,
      "other_events_in_window": 12,
      "note": null
    }
  ],
  "summary": {
    "event_count": 18,
    "with_d1": 14, "avg_d1_pct": -2.0, "positive_d1_count": 5, "with_excess_d1": 14, "avg_excess_d1_pct": -0.54,
    "with_d5": 10, "avg_d5_pct": -3.0, "positive_d5_count": 1, "with_excess_d5": 10, "avg_excess_d5_pct": -1.56,
    "with_d20": 4, "avg_d20_pct": -1.67, "positive_d20_count": 1, "with_excess_d20": 4, "avg_excess_d20_pct": 0.36
  },
  "disclaimer": "사건 이후의 주가 움직임은 시간적 선후관계일 뿐 ..."
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `price_as_of` | date \| null | 이 종목 주가의 마지막 날짜 |
| `market_index` | string \| null | 비교한 지수(`KS11` 코스피, `KQ11` 코스닥). ETF·시장 구분 없음은 `null` |
| `events[].event_type` | string | `disclosure` / `news` |
| `events[].event_at` | string \| null | 뉴스 발행 시각(KST ISO). 공시는 `null` |
| `events[].event_date` | string \| null | 시각을 모르는 사건의 날짜(공시 접수일). `event_at`이 있으면 `null`(둘 중 정확히 하나만 값이 있다) |
| `events[].time_known` | boolean | 시각을 아는 사건인지(공시는 항상 `false`) |
| `events[].base_date`, `base_close` | date, number \| null | 기준일과 그날 종가. 시세 데이터 시작 전이면 `null` |
| `events[].reaction_start_date` | date \| null | 반응 시작일(1일째). 다음 거래일이 아직 없으면 `null` |
| `events[].returns.d1/d5/d20.pct` | number \| null | 기준 종가 대비 N일째 종가 수익률(%). 아직 N거래일이 안 지났으면 `null` |
| `...index_pct`, `excess_pct` | number \| null | 같은 구간 시장 지수 수익률(%)과 초과수익률(종목 - 지수, %p) |
| `...available` | boolean | `pct`가 계산됐는지(`pct != null`과 같다) |
| `...reason` | string \| null | `pct`를 못 구한 사유("20거래일 경과 전" 등). `pct`는 있는데 지수/초과수익률이 없을 때는 그 사유(ETF 등) |
| `events[].volume_ratio` | number \| null | 반응 시작일 거래량 / 기준일까지 최근 20거래일 평균 거래량 |
| `events[].volume_sample_days` | int | 위 평균에 쓴 일수(20일치가 안 되면 있는 만큼, 최대 20) |
| `events[].other_events_in_window` | int | 같은 종목의 **다른 사건**(공시·뉴스 모두, 유형 필터·`limit`과 무관하게 `since_days` 안 전체) 중 반응 시작일이 이 사건의 반응 구간(1일째~20일째, 또는 마지막 거래일)에 드는 건수 |
| `events[].note` | string \| null | 가격 조정(분할 등) 가능성 경고 등 |
| `summary` | object | 사건 수와 지표별 평균. 평균은 `available`인 사건만 대상이고 표본 수(`with_dN`)를 같이 준다. 해당 사건이 없으면 평균은 `null` |
| `disclaimer` | string | 이 API 전용 고정 면책 문구 |

### 계산 정의

1. **거래일 달력**: 그 종목의 주가(`stock_price`) 날짜다. 휴장일·주말은 달력에 없는 날이다(추석·대체공휴일 포함, 별도 휴장일 목록은 없다).
2. **기준 종가와 반응 시작일**
   - 시각을 아는 사건(뉴스): 거래일 **15:30(KST) 이후** 발행이면 기준일 = 그날, 반응 시작일 = 다음 거래일. 거래일 **장 시작 전·장중**(15:30 전) 발행이면 기준일 = 직전 거래일, 반응 시작일 = 그날. **휴장일**(주말 포함) 발행이면 기준일 = 직전 거래일, 반응 시작일 = 다음 거래일.
   - 날짜만 아는 사건(공시): 시각을 알 수 없으므로 기준일 = 직전 거래일, 반응 시작일 = 접수일(접수일이 휴장일이면 다음 거래일), `time_known=false`. 장 마감 후 접수된 공시도 접수일 반응으로 세므로 반응이 하루 빠르게 잡힐 수 있다.
   - 뉴스의 발행 시각이 정확히 00:00:00이면 시각 없이 날짜만 들어온 것으로 보고 공시처럼 다룬다(`time_known=false`).
3. **N거래일 수익률**(N = 1, 5, 20): 반응 시작일을 1일째로 센 N일째 종가 / 기준 종가 - 1 (%).
4. **초과수익률**: 같은 기준일과 같은 N일째 날짜의 시장 지수(`company.market`이 KOSPI면 KS11, KOSDAQ·KOSDAQ GLOBAL이면 KQ11) 수익률을 같은 방식으로 구해 `종목 - 지수`(%p). ETF나 시장 구분이 없는 종목, 그 날짜의 지수 값이 없는 경우는 `null`(`reason`에 사유).
5. **거래량 비율**: 반응 시작일 거래량 / 기준일을 포함한 최근 20거래일 평균 거래량. 20일치가 안 되면 있는 만큼으로 계산하고 `volume_sample_days`로 알려 준다.
6. **가격 조정 경고**: 기준일부터 20일째(또는 마지막 거래일)까지 종가 기준 일간 등락률 절댓값이 30%를 넘는 날이 있으면 `note`에 "가격 조정(분할 등) 가능성"을 넣는다. 수정주가 여부는 알 수 없다.
7. 모든 숫자는 소수 둘째 자리 반올림이다.

### 한계

- **인과관계가 아니다.** 사건 뒤의 주가는 시장 전체·업종·다른 뉴스의 영향을 함께 받는다. `excess_pct`(지수 대비)와 `other_events_in_window`(같은 구간의 다른 사건 수)는 이를 가늠하는 참고 수치일 뿐 영향을 분리해 주지는 않는다.
- **공시는 접수일만 있고 시각이 없다.** 장중/장후 구분이 안 되어 반응이 접수일에 잡힌다.
- **달력은 종목 주가 기준**이다. 주가 수집이 안 된 날은 거래일로 보지 않는다(2026-10-14 현재 주식은 2026-06-26부터, ETF는 2026-07-16부터 시세가 있어, 그 이전 사건은 `base_close`가 `null`이다).
- **표본이 적다.** 뉴스는 종목별 최신 기사 위주로 수집되어 오래된 뉴스가 드물고(삼성전자 뉴스는 2026-09-24 이후만 있다), 최근 사건은 5·20거래일이 아직 안 지나 `null`이다. `summary.with_dN`을 꼭 같이 보여 준다.
- 뉴스는 제목·본문에 그 종목이 직접 나오는지만 보는 기존 수집 규칙을 따르므로, 종목과 무관한 기사가 섞여 있을 수 있다.
- 지수는 코스피·코스닥 둘뿐이고, 환율·업종 지수 등은 반영하지 않는다.

| 상태 | 원인 | 응답 |
| --- | --- | --- |
| 404 | 종목을 찾지 못함 | `{"detail": "..."}` (다른 종목 API와 같은 메시지 형식) |
| 422 | `event_type`/`limit`/`since_days` 범위 오류 | 검증 오류 형식 |
| 503 | DB 연결 불가 | 다른 API와 같음 |

---

## GET /api/stocks/{ticker}/price-history

종목 상세 화면의 주가 추이 차트용 일별 시세(2026-10-18 추가, 응답 추가만이고 기존 API는 그대로). 로그인 없이 호출할 수 있다.
`{ticker}`는 티커 또는 종목명(별칭 포함)이고, 주식과 ETF 모두 같은 형태로 돌려준다. 읽기 전용이고 LLM은 쓰지 않는다.

```bash
curl "http://localhost:8000/api/stocks/005930/price-history?days=90"
```

| 쿼리 | 기본 | 설명 |
| --- | --- | --- |
| `days` | 90 | 정수 1~365(2026-11-10에 상한 90에서 365로 확장). **달력 일이 아니라 최근 N거래일**이다(그 종목의 저장된 일별 주가 행 수 기준, 주말·휴장일은 없는 날). 범위를 벗어나면 422 |

실제 응답(2026-10-18 조회, 삼성전자 `days=90`, `items` 73개 중 앞 2개와 뒤 1개만 발췌):

```json
{
  "ticker": "005930",
  "company_name": "삼성전자",
  "asset_type": "stock",
  "days": 90,
  "count": 73,
  "price_as_of": "2026-10-14",
  "items": [
    {"date": "2026-06-26", "close": 339500.0, "volume": 39735951, "change_pct": -5.3, "open": 354000.0, "high": 356500.0, "low": 321500.0},
    {"date": "2026-06-29", "close": 323000.0, "volume": 35436521, "change_pct": -4.86, "open": 331000.0, "high": 335500.0, "low": 316000.0},
    "...",
    {"date": "2026-10-14", "close": 235000.0, "volume": 17016990, "change_pct": 0.43, "open": 233500.0, "high": 241500.0, "low": 230000.0}
  ]
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `ticker`, `company_name` | string | 확인된 종목의 티커와 이름 |
| `asset_type` | string | `stock` / `etf` |
| `days` | int | 요청한 거래일 수(요청값 그대로, 데이터가 모자라도 바뀌지 않는다) |
| `count` | int | 실제로 들어 있는 `items` 개수(`<= days`). 데이터가 `days`보다 적으면 `count < days`이고 에러가 아니다 |
| `price_as_of` | date \| null | `items` 마지막 날짜(가장 최근 거래일). 주가가 없으면 `null` |
| `items[].date` | date | 거래일(`YYYY-MM-DD`). **날짜 오름차순**(과거 -> 최근)이라 차트에 그대로 그리면 된다 |
| `items[].close` | number | 종가 |
| `items[].volume` | int \| null | 거래량(주) |
| `items[].change_pct` | number \| null | 전일 대비 등락률(%) |
| `items[].open`, `high`, `low` | number \| null | 시가, 고가, 저가 |
| `available_count` | int | (2026-11-10 추가) 이 종목의 저장된 주가 행 **전체** 수. `days`와 무관하다. 180·365거래일 버튼을 켤지 정할 때 쓴다(`available_count < 365`면 365는 모자란다) |
| `first_date` | date \| null | (2026-11-10 추가) 이 종목의 가장 이른 저장 날짜. 주가가 없으면 `null` |
| `missing_trading_days` | int | (2026-11-10 추가) `items` 첫날~끝날 사이에서 KRX 거래일 달력이 거래일로 확정한 날인데 행이 없는 날 수. 휴장일·주말은 세지 않는다. 0이 아니면 그 사이 수집 공백이 있다 |

값이 없는 칸은 `null`이다(필드는 항상 있다). 숫자는 DB에 저장된 값 그대로다.

**2026-11-10(41차) 확장:** `days` 상한 90은 성능 제한이 아니라 시세가 2026-06-26부터만 있던 때의 데이터 한계였다. 지금은 2023-10-30부터
733거래일이 있고 350종목 중 342종목이 365행 이상이다. 같은 `(company_id, price_date)` 인덱스로 최근 N행만 읽고 창 함수로 전체 행 수를 같이
세서 쿼리 수는 그대로 2개(종목 해석 + 시세)다. dev DB 측정: 삼성전자 `days=365` 약 46~59ms·약 44KB, `days=90` 약 14ms·11KB.
기본값(90)과 기존 필드·순서는 그대로라 기존 20·60·90 호출의 `items`는 바뀌지 않는다. 실제 응답(2026-11-10, dev DB):

| 요청 | `count` | `available_count` | `first_date` | `missing_trading_days` |
| --- | --- | --- | --- | --- |
| 삼성전자 `days=365` | 365 | 733 | 2023-10-30 | 0 |
| 에임드바이오(0009K0) `days=365` | 222 | 222 | 2025-12-04 | 0 |

한계:

- **데이터 시작일이 있다.** 2026-10-29 백필 뒤 대부분 종목은 2023-10-30부터 시세가 있지만, 최근 상장 종목은 더 짧다(2026-11-10 dev DB:
  350종목 중 8종목이 365행 미만, 가장 짧은 종목 52행). 화면에는 `count`를 기준으로 그리고 `days`개를 가정하지 않는다.
  (2026-10-18 당시에는 주식 2026-06-26, ETF 2026-07-16부터라 삼성전자 73개였다.)
- **일별 저장 데이터**라 장중 시세가 아니다. 마지막 날짜는 `price_as_of`이고, 오늘 현재가는 `GET /api/stocks/{ticker}/realtime-price`를 쓴다.
- **종가는 수집 소스(FinanceDataReader)가 준 값 그대로이고, 그 소스가 수정주가로 준 구간이 섞여 있다.** 백엔드는 따로 보정하지 않지만
  "보정하지 않은 원 종가"라고 단정할 수 없다. 2026-11-10 dev DB에서 5만 원 이상 주식 종가 중 100원 단위가 아닌(호가 단위에 맞지 않는) 행이
  2023~2026년 모두 있다(예: 삼성바이오로직스 2026-10-01 1,417,007원). 행마다 수정 여부를 저장하지 않아 어느 행이 수정값인지 구분할 수 없다.
  저장된 등락률(`change_pct`)은 연속한 종가로 계산한 값과 0.6%p 넘게 다른 행이 없었다(248,655행). 화면 문구는 "수집 소스의 종가"로 쓰는 것이 맞다.
- 주가 행이 아직 없는 종목은 200에 `items=[]`, `count=0`, `price_as_of=null`이다.

| 상태 | 원인 | 응답 |
| --- | --- | --- |
| 404 | 종목을 찾지 못함 | `{"detail": "..."}` (다른 종목 API와 같은 메시지 형식) |
| 422 | `days`가 정수가 아니거나 1~365 밖 | 검증 오류 형식 |
| 503 | DB 연결 불가 | 다른 API와 같음 |

---

## GET /api/market/overview

시장 요약(2026-11-10, 41차). 로그인 없이 호출한다. 지금까지 시장 지수는 일일 브리핑(로그인 + 오늘 브리핑 생성)의 `market` 영역에만 있어서
브리핑이 없으면 리서치 홈의 "오늘의 시장"이 비었다. 이 API는 같은 저장 데이터를 브리핑 없이 읽는다. 읽기 전용(쿼리 4개), LLM 없음, 파라미터 없음.
계산 코드는 `app/market_snapshot.py`.

```bash
curl http://localhost:8000/api/market/overview
```

실제 응답(2026-11-10 16:55 KST, dev DB. `by_market`의 KOSDAQ과 `prices` 기록은 줄였다):

```json
{
  "as_of": "2026-11-03",
  "expected_date": "2026-11-10",
  "generated_at": "2026-11-10T16:55:58.354334+09:00",
  "status": "ok",
  "missing": [],
  "freshness": {"status": "delayed", "lag_business_days": 5, "calendar_unknown_days": 0, "intraday_excluded": false, "dates_differ": false},
  "indices": [
    {"code": "KS11", "name": "코스피", "date": "2026-11-03", "close": 5637.63, "previous_date": "2026-11-02", "previous_close": 5859.08,
     "change": -221.45, "change_pct": -3.78, "change_unavailable_reason": null,
     "first_collected_at": "2026-11-03T19:48:21.263317+09:00", "lag_business_days": 5},
    {"code": "KQ11", "name": "코스닥", "date": "2026-11-03", "close": 704.79, "previous_date": "2026-11-02", "previous_close": 734.36,
     "change": -29.57, "change_pct": -4.03, "change_unavailable_reason": null,
     "first_collected_at": "2026-11-03T19:48:21.307255+09:00", "lag_business_days": 5}
  ],
  "breadth": {
    "date": "2026-11-03", "universe": "collected_stocks", "lag_business_days": 5,
    "total": 300, "up": 67, "down": 230, "flat": 3, "change_missing": 0, "no_price": 0, "registered": 300,
    "up_ratio_pct": 22.3, "down_ratio_pct": 76.7, "flat_ratio_pct": 1.0,
    "by_market": {"KOSPI": {"total": 219, "up": 63, "down": 153, "flat": 3, "change_missing": 0, "no_price": 0, "registered": 219,
                            "up_ratio_pct": 28.8, "down_ratio_pct": 69.9, "flat_ratio_pct": 1.4}, "KOSDAQ": {"...": "..."}}
  },
  "last_collection": {"index": {"step": "index", "status": "success", "ok": true, "finished_at": "2026-11-03T19:48:22.059546+09:00", "error_code": null},
                      "prices": {"...": "..."}},
  "source": {"provider": "FinanceDataReader", "tables": ["market_index", "stock_price"], "per_row_source_recorded": false},
  "unavailable": ["trading_value", "market_volume"],
  "note": "저장된 일별 종가 기준이며 실시간 시세가 아닙니다. 상승·하락 종목 수는 수집 대상 종목만 센 값입니다."
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `as_of` | date \| null | 지수 기준일(두 지수 날짜가 다르면 더 이른 날). 지수가 하나도 없으면 `null` |
| `expected_date` | date | 지금 종가가 있어야 할 가장 최근 KRX 거래일(장중이면 직전 거래일, 휴장일이면 그 앞 거래일) |
| `generated_at` | string | 응답을 만든 시각(ISO 8601, KST) |
| `status` | `ok` \| `partial` \| `no_data` | `partial`: 지수 하나나 상승·하락 집계가 없다. `no_data`: 지수도 집계도 없다(그래도 200) |
| `missing` | string[] | 없는 부분: `KS11`, `KQ11`, `breadth` |
| `freshness.status` | `latest` \| `delayed` \| `stale` \| `no_data` | 기대 거래일 대비 늦은 거래일 수로 0이면 latest, 1~5 delayed, 6 이상 stale. **일일 브리핑 `freshness.status`와 같은 기준**이라 같은 지연 띠를 쓰면 된다 |
| `freshness.lag_business_days` | int \| null | 지수·상승하락 중 가장 늦은 것(KRX 거래일 수, 휴장일 제외) |
| `freshness.calendar_unknown_days` | int | 지연에 넣은 날 중 KRX 달력으로 확인할 수 없는 날(달력 범위 밖·미확정) 수 |
| `freshness.intraday_excluded` | bool | 지금 장중(거래일 15:30 전)이라 오늘 날짜 행을 쓰지 않았다(장중 값은 종가가 아니다) |
| `freshness.dates_differ` | bool | 지수끼리 또는 지수와 상승·하락의 기준일이 다르다 |
| `indices[]` | 2개, 항상 `KS11`, `KQ11` 순서 | 데이터가 없는 지수도 항목은 있고 값이 `null`이다 |
| `indices[].date`, `close` | date, number | 그 지수의 마지막 저장 종가와 날짜 |
| `indices[].previous_date`, `previous_close` | date, number | 비교에 쓴 직전 저장 행 |
| `indices[].change`, `change_pct` | number \| null | 등락폭(포인트)·등락률(%), 소수 둘째 자리. **직전 저장 행이 직전 KRX 거래일일 때만** 계산한다 |
| `indices[].change_unavailable_reason` | `no_previous_row` \| `previous_trading_day_missing` \| null | 등락을 비운 이유. 수집 공백으로 직전 거래일 행이 없으면 여러 날의 변화를 "전일 대비"라고 부르지 않는다 |
| `indices[].first_collected_at` | string \| null | 그 행을 처음 저장한 시각(KST). 기준일(`date`)과 다르다 |
| `indices[].lag_business_days` | int | 그 지수의 기대 거래일 대비 지연 |
| `breadth` | object \| null | 장중 행을 뺀 가장 최근 주식 시세 날짜의 상승·하락·보합. 주식 시세가 없으면 `null` |
| `breadth.universe` | `collected_stocks` | **시장 전체가 아니라 수집 대상(시가총액 상위) 주식만**, ETF 제외 |
| `breadth.total` | int | 그날 시세 행이 있는 주식 수(= `up + down + flat + change_missing`) |
| `breadth.change_missing` | int | 시세는 있지만 등락률이 비어 있는 종목(상승·하락·보합에 넣지 않는다) |
| `breadth.no_price` | int | 등록된 주식 중 그날 시세 행이 없는 종목(`total`에 들지 않는다) |
| `breadth.up_ratio_pct` 등 | number \| null | `up / (up + down + flat) x 100`(소수 첫째 자리). 분모가 0이면 `null` |
| `breadth.by_market` | object | `KOSPI`, `KOSDAQ`(코스닥 글로벌 포함), 시장이 비어 있는 주식이 있으면 `OTHER`. 각 값은 위와 같은 개수·비율 |
| `last_collection.index`, `.prices` | object \| null | `refresh_data`의 지수·시세 단계의 마지막 실제 시도(`skipped` 제외): `step`, `status`(`success`/`partial`/`failed`), `ok`, `finished_at`(KST), `error_code`. 기록이 없으면 `null`. **지연·결측이 수집 실패 때문인지** 구분하는 용도이고 숫자는 바꾸지 않는다. 수동 수집 스크립트는 이 기록을 남기지 않는다 |
| `unavailable` | string[] | 저장하지 않아 제공하지 않는 지표. `trading_value`(거래대금), `market_volume`(시장 전체 거래량). 값을 만들지 않는다 |
| `source.per_row_source_recorded` | bool | `false`: 지수 수집은 FinanceDataReader 폴백 체인(별칭·Yahoo 심볼)을 쓰지만 행마다 어느 소스였는지 저장하지 않는다 |

화면에서: `status`가 `no_data`면 "시장 데이터가 아직 없어요", `freshness.status`가 `delayed`·`stale`이면 브리핑과 같은 지연 띠,
`change`가 `null`이면 등락 자리에 "—"와 이유를 보여준다. 0%로 그리지 않는다. 상승·하락 수 옆에는 "수집 대상 N종목 기준"을 같이 적는다.

| 상태 | 원인 | 응답 |
| --- | --- | --- |
| 200 | 데이터가 없어도 | `status: "no_data"` |
| 503 | DB 연결 불가 | 다른 API와 같음 |

---

## GET /api/stocks/{ticker}/news

종목 뉴스 목록(2026-11-10, 41차). 로그인 없이 호출한다. 저장된 기사를 페이지로 나눠 줄 뿐 새로 요약하거나 계산하지 않는다(`app/stock_feed.py`).
종목 해석은 **엄격 모드**(`allow_fuzzy=False`, 주문과 같다): 종목코드·정확한 종목명·공백/대소문자 차이·등록된 별칭만 받는다. 비슷한 이름으로
다른 종목의 뉴스를 보여주지 않으며, 못 찾으면 404 + 후보 안내 문장이다(예: `{"detail": "정확한 종목을 찾지 못했습니다. 혹시 삼성전자인가요?"}`).

```bash
curl "http://localhost:8000/api/stocks/005930/news?limit=20&offset=0"
```

| 쿼리 | 기본 | 설명 |
| --- | --- | --- |
| `limit` | 20 | 1~50. 밖이면 422 |
| `offset` | 0 | 0 이상. 밖이면 422 |
| `date_from`, `date_to` | 없음 | `YYYY-MM-DD`, **KST 달력 날짜, 양끝 포함**(게시 시각 기준). `date_from > date_to`면 422(`loc: ["query", "date_from"]`, 기록 API와 같은 형식) |
| `order` | `desc` | `desc`(최신순)·`asc`. 같은 시각이면 `id`가 보조 키다 |

실제 응답(2026-11-10, dev DB, 삼성전자 `limit=3`, `items` 앞 1개만):

```json
{
  "ticker": "005930", "company_name": "삼성전자", "asset_type": "stock",
  "total": 60, "limit": 3, "offset": 0, "has_next": true, "total_pages": 20,
  "filters": {"date_from": null, "date_to": null, "order": "desc"},
  "freshness": {"last_collected_at": "2026-11-03T19:54:42.431681+09:00", "collection_lag_weekdays": 5, "stale": true,
                "company_latest_at": "2026-11-03T19:48:00+09:00",
                "last_collection": {"step": "news", "status": "success", "ok": true, "finished_at": "2026-11-03T19:54:42.667374+09:00", "error_code": null}},
  "items": [
    {"id": 16494, "title": "화성특례시, 서울서 AI 엑스포 'MARS 2026' 개막… 62개 기업·기관 참여",
     "summary": "시에 따르면 화성에는 삼성전자와 현대차·기아 남양연구소, 제조업체 2만6000여 곳이 자리하고 있다. ...",
     "source": "www.thereport.co.kr", "url": "https://www.thereport.co.kr/news/articleView.html?idxno=93012",
     "published_at": "2026-11-03T19:48:00+09:00", "collected_at": "2026-11-03T19:48:22.921852+09:00",
     "is_relevant": true, "related_tickers": ["000270", "004170", "005380", "069620"]}
  ]
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `total`, `limit`, `offset`, `has_next`, `total_pages` | int, int, int, bool, int | 목록 API 공통 규칙과 같다(`has_next = offset + limit < total`, `total`이 0이면 `total_pages` 0) |
| `filters` | object | 적용한 `date_from`·`date_to`·`order`(요청값 그대로) |
| `freshness.last_collected_at` | string \| null | **뉴스 전체**의 마지막 저장 시각(KST). 수집이 멈췄는지 보는 값 |
| `freshness.collection_lag_weekdays` | int \| null | 그 날짜가 최근 평일보다 늦은 평일 수. 뉴스·공시는 KRX 휴장과 무관해 평일 기준이다 |
| `freshness.stale` | bool | `collection_lag_weekdays >= 2`이거나 수집 기록이 없으면 `true` |
| `freshness.company_latest_at` | string \| null | 이 종목(목록 조건)의 가장 최근 게시 시각. 오래돼도 수집 지연이 아닐 수 있다(기사가 원래 없을 수 있다) |
| `freshness.last_collection` | object \| null | `refresh_data` 뉴스·공시 단계(`news`)의 마지막 실제 시도(시장 요약의 `last_collection`과 같은 모양) |
| `items[].id` | int | 뉴스 행 id |
| `items[].title`, `url`, `source` | string, string \| null, string \| null | 제목, 원문 URL, 언론사 도메인(예: `www.mk.co.kr`) |
| `items[].summary` | string \| null | 수집 때 네이버 뉴스 검색 API가 준 기사 앞부분 발췌. **우리가 만든 요약이 아니다**. 비어 있으면 `null` |
| `items[].published_at` | string \| null | 기사 게시 시각(ISO 8601, KST) |
| `items[].collected_at` | string \| null | 이 행을 처음 저장한 시각(KST) |
| `items[].is_relevant` | bool \| null | 수집 때 저장한 종목 관련성 판정(`true` 또는 판정 없음 `null`). `false`인 기사는 목록에서 뺀다 |
| `items[].related_tickers` | string[] | 같은 URL이 연결된 **다른** 종목 코드(오름차순). 같은 기사가 여러 종목에 연결되는 것은 정상이다 |

목록 규칙:

- **관련성:** 다른 모든 뉴스 경로(브리핑·사건 반응·리서치 도구)와 같이 `is_relevant = false` 기사를 뺀다. 수집 단계가 종목명으로 검색해 온 기사라
  제목에 종목명이 없는 시황 기사가 섞일 수 있다(사건 반응 API는 여기에 "제목에 종목명" 조건을 더 건다. 이 목록은 그 조건을 걸지 않는다).
- **중복:** 같은 종목에 같은 URL이 두 번 저장됐으면(유니크 제약이 없다, `docs/DATA_QUALITY_AND_TRADING_CALENDAR.md`) `id`가 가장 큰 행 하나만 보인다.
  다른 종목에 연결된 같은 URL 행은 지우거나 숨기지 않는다. 제목이 비슷한 다른 언론사 기사는 묶지 않는다(일일 브리핑은 묶는다).
- **빈 결과:** 200 + `items: []`, `total: 0`. `freshness.last_collection.status`가 `failed`면 수집 실패, `success`인데 비었으면 저장된 기사가 원래 없는 것이다.

| 상태 | 원인 | 응답 |
| --- | --- | --- |
| 404 | 종목을 찾지 못함(엄격 모드) | `{"detail": "..."}` 후보 안내 문장 |
| 422 | `limit`·`offset`·`order`·날짜 형식이 틀림, `date_from > date_to` | 검증 오류 형식(`loc[0] = "query"`) |
| 503 | DB 연결 불가 | 다른 API와 같음 |

쿼리 수: 종목 해석 1 + 건수·페이지·같은 URL의 다른 종목·신선도·수집 시도 5 = 6개(페이지 크기와 무관). dev DB 측정 `limit=50` 약 27ms·33KB.

---

## GET /api/stocks/{ticker}/disclosures

종목 DART 공시 목록(2026-11-10, 41차). 로그인 없이 호출한다. 종목 해석·페이지·날짜 필터·`order`·오류는 위 뉴스 목록과 같다.
`date_from`·`date_to`는 접수일(KST) 기준이다.

| 쿼리 | 기본 | 설명 |
| --- | --- | --- |
| `limit`, `offset`, `date_from`, `date_to`, `order` | | 뉴스 목록과 같다 |
| `correction` | `all` | `all`(전부)·`only`(정정 공시만)·`exclude`(정정 공시 제외). 그 밖의 값은 422 |

실제 응답(2026-11-10, dev DB, 삼성전자 `correction=only&limit=3`, `items` 앞 1개만):

```json
{
  "ticker": "005930", "company_name": "삼성전자", "asset_type": "stock",
  "total": 3, "limit": 3, "offset": 0, "has_next": false, "total_pages": 1,
  "filters": {"date_from": null, "date_to": null, "order": "desc", "correction": "only"},
  "supported": true, "message": null,
  "freshness": {"last_collected_at": "2026-11-03T19:53:51.961770+09:00", "collection_lag_weekdays": 5, "stale": true,
                "company_latest_at": "2026-10-30T00:00:00+09:00", "last_collection": {"step": "news", "status": "success", "...": "..."}},
  "items": [
    {"id": 8071, "title": "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)",
     "disclosure_type": "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)", "rcept_no": "20261029800099",
     "disclosed_date": "2026-10-29", "disclosed_at": "2026-10-29T00:00:00+09:00",
     "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20261029800099",
     "is_correction": true, "collected_at": "2026-10-31T12:31:36.764855+09:00"}
  ]
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `supported` | bool | ETF는 DART 공시 수집 대상이 아니라 `false`(200, `items: []`, `total: 0`, `message`에 안내). 주식은 `true` |
| `message` | string \| null | `supported=false`일 때 안내 문장 |
| `freshness` | object | 뉴스 목록과 같은 모양. `last_collected_at`은 **공시 전체**의 마지막 저장 시각, `company_latest_at`은 이 종목 최근 접수일 00:00 KST |
| `items[].disclosure_type` | string \| null | DART 보고서명(정정 접두어 포함 원문). 제목과 같은 값이 저장돼 있다 |
| `items[].rcept_no` | string \| null | DART 접수번호(14자리). 원문 URL에서 읽는다 |
| `items[].disclosed_date` | date \| null | 접수일(KST). **시각은 저장하지 않는다** |
| `items[].disclosed_at` | string \| null | 접수일 00:00 KST(`2026-10-29T00:00:00+09:00`). 일일 브리핑의 공시 시각과 같은 표기다. 시각으로 보여주지 말 것 |
| `items[].source_url` | string \| null | DART 원문 뷰어 URL |
| `items[].is_correction` | bool | `[기재정정]`·`[첨부정정]`·`[첨부추가]`·`[발행조건확정]` 등 정정·추가 공시(`app.ingest_validation.is_correction`). 원본과 접수번호가 다른 **별개 공시**라 중복으로 지우지 않는다 |
| `items[].collected_at` | string \| null | 이 행을 처음 저장한 시각(KST) |

중복: 같은 종목에 같은 원문 URL(같은 접수번호)이 두 번 저장됐으면 `id`가 큰 행 하나만 보인다. 쿼리 수: 종목 해석 1 + 4 = 5개(ETF는 1 + 2).

---

## GET /api/disclaimer

서비스 면책 문구. 로그인 없이 호출할 수 있다. 앱 하단/푸터나 약관 화면에 그대로 보여주면 된다.

```bash
curl http://localhost:8000/api/disclaimer
```

```json
{
  "text": "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
}
```

같은 문구가 `POST /api/research`와 `GET /api/research/{report_id}` 응답의 `disclaimer` 필드에도 들어간다(목록 응답에는 없음).
기존 필드는 그대로이고 `disclaimer`만 추가됐다.

---

## GET /api/briefing

로그인 사용자의 **보유종목(모의투자)과 관심종목**으로 만드는 "오늘의 개인화 브리핑". **LLM을 쓰지 않는다** - 응답의 모든 숫자(종가, 등락률,
거래량 배수, 평가금액, 비중, 손익)와 문장을 서버 코드가 DB 값으로 계산하고, 문장은 관찰한 사실만 쓴다(투자 권유 표현 없음, 응답에 면책 문구 포함).
**읽기 전용**이다: `GET /api/portfolio`와 달리 계좌가 없는 사용자에게 계좌를 만들지 않는다. 외부 네트워크 호출이 없고(시세는 DB의 일별 종가,
실시간 아님) 쿼리 수는 종목 수와 무관하게 고정(최대 9개)이다.

```bash
curl "http://localhost:8000/api/briefing?news_limit=3" -H "Authorization: Bearer $TOKEN"
```

### 요청

| 쿼리 | 타입 | 기본 | 범위 | 설명 |
| --- | --- | --- | --- | --- |
| `news_limit` | int | 3 | 1~5 | 종목당 뉴스 수(최근 7일, 최신순, 중복 제거). 범위 밖이거나 정수가 아니면 **422** |

### 응답 200

아래는 실제 개발 DB의 종목으로 만든 응답이다(테스트 계정, 삼성전자 10주 + KODEX 200 20주 보유, SK하이닉스·KODEX 레버리지 관심종목).
지면 때문에 `holdings`/`watchlist`/`weights`/`highlights`/`news`/`disclosures`는 일부만 남겼다(실제 응답은 전부 담긴다).
생성 시각이 장중(10:21)이라 오늘(10-28) 행을 쓰지 않고 직전 거래일(10-27) 종가 기준이다("결손 처리와 데이터 기준"의 장중 규칙 참고).

```json
{
  "generated_at": "2026-10-28T10:21:42.183599+09:00",
  "data_date": "2026-10-27",
  "stale": false,
  "stale_notice": null,
  "message": null,
  "market": {
    "kospi": {
      "code": "KS11",
      "name": "코스피",
      "date": "2026-10-27",
      "close": 5638.34,
      "change_pct": 3.09
    },
    "kosdaq": {
      "code": "KQ11",
      "name": "코스닥",
      "date": "2026-10-27",
      "close": 743.83,
      "change_pct": 2.29
    },
    "usd_krw": {
      "code": "USD/KRW",
      "name": "원/달러",
      "date": "2026-10-27",
      "close": 1324.77,
      "change_pct": 0.23
    }
  },
  "portfolio": {
    "has_account": true,
    "initial_balance": 10000000.0,
    "cash_balance": 5971900.0,
    "holdings_value": 4028100.0,
    "total_asset": 10000000.0,
    "profit_loss": 0.0,
    "profit_loss_pct": 0.0,
    "day_profit_loss": 173200.0,
    "top_weight": {
      "ticker": "005930",
      "name": "삼성전자",
      "weight_pct": 22.5
    },
    "weights": [
      {
        "ticker": "005930",
        "name": "삼성전자",
        "weight_pct": 22.5
      },
      {
        "ticker": "069500",
        "name": "KODEX 200",
        "weight_pct": 17.78
      }
    ],
    "message": null
  },
  "holdings": [
    {
      "ticker": "005930",
      "name": "삼성전자",
      "asset_type": "stock",
      "data_available": true,
      "latest_close": 225000.0,
      "latest_date": "2026-10-27",
      "day_change_pct": 5.39,
      "week_change_pct": 7.66,
      "volume": 14196879,
      "avg_volume_20d": 17245003.7,
      "volume_ratio": 0.82,
      "news": [
        {
          "title": "삼성, 갤럭시 워치·링 '수면 무호흡' 고도화…호흡 장애 3단계 분석",
          "source": "www.etnews.com",
          "published_at": "2026-10-28T10:04:00+09:00",
          "url": "https://n.news.naver.com/mnews/article/030/0003479457?sid=105"
        }
      ],
      "disclosures": [],
      "disclosure_count": 0,
      "notice": null,
      "flags": [
        {
          "type": "day_move",
          "scope": "holding",
          "ticker": "005930",
          "name": "삼성전자",
          "text": "삼성전자의 2026-10-27 종가는 전 거래일 대비 +5.39% 상승했습니다.",
          "value": 5.39,
          "threshold": 3.0,
          "unit": "%",
          "date": "2026-10-27",
          "direction": "up"
        },
        {
          "type": "week_move",
          "scope": "holding",
          "ticker": "005930",
          "name": "삼성전자",
          "text": "삼성전자의 종가는 최근 5거래일간 +7.66% 상승했습니다.",
          "value": 7.66,
          "threshold": 7.0,
          "unit": "%",
          "date": "2026-10-27",
          "direction": "up"
        }
      ],
      "quantity": 10,
      "avg_price": 225000.0,
      "eval_amount": 2250000.0,
      "weight_pct": 22.5,
      "profit_loss": 0.0,
      "profit_loss_pct": 0.0,
      "day_profit_loss": 115000.0
    }
  ],
  "watchlist": [
    {
      "ticker": "000660",
      "name": "SK하이닉스",
      "asset_type": "stock",
      "data_available": true,
      "latest_close": 1374000.0,
      "latest_date": "2026-10-27",
      "day_change_pct": 6.51,
      "week_change_pct": 15.46,
      "volume": 3136495,
      "avg_volume_20d": 3216308.5,
      "volume_ratio": 0.98,
      "news": [
        {
          "title": "이천신산업진흥원 설립안, 경기도 심의 통과",
          "source": "www.sedaily.com",
          "published_at": "2026-10-28T10:06:00+09:00",
          "url": "https://n.news.naver.com/mnews/article/011/0004675982?sid=102"
        }
      ],
      "disclosures": [
        {
          "title": "수시공시의무관련사항(공정공시)",
          "disclosure_type": "수시공시의무관련사항(공정공시)",
          "disclosed_at": "2026-10-28T00:00:00+09:00",
          "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20261028800040"
        }
      ],
      "disclosure_count": 10,
      "notice": null,
      "flags": [
        {
          "type": "day_move",
          "scope": "watchlist",
          "ticker": "000660",
          "name": "SK하이닉스",
          "text": "SK하이닉스의 2026-10-27 종가는 전 거래일 대비 +6.51% 상승했습니다.",
          "value": 6.51,
          "threshold": 3.0,
          "unit": "%",
          "date": "2026-10-27",
          "direction": "up"
        },
        {
          "type": "week_move",
          "scope": "watchlist",
          "ticker": "000660",
          "name": "SK하이닉스",
          "text": "SK하이닉스의 종가는 최근 5거래일간 +15.46% 상승했습니다.",
          "value": 15.46,
          "threshold": 7.0,
          "unit": "%",
          "date": "2026-10-27",
          "direction": "up"
        },
        {
          "type": "new_disclosure",
          "scope": "watchlist",
          "ticker": "000660",
          "name": "SK하이닉스",
          "text": "SK하이닉스의 공시가 최근 7일 안에 10건 있습니다.",
          "value": 10.0,
          "threshold": 1.0,
          "unit": "건",
          "date": "2026-10-28",
          "direction": null
        }
      ]
    }
  ],
  "highlights": [
    {
      "type": "day_move",
      "scope": "holding",
      "ticker": "005930",
      "name": "삼성전자",
      "text": "삼성전자의 2026-10-27 종가는 전 거래일 대비 +5.39% 상승했습니다.",
      "value": 5.39,
      "threshold": 3.0,
      "unit": "%",
      "date": "2026-10-27",
      "direction": "up"
    },
    {
      "type": "day_move",
      "scope": "holding",
      "ticker": "069500",
      "name": "KODEX 200",
      "text": "KODEX 200의 2026-10-27 종가는 전 거래일 대비 +3.38% 상승했습니다.",
      "value": 3.38,
      "threshold": 3.0,
      "unit": "%",
      "date": "2026-10-27",
      "direction": "up"
    }
  ],
  "disclaimer": "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
}
```

### 필드

모든 필드는 **항상 존재**한다(값이 없으면 키를 빼지 않고 `null` 또는 빈 배열). 숫자는 JSON number, 날짜는 문자열이다.

**최상위**

| 필드 | 타입 | null | 설명 |
| --- | --- | --- | --- |
| `generated_at` | string | 불가 | 생성 시각(ISO 8601, KST) |
| `data_date` | string(YYYY-MM-DD) | 가능 | 브리핑에 쓴 시세의 마지막 날짜(종목들 중 가장 최근, 종목이 없으면 코스피/코스닥 지수 날짜). 시세가 전혀 없으면 null |
| `stale` | boolean | 불가 | `data_date`가 최근 거래일보다 2영업일 이상 뒤처졌거나 `data_date`가 null이면 true(`GET /api/status`와 같은 규칙) |
| `stale_notice` | string | 가능 | `stale`일 때만: "마지막 데이터는 YYYY-MM-DD 기준입니다." (데이터가 없으면 "수집된 시세 데이터가 없습니다.") |
| `message` | string | 가능 | 보유종목과 관심종목이 모두 없을 때만: "관심종목을 추가하면 개인화된 브리핑을 볼 수 있습니다." |
| `market` | object | 불가 | 아래 "market" |
| `portfolio` | object | 불가 | 아래 "portfolio" |
| `holdings` | array | 불가 | 보유종목 항목(비중 큰 순, 시세 없는 종목은 뒤). 없으면 `[]` |
| `watchlist` | array | 불가 | 관심종목 항목(등록 최신순). **보유종목은 여기서 빠진다**. 없으면 `[]` |
| `highlights` | array&lt;Flag&gt; | 불가 | 전체 flag 중 중요도 순 상위 5개(없으면 `[]`) |
| `disclaimer` | string | 불가 | 서비스 면책 문구(`GET /api/disclaimer`와 같은 문구). 화면에 같이 보여 준다 |

**`market`**: `kospi`, `kosdaq`, `usd_krw`(원/달러) 세 키가 항상 있고 각각 `Quote | null`이다. 해당 데이터가 DB에 없으면 그 항목만 null이고 전체는 실패하지 않는다.

| `Quote` 필드 | 타입 | null | 설명 |
| --- | --- | --- | --- |
| `code` | string | 불가 | `KS11` / `KQ11` / `USD/KRW` |
| `name` | string | 불가 | 코스피 / 코스닥 / 원/달러 |
| `date` | string | 불가 | 최신 값의 날짜 |
| `close` | number | 불가 | 최신 종가(환율은 원) |
| `change_pct` | number | 가능 | 직전 값 대비 %(저장된 종가에서 계산, 소수 둘째 자리). 직전 값이 없으면 null |

**`portfolio`** (비중은 모두 **총자산 = 현금 + 시세가 있는 보유종목 평가금액** 대비 %, `POST /api/portfolio/diagnosis`와 같은 정의)

| 필드 | 타입 | null | 설명 |
| --- | --- | --- | --- |
| `has_account` | boolean | 불가 | 모의투자 계좌가 있는지. false이면 아래 숫자는 모두 null/빈 배열 |
| `initial_balance` | number | 불가 | 초기 자금(10,000,000) |
| `cash_balance` | number | 가능 | 현금 |
| `holdings_value` | number | 가능 | 보유종목 평가금액 합(최신 종가 x 수량) |
| `total_asset` | number | 가능 | 현금 + 평가금액 |
| `profit_loss` | number | 가능 | 총자산 - 초기 자금 |
| `profit_loss_pct` | number | 가능 | `profit_loss` / 초기 자금 x 100 |
| `day_profit_loss` | number | 가능 | 전일 대비 평가손익 = 보유수량 x (최신 종가 - 직전 종가)의 합(직전 종가가 있는 종목만). 해당 종목이 없으면 null |
| `top_weight` | object | 가능 | 비중이 가장 큰 종목 `{ticker, name, weight_pct}`. 시세 있는 보유종목이 없으면 null |
| `weights` | array | 불가 | `{ticker, name, weight_pct}` 비중 큰 순. 시세 없는 종목은 빠진다 |
| `message` | string | 가능 | 계좌가 없을 때 / 보유종목이 없을 때 / 시세 없는 보유종목이 있어 계산에서 제외했을 때 안내 문구 |

**`holdings[]`, `watchlist[]` 공통 항목**

| 필드 | 타입 | null | 설명 |
| --- | --- | --- | --- |
| `ticker`, `name` | string | 불가 | 종목 식별자 |
| `asset_type` | `"stock"` 또는 `"etf"` | 불가 | |
| `data_available` | boolean | 불가 | 시세가 하나라도 있는지. **false이면 아래 시세 필드는 모두 null**이고 다른 종목에는 영향이 없다 |
| `latest_close` | number | 가능 | 최신 종가(원) |
| `latest_date` | string | 가능 | 그 종가의 날짜 |
| `day_change_pct` | number | 가능 | 1일 등락률(최신 종가 / 직전 거래일 종가 - 1, %). 시세가 1일치뿐이면 null |
| `week_change_pct` | number | 가능 | 5거래일 등락률(최신 종가 / 5거래일 전 종가 - 1, %). 6일치 미만이면 null |
| `volume` | integer | 가능 | 최신 거래량(주) |
| `avg_volume_20d` | number | 가능 | 직전 20거래일(최신 제외) 평균 거래량. 20일치가 안 되거나 그 중 비어 있는 날이 있으면 null |
| `volume_ratio` | number | 가능 | `volume` / `avg_volume_20d`. 평균이 null이거나 0이면 null(0으로 나누지 않는다) |
| `news` | array | 불가 | 최근 7일 뉴스 최대 `news_limit`건 `{title, source(null 가능), published_at(ISO, KST), url(null 가능)}`. 제목이나 URL이 같은 기사는 최신 하나만. ETF는 리서치의 뉴스 도구와 같은 기준(ETF 이름이 직접 나온 기사만) |
| `disclosures` | array | 불가 | 최근 7일 공시 최대 3건 `{title, disclosure_type(null 가능), disclosed_at, source_url(null 가능)}`. ETF는 공시를 수집하지 않아 항상 `[]` |
| `disclosure_count` | integer | 불가 | 최근 7일 공시 전체 건수(`disclosures`는 3건까지만 담는다) |
| `notice` | string | 가능 | 레버리지/인버스 ETF(이름으로 판별)에만 붙는 짧은 주의 문구 |
| `flags` | array&lt;Flag&gt; | 불가 | 이 종목의 flag(아래). 없으면 `[]` |

**`holdings[]`에만 더 있는 필드**

| 필드 | 타입 | null | 설명 |
| --- | --- | --- | --- |
| `quantity` | integer | 불가 | 보유 수량 |
| `avg_price` | number | 불가 | 평균 매입 단가 |
| `eval_amount` | number | 가능 | 평가금액 = 최신 종가 x 수량 (시세 없으면 null) |
| `weight_pct` | number | 가능 | 총자산 대비 비중 % |
| `profit_loss` | number | 가능 | (최신 종가 - 평단) x 수량 |
| `profit_loss_pct` | number | 가능 | 최신 종가 / 평단 - 1 (%) |
| `day_profit_loss` | number | 가능 | 수량 x (최신 종가 - 직전 종가). 직전 종가가 없으면 null |

**`Flag`** (`highlights[]`와 항목의 `flags[]`가 같은 모양)

| 필드 | 타입 | null | 설명 |
| --- | --- | --- | --- |
| `type` | string | 불가 | `concentration`, `day_move`, `week_move`, `volume_spike`, `new_disclosure` 중 하나 |
| `scope` | `"holding"` 또는 `"watchlist"` | 불가 | 어느 목록의 종목인지 |
| `ticker`, `name` | string | 불가 | 종목 |
| `text` | string | 불가 | 관찰 사실 한 줄(화면에 그대로 보여 줘도 된다) |
| `value` | number | 불가 | 근거 수치(`unit` 단위) |
| `threshold` | number | 불가 | 이 flag가 켜지는 기준값(같은 단위) |
| `unit` | `"%"`, `"배"`, `"건"` 중 하나 | 불가 | |
| `date` | string | 가능 | 근거 날짜: 시세 flag는 종가 날짜, `new_disclosure`는 가장 최근 공시일 |
| `direction` | `"up"` 또는 `"down"` | 가능 | `day_move`/`week_move`만 값이 있다(색 표시용). 나머지는 null |

### flag 종류와 임계값

임계값은 `backend/app/briefing.py` 상단 상수다. 비교는 화면에 보이는 소수 둘째 자리 값으로 하고 **경계값은 포함(이상)** 이다
(정확히 3.00%면 켜진다).

| `type` | 켜지는 조건 | 상수 | `unit` | 문구 예 |
| --- | --- | --- | --- | --- |
| `concentration` | 보유종목 한 종목의 비중 >= 40.0% (보유종목만) | `CONCENTRATION_PCT` | % | "삼성전자의 비중이 총자산의 45.20%입니다(기준 40% 이상)." |
| `day_move` | 1일 등락률의 절댓값 >= 3.0% | `DAY_MOVE_PCT` | % | "삼성전자의 2026-10-27 종가는 전 거래일 대비 +5.39% 상승했습니다." |
| `week_move` | 5거래일 등락률의 절댓값 >= 7.0% | `WEEK_MOVE_PCT` | % | "삼성전자의 종가는 최근 5거래일간 +7.66% 상승했습니다." |
| `volume_spike` | 거래량 배수 >= 2.0 | `VOLUME_SPIKE_RATIO` | 배 | "삼성전자의 거래량이 직전 20거래일 평균의 2.30배입니다." |
| `new_disclosure` | 최근 7일 공시 >= 1건 | `NEW_DISCLOSURE_MIN`, `RECENT_DAYS` | 건 | "SK하이닉스의 공시가 최근 7일 안에 10건 있습니다." |

한 종목의 `flags`는 위 표의 순서다. **`highlights` 정렬 규칙**(앞이 먼저): (1) 보유종목 flag가 관심종목 flag보다 먼저, (2) 종류 순서 `concentration` >
`day_move` > `week_move` > `volume_spike` > `new_disclosure`, (3) 기준 대비 크기(value의 절댓값 / threshold)가 큰 것, (4) 종목코드 오름차순. 상위 5개만 담는다.
문구는 "상승했습니다/하락했습니다/배입니다" 같은 관찰 표현만 쓴다. 매수·매도·추천 등의 표현은 코드에서 만들어질 수 없고, 모든 flag 문구를
`evals/metrics.py`의 금지 표현 검사로 테스트한다.

### 결손 처리와 데이터 기준

- 시세가 1일치뿐이면 `day_change_pct`/`week_change_pct`/`volume_ratio`가 null. 직전 20거래일이 안 되면 `volume_ratio`와 `avg_volume_20d`가 null.
  종가가 비어 있거나 0 이하인 행은 없는 것으로 본다.
- 시세가 아예 없는 종목은 `data_available: false`로 항목에 남는다. 보유종목이면 평가금액·비중·손익이 null이고 총자산 계산에서 빠지며
  `portfolio.message`에 이름이 안내된다.
- **장중 규칙**: 평일 15:30(KST) 전에는 **오늘 날짜의 행을 쓰지 않는다**. 장중에 수집한 행은 종가가 아니고, 그날 거래량도 반나절치라서 거래량 배수가
  실제보다 작게 나온다. 이때 브리핑은 직전 거래일 종가 기준이고 `data_date`가 그 날짜를 보여 준다. 15:30 이후에 `refresh_data`로 수집하면 오늘 종가가 쓰인다.
- 시장 지수·환율은 DB에 저장된 일별 값이다(실시간 아님). 보통 시세보다 하루쯤 늦을 수 있어 각 `Quote.date`를 같이 보여 주는 편이 좋다.
- 데이터는 수동 갱신(`python -m scripts.refresh_data`)이라 갱신이 멈추면 `stale`이 true가 된다.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 422 | `news_limit`이 1~5 밖이거나 정수가 아님 | FastAPI 기본 검증 오류(`detail`이 배열) |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

보유종목도 관심종목도 없는 사용자는 에러가 아니라 **200 + `message`** (`market`은 채워짐, `holdings`/`watchlist`/`highlights`는 `[]`)다.

### 한계

- 숫자는 일별 종가 기준이고 실시간이 아니다. 평가금액·손익도 DB 종가로 계산하므로 `GET /api/portfolio`(실시간 시도)의 값과 다를 수 있다.
- 뉴스·공시는 제목과 출처만 보여 주며, **기사 내용의 사실 여부는 확인하지 않는다**. 종목과 관련이 적은 기사가 섞일 수 있다(ETF는 이름이 직접 나온 기사로 걸러낸다).
- "전일 대비 평가손익"은 종목마다 자기 직전 거래일 종가를 쓴다. 종목별 최신 날짜가 다르면(수집이 덜 된 종목) 같은 날의 변화가 아닐 수 있다. 종목별 `latest_date`를 확인한다.
- ~~공휴일은 모른다(평일을 영업일로 본다).~~ 2026-11-08(36차)부터 KRX 거래일 달력(`app/krx_calendar.py`)으로 공휴일·대체공휴일·연말 휴장을 뺀다. 연휴 직후의 `stale` 오탐이 없어졌다.
- flag 임계값은 종목 변동성과 무관한 고정값이다(레버리지 ETF는 `day_move`가 자주 켜진다 - `notice` 참고).

### 프론트(zod) 메모

`nullable()`로 받아야 하는 필드는 위 표의 "null 가능" 열이 전부다. 배열 필드(`holdings`, `watchlist`, `highlights`, `news`, `disclosures`, `flags`, `weights`)는
절대 null이 아니다. `holdings[]` 스키마는 공통 항목 스키마를 `extend`해서 만들면 된다. `type`/`scope`/`unit`/`direction`/`asset_type`은 위 값만 오지만, 나중에 flag 종류가 늘 수 있으니
`type`은 알 수 없는 값을 무시하거나 그대로 보여 주는 쪽을 권한다.

---

## GET /api/health

서비스 상태 확인(공개, 로그인 불필요). 발표·시연 전 점검용이다. 검사마다 상한 2초로 **병렬** 실행하고, 하나가 실패하거나 멈춰도
**항상 200**으로 `status: "degraded"`와 사유를 돌려준다(예외 없음). 응답에는 비밀값·내부 주소·버전(마이그레이션 리비전, 모델 태그)을 넣지 않는다.

```bash
curl http://localhost:8000/api/health
```

### 응답 200 (2026-10-28 실제 응답, dev DB)

```json
{"status": "ok", "checks": {"db": {"ok": true}, "migrations": {"ok": true}, "ollama": {"ok": true, "model_installed": true, "model_loaded": false}, "data": {"ok": true, "latest_date": "2026-10-28", "stale": false}}}
```

| 검사 | ok 조건 | 추가 필드 | 실패 사유(`reason`) 예 |
| --- | --- | --- | --- |
| `db` | `SELECT 1` 성공 | - | "연결 또는 조회 실패(OperationalError)", "응답 시간 초과" |
| `migrations` | DB의 alembic 리비전 == 코드의 head | - | "DB 스키마가 코드의 최신 마이그레이션과 다릅니다" |
| `ollama` | 서버 연결 + 기본 모델(llama3.1:8b) 설치 | `model_installed`, `model_loaded`(메모리에 올라가 있는지. false여도 ok - 첫 질문이 몇 초 느릴 뿐) | "기본 모델이 설치되어 있지 않습니다", "연결 또는 조회 실패(ConnectionError)" |
| `data` | 마지막 시세 날짜가 2영업일 이상 뒤처지지 않음(`GET /api/status`와 같은 규칙) | `latest_date`(null 가능), `stale` | "시세 데이터가 오래됐습니다", "시세 데이터가 없습니다" |

- `status`는 모든 검사가 ok면 `"ok"`, 하나라도 아니면 `"degraded"`. 사유는 예외 **종류만** 쓴다(메시지에는 주소·SQL이 들어갈 수 있어 내보내지 않음).
- 실패한 검사가 있어도 2초 남짓이면 응답한다. 측정: 정상 23ms(첫 호출 162ms).

---

## GET /api/portfolio/performance (2026-10-29)

인증 필요. 모의투자 체결 내역을 날짜순으로 다시 계산해 **거래일 종가 기준 일별 자산 곡선**과 성과 지표를 돌려준다.
**LLM을 쓰지 않고** 모든 숫자는 코드가 DB 값(체결, 종가, 코스피 지수)으로 계산한다. 읽기 전용이다(계좌를 만들지 않고 아무것도 쓰지 않는다).
본인 계좌만 대상이고, 토큰이 없으면 401. 문구는 관찰 사실만 쓴다(투자 권유 없음, 응답에 `disclaimer` 포함).

```bash
curl "http://localhost:8000/api/portfolio/performance?period=3m" -H "Authorization: Bearer $TOKEN"
```

| 쿼리 | 값 | 기본 | 범위 밖 |
| --- | --- | --- | --- |
| `period` | `1m` / `3m` / `6m` / `1y` / `all` (`1y`는 2026-11-06 추가) | `all` | 422(대소문자 구분, `2y`·빈 값도 422) |
| `view` | `default` / `benchmark` | `default` | `benchmark`면 아래 "성과·벤치마크 비교" 응답(2026-11-06) |

### 계산 규칙

- **자산** = 현금 + Σ(보유 수량 × 그 거래일 종가). 체결은 체결일(KST)의 장 마감 종가로 평가한다. 같은 날 여러 건은 체결 순서대로 반영한다.
  주말·휴일 체결은 다음 거래일 종가에 반영된다. 매수·매도의 현금·평균단가 계산은 주문 API와 같은 함수(`app.portfolio.apply_buy/apply_sell`)다.
- **거래일**: 시세가 한 종목이라도 수집된 날. 휴장일(시세 행이 없는 날)은 건너뛴다.
- **기간**: `all`은 첫 체결 직전 거래일부터 `data_date`까지이고 시작값은 초기 자금(`initial_balance`, 1,000만원).
  `1m`/`3m`/`6m`은 `data_date`에서 그 개월 수를 뺀 날(말일 보정) 이전 마지막 거래일부터이고, 시작값은 그날의 자산이다.
  첫 체결 전 구간은 초기 자금 그대로다(notice로 알린다).
- **장중 규칙**: 거래일 15:30(KST) 전에는 오늘 날짜의 시세·지수 행을 쓰지 않는다(`GET /api/briefing`과 같은 함수, 2026-11-08부터 KRX 휴장일은 장중이 아니다).
  `data_date`는 그렇게 정한 마지막 거래일이고, 2영업일(2026-11-08부터 KRX 거래일 기준, 그 전에는 평일) 이상 뒤처지면 `stale: true`와 `stale_notice`가 붙는다.
- **`data_date` 이후 체결**(오늘 장중이나 종가 수집 전 주문)은 아직 종가가 없어 곡선·지표에 넣지 않고 `trades.pending_count`로만 센다(notice).
- **결손**: 보유 종목의 그날 종가가 없으면 직전 종가를 쓰고, 종가가 한 번도 없으면 마지막 체결가를 쓴다. 어느 쪽이든 `data_gaps`에 남긴다.
  잔고·수량이 맞지 않아 재생할 수 없는 체결은 빼고 notice로 알린다. 체결 내역을 다시 계산한 현금이 현재 잔고와 다르면 notice로 알린다.
- **0으로 나누기 없음**: 분모가 0 이하이면 그 값은 `null`이다.

### 지표 정의

| 필드 | 정의 |
| --- | --- |
| `summary.cumulative_return_pct` | (마지막 자산 ÷ 초기 자금 − 1) × 100 |
| `summary.period_return_pct` | (마지막 자산 ÷ 기간 시작값 − 1) × 100. `all`이면 누적 수익률과 같다 |
| `summary.profit_loss` | 마지막 자산 − 기간 시작값 |
| `summary.cash_weight_pct` | 마지막 날 현금 ÷ 마지막 자산 × 100 |
| `risk.max_drawdown.pct` | 기간 곡선에서 min(자산 ÷ 그때까지의 최고 자산 − 1) × 100 (0 이하). `peak_date`/`trough_date`는 그 고점·저점, `recovery_date`는 저점 뒤 처음으로 고점 자산 이상이 된 날(없으면 null), `duration_days`는 고점→저점 거래일 수. 하락이 없으면 0과 날짜 null |
| `risk.daily_volatility_pct` | 연속한 두 거래일 자산 등락률(%)의 **표본 표준편차**. 수익률이 2개 미만(곡선 2점 이하)이면 null |
| `risk.annualized_volatility_pct` | 일간 변동성 × √252 |
| `trades.win_rate_pct` | 기간 중 매도 건 가운데 실현손익((매도가 − 매도 직전 평균단가) × 수량)이 양수인 비율. 매도가 없으면 null. 0원은 `even` |
| `contributions[].profit_loss` | 기간 끝 평가액 + 기간 중 매도대금 − 기간 중 매수대금 − 기간 시작 평가액. **종목 합 = `summary.profit_loss`**. 큰 순으로 정렬 |
| `contributions[].contribution_pct` | 위 손익 ÷ 기간 시작값 × 100. 종목 합 = 기간 수익률 |
| `benchmark` | 코스피(`market_index`의 `KS11`). 기간과 지수 데이터가 겹치는 구간 [`start_date`, `end_date`]에서 지수 수익률과 같은 구간의 포트폴리오 수익률, 초과수익(%p = 포트폴리오 − 지수). 지수가 기간을 다 덮지 못하면 `covers_full_period: false`와 notice. 지수 데이터가 없거나 겹치지 않으면 `null`과 notice |

### 응답 200

| 필드 | 타입 | null | 설명 |
| --- | --- | --- | --- |
| `generated_at` | string | 아니오 | 응답 시각(KST, ISO 8601) |
| `period` | string | 아니오 | 요청한 기간 |
| `data_date` | string | 예 | 마지막 거래일(YYYY-MM-DD). 시세가 하나도 없으면 null |
| `stale` / `stale_notice` | bool / string | 아니오 / 예 | 데이터가 뒤처졌는지와 안내 |
| `start_date` / `end_date` | string | 예 | 곡선의 첫날·마지막 날(시세가 없으면 null) |
| `initial_balance` | number | 아니오 | 초기 자금 |
| `message` | string | 예 | 체결이 없을 때(`아직 체결된 모의투자 주문이 없습니다...`), 체결이 모두 `data_date` 이후일 때, 시세가 없을 때의 안내. 정상 계산이면 null |
| `summary` | object | 아니오 | `start_value`, `end_value`, `cash_balance`, `eval_amount`, `profit_loss`(숫자), `cumulative_return_pct`?, `period_return_pct`?, `cash_weight_pct`? |
| `risk` | object | 아니오 | `max_drawdown`{`pct`, `peak_date`?, `trough_date`?, `recovery_date`?, `duration_days`}, `daily_volatility_pct`?, `annualized_volatility_pct`?, `daily_return_count` |
| `trades` | object | 아니오 | 기간 중 `count`, `buys`, `sells`, `wins`, `losses`, `even`, `win_rate_pct`?, `realized_profit_loss`, 그리고 `pending_count`(종가 반영 전 체결 수) |
| `contributions[]` | array | 아니오 | `ticker`, `company_name`, `profit_loss`, `contribution_pct`?, `quantity`(기간 끝), `eval_amount`, `unrealized_profit_loss`?(보유 중일 때만, 평균단가 대비), `realized_profit_loss` |
| `benchmark` | object | 예 | 위 정의 |
| `curve` | object | 아니오 | `points[]`{`date`, `total_asset`, `cash_balance`, `eval_amount`, `return_pct`?(기간 시작값 대비 %)}, `total_points`(샘플링 전), `sampled`, `max_points`(400) |
| `data_gaps[]` | array | 아니오 | `ticker`, `company_name`, `kind`(`previous_close` / `trade_price`), `days`, `first_date`, `last_date` (기간 안에서 대체 가격을 쓴 날) |
| `notices[]` | string[] | 아니오 | 부분 비교·결손·대기 체결 등 안내 |
| `disclaimer` | string | 아니오 | `GET /api/disclaimer`와 같은 문구 |

`?` 표시는 null 가능. 체결이 없으면 곡선은 초기 자금 한 점(`data_date`), 지표는 0이고 변동성·승률·벤치마크는 null이다.

**곡선 샘플링**: 포인트가 400개를 넘으면 고르게 줄이되 첫 점·마지막 점·최고점·최저점·MDD 고점/저점은 반드시 남기고 `sampled: true`다.
지표는 샘플링 전 전체 곡선으로 계산한다.

예시(captest 계정, 과거 체결 5건: 삼성전자 20주 매수 → 8주 매도, SK하이닉스 1주 매수 → 매도, NAVER 5주 매수, 2026-10-29 15:05):

```json
{
  "period": "all", "data_date": "2026-10-28", "stale": false, "start_date": "2026-09-22", "end_date": "2026-10-28",
  "initial_balance": 10000000.0, "message": null,
  "summary": {"start_value": 10000000.0, "end_value": 8409000.0, "cash_balance": 4635500.0, "eval_amount": 3773500.0,
              "profit_loss": -1591000.0, "cumulative_return_pct": -15.91, "period_return_pct": -15.91, "cash_weight_pct": 55.13},
  "risk": {"max_drawdown": {"pct": -20.23, "peak_date": "2026-09-22", "trough_date": "2026-10-20", "recovery_date": null, "duration_days": 14},
           "daily_volatility_pct": 2.3419, "annualized_volatility_pct": 37.1764, "daily_return_count": 20},
  "trades": {"count": 5, "buys": 3, "sells": 2, "wins": 0, "losses": 2, "even": 0, "win_rate_pct": 0.0,
             "realized_profit_loss": -1049000.0, "pending_count": 0},
  "contributions": [
    {"ticker": "035420", "company_name": "NAVER", "profit_loss": 40000.0, "contribution_pct": 0.4, "quantity": 5,
     "eval_amount": 917500.0, "unrealized_profit_loss": 40000.0, "realized_profit_loss": 0.0},
    {"ticker": "000660", "company_name": "SK하이닉스", "profit_loss": -629000.0, "contribution_pct": -6.29, "quantity": 0,
     "eval_amount": 0.0, "unrealized_profit_loss": null, "realized_profit_loss": -629000.0},
    {"ticker": "005930", "company_name": "삼성전자", "profit_loss": -1002000.0, "contribution_pct": -10.02, "quantity": 12,
     "eval_amount": 2856000.0, "unrealized_profit_loss": -582000.0, "realized_profit_loss": -420000.0}
  ],
  "benchmark": {"index_code": "KS11", "index_name": "코스피", "start_date": "2026-09-22", "end_date": "2026-10-27",
                "index_return_pct": -19.66, "portfolio_return_pct": -17.57, "excess_return_pct": 2.08, "covers_full_period": false},
  "curve": {"points": [{"date": "2026-09-22", "total_asset": 10000000.0, "cash_balance": 10000000.0, "eval_amount": 0.0, "return_pct": 0.0},
                       "... 21개 ..."], "total_points": 21, "sampled": false, "max_points": 400},
  "data_gaps": [],
  "notices": ["코스피 지수 데이터가 2026-09-22~2026-10-27만 있어 그 구간끼리만 비교했습니다."],
  "disclaimer": "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. ..."
}
```

### 쿼리 수

요청당 DB 쿼리는 보유·거래 종목 수와 상관없이 **5개**다(계좌 1 + 체결 1 + 거래일 달력 1 + 종가 1(`company_id IN (...)`) + 지수 1). 인증의 사용자 조회 1개는 별도다.
`tests/test_performance_devdb.py`가 종목 1개와 10개 계좌의 쿼리 수가 같고 5 이하인지 확인한다.

### 에러

| 상태 | 언제 |
| --- | --- |
| 401 | 토큰 없음/무효(위 "인증 에러") |
| 422 | `period`가 허용 값이 아님 |
| 503 | DB 연결 불가 |

### 한계

- **일봉 종가 기준이다.** 체결 당일의 장중 움직임은 반영하지 않고, 장 마감 뒤에 체결한 주문도 그날 종가로 평가한다. 체결가 자체는 주문 때의 현재가 그대로다.
- **수수료·세금·슬리피지는 반영하지 않는다**(모의투자 주문 자체가 반영하지 않는다).
- 거래일은 "시세가 한 종목이라도 있는 날"이라, 전 종목 수집이 빠진 날(예: 2026-10-15·16)은 휴장일처럼 건너뛴다. 일부 종목만 빠진 날은 `data_gaps`로 보인다.
- `POST /api/portfolio/reset`은 체결 내역을 지우므로 초기화 전의 성과는 계산할 수 없다(초기화 이후 체결만 재생한다).
- 지수(코스피)는 수집 시점에 따라 시세보다 늦을 수 있어(2026-10-29 기준 지수는 10/27, 시세는 10/28) 벤치마크가 부분 구간 비교가 되는 경우가 많다.
- 벤치마크는 코스피만이다(코스닥 종목만 가진 계좌도 코스피와 비교한다). 무위험 수익률이 없어 샤프 비율 같은 위험조정 지표는 없다.
- 변동성은 거래일 간 자산 등락률이라 표본(거래일)이 적으면 의미가 약하다.

---

## 성과·벤치마크 비교: `GET /api/portfolio/performance?view=benchmark` (2026-11-06)

화면용 상세(필드 표, 상태별 화면, 실제 dev DB 응답·빈 응답·계산 불가 응답 JSON)는 `docs/FRONTEND_HANDOFF_PORTFOLIO_PERFORMANCE.md`. 계산 `app/performance_compare.py`, 문장 `app/performance_i18n.py`, DB `app/performance_db.get_performance_view`. `view` 없이(`default`) 부르면 위 응답 그대로다(필드 불변). 이번에 `period`에 `1y`가 더해졌다(기본 응답에도 적용, 대문자 값은 여전히 422).

**조사 결과**: 체결 내역(`trade`: 체결 시각·수량·체결가, 수정 없는 로그)을 날짜순으로 재생하는 성과 계산(`app/performance.py`, 2026-10-29)이 이미 있었다. 날짜별 보유 수량·현금 복원, 거래일 종가 평가, 1m·3m·6m·all, 최대 낙폭·변동성, 코스피 기간 수익률까지 있다. 수수료·세금은 주문 계산에 없고(체결가 × 수량), 입금·출금 기능도 없다. 계좌 초기화는 체결·보유를 지우고 현금을 `INITIAL_BALANCE`로 되돌린다. 그래서 초기 자금부터의 재생은 코드상 근거가 있다. 다만 재생 결과가 계좌와 다를 때 안내 문장만 붙이고 계산을 계속했고(`CASH_MISMATCH`), 보유 수량은 대조하지 않았다. 지수는 코스피만 쓰고 공통 거래일 시계열·정규화가 없었으며, 직전 종가로 평가한 날을 점마다 표시하지 않았다. 지수 데이터는 `market_index`의 KS11·KQ11 각 733거래일(2023-10-30~2026-11-03, 종목 시세 거래일과 같음)이고 KOSPI200은 없다.

**새 계산(`calc_version` = `performance.v2`)**
- 이력 대조: 모든 체결을 재생한 현금·보유 수량이 지금 계좌(`virtual_account.cash_balance`, `holding`)와 다르거나 재생할 수 없는 체결(보유보다 많이 판 기록 등)이 있으면 `status: history_unavailable`, 수익률·시계열·비교는 null·빈 값이다. 현재 보유를 과거에 소급하지 않는다.
- 총자산 = 현금 + Σ(그날 보유 수량 × 그날 종가). 그날 종가가 없으면 직전 종가(없으면 마지막 체결가)로 평가하고 그 점에 `estimated`·`estimated_tickers`를 붙인다. 하나라도 있으면 `status: partial`이다.
- 기간 시작 = `data_date`에서 개월 수를 뺀 날 이하의 마지막 거래일. 첫 체결 전이면 첫 체결 직전 거래일(inception)로 늦추고 `requested_start_date`를 함께 준다.
- 시작 금액: `all`은 초기 자금, 그 밖은 실제 시작일 총자산. 기간 수익률 = 끝 ÷ 시작 − 1, 누적 수익률 = 끝 ÷ 초기 자금 − 1, 일별 수익률 = 당일 ÷ 전 거래일 − 1. 입출금이 없어 단순 수익률이 TWR과 같다(`cash_flows`).
- 벤치마크(`benchmark` = KS11|KQ11|KOSPI|KOSDAQ): 포트폴리오와 지수가 둘 다 있는 거래일만 쓴다. 첫 공통 거래일을 양쪽 100으로 정규화하고(포트폴리오 시작일과 다르면 그날로 다시 맞춘다), 초과 수익률(%p) = 포트폴리오 − 지수다. 공통 거래일이 2일 미만이면 `insufficient_overlap`, 지수가 없으면 `no_data`, 포트폴리오가 계산 불가면 `portfolio_unavailable`, 지수가 빠진 날이 있거나 포트폴리오에 추정값이 있으면 `partial`.
- 지표: 최고·최저 총자산(1개 이상), 최대 낙폭(2개 이상), 변동성(일간 수익률 20개 이상), 연환산 수익률(252개 이상). 기준에 못 미치면 null.
- 가격 기준: 저장된 종가(`stock_price.close_price`). 배당·분배금은 포함하지 않는다. 분할·병합 보정은 하지 않고, 보유 종목의 하루 ±30.5% 초과 변동을 `price_discontinuity`로 경고한다(`app.price_quality`). 수집 소스의 수정주가 여부는 이 기능에서 확인하지 않았다(`corporate_actions_adjusted: not_verified`).
- 지연: `data_lag_business_days` = 기대 거래일(`outlook.expected_trading_day`, 장 마감 전이면 직전 평일) 대비 마지막 종가일의 영업일 지연. 0보다 크면 `data_delayed` 경고.

**상태**: `ok`, `partial`, `no_account`(계좌·체결 없음), `no_trades`, `pending_only`(체결이 모두 마지막 종가일 뒤), `no_prices`, `history_unavailable`.

**응답 필드**: `generated_at, calc_version, locale, status, message, history_verified, period, requested_start_date, start_date, end_date, data_date, expected_date, data_lag_business_days, initial_balance, benchmark_code, benchmark_name, summary{current_cash, start_value, end_value, profit_loss, period_return_pct, cumulative_return_pct, eval_amount, cash_balance}, metrics{highest, lowest, max_drawdown, daily_volatility_pct, annualized_volatility_pct, annualized_return_pct, daily_return_count, trading_days, sample_rules}, series{points[{date, total_asset, cash_balance, eval_amount, daily_return_pct, cumulative_return_pct, portfolio_index, estimated, estimated_tickers}], total_points, sampled, max_points}, comparison{status, message, benchmark_code, benchmark_name, start_date, end_date, common_days, start_aligned, benchmark_start_value, benchmark_end_value, benchmark_last_date, benchmark_lag_business_days, portfolio_return_pct, benchmark_return_pct, excess_return_pp, points[{date, portfolio_index, benchmark_index, benchmark_close, portfolio_return_pct, benchmark_return_pct, excess_return_pp, estimated}], total_points, sampled}, estimated_dates, cash_flows, price_basis, warnings[{kind, …, message}], disclaimer`. 금액은 원, 수익률은 %(소수 둘째 자리), 초과 수익률은 %p, 지수 정규화 값은 소수 넷째 자리. 시계열은 400점 상한이다(첫·끝·최고·최저·최대 낙폭 점은 남긴다).

**인증·성능·저장**: 로그인 필요(401), 본인 `owner_key`만 읽는다. 쿼리는 체결 수와 관계없이 7개 이하(계좌·체결·달력·종가·코스피 + 보유 1 + 선택 지수 1)다. 일별 스냅샷 테이블은 만들지 않았다. 체결 내역이 완전하면 재생으로 복원되고, 불완전하면 대조에서 `history_unavailable`로 드러나기 때문이다. 마이그레이션은 없다.

**다국어**: 수치는 한 번 계산하고 `message`·`comparison.message`·`warnings[].message`·`benchmark_name`·`disclaimer`만 4개 언어로 쓴다.

## POST /api/backtest (2026-10-29)

인증 필요. 과거 종가로 전략을 시험한다. **LLM을 쓰지 않고** 모든 숫자는 코드가 DB 종가로 계산한다. 결과는 저장하지 않는다(요청마다 계산).
과거 성과는 미래 수익을 보장하지 않는다(응답의 `disclaimer`).

처리 순서: 인증(401) → 본문 검증(422) → 레이트 리밋(429, 사용자당 분당 10회) → 종목 해석(404) → 데이터 확인(400) → 계산.

```bash
curl -X POST http://localhost:8000/api/backtest -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tickers": ["005930", "069500"], "strategy": "sma_cross", "params": {"short": 20, "long": 60},
       "start": "2023-11-01", "end": "2026-10-28", "initial_cash": 10000000}'
```

### 요청

| 필드 | 타입 | 기본 | 규칙(어기면 422) |
| --- | --- | --- | --- |
| `tickers` | string[] | (필수) | 1~5개, 각 1~50자, 중복 불가. 종목코드·정확한 종목명·별칭만 인정(주문 API와 같은 엄격 모드). 못 찾으면 **404 + 후보 안내**("혹시 SK하이닉스, … 중 하나인가요?") |
| `strategy` | string | (필수) | `buy_and_hold` / `sma_cross` / `dca` |
| `params` | object | `{}` | 전략별(아래). 모르는 키, 숫자가 아닌 값, 범위 밖은 422(`loc: ["body", "params", 키]`) |
| `start`, `end` | date | (필수) | `1990-01-01` ≤ start < end ≤ 오늘, 기간 최대 5년 |
| `initial_cash` | number | 10,000,000 | 100,000 ~ 10,000,000,000 |
| `fee_rate` | number | 0.00015 | 0 ~ 0.01. **가정값이며 실제 수수료와 다를 수 있음** |
| `sell_tax_rate` | number | 0.0018 | 0 ~ 0.01. **가정값이며 실제 세율과 다를 수 있음**(현재 법정 세율을 단정하지 않는다) |

### 전략별 규칙

| 전략 | params(기본) | 규칙 |
| --- | --- | --- |
| `buy_and_hold` | 없음 | 첫 거래일에 매수 신호 → 다음 거래일 종가에 칸(sleeve)의 현금으로 살 수 있는 만큼 매수, 끝까지 보유 |
| `sma_cross` | `short` 2~120(20), `long` 3~250(60), `short < long`, 정수 | t일에 단기 SMA > 장기 SMA이면 목표 "보유", 아니면 "현금". 목표가 현재 상태와 다르면 다음 거래일 종가에 전량 매수(칸 현금 전부) 또는 전량 매도. 장기 SMA를 구할 수 있는 날(기간 안 `long`번째 거래일)부터 판단한다(기간 앞 데이터는 쓰지 않는다). 첫 판단일에 이미 단기 > 장기면 다음 날 매수 |
| `dca` | `monthly_amount` 10,000 ~ `initial_cash`(min(1,000,000, initial_cash)) | 그 달의 첫 거래일에 매수 신호 → 다음 거래일 종가에 `monthly_amount ÷ 종목 수`(칸 현금이 모자라면 남은 현금)만큼 매수. 1주도 못 사면 건너뛰고 notice로 센다. 쓰지 않은 현금은 남는다 |

### 룩어헤드 방지와 체결·비용 가정

- **거래일 t의 신호는 t일 종가까지의 값만으로 계산하고, 체결은 t+1 거래일 종가로 한다.** 마지막 거래일의 신호는 체결되지 않는다.
  `tests/test_backtest.py`의 `TestNoLookahead`가 "k일 뒤의 가격을 바꿔도 k일까지의 신호·거래·자산 곡선이 그대로"인지 3전략 × 3시점으로 확인한다.
- 여러 종목은 **모든 종목의 종가가 있는 날만** 거래일로 쓴다(공통 달력). 빠진 날 수는 notice로 알린다.
- **여러 종목**: 초기 자금을 종목 수로 똑같이 나눈 칸마다 따로 운용한다. 칸 사이에 현금을 옮기지 않고 리밸런싱하지 않는다.
- **정수 주식 수**: 매수 수량 = floor(쓸 금액 ÷ (체결가 × (1 + 수수료율))). 남는 금액은 현금(이자 없음).
- **비용**: 매수 비용 = 수량 × 체결가 × (1 + 수수료율), 매도 수령액 = 수량 × 체결가 × (1 − 수수료율 − 매도세율).
- **기간 끝**: 강제로 팔지 않고 마지막 종가로 평가한다(매도 비용 미반영).
- 호가 단위, 거래량 제약, 슬리피지, 배당은 반영하지 않는다. 시세는 FinanceDataReader의 **수정주가**다.
- 장 마감(평일 15:30 KST) 전에는 오늘 날짜 행을 쓰지 않는다(브리핑·성과 추적과 같은 규칙).
- 응답의 `assumptions[]`에 위 가정이 문장으로 들어 있다.

### 기간 조정과 데이터 부족

- 요청 기간이 보유 데이터보다 길면 **모든 종목의 종가가 있는 구간으로 조정**하고 `period.adjusted: true`, 실제 `period.start`/`period.end`,
  notice("요청 기간(…) 중 모든 종목의 종가가 있는 구간(…)으로 계산했습니다")로 밝힌다. 조용히 줄이지 않는다.
- 공통 거래일이 **30일 미만이면 400**과 이유(종목별 보유 시세 범위 포함)로 거부한다. 400인 이유: 요청 값은 규칙에 맞지만(그래서 422가 아니다)
  데이터로 처리할 수 없는 경우라, 진단의 "보유 종목 없음"·주문의 "잔고 부족"과 같은 코드를 쓴다.

```json
{ "detail": "요청 기간에 모든 종목의 종가가 있는 거래일이 8일이라 백테스트할 수 없습니다(최소 30일). 보유 시세: 005930 2025-06-02~2025-06-13." }
```

### 지표 정의

| 필드 | 정의 |
| --- | --- |
| `metrics.total_return_pct` | (마지막 자산 ÷ 초기 자금 − 1) × 100 |
| `metrics.cagr_pct` | ((마지막 자산 ÷ 초기 자금)^(365.25 ÷ 첫날~마지막 날 달력 일수) − 1) × 100 |
| `metrics.max_drawdown` | 자산 곡선에서 min(자산 ÷ 그때까지 최고 자산 − 1) × 100과 고점·저점·회복일(없으면 null)·고점→저점 거래일 수 |
| `metrics.annualized_volatility_pct` | 일간 자산 등락률(%)의 표본 표준편차 × √252. 수익률이 2개 미만이면 null |
| `metrics.sharpe_like` | 일간 등락률 평균 ÷ 표본 표준편차 × √252. **무위험 수익률 0 가정**(정식 Sharpe 비율이 아니다). 표준편차가 0이면 null |
| `metrics.trade_count` / `buy_count` / `sell_count` | 체결 수 |
| `metrics.fees_paid` / `taxes_paid` / `ending_cash` | 낸 수수료·세금 합, 마지막 날 현금 |
| `benchmark` | 같은 기간 코스피(KS11) 매수 후 보유(비용 없음): 기간 안 첫 지수 종가 → 마지막 지수 종가. `excess_return_pct` = 전략 총수익률 − 지수 수익률(%p). 지수가 기간을 다 덮지 못하면 `covers_full_period: false`와 notice, 지수가 없으면 null |

### 응답 200

| 필드 | null | 설명 |
| --- | --- | --- |
| `generated_at` | 아니오 | 계산 시각(KST) |
| `input` | 아니오 | 요청 요약: `tickers[]`{`ticker`, `name`}, `strategy`, `params`(기본값을 채운 값), `start`, `end`, `initial_cash`, `fee_rate`, `sell_tax_rate` |
| `period` | 아니오 | `requested_start`, `requested_end`, 실제 `start`, `end`, `trading_days`, `adjusted` |
| `metrics` | 아니오 | 위 표. `total_return_pct`, `cagr_pct`, `annualized_volatility_pct`, `sharpe_like`는 null 가능 |
| `per_ticker[]` | 아니오 | 칸별 `start_value`, `final_value`, `return_pct`(null 가능), `quantity`(마지막 보유), `trade_count` |
| `benchmark` | 예 | 위 표 |
| `curve` | 아니오 | `points[]`{`date`, `total_value`, `cash`}, `total_points`, `sampled`, `max_points`(400). 400점을 넘으면 고르게 줄이되 첫·마지막·최고·최저·MDD 고점/저점은 남긴다 |
| `trades` | 아니오 | `items[]`{`ticker`, `side`, `signal_date`, `date`(체결일 = 신호 다음 거래일), `quantity`, `price`, `amount`, `fee`, `tax`, `cash_after`(그 칸의 현금)}, `total`, `truncated`, `max_items`(500) |
| `assumptions[]` | 아니오 | 체결·비용 가정 문장 |
| `data_warnings[]` | 아니오 | 쓰인 구간의 시세 경고: `ticker`, `name`, `kind`(`jump` / `non_positive` / `flat_run`), `date`, `detail`, `value` |
| `notices[]` | 아니오 | 기간 조정, 공통 거래일에서 뺀 날, 적립 건너뜀, 거래 없음, 벤치마크 부분 비교 |
| `disclaimer` | 아니오 | 기존 면책 문구 + "백테스트 결과는 과거 데이터로 계산한 가상의 성과이며 미래 수익을 보장하지 않습니다." |

**데이터 경고(`data_warnings`) 규칙** (`app/price_quality.py`, 백필 점검과 같은 함수):
- `jump`: 직전 거래일 종가 대비 |등락률| > 30.5%(가격제한폭 30% + 수정주가 반올림 여유 0.5%p). 액면분할·병합 같은 가격 불연속이거나,
  레버리지 ETF처럼 제한폭이 넓은 상품의 실제 급등락일 수 있다(판단하지 않고 알리기만 한다).
- `flat_run`: 같은 종가가 5거래일 이상 이어짐(거래정지 중 같은 값으로 채운 행일 가능성).
- `non_positive`: 종가 0 이하.

예시(captest 계정, 2026-10-29 15:41, 일부 생략):

```json
{
  "input": {"tickers": [{"ticker": "005930", "name": "삼성전자"}, {"ticker": "069500", "name": "KODEX 200"}],
            "strategy": "sma_cross", "params": {"short": 20, "long": 60}, "start": "2023-11-01", "end": "2026-10-28",
            "initial_cash": 10000000.0, "fee_rate": 0.00015, "sell_tax_rate": 0.0018},
  "period": {"requested_start": "2023-11-01", "requested_end": "2026-10-28", "start": "2023-11-01", "end": "2026-10-28",
             "trading_days": 727, "adjusted": false},
  "metrics": {"final_value": 27515439.87, "total_return_pct": 175.15, "cagr_pct": 40.29,
              "max_drawdown": {"pct": -40.9, "peak_date": "2026-06-18", "trough_date": "2026-10-15", "recovery_date": null, "duration_days": 79},
              "annualized_volatility_pct": 36.69, "sharpe_like": 1.142, "trade_count": 22, "buy_count": 11, "sell_count": 11,
              "fees_paid": 25826.17, "taxes_paid": 170897.97, "ending_cash": 27515439.87},
  "per_ticker": [{"ticker": "005930", "name": "삼성전자", "start_value": 5000000.0, "final_value": 14511298.15, "return_pct": 190.23, "quantity": 0, "trade_count": 12},
                 {"ticker": "069500", "name": "KODEX 200", "start_value": 5000000.0, "final_value": 13004141.72, "return_pct": 160.08, "quantity": 0, "trade_count": 10}],
  "benchmark": {"index_code": "KS11", "index_name": "코스피", "start_date": "2023-11-01", "end_date": "2026-10-28",
                "return_pct": 161.23, "excess_return_pct": 13.92, "covers_full_period": true},
  "curve": {"points": [{"date": "2023-11-01", "total_value": 10000000.0, "cash": 10000000.0}, "..."], "total_points": 727, "sampled": true, "max_points": 400},
  "trades": {"items": [{"ticker": "005930", "side": "buy", "signal_date": "2024-01-26", "date": "2024-01-29", "quantity": 67,
                        "price": 74400.0, "amount": 4984800.0, "fee": 747.72, "tax": 0.0, "cash_after": 14452.28}, "..."],
             "total": 22, "truncated": false, "max_items": 500},
  "assumptions": ["신호는 그날 종가까지의 데이터로만 계산하고, 체결은 다음 거래일 종가로 합니다. ...", "..."],
  "data_warnings": [], "notices": [],
  "disclaimer": "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. ... 백테스트 결과는 과거 데이터로 계산한 가상의 성과이며 미래 수익을 보장하지 않습니다."
}
```

### 에러

| 상태 | 언제 |
| --- | --- |
| 401 | 토큰 없음/무효. 본문 검증보다 먼저다 |
| 422 | 위 요청 규칙 위반(전략 파라미터는 `loc: ["body", "params", 키]`) |
| 429 | 사용자당 분당 10회 초과(`RATE_LIMIT_BACKTEST_PER_MIN`) |
| 404 | 종목을 찾지 못함(후보 안내 포함) |
| 400 | 공통 거래일 30일 미만(이유 포함) |

### 처리 시간 (2026-10-29 측정, 로컬 dev DB, TestClient)

최악 입력(5종목: 005930·000660·069500·035420·122630, 5년 요청 → 보유 데이터 729거래일로 조정): 전략별 5회 중앙값
buy_and_hold 35ms, sma_cross(5/250) 41ms, dca 33ms, 최대 72ms. 응답 크기 약 30~34KB.

### 한계

- **과최적화 위험**: 파라미터를 바꿔 가며 좋은 결과를 고르면 과거 데이터에만 맞는 설정이 된다. 표본은 약 3년(2023-10-30~)뿐이다.
- **생존 편향**: 종목 목록은 현재 시가총액 상위 300개와 ETF 50개다. 그 사이 상장폐지되거나 순위에서 빠진 종목은 없어서 결과가 좋게 나오기 쉽다.
- **단순 비용 모델**: 수수료·세금은 고정 비율 가정값이고, 슬리피지·호가 단위·거래량 제약·배당·세금 세부 규칙은 반영하지 않는다.
- 종가 체결이라 장중 가격으로 체결할 수 없고, 다음 날 종가가 신호 시점 가격과 크게 다를 수 있다.
- 벤치마크는 코스피 지수(배당 미포함 가격지수)이고, 전략 자산에도 배당이 없다.
- 리밸런싱, 손절, 공매도, 레버리지, 종목 간 현금 이동은 없다.

---

## GET /api/portfolio/exposure (2026-10-30)

내 모의투자 **보유 개별 종목과 보유 ETF의 구성종목을 합쳐 기업별로 실제로 얼마나 노출돼 있는지**(실질 노출) 계산한다. ETF 때문에 숨은 집중과 겹침을 보여준다.
**인증 필요**(없거나 잘못된 토큰은 401, 쿼리 검증 오류(422)보다 먼저), 소유자는 토큰의 사용자뿐이다. **읽기 전용, DB만 읽는다**(외부 요청·LLM·저장 없음, 계좌를 만들지 않는다).
쿼리는 보유 종목 수와 무관하게 **고정 3개**다(보유+종목 1, 종목별 최근 종가 2개 1, 분해할 ETF의 최신 구성종목+company 매핑 1; 보유가 없으면 1개).
레이트 리밋은 없다(성과 추적·브리핑 같은 기존 조회 API와 같다). 계산은 `app/exposure.py`(순수), DB 조회는 `app/exposure_db.py`.

### 요청

| 쿼리 | 기본 | 범위 | 설명 |
|---|---|---|---|
| `limit` | 10 | 1~50 (밖은 **422**) | `companies`(실질 비중이 큰 기업)를 몇 개까지 보여줄지. `overlaps`·`flags`·`summary`는 `limit`과 무관하게 전체 기준이다 |

### 보유가 없을 때

**200 + 빈 목록 + `message`**다(성과 추적·브리핑 같은 읽기 전용 조회 API와 같은 방식). 보유가 없으면 400을 주는 것은 AI를 호출하는 `POST /api/portfolio/diagnosis`뿐이다.
`message`는 `"보유 중인 종목이 없습니다. 모의투자로 종목이나 ETF를 매수하면 기업별 실질 노출을 계산합니다."`이고 숫자는 0, 목록은 `[]`, `valuation_date`는 `null`이다.
보유 종목 전부의 최신 종가가 없으면 `"보유 종목의 최신 종가를 구하지 못해 실질 노출을 계산할 수 없습니다."`(200).

### 계산식

- **보유 평가금액** = `round(수량 x 최신 종가, 2)`. 최신 종가는 DB의 종목별 마지막 종가이고 장 마감 전(평일 15:30 KST)에는 오늘 행(장중 값)을 빼고 직전 종가를 쓴다(성과 추적·브리핑과 같은 규칙).
  **분모(`전체 보유 평가금액`)는 보유 종목의 평가금액 합계이고 현금은 넣지 않는다**(계좌 총자산 대비가 아니다). 종가가 없는 종목은 평가에서 빼고 `data_warnings`(`no_price`)에 남긴다.
- **개별 주식**은 평가금액 전부가 그 기업의 직접 노출이다.
- **데이터가 있는 일반 ETF**는 `ETF 평가금액 x 구성종목 비중% / 100`을 그 구성종목에 배분한다(ETF 경유 노출). 가장 최근 기준일의 구성종목만 쓴다.
  같은 기업의 직접 노출과 ETF 경유 노출(여러 ETF, 같은 ETF 안의 중복 행 포함)은 합산한다.
- **기업 묶기**: 구성종목 코드가 `company.ticker`와 일치하면 그 종목코드로 묶는다(`linked=true`, 직접 보유한 같은 종목과 자동으로 합쳐진다).
  코드가 없거나 `company`에 없는 구성종목(해외 종목, 선물 등)은 **이름(공백·대소문자 무시)으로 묶고 `linked=false`**로 표시하며 `code`는 운용사가 준 표기(`NVDA US EQUITY` 등, 없으면 `null`)다.
  연결 안 된 구성종목은 이름이 같아도 `company`의 종목과 합치지 않는다.
- **양(+) 비중만 기업에 배분**하고, 그 ETF 평가금액에서 배분한 금액을 뺀 나머지가 **기타(현금·파생 등)**다(`summary.other_amount`). 양의 비중 합계가 100%보다 작으면 기타는 양수,
  100%보다 크면 **기타가 음수**다(커버드콜의 매도 옵션·스왑처럼 음수 비중으로 공시된 포지션 때문일 수 있다). 음수 비중 구성종목은 기업 노출로 잡지 않는다.
- **비중 합계 경고**(`data_warnings`): 양의 비중 합계가 **100.5% 초과**면 `etf_weight_over_100`(배분은 공시 비중 그대로 하고 기타가 음수가 됨을 알림), **95% 미만**이면 `etf_weight_under_100`(나머지는 기타로 둠).
  100.0~100.5%는 공시 반올림 수준이라 경고하지 않는다(예: TIGER 200의 합계 100.09%는 기타 -0.09%로 잡힌다). 보유 종목들의 종가 날짜가 다르면 `older_price` 경고.
- **합계 불변식**: `모든 기업의 합계 금액(summary.companies_amount) + 기타(other_amount) + 분해하지 않은 ETF(undecomposed_etf_amount) = 전체 보유 평가금액(total_eval_amount)`.
  계산은 반올림하지 않은 값으로 하고 출력만 소수 둘째 자리로 반올림하므로, **반올림된 항목을 더한 값과 전체 평가금액의 차이는 항목 수 x 0.005원 이내**다(`summary`의 값끼리는 0.02원 이내).
  `companies`는 `limit`개만 보여주므로 목록을 더해서는 맞출 수 없다(`summary.company_count`가 전체 기업 수). 맞춰 볼 때는 `summary`의 값을 쓴다.
- **정렬**: 실질 금액 큰 순, 같으면 이름 순. `rank`는 1부터.
- **플래그(`flags`)**: ① `concentration` - 실질 비중이 **40% 이상**(브리핑·진단의 집중 기준과 같은 값), ② `etf_inflates_weight` - ETF 경유 때문에 실질 비중이 직접 비중보다 **10%p 이상** 커진 기업(`value_pct` = ETF 경유 비중).
  `limit`과 무관하게 모든 기업을 검사한다. 문구는 관찰 사실만 담는다(투자 권유 없음).

### 분해하지 않는 ETF와 이유 (`undecomposed_etfs`)

평가금액과 이유를 따로 모으고, 기업 노출·기타에는 넣지 않는다(전체 평가금액에는 포함).

| `reason_code` | 대상 | 이유 |
|---|---|---|
| `no_holdings_data` | 구성종목 데이터가 없는 ETF(RISE·ACE 등 미지원 운용사, 아직 수집 안 된 KODEX 등) | 모르는 것을 지어내지 않는다 |
| `leveraged_inverse` | 이름으로 판별한 레버리지·인버스 ETF(채팅의 레버리지 안내와 같은 판정 `app.etf.leverage_kind`) | 배수와 선물·스왑 구조 때문에 구성종목 비중을 그대로 곱하면 실제 노출을 오해하게 한다. 구성종목 데이터가 있어도 분해하지 않는다 |

### 응답 200 (2026-10-30 실제 응답, dev DB, captest 계정에 삼성전자 10주 + TIGER 200 30주, `limit=3`; 일부 필드는 `...`로 생략)

```json
{
  "valuation_date": "2026-10-28", "stale": true, "etf_holdings_stale": false, "limit": 3, "message": null,
  "summary": {"total_eval_amount": 5306150.0, "holding_count": 2, "stock_amount": 2450000.0, "etf_amount": 2856150.0,
              "decomposed_etf_amount": 2856150.0, "undecomposed_etf_amount": 0.0, "companies_amount": 5308720.54,
              "other_amount": -2570.54, "other_weight_pct": -0.05, "company_count": 194},
  "companies": [
    {"rank": 1, "name": "삼성전자", "code": "005930", "linked": true, "direct_amount": 2450000.0, "via_etf_amount": 1007935.33,
     "via_etfs": [{"ticker": "102110", "name": "TIGER 200", "amount": 1007935.33, "etf_weight_pct": 35.29}],
     "total_amount": 3457935.33, "effective_weight_pct": 65.17, "direct_weight_pct": 46.17},
    {"rank": 2, "name": "SK하이닉스", "code": "000660", "linked": true, "direct_amount": 0.0, "via_etf_amount": 754880.44, "...": "...",
     "total_amount": 754880.44, "effective_weight_pct": 14.23, "direct_weight_pct": 0.0},
    {"rank": 3, "name": "SK스퀘어", "code": "402340", "...": "...", "total_amount": 75687.98, "effective_weight_pct": 1.43, "direct_weight_pct": 0.0}
  ],
  "overlaps": [{"name": "삼성전자", "direct_amount": 2450000.0, "via_etf_amount": 1007935.33, "total_amount": 3457935.33, "effective_weight_pct": 65.17, "...": "..."}],
  "decomposed_etfs": [{"ticker": "102110", "name": "TIGER 200", "eval_amount": 2856150.0, "weight_pct": 53.83, "holdings_as_of": "2026-10-29",
                       "stale": false, "stale_notice": null, "constituent_count": 200, "weight_sum_pct": 100.09}],
  "undecomposed_etfs": [],
  "flags": [
    {"kind": "concentration", "name": "삼성전자", "code": "005930", "value_pct": 65.17, "threshold_pct": 40.0,
     "message": "삼성전자의 실질 비중이 보유 평가금액의 65.17%입니다(기준 40% 이상)."},
    {"kind": "etf_inflates_weight", "name": "삼성전자", "code": "005930", "value_pct": 19.0, "threshold_pct": 10.0,
     "message": "삼성전자는 ETF를 통한 노출 때문에 실질 비중(65.17%)이 직접 비중(46.17%)보다 19.00%p 큽니다(기준 10%p 이상)."},
    {"kind": "etf_inflates_weight", "name": "SK하이닉스", "...": "..."}
  ],
  "data_warnings": [],
  "disclaimer": "실질 노출은 보유 평가금액(종가 기준)과 ETF 구성종목 비중으로 계산한 참고 값이며 투자 판단 보조용입니다. 구성종목과 비중은 운용사가 공시한 기준일의 값이라 현재와 다를 수 있습니다. 이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. ..."
}
```

### 필드와 null 여부

| 필드 | 설명 |
|---|---|
| `generated_at` | 응답 생성 시각(KST ISO) |
| `valuation_date` | **보유 평가 기준일** = 보유 종목들의 최신 종가 날짜 중 가장 늦은 것. 보유가 없거나 종가가 없으면 `null` |
| `stale` / `stale_notice` | 평가 기준일이 최근 영업일보다 2영업일 이상 뒤처졌으면 `true`와 안내(`/api/status`와 같은 규칙). 아니면 `false` / `null` |
| `etf_holdings_stale` | 분해한 ETF 중 구성종목 기준일이 2영업일 이상 오래된 것이 하나라도 있으면 `true` |
| `message` | 보유가 없거나 종가가 전혀 없을 때만 문장, 아니면 `null` |
| `limit` | 적용된 `limit` |
| `summary.*` | 모두 숫자(null 없음). `total_eval_amount`(분모), `holding_count`(평가에 포함된 보유 수), `stock_amount`/`etf_amount`, `decomposed_etf_amount`/`undecomposed_etf_amount`, `companies_amount`(전체 기업 합), `other_amount`/`other_weight_pct`(음수일 수 있음), `company_count`(전체 기업 수) |
| `companies[]` | 상위 `limit`개. `rank`, `name`, `code`(연결된 종목은 종목코드, 연결 안 된 구성종목은 운용사 표기이거나 `null`), `linked`, `direct_amount`, `via_etf_amount`, `via_etfs[]`(`ticker`, `name`, `amount`, `etf_weight_pct`: 그 ETF의 구성종목 비중), `total_amount`, `effective_weight_pct`(= 합계/전체 평가금액), `direct_weight_pct` |
| `overlaps[]` | 직접 보유와 ETF 경유 노출이 **모두** 있는 기업 전체(`limit` 무관, `companies[]`와 같은 필드) |
| `decomposed_etfs[]` | 분해한 ETF: `ticker`, `name`, `eval_amount`, `weight_pct`(전체 대비), `holdings_as_of`(**구성종목 기준일**), `stale`, `stale_notice`(stale일 때만, 아니면 `null`), `constituent_count`, `weight_sum_pct`(양의 비중 합계) |
| `undecomposed_etfs[]` | 분해하지 않은 ETF: `ticker`, `name`, `eval_amount`, `weight_pct`, `reason_code`, `reason` |
| `flags[]` / `data_warnings[]` | 위 규칙. 없으면 `[]` |
| `disclaimer` | 투자 판단 보조용·공시 기준일 값이라 현재와 다를 수 있다는 문구 + 서비스 면책 |

**두 종류의 기준일**: `valuation_date`는 보유 종목의 종가 날짜(평가 기준)이고, `decomposed_etfs[].holdings_as_of`는 ETF 구성종목의 운용사 공시 기준일이다. 둘은 다를 수 있다.

### 처리 시간 (2026-10-30 측정, 로컬 dev DB, TestClient)

보유 개별 종목 10 + ETF 3(TIGER 200 구성종목 200개, TIGER 머니마켓액티브 356개, TIGER 미국S&P500 503개), 전체 기업 1,050개, 5회: **API 응답(인증·직렬화 포함) 중앙값 22.8ms, 최대 52.6ms**,
`get_exposure`만 중앙값 16.9ms(쿼리 3개). 같은 환경의 `GET /api/portfolio/performance`가 약 21ms다. 응답 크기는 `limit`과 `overlaps` 크기에만 비례한다.

### 한계

- **구성종목 데이터가 있는 ETF만 분해한다.** 2026-10-30 현재 dev DB에는 TIGER 15개(2026-10-29 기준)뿐이고 KODEX 23개는 수집되지 않았으며(운용사 사이트 429), RISE·ACE·SOL·HANARO·PLUS·TIME은 수집 대상이 아니다. 그 ETF는 `undecomposed_etfs`(`no_holdings_data`)로 나온다.
- **해외 종목은 `company`와 연결되지 않는다**(`linked=false`, 이름으로만 묶음). 같은 회사가 ETF마다 다른 영문 표기로 공시되면 서로 합쳐지지 않고, 국내 종목이라도 코드가 없는 구성종목은 직접 보유 종목과 합쳐지지 않는다.
- **구성종목 기준일 지연**: 운용사가 공시한 기준일의 값을 수동으로 수집한 것이라 현재와 다를 수 있다. `holdings_as_of`와 `stale`을 같이 보여줘야 한다.
- **구성종목 비중은 공시 값**이다(합계가 100%와 조금 다르거나, 채권형·머니마켓 ETF처럼 크게 낮을 수 있음). 모자란 만큼은 기타로 둔다.
- **평가는 종가 기준**이다(실시간 아님). 장중에는 오늘 행을 빼고 직전 종가를 쓰며, 종목마다 종가 날짜가 다를 수 있다(`older_price` 경고).
- **업종별 노출은 제공하지 않는다**: `company.sector`가 전 종목 비어 있어(주식 300개 모두 NULL) 이 필드를 만들지 않았다. 업종 컬럼이 채워지면 추가할 수 있다.
- 현금은 분모에 넣지 않는다. 레버리지·인버스 ETF의 실제 노출(배수)은 계산하지 않는다. ETF를 구성종목으로 담은 ETF는 그 ETF를 하나의 구성종목으로 둔다(더 풀지 않는다).

---

## ETF 실질 노출(look-through)과 포트폴리오 집중도 (2026-11-06)

화면용 상세(필드 표, 상태 처리, 추천 화면, 합성 응답 사례)는 `docs/FRONTEND_HANDOFF_ETF_EXPOSURE.md`. 여기는 규칙과 계약만 적는다.

**새 라우트 없음.** `GET /api/portfolio/exposure?view=look_through`(계산 `app/look_through.py`, 문장 `app/look_through_i18n.py`, DB `app/exposure_db.get_look_through`·`load_constituents`)와 진단 결과의 `look_through`, 진단 비교의 `exposure_changes`로 제공한다. `view`가 없으면(`default`) 위 "GET /api/portfolio/exposure" 응답 그대로다(필드·값 불변).

**조사 결과(기존 `app/exposure.py`)**: 보유 평가액 대비 비중(현금 제외), 양수 비중만 배분, 레버리지·인버스와 구성 데이터 없는 ETF는 분해 안 함, 비중 합계 95% 미만·100.5% 초과 경고는 이미 정확했다. 부족했던 것: 총자산(현금 포함) 대비 비중이 없음, 다른 ETF를 담은 ETF의 하위 ETF(dev DB 12행, 7개 ETF: TIGER 배당커버드콜액티브 안의 TIGER 200 13.93% 등)를 기업처럼 합침, ETF별 구성종목 기여·품질 값이 없음, 진단 저장본에 노출이 없어 `exposure_changes`가 코드에서 `None`으로 고정됨.

**etf_holding 데이터(2026-11-06 읽기 전용 점검)**: 4,926행, ETF 38개(KODEX 23·TIGER 15), 기준일 ETF마다 1개(2026-10-29 또는 10-30, 수집 2026-10-29~31). `weight_pct`는 0~100 단위 Numeric(9,4) NOT NULL(-0.14 ~ 99.92). ETF별 구성종목 1~675개, 양수 비중 합계 42.96%(TIGER KOFR금리액티브(합성))~200.18%(KODEX 레버리지), 95% 미만 3개·100.5% 초과 4개(레버리지 2개 포함). 0 이하 비중 76행(음수는 2개 ETF), 코드 없음 360행, 같은 ETF·기준일 안 코드 중복 0건. company와 연결 1,547행(그중 ETF 12행), 6자리 코드인데 연결 안 됨 337행. 같은 발행사 이름의 서로 다른 CP·전단채(ISIN 다름)가 많아 중복 판정은 이름이 아니라 코드로 한다.

**계산 규칙(`calc_version` = `look_through.v1`)**
- 평가액 = 수량 × 최신 종가(장중 행 제외, 진단에서는 진단이 쓴 현재가). 종가가 없으면 `unpriced`로 따로 두고 0원으로 치지 않는다.
- 총자산 = 현금 + 평가 가능한 직접 주식 + 평가 가능한 ETF. ETF 구성종목 금액을 총자산에 다시 더하지 않는다.
- ETF 간접 노출 = ETF 평가액 × 구성 비중 / 100(양수만). 같은 기업은 직접 보유·여러 ETF를 합산한다(연결된 종목코드로, 연결 안 되면 이름으로).
- 기업 비중 = 실질 노출 / 총자산 × 100. 100%로 정규화하지 않는다.
- 분해한 ETF 평가액 = 기업 배분 + 중첩 ETF 배분 + 잔여(비중 합계 < 100%면 양수, > 100%면 음수). 분해하지 않은 ETF는 평가액 전체가 미확인이다. 미확인 노출 = 중첩 ETF + 양수 잔여 + 분해하지 않은 ETF. 불변식: 직접 + 간접 + 중첩 + 잔여 + 분해 안 함 = 보유 평가액(테스트가 무작위 30개 입력으로 확인).
- 분해하지 않는 ETF: 레버리지·인버스(이름 판별, 배수를 곱하지 않고 공시 비중만 `constituents`에 금액 null로), 구성 데이터 없음, 비중 오류(한 종목 > 100% 또는 양수 비중 없음).
- 중첩 ETF: 구성종목이 ETF(company.asset_type = 'etf')이거나 자기 자신이면 재귀 분해하지 않고 `nested_etf_amount`로 둔다(순환·중복 집계 없음).
- 품질 경고(데이터를 고치지 않는다): null·음수 비중(뺌), 같은 종목 중복 행(합산하고 경고), 비중 합계, 연결 안 된 구성종목, 구성 기준일이 2영업일 이상 오래됨(`data_status.is_stale`).
- 구성종목의 시세는 쓰지 않는다(간접 노출은 ETF 평가액 × 공시 비중이라 구성종목 종가가 없어도 계산된다).
- 플래그(총자산 대비): 기업 실질 노출 ≥ 40%, ETF 간접 노출 ≥ 10%p, 한 ETF를 통한 한 기업 노출 ≥ 10%.
- 업종: company.sector가 있는 기업이 기업 노출의 95% 이상이면 available, 일부면 partial, 없으면 unavailable(지금은 unavailable). 업종 뉴스용 제한 분류(반도체·2차전지·전력설비)는 쓰지 않는다.

**응답(`view=look_through`)**: `calc_version, locale, status(ok|empty|no_prices), message, has_account, valuation_date, expected_date, price_lag_business_days, stale, stale_notice, etf_holdings_stale, limit, summary, companies[], overlaps[], etfs[], unpriced[], sector, flags[], data_warnings[], disclaimer`. 금액은 원(소수 둘째 자리), 비중은 %(소수 둘째 자리, 총자산 대비; `holdings_weight_pct`만 보유 평가액 대비), 구성 비중은 공시 값(%, 소수 넷째 자리). 총자산이 0이면 비중은 null. 필드별 설명은 인계 문서 3절.

**진단 연결**: `POST /api/portfolio/diagnosis`(그리고 저장본 `GET /api/portfolio/diagnosis/{id}`)에 `look_through`가 더해진다. 진단이 쓴 현재가·현금과 그때의 ETF 최신 구성종목으로 위 규칙을 계산해 요약(상위 기업 20, 겹침 20, ETF별 당시 상위 구성종목·기준일, 플래그, 경고, `holding_top1_weight_pct` = 기존 직접 보유 기준 상위 1종목 비중, `computed_at`, `valuation_basis: diagnosis_prices`)을 진단 언어로 저장한다. 기존 진단 필드의 값·의미는 그대로다. 계산이 실패해도 진단은 정상이고 `look_through.status`가 `unavailable`이다. 저장 위치는 portfolio_diagnosis.result JSON이라 **마이그레이션은 없다**(Alembic head 그대로 `5e7b9d2c4a10`). 이 기능 전 저장본은 키가 없어 `look_through: null`로 나간다(채워 넣지 않는다).

**진단 비교**: `GET /api/portfolio/diagnosis/{id}/compare?view=record`의 `exposure_changes`는 두 저장본의 `look_through`가 모두 status ok일 때만 `{calc_version, valuation_date, summary{…: {previous, current, change}}, look_through_top1, companies[{name, code, previous_total_weight_pct, current_total_weight_pct, change_pct, previous_total_amount, current_total_amount, status}], note_code: "stored_snapshots", companies_compared: "top_saved"}`. 저장본끼리 비교하며 다시 계산하지 않는다.

**인증·성능**: 로그인 필요(401), 본인 `owner_key`의 보유·계좌만 읽는다. 쿼리는 보유 수와 관계없이 6개 이하(계좌·보유·종가·ETF id·구성종목). dev DB 구성종목 4,926행 전체 읽기 35ms(쿼리 1개), 38개 ETF를 모두 가진 포트폴리오 계산 27ms(기업 2,216개).

**다국어**: 수치는 한 번 계산하고 `message`·`notice`·`stale_notice`·`sector.message`·`disclaimer`만 4개 언어 템플릿으로 쓴다(숫자·코드·기준일·상태는 언어와 무관하게 같다, 종목명은 원문).

## GET /api/etf/{ticker}/holdings (2026-10-30)

ETF의 **구성종목과 편입 비중**(운용사가 공시하는 PDF를 수집해 `etf_holding`에 저장한 값). **공개**(인증 없음) - `/api/stocks/{ticker}/...` 시세 API와 같은 정책이다.
사용자 데이터가 없고 읽기 전용이다. LLM을 쓰지 않는다. `{ticker}`는 종목코드 또는 ETF 이름(`resolve_company`가 해석, 예: `102110`, `TIGER 200`).

### 데이터 출처와 수집 (한계 포함)

| 소스 | 결과 | 비고 |
|---|---|---|
| 삼성자산운용 KODEX 홈페이지 공시 PDF (`samsungfund.com`, JSON) | **채택** | 로그인·API 키 없음, 종목명·코드·비중·기준일 제공 |
| 미래에셋 TIGER ETF 홈페이지 공시 PDF (`investments.miraeasset.com`, HTML) | **채택** | 같음. 이용약관(회원 대상)에 복제·제공 금지 조항이 있어 **외부 공개 전 운용사 허락이 필요**하다 |
| KRX 정보데이터시스템 ETF PDF, `pykrx` | 미채택 | 이제 로그인(KRX_ID/PW)이 필요해 익명 조회가 `LOGOUT`으로 막힌다 |
| 네이버 금융 ETF 구성종목 영역 | 미채택 | `robots.txt`가 `Disallow: /`, 상위 10개만 제공, 해외 종목 비중은 `-` |

- 대상은 `company`의 ETF 50개 중 **KODEX 23개 + TIGER 15개 = 38개**다. RISE·ACE·SOL·HANARO·PLUS·TIME 12개는 수집하지 않는다(그 ETF는 "수집하지 않았습니다" 응답).
- 수집은 **수동**이다(스케줄러 없음): `python -m scripts.collect_etf_holdings [--tickers 102110,069500] [--delay 3] [--dry-run] [--report 경로]`. 하루 한 번 안팎으로만 쓴다.
  요청 사이 간격(기본 3초)을 두고, 용도를 밝힌 User-Agent를 쓰며, 한 ETF가 실패해도 나머지를 계속하고(사유는 요약·`--report` JSON에 남음), 일시 오류는 한 번 더 시도한다.
  **403·429·봇 확인 화면(Cloudflare)을 받으면 그 호스트에는 더 요청하지 않고 우회하지 않는다**(`HostBlocked`). 실제로 2026-10-29 조사 중 삼성 사이트에 약 100건/15분을 보내자
  429가 나왔고, **2026-10-30에도 같은 429가 계속되어 KODEX 23개는 이 DB에 아직 수집되지 않았다**(TIGER 15개만 2026-10-29 기준으로 저장됨).
  차단이 풀린 뒤 `--tickers`로 KODEX만 다시 실행하면 된다. 저장은 (ETF, 기준일) 단위 교체라 반복 실행해도 같은 결과다(멱등).
- 테이블 `etf_holding`: `company_id`(ETF, cascade), `as_of_date`, `holding_name`, `holding_code`(NULL 가능), `weight_pct`, `source`, `collected_at`.
  (ETF, 기준일, 종목명, 종목코드) 유일 키(`NULLS NOT DISTINCT`)라서 코드가 NULL인 행도 중복되지 않는다. 마이그레이션 `f4b81d29c6e5`(업/다운 왕복 테스트 있음).
  **현금성 자산(원화예금·CASH 등)과 비중이 없는 행은 저장하지 않는다.** 기준일별 스냅샷이 쌓이고 API는 가장 최근 기준일만 보여준다.
  커버드콜의 매도 콜옵션·TRS는 운용사가 음수 비중으로 공시하므로 그대로 저장한다.

### 요청

| 쿼리 | 기본 | 범위 | 설명 |
|---|---|---|---|
| `limit` | 10 | 1~50 (밖은 **422**) | 비중이 큰 순서로 보여줄 구성종목 수 |

### 응답 200 (2026-10-30 실제 응답, dev DB, `GET /api/etf/102110/holdings?limit=3`)

```json
{
  "ticker": "102110",
  "name": "TIGER 200",
  "asset_type": "etf",
  "as_of_date": "2026-10-29",
  "collected_at": "2026-10-29T07:12:14.797394+00:00",
  "stale": false,
  "stale_notice": null,
  "source": "미래에셋자산운용 TIGER ETF 홈페이지의 구성종목(PDF)",
  "holding_count": 200,
  "total_weight_pct": 100.09,
  "top_weight_pct": 64.37,
  "limit": 3,
  "items": [
    {"rank": 1, "name": "삼성전자", "code": "005930", "weight_pct": 35.29},
    {"rank": 2, "name": "SK하이닉스", "code": "000660", "weight_pct": 26.43},
    {"rank": 3, "name": "SK스퀘어", "code": "402340", "weight_pct": 2.65}
  ],
  "data_warnings": [],
  "message": null,
  "disclaimer": "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. ..."
}
```

| 필드 | 설명 |
|---|---|
| `as_of_date` | 운용사가 공시한 기준일(YYYY-MM-DD). 데이터가 없으면 `null` |
| `collected_at` | 이 서비스가 수집한 시각(ISO). 운용사 공시 시각이 아니다 |
| `stale` / `stale_notice` | 기준일이 최근 영업일보다 2영업일 이상 뒤처지면 `true`와 안내 문장(`/api/status`의 시세 stale과 같은 규칙, 휴장일 달력은 없음) |
| `source` | 출처 문구 |
| `holding_count` | 그 기준일의 저장된 구성종목 수(현금성 제외, `limit`과 무관) |
| `total_weight_pct` | 저장된 **전체** 구성종목의 비중 합계(%, `limit`과 무관). 데이터가 없으면 `null` |
| `top_weight_pct` | `items`(상위 `limit`개)의 비중 합계 |
| `items[].rank` / `name` / `code` / `weight_pct` | 순위(1부터), 구성종목명, 종목코드(**없으면 `null`**, 해외 종목은 `NVDA US EQUITY` 같은 운용사 표기), 비중(%) |
| `data_warnings[]` | `{kind, message}`. 합계가 기대값(보통 100%, 레버리지는 200%)에서 5%p를 넘게 벗어나면 `kind="weight_sum"` 경고. 정상이면 빈 배열 |
| `message` | 데이터가 없을 때만 사유(`items=[]`) |
| `disclaimer` | 고정 면책 문구 |

비중 합계가 100%에서 조금 벗어나는 것은 정상이다(예: 100.09, 99.54): 현금성 자산을 뺐고 운용사가 소수 둘째 자리로 반올림해 공시한다. 머니마켓·CD금리 같은 채권형 ETF는
편입 자산이 PDF에 종목으로 다 잡히지 않아 합계가 크게 낮을 수 있고(2026-10-29 실제: TIGER CD금리투자KIS 79.89%, KOFR금리액티브 42.88%) 이때 `data_warnings`에 경고가 나온다.

### 데이터가 없는 ETF (200, 404가 아님)

`price-history`가 주가가 아직 없을 때 200 + 빈 목록을 주는 것과 같은 방식이다: `items=[]`, `holding_count=0`, `as_of_date`/`total_weight_pct`는 `null`, `message`에 사유.

- 미지원 운용사(예: RISE 200): `"이 ETF의 구성종목·비중 데이터는 수집하지 않았습니다. 현재 삼성 KODEX와 미래에셋 TIGER ETF만 운용사 공시 PDF에서 수집합니다."`
- KODEX·TIGER인데 수집 전(예: 2026-10-30 현재 KODEX 200): `"이 ETF의 구성종목·비중 데이터는 아직 수집되지 않았습니다(운용사 공시 PDF 수집 전)."`

### 에러

| 상태 | 경우 |
|---|---|
| 404 | 종목을 찾지 못함(`detail`에 후보 안내), **ETF가 아닌 종목**(`"삼성전자는 ETF가 아니라 구성종목 비중을 제공하지 않습니다."`) |
| 422 | `limit`이 1~50 밖 |

### 한계

- **지연**: 운용사는 영업일마다 공시하지만 이 서비스는 사람이 수집해야 갱신된다. `as_of_date`와 `collected_at`을 항상 같이 보여주고 `stale`을 확인하라.
- **커버리지**: 50개 ETF 중 KODEX·TIGER 38개만 대상이고, 그중에서도 실제 수집에 성공한 것만 데이터가 있다. 2026-10-30 현재 TIGER 15개만 있다.
- 해외 종목은 코드가 `AAPL US EQUITY` 같은 블룸버그식 표기이거나 `null`이고, 종목명은 운용사 공시 그대로(영문)다. 같은 회사가 `company` 테이블의 종목과 자동 연결되지 않는다.
- 선물·스왑으로 구성된 ETF(KODEX 레버리지·인버스 등)는 "구성종목"이 주식이 아니라 지수선물·스왑으로 나온다. 레버리지는 비중 합계가 200% 안팎이다.
- 채팅 답변은 한 번에 최대 10개까지만 보여준다(질문이 더 많이 요청하면 그렇게 알린다). API는 `limit` 최대 50개다. 한 번에 최근 기준일 하나만 반환한다(과거 기준일 조회·비교 없음).
- 쿼리 수는 종목 해석 + 최신 기준일 + 상위 N개 + 전체 합계로 고정이다(구성종목 수와 무관).

---

## 기록 관리: 날짜별 목록·검색·통합 검색 (2026-11-05)

리서치 기록과 포트폴리오 진단 이력을 날짜별로 보고, 종목명·종목코드·키워드·날짜로 찾고, 당시 결과를 다시 여는 기능이다.
**새 테이블·컬럼·마이그레이션은 없다.** 기존 `research_report`·`tool_call_log`·`portfolio_diagnosis`를 읽기만 한다(조회·검색이 어떤 행도 고치지 않는다).

### 하위 호환 원칙

- **파라미터를 안 주면 기존 응답이 한 글자도 바뀌지 않는다.** 새 필드는 `view=record`를 줄 때만 붙는다.
- 새 필터(`date_from`, `date_to`, `timezone`, `analysis_type`, 진단 이력의 `q`)는 `view`와 관계없이 동작한다.
- `GET /api/research`의 기존 `q` 의미(질문·요약·리포트 종목명·종목코드 부분 일치)는 그대로다. 답변 전문·여러 종목·별칭까지 찾는 넓은 검색은 통합 검색(`GET /api/history/search`)을 쓴다.

### 날짜·시간대 규칙 (세 API 공통)

| 항목 | 규칙 |
| --- | --- |
| 저장 | `created_at`은 DB에 UTC로 저장된 그대로 응답한다(`+00:00`) |
| `timezone` | IANA 이름(`Asia/Seoul`, `America/New_York`, `UTC` …). 없거나 공백이면 `Asia/Seoul`. 모르는 이름·64자 초과는 422 |
| `date_from` / `date_to` | `YYYY-MM-DD`, **`timezone`의 달력 날짜**. `date_to`는 그날 끝까지 포함(다음 날 0시 미만). 하나만 줘도 된다. `date_from > date_to`면 422, 없는 날짜(`2026-02-30`)도 422 |
| `local_date` / `local_time` | `timezone` 기준 작성 날짜(`2026-11-05`)와 시각(`10:00`) |
| `date_group` | `timezone`의 오늘 기준 달력 날짜 차이: `today`(0일) · `yesterday`(1일) · `last_7_days`(2~6일) · `last_30_days`(7~29일) · `older`(30일 이상). 미래 시각은 `today` |

예: UTC `2026-11-04T15:00:00+00:00`은 서울 기준 `2026-11-05 00:00`(today), UTC 기준 `2026-11-04`(yesterday)다.

### 검색 낱말 규칙 (통합 검색 `keyword`, 진단 이력 `q`)

- 공백으로 나눈 낱말(최대 5개)이 **모두** 들어 있어야 한다(AND). `"ETF 중복"` = "ETF"와 "중복"이 각각 들어 있음.
- 대소문자 무시 부분 일치. `%`, `_`, 백슬래시는 글자 그대로다(`"삼성%전자"`는 "삼성전자"를 찾지 않는다). 검색어는 바인딩 파라미터로만 전달된다.
- 종목 별칭은 등록명으로도 찾는다(`삼전` → 삼성전자, `하닉` → SK하이닉스, `app/company_aliases.py`).
- 리서치에서 찾는 곳: 질문·요약·**답변 전문**·리포트 종목(이름·코드)·tool 결과에 나온 종목(이름·코드). "ETF"가 들어간 낱말은 ETF를 조회한 리포트도 찾는다.
- 진단에서 찾는 곳: 요약·강점·위험·제안·플래그 문장·보유 종목명·종목코드. 플래그가 없을 때의 고정 문장("규칙 기준으로 확인된 위험 항목은 없습니다.")은 검색 대상에서 뺐다.
- 위험 키워드는 진단 플래그로도 찾는다(플래그 문장에는 "집중도" 같은 낱말이 없어서다):

| 낱말에 들어 있으면 | 찾는 플래그 |
| --- | --- |
| 집중, 쏠림, 편중, concentration | `CONCENTRATION_HIGH`, `MARKET_SKEW` |
| 분산 | `CONCENTRATION_HIGH`, `FEW_HOLDINGS` |
| 종목수 | `FEW_HOLDINGS` |
| 현금, cash | `CASH_HIGH`, `CASH_LOW` |
| 손실, 손익, loss | `BIG_LOSS` |
| 레버리지, 인버스, leverage | `LEVERAGED_INVERSE_ETF` |
| 위험, 리스크, risk | 플래그가 하나라도 있는 진단 |

### `GET /api/research` 추가 파라미터

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `date_from`, `date_to`, `timezone` | 없음, 없음, `Asia/Seoul` | 위 날짜 규칙 |
| `analysis_type` | 없음 | `outlook`, `event`, `briefing`, `discovery`, `etf`, `disclosure`, `news`, `price`, `market`, `general` 중 하나. 그 유형이 `analysis_types`에 있는 리포트만 |
| `view` | `default` | `record`면 아래 필드와 응답의 `timezone`을 붙인다. 그 밖의 값은 422 |

`analysis_type`은 저장된 tool 이력으로 코드가 정한다(LLM 없음): `outlook` = 전망 경로, `event` = 사건 반응, `briefing` = 채팅 브리핑, `discovery` = 급등락 탐색,
`etf` = ETF를 조회함, `disclosure` = 공시, `news` = 뉴스·문서 검색, `price` = 주가, `market` = 시장 지수·환율, `general` = 아는 tool을 하나도 쓰지 않음(개념 설명 등).
한 리포트가 여러 유형일 수 있고 `analysis_type`은 이 순서에서 처음 걸린 값이다.

`view=record` 항목에 붙는 필드(기존 필드는 그대로):

| 필드 | 설명 |
| --- | --- |
| `title` | 질문을 공백 정리해 앞 60자(제목 컬럼이 없어 질문에서 만든다) |
| `company_names`, `tickers` | 리포트 종목 + tool 결과에 나온 종목(순서 유지, 중복 없음). `company_name`(정확히 1종목일 때만)과 달리 여러 종목이면 모두 |
| `analysis_type`, `analysis_types` | 위 분석 유형 |
| `source_count` | 뉴스·공시 출처 수. 상세 `sources`와 같은 규칙으로 중복 제거 |
| `local_date`, `local_time`, `date_group` | 위 날짜 규칙 |

`GET /api/research/{report_id}?view=record`는 같은 필드에 `data_basis`(당시 tool 결과에 남은 기준일: `period_start_date`, `period_end_date`, `base_date`, `latest_date`, `data_date`, `price_as_of`, `as_of` 중 있는 것)와 `timezone`을 더한다.
답변·출처는 저장된 그대로이고 현재 시세·뉴스로 다시 만들지 않는다. 뉴스·공시 제목은 원문 그대로다.

### `GET /api/portfolio/diagnosis/history` 추가 파라미터

`q`(최대 50자, 위 검색 낱말 규칙), `date_from`, `date_to`, `timezone`, `view`. `total`도 조건을 적용한 건수다.
`view=record` 항목에 붙는 필드: `title`("삼성전자 외 2종목 진단", 저장된 보유 종목으로 만듦), `cash_weight_pct`, `top_holdings`(비중 상위 3종목: `ticker`, `company_name`, `weight_pct`),
`risk_summary`(저장된 위험 요인 첫 문장), `flag_codes`, `generated_at`(진단을 계산한 시각 = 그때 쓴 시세의 기준 시각), `local_date`, `local_time`, `date_group`. 모두 저장본의 값이다.

### `GET /api/portfolio/diagnosis/{id}/compare?view=record`

기존 응답(`current`, `previous`, `changes`, `disclaimer`)에 다음을 더한다.

| 필드 | 설명 |
| --- | --- |
| `basis` | `current_created_at`, `current_generated_at`, `previous_created_at`, `previous_generated_at`, `recomputed`(항상 false), `note`("두 진단은 각각 저장 당시의 보유 구성과 시세로 계산한 결과입니다…", 비교 대상이 없으면 null) |
| `metric_changes` | `cash_balance`, `cash_weight_pct`, `stock_eval_amount`, `holding_count`, `top1_weight_pct`, `top3_weight_pct`, `herfindahl_index`, `total_profit_loss_pct` 각각 `{previous, current, change}`. 비중은 %p, HHI는 소수 4자리, 한쪽이 null이면 change도 null. 비교 대상이 없으면 null |
| `exposure_changes` | 두 진단 저장본 모두에 실질 노출 스냅샷(`look_through`, status ok)이 있을 때만 기업별 실질 비중 변화(아래 "ETF 실질 노출(look-through)" 절). 2026-11-06 전에 저장한 진단이 한쪽이라도 끼면 null(현재 구성종목으로 과거 진단을 다시 계산하지 않는다) |

개선된 위험·새 위험은 기존 `changes.flags.resolved` / `changes.flags.added`다.

### `GET /api/history/search` (인증 필요)

| 이름 | 기본값 | 범위 | 설명 |
| --- | --- | --- | --- |
| `type` | `all` | `all`, `research`, `diagnosis` | 탭 |
| `keyword` | 없음 | 최대 50자 | 위 검색 낱말 규칙. 없거나 공백이면 전체 |
| `date_from`, `date_to`, `timezone` | | | 위 날짜 규칙 |
| `page` | 1 | 1~10000 | |
| `size` | 20 | 1~100 | |
| `sort` | `created_at,desc` | `created_at,desc`, `created_at,asc` | 같은 시각이면 종류, 그다음 id(같은 방향). 페이지를 넘겨도 중복·누락 없음 |

```json
{
  "type": "all", "keyword": "SK하이닉스", "terms": ["SK하이닉스"], "date_from": null, "date_to": null,
  "timezone": "Asia/Seoul", "sort": "created_at,desc", "page": 1, "size": 20,
  "total": 3, "total_pages": 1, "has_next": false,
  "counts": {"all": 3, "research": 2, "diagnosis": 1},
  "items": [
    {
      "kind": "research", "id": 32500, "created_at": "2026-11-05T01:00:00+00:00",
      "local_date": "2026-11-05", "local_time": "10:00", "date_group": "today",
      "title": "SK하이닉스 전망 알려줘", "summary": "SK하이닉스 단기 방향은 상승 쪽입니다.",
      "company_names": ["SK하이닉스"], "tickers": ["000660"], "detail_path": "/api/research/32500",
      "research": {"report_id": 32500, "previous_report_id": null, "question": "SK하이닉스 전망 알려줘",
                   "analysis_type": "outlook", "analysis_types": ["outlook", "news", "price"], "source_count": 2,
                   "used_tools": ["stock_tool", "news_tool"]},
      "diagnosis": null
    },
    {
      "kind": "diagnosis", "id": 51639, "created_at": "2026-11-05T00:30:00+00:00",
      "local_date": "2026-11-05", "local_time": "09:30", "date_group": "today",
      "title": "삼성전자 외 2종목 진단", "summary": "총자산 9,000,000원 중 현금이 3,000,000원(비중 33.33%)이고, 보유 종목은 3개입니다. …",
      "company_names": ["삼성전자", "SK하이닉스", "에코프로비엠"], "tickers": ["005930", "000660", "247540"],
      "detail_path": "/api/portfolio/diagnosis/51639", "research": null,
      "diagnosis": {"diagnosis_id": 51639, "source": "rule_based", "total_asset": 9000000.0, "holdings_count": 3,
                    "cash_weight_pct": 33.33, "top_holdings": [{"ticker": "005930", "company_name": "삼성전자", "weight_pct": 22.22}],
                    "risk_summary": "규칙 기준으로 확인된 위험 항목은 없습니다.", "flags": [], "flag_codes": [],
                    "generated_at": "2026-11-05T00:56:00.344219+00:00"}
    }
  ]
}
```

(dev DB에 테스트용 사용자와 합성 기록을 넣고 실제로 호출한 응답에서 줄였다. `top_holdings`는 3개 중 1개만 남겼고, 합성 데이터라 `generated_at`이 `created_at`과 다르다.)

- `kind`가 기록 종류다. 상세는 `detail_path`(기존 API: `GET /api/research/{id}`, `GET /api/portfolio/diagnosis/{id}`)로 연다.
- `total`은 `type` 탭의 건수, `counts`는 `type`과 관계없이 탭마다의 건수다(탭 숫자 표시용, 같은 검색 조건).
- 결과가 없으면 200, `total: 0`, `items: []`. `page`가 끝을 넘어도 200, 빈 `items`.
- 에러: 401(토큰 없음, 잘못된 파라미터를 같이 보내도 401이 먼저), 422(위 범위 밖, 검증 오류 형식), 503(DB 연결 불가).
- 다른 사용자의 기록은 어떤 조건으로도 나오지 않는다(모든 쿼리가 `user_id` 조건으로 시작한다).
- 성능: 두 테이블의 `(종류, id, created_at)`만 `UNION ALL`로 모아 DB에서 정렬·`LIMIT/OFFSET` 한 뒤 그 페이지 행만 읽는다. 쿼리 수는 페이지 크기와 무관하게 인증 1 + 건수 2 + 페이지 1 + 리서치 3 + 진단 1 = 최대 8개(테스트가 센다).

---

## 다국어: ko-KR · en-US · ja-JP · zh-CN (2026-11-05)

프런트 인계 문서: `docs/FRONTEND_HANDOFF_I18N_HISTORY.md`, 번역 리소스 초안: `docs/i18n/{locale}.json`.

### 원칙

- **기본은 ko-KR이고, 설정을 바꾸지 않은 사용자의 응답은 한 글자도 바뀌지 않는다.** locale을 주지 않으면(그리고 계정 설정이 ko-KR이면) 기존 코드 경로를 그대로 탄다.
- locale 결정: **요청의 `locale` > 계정 설정(`preferred_locale`) > ko-KR.** 요청에 지원하지 않는 값(`fr-FR`, `zh-TW` 등)이 오면 계정 설정이 아니라 ko-KR이다. `Accept-Language` 헤더는 보지 않는다.
  표기는 대소문자·밑줄을 허용하고 언어만 와도 된다(`en`, `en_us` → en-US, `zh`, `zh-Hans` → zh-CN).
- **숫자·날짜·종목명은 언어와 관계없이 같다.** 계산은 언어와 무관하게 한 번만 하고, 문장만 바꾼다. 금액은 값은 같고 표기만 바뀐다(`1,414,000원` → `KRW 1,414,000` / `1,414,000ウォン` / `1,414,000韩元`, `1조 2,000억원` → `KRW 1.2 trillion` / `1兆2,000億ウォン` / `1.2万亿韩元`).
- 종목명·ETF 상품명은 공식 등록명(한글 포함) 그대로, 뉴스·공시 제목은 원문 그대로다(번역하지 않는다).
- 과거 기록은 다시 번역하거나 덮어쓰지 않는다. 기록마다 만든 언어(`locale`)가 저장되고, 기존 기록은 ko-KR이다.

### 계정 설정: `GET /api/auth/preferences`, `PUT /api/auth/preferences` (인증 필요, 본인만)

```json
{"preferred_locale": "ko-KR", "preferred_timezone": null, "effective_timezone": "Asia/Seoul",
 "supported_locales": ["ko-KR", "en-US", "ja-JP", "zh-CN"], "default_locale": "ko-KR"}
```

PUT 본문: `{"preferred_locale": "en-US", "preferred_timezone": "America/New_York"}`(둘 다 선택, 보내지 않은 필드는 그대로, `preferred_timezone: null`은 Asia/Seoul로 되돌림).
지원하지 않는 언어·모르는 시간대는 **422**(설정 저장은 조용히 ko-KR로 바꾸지 않는다). 응답은 GET과 같은 형태. 다시 로그인해도 유지된다.
`preferred_timezone`은 기록 API(`/api/research`, `/api/portfolio/diagnosis/history`, `/api/history/search`)의 `timezone` 기본값이 된다.
`GET /api/auth/me` 응답은 바꾸지 않았다(설정은 이 API로 읽는다).

### locale을 받는 API

| API | 전달 | 바뀌는 것 |
| --- | --- | --- |
| `POST /api/research` | 본문 `locale`(최대 20자) | `answer`, `disclaimer`. 2026-11-09(38차)부터 응답에 `locale`(실제 언어)·`translation_status`도 있다 |
| `POST /api/portfolio/diagnosis` | 쿼리 `locale` | `summary`·`strengths`·`risks`·`suggestions`·`notes`·`flags[].message`·`disclaimer`, 보유 없음 400의 `detail` |
| `GET /api/portfolio/diagnosis/{id}/compare?view=record` | 쿼리 `locale` | `basis.note` |
| `GET /api/briefing` | 쿼리 `locale` | flag `text`·`unit`, 종목 `notice`, `message`, `portfolio.message`, `stale_notice`, 시장 지표 `name`, `disclaimer` |
| `GET /api/portfolio/exposure` | 쿼리 `locale` | `message`, `flags[].message`, `data_warnings[].message`, `decomposed_etfs[].stale_notice`, `undecomposed_etfs[].reason`, `disclaimer` |
| `GET /api/disclaimer` | 쿼리 `locale` | `text`(공개 API라 계정 설정은 보지 않는다) |

### 리서치 답변을 그 언어로 만드는 방법 (`POST /api/research`)

답변은 언제나 기존 한국어 경로로 먼저 만든다. 그래서 숫자 검증·종목 정합성·서술 검증·외국 문자 제거가 ko-KR과 똑같이 적용된다. 그다음 언어별로:

1. 고정 안내(답변 실패·지연) → 언어별 고정 문구
2. 용어 사전 답(종목 없는 정의·비교·해석 질문) → 언어별 사전(`app/glossary_i18n.py`, 40개 표제어를 언어별로 직접 작성). 한국어 답이 사전 답과 글자까지 같을 때만
3. 전망 간결 답과 후속 답(결론·고르기·왜·한 줄·비교·확률·매수 가부·위험·데이터 기준) → **언어별 코드 템플릿**(`app/outlook_i18n.py`, 2026-11-06).
   한국어 답과 같은 계산 결과(stock_tool 기록의 indicators·signals·label·data)로 문장만 그 언어로 쓴다(LLM 없음). 기록으로 한국어 답을 다시 만들어
   저장된 답과 글자까지 같고, 템플릿 답의 숫자가 모두 한국어 답에 있는 값일 때만 쓴다. 아니면 4번으로 간다. `translation_status`는 `localized`.
   예(ja-JP): `SK하이닉스(000660) · 今後5取引日 · 方向: 上昇寄り（信頼度: 低い）` / `上昇寄りです。ただし、シグナルが弱く（5つのシグナル群のうち上昇2・下落0・中立3）、…`
   최근 N거래일 주가 흐름 답(아래 "최근 N거래일 주가 흐름 질문")도 같은 조건의 언어별 템플릿이다(`app/price_flow_i18n.py`). 예(en-US): `삼성전자(005930) · price trend over the last 20 trading days`
4. 그 밖(상세 전망 리서치·실적/공시 날짜/신호 의미 후속·기간·시세·LLM 답 등) → **보호 번역**(`app/i18n_translate.py`): 숫자·날짜·금액·종목명·기사/공시 제목·링크를 코드가 자리표시자(⟦n⟧)로 가린 뒤
   LLM이 문장만 번역하고 코드가 원래 값으로 되돌린다. 번역문이 ① 자리표시자를 정확히 한 번씩 쓰고 ② 자리표시자 밖에 숫자가 없고 ③ 한글이 남지 않고
   ④ 그 언어의 글자이고 ⑤ 제목(#) 줄 수가 같을 때만 쓴다. 어기면 한 번 다시 시키고, 그래도 어기거나 LLM이 응답하지 않으면
   **2026-11-07(35차)부터 그 언어의 안내 + 데이터 요약**이다(`app/quote_i18n.fallback_summary`, `translation_status` = `fallback_summary`):
   "※ We couldn't verify an accurate translation of this answer's explanation…" + 도구 결과의 종가·등락률·거래량·기준일·지연, 거래량 순위,
   근거 기사·공시 제목(원문) + 면책. 쓸 데이터가 없으면 안내 + 면책만이다. 이전(`fallback_original`)처럼 한국어 원문을 보여 주지 않는다
   (한국어 원문은 `original_content`·`view=record`의 `original_answer`에 그대로 있다). 번역문 끝에는 "한국어 답변을 번역했고 이름·제목은 원문 그대로"라는 안내가 붙는다.
5. 단순 시세 답(2026-11-07)은 3번과 같은 조건의 언어별 템플릿이다(`app/quote_i18n.localize`, `localized`).

리포트 저장: `content` = 사용자에게 보인 답(그 언어), `original_content` = 번역 전 한국어 답, `locale`, `translation_status`(`translated` | `localized` | `fallback_summary` | `fallback_original`). `fallback_original`은 2026-11-07 이전에 저장된 리포트에만 있다.
**프론트**: `fallback_summary`면 해설이 빠진 요약 답이므로 "원문(한국어) 보기"로 `GET /api/research/{id}?view=record`의 `original_answer`를 보여 줄 수 있다.
2026-11-09(38차)부터 `POST /api/research` 응답에도 `"locale"`·`"translation_status"`가 있어(ko-KR이면 `"translation_status": null`) 상세를 다시 부르지 않고 판단할 수 있다.
번역은 덩어리 하나라도 검증에 실패하면 답 전체가 `fallback_summary`다(일부만 번역된 답은 없다). LLM 연결 실패와 검증 실패는 같은 값이다.
같은 기록으로 후속 질문을 하면 맥락은 `original_content`(한국어)다. `GET /api/research/{id}?view=record`는 `locale`, `translation_status`, `original_answer`를 더 준다.
목록·통합 검색 항목(`view=record`, `/api/history/search`)에는 `locale`이 있다.

### 알려진 한계 (2026-11-05 확인)

- 보호 번역은 LLM이 필요하다. 이 작업 중 로컬 Ollama(127.0.0.1:13434)는 모델 목록은 응답했지만 채팅 호출이 `llama-server binary not found`로 실패해,
  **실제 모델의 번역 품질은 측정하지 못했다.** 그 상태에서는 상세 전망·시세 같은 답이 위 4번의 "한국어 원문 + 안내"로 나간다(테스트는 가짜 LLM으로 검증했다).
  전망 간결 답·후속 답(3번)은 LLM 없이 템플릿이라 이 상태에서도 그 언어로 나온다(2026-11-06 dev DB 1,050개 종목·기간 × 10종류 × 3개 언어 31,500건 확인).
- 질문 이해(라우팅·용어 사전 판정·전망 판정)는 한국어 규칙이다. 영어·일본어·중국어로 **질문**하면 대부분 LLM 경로로 간다.
- HTTP 오류의 `detail` 문자열(401·404·422·429·503)은 호환을 위해 한국어 그대로다. 화면 문구는 상태 코드로 `docs/i18n/*.json`의 `errors.*`를 쓴다.
- 기존 진단 저장본에는 `locale` 키가 없다(= ko-KR). ko-KR이 아닌 진단만 저장본(`result`)에 `locale`이 들어간다(컬럼은 늘리지 않았다).

---

## 개인화 일일 투자 브리핑 (2026-11-06)

화면용 상세(필드 표, 상태 처리, 추천 화면, 실제 응답 예시)는 `docs/FRONTEND_HANDOFF_DAILY_BRIEFING.md`. 여기는 백엔드 규칙만 적는다.

**기존 `GET /api/briefing`과의 차이**: `/api/briefing`은 요청할 때마다 다시 계산하는 화면용 응답이고 저장하지 않는다(시장 3지표 1일 변화, 종목별 등락률·거래량·뉴스·공시·flag, 포트폴리오 요약). 일일 브리핑은 그 계산(`app.briefing.build_briefing`)을 그대로 재사용하고, 그 위에 아래를 더해 5개 영역으로 만든 뒤 날짜별로 저장한다(`app/daily_briefing.py`, `app/daily_briefing_db.py`, `app/api/daily_briefing.py`, 테이블 `daily_briefing`, 마이그레이션 `5e7b9d2c4a10`).

| 영역 | 내용(모두 DB 값 또는 결정적 계산) | 재사용한 코드 |
| --- | --- | --- |
| `market` 오늘의 시장 요약 | 코스피·코스닥·원/달러 종가·1일·5거래일 변화, 제목에 시장 키워드가 있는 최근 3일 기사(제목만, 해석 안 함) | `briefing` 시세 정의 |
| `holdings` 보유 종목 | 기준일·지연 영업일, 1·5·20거래일 등락률, 평가액·비중·손익, 위험 요인, 직접 관련 뉴스(제목에 종목명), 간접 뉴스(업종·기초지수), 공시 | `build_briefing`, `news_relevance.title_names_subject`, `related_news.indirect_news` |
| `watchlist` 관심 종목 | 보유와 겹치는 종목 제외, 규칙 기준 변화가 있는 종목 먼저, 없으면 "특별한 업데이트가 없습니다", 마지막 리서치 이후 저장된 뉴스·공시 수 | `build_briefing`, `research_report` |
| `risk` 위험 점검 | 진단 지표·플래그(DB 종가 기준), 업종 집중도는 데이터 없음 안내, ETF 실질 노출은 구성종목으로 분해한 경우에만 | `diagnosis.compute_portfolio`·`compute_flags`, `exposure_db.get_exposure` |
| `checklist` 오늘 확인할 사항 | 최대 3개: 데이터 지연 > 보유 실적 공시·공시 > 위험 플래그 > 관심 종목 공시 | |

- `diagnosis.load_inputs`는 실시간 시세(외부 호출)를 써서 쓰지 않았다. 위험 점검은 브리핑과 같은 DB 종가로 계산한다. 외부 시세·뉴스·공시 API는 부르지 않는다.
- 비슷한 제목(`news_relevance.is_duplicate_title`, 0.9)은 한 묶음(`similar_count`)으로 보인다. 직접 관련 뉴스는 제목이 그 종목을 가리키는 기사만이다(기존 `/api/briefing`의 `news`는 종목에 연결된 기사 전부라 시황 마감 기사가 섞였다).

**데이터 신선도**: 전망 경로와 같은 규칙이다. 기대 거래일 = `outlook.expected_trading_day`(장 마감 전이면 직전 거래일, 2026-11-08부터 KRX 거래일 달력), 지연 = `data_status.business_days_behind`(거래일 수), 0이면 `latest`, 1~5(`outlook.MAX_LAG_BUSINESS_DAYS`)면 `delayed`, 초과면 `stale`, 시세가 없으면 `no_data`. 늦은 이유는 `outlook.price_status`(미수집·수집 실패·휴장일 추정·종목만 누락), 마지막 갱신 실행은 `data_status.summarize_last_run`. `latest`가 아니면 `content.freshness.headline`이 경고 문장이고 `warning: true`다. 지수·환율 기준일이 다르면 `notices`에 따로 적는다. 뉴스·공시 수집 시각은 `max(created_at)`.

**LLM 역할**: 요약 한 단락(`content.summary`)만 쓸 수 있다. 입력은 그 언어로 그린 브리핑의 사실뿐이고, 출력이 다음을 모두 지킬 때만 쓴다(아니면 1번 다시 시키고, 그래도 안 되면 코드 요약).
1. 숫자가 입력에 있는 값(표기 변형 포함, `diagnosis.allowed_numbers`)뿐
2. 금지 표현(매수·매도 지시, 목표 주가, 확률, 추천, "오를 것" 등)이 없음
3. 브리핑에 없는 종목명(DB 종목명 전체 + 진단의 통칭 사전)이 없음
4. 그 언어의 글자(종목명·뉴스/공시 제목 원문은 예외)
5. 시세가 지연·오래됨이면 첫 문장에 기준일이 있음
Ollama 연결 실패·타임아웃은 재시도 없이 코드 요약이다. 주소(`OLLAMA_HOST`)와 모델(`llm_settings.chat_model`)은 기존 설정 그대로다. `use_llm: false`면 부르지 않는다.

**저장 정책**: 같은 사용자·같은 `local_date`(요청 `timezone` → 계정 `preferred_timezone` → Asia/Seoul 기준)는 version 1 한 건이다. 다시 `generate`하면 저장본을 200으로 돌려주고 다시 계산하지 않는다. `regenerate: true`면 version을 올린 새 행을 더하고 기존 행은 고치지 않는다(하루 `MAX_VERSIONS` = 5, 넘으면 409). 동시에 두 번 만들면 유니크 제약(`user_id, local_date, version`)이 막고 먼저 저장된 것을 돌려준다. 조회는 저장한 `content`를 그대로 돌려주며 현재 시세로 다시 계산하지 않는다. 사용자를 지우면 브리핑도 지워진다(CASCADE).

**저장 컬럼**: `user_id`, `local_date`, `timezone`, `locale`, `version`, `generated_at`, `data_as_of`, `market_data_status`, `summary_source`(llm\|code), `content`(그 언어), `original_content`(같은 계산의 한국어), `tickers`, `sources`, `section_status`(JSONB).

**다국어**: 문장은 모두 `app/daily_briefing_text.py`의 언어별 템플릿이다(진단·노출·브리핑 flag 문장은 기존 `diagnosis_i18n`·`exposure_i18n`·`briefing_i18n`). 같은 계산 결과를 언어만 바꿔 그리므로 숫자 필드는 언어와 관계없이 같다. 숫자·날짜·종목코드·회사 공식 명칭·뉴스/공시 제목은 번역하지 않는다. 기록은 만든 언어로 남고 나중에 다시 번역하지 않는다. 한국어 원본(`original_content`)의 요약은 코드 요약이다(LLM은 요청 언어로 한 번만 부른다).

**인증·분리**: 4개 라우트 모두 로그인 필요(401). 모든 조회는 `user_id`로 거르고, 보유·관심 종목은 `owner_key`(`user:{id}`)로만 읽는다. 남의 브리핑 id는 404다. 생성은 사용자당 분당 3회(`RATE_LIMIT_DAILY_BRIEFING_PER_MIN`, 넘으면 429).

**알려진 한계**: 업종 집중도 없음(company.sector 비어 있음), 미래 실적 발표 일정 데이터 없음, 시장 이벤트는 키워드 기사 목록뿐, 매일 자동 생성 안 함, 통합 검색(`/api/history/search`)에 브리핑이 나오지 않음, LLM 요약이 숫자를 나열하는 식으로 길어질 수 있음(검증은 통과).

## 운영 설정 (환경변수, 2026-10-28)

비밀값은 코드에 두지 않는다(`backend/.env`, 커밋 금지, 예시는 `backend/.env.example`). 아래는 비밀값이 아닌 설정과 기본값이다.

| 환경변수 | 기본값 | 뜻 |
| --- | --- | --- |
| `APP_ENV` | `development` | `production`이면 약한 `JWT_SECRET`(예시 문구·반복 문자)으로 **기동하지 않는다**. 개발이면 경고 로그만 |
| `JWT_SECRET` | (없음, 필수) | 32자 이상. 없거나 짧으면 모든 모드에서 기동 거부(기존) |
| `CORS_ALLOW_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 쉼표 구분 허용 origin. `*`면 전부 허용(credentials는 항상 끔) |
| `CORS_EXPOSE_HEADERS` | `X-Total-Count` | 쉼표 구분. 브라우저 JS가 읽을 수 있게 노출할 응답 헤더(`GET /api/companies`의 전체 건수) |
| `RATE_LIMIT_ENABLED` | `1` | `0`/`false`/`off`면 레이트 리밋을 끈다(테스트가 끈다) |
| `RATE_LIMIT_LOGIN_PER_MIN` | `10` | IP당 분당 로그인 시도 |
| `LOGIN_LOCK_FAILURES` / `LOGIN_LOCK_SECONDS` | `5` / `300` | 같은 아이디+IP 연속 실패 5회면 300초 잠금(성공하면 초기화) |
| `RATE_LIMIT_SIGNUP_PER_HOUR` | `10` | IP당 시간당 가입 |
| `RATE_LIMIT_ORDERS_PER_MIN` | `30` | 사용자당 분당 모의투자 주문 |
| `RATE_LIMIT_RESEARCH_PER_MIN` | `6` | 사용자당 분당 AI 질문(`POST /api/research`) |
| `RATE_LIMIT_RESEARCH_CONCURRENT` | `1` | 사용자당 동시에 처리 중인 AI 질문 |
| `RATE_LIMIT_LOGOUT_PER_MIN` | `10` | 사용자당 분당 로그아웃(2026-10-29) |
| `RATE_LIMIT_PASSWORD_CHANGE_PER_HOUR` | `10` | 사용자당 시간당 비밀번호 변경 시도(2026-10-29) |
| `RATE_LIMIT_BACKTEST_PER_MIN` | `10` | 사용자당 분당 백테스트(`POST /api/backtest`, 2026-10-29) |
| `RATE_LIMIT_RECOVERY_PER_MIN` | `5` | IP당 분당 `find-username`·`forgot-password`(엔드포인트별로 따로, 2026-10-30) |
| `RATE_LIMIT_RECOVERY_EMAIL_PER_HOUR` | `3` | 같은 이메일로 시간당 `find-username`·`forgot-password`(엔드포인트별, 메일 폭탄 방지) |
| `RATE_LIMIT_TOKEN_PER_MIN` | `10` | IP당 분당 `verify-email`·`reset-password`(엔드포인트별, 토큰 무차별 대입 방지) |
| `RATE_LIMIT_OAUTH_PER_MIN` | `20` | IP당 분당 소셜 로그인 시작·콜백(각각, 2026-10-30) |
| `RATE_LIMIT_OAUTH_EXCHANGE_PER_MIN` | `10` | IP당 분당 1회용 로그인 코드 교환 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `NAVER_LOGIN_CLIENT_ID` / `NAVER_LOGIN_CLIENT_SECRET` / `OAUTH_REDIRECT_BASE` | 위 "소셜 로그인" | 소셜 로그인 키(비밀값은 `.env`에만)와 백엔드 공개 주소 |
| `RATE_LIMIT_VERIFICATION_MAIL_PER_HOUR` | `5` | 사용자당 시간당 인증 메일(`PUT /api/auth/email` + `resend-verification` 합산) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_FROM` / `FRONTEND_BASE_URL` | 위 "계정 복구" | 메일 발송(`SMTP_HOST`가 없으면 개발용 파일 발송기). `SMTP_PASSWORD`는 비밀값 |
| `PASSWORD_CHANGE_LOCK_FAILURES` / `PASSWORD_CHANGE_LOCK_SECONDS` | `5` / `300` | 같은 사용자의 현재 비밀번호 연속 오류 5회면 300초 비밀번호 변경 잠금(성공하면 초기화, 2026-10-29) |
| `MAX_REQUEST_BODY_BYTES` | `1048576` | 요청 본문 상한(넘으면 413, 2026-10-30) |
| `RATE_LIMIT_PUBLIC_GET_PER_MIN` | `600` | IP당 분당 모든 GET `/api/*` |
| `RATE_LIMIT_MAIL_PER_RECIPIENT_PER_HOUR` | `10` | 같은 수신 주소로 보내는 메일 시간당(모든 종류 합산, 넘으면 생략) |
| `RATE_LIMIT_ACCOUNT_DELETE_PER_HOUR` | `5` | 사용자당 시간당 회원 탈퇴 시도 |
| `RATE_LIMIT_DAILY_BRIEFING_PER_MIN` | `3` | 사용자당 분당 일일 브리핑 생성(`POST /api/briefings/daily/generate`, 2026-11-06) |
| `SECURITY_HSTS` | (꺼짐) | `1`이면 Strict-Transport-Security(HTTPS로만 서비스할 때) |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | `/api/health`의 Ollama 검사 주소. 에이전트·진단·임베딩의 `ollama` 라이브러리도 같은 변수를 읽는다. 재부팅 뒤 11434가 막히면 README "재부팅 뒤 11434가 막혔을 때" 참고 |

### 레이트 리밋 (429)

| 대상 | 기준 | 기본 한도 | 순서 |
| --- | --- | --- | --- |
| `POST /api/auth/login` | IP | 분당 10회 | 인증 전이라 맨 먼저 |
| `POST /api/auth/login` 연속 실패 | 아이디 + IP | 5회 실패면 5분 잠금 | IP 한도 다음. 없는 아이디도 똑같이 세서 응답으로 계정 존재 여부가 드러나지 않는다 |
| `POST /api/auth/signup` | IP | 시간당 10회 | 맨 먼저 |
| `POST /api/portfolio/orders` | 사용자 | 분당 30회 | 인증(401) 다음 |
| `POST /api/research` | 사용자 | 분당 6회 + 동시 1건 | 인증(401) 다음. 처리 중인 질문이 있으면 두 번째는 429(`Retry-After: 5`) |
| `POST /api/auth/logout` | 사용자 | 분당 10회 | 인증(401) 다음. 429면 폐기하지 않는다 |
| `POST /api/auth/change-password` | 사용자 | 시간당 10회 | 인증(401)과 본문 검사(422) 다음. 매번 argon2 검증·해시를 하므로 CPU 소모를 제한한다 |
| `POST /api/auth/change-password` 현재 비밀번호 연속 오류 | 사용자 | 5회 오류면 5분 잠금 | 시간당 한도 다음. 잠금 중에는 맞는 비밀번호도 429. 아이디+IP가 아니라 사용자 기준이라 토큰을 가진 쪽이 IP를 바꿔 대입해도 막힌다 |
| `POST /api/backtest` | 사용자 | 분당 10회 | 인증(401)과 본문 검사(422) 다음, 종목 해석 전 |
| 모든 `GET /api/*` | IP | 분당 600회 | 라우트보다 먼저(2026-10-30). 공개 시세·검색·헬스 포함 |
| `DELETE /api/auth/me` | 사용자 | 시간당 5회 + 비밀번호 변경과 같은 연속 오류 잠금 | 인증(401) 다음 |
| 메일 발송(모든 종류) | 수신 주소 | 시간당 10통 | 응답 뒤 발송 단계. 넘으면 보내지 않고 응답은 같다 |

```
HTTP/1.1 429 Too Many Requests
Retry-After: 300

{ "detail": "요청이 너무 많습니다. 300초 후 다시 시도해주세요." }
```

- 막는 것: 한 IP의 로그인·가입 반복, 같은 아이디+IP의 비밀번호 대입, 한 사용자의 주문 폭주와 AI 질문 폭주·동시 질문(Ollama 점유).
- **못 막는 것(한계)**: 메모리 기반이라 서버를 재시작하면 초기화되고, 워커·인스턴스가 여럿이면 각자 따로 센다(단일 프로세스 가정).
  IP는 `request.client.host`(직접 연결한 주소)라 프록시 뒤에서는 모든 사용자가 같은 IP로 보여 IP 한도를 함께 쓴다(`X-Forwarded-For`는 위조될 수 있어 믿지 않음).
  여러 IP에서 한 아이디를 노리는 분산 대입은 아이디+IP 잠금으로 막히지 않는다(정상 사용자를 잠그지 않기 위한 선택). 공개 조회 API(시세·종목 목록 등)에는 한도가 없다.
- 프론트: 429면 `detail`을 그대로 보여주고 `Retry-After`초 동안 같은 요청 버튼을 막으면 된다.

### 서버 오류 (500)

처리되지 않은 예외는 `500 {"detail": "서버 오류가 발생했습니다."}`다(스택트레이스·내부 경로·SQL 없음, 상세는 서버 로그에만).
2026-10-28 전에는 같은 경우 평문 `Internal Server Error`였다. 422(FastAPI 기본 형식)와 기존 401/404/409/502/503은 그대로다.

### 로그

요청마다 `app.access` 로거에 `메서드 경로(쿼리 제외) 상태 처리시간ms user=<id 또는 ->`만 남긴다. 3초 이상은 WARNING.
Authorization 헤더·토큰·비밀번호·본문은 남기지 않는다. DB 오류 로그는 예외 원문 대신 종류와 SQL 문장 앞부분만(파라미터 제외).

---

## 에러 응답 형식

### 일반 오류 (400 / 401 / 404 / 409 / 429 / 500 / 502 / 503)

항상 `detail`이 **문자열**이다. 사용자에게 그대로 보여줘도 되는 한국어 메시지. 401에는 `WWW-Authenticate: Bearer` 헤더가 붙는다.

```json
{ "detail": "..." }
```

### 검증 오류 (422)

FastAPI 기본 형식으로 `detail`이 **배열**이다. 필드별 위치는 `loc`, 사유는 `msg`.
(`detail`의 타입이 상태코드에 따라 문자열/배열로 달라지므로 프론트에서 `Array.isArray(detail)`로 분기하세요.)

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "question"],
      "msg": "Value error, 질문이 비어 있습니다.",
      "input": "   ",
      "ctx": { "error": {} }
    }
  ]
}
```

길이 초과는 `"type": "string_too_long"`, `"msg": "String should have at most 1000 characters"`.
필수 필드 누락은 `"type": "missing"`.
`/api/auth/*`의 422에는 `input` 키가 빠진다(입력한 비밀번호가 응답에 되돌아오지 않게). 나머지 경로는 위 형식 그대로다.

## 인증 관련 한계 (2026-10-29 갱신)

- 로그인·가입 횟수 제한은 2026-10-28에 들어갔다(메모리 기반, 위 "레이트 리밋"의 한계가 그대로다).
- 토큰 폐기는 2026-10-29부터 된다(로그아웃·비밀번호 변경). 남은 한계:
  - **폐기는 사용자 단위다(전체 기기 로그아웃).** 한 기기만 로그아웃하거나, 기기별 세션 목록을 보여 주는 기능은 없다.
  - **폐기하지 않은 access token은 여전히 24시간 유효하다.** 토큰을 훔친 사람은 사용자가 로그아웃·비밀번호 변경을 하기 전까지 쓸 수 있다.
    짧은 만료 + refresh token 구조가 아니다.
  - **refresh token이 없다.** 24시간이 지나면 다시 로그인해야 한다.
  - 훔친 토큰으로 로그아웃을 부르면 원래 사용자도 모든 기기에서 로그아웃된다(가용성 문제, 데이터 노출은 아님).
    훔친 토큰으로 비밀번호를 바꾸려면 현재 비밀번호가 필요하다.
  - DB를 백업에서 복원하거나 마이그레이션을 내렸다 올리면 `token_version`이 예전 값(또는 0)으로 돌아가 이미 폐기한 토큰이 다시 통할 수 있다.
    그런 작업 뒤에는 `JWT_SECRET`을 바꿔 모든 토큰을 한꺼번에 무효로 하는 것이 안전하다.
- 서버의 `JWT_SECRET`을 바꾸면 모든 토큰이 한꺼번에 무효가 된다(기존).
- 비밀번호 재설정(분실 시), 회원 탈퇴 API는 없다.
- HTTPS는 배포 환경(리버스 프록시)에서 처리해야 한다. 로컬 개발 서버는 HTTP라 토큰이 평문으로 오간다.

## 알아둘 점

- `answer`는 로컬 LLM(llama3.1:8b)이 생성하므로 같은 질문에도 매번 표현이 다르고, 뉴스가 질문 종목과 무관해 보이는 경우나 모델의 사실 오류가 섞일 수 있다. 근거는 `sources`로 확인하도록 UI에 링크를 노출하는 것을 권장한다.
- `POST`는 성공할 때마다 리포트를 저장한다(`report_id`). 테스트 호출도 목록에 쌓인다. 필요 없는 리포트는 `DELETE`로 지운다.
- 대화 스레드는 `previous_report_id`로 이어진다(각 리포트는 자기 직전 것만 가리킨다). 모델 맥락으로는 직전 1개만 쓰이므로,
  더 앞선 대화 내용을 참조하는 질문("아까 첫 질문에서 말한 그 종목")은 이어받지 못할 수 있다.
- 종목은 DB에 등록된 이름 기준으로 찾되, "네이버"(`NAVER`), "포스코홀딩스"(`POSCO홀딩스`), "현대자동차"(`현대차`)처럼 흔한 통칭은 별칭 사전
  (`backend/app/company_aliases.py`)으로 등록명에 연결한다. 사전에 없는 통칭은 인식하지 못하고 200 응답의 `answer`에 "찾지 못했다"는 안내가 나오므로,
  프론트에서 종목 자동완성/선택 UI를 둔다면 등록명(company 테이블의 name)으로 보내는 것이 가장 안전하다.
- 사건 이후 주가 반응은 인과가 아니고 표본이 적다(위 "사건(뉴스·공시) 이후 주가 반응 질문"과 `GET /api/stocks/{ticker}/event-reactions`의 한계). 이 질문 유형의 `POST /api/research`는 LLM을 부르지 않아 즉시 응답한다.
- `POST /api/portfolio/diagnosis`는 LLM 설명 때문에 보통 4~5초, 모델이 내려가 있었다면 첫 호출이 더 걸린다. Ollama 장애 시 규칙 기반 문장으로 폴백한다(`source`).
- `GET /api/status`의 `stale`은 2026-11-08부터 KRX 거래일 기준이다(그 전에는 공휴일을 모르는 평일 기준). 환율은 소스가 주는 날짜까지만 있어 보통 1~2일 늦다(2026-10-14 기준 10/12).
- 데이터는 수동으로 갱신한다(`python -m scripts.refresh_data`, 평일 15:30 이후 권장). 2026-10-14 갱신 기준 주가·지수·뉴스·공시는 10/14, 환율은 10/12까지다.
  현재 기준일은 `GET /api/status`로 확인한다. "오늘 주가"를 물어도 DB의 최신 종가로 답한다.

## 성능 (일반 조회 API)

PRD 비기능 요구사항은 일반 API P95 500ms 이내다(`POST /api/research`는 AI 연산이라 예외). 2026-09-28에 실제 `uvicorn` 서버(로컬, dev DB)를
띄워 각 경로를 연속 200회 호출한 결과:

| 요청 | p50 | p95 | p99 | 응답 크기 |
| --- | --- | --- | --- | --- |
| `GET /api/research` (limit=20 기본) | 3.8 ms | 4.1 ms | 4.3 ms | 2.7 KB |
| `GET /api/research?limit=100` | 3.8 ms | 4.1 ms | 4.3 ms | 2.7 KB |
| `GET /api/research?limit=20&offset=2` | 4.0 ms | 4.2 ms | 4.3 ms | 1.9 KB |
| `GET /api/research/5` (tool 로그 2건) | 3.4 ms | 3.9 ms | 4.1 ms | 0.4 KB |
| `GET /api/research/2` | 4.1 ms | 4.4 ms | 4.6 ms | 2.3 KB |
| `GET /api/research` (매 요청 새 연결) | 4.4 ms | 4.9 ms | 5.1 ms | 2.7 KB |

기준(500 ms) 대비 두 자릿수 이상 여유가 있다. 다만 dev DB에는 리포트가 6건뿐이라, 규모가 커졌을 때를 따로 확인했다. 실제 테이블을 건드리지
않는 임시 테이블에 리포트 5만 건 + tool 로그 10만 건을 만들어 목록/상세와 같은 쿼리의 **DB 측 시간**만 잰 값이다(네트워크·직렬화 제외):

| 쿼리 | p50 | p95 |
| --- | --- | --- |
| 목록 `count(*)` | 7.1 ms | 7.8 ms |
| 목록 최신 20건 / 100건 | 11.4 / 11.7 ms | 12.1 / 12.3 ms |
| 목록 20건, `offset=40000`(깊은 페이지) | 34.9 ms | 38.1 ms |
| 목록 20건의 tool 이름 조회 | 9.5 ms | 10.3 ms |
| 상세(PK + `report_id` 인덱스로 tool 로그) | 0.4 ms | 0.6 ms |

목록은 `created_at` 인덱스가 없어 전체 스캔 + 정렬(5만 건에서 약 12 ms)이고, 보고서가 수십만 건을 넘기면 `(created_at DESC, id DESC)` 인덱스를
검토할 만하다. 지금 규모에서는 불필요하다.

## 리서치 답변 개선 (2026-11-07, 35차)

`POST /api/research`의 요청·응답 필드는 그대로다(새 최상위 키 없음). 바뀐 것은 아래 질문의 `answer` 문장과 `used_tools`이고, 번역 실패 시
`translation_status`에 `fallback_summary`가 생겼다(위 "다국어" 절). 모두 LLM 없이 코드가 답한다.

| 질문 예 | 이전 | 지금 | `used_tools` |
|---|---|---|---|
| "삼성전자 지금 사야 해?", "팔아야 돼?" | LLM이 관련 없는 뉴스 나열 | 전망 경로(방향·근거·불확실성·면책, 아래 "전망" 절과 같은 답) | `stock_tool`, `market_tool` |
| "삼성전자 목표주가 확정해 줘", "내일 얼마까지 올라?", "수익률 20% 보장 가능해?", "무조건 오르지?", "주식은 언제 사는 게 좋아?" | 시세 한 줄 또는 LLM | 요청 종류별 안내(목표주가·가격 예측·보장·시점 추천을 하지 않는 이유) + 대신 볼 수 있는 질문 + (종목이 있으면) 참고 시세·기준일 + 면책. 목표가·확률·기대수익률 숫자를 쓰지 않는다 | 종목 있으면 `stock_tool`, 없으면 없음 |
| 단순 시세("삼성전자 현재가 알려줘") | 종가 한 줄(+ "오늘·지금" 질문이 2영업일 넘게 지연일 때만 "마지막 데이터는 … 기준") | 종가 한 줄 + `데이터 기준일: YYYY-MM-DD 종가(DB에 저장된 종가이며 실시간 시세가 아닙니다).` + 지연이면 기대 거래일·지연 영업일 문장 | `stock_tool` |
| 주문 요청("10주 사줘") | 주문 안내 + 참고 시세 | 같음 + 참고 시세 아래 같은 기준일·지연 줄. 주문 인식·엄격 매칭·체결은 바뀌지 않았다(채팅은 여전히 주문하지 않는다) | `stock_tool` |
| "오늘 거래량 많은 종목", "거래량 상위 10개", "코스피 거래량 상위" | LLM 일반 경로 | 최근 거래일 거래량 순위(시장 필터: 코스피·코스닥, ETF 제외) + 기준일·지연 + "급증과 다름" 안내 | `discovery_tool`(`category: volume_top`) |
| "최근 거래량 급증 종목" | LLM(탐색 도구) | 직전 20거래일 평균 대비 2배 이상, 배수 순 + 기준일·지연 | `discovery_tool`(`volume_surge`) |
| "지금 시장 상황이 어때?", "오늘 코스피 지수 어때?" | "해당 종목을 찾지 못했습니다" / 용어 정의 | 코스피·코스닥 종가와 1·5·20거래일 수익률, 같은 날 상승·하락 종목 수, 기준일·지연, 예측 아님 안내 | `market_overview` |
| "오늘 주가가 얼마야?"(종목 없음) | LLM이 임의로 삼성전자 조회 | 어떤 종목인지 되묻는 안내 | 없음 |
| "삼성전자 20일 수익률 분석해 줘" | 달력 20일(약 15거래일)로 해석 | 최근 20거래일 주가 흐름 답(거래일 기준) | `stock_tool` |
| 휴장일 하루 질문("어제 종가"를 일요일에) | "직전 거래일(2026-11-03) 종가는 …"(실제 직전 거래일은 11-06인데 수집이 늦은 경우에도) | 그 종가가 종목의 마지막 저장 시세이고 그날의 직전 평일보다 앞서면 "저장된 마지막 시세인 2026-11-03 … 종가는 …" + 위 기준일·지연 줄. 과거 휴장일(예: 개천절 다음 토요일)은 예전 문장 그대로 | `stock_tool`(`called_via: period_history`) |

기준일·지연 계산은 주가 흐름·전망 답과 같다(`app.outlook.expected_trading_day`: 거래일, 15:30 전이면 직전 거래일 / `app.data_status.business_days_behind`: 거래일 수.
2026-11-08 36차부터 KRX 거래일 달력 기준, 35차 작성 당시에는 평일 기준·공휴일 달력 없음). 1영업일 이전은 정보로, 2영업일 이상은 "최신 시세가 아닙니다"로 쓴다. 실시간 시세는 별도 엔드포인트(`GET /api/stocks/{ticker}/realtime-price`)다.
지연이면(2026-11-08) 전망 답과 같은 늦은 이유 추정(`app.outlook.price_status`: 미수집·수집 실패·휴장일 추정·종목만 누락, 수집 기록으로 추정)을 한 문장 더 붙인다.
예: `늦은 이유(수집 기록으로 추정): 휴장일 추정(2026-10-09 19:30 수집에서 시장 전체에 그날 시세 없음).` 35차 당시에는 공휴일 다음 날 평일 기준 지연 수가 1 늘어나
이 이유로 구분했다(36차부터는 달력이 휴장일을 빼므로 이 경우 지연이 아니다. 지연 영업일 수는 전망·`GET /api/status`와 같다). en/ja/zh는 라벨만(`presumed market holiday` 등) 옮긴다.

**기간 최고·최저 정의(통일)**: 질문 기간 안의 거래일 종가만(기간 첫날·마지막 날 포함, 거래일 기준, 시세가 없는 날은 빠진다). 기간 직전 거래일 종가는 수익률의
비교 기준으로만 쓰고 최고·최저에 넣지 않는다. 기간 질문 답(`stock_tool` 요약 "이 기간 종가 최고는 …")은 이미 이 정의였고, 주가 흐름 답("최근 20거래일 범위"의
최고·최저 종가)이 비교 기준일까지 넣던 것을 이 정의로 바꿨다(그래서 그 답의 최저가가 비교 기준 종가로 나오던 경우가 사라진다).
2026-11-11(42차): 전망 답(`app/outlook.py` `compute_indicators`)의 "분석 기간 기준 최근 N거래일"·"52주 기준 최근 250거래일" 최고·최저와 국면 입력 "최근 20거래일 최저 종가"도 이 정의(기준일을 포함한 최근 N개 종가)로 바꿨다. 전에는 N+1개 종가에서 골라, 같은 "최근 20거래일"인데 주가 흐름 답과 값이 달랐다(2026-11-03 실제 DB 1주일 기간 350종목 중 91종목, 예: SK하이닉스 전망 최고 1,842,000원(10-02, 비교 기준일) vs 주가 흐름 1,784,000원(10-06)). 답의 "조회 기간 a~b"는 이제 정확히 N거래일이다. 평가기(`evals/forecast_check.py`)도 같은 정의로 검산한다.

**표기 정리**: LLM 답의 원시 숫자 표기를 고친다(`1414000.0원` → `1,414,000원`, `-4.260000%` → `-4.26%`, 0이 아닌 소수는 그대로). 같은 문단·같은 문장(20자 이상)
반복은 한 번만 남긴다. 값은 바꾸지 않는다(`app/answer_tidy.py`).

## 테스트 실행 (백엔드)

AI 답변 품질 평가(2026-11-07, 개발자용, API 아님): `cd backend && python -m evals.run_quality`(DB·Ollama 불필요, 결정론, 보고서 `evals/results/quality_latest.{json,md}`),
실제 모델 답 채점은 `OLLAMA_HOST=http://127.0.0.1:13434 python -m evals.run_quality --mode live`(로컬 전용, 리포트 저장 없음). 구조·지표·하드 실패·기준선은 `docs/AI_QUALITY_EVALUATION.md`.

`cd backend && pytest` — 빠른 테스트 전체(단위·라우터 mock + dev DB 통합, 2026-10-28 기준 3159개). 단계별로는 `pytest -m "not slow and not integration"`(DB·Ollama 불필요, 2766개), `pytest -m "integration and not slow"`(dev DB 통합 393개, DB가 꺼져 있으면 자동 skip). 사건 반응 slow 측정은 `EVENT_REACTION_LLM_INTERPRETATION`을 스스로 켜고 돈다. LLM을 실제로 호출하는 느린 테스트는 기본 제외이며 `pytest -m slow`로 따로 돌린다(dev DB에 리포트를 만들었다가 지운다). 전부 한 번에: `pytest -m "slow or not slow"`. DB 없이 단위 테스트만: `pytest -m "not integration"`.
인증 테스트는 `tests/conftest.py`가 테스트마다 임의 `JWT_SECRET`을 주입하므로 `.env`의 실제 키를 쓰지 않는다. 라우터 테스트는
`login_as` 픽스처로 `get_current_user`를 가짜 사용자로 바꾸고, dev DB 통합 테스트(`tests/test_auth_devdb.py`)는 실제 가입/토큰으로
돌린 뒤 만든 사용자를 지운다(`pt_` 접두어).

## 번역 상태·시간대·종목 매칭 계약 (2026-11-09, 38차)

전체 정리는 `docs/FRONTEND_INTEGRATION_HANDOFF.md` 15절(번역 상태 처리 흐름, API별 locale·fallback 표, 날짜·시각 필드 표, 종목 매칭 표)이다.

- **바뀐 응답**: `POST /api/research`에 `"locale"`(실제 언어)·`"translation_status"`(ko-KR이면 null) 두 키를 더했다. 기존 6개 키의 값은 그대로다.
  기본 상세(`GET /api/research/{id}`)와 목록은 바꾸지 않았다.
- **시각**: 값은 바꾸지 않았다. DB 시각 컬럼은 모두 `timestamptz`, 저장 시각(`created_at`·`executed_at`·`added_at`·`collected_at`·`queried_at`, 진단 `generated_at`)은
  UTC `+00:00`, 응답 때 계산한 시각(브리핑·노출·성과·백테스트·`/api/status`의 `generated_at`, `finished_at`, 브리핑 뉴스·공시 시각, `event_at`)은 KST `+09:00`이다.
  오프셋 없는 시각은 내보내지 않는다. 날짜(`price_date` 등)는 한국 거래일이다.
- **고친 것**: AI 답의 번역 실패 요약과 코드 요약에서 뉴스·공시 날짜를 UTC 문자열 앞 10자로 써서 하루 이르게 나오던 것(공시는 KST 자정 = UTC 전날 15:00로 저장)을
  한국 날짜로 고쳤다(`app/display_dates.kst_day`). 오프셋 없는 값·날짜만 있는 값은 그대로 둔다.
- **매칭**: 관심종목·종목 조회는 기본 모드(음차·약칭·오타·유사 보정), 주문·백테스트는 엄격 모드(`allow_fuzzy=False`, 코드·정확명·공백/대소문자·등록 별칭·ETF 브랜드 표기만)다. 바꾸지 않았다.

## 종합 진단 결과 수정 (2026-11-09, 39차)

응답 필드 추가·삭제는 없다. 값이 바뀐 것:

- **AI 답변의 뉴스·공시 날짜**: `news_tool`·`disclosure_tool`·`rag_search_tool` 결과의 `published_at`·`disclosed_at`을 UTC 문자열에서 같은 순간의 KST 문자열(`+09:00`)로
  바꿨다(`app/display_dates.kst_iso`). 이전에는 LLM이 UTC 날짜를 보고 답을 썼고, ETF 답의 `## 관련 뉴스` 날짜와 근거 우선 답변 날짜가 문자열 앞 10자를 써서
  한국 시간 00:00~09:00 기사와 모든 공시(KST 자정 저장)가 하루 이르게 나왔다. 답변 날짜 검증기도 도구 결과의 날짜를 보므로 이제 한국 날짜를 기준으로 본다.
  예전에 저장된 도구 기록(UTC 문자열)이 들어와도 화면용 날짜는 `kst_day`가 한국 날짜로 바꾼다.
- **`GET /api/portfolio/exposure?view=look_through`의 `summary.unidentified_amount`·`unidentified_weight_pct`·`over_allocated_amount`**: ETF별 잔여를 부호째 더한
  순합계로 계산해, 비중 합계가 100%를 넘는 ETF와 못 미치는 ETF를 같이 보유하면 서로 상쇄돼 작게(0까지) 나왔다. 이제 미달 ETF들의 잔여 합, 초과 ETF들의 초과분 합이다.
  `residual_amount`는 예전처럼 순합계(음수 가능)다. ETF 하나만 보유했거나 모두 같은 쪽이면 값이 같다. 진단에 저장된 예전 스냅샷은 바꾸지 않는다.
- **`python -m scripts.quarantine reprocess --apply`**: 데이터 갱신과 같은 advisory lock을 잡는다(실행 중이면 종료 코드 2).

## 잔여 품질 개선 (2026-11-09, 40차)

- **`POST /api/portfolio/orders` 응답에 `asset_type`**(`stock` | `etf`, 하위 호환 추가). 위 주문 절 참고. 다른 필드 값은 그대로다.
- **시장 상황 답(리서치 "지금 시장 상황 어때?")**: 상승·하락·보합 집계에 등락률이 비어 있는 종목이 있으면 "등락률 미확인 N종목", 등록된 주식 중
  그날 시세가 없는 종목이 있으면 "그날 시세가 없는 N종목은 집계에서 뺐습니다"를 덧붙인다(전체 = 상승 + 하락 + 보합 + 등락률 미확인).
  둘 다 0이면 문장은 예전과 같다. API 응답 필드가 아니라 답 문장과 도구 기록(`breadth.change_missing_count`·`no_price_count`)만 바뀐다.
- **`python -m scripts.collect_universe`**(뉴스·공시 수동 수집)가 데이터 갱신과 같은 advisory lock을 잡는다. 실행 중이면 수집하지 않고 종료 코드 2.
  `--dry-run`은 락을 잡지 않는다. `scripts/refresh_data`는 락을 잡은 채 이 스크립트의 `main()`을 직접 부르므로 그대로다.
- 뉴스·공시 DB 유니크 제약은 넣지 않았다(이유는 `docs/DATA_QUALITY_AND_TRADING_CALENDAR.md` "중복 방지 구조").
