# lab-scale — AI 투자 리서치 에이전트

한국 상장사(시가총액 상위 300종목)에 대해 "삼성전자 오늘 왜 올랐어? 주가랑 뉴스 같이 확인해줘" 같은 자연어 질문을 받으면,
로컬 LLM이 필요한 조회(주가·뉴스·공시·의미 검색)를 스스로 골라 실행하고 그 결과로 답변과 근거 링크를 돌려주는 리서치 서비스입니다.
2인 팀 프로젝트로, 백엔드/데이터/에이전트와 프론트엔드를 나눠서 진행하고 있습니다.

## 지금 상태 요약 (서윤님 복귀용, 2026-09-28 기준)

**한 줄 요약:** 백엔드(API + 에이전트 + 데이터 수집)는 동작하고 테스트가 전부 통과합니다(기본 232개 + 실제 Ollama를 부르는 slow 6개 = 238개,
`pytest -m "slow or not slow"`로 한 번에). 남은 큰 일은 프론트엔드 연동입니다. 작업 브랜치는 `feature/backend`이고 원격에 push되어 있습니다.

### 구현된 API

요청/응답 예시, 에러 형식, CORS는 전부 **[backend/API.md](backend/API.md)** 에 있습니다. 프론트 연동은 이 문서만 보면 됩니다.

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/api/research` | 질문 → AI 답변 + 근거 + 리포트 저장. `previous_report_id`로 직전 리포트를 이어서 후속 질문 |
| GET | `/api/research` | 저장된 리포트 목록(최신순, `limit`/`offset` 페이지네이션, `total` 포함) |
| GET | `/api/research/{report_id}` | 리포트 한 건(전체 답변, sources, tool 사용 이력) |
| DELETE | `/api/research/{report_id}` | 리포트 삭제(204, 본문 없음). 후속 리포트는 남고 연결만 끊김 |

프론트에서 특히 챙길 것: POST는 로컬 LLM이라 **2~25초**(로딩 상태와 60초 이상 타임아웃) / 에러 `detail`은 404·502·503에서는 문자열, 422에서는 배열
/ `report_id`는 저장 실패 시 null / 허용 origin은 `localhost:5173`, `localhost:3000` 두 개뿐(다른 포트면 알려주세요).

### DB 스키마 개요

PostgreSQL 16 + pgvector. **스키마의 기준은 Alembic 마이그레이션**(`backend/alembic/versions/`, 현재 head `0c0a80524432`)입니다.

| 테이블 | 내용 | 주요 컬럼 |
| --- | --- | --- |
| `company` | 종목 300개(시총 상위) | `ticker`(unique), `name`, `market`(KOSPI/KOSDAQ/KOSDAQ GLOBAL), `sector`(현재 비어 있음) |
| `stock_price` | 일별 OHLCV | `company_id`, `price_date`, `close_price`, `volume`, `change_pct` (종목+날짜 unique) |
| `market_index` | 코스피(KS11)/코스닥(KQ11) 일별 종가 | `index_code`, `price_date`, `close_price`, `change_pct` (지수+날짜 unique) |
| `news` | 네이버 뉴스 | `company_id`, `title`, `url`, `published_at`, `embedding vector(1024)` |
| `disclosure` | DART 공시 | `company_id`, `title`, `disclosed_at`, `source_url`, `content`(원문), `embedding vector(1024)` |
| `research_report` | 질문/답변 보고서 | `question`, `content`(답변), `summary`, `company_id`, `previous_report_id`(직전 리포트, 자기 참조) |
| `tool_call_log` | 보고서별 tool 호출 이력 | `report_id`(삭제 시 cascade), `tool_name`, `arguments`/`result`(JSONB) |

**서윤님이 설계한 스키마와 실제로 만들어진 스키마를 맞춰봐 주세요.** 최초 설계 스냅샷은 `db/schema.sql`(서윤님 커밋)이고, 임시 DB에 그 파일을 적용해서
실제 DB와 컬럼·인덱스·FK를 기계적으로 비교했습니다. 컬럼/타입/FK는 모두 같고 **차이는 아래 4가지**입니다(설계와 다른 것이 의도에 맞는지 확인 필요).

1. `market_index` 테이블 추가(FR-06, 설계에 없던 확장)
2. `research_report.previous_report_id` 컬럼 + 자기 참조 FK(`ON DELETE SET NULL`) 추가(FR-10, 설계에 없던 확장)
3. `news.embedding`, `disclosure.embedding`에 HNSW 인덱스(코사인, m=16, ef_construction=64) 추가(유사도 검색 성능)
4. 인덱스 정렬 방향: 설계는 `(company_id, 날짜 DESC)`, 실제는 오름차순. btree는 역방향 스캔이 되므로 조회 기능상 동일

`db/schema.sql`은 갱신하지 않고 상단에 "최초 설계 스냅샷이며 기준은 Alembic"이라는 안내만 달았습니다.

**회사명 별칭(alias):** `company.name`은 `NAVER`, `POSCO홀딩스`, `현대차`처럼 공식 표기라서 사용자가 쓰는 "네이버", "포스코홀딩스", "현대자동차"가 정확
일치하지 않습니다. 이 통칭은 지금 **코드 레벨 딕셔너리**(`backend/app/company_aliases.py`의 `COMPANY_ALIASES`, 별칭 → DB 등록명)로 처리하고 있고,
**이번 작업에서 DB 스키마는 변경하지 않았습니다**(스키마 변경은 팀 협의가 필요해서 제외). 별칭이 많아지면 DB 테이블(alias table)로 옮기는 방향을
서윤님과 논의할 예정입니다. `resolve_company`가 정확/공백·대소문자 일치 다음, 퍼지 매칭 앞에서 이 사전을 봅니다(상세는 아래 "해결된 이슈 기록").

### 데이터 현황 (2026-09-28 조회, 수집은 수동)

종목 300 · 주가 18,565행(6/26~9/23) · 지수 코스피/코스닥 각 116행(4/1~9/17) · 뉴스 3,013건(최신 9/24, 임베딩 완료) ·
공시 6,308건(최신 9/22, 원문 6,268건, 임베딩 완료) · 리포트 6건(1~6번은 **데모 데이터라 지우지 않습니다**).
지수는 FinanceDataReader가 오늘도 9/17까지만 줘서 종목 주가보다 며칠 늦습니다(우리 쪽 문제가 아니라 원천의 제한, 재확인함).

### FR 대응 현황

FR-01·07·11·12·13은 원본 PRD의 정의와 인수조건을 확인해서 **코드 기준으로 검증**했습니다(2026-09-28, 판단 근거는 표 아래 "인수조건별 검증").
나머지 FR-02~06, 08~10의 이름은 `db/schema.sql` 주석과 작업 지시에서 가져온 것이고 PRD 원문과 대조하지는 않았습니다. 특히 **FR-05는
`db/schema.sql`이 `research_report`를 FR-05용으로 표시한 것 말고는 PRD 정의를 확인하지 못했습니다**(PRD에서 "AI 리서치 보고서 생성"은 FR-11).
이 저장소에는 PRD 원문이 없어서 원본과 다시 맞춰봐 주세요.

| FR | 저장소에서 확인되는 근거 | 상태 |
| --- | --- | --- |
| FR-01 (P0) 자연어 투자 질문 분석 | `POST /api/research`, LLM tool calling + `company_resolver` + `_repair_company_args` + `routing.needs_market_tool` | **부분 구현**: 질문 입력·Tool 선택은 됨, 종목 식별은 됨(통칭은 별칭 사전으로 처리), 기간 식별은 LLM이 `period_days`로 바꿔 넘기는 수준(코드 파서 없음) |
| FR-02 주가·거래량 | `stock_price`, `stock_tool`, 300종목 수집 | 완료 |
| FR-03 뉴스 | `news`, `news_tool`, 네이버 수집 | 완료 |
| FR-04 공시 | `disclosure`, `disclosure_tool`, DART 목록+원문 | 완료 |
| FR-05 (PRD 정의 미확인) | `db/schema.sql`이 `research_report`를 FR-05용으로 표시. 저장·조회·삭제 API가 있음 | 스키마 표기 기준으로 구현됨, PRD 대조 필요 |
| FR-06 기업·산업·시장 요인 비교 | `market_tool` | **1차 완료**(시장 지수). 업종(섹터) 비교는 미구현 |
| FR-07 (P1) 경제지표 영향 분석 | 금리/환율 수집·저장·tool 코드와 DB 테이블이 없음. `db/schema.sql`도 "`economic_indicator`는 지금 만들지 않음"이라고 적음 | **미구현 - 신규 기능, 데이터 소스부터 필요** |
| FR-08 근거 임베딩/RAG | bge-m3 임베딩, `rag_search_tool` | 완료 |
| FR-09 tool 호출 이력 | `tool_call_log`, 리포트 상세의 `used_tools` | 완료 |
| FR-10 대화형 후속 질문 | `previous_report_id` | 완료(직전 1개까지, 체이닝은 범위 밖) |
| FR-11 (P1) AI 리서치 보고서 생성 | `agent._answer`(답변 자동 생성) + `reports.save_report` + `sources`(근거 문서). 프론트엔드 없음 | **백엔드 부분 구현 / 프론트 연동 필요**: 자동 생성·근거 포함은 됨, PRD가 말한 종합 "보고서 형식"(주가 동향·원인·뉴스·공시·시장 상황·위험요인)은 없음 |
| FR-12 (P2) 관심종목 및 리서치 이력 | 리서치 이력: `GET /api/research`, `GET /api/research/{id}`, `DELETE`. 관심종목: 테이블·API·코드 없음 | **리서치 이력은 완료(백엔드), 관심종목 기능은 미구현(신규 테이블 필요)** |
| FR-13 (P2) AI 시장 관심 종목 탐색 | 후보 탐색/스크리닝 코드, tool, 엔드포인트가 없음 | **미구현 - 신규 기능** |

#### 인수조건별 검증 (FR-01·07·11·12·13, 2026-09-28, 코드·측정 기준)

**FR-01 자연어 투자 질문 분석 — 부분 구현** (인수조건 3개)
- *자연어 질문 입력 가능*: 충족. `POST /api/research`의 `question`(1~1000자).
- *종목 및 기간 식별*: **종목은 충족(제한적), 기간은 제한적.**
  - 종목: LLM이 tool 인자로 종목을 뽑고 → `company_resolver.resolve_company`(정확 일치 → 공백/대소문자 무시 → 퍼지, 우선주 구분)로 DB 종목에
    맞추고 → 인자가 깨지면 `agent._repair_company_args`가 질문 원문(후속 질문이면 직전 질문)에서 `extract_from_text`로 되찾는다. 여러 종목("현대차랑 카카오")은
    종목별로 tool을 각각 부른다(데모 보고서 #5). "네이버" 같은 통칭은 `app/company_aliases.py`의 별칭 사전으로 등록명에 연결한다(아래 "해결된 이슈 기록").
  - 기간: **코드에는 기간을 해석하는 로직이 없다**(날짜 파서/정규식 없음). LLM이 "최근 일주일"을 `period_days=7`처럼 숫자로 바꿔 `stock_tool`/`market_tool`에
    넘길 뿐이다. 실제 LLM에 6회씩 던진 측정: 오늘/어제 → 1(각 6/6, 5/6), 최근 3일 → 3(6/6), 일주일 → 7(6/6), 한 달 → 30(6/6),
    3개월 → 약 90(4/6, **2/6은 3일로 오해석**), 올해 → 365(6/6). 이 숫자를 받는 쪽의 문제: `stock_tool`의 `period_days`는 달력 일수가 아니라
    **최근 N개 거래일 행**(30 → 실제 달력 42일)이고, **60을 넘으면 조용히 60행으로 잘리며 안내 필드가 없다**(3개월 초과·올해 요청). "어제/오늘"은 날짜를
    모르고 DB의 최신 행(현재 9/23)을 돌려준다. `news_tool`/`disclosure_tool`/`rag_search_tool`은 **기간 인자 자체가 없어서** "지난주 뉴스",
    "지난달 공시"도 그냥 최신 N건이다. `market_tool`만 인자 설명에 "거래일 수(일주일=5)"를 명시한다. 즉 기간 식별은 주가·시장 비교 두 tool에서만,
    그것도 LLM 해석에 의존한다.
- *필요한 분석 Tool 선택 가능*: 충족(신뢰도 한계 있음). 별도의 "분석 목적" 분류는 없고 tool 선택으로 암묵적으로 처리한다. `benchmark_routing.py` 측정: 주가/뉴스/공시 목록
  100%, 공시 "내용" 검색 → rag 77~89%(held-out 66~84%), 주가+뉴스 동시 호출(multi) 50~78%(측정마다 크게 흔들림, 대체로 60% 안팎),
  시장 비교 100%(키워드 게이트 사용, 독립 질문 세트 커버리지 12/14), 개념 질문의 tool 미호출 0%(no_tool 이슈).
- 관련 테스트: `test_company_resolver.py`(종목 해석 20개), `test_routing.py`(시장 게이트), `test_followup.py`(종목 인자 보정 포함), slow 통합 테스트.
  **기간 추출을 검증하는 테스트는 없다**(`market_tool`의 `period_days` 값 정리 테스트만 있음).

**FR-07 경제지표 영향 분석 — 미구현 (신규 기능, 데이터 소스부터 필요)** (인수조건 3개 모두 미충족)
- 저장소 전체 검색에서 금리/환율/경제지표를 수집·저장·조회하는 코드가 없다(검색에 걸린 것은 벤치마크·테스트의 예시 질문 문구뿐). DB 테이블 8개(`company`,
  `stock_price`, `market_index`, `news`, `disclosure`, `research_report`, `tool_call_log`, `alembic_version`) 중 관련 테이블도 없고, 에이전트 tool 5개 중 해당 없음.
- 데이터 소스 조사(코드는 건드리지 않음): 환율은 FinanceDataReader `USD/KRW`로 조회된다(2026-09-28 확인, 그날까지 제공). 금리는 시도한 FDR 심볼 2개가 404라
  확인하지 못했고 한국은행 ECOS 같은 별도 소스를 조사해야 한다. "관련 경제 이벤트 확인"에 쓸 이벤트 데이터의 출처도 정해야 한다.
- 지금은 "환율이 오르면…" 같은 질문에 데이터 조회 없이 LLM의 일반 지식으로만 답한다.

**FR-11 AI 리서치 보고서 생성 — 백엔드 부분 구현 / 프론트 연동 필요** (인수조건 3개)
- *분석 결과 기반 자동 보고서 생성*: **부분 충족.** tool 결과를 근거로 LLM이 답변을 자동 생성하고 `research_report`(`content`, `summary`)에 저장한다
  (`agent._answer`, `reports.save_report`). 그러나 PRD가 말한 구성(주가 동향, 주요 원인, 뉴스·공시, 시장 상황, 위험요인)을 한 번에 종합하는 **"보고서 형식"은 없다.** 답변은
  질문 하나에 대한 자유 서술이고(저장된 데모 6건: 43~314자, tool 1~2개), 섹션이나 위험요인을 요구하는 프롬프트·코드가 없다.
- *주요 근거 및 출처 포함*: **문서 근거는 충족, 수치 출처는 없음.** `sources`(뉴스/공시/RAG 문서의 제목·종목·URL, `app/sources.py`)가 POST·GET 응답에 들어가고 저장된
  `tool_call_log`에서 복원된다. 다만 주가·시장 지수 수치는 `sources`에 들어가지 않는다(주가만 쓴 데모 #3, #5는 `sources` 0건, `used_tools`로만 추적).
- *브라우저에서 보고서 확인*: 프론트엔드가 이 저장소에 없어서 **백엔드 API(`GET /api/research`, `/{id}`)까지만** 되어 있고 화면은 프론트 연동이 필요하다.

**FR-12 관심종목 및 리서치 이력 — 리서치 이력 완료, 관심종목 미구현** (인수조건 3개)
- *리서치 이력 조회*: 충족. `GET /api/research`(최신순, `limit`/`offset`, `total`), 후속 질문 스레드는 `previous_report_id`.
- *저장된 결과 재열람*: 충족. `GET /api/research/{id}`가 질문·답변·sources·used_tools를 복원한다(`DELETE`도 있음).
- *관심 종목 추가/삭제*: **미충족.** watchlist 테이블·API·코드가 전혀 없다(노출된 엔드포인트는 위 4개뿐, 관련 코드 검색 결과 없음). 신규 테이블이 필요하다.
  주의: 이 서비스에는 사용자/인증 개념이 없어서(API.md "인증 없음") 이력도 전체 공용이다. 관심종목을 만들려면 사용자 식별 방식부터 정해야 한다.

**FR-13 AI 시장 관심 종목 탐색 — 미구현 (신규 기능)** (인수조건 2개 모두 미충족)
- 후보 탐색·스크리닝·랭킹 코드, tool, 엔드포인트가 없다. `stock_tool`은 종목을 지정해야만 동작해서 "오늘 급등한 종목"을 찾는 질문에 쓸 tool이 없다.
- 재료는 DB에 있다: `stock_price`에 300종목의 등락률·거래량, `news`에 종목별 뉴스가 있어서 집계 쿼리로 후보를 뽑을 수는 있어 보이지만 아직 코드가 없다.

### 성능

PRD 비기능 요구(일반 API P95 500ms 이내)는 만족합니다. 조회 API는 실서버 기준 p95 약 4~5ms이고, 보고서가 5만 건이어도 DB 쿼리는
수십 ms입니다(POST는 AI 연산이라 예외). 측정 방법과 수치는 [backend/API.md](backend/API.md)의 "성능" 절에 있습니다.

## 아키텍처

```
브라우저(React + Vite, 서윤님 담당)
        │  HTTP/JSON  (CORS: localhost:5173, localhost:3000)
        ▼
FastAPI  ──  POST /api/research ─▶ 에이전트(app/agent.py)
(backend/app)                        │  Ollama llama3.1:8b, tool calling + few-shot
                                     ├─ stock_tool       주가/등락률/거래량
                                     ├─ news_tool        최신 뉴스
                                     ├─ disclosure_tool  최신 DART 공시
                                     ├─ rag_search_tool  bge-m3 임베딩 유사도 검색(뉴스+공시)
                                     └─ market_tool      종목 vs 시장(코스피/코스닥) 지수 등락률 비교
                                                         (시장 비교 질문일 때만 tool 목록에 동적 추가)
                                             │
                                     PostgreSQL 16 + pgvector (docker)
                                     company · stock_price · market_index · news · disclosure
                                     research_report · tool_call_log
```

- **백엔드:** FastAPI, SQLAlchemy 2, Alembic. 질문/답변/tool 호출 이력은 매 요청마다 DB에 저장되고 `GET /api/research`로 다시 볼 수 있습니다.
- **모델:** 답변·tool 선택은 `llama3.1:8b`, 임베딩은 `bge-m3`(1024차원, HNSW 인덱스). 모두 로컬 Ollama에서 돌아갑니다.
- **데이터 소스:** 주가와 시장 지수(코스피 KS11, 코스닥 KQ11)는 FinanceDataReader, 뉴스는 네이버 검색 API, 공시는 DART OpenAPI(목록 + 원문).
- **프론트엔드:** React + Vite로 서윤님이 진행 중입니다. 이 저장소(`feature/backend` 브랜치)에는 프론트엔드 코드가 아직 없어서 진행 상황은 여기서 확인하지 못했습니다.

## 로컬 실행

필요한 것: Docker, Python 3.13(개발 환경 기준), [Ollama](https://ollama.com/download). 아래 명령은 별도 표기가 없으면 `backend/`에서 실행합니다.

**1. DB 실행** (저장소 루트에서)

```bash
docker compose up -d      # pgvector/pgvector:pg16, 호스트 포트 15432 (5432는 Windows 예약 포트라 피함)
```

**2. 환경 변수** — `backend/.env`를 만듭니다(`.gitignore` 대상이라 커밋되지 않습니다).

```
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment
NAVER_CLIENT_ID=...       # 뉴스 수집에만 필요
NAVER_CLIENT_SECRET=...   # 뉴스 수집에만 필요
DART_API_KEY=...          # 공시 수집에만 필요
```

`DATABASE_URL`을 생략하면 위 값이 기본값으로 쓰입니다. API 서버만 띄우고 이미 채워진 DB를 쓸 때는 네이버/DART 키가 필요 없습니다.

**3. 파이썬 환경과 스키마**

```bash
python -m venv .venv
.venv\Scripts\activate              # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head                # 테이블, pgvector 확장, HNSW 인덱스 생성
```

**4. 모델 받기**

```bash
ollama pull llama3.1:8b
ollama pull bge-m3
```

(`qwen2.5:7b-instruct`는 `scripts/benchmark_models.py`로 모델을 비교할 때만 필요합니다.)

**5. 서버 실행**

```bash
uvicorn app.main:app --port 8000    # 개발 중에는 --reload 추가
```

`http://localhost:8000/docs`에서 Swagger UI로 바로 호출해볼 수 있습니다.

### (선택) 데이터 수집

DB가 비어 있을 때 순서대로 실행합니다. 각 스크립트의 옵션(`--dry-run`, `--limit`, `--every`, `--report` 등)은 파일 상단 docstring에 있고,
수집은 스케줄러 없이 수동으로 실행합니다. 네이버·DART 키가 필요합니다.

```bash
python -m scripts.build_universe 300            # 시총 상위 300종목 목록 생성 (scripts/universe_top300.json, 커밋 대상 아님)
python -m scripts.collect_prices --days 90      # 주가(OHLCV)
python -m scripts.collect_market_index --days 180    # 코스피/코스닥 지수 일별 종가 + company.market(상장 시장) 채우기
python -m scripts.collect_universe --report r.json   # 종목별 뉴스 + DART 공시 목록
python -m app.embeddings --apply                # 임베딩이 비어 있는 뉴스/공시에 bge-m3 임베딩 채우기 (--apply 없으면 dry-run)
python scripts/collect_disclosure_content.py --apply   # 공시 원문 수집 (이어서 실행 가능, DART 일일 한도 약 2만 건)
```

공시 원문은 `disclosure.content`에 저장되어 `rag_search_tool`이 근거 요약문으로 LLM에 보여줍니다. 임베딩 텍스트에는 일부러 넣지 않습니다
(재본 결과 검색 적중이 떨어졌습니다. 근거는 `backend/app/embeddings.py`의 `_disclosure_text` 주석 참고).

`app/tools/market_tool.py`(종목 등락률 vs 상장 시장 지수 등락률 비교)는 `market_index`(지수)와 `company.market`(상장 시장)이
필요합니다. 새 종목을 등록한 뒤에는 `collect_market_index`를 다시 실행해야 그 종목의 시장 구분이 채워집니다(멱등).
지수 데이터가 종목 주가보다 며칠 일찍 끝나 있으면 지수의 마지막 날짜까지만 비교하고 결과의 `note`에 그 사실을 남깁니다.
`market_tool`은 기본 tool 목록에 없고, 질문에 시장 비교 표현이 있을 때만 그 요청에 추가됩니다(`app/routing.py`,
아래 Known Issues 참고).

## 테스트

```bash
pytest                            # backend/ 에서. 빠른 테스트(단위 + dev DB 통합), slow는 기본 제외
pytest -m slow                    # 실제 Ollama를 부르는 slow만 (약 30초, dev DB에 리포트를 만들었다가 지움)
pytest -m "slow or not slow"      # 전부 한 번에 (2026-09-28 기준 238개 통과)
```

구성(단위 / DB 통합 / LLM을 호출하는 slow)과 실행 옵션은 [backend/API.md](backend/API.md)의 "테스트 실행" 절을 참고하세요.
`scripts/test_tool_calling.py`는 실제 Ollama로 에이전트를 몇 번 호출해보는 수동 스모크 스크립트이고,
`scripts/benchmark_models.py`는 tool 호출 성공률 벤치마크입니다(둘 다 pytest 대상이 아닙니다).

## API

엔드포인트(`POST /api/research`, `GET /api/research`, `GET /api/research/{report_id}`, `DELETE /api/research/{report_id}`), 요청/응답 스키마,
에러 형식, CORS 안내, 성능 실측은 **[backend/API.md](backend/API.md)** 에 있습니다.

## 구현 상태

- [x] 데이터 수집: 300종목 주가 · 뉴스(네이버, 비금융 도메인 필터·동명 종목 검색 보정) · DART 공시와 공시 원문
- [x] 임베딩 + 유사도 검색: bge-m3 / pgvector HNSW, 뉴스·공시 전체 임베딩 완료
- [x] 에이전트: 4개 기본 tool(주가/뉴스/공시/RAG 검색) + 시장 비교 질문에만 붙는 `market_tool`, 종목명 보정(오타/공백/우선주 처리),
  few-shot, 실패 시 안내 응답. "네이버", "포스코홀딩스"처럼 DB 등록명과 다른 통칭은 코드 레벨 별칭 사전(`app/company_aliases.py`)으로 처리
- [x] FR-06 시장 지수 비교(1차 범위: 코스피/코스닥): 지수 수집(`market_index`), 종목 상장 시장 구분, 종목 vs 시장
  등락률 비교 `market_tool`, agent 연동(키워드 사전 분류로 시장 비교 질문에만 tool 노출)
- [ ] FR-06 업종(섹터) 지수 비교: 1차 범위 밖, 후속 작업(업종 지수 수집과 종목-업종 매핑 필요)
- [x] 리포트 저장·조회: 질문/답변/근거/tool 호출 이력
- [x] FR-10 대화형 후속 질문(범위: 직전 보고서 1개까지): `POST /api/research`에 `previous_report_id`를 주면 그 보고서의
  질문/답변을 대화 기록으로 넘겨 "그럼 최근 뉴스는?"처럼 종목명이 빠진 질문도 이어서 답함. `research_report.previous_report_id`
  (자기 참조 FK, 직전 보고서 삭제 시 SET NULL)로 저장되고 GET 응답에도 나옴. 여러 턴을 따라가는 체이닝은 범위 밖
  (직전 답변은 800자까지만 맥락에 넣고, 이전 tool 호출/결과는 넣지 않음). 종목 인자를 모델이 깨뜨리면 이번 질문에 회사가
  없을 때만 직전 질문에서 회사를 되찾는 보정이 있음(`app/agent.py`)
- [ ] FR-01 자연어 질문 분석: **부분 구현** — 질문 입력·Tool 선택·종목 식별은 됨(통칭은 별칭 사전), 기간 식별은 LLM이 `period_days`로 바꿔 넘기는 수준이고 뉴스/공시/RAG는 기간 무시
- [ ] FR-07 경제지표(금리/환율) 영향 분석: **미구현 — 신규 기능, 데이터 소스부터 필요**
- [ ] FR-11 AI 리서치 보고서: **백엔드 부분 구현 / 프론트 연동 필요** — 답변 자동 생성·근거(sources)·저장·조회는 됨, 종합 보고서 형식(원인·시장 상황·위험요인 섹션)은 없음
- [ ] FR-12 관심종목 및 이력: **리서치 이력은 완료, 관심종목은 미구현(신규 테이블 필요)**
- [ ] FR-13 시장 관심 종목 탐색: **미구현 — 신규 기능**
- [x] REST API(리포트 생성/목록/조회/삭제) + 에러 처리(Ollama/DB 장애 시 502/503) + CORS
- [x] 자동 테스트(pytest)와 API 문서
- [ ] 프론트엔드 연동: 서윤님 담당, API 스펙은 확정(API.md), 실제 연동은 진행 필요
- [ ] 모의투자 기능: 미구현, 범위 논의 필요
- [ ] 데이터 정기 갱신: 지금은 수동 실행이라 자동화 여부 논의 필요
- [ ] 아래 Known Issues (tool 선택 신뢰도)

## Known Issues / TODO (tool-calling 신뢰도, llama3.1:8b)

`app/agent.py`의 tool 선택 신뢰도 등 미해결 이슈(위쪽)와, 이미 해결한 이슈의 기록(아래 "해결된 이슈 기록")입니다.
재현/벤치마크 방법은 `backend/scripts/benchmark_models.py`, `backend/scripts/test_tool_calling.py`,
`backend/scripts/benchmark_routing.py` 참고.

### 미해결 이슈

- **퍼지 매칭이 다른 종목으로 잘못 연결할 수 있음**: `resolve_company`의 유사 이름 보정(difflib, 임계값 0.8)은 못 찾는 데서 끝나지 않고 다른 종목을
  고르기도 한다. 별칭 작업 중 확인한 실례: "삼성SDS" → 삼성SDI, "KB금융지주" → JB금융지주, "삼성엔지니어링" → 주성엔지니어링(이 셋은 별칭으로 막았다).
  별칭 사전에 없는 이름에서는 같은 유형의 오연결이 남아 있을 수 있다. 근본 대책은 별칭 확충이나 퍼지 보정 축소이며 아직 하지 않았다.
- **기간 표현 처리의 한계 (FR-01 검증에서 확인)**: 기간을 해석하는 코드가 없고 LLM이 `period_days` 숫자로 바꿔 넘긴다. "3개월"을 3일로 오해석하는 경우가
  6회 중 2회 있었고, `stock_tool`은 60행을 넘으면 조용히 60행(약 3개월)으로 자르며 그 사실을 알리지 않는다("올해 주가"도 60행). `period_days`는 달력 일수가 아니라
  거래일 행 수라서 "한 달=30"이 실제로는 달력 42일이다. "어제/오늘"은 날짜를 모르고 DB의 최신 행을 준다. 뉴스·공시·RAG는 기간 인자가 없어 "지난주"/"지난달"이 무시된다.
  고치려면 기간 파서(또는 tool에 날짜 범위 인자)가 필요해서 기능 추가에 가깝다. 자세한 측정은 위 "FR-01 인수조건별 검증".
- **데이터가 수동 갱신이라 오래됨** (2026-09-28 기준): 주가 9/23, 뉴스 9/24, 공시 9/22까지. 코스피/코스닥 지수는 FinanceDataReader가 9/17까지만
  줘서 종목 주가보다 며칠 늦고, `market_tool`은 지수의 마지막 날짜까지만 비교한다(결과의 `note`에 표시). 자동 갱신 여부는 논의 필요.
- **답변 품질(llama3.1:8b)**: 답변이 짧고 수치 없이 결론만 말하거나(시장 비교 질문 4건 중 2건), 드물게 어색한 표현과 다른 언어 문자가 섞인다
  (예: 뉴스 답변에 "관련ニュ스"). tool 결과의 숫자는 대체로 정확히 옮기지만 서술은 불안정하다. 별도 대응은 아직 없다.
- **slow 테스트의 드문 실패 1건**: 후속 질문 slow 테스트(`test_follow_up_without_company_name_keeps_previous_company[news_tool]`)가 처음 돌렸을 때
  한 번 실패했고 이후 약 90회(재현 시도 포함)에서는 재현되지 않았다. 원인 미특정이며, 다음 실패 때 원인이 보이도록 단언 메시지에
  호출된 tool/종목/답변을 남기게 해 두었다. 이 테스트들은 LLM 특성상 비결정적이다.
- **no_tool 남발**: "주식 기본 용어 알려줘" 같은 개념성 질문에도 매번 불필요하게
  tool을 호출함 (반복 테스트 기준 0/5 ~ 6/6 실패 유지, rag_search_tool 도입 전엔
  stock_tool/disclosure_tool을, 도입 후엔 주로 rag_search_tool을 잘못 호출).
  답변 내용 자체는 안전(사실 왜곡 없음)하고, DB 조회 낭비(지연/비용)만 있는
  효율성 문제. **우선순위가 낮아 프롬프트 수정으로는 안전한 해결책을 못 찾은 채 보류.**
  - 측정: `benchmark_routing.py`의 `no_tool`(3문항)과, 프롬프트 튜닝 중 예시로 쓰지 않은
    `no_tool_ho`(5문항)로 8회씩 interleaved 측정. 베이스라인은 no_tool 0~4%, no_tool_ho 0%.
    잘못 호출되는 tool은 거의 전부 `rag_search_tool`(종목 언급이 없어도 기본 "검색 tool"로 고름),
    그다음 `stock_tool`.
  - 시도 1, SYSTEM_PROMPT 문장 교체(종목별 데이터 조회가 필요 없는 개념·일반 지식 질문엔 tool 금지,
    종목 언급이 없으면 rag도 금지; `nt_sp`): 0/24, 0/40 — 효과 없음. 기존 프롬프트에 이미
    "개념 질문에는 절대 tool을 호출하지 말라"는 문장과 개념 질문 few-shot이 있어서 더해도 변화가 없음.
  - 시도 2, 개념 질문 few-shot 1개 추가(채권 가격 설명, 실제 질문과 문구 겹침 없음; `nt_fs`) 및
    시도 1과 병행(`nt_sp_fs`): 둘 다 0/24, 0/40 — 효과 없음.
  - 시도 3, rag_search_tool 설명에 "일반 지식·용어 질문엔 호출하지 않는다" 한 줄 추가(`nt_desc`):
    no_tool 17%, no_tool_ho 5% — 소폭 개선이지만 실익 없는 수준.
  - 시도 4, rag_search_tool·stock_tool 설명 앞머리에 "종목명이 명시된 질문에서만 호출, 일반 지식·용어·개념
    질문엔 절대 호출 금지"(`nt_desc2`): no_tool 46%로 올랐지만 **no_tool_ho는 0/40 그대로**. 설명에 넣은
    "용어·개념"이 개발용 질문("기본 용어를 알려줘")과 겹쳐서 생긴 착시이고 일반화되지 않음
    (few-shot 오염 때와 같은 유형). `nt_desc2_sp`(+시도 1 문장)는 0/24, 0/40.
  - 결론: 시도한 프롬프트 변형(SYSTEM_PROMPT / few-shot / tool description) 중 held-out에서 의미 있게
    개선된 것이 없어 `app/agent.py`는 수정하지 않음. 회귀 확인용 전체 가드레일 재측정도 적용할 변경이 없어
    생략함(`agent.py` 프롬프트/TOOLS는 위 공시 라우팅 수정 이후 그대로). 원인 추정은 8B 모델이 "종목 언급 없는
    질문"과 "rag_search_tool"의 관계를 프롬프트 문장으로는 못 구분하고 검색형 tool을 기본값으로 고른다는 것.
  - 대안(우선순위가 올라가면): 코드 레벨 사전 분류 — 질문에서 종목명(`company_resolver.extract_from_text`)과
    tool 트리거 키워드가 하나도 없으면 1차 호출에 tools를 아예 넘기지 않기(개념 질문은 tool 없이 바로 답변).
    프롬프트 변경 없이 확실히 막을 수 있지만, 종목 없이 묻는 정당한 rag 검색("반도체 업황 관련 근거")도
    막지 않도록 키워드 목록을 함께 검증해야 함.

### 해결된 이슈 기록

- **[완료] 한글 통칭 종목명을 인식하지 못하던 문제 — 코드 레벨 별칭 사전**: DB에는 `NAVER`, `POSCO홀딩스`, `현대차`로 등록돼 있어서 "네이버", "포스코홀딩스",
  "현대자동차"가 "종목을 찾지 못했습니다"로 끝났다(2026-09-28 정기 점검에서 발견). **DB 스키마는 변경하지 않고** `backend/app/company_aliases.py`의
  `COMPANY_ALIASES`(별칭 → 등록명)로 처리했다. 별칭이 많아지면 DB 테이블(alias table)로 옮기는 방향을 서윤님과 논의할 예정이다.
  - 조회 순서: 정확 일치 → 공백/대소문자 무시 일치 → **별칭(공백 제거·대소문자 무시로 비교)** → 퍼지 보정. 별칭을 퍼지보다 먼저 보는 이유는 퍼지가 "삼성SDS"를
    삼성SDI로 잘못 연결하기 때문이다. 별칭의 등록명이 DB에 없으면 아무것도 하지 않고 다음 단계로 넘어간다. `extract_from_text`(종목 인자 보정, 후속 질문 폴백)에도
    별칭을 같은 방식으로 연결했다(더 긴 실제 이름이 이긴다: "한전KPS" 질문에서 "한전"은 무시).
  - 별칭 56개(주요 종목 위주, DB 종목명 300개를 훑어 사용자가 다르게 부를 만한 이름만): 필수 3개(네이버, 포스코홀딩스, 현대자동차) 외에 약칭·은어(삼전, 하닉/하이닉스, 카뱅,
    LG엔솔, 한전, 가스공사, SKT, KAI 등), 영문/한글 표기(삼성SDS, LG CNS, 에스오일, HYBE, 엔씨소프트, JYP엔터, SM엔터 등), 지주·금융('금융지주' 유무: KB금융지주, 신한금융,
    하나금융, 우리금융, IBK기업은행 등), 사명 변경 전 이름·그룹 표기(현대중공업, 한국조선해양, 두산중공업, 포스코케미칼, 현대상선, 현대산업개발 등). 전체 목록은 파일 참고.
    "포스코", "아모레"처럼 여러 회사에 걸치는 이름은 일부러 넣지 않았다. 이미 퍼지가 맞게 연결하는 이름(LG생건, DB손보, HD현대건설기계)도 뺐다.
  - 회귀 확인: 커밋된 이전 `company_resolver`와 비교해, 종목명·티커·공백/대소문자·오타·우선주 표기 등 **2,292개 입력에서 결과가 완전히 동일**(차이 0건). `extract_from_text`는
    655개 문장 중 달라진 4개가 전부 "네이버…", "포스코홀딩스…"가 들어간 벤치마크 질문이 이제 종목을 찾게 된 것이다. 테스트 `tests/test_company_aliases.py`.
  - 알려진 한계: 별칭은 수작업 목록이라 목록에 없는 통칭은 여전히 못 찾는다. `extract_from_text`는 부분 문자열 검색이라 "삼성바이오에피스"처럼 별칭("삼성바이오")으로
    시작하는 다른 이름이 질문 원문에 있으면 잘못 잡힐 수 있다(모델이 종목 인자를 깨뜨린 경우의 보정에서만 쓰인다).

- **[완료] FR-06 시장 지수 비교(market_tool) — 키워드 사전 분류 방식**: 시장 비교 질문에서 종목 등락률을 코스피/코스닥
  지수의 같은 기간 등락률과 비교해 답한다(1차 범위는 시장 전체 지수, 업종(섹터) 지수 비교는 후속 작업).
  - 배경: `market_tool`을 5번째 tool로 LLM에 상시 노출하면 stock_tool+news_tool 동시 호출(multi)이 깨진다
    (`benchmark_routing.py` n=96: 60% → 36%, 설명을 줄여도 36%, +프롬프트 문장 29%. 표준오차 약 ±5%p라 노이즈 아님).
    market_tool이 multi 질문에서 호출된 건 1/96뿐이라 "5번째 tool이 목록에 있는 것" 자체가 원인이다. stock_tool 설명에
    시장 비교를 녹이는 통합안도 multi가 50% → 28~33%(n=64)로 같이 깨져서 폐기(시험용 변형은 커밋 377d8dc).
  - 방식: LLM에게 맡기지 않고 코드가 판단한다. `app/routing.py`의 `needs_market_tool(question)`이 True인 요청에만
    `build_request()`가 market_tool과 시스템 프롬프트 문장 하나를 추가한다. False면 SYSTEM_PROMPT/TOOLS/few-shot이 이전과
    완전히 같은 4-tool 요청이다(비시장 벤치마크 질문 44개의 LLM 요청을 바이트 단위로 비교해 차이 0건 확인).
  - 키워드(하나라도 맞으면 True): `코스피|코스닥|KOSPI|KOSDAQ`, `시장|증시` + `전체·전반·대비·평균·흐름·수익률·보다·요인·
    분위기·영향·지수`, `장` + `전체·전반·대비·평균·흐름·분위기`('공장 전체'처럼 한글 뒤에 붙은 '장'은 제외), `지수` + `대비·보다·
    와·과·랑·하고·비교·흐름·때문·영향·상승률·하락률·수익률`, `초과 수익|상대 수익|벤치마크|장세`, `개별 (종목) 요인|이슈`, `종목만의`.
    '시장 점유율', '시장 진출', '소비자물가지수'처럼 비교가 아닌 표현은 일부러 뺐다.
  - 커버리지/오탐(단위 테스트 `tests/test_routing.py`):
    - 벤치마크 market 6/6, market_ho 5/5 — 단, 이 질문들을 보고 키워드를 정했으므로 낙관적인 수치다.
    - 키워드 설계와 별개로 새로 쓴 시장 비교 질문 14개: 첫 실행 12/14(86%). 놓친 것은 "장세"(패턴 추가함)와 키워드가
      전혀 없는 "다 같이 빠진 거야?"식 표현(코드 키워드로는 못 잡는 한계). "장세" 추가 뒤 13/14이지만 그 수치는 더는 독립 측정이 아님.
    - 오탐: market 외 벤치마크 질문 44개에서 0건. 시장/지수 단어가 들어간 비교 아닌 질문 15개에서는 3건("코스피 200이 뭐야?",
      "코스닥 상장 요건", "PER이 시장 전체 평균보다 높으면…" 같은 개념 질문 — 키워드로 구분 불가). 오탐의 비용은 그 질문의
      tool 목록이 5개가 되는 것뿐이다(못 잡는 쪽이 기능 누락이라 더 나쁘다).
  - 라우팅 재검증(`benchmark_routing.py`, 8회 interleaved): market 48/48, market_ho 40/40, market_tool 오호출 0/352.
    비시장 그룹은 게이트 적용/미적용 수치가 rag 77/77%, rag_ho 69/78%, disc_list·disc_ho·stock·news 100/100%, multi 78/53%,
    no_tool·no_tool_ho 0/0%로 나왔다. 요청이 바이트 단위로 같은데도 multi·rag_ho가 다른 것은 샘플링 노이즈다(같은 입력에서도
    질문별 결과가 실행마다 7:1 vs 5:3처럼 갈린다).
  - 답변 확인(`ask_question`): 시장 질문 4개 모두 market_tool이 호출됐고, 수치를 말한 답변 2건은 tool 결과와 일치했으며 나머지 2건은 수치 없이 결론만 말했다. "최근 일주일"을
    모델이 period_days=7(약 열흘)로 넘기던 것을 인자 설명("일주일이면 5, 한 달이면 20")으로 고쳐 6/6이 5로 들어간다.
    다만 llama3.1:8b 특성상 답변이 짧고 수치 없이 모호하거나 어색한 표현이 섞이기도 한다(별개의 기존 한계).
  - 알려진 한계: 키워드가 없는 시장 비교 표현은 못 잡는다. 업종 비교는 미지원. 지수 데이터가 종목 주가보다 일찍 끝나면 그 날짜까지만 비교한다.
- **[해결] 공시 "내용" 질문이 rag_search_tool 대신 disclosure_tool로 라우팅되던 문제**:
  "삼성전자 자사주 매입 관련 공시 내용 자세히 알려줘"류 질문이 "공시"라는 단어
  때문에 거의 항상 disclosure_tool(최신순 목록)로 갔던 문제. `scripts/benchmark_routing.py`로
  세 가지 방향을 A/B 측정(interleaved, 질문당 8회 반복)한 끝에 해결함:
  - *tool description만 수정*: few-shot과 겹치는 예시 문구를 써서 rag 64%로 보였지만,
    질문 문구와 few-shot 문구가 겹쳐 생긴 측정 오염이었음(de-overlap 후 rag 15%,
    rag_ho 2%, multi 33→67%대 회귀) → 폐기.
  - *few-shot 예시 추가*(rag 전용, rag+list 쌍): rag 12~15%, rag_ho 0/48으로 개선폭이
    작고 회귀도 없어 적용할 가치가 없다고 판단 → 폐기.
  - *SYSTEM_PROMPT 라우팅 문장 재작성 + tool description 동시 수정* (`sp_desc` 변형,
    현재 적용됨): "최신순 목록"과 "내용 검색"을 대비시켜 명시. 8반복 A/B 결과
    rag 0%→80%(51/64), held-out rag_ho 0%→67~72%(43~46/64)로 크게 개선. disc_list
    100%, disc_ho 100%, stock 100%, news 100% 그대로 유지(회귀 없음). multi(stock_tool+
    news_tool 동시 호출)는 56~72%(n=32)로 변경 전 노이즈 범위(38~67%, 반복 실행 시
    표준오차 약 ±9%p)와 겹쳐 유의한 회귀로 보지 않음. no_tool은 원래부터 0%였던
    이슈라 그대로 유지(위 항목과 동일 사안).
  - SYSTEM_PROMPT/TOOLS의 disclosure_tool·rag_search_tool 부분과 `benchmark_routing.py`는
    커밋 `99b5081`에 반영.
- **결론 / 다음 작업**: 공시 라우팅 이슈는 SYSTEM_PROMPT와 tool description을
  함께(따로가 아니라) 고쳐야 회귀 없이 개선된다는 것을 확인함 — 국소 패치 하나만으로는
  부족했음. 프롬프트로 풀리는 라우팅 이슈는 여기까지고 no_tool 남발은 위 "미해결 이슈"에 보류돼 있다. tool을 더 추가할 때는 이번처럼
  `scripts/benchmark_routing.py`로 변경 전/후를 interleaved A/B 측정해서 회귀
  여부를 확인할 것 (특히 multi처럼 노이즈가 큰 그룹은 n을 충분히 늘려서 판단).
  장기적으로 로컬 8B 모델 하나의 프롬프트 튜닝만으로 감당하기 어려워지면
  코드 레벨 사전 분류(키워드/임베딩 기반 라우터로 tool 후보를 먼저 좁히기)나
  disclosure_tool과 rag_search_tool을 하나의 tool로 통합하는 방안을 검토할 것.
