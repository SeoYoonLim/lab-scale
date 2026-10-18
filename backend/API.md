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
| **인증 필요** | `/api/auth/me`, `/api/research`(POST/GET), `/api/research/{report_id}`(GET/DELETE), `/api/watchlist`(GET/POST), `/api/watchlist/{ticker}`(DELETE), `/api/portfolio`(GET), `/api/portfolio/orders`(POST), `/api/portfolio/trades`(GET), `/api/portfolio/reset`(POST), `/api/portfolio/diagnosis`(POST), `/api/portfolio/diagnosis/history`(GET), `/api/portfolio/diagnosis/{id}`(GET/DELETE), `/api/portfolio/diagnosis/{id}/compare`(GET) |
| 공개 | `/api/auth/signup`, `/api/auth/login`, `/api/disclaimer`, `/api/stocks/{ticker}/realtime-price`, `/api/companies`, `/api/discovery/trending`, `/api/status`, `/api/stocks/{ticker}/event-reactions`, `/api/stocks/{ticker}/price-history` |

- 데이터는 **사용자별로 분리**된다. 리서치 리포트 목록/조회/삭제/이어 쓰기(`previous_report_id`)는 본인 것만 대상이고,
  **남의 리포트 ID는 없는 리포트와 똑같이 404**다(403이 아니다 — 그 ID가 존재하는지 드러내지 않기 위해).
  관심종목/모의투자도 사용자마다 따로다.
- 로그인 도입 전에 만들어진 리포트(소유자 없음)와 `X-Device-Id`로 만든 관심종목/모의투자 데이터는 DB에 남아 있지만
  어떤 사용자에게도 보이지 않는다(새 계정으로 이전되지 않음).
- 서버는 세션/쿠키를 쓰지 않는다. 로그아웃은 프론트가 저장한 토큰을 지우는 것으로 끝난다(서버 측 토큰 폐기 없음, 아래 "한계").

### 인증 에러 (401)

토큰이 없거나, `Bearer` 형식이 아니거나, 서명이 틀리거나(변조/다른 서버 키), 만료됐거나, 토큰의 사용자가 삭제됐으면
**전부 같은 401**이다(사유를 구분해 알려주지 않는다). 응답 헤더에 `WWW-Authenticate: Bearer`가 붙는다.

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
6. **앱 시작 시**: 저장된 토큰이 있으면 `GET /api/auth/me`로 유효한지 확인하고 사용자 이름을 표시한다(401이면 5번).
7. **CORS**: `Authorization` 헤더를 보내면 브라우저가 preflight(OPTIONS)를 먼저 보낸다. 허용 origin(아래)에서는 그대로 통과한다.

## CORS

허용 origin은 아래 두 개뿐이다.

- `http://localhost:5173` (Vite)
- `http://localhost:3000` (CRA / Next)

**프론트가 다른 포트나 도메인에서 뜬다면 미리 알려주세요.** `backend/app/main.py`의 `allow_origins`에 추가해야 하고,
추가 전에는 브라우저가 CORS 오류로 요청을 막습니다(curl/Postman은 영향 없음).

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
- **ETF 상품 정보(구성종목, 추종지수, 운용보수, 순자산, 괴리율)는 수집하지 않는다**. 그래서 리서치 답변은 이런 내용을 말하지 않도록
  막았다(모르는 것을 지어내지 않게). 레버리지·인버스 구조 설명은 이름으로 판별한 고정 문구뿐이다.
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
| GET | `/api/auth/me` | 필요 | 토큰의 사용자 정보 |
| POST | `/api/research` | 필요 | 질문을 보내 AI 답변 생성(+리포트 저장). `previous_report_id`로 내 직전 보고서를 이어서 후속 질문 가능 |
| GET | `/api/research` | 필요 | 내 리포트 목록(최신순, 페이지네이션) |
| GET | `/api/research/{report_id}` | 필요 | 내 리포트 한 건 조회 |
| DELETE | `/api/research/{report_id}` | 필요 | 내 리포트 한 건 삭제(tool 호출 이력 포함) |
| GET | `/api/stocks/{ticker}/realtime-price` | 공개 | 종목 현재가(비공식 소스 기반 실시간 시세, 장외/장애 시 자동 폴백) |
| GET | `/api/companies` | 공개 | 종목 목록/검색(`q`로 종목명·티커 부분 일치, 별칭 포함) + 최근 종가·등락률. `asset_type=stock\|etf`로 주식/ETF만 |
| GET | `/api/discovery/trending` | 공개 | 급등/급락/거래량 급증 종목(기본은 주식만, `asset_type=etf`로 ETF) |
| GET | `/api/watchlist` | 필요 | 내 관심종목 목록 |
| POST | `/api/watchlist` | 필요 | 관심종목 추가 |
| DELETE | `/api/watchlist/{ticker}` | 필요 | 관심종목 삭제 |
| GET | `/api/portfolio` | 필요 | 내 모의투자 잔고 + 보유 종목(첫 호출 시 계좌 자동 생성) |
| POST | `/api/portfolio/orders` | 필요 | 모의투자 매수/매도 주문 |
| GET | `/api/portfolio/trades` | 필요 | 내 체결 내역(최신순, `limit`/`offset` 페이지네이션, `ticker` 필터) |
| POST | `/api/portfolio/reset` | 필요 | 내 모의투자 초기화(보유·체결 내역 삭제 + 잔고 초기값). 본문 `{"confirm": true}` 필수 |
| POST | `/api/portfolio/diagnosis` | 필요 | 내 포트폴리오 AI 진단(지표·플래그는 코드 계산, 설명은 LLM 또는 규칙 기반 폴백). 결과를 저장하고 `diagnosis_id`·`created_at`을 붙인다 |
| GET | `/api/portfolio/diagnosis/history` | 필요 | 내 진단 이력 목록(최신순, `limit`/`offset` 페이지네이션, 최근 50건 보관) |
| GET | `/api/portfolio/diagnosis/{id}` | 필요 | 내 진단 한 건(저장된 진단 응답 그대로) |
| GET | `/api/portfolio/diagnosis/{id}/compare` | 필요 | 이전 진단과 비교(코드로만 계산). `base`로 기준 진단 지정 |
| DELETE | `/api/portfolio/diagnosis/{id}` | 필요 | 내 진단 한 건 삭제 |
| GET | `/api/disclaimer` | 공개 | 서비스 면책 문구(`{"text": "..."}`) |
| GET | `/api/status` | 공개 | 데이터 기준일(소스별 최신 날짜, 지수 stale 여부, 마지막 갱신 결과). 푸터의 "데이터 기준일" 표시용 |
| GET | `/api/stocks/{ticker}/price-history` | 공개 | 주가 추이 차트용 일별 시세(종가·거래량·등락률·시가·고가·저가). `days`는 1~90(기본 90)이고 **최근 N거래일**(달력 일 아님). 날짜 오름차순. 주식은 2026-06-26부터, ETF는 2026-07-16부터 데이터가 있어 90개가 안 될 수 있다(`count`로 확인) |
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
| `password` | string | 필수. **8~128자**(문자 종류 제한 없음). 위반 시 422 |

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
| 422 | 아이디/비밀번호 규칙 위반, 필드 누락, 잘못된 JSON | 검증 오류 형식. 단 `/api/auth/*`의 422에는 **`input` 키가 없다**(입력한 비밀번호를 되돌려주지 않기 위해) |
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
{ "id": 1, "username": "alice_01" }
```

### 에러

401(토큰 없음/만료/변조/삭제된 사용자, 위 "인증 에러" 참고).

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

참고: 종목이 DB에 없으면 오류가 아니라 200 응답의 `answer`에 "찾지 못했다"는 안내가 담긴다.

#### 답변 품질 보정 (2026-10-11, 응답 필드는 그대로)

LLM이 tool을 고른 뒤 서버 코드가 아래 네 가지를 보정한다. 요청/응답 형식은 바뀌지 않았다.

- **역할 접두어 제거**: `answer`가 `assistant\n\n`으로 시작하던 문제(모델의 역할 헤더가 본문으로 샌 것)를 서버에서
  떼어낸다. 프론트에서 따로 처리할 필요 없다. 이미 저장된 예전 리포트의 `answer`는 그대로다.
- **흐름 질문의 기본 기간**: 기간을 말하지 않고 주가의 흐름/추이/추세/동향/움직임 또는 "최근(요즘) 주가·상황"을
  묻는 질문은 최근 **20거래일**을 조회한다(예: "삼성전자 최근 주가 흐름 알려줘"). 질문에 기간이 있으면
  ("지난주", "최근 3일", "한 달", "5거래일") 기본값을 쓰지 않고 그 기간을 따른다("지난주"·"최근 3일"처럼 서버가
  인식하는 표현은 서버가 계산한 거래일 수, "한 달"처럼 인식하지 못하는 표현은 모델이 추론한 값 — "한 달"은 보통
  30거래일로 조회된다). 여러 거래일을 조회한 답변에는 기간 등락률
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
| 503 | Ollama 서버에 연결 불가 | `{"detail": "AI 모델 서버(Ollama)에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |
| 500 | 그 외 예상 못 한 서버 오류 | 본문이 JSON이 아닌 plain text `Internal Server Error` |

---

## GET /api/research

내 리포트 목록(최신순). `total`도 내 리포트 수다.

> **Breaking change:** 응답이 배열이 아니라 `{"total": int, "items": [...]}` envelope이다.
> 예전처럼 응답을 바로 배열로 다루던 코드는 `response.items`로 바꿔야 한다.

### 쿼리 파라미터

| 이름 | 기본값 | 범위 | 설명 |
| --- | --- | --- | --- |
| `limit` | 20 | 1~100 | 한 페이지 건수 |
| `offset` | 0 | 0 이상 | 건너뛸 건수 |

범위를 벗어나면 422.

### 응답 200

```json
{
  "total": 16,
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
| `total` | 저장된 전체 리포트 수(`limit`/`offset`과 무관). 페이지 수 계산용 |
| `items[].report_id` | 리포트 ID |
| `items[].previous_report_id` | 이어받은 직전 리포트 ID. 독립 질문이거나 직전 리포트가 삭제됐으면 null |
| `items[].question` | 원래 질문 |
| `items[].summary` | 답변 앞부분 미리보기(약 200자, 공백 정리됨). null일 수 있음 |
| `items[].company_name` | 질문에서 다룬 종목이 **정확히 1개일 때만** 채워지고, 0개나 2개 이상이면 null |
| `items[].created_at` | ISO 8601(UTC, `+00:00`) |
| `items[].used_tools` | 사용한 tool 이름 목록 |

목록에는 `answer`와 `sources`가 없고 `disclaimer`도 없다(상세 조회에서 제공). 다음 페이지: `offset += limit`, `offset >= total`이면 끝.
`offset`이 `total` 이상이면 `items`가 빈 배열인 200이다.

```bash
curl "http://localhost:8000/api/research?limit=2&offset=1"
```

### 에러

- 401: 토큰 없음/만료/변조 (위 "인증 에러")
- 422: `limit`/`offset`이 범위 밖이거나 정수가 아님 (검증 오류 형식)
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

```bash
curl "http://localhost:8000/api/companies?q=삼성전자"
curl "http://localhost:8000/api/companies?q=네이버"   # 별칭 -> NAVER
curl "http://localhost:8000/api/companies?limit=10"  # q 없이 상위 10개
curl "http://localhost:8000/api/companies?q=코덱스200&asset_type=etf"   # ETF만, 한글 브랜드 표기도 된다
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
| 422 | `limit`이 1~300 범위 밖, `q`가 100자 초과, 또는 `asset_type`이 `stock`/`etf`가 아님 | 검증 오류 형식 |
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
| `ticker` | string | 필수. 종목코드 또는 종목명(별칭/유사 종목명 포함). 1~50자 |
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

#### 평균단가 계산

- **매수**: 기존 보유가 없으면 평균단가 = 체결가. 있으면 가중평균 `(기존수량×기존평균 + 이번수량×체결가) / 합산수량`으로 다시 계산한다.
- **매도**: 평균단가는 바뀌지 않는다(남은 수량의 매입 단가는 그대로). 전량 매도되면 보유 레코드 자체를 지운다 -
  그래야 그 종목을 다시 사기 시작할 때 과거 평균단가에 영향받지 않고 새 체결가로 시작한다.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 400 | 매수 시 잔고 부족 | `{"detail": "잔고가 부족합니다(필요 ...원, 보유 ...원)."}` |
| 400 | 매도 시 보유 수량 초과 | `{"detail": "보유 수량이 부족합니다(매도 요청 ...주, 보유 ...주)."}` |
| 404 | 종목을 찾지 못함(`company_resolver` 기준) | `{"detail": "'...' 종목을 찾지 못했습니다. ..."}` |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 422 | `ticker`/`side`/`quantity` 형식 위반 | 검증 오류 형식 |
| 503 | 실시간 소스와 DB 폴백 모두 가격을 못 구함(둘 다 없을 때만) | `{"detail": "'...'의 가격을 구하지 못해 주문을 체결할 수 없습니다."}` |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## GET /api/portfolio/trades

내 모의투자 체결 내역(최신순: 체결 시각 내림차순, 같으면 id 내림차순). 로그인한 사용자 본인 것만 나온다.

| 쿼리 | 기본값 | 규칙 |
| --- | --- | --- |
| `limit` | 50 | 1~200. 범위를 벗어나면 422 |
| `offset` | 0 | 0 이상. 벗어나면 422 |
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

체결이 없으면 오류가 아니라 `{"total": 0, "limit": 50, "offset": 0, "items": []}`인 200이다. 다음 페이지는 `offset += limit`,
`offset >= total`이면 끝이다.

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 401 | 토큰 없음/만료/변조 | 위 "인증 에러" |
| 404 | `ticker`로 종목을 찾지 못함 | `{"detail": "'...' 종목을 찾지 못했습니다. ..."}` |
| 422 | `limit`/`offset` 범위 밖이거나 정수가 아님, `ticker`가 빈 문자열/50자 초과 | 검증 오류 형식 |
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

내 진단 이력(최신순). 이력이 없으면 `total: 0`, `items: []`.

### 쿼리 파라미터

| 이름 | 기본값 | 규칙 |
| --- | --- | --- |
| `limit` | 20 | 1~100. 범위 밖이면 422 |
| `offset` | 0 | 0 이상. 음수면 422 |

```bash
curl "http://localhost:8000/api/portfolio/diagnosis/history?limit=20&offset=0" -H "Authorization: Bearer $TOKEN"
```

### 응답 200

```json
{
  "total": 2,
  "limit": 20,
  "offset": 0,
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
| 422 | `limit`/`offset`이 범위 밖이거나 숫자가 아님 | 검증 오류 형식 |
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
| `sources.market_index.stale` | boolean | 최근 거래일(평일 기준) 대비 **2영업일 이상** 뒤처졌거나 데이터가 없으면 `true`. 주말은 세지 않고 공휴일은 모른다 |
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

### 갱신 스크립트와 기록 테이블

데이터는 스케줄러 없이 `python -m scripts.refresh_data`(backend 디렉터리)로 수동 갱신한다. 평일 **15:30(KST) 이후** 실행을 권장한다.
장중에 실행하면 당일 행이 장중 값일 수 있어 경고를 출력하며, 15:30 이후 다시 실행하면 같은 날짜가 종가로 덮어써진다(upsert).

| 옵션 | 설명 |
| --- | --- |
| `--only prices,index,fx,news,embed,content` | 쉼표로 구분한 단계만 실행 |
| `--skip ...` | 같은 형식으로 단계를 제외(`--only`와 함께 쓰면 그 결과에서 뺀다) |
| `--days N` | 주가·지수·환율 조회 기간(달력 일), 기본 10 |
| `--timeout-min N` | 단계별 제한 시간(분), 기본 30. 넘으면 그 단계만 `failed`(`StepTimeout`)로 기록하고 다음 단계로 진행 |
| `--dry-run` | 실제 호출·DB 기록 없이 계획만 출력 |

한 단계가 실패해도 다음 단계로 계속 진행하고, 마지막에 단계별 결과 표를 출력하며 하나라도 실패하면 종료 코드 1이다.
종목별 수집 실패가 절반 미만이면 경고만 하고 그 단계는 성공으로 본다.

`data_refresh_log` 테이블(마이그레이션 `c5d8f3a07b14`)에는 실행마다 **6단계 모두 한 행씩**(건너뛴 단계는 `skipped`) 기록한다:
`id`, `step`, `status`(`success`/`failed`/`skipped`), `started_at`, `finished_at`, `rows_added`(실행 전후 행 수 차이, 임베딩·본문 단계는 채워진 건수), `latest_date`, `error_type`(예외 클래스 이름만, 메시지는 저장하지 않는다).

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
| `days` | 90 | 정수 1~90. **달력 일이 아니라 최근 N거래일**이다(그 종목의 저장된 일별 주가 행 수 기준, 주말·휴장일은 없는 날). 범위를 벗어나면 422 |

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

값이 없는 칸은 `null`이다(필드는 항상 있다). 숫자는 DB에 저장된 값 그대로다.

한계:

- **데이터 시작일이 있다.** 2026-10-18 현재 주식은 2026-06-26부터, ETF는 2026-07-16부터 시세가 있어 `days=90`이어도 `count`가 90보다 작을 수 있다
  (삼성전자 73개, KODEX 200 59개). 날짜가 쌓이면 늘어난다. 화면에는 `count`를 기준으로 그리고 90개를 가정하지 않는다.
- **일별 저장 데이터**라 장중 시세가 아니다. 마지막 날짜는 `price_as_of`이고, 오늘 현재가는 `GET /api/stocks/{ticker}/realtime-price`를 쓴다.
- 분할·배당 등에 대한 수정주가 보정은 하지 않는다(수집된 값 그대로).
- 주가 행이 아직 없는 종목은 200에 `items=[]`, `count=0`, `price_as_of=null`이다.

| 상태 | 원인 | 응답 |
| --- | --- | --- |
| 404 | 종목을 찾지 못함 | `{"detail": "..."}` (다른 종목 API와 같은 메시지 형식) |
| 422 | `days`가 정수가 아니거나 1~90 밖 | 검증 오류 형식 |
| 503 | DB 연결 불가 | 다른 API와 같음 |

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

## 에러 응답 형식

### 일반 오류 (400 / 401 / 404 / 409 / 502 / 503)

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

## 인증 관련 한계 (2026-10-07 기준, 2026-10-14에도 그대로)

- **로그인 시도 횟수 제한(rate limit)이 없다.** 같은 아이디에 비밀번호를 무한히 대입해볼 수 있다. 외부에 공개하기 전에
  IP/아이디 기준 제한(또는 리버스 프록시 단의 제한)을 넣어야 한다.
- **토큰 폐기(로그아웃/비밀번호 변경 시 무효화)가 없다.** 발급된 토큰은 만료(24시간)까지 유효하다. 서버의 `JWT_SECRET`을
  바꾸면 모든 토큰이 한꺼번에 무효가 된다.
- refresh token이 없다. 24시간이 지나면 다시 로그인해야 한다.
- 비밀번호 변경/재설정, 회원 탈퇴 API는 없다.
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
- `GET /api/status`의 `stale`은 공휴일을 모르는 평일 기준이다. 환율은 소스가 주는 날짜까지만 있어 보통 1~2일 늦다(2026-10-14 기준 10/12).
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

## 테스트 실행 (백엔드)

`cd backend && pytest` — 빠른 테스트 전체(단위·라우터 mock + dev DB 통합, 2026-10-18 기준 2074개). 단계별로는 `pytest -m "not slow and not integration"`(DB·Ollama 불필요, 1864개), `pytest -m "integration and not slow"`(dev DB 통합 210개, DB가 꺼져 있으면 자동 skip). 사건 반응 slow 측정은 `EVENT_REACTION_LLM_INTERPRETATION`을 스스로 켜고 돈다. LLM을 실제로 호출하는 느린 테스트는 기본 제외이며 `pytest -m slow`로 따로 돌린다(dev DB에 리포트를 만들었다가 지운다). 전부 한 번에: `pytest -m "slow or not slow"`. DB 없이 단위 테스트만: `pytest -m "not integration"`.
인증 테스트는 `tests/conftest.py`가 테스트마다 임의 `JWT_SECRET`을 주입하므로 `.env`의 실제 키를 쓰지 않는다. 라우터 테스트는
`login_as` 픽스처로 `get_current_user`를 가짜 사용자로 바꾸고, dev DB 통합 테스트(`tests/test_auth_devdb.py`)는 실제 가입/토큰으로
돌린 뒤 만든 사용자를 지운다(`pt_` 접두어).
