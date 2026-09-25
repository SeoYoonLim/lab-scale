# AI 투자 리서치 에이전트 API

프론트엔드 연동용 문서. 아래 예시 응답은 실제 서버(`uvicorn app.main:app --port 8000`)를 호출해 얻은 결과이며,
긴 필드(answer, sources)만 잘라서 표기했다.

- Base URL (로컬): `http://localhost:8000`
- 요청/응답은 모두 JSON(UTF-8). 인증 없음.
- 자동 생성 문서: `http://localhost:8000/docs` (Swagger UI)

## CORS

허용 origin은 아래 두 개뿐이다.

- `http://localhost:5173` (Vite)
- `http://localhost:3000` (CRA / Next)

**프론트가 다른 포트나 도메인에서 뜬다면 미리 알려주세요.** `backend/app/main.py`의 `allow_origins`에 추가해야 하고,
추가 전에는 브라우저가 CORS 오류로 요청을 막습니다(curl/Postman은 영향 없음).

## 엔드포인트 요약

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/api/research` | 질문을 보내 AI 답변 생성(+리포트 저장) |
| GET | `/api/research` | 저장된 리포트 목록(최신순, 페이지네이션) |
| GET | `/api/research/{report_id}` | 저장된 리포트 한 건 조회 |

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
  "report_id": 18
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `answer` | string | 최종 답변(한국어, 줄바꿈 `\n` 포함, 마크다운 목록 문법이 섞여 올 수 있음) |
| `used_tools` | string[] | 실제로 실행된 tool 이름(호출 순서). `stock_tool`, `news_tool`, `disclosure_tool`, `rag_search_tool` 중. 개념 질문 등은 **빈 배열**일 수 있다 |
| `sources` | object[] | 답변의 근거 문서(뉴스/공시). 주가만 조회했거나 tool을 안 썼으면 **빈 배열** |
| `sources[].tool` | string | 이 근거를 가져온 tool |
| `sources[].type` | `"news"` \| `"disclosure"` | 근거 종류 |
| `sources[].title` | string | 기사/공시 제목 |
| `sources[].company_names` | string[] | 관련 종목명 |
| `sources[].company_filter` | string \| null | rag 검색에서 종목으로 범위를 좁힌 경우 그 종목명, 아니면 null |
| `sources[].url` | string \| null | 원문 링크. 없으면 null |
| `report_id` | int \| null | 저장된 리포트 ID. `GET /api/research/{report_id}`로 다시 조회 가능. **저장에 실패하면 null**(답변 자체는 정상 반환) |

참고: 종목이 DB에 없으면 오류가 아니라 200 응답의 `answer`에 "찾지 못했다"는 안내가 담긴다.

### 예시 curl

```bash
curl -X POST http://localhost:8000/api/research \
  -H "Content-Type: application/json" \
  -d '{"question": "삼성전자 최근 3일 등락률이랑 거래량 알려줘."}'
```

### 에러

| 상태 | 언제 | body |
| --- | --- | --- |
| 422 | `question` 누락 / 공백뿐 / 1000자 초과 / 잘못된 JSON | 아래 "검증 오류(422)" 형식 |
| 502 | Ollama가 응답은 했으나 오류 반환(모델 미설치 등) | `{"detail": "AI 모델 서버가 오류를 반환했습니다. 잠시 후 다시 시도해주세요."}` |
| 503 | Ollama 서버에 연결 불가 | `{"detail": "AI 모델 서버(Ollama)에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |
| 500 | 그 외 예상 못 한 서버 오류 | 본문이 JSON이 아닌 plain text `Internal Server Error` |

---

## GET /api/research

저장된 리포트 목록(최신순).

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
| `items[].question` | 원래 질문 |
| `items[].summary` | 답변 앞부분 미리보기(약 200자, 공백 정리됨). null일 수 있음 |
| `items[].company_name` | 질문에서 다룬 종목이 **정확히 1개일 때만** 채워지고, 0개나 2개 이상이면 null |
| `items[].created_at` | ISO 8601(UTC, `+00:00`) |
| `items[].used_tools` | 사용한 tool 이름 목록 |

목록에는 `answer`와 `sources`가 없다(상세 조회에서 제공). 다음 페이지: `offset += limit`, `offset >= total`이면 끝.
`offset`이 `total` 이상이면 `items`가 빈 배열인 200이다.

```bash
curl "http://localhost:8000/api/research?limit=2&offset=1"
```

### 에러

- 422: `limit`/`offset`이 범위 밖이거나 정수가 아님 (검증 오류 형식)
- 503: DB 연결 불가 (위와 동일한 body)

---

## GET /api/research/{report_id}

저장된 리포트 한 건(전체 답변 + sources).

### 응답 200

```json
{
  "report_id": 18,
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
| 404 | 해당 ID의 리포트가 없음 | `{"detail": "report_id=999999 리포트를 찾을 수 없습니다."}` |
| 422 | ID가 정수가 아니거나 1 미만/BIGINT 초과 | 검증 오류 형식 |
| 503 | DB 연결 불가 | `{"detail": "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요."}` |

---

## 에러 응답 형식

### 일반 오류 (404 / 502 / 503)

항상 `detail`이 **문자열**이다. 사용자에게 그대로 보여줘도 되는 한국어 메시지.

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

## 알아둘 점

- `answer`는 로컬 LLM(llama3.1:8b)이 생성하므로 같은 질문에도 매번 표현이 다르고, 뉴스가 질문 종목과 무관해 보이는 경우나 모델의 사실 오류가 섞일 수 있다. 근거는 `sources`로 확인하도록 UI에 링크를 노출하는 것을 권장한다.
- `POST`는 성공할 때마다 리포트를 저장한다(`report_id`). 테스트 호출도 목록에 쌓인다.

## 테스트 실행 (백엔드)

`cd backend && pytest` — 단위 테스트 + dev DB 통합 테스트(DB가 꺼져 있으면 자동 skip). LLM을 실제로 호출하는 느린 테스트는 기본 제외이며 `pytest -m slow`로 따로 돌린다. DB 없이 단위 테스트만: `pytest -m "not integration"`.
