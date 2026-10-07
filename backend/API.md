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
| **인증 필요** | `/api/auth/me`, `/api/research`(POST/GET), `/api/research/{report_id}`(GET/DELETE), `/api/watchlist`(GET/POST), `/api/watchlist/{ticker}`(DELETE), `/api/portfolio`(GET), `/api/portfolio/orders`(POST), `/api/portfolio/trades`(GET), `/api/portfolio/reset`(POST) |
| 공개 | `/api/auth/signup`, `/api/auth/login`, `/api/disclaimer`, `/api/stocks/{ticker}/realtime-price`, `/api/companies`, `/api/discovery/trending` |

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
| GET | `/api/companies` | 공개 | 종목 목록/검색(`q`로 종목명·티커 부분 일치, 별칭 포함) + 최근 종가·등락률 |
| GET | `/api/discovery/trending` | 공개 | 급등/급락/거래량 급증 종목 |
| GET | `/api/watchlist` | 필요 | 내 관심종목 목록 |
| POST | `/api/watchlist` | 필요 | 관심종목 추가 |
| DELETE | `/api/watchlist/{ticker}` | 필요 | 관심종목 삭제 |
| GET | `/api/portfolio` | 필요 | 내 모의투자 잔고 + 보유 종목(첫 호출 시 계좌 자동 생성) |
| POST | `/api/portfolio/orders` | 필요 | 모의투자 매수/매도 주문 |
| GET | `/api/portfolio/trades` | 필요 | 내 체결 내역(최신순, `limit`/`offset` 페이지네이션, `ticker` 필터) |
| POST | `/api/portfolio/reset` | 필요 | 내 모의투자 초기화(보유·체결 내역 삭제 + 잔고 초기값). 본문 `{"confirm": true}` 필수 |
| GET | `/api/disclaimer` | 공개 | 서비스 면책 문구(`{"text": "..."}`) |

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
| `used_tools` | string[] | 실제로 실행된 tool 이름(호출 순서). `stock_tool`, `news_tool`, `disclosure_tool`, `rag_search_tool`, `market_tool`(시장 지수 비교 질문일 때만) 중. 개념 질문 등은 **빈 배열**일 수 있다 |
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

```bash
curl "http://localhost:8000/api/companies?q=삼성전자"
curl "http://localhost:8000/api/companies?q=네이버"   # 별칭 -> NAVER
curl "http://localhost:8000/api/companies?limit=10"  # q 없이 상위 10개
```

### 응답 200

```json
[
  {
    "ticker": "005930",
    "name": "삼성전자",
    "market": "KOSPI",
    "sector": null,
    "latest_close": 286500.0,
    "change_pct": 3.24
  }
]
```

`latest_close`/`change_pct`는 DB에 저장된 **가장 최근 종가**(일별, 실시간 아님) 기준이다 - 실시간 시세가 필요하면
`GET /api/stocks/{ticker}/realtime-price`를 따로 호출하세요. 그 종목에 저장된 주가가 아직 없으면 둘 다 `null`.
검색 결과가 없으면 `[]`(에러 아님).

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 422 | `limit`이 1~300 범위 밖, 또는 `q`가 100자 초과 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

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
      "added_at": "2026-10-01T02:29:00.729024+00:00",
      "latest_close": 286500.0,
      "latest_close_date": "2026-09-23",
      "change_pct": 3.24
    }
  ]
}
```

종목에 저장된 주가가 아직 없으면 `latest_close`/`latest_close_date`/`change_pct`가 `null`이다. 관심종목이 없으면 `items: []`.

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
  "added_at": "2026-10-01T02:29:00.729024+00:00",
  "latest_close": null,
  "latest_close_date": null,
  "change_pct": null
}
```

(POST 응답에는 최근 종가를 다시 조회해 채우지 않는다. 바로 보여주려면 GET으로 다시 조회하세요.)

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
| `holdings[].avg_price` | number | 평균 매입 단가(가중평균, 매도로는 바뀌지 않음) |
| `holdings[].current_price` | number \| null | 현재가. 실시간+DB 폴백 모두 실패하면 `null`(이 종목은 평가손익 계산에서 제외되고 `note`에 안내) |
| `holdings[].is_realtime` | boolean | `current_price`가 실시간 조회 성공값이면 `true`, DB 폴백이면 `false` |
| `holdings[].eval_amount` / `profit_loss` / `profit_loss_pct` | number \| null | 평가금액 / 평가손익(금액) / 평가손익률(%). `current_price`가 `null`이면 같이 `null` |
| `total_eval_amount` | number | 보유 종목 평가금액 합(가격을 못 구한 종목은 0으로 취급돼 빠짐) |
| `total_asset` | number | `cash_balance + total_eval_amount` |
| `note` | string \| null | 가격을 못 구한 종목이 있으면 안내 문구, 없으면 `null` |

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
| `items[].side` | `"buy"` 또는 `"sell"` |
| `items[].price` | 체결가 |
| `items[].amount` | 체결 금액 = `price × quantity`(서버가 계산, trade 테이블에는 없는 값) |
| `items[].executed_at` | ISO 8601(UTC, `+00:00`) |

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

## 인증 관련 한계 (2026-10-07 기준)

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
- 데이터는 수동으로 갱신한다. 2026-09-28 기준 주가는 9/23, 뉴스는 9/24, 공시는 9/22까지이고 코스피/코스닥 지수는 9/17까지다. "오늘 주가"를
  물어도 DB의 최신 값(위 날짜)으로 답한다.

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

`cd backend && pytest` — 단위 테스트 + dev DB 통합 테스트(DB가 꺼져 있으면 자동 skip). LLM을 실제로 호출하는 느린 테스트는 기본 제외이며 `pytest -m slow`로 따로 돌린다(dev DB에 리포트를 만들었다가 지운다). 전부 한 번에: `pytest -m "slow or not slow"`. DB 없이 단위 테스트만: `pytest -m "not integration"`.
인증 테스트는 `tests/conftest.py`가 테스트마다 임의 `JWT_SECRET`을 주입하므로 `.env`의 실제 키를 쓰지 않는다. 라우터 테스트는
`login_as` 픽스처로 `get_current_user`를 가짜 사용자로 바꾸고, dev DB 통합 테스트(`tests/test_auth_devdb.py`)는 실제 가입/토큰으로
돌린 뒤 만든 사용자를 지운다(`pt_` 접두어).
