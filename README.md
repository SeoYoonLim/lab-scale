# lab-scale — AI 투자 리서치 에이전트

한국 상장사(시가총액 상위 300종목)에 대해 "삼성전자 오늘 왜 올랐어? 주가랑 뉴스 같이 확인해줘" 같은 자연어 질문을 받으면,
로컬 LLM이 필요한 조회(주가·뉴스·공시·의미 검색)를 스스로 골라 실행하고 그 결과로 답변과 근거 링크를 돌려주는 리서치 서비스입니다.
2인 팀 프로젝트로, 백엔드/데이터/에이전트와 프론트엔드를 나눠서 진행하고 있습니다.

## 지금 상태 요약 (서윤님 복귀용, 2026-09-28 기준)

**한 줄 요약:** 백엔드(API + 에이전트 + 데이터 수집 + 관심종목/모의투자 + 환율 영향 분석 + 시장 관심 종목 탐색)는 동작하고
테스트가 전부 통과합니다(기본 594개 + 실제 Ollama를 부르는 slow 14개 = 608개, `pytest -m "slow or not slow"`로 한 번에).
남은 큰 일은 프론트엔드 연동입니다. 작업 브랜치는
`feature/backend`이고 원격에 push되어 있습니다.

### 프론트엔드 현황 (서윤 작성, 2026-10-02 기준)

작업 브랜치는 `feature/frontend`이고 원격에 push되어 있습니다(`feature/backend`와 별도 브랜치라 이 문서가 자동으로
반영하지는 않아서 직접 적습니다). React + Vite + TypeScript, `backend/API.md`에 맞춰 실제 백엔드와 바로 연동되도록
작성했습니다(`VITE_USE_MOCK=false`로 전환하면 mock 대신 `/api` 프록시로 `localhost:8000`을 호출).

- **리서치**(`/`): `POST /api/research` 연동, 후속 질문(`previous_report_id`)을 같은 세션 안에서 자동으로 이어가고,
  `sources`는 번호 매긴 근거로 보여줍니다. 질문 없이 바로 보여주는 "오늘의 관심 종목"(`GET /api/discovery/trending`)도 포함.
- **기록**(`/reports`): `GET /api/research`(페이지네이션) + `GET /api/research/{id}`(펼쳐보기) + `DELETE`.
- **포트폴리오**(`/portfolio`): 관심종목(`GET`/`POST /api/watchlist`, `DELETE`)과 모의투자(`GET /api/portfolio`,
  `POST /api/portfolio/orders`) 연동. `X-Device-Id`는 프론트가 `crypto.randomUUID()`로 만들어 localStorage에
  저장합니다(`frontend/src/utils/deviceId.ts`).
- **종목 상세**(`/companies/{ticker}`): 현재가만 `GET /api/stocks/{ticker}/realtime-price`를 3초 간격으로 폴링해서
  실제 데이터입니다. 과거 주가·뉴스·공시 목록과 종목 목록(`/companies`)은 **대응하는 조회 API가 없어서 여전히 mock**입니다.

**아직 안 한 것**
- 위 화면들을 실제로 돌아가는 백엔드(Ollama + 채워진 DB)에 붙여서 end-to-end로 테스트한 적은 없습니다. 지금까지는
  `API.md`에 적힌 계약과 똑같이 동작하는 mock을 상대로만 검증했습니다. 이 컴퓨터에는 `llama3.1:8b`/`bge-m3` 모델이
  없고(벤치마크용 `qwen2.5:7b-instruct`만 있음) 로컬 DB도 비어 있어서, 실제로 붙여보려면 모델 설치와 DB 데이터(직접
  수집하거나 덤프를 받는 것)가 필요합니다.
- 프론트 자동화 테스트는 아직 없습니다(백엔드 pytest 608개와 대비됩니다).
- FR-11이 말하는 "종합 보고서 형식"은 백엔드 답변이 마크다운 소제목(`## `)으로 구조화되기 시작했으니(위 FR-11 참고),
  프론트에서 그 구조를 활용해 섹션별 레이아웃으로 나눠 보여주는 건 다음에 할 수 있습니다. 지금은 통째로 보여줍니다.
- 종목 목록/상세용 조회 API(과거 주가, 뉴스, 공시)가 계속 필요할지, 아니면 관심종목·모의투자 쪽으로 로드맵이
  옮겨간 만큼 이 화면 자체를 재설계할지는 아직 팀 논의가 필요합니다.

### 구현된 API

요청/응답 예시, 에러 형식, CORS는 전부 **[backend/API.md](backend/API.md)** 에 있습니다. 프론트 연동은 이 문서만 보면 됩니다.

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/api/research` | 질문 → AI 답변 + 근거 + 리포트 저장. `previous_report_id`로 직전 리포트를 이어서 후속 질문 |
| GET | `/api/research` | 저장된 리포트 목록(최신순, `limit`/`offset` 페이지네이션, `total` 포함) |
| GET | `/api/research/{report_id}` | 리포트 한 건(전체 답변, sources, tool 사용 이력) |
| DELETE | `/api/research/{report_id}` | 리포트 삭제(204, 본문 없음). 후속 리포트는 남고 연결만 끊김 |
| GET | `/api/stocks/{ticker}/realtime-price` | 종목 현재가(비공식 소스 기반 실시간, 장외/장애 시 DB 최근 종가로 자동 폴백) |
| GET/POST | `/api/watchlist` | 관심종목 조회/추가 (`X-Device-Id` 헤더로 사용자 구분, 로그인 없음) |
| DELETE | `/api/watchlist/{ticker}` | 관심종목 삭제 |
| GET | `/api/portfolio` | 모의투자 잔고 + 보유 종목(평가손익 포함). 디바이스ID 첫 호출 시 계좌 자동 생성(초기 잔고 1,000만원) |
| POST | `/api/portfolio/orders` | 모의투자 매수/매도 주문(현재가로 즉시 체결) |
| GET | `/api/discovery/trending` | 급등/급락/거래량 급증 종목(FR-13, 질문 없이 바로 호출, `category`/`limit` 쿼리) |

프론트가 관심종목·모의투자 API를 쓰려면 **모든 요청에 `X-Device-Id` 헤더**(프론트가 만들어 localStorage에 저장하는 uuid)를
실어야 합니다. 로그인이 없어서 이 값 자체가 사용자 구분자이고(인증 아님, 그 값을 그대로 신뢰), 헤더가 없으면 422입니다.

프론트에서 특히 챙길 것: POST는 로컬 LLM이라 **2~25초**(로딩 상태와 60초 이상 타임아웃) / 에러 `detail`은 404·502·503에서는 문자열, 422에서는 배열
/ `report_id`는 저장 실패 시 null / 허용 origin은 `localhost:5173`, `localhost:3000` 두 개뿐(다른 포트면 알려주세요).

### DB 스키마 개요

PostgreSQL 16 + pgvector. **스키마의 기준은 Alembic 마이그레이션**(`backend/alembic/versions/`, 현재 head `d1bbfc5e2f0f`)입니다.

| 테이블 | 내용 | 주요 컬럼 |
| --- | --- | --- |
| `company` | 종목 300개(시총 상위) | `ticker`(unique), `name`, `market`(KOSPI/KOSDAQ/KOSDAQ GLOBAL), `sector`(현재 비어 있음) |
| `stock_price` | 일별 OHLCV | `company_id`, `price_date`, `close_price`, `volume`, `change_pct` (종목+날짜 unique) |
| `market_index` | 코스피(KS11)/코스닥(KQ11) 일별 종가 | `index_code`, `price_date`, `close_price`, `change_pct` (지수+날짜 unique) |
| `news` | 네이버 뉴스 | `company_id`, `title`, `url`, `published_at`, `embedding vector(1024)` |
| `disclosure` | DART 공시 | `company_id`, `title`, `disclosed_at`, `source_url`, `content`(원문), `embedding vector(1024)` |
| `research_report` | 질문/답변 보고서 | `question`, `content`(답변), `summary`, `company_id`, `previous_report_id`(직전 리포트, 자기 참조) |
| `tool_call_log` | 보고서별 tool 호출 이력 | `report_id`(삭제 시 cascade), `tool_name`, `arguments`/`result`(JSONB) |
| `watchlist` | 관심종목(FR-12) | `device_id`, `company_id` (조합 unique) |
| `virtual_account` | 모의투자 가상 계좌(디바이스ID당 1개) | `device_id`(PK), `cash_balance`(초기 1,000만원) |
| `holding` | 모의투자 보유 종목 | `device_id`, `company_id`(조합 unique), `quantity`, `avg_price`(가중평균) |
| `trade` | 모의투자 체결 내역 | `device_id`, `company_id`, `side`(buy/sell), `quantity`, `price`, `executed_at` |
| `exchange_rate` | 환율(USD/KRW) 일별 종가(FR-07) | `pair_code`, `price_date`, `close_price`, `change_pct` (통화쌍+날짜 unique) |

**서윤님이 설계한 스키마와 실제로 만들어진 스키마를 맞춰봐 주세요.** 최초 설계 스냅샷은 `db/schema.sql`(서윤님 커밋)이고, 임시 DB에 그 파일을 적용해서
실제 DB와 컬럼·인덱스·FK를 기계적으로 비교했습니다. 컬럼/타입/FK는 모두 같고 **차이는 아래 6가지**입니다(설계와 다른 것이 의도에 맞는지 확인 필요).

1. `market_index` 테이블 추가(FR-06, 설계에 없던 확장)
2. `research_report.previous_report_id` 컬럼 + 자기 참조 FK(`ON DELETE SET NULL`) 추가(FR-10, 설계에 없던 확장)
3. `news.embedding`, `disclosure.embedding`에 HNSW 인덱스(코사인, m=16, ef_construction=64) 추가(유사도 검색 성능)
4. 인덱스 정렬 방향: 설계는 `(company_id, 날짜 DESC)`, 실제는 오름차순. btree는 역방향 스캔이 되므로 조회 기능상 동일
5. `watchlist`, `virtual_account`, `holding`, `trade` 4개 테이블 추가(FR-12 관심종목 + 모의투자, 설계에 없던 확장).
   회원가입/로그인이 없는 서비스라 사용자 구분을 프론트가 만드는 디바이스ID(`X-Device-Id` 헤더, uuid)로 했다 -
   인증이 아니라 단순 구분자이고, `company`처럼 다른 테이블과 달리 `device_id`는 FK가 아니라 프론트가 보낸
   문자열을 그대로 저장한다. `virtual_account.device_id`를 기본키로 써서 "디바이스ID당 계좌 1개"를 표현했다.
6. `exchange_rate` 테이블 추가(FR-07 경제지표 영향 분석, 1차 범위는 환율만, 설계에 없던 확장). `market_index`와
   같은 구조(통화쌍 코드 + 날짜 + 종가 + 등락률)를 그대로 따랐다.

`db/schema.sql`은 갱신하지 않고 상단에 "최초 설계 스냅샷이며 기준은 Alembic"이라는 안내만 달았습니다.

**회사명 별칭(alias):** `company.name`은 `NAVER`, `POSCO홀딩스`, `현대차`처럼 공식 표기라서 사용자가 쓰는 "네이버", "포스코홀딩스", "현대자동차"가 정확
일치하지 않습니다. 이 통칭은 지금 **코드 레벨 딕셔너리**(`backend/app/company_aliases.py`의 `COMPANY_ALIASES`, 별칭 → DB 등록명)로 처리하고 있고,
**이번 작업에서 DB 스키마는 변경하지 않았습니다**(스키마 변경은 팀 협의가 필요해서 제외). 별칭이 많아지면 DB 테이블(alias table)로 옮기는 방향을
서윤님과 논의할 예정입니다. `resolve_company`가 정확/공백·대소문자 일치 다음, 퍼지 매칭 앞에서 이 사전을 봅니다(상세는 아래 "해결된 이슈 기록").

### 데이터 현황 (2026-09-28 조회, 수집은 수동)

종목 300 · 주가 18,565행(6/26~9/23) · 지수 코스피/코스닥 각 116행(4/1~9/17) · 뉴스 3,013건(최신 9/24, 임베딩 완료) ·
공시 6,308건(최신 9/22, 원문 6,268건, 임베딩 완료) · 리포트 6건(1~6번은 **데모 데이터라 지우지 않습니다**) ·
환율(USD/KRW) 129행(4/2~9/29, 2026-10-01 수집).
지수는 FinanceDataReader가 오늘도 9/17까지만 줘서 종목 주가보다 며칠 늦습니다(우리 쪽 문제가 아니라 원천의 제한, 재확인함).

### FR 대응 현황

FR-01·05·07·11·12·13은 원본 PRD의 정의와 인수조건을 확인해서 **코드 기준으로 검증**했습니다(FR-01·07·11·12·13은 2026-09-28,
FR-05는 2026-10-01, 판단 근거는 표 아래 "인수조건별 검증"). 나머지 FR-02~04, 06, 08~10의 이름은 `db/schema.sql` 주석과 작업
지시에서 가져온 것이고 PRD 원문과 대조하지는 않았습니다. 이 저장소에는 PRD 원문이 없어서 원본과 다시 맞춰봐 주세요.

**FR-05 정정(2026-10-01):** `db/schema.sql`이 `research_report`를 FR-05로 표시해서 지금까지 "PRD 정의 미확인"으로 남겨뒀는데,
PRD 원문을 확인해보니 **FR-05는 "주가 변동 원인 분석"이고 `research_report`가 속한 "AI 리서치 보고서 생성"은 FR-11**이다.
즉 `db/schema.sql`의 라벨링이 PRD와 맞지 않았다(스키마 파일은 건드리지 않고 이 README의 FR 표만 바로잡는다).

| FR | 저장소에서 확인되는 근거 | 상태 |
| --- | --- | --- |
| FR-01 (P0) 자연어 투자 질문 분석 | `POST /api/research`, LLM tool calling + `company_resolver` + `_repair_company_args` + `routing.needs_market_tool` + `period_parser.parse_period`(2026-10-01) | **부분 구현**: 질문 입력·Tool 선택은 됨, 종목 식별은 됨(통칭은 별칭 사전으로 처리), 기간 식별은 정해진 표현(아래 FR-01 상세)에서는 코드가 직접 처리하고 그 외는 이전처럼 LLM 추론에 의존 |
| FR-02 주가·거래량 | `stock_price`, `stock_tool`, 300종목 수집 | 완료 |
| FR-03 뉴스 | `news`, `news_tool`, 네이버 수집 | 완료 |
| FR-04 공시 | `disclosure`, `disclosure_tool`, DART 목록+원문 | 완료 |
| FR-05 (P1) 주가 변동 원인 분석 | 전용 tool/로직은 없음. `agent.py`가 "왜 올랐는지/내렸는지" 질문에 stock_tool(수치)+news_tool(뉴스 근거)을 함께 호출하도록 유도하고, 종목 고유 요인인지 시장 전체 요인인지는 FR-06의 `market_tool`이 구분 | **부분 구현**: 원인과 관련된 신호(뉴스, 시장 요인 구분)는 tool로 제공되지만 "원인 분석"을 전담하는 로직이 없고 최종 종합은 LLM 서술에 의존. 원인 분석의 정확도를 재는 테스트도 없음(아래 FR-05 상세) |
| FR-06 기업·산업·시장 요인 비교 | `market_tool` | **1차 완료**(시장 지수). 업종(섹터) 비교는 미구현 |
| FR-07 (P1) 경제지표 영향 분석 | `exchange_rate` 테이블 + `fx_tool` + `routing.needs_fx_tool` 키워드 게이트(2026-10-01). 금리는 범위 밖 | **1차 완료(환율만)**. 금리는 쓸 만한 무료 소스를 찾지 못해 범위 밖(아래 FR-07 상세) |
| FR-08 근거 임베딩/RAG | bge-m3 임베딩, `rag_search_tool` | 완료 |
| FR-09 tool 호출 이력 | `tool_call_log`, 리포트 상세의 `used_tools` | 완료 |
| FR-10 대화형 후속 질문 | `previous_report_id` | 완료(직전 1개까지, 체이닝은 범위 밖) |
| FR-11 (P1) AI 리서치 보고서 생성 | `agent._answer`(답변 자동 생성, 2026-10-01부터 `ANSWER_STRUCTURE_INSTRUCTION`으로 섹션 구조화 유도) + `reports.save_report` + `sources`(근거 문서). 프론트엔드 없음 | **백엔드 부분 구현 / 프론트 연동 필요**: 자동 생성·근거 포함·섹션 구조화(조회한 tool에 맞는 섹션만)는 됨, 위험요인 판단은 여전히 LLM 서술에 의존하고 전담 검증 로직은 없음(아래 FR-11 상세) |
| FR-12 (P2) 관심종목 및 리서치 이력 | 리서치 이력: `GET /api/research`, `GET /api/research/{id}`, `DELETE`. 관심종목: `watchlist` 테이블 + `GET`/`POST /api/watchlist`, `DELETE /api/watchlist/{ticker}` | **완료** (2026-10-01, 사용자 구분은 로그인이 아니라 `X-Device-Id` 헤더) |
| FR-13 (P2) AI 시장 관심 종목 탐색 | `app/discovery.py`(순수 집계) + `discovery_tool` + `routing.needs_discovery_tool` + `GET /api/discovery/trending`(2026-10-01) | **완료**. "관심"의 기준이 등락률/거래량 수치뿐이라 질적 신호(뉴스·공시)는 반영 안 됨(아래 FR-13 상세) |

#### 인수조건별 검증 (FR-01·05·07·11·12·13, FR-01/07/11/12/13은 2026-09-28, FR-05는 2026-10-01, 코드·측정 기준)

**FR-01 자연어 투자 질문 분석 — 부분 구현** (인수조건 3개)
- *자연어 질문 입력 가능*: 충족. `POST /api/research`의 `question`(1~1000자).
- *종목 및 기간 식별*: **종목은 충족(제한적), 기간은 지원 범위 내에서 충족(2026-10-01, 그 전엔 LLM 추론에만 의존해 제한적이었음).**
  - 종목: LLM이 tool 인자로 종목을 뽑고 → `company_resolver.resolve_company`(정확 일치 → 공백/대소문자 무시 → 퍼지, 우선주 구분)로 DB 종목에
    맞추고 → 인자가 깨지면 `agent._repair_company_args`가 질문 원문(후속 질문이면 직전 질문)에서 `extract_from_text`로 되찾는다. 여러 종목("현대차랑 카카오")은
    종목별로 tool을 각각 부른다(데모 보고서 #5). "네이버" 같은 통칭은 `app/company_aliases.py`의 별칭 사전으로 등록명에 연결한다(아래 "해결된 이슈 기록").
  - 기간: `app/period_parser.py`가 질문에서 고정 표현(오늘/어제/이번주·이번 주/지난주·지난 주/이번달·이번 달/지난달·지난 달/올해/작년)과
    수량 표현(N일/N주/N개월/N년, 예: "3개월 동안")을 코드로 직접 인식해 `agent._apply_period_override`가 `stock_tool`/`market_tool`의
    `period_days`를 그 값으로 덮어쓴다(LLM이 뭘 추론했든 우선). 인식 못 하면(예: "일주일", "3분기", "N일 전"처럼 특정 시점을 가리키는
    표현) 이전처럼 LLM 추론값을 그대로 쓴다 - 예외를 던지지 않는다. `period_days`는 여전히 달력 일수가 아니라 거래일 수라서, 파서는 날짜
    범위의 평일(월~금) 수를 세어 근사치로 돌려준다(공휴일 미반영, `app/period_parser.py` 모듈 docstring 참고). 60을 넘으면 이전과
    동일하게 조용히 60으로 잘린다(이 동작 자체는 바꾸지 않음). `news_tool`/`disclosure_tool`/`rag_search_tool`은 여전히 **기간 인자
    자체가 없어서** "지난주 뉴스", "지난달 공시"도 그냥 최신 N건이다(이번 작업 범위 밖).
- *필요한 분석 Tool 선택 가능*: 충족(신뢰도 한계 있음). 별도의 "분석 목적" 분류는 없고 tool 선택으로 암묵적으로 처리한다. `benchmark_routing.py` 측정: 주가/뉴스/공시 목록
  100%, 공시 "내용" 검색 → rag 77~89%(held-out 66~84%), 주가+뉴스 동시 호출(multi) 50~78%(측정마다 크게 흔들림, 대체로 60% 안팎),
  시장 비교 100%(키워드 게이트 사용, 독립 질문 세트 커버리지 12/14), 개념 질문의 tool 미호출 0%(no_tool 이슈).
- 관련 테스트: `test_company_resolver.py`(종목 해석 20개), `test_routing.py`(시장 게이트), `test_followup.py`(종목 인자 보정 포함),
  `test_period_parser.py`(기간 파서 37개), `test_period_override.py`(agent 연동 + 회귀, 아래 "해결된 이슈 기록"), slow 통합 테스트.

**FR-05 주가 변동 원인 분석 — 부분 구현 (2026-10-01)** (PRD 정의 확인, 자세한 인수조건은 원문 미보유로 기능 단위로만 검토)
- 전담 tool이나 "원인 분류" 로직은 없다. 대신 `agent.py`의 SYSTEM_PROMPT가 "왜 올랐는지/내렸는지" 질문에 `stock_tool`(등락률·거래량
  수치)과 `news_tool`(관련 뉴스)을 함께 호출하도록 유도하고(few-shot 예시 1도 이 패턴), 종목 고유 요인인지 시장 전체 흐름 때문인지는
  FR-06의 `market_tool`이 지수 대비 등락률 차이(`diff_pct_point`)로 구분한다(`needs_market_tool` 키워드 게이트가 걸린 질문에서만).
  즉 "원인"에 쓸 수 있는 **신호**(뉴스, 시장 요인 기여도)는 세 tool이 제공하지만, 그 신호들을 종합해 "원인"이라고 서술하는 것은
  전적으로 LLM의 최종 답변 생성(`_answer`의 3단계)에 맡겨져 있고 코드가 그 서술의 사실성·인과관계를 검증하지는 않는다.
- 한계: (1) 원인 분석 전용 테스트가 없다(답변 품질은 README "미해결 이슈"의 "답변 품질" 항목 참고). (2) 뉴스가 질문 종목과
  무관해 보이는 경우가 있다고 이미 기록돼 있어(API.md "알아둘 점"), LLM이 무관한 뉴스를 원인처럼 서술할 위험이 있다. (3) 시장
  요인 비교(market_tool)는 질문이 명시적으로 시장 비교를 요청할 때만 붙고, "왜 올랐어?"만으로는 자동으로 켜지지 않는다.

**FR-07 경제지표 영향 분석 — 1차 완료(환율만), 금리는 범위 밖 (2026-10-01)** (인수조건 3개 중 환율 관련 부분만 충족)
- *환율 데이터 수집·저장*: 충족. `exchange_rate` 테이블(USD/KRW, `scripts/collect_exchange_rate.py`로 FinanceDataReader에서
  수집, 2026-10-01 기준 2026-04-02~09-29 129행). market_index와 같은 구조(통화쌍 코드 + 날짜 + 종가 + 등락률)지만, FDR이
  통화쌍에는 `market_index`가 쓰는 `Change` 컬럼을 주지 않아서(실제 호출로 확인) 종가의 일별 변화율을 직접 계산해서 저장한다.
- *환율 조회 tool*: 충족. `app/tools/fx_tool.py`의 `fx_tool(period_days=5)`가 최근 N거래일 현재가·전일대비 등락률·기간
  추세(상승/하락/보합)를 돌려준다. 종목이 아니라 `ticker` 인자가 없다. `market_tool`과 같은 이유로 기본 tool 목록에는 없고,
  `app/routing.py`의 `needs_fx_tool`(키워드: "환율", "원/달러", "달러/원", "USD/KRW")이 걸린 질문에만 `agent.build_request`가
  `fx_tool`과 안내 문장(`FX_HINT`)을 추가한다(market_tool과 동시에 걸릴 수도 있다 - 예: "환율 때문에 코스피가 빠졌어?").
- *금리·경제 이벤트*: **범위 밖으로 확정.** 금리는 시도한 FDR 심볼 2개가 404라 확인하지 못했고, 한국은행 ECOS 같은 별도 소스
  연동이 필요해 이번 작업에서는 다루지 않았다. "관련 경제 이벤트 확인"에 쓸 이벤트 데이터의 출처도 아직 없다.
- *회귀 검증*: `scripts/benchmark_routing.py --reps 4`(fx/fx_ho 그룹 신설 포함 전체 13개 그룹, 2026-10-01 측정)로 fx_tool
  추가가 다른 그룹에 영향을 주지 않는지 확인했다 - fx 100%(24/24), fx_ho 100%(20/20), 기존 그룹은 전부 과거 기록 범위 안
  (rag 78%, rag_ho 69%, disc_list/disc_ho 100%, multi 62%, stock/news 100%, market/market_ho 100%, no_tool/no_tool_ho 0%는
  기존과 동일하게 미해결). **market_tool/fx_tool 모두 자기 그룹 밖 220개 질문에서 오호출 0건**이라, fx_tool이 그 220개
  질문의 tool 목록을 전혀 바꾸지 않았다는 뜻이고(해당 질문들은 `build_request`가 바이트 단위로 이전과 동일한 결과를 돌려줌),
  그 그룹들의 성공률 변동은 전부 LLM 자체의 실행 간 노이즈다. 자세한 내용은 아래 "해결된 이슈 기록".
- 관련 테스트: `test_fx_tool.py`(11개, 단위 + dev DB 통합), `test_routing.py`의 `needs_fx_tool`/`build_request` 관련 테스트,
  `test_agent_slow.py::test_fx_question_calls_fx_tool_without_fewshot_leak`(slow).

**FR-11 AI 리서치 보고서 생성 — 백엔드 부분 구현 / 프론트 연동 필요 (답변 구조화는 2026-10-01 추가)** (인수조건 3개)
- *분석 결과 기반 자동 보고서 생성*: **부분 충족(2026-10-01 이전보다 개선).** tool 결과를 근거로 LLM이 답변을 자동 생성하고
  `research_report`(`content`, `summary`)에 저장한다(`agent._answer`, `reports.save_report`). 이제 최종 답변 생성 직전에
  `ANSWER_STRUCTURE_INSTRUCTION`을 추가로 넣어, PRD가 말한 구성(주가 동향/원인 분석/뉴스·공시 근거/시장 상황/위험요인) 중
  **실제로 호출한 tool의 결과가 뒷받침하는 섹션만** 마크다운 소제목(`## `)으로 나눠 쓰도록 유도한다(호출하지 않은 tool의
  섹션은 지어내지 말 것, 단순 조회 질문은 섹션 없이 한두 문장으로 답할 것도 같이 지시). 이 안내는 **1차 호출(tool 선택)에는
  관여하지 않는다** - `SYSTEM_PROMPT`/`TOOLS`를 건드리지 않고 tool 실행이 끝난 뒤 메시지에 한 번 추가하는 것뿐이라,
  `scripts/benchmark_routing.py`가 측정하는 경로(`build_request` + `_first_call`)는 이 상수를 아예 거치지 않는다(코드
  구조상 라우팅 회귀가 있을 수 없음, 실제로 재측정해도 변경 전과 동일 - 위 FR-07 회귀 검증 결과 참고).
  **한계**: (1) 위험요인 섹션을 실제로 쓸지, 쓴다면 뭘 위험요인으로 꼽을지는 전적으로 LLM 판단이고 그 판단의 사실성·
  타당성을 검증하는 코드가 없다. (2) 섹션 헤더가 정확히 `## ` 형식으로 나오는지도 LLM 성향에 달렸다(llama3.1:8b가 글머리
  기호 목록으로만 답하고 섹션 헤더를 생략한 사례가 있어, 안내 문구에 예시를 붙여 개선했다 - 자세한 내용은 아래 "해결된
  이슈 기록"). (3) 완전히 결정론적으로 검증할 수 없어 slow 테스트(`test_agent_slow.py`)로 "완전히 망가지지 않았는지"만 본다.
- *주요 근거 및 출처 포함*: **문서 근거는 충족, 수치 출처는 없음.** `sources`(뉴스/공시/RAG 문서의 제목·종목·URL, `app/sources.py`)가 POST·GET 응답에 들어가고 저장된
  `tool_call_log`에서 복원된다. 다만 주가·시장 지수·환율 수치는 `sources`에 들어가지 않는다(주가만 쓴 데모 #3, #5는 `sources` 0건, `used_tools`로만 추적).
- *브라우저에서 보고서 확인*: 프론트엔드가 이 저장소에 없어서 **백엔드 API(`GET /api/research`, `/{id}`)까지만** 되어 있고 화면은 프론트 연동이 필요하다.
- 관련 테스트: `test_agent_slow.py::test_comprehensive_question_produces_markdown_sections`(여러 tool을 쓴 질문에서 `##` 섹션이
  실제로 나오는지), `::test_simple_quantity_question_is_not_forced_into_unrelated_sections`(단순 조회 질문이 호출 안 한 tool의
  섹션을 지어내지 않는지). 둘 다 slow(실제 Ollama 호출).

**FR-12 관심종목 및 리서치 이력 — 완료 (2026-10-01)** (인수조건 3개)
- *리서치 이력 조회*: 충족. `GET /api/research`(최신순, `limit`/`offset`, `total`), 후속 질문 스레드는 `previous_report_id`.
- *저장된 결과 재열람*: 충족. `GET /api/research/{id}`가 질문·답변·sources·used_tools를 복원한다(`DELETE`도 있음).
- *관심 종목 추가/삭제*: 충족. `watchlist` 테이블 + `GET`/`POST /api/watchlist`, `DELETE /api/watchlist/{ticker}`.
  이 서비스에는 로그인이 없어서(API.md "인증 없음") 사용자 구분을 프론트가 만드는 디바이스ID(`X-Device-Id` 헤더, uuid,
  localStorage 저장)로 했다 - 인증이 아니라 그 값을 그대로 신뢰하는 구분자라서, 헤더 값을 공유하면 다른 사람의
  관심종목을 볼 수 있다는 한계가 있다(설계상 받아들인 한계, README "DB 스키마 개요" 참고).
  연구 이력(`research_report`)은 이 디바이스ID를 쓰지 않고 여전히 전체 공용이다(이번 작업 범위 밖).

**FR-13 AI 시장 관심 종목 탐색 — 완료 (2026-10-01)** (인수조건 2개)
- *후보 탐색·스크리닝*: 충족. 새 외부 데이터 소스 없이 이미 수집된 `stock_price`(300종목 일별 OHLCV)만으로 `app/discovery.py`가
  세 가지 집계를 한다 - **gainers**(최근 거래일 등락률 상위 N, 급등), **losers**(등락률 하위 N, 급락), **volume_surge**(당일
  거래량이 직전 `window_days`거래일(당일 제외) 평균 거래량의 `VOLUME_SURGE_MIN_RATIO`(2.0)배 이상인 종목, 배수 내림차순).
  복잡한 예측/ML 없이 순수 정렬·필터만 한다(market_tool/fx_tool과 같은 "실용성 우선" 원칙). 당일 데이터가 없는 종목은 집계에서
  빠진다. 평균 거래량 계산에서 당일을 뺀 이유: 포함하면 급증한 당일 수치가 자기 평균을 끌어올려 배수가 희석된다.
- *대화형 질문 + REST 엔드포인트 둘 다 지원*: 충족.
  - 채팅: `discovery_tool(category, limit)`을 `agent.py`에 `market_tool`/`fx_tool`과 같은 패턴으로 연동했다 - 기본 tool
    목록에는 없고 `app/routing.py`의 `needs_discovery_tool`이 걸린 질문에만 추가한다(상시 노출 시 multi 호출이 깨지는 문제,
    FR-06/FR-07 해결 기록과 동일한 이유). `build_request`는 이제 market/fx/discovery 세 게이트를 리스트로 처리해서, 한
    질문에서 여러 개가 걸리면 전부 추가된다(아무 게이트도 안 걸리면 이전처럼 `SYSTEM_PROMPT`/`TOOLS` 객체를 그대로 돌려줌).
  - REST: `GET /api/discovery/trending?category=gainers&limit=10` - 질문 없이 프론트가 바로 호출할 수 있다. 쿼리 파라미터는
    `category`(gainers/losers/volume_surge)와 `limit`(1~50)뿐이다.
  - 게이트 키워드: "급등"/"급락"/"상한가"/"하한가"는 **"종목"과 함께 나올 때만** 인정한다(실측으로 "LG화학 주가 급등한
    이유가 뭐야?" 같은 **특정 종목** 질문도 bare 단어만 보면 걸려서, multi 그룹 등 다른 질문에 discovery_tool이 섞여 들어가는
    것을 확인하고 고쳤다 - 아래 회귀 검증). 그 외 "거래량 (급증/증가/늘어)", "요즘/오늘/최근 뜨는/핫한/인기 종목", "오늘의
    관심종목", "관심종목 추천/탐색", "오늘/최근 많이/크게 오른/내린/떨어진/빠진 종목" 패턴도 더했다.
- **회귀 검증**: `scripts/benchmark_routing.py --reps 4`로 discovery/discovery_ho를 포함한 15개 그룹 전체(66문항×4회,
  2026-10-01 측정)를 돌렸다. discovery 88%(21/24, 게이트는 6문항 전부 정확히 잡지만 LLM이 간혹 다른 tool만으로 답함 -
  multi 그룹과 같은 성격의 노이즈), discovery_ho 80%(16/20, 게이트가 못 잡는 "관심 가질 만한 종목 추천해줘" 1문항에서만
  실패하고 나머지 4문항은 16/16). 기존 그룹은 전부 과거 기록 범위 안(rag 88%, rag_ho 66%, disc_list/disc_ho 100%, multi
  75%, stock/news 100%, market/market_ho 100%, fx/fx_ho 100%, no_tool/no_tool_ho 0%). **market_tool/fx_tool/discovery_tool
  세 tool 모두 자기 그룹 밖 264개 질문에서 오호출 0건**이라, discovery_tool 추가가 다른 모든 그룹의 `build_request` 결과를
  바이트 단위로 전혀 바꾸지 않았다는 뜻이다(식별자 비교상 완전히 같은 경로).
- 관련 테스트: `test_discovery.py`(16개, rank_* 순수 로직 + dev DB 통합), `test_routing.py`의 `needs_discovery_tool`/
  `build_request` 3-게이트 조합 테스트, `test_discovery_api.py`(7개, mock), `test_agent_slow.py`의 slow 테스트 1개.
- **한계(솔직하게)**: "관심"의 기준이 등락률·거래량 수치뿐이다. 왜 움직였는지(뉴스·공시 같은 질적 신호)는 전혀 반영하지
  않는다 - 그건 news_tool/disclosure_tool/rag_search_tool의 몫이고, discovery_tool과 자동으로 엮이지 않는다. 거래량 급증
  임계값(2.0배)과 평균 window(기본 20거래일)는 고정 상수이고 API/tool 모두 바꿀 수 없다(요구사항 범위를 "category와 limit
  정도만"으로 명시한 데 따름). 키워드 게이트는 "관심 가질 만한 종목 추천해줘"처럼 멀리 떨어진 구어체 표현은 못 잡는다
  (market_tool의 `KNOWN_MISSES`와 같은 성격의 알려진 한계). 상장폐지·거래정지 등으로 당일 데이터가 없는 종목은 조용히
  제외된다(안내 메시지 없음).

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
- **프론트엔드:** React + Vite + TypeScript, `feature/frontend` 브랜치(원격에 push됨, `feature/backend`에는 코드가 없음).
  리서치/기록/관심종목/모의투자/오늘의 관심종목은 API.md 기준으로 연동 완료. 종목 상세의 과거 주가·뉴스·공시, 종목
  목록은 대응 API가 없어 mock(위 "프론트엔드 현황" 참고).

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
pytest -m "slow or not slow"      # 전부 한 번에 (2026-10-01 기준 608개 통과)
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
  (직전 답변은 800자까지만 맥락에 넣고, 이전 tool 호출/결과는 넣지 않음). 회사명이 없는 후속 질문에서 모델이 종목 인자를 깨뜨리거나
  엉뚱한 회사로 채우면 직전 질문의 종목으로 되돌리는 보정이 있음(`app/agent.py`의 `_repair_company_args`, 아래 "해결된 이슈 기록")
- [ ] FR-01 자연어 질문 분석: **부분 구현** — 질문 입력·Tool 선택·종목 식별은 됨(통칭은 별칭 사전), 기간 식별은 `period_parser`가 지원하는
  표현(오늘/어제/이번주/지난주/이번달/지난달/올해/작년, N일/N주/N개월/N년)은 코드가 직접 처리하고(2026-10-01) 그 외는 LLM 추론에 의존.
  뉴스/공시/RAG는 여전히 기간 인자가 없어 기간을 무시함
- [x] FR-07 경제지표 영향 분석(1차 범위: 환율만, 금리는 범위 밖): `exchange_rate` 테이블(USD/KRW) + `fx_tool` +
  `routing.needs_fx_tool` 키워드 게이트(market_tool과 같은 패턴, 기본 tool 목록에는 없음). 데이터는
  `scripts/collect_exchange_rate.py`로 실제 수집 완료(2026-10-01, 129행)
- [ ] FR-11 AI 리서치 보고서: **백엔드 부분 구현 / 프론트 연동 필요** — 답변 자동 생성·근거(sources)·저장·조회는 됨.
  2026-10-01부터 호출한 tool에 맞는 섹션(주가 동향/원인 분석/뉴스·공시 근거/시장 상황/위험요인)으로 답변을
  구조화하도록 유도하지만, 위험요인 판단 등은 여전히 LLM 서술에 의존하고 전담 검증 로직은 없음
- [x] FR-12 관심종목 및 이력: 리서치 이력(기존 완료)에 이어 관심종목 추가. `watchlist` 테이블 + `GET`/`POST /api/watchlist`,
  `DELETE /api/watchlist/{ticker}`(종목 중복 등록 409, 미등록 삭제 404). 로그인이 없어 `X-Device-Id` 헤더(프론트가 만드는
  uuid)로 사용자를 구분(아래 "모의투자 기능"과 같은 방식, 인증 아님)
- [x] FR-13 시장 관심 종목 탐색: `app/discovery.py`(급등/급락/거래량 급증, stock_price만으로 순수 집계) +
  `discovery_tool`(채팅, market_tool/fx_tool과 같은 키워드 게이트 패턴) + `GET /api/discovery/trending`(REST, 질문 없이
  바로 호출). "관심" 기준은 등락률/거래량 수치뿐이고 뉴스·공시 같은 질적 신호는 반영하지 않음
- [x] 종목 실시간 시세: `GET /api/stocks/{ticker}/realtime-price`. 계좌 개설이 필요한 KIS Open API 대신, 네이버 금융
  종목 페이지가 장중에 쓰는 비공식 폴링 API(`polling.finance.naver.com`, 인증 불필요)를 서버가 대신 호출. 종목별
  3~5초 서버 캐싱, 상위 소스 실패·장외 시간에는 예외 대신 DB 최근 종가로 자동 폴백(`is_realtime`/`source`로 구분)
- [x] REST API(리포트 생성/목록/조회/삭제) + 에러 처리(Ollama/DB 장애 시 502/503) + CORS
- [x] 자동 테스트(pytest)와 API 문서
- [x] 프론트엔드 연동(2026-10-02): 리서치/기록/관심종목/모의투자/오늘의 관심종목까지 API.md 기준으로 연동 완료
  (`feature/frontend` 브랜치). 종목 목록·과거 주가/뉴스/공시는 대응 API가 없어 mock 유지. 실제 Ollama+DB로
  end-to-end 테스트는 아직 안 함(위 "프론트엔드 현황" 참고)
- [x] 모의투자 기능: `GET /api/portfolio`(잔고+보유종목+평가손익), `POST /api/portfolio/orders`(매수/매도, 현재가로 즉시 체결).
  `virtual_account`/`holding`/`trade` 3개 테이블, 디바이스ID당 계좌 1개(첫 요청에 초기 잔고 1,000만원으로 자동 생성).
  매수는 가중평균으로 평균단가를 다시 계산하고 매도는 평균단가를 바꾸지 않으며, 전량 매도되면 보유 레코드를 지워서
  재매수가 과거 평균단가에 영향받지 않게 함. 잔고 부족/수량 초과는 400, 가격 조회 실패(실시간+DB 폴백 둘 다 실패)는 503.
  호가 단위·장 시간 체크는 범위 밖(장외에도 체결됨, 가격은 `app/realtime_price.py` 폴백 규칙을 그대로 따름)
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
- **기간 표현 처리의 남은 한계 (2026-10-01 기간 파서 도입 이후에도 남은 부분)**: `period_parser`가 지원하지 않는 표현("일주일", "3분기",
  "N일 전"처럼 시점을 가리키는 표현 등)은 여전히 LLM 추론에 의존해 부정확할 수 있다. `news_tool`/`disclosure_tool`/`rag_search_tool`은
  여전히 기간 인자 자체가 없어 "지난주 뉴스"도 그냥 최신 N건이다. `stock_tool`/`market_tool`의 `period_days` 상한(60)은 그대로라
  "올해"/"작년"처럼 큰 범위는 여전히 조용히 60으로 잘린다. 평일 수는 공휴일을 고려하지 않는 근사치다(KRX 휴장일 캘린더 없음). 자세한
  지원 범위는 `app/period_parser.py` 모듈 docstring, 해결 경위는 아래 "해결된 이슈 기록" 참고.
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

- **[완료] 후속 질문(FR-10)이 직전 종목을 유지하지 못하던 문제** (별칭 작업 중 발견, 이전 FR-10 검증이 놓친 것): 모델이 회사명 없는 후속 질문("그럼 최근 뉴스는?")의
  `company_name`에 "삼성전자"를 기본값처럼 채웠다. 그 이름은 DB에서 정상 해석되니 기존 보정(해석 실패 시에만 동작)이 개입하지 못했고, 다른 회사의 뉴스로 조용히 답했다.
  직전 종목별 유지율(5회씩): 삼성전자 5/5, KCC 4/5, SK하이닉스 3/5, 카카오 1/5, 현대차 0/5(삼성전자 제외 5종목 합계 8/25 = 32%). **이전 FR-10 검증이 삼성전자
  한 종목으로만 이뤄져(24/24) 모델의 기본값과 우연히 일치해서 이 결함이 가려졌다.** 수정: 이번 질문에 회사명이 없고 직전 질문에 회사가 정확히 하나면, 모델이 채운 회사가
  그 종목이 아닐 때 직전 종목으로 바꾼다(이번 질문에 회사가 있으면 그 회사를 따름, 직전 보고서가 없으면 이 경로는 실행되지 않음). 수정 후 6종목 x 6회 36/36이고,
  이번 질문에 다른 회사를 명시하는 경우와 공시·시장 비교 후속도 직전 종목을 유지/명시 종목을 따르는 것을 확인했다. slow 테스트를 삼성전자가 아닌 카카오·네이버로 바꿨고,
  수정 전 코드에서는 그 6개 중 4개가 실패한다. 한계: 이번 질문에 회사가 없으면 직전 **질문**의 종목이 우선이라, 직전 **답변**에만 나온 다른 회사를 가리키는 후속 질문
  ("그중 두 번째 회사 뉴스는?")은 직전 질문의 종목으로 바뀐다(회사명을 직접 쓰면 그 회사를 따른다).

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

- **[완료] FR-01 기간 표현 처리 — 코드 레벨 기간 파서(`app/period_parser.py`) 도입 (2026-10-01)**: 기간을 해석하는 코드가 없어서
  LLM이 "3개월"을 `period_days=3`(3일)으로 잘못 바꾸는 경우가 6회 중 2회 있었다(위 "FR-01 인수조건별 검증"). 이제 질문에서 고정 표현
  (오늘/어제/이번주·이번 주/지난주·지난 주/이번달·이번 달/지난달·지난 달/올해/작년)과 수량 표현(N일/N주/N개월/N년)을 코드가 직접 인식해서,
  인식에 성공하면 `agent._apply_period_override`가 `stock_tool`/`market_tool`의 `period_days`를 LLM 추론값 대신 그 값으로 덮어쓴다.
  인식하지 못하면(지원 목록 밖의 표현, "N일 전"처럼 특정 시점을 가리키는 표현, 숫자 없는 "일주일"/"한 달" 등) 예외 없이 이전과 똑같이
  LLM 추론값을 그대로 쓴다.
  - `period_days`는 `stock_tool`/`market_tool` 모두 달력 일수가 아니라 "최근 N개 거래일 행"으로 쓰이므로(두 tool 다 날짜로 필터링하지
    않고 `LIMIT period_days`), 파서는 날짜 범위를 구한 뒤 그 범위의 평일(월~금) 수를 세어 돌려준다(`_business_days_between`). KRX
    공휴일 캘린더가 없어 완전히 정확하지는 않지만(공휴일도 평일로 센다), 기존 LLM 추측(3개월→3일 오류 등)보다 근거가 분명하고 일관된
    근사치다. 60을 넘는 값("올해"≈196 거래일, "작년"≈261 거래일)은 두 tool의 `to_int(..., hi=60)`이 이전과 똑같이 조용히 60으로 자른다
    - 이 모듈이 그 제한 자체를 바꾸지는 않는다.
  - "지지난달"(그 이전 달)이 "지난달"의 부분 문자열이거나 "재작년"이 "작년"의 부분 문자열인 것처럼, 다른 뜻인데 겹치는 표현은
    `(?<!지)지난\s?달`류의 부정 후방탐색(negative lookbehind)으로 제외했다. "N년 전"처럼 기간(범위)이 아니라 특정 시점을 가리키는
    표현은 숫자+단위 뒤에 "전"이 오면 제외한다(부정 전방탐색). "2024년"처럼 비현실적으로 큰 수량(년 50·개월 120·주 520·일 3650 초과)은
    기간이 아니라 연도 등 다른 의미로 보고 인식하지 않는다.
  - `news_tool`/`disclosure_tool`/`rag_search_tool`은 애초에 기간 인자가 없어서(이번 작업 범위 밖) "지난주 뉴스"도 여전히 최신 N건이다.
  - **회귀 확인**: `git diff`로 보면 이번 변경은 `agent.py`에 import 한 줄, `_apply_period_override` 함수 신설, `_answer()`에 그 함수를
    부르는 한 줄과 결과를 기록하는 조건문 세 줄을 더한 것뿐이고 **기존 줄은 한 줄도 고치지 않은 순수 추가 diff**다(32 lines, 0 삭제).
    `_apply_period_override`는 기간 표현을 인식하지 못하면(`parse_period`가 None) 즉시 `{}`를 돌려주고 호출(calls)을 전혀 건드리지
    않으므로, "인식 못 하는 질문에서는 이전과 완전히 동일하다"는 것이 코드 구조로 증명된다. 이 불변식을 합성 입력으로 직접 확인했다
    (`test_period_override.py`): 실제 라우팅 벤치마크 질문 55개 + 회사명(별칭 사전의 등록명 44개) × 기간 표현 없는 질문 템플릿 12개 =
    528개, 총 583개 중 벤치마크의 10개("최근 5일", "이번 주" 등 원래도 지원 대상인 표현 포함 질문)만 인식되고 **나머지 573개는 호출
    인자가 한 글자도 바뀌지 않음**(완전 동일, 차이 0건) - 이전 작업들(company_resolver 2,292개, market_tool 게이트 44개)과 같은 방식의
    회귀 검증이다.
  - 테스트: `test_period_parser.py`(파서 자체, 37개 - 고정/수량 표현, 평일 계산, 겹치는 표현 제외, 복수 표현 중 첫 번째 우선), `test_period_override.py`
    (agent 연동 68개 - override 단위 테스트, `_answer()` 통합, 위 회귀 검증), `test_agent_slow.py`에 실제 Ollama 호출로 "지난달" 질문의
    `stock_tool` 호출 인자가 파서 값과 같은지 보는 slow 테스트 1개 추가. 전체 522개(기본 512 + slow 10) 통과.
  - 알려진 한계: 후속 질문("그럼 지난달은?")에서 직전 질문의 기간을 이어받지 않는다(이번 질문 텍스트만 본다 - `_repair_company_args`의
    종목 이어받기와 다름). 위 "미해결 이슈"의 "기간 표현 처리의 남은 한계"에 나머지 제약을 정리했다.

- **[문서 정정] FR-05 정의 오해 바로잡음 (2026-10-01, 코드 변경 없음)**: `db/schema.sql`이 `research_report`를 FR-05로 표시해서
  지금까지 "FR-05는 PRD 정의 미확인"으로 남겨뒀는데, PRD 원문을 확인한 결과 **FR-05는 "주가 변동 원인 분석"**이고, `research_report`가
  속하는 "AI 리서치 보고서 생성"은 **FR-11**이었다(스키마 파일의 라벨링이 PRD와 어긋났던 것). 코드는 바꾸지 않고 README의 FR 표와
  인수조건 검증만 바로잡았다 - 현재 코드에서 FR-05에 해당하는 것은 `agent.py`가 "왜 올랐는지" 질문에 stock_tool+news_tool을 함께
  호출하도록 유도하는 부분과, 종목 고유/시장 전체 요인 구분을 제공하는 FR-06의 `market_tool`이다(자세한 내용은 위 FR 표와 "FR-05
  인수조건별 검증"). 전담 로직이 없고 LLM 서술에 의존하는 "부분 구현"으로 평가했다.

- **[완료] FR-07 환율 영향 분석 — `exchange_rate` + `fx_tool` + 키워드 게이트 (2026-10-01)**: 금리는 쓸 만한 무료 소스를
  찾지 못해(시도한 FDR 심볼 2개 404) 범위에서 빼고 환율(USD/KRW)만 구현했다. `market_index`/`market_tool` 전례를 그대로
  따랐다 - `app/collectors.py`의 `fetch_and_save_exchange_rate`(FDR 수집 후 upsert, 멱등)와 `scripts/collect_exchange_rate.py`
  (실제로 129행 수집 완료, 2026-04-02~09-29), `app/tools/fx_tool.py`의 `fx_tool(period_days=5)`(최근 N거래일 현재가·전일대비
  등락률·추세), `app/routing.py`의 `needs_fx_tool`(키워드: 환율/원달러/USD-KRW).
  - FDR이 통화쌍에는 `Change` 컬럼을 주지 않는다(실제 호출로 확인, market_index가 쓰는 KRX 심볼과 다름) - 종가의 일별
    변화율(`pct_change`)을 직접 계산해서 저장한다.
  - `fx_tool`도 `market_tool`처럼 기본 tool 목록에 상시 노출하면 multi 호출이 깨질 위험이 있어(FR-06 해결 기록 참고) 기본
    TOOLS에 넣지 않고, `needs_fx_tool`이 걸린 요청에만 `agent.build_request`가 추가한다. `build_request`를 두 게이트를
    함께 처리하도록 고쳐서(market/fx 둘 다 안 걸리면 이전처럼 `SYSTEM_PROMPT`/`TOOLS` 객체를 그대로(식별자 동일) 돌려줌 -
    `test_routing.py`의 identity 단언이 그대로 통과), 한 질문에서 둘 다 걸리면(예: "환율 때문에 코스피가 빠졌어?") 둘 다
    추가되는 것도 확인했다. 기간 파서(`app/period_parser.py`)도 `fx_tool`의 `period_days`에 바로 연결했다(`_PERIOD_ARG`에
    `fx_tool` 추가 - 예: "환율 지난달 추이" 같은 질문도 정확한 거래일 수로 바뀐다).
  - **회귀 검증**: `scripts/benchmark_routing.py --reps 4`로 fx/fx_ho를 포함한 13개 그룹 전체(66문항×4회=264회 호출,
    2026-10-01 측정)를 돌렸다. fx 100%(24/24), fx_ho 100%(20/20)이고, 기존 그룹은 rag 78%(25/32), rag_ho 69%(22/32),
    disc_list/disc_ho 100%, multi 62%(10/16), stock/news 100%, market/market_ho 100%, no_tool/no_tool_ho 0% - 전부 기존에
    기록된 범위 안(예: multi는 노이즈 범위 38~78%로 기록돼 있음)이라 유의미한 회귀가 아니다. 특히 **market_tool/fx_tool
    둘 다 자기 그룹 밖 220개 질문에서 오호출이 0건**이었는데, 이는 그 220개 질문에 대해 `build_request`가 바이트 단위로
    이전과 동일한 `SYSTEM_PROMPT`/`TOOLS`를 돌려줬다는 뜻이라서(식별자 비교상 전혀 다른 경로를 타지 않음), 그 그룹들의
    성공률 변동은 전부 LLM 실행 간 노이즈이지 fx_tool 추가의 영향이 아니라고 결론 내릴 수 있다.
  - fx_tool 게이트는 market_tool의 "코스피/코스닥"처럼 뒤 문맥을 더 보지 않고 "환율"이라는 단어 하나로 충분히 구체적이라고
    보고, "환율이 오르면 왜 수출주가 유리해?" 같은 개념 질문에도 True가 나오는 것을 허용한다(오탐 비용은 tool 목록이
    하나 느는 정도, market_tool의 `KNOWN_FALSE_POSITIVES`와 같은 성격 - `test_routing.py`의 `FX_KNOWN_FALSE_POSITIVES`).
  - 테스트: `test_fx_tool.py` 11개(단위 + dev DB 통합), `test_routing.py`에 `needs_fx_tool`/`build_request` 조합 테스트
    추가, `test_agent_slow.py`에 실제 Ollama로 "요즘 환율 어때?" 질문이 `fx_tool`을 부르는지 보는 slow 테스트 1개 추가.
  - 알려진 한계: 금리·경제 이벤트는 범위 밖. 환율 수치는 `sources`(근거 문서 목록)에 들어가지 않는다(주가·시장 지수와
    동일한 기존 한계, `app/sources.py`가 news/disclosure/rag 문서만 다룸).

- **[완료] FR-11 답변 구조화 — 최종 답변 생성 단계에 섹션 안내 추가 (2026-10-01)**: 지금까지는 답변이 질문 하나에 대한
  자유 서술이라 PRD가 말하는 종합 보고서 형식(주가 동향/원인/뉴스·공시/시장 상황/위험요인)이 없었다. `agent.py`의
  `ANSWER_STRUCTURE_INSTRUCTION`을 최종 답변 생성(`_answer`의 3단계) 직전에만 `messages`에 추가해서, 실제로 호출한
  tool의 결과가 뒷받침하는 섹션만 `## 섹션 제목` 형식으로 나누고 호출하지 않은 tool의 섹션은 쓰지 말라고 지시한다(단순
  조회 질문은 섹션 없이 한두 문장으로 답하라는 지시도 함께).
  - **1차 호출(tool 선택)에는 영향이 없다 - 설계상 그렇게 만들었다.** `SYSTEM_PROMPT`/`TOOLS`는 전혀 건드리지 않고
    `_answer()`의 두 번째 `ollama.chat()` 호출 직전에만 별도 메시지로 추가했으므로, `scripts/benchmark_routing.py`가
    측정하는 `build_request`/`_first_call` 경로는 이 상수를 아예 거치지 않는다. 즉 라우팅 회귀가 코드 구조상 있을 수
    없다(위 FR-07의 회귀 측정도 이 사실을 실측으로 뒷받침한다 - fx_tool과 무관한 질문들은 성공률 변동이 전부 LLM
    노이즈였다).
  - **프롬프트 반복(실측)**: 처음 "마크다운 소제목(##)으로 나눠서 쓰라"는 한 문장짜리 지시만 줬을 때, 실제 호출에서
    모델이 섹션 헤더 없이 글머리 기호(`*`) 목록 + 마지막 한 문장 요약으로만 답했다(stock_tool+news_tool+market_tool 모두
    불렀는데도 `##`가 0개). 지시를 "줄글이나 글머리 기호 목록이 아니라 `## 섹션 제목`으로 나누라"고 구체화하고 실제 형식
    예시(`'## 주가 동향\n삼성전자는 ...'`)를 넣은 뒤 재측정하니 `## 주가 동향`, `## 원인 분석` 등 실제 헤더가 나왔다.
  - 멀티 tool 호출 자체가 비결정적이라(multi 그룹 56~78%대) "여러 tool을 쓴 질문에 `##`가 나오는지"는 어떤 tool
    조합인지 따지지 않고 "tool 2개 이상 + `##` 포함"으로, "단순 조회 질문은 호출 안 한 tool 섹션을 안 만드는지"는
    "## 시장 상황"/"## 위험요인"/"## 뉴스" 부재로 확인했다(3회 반복 재현, `test_agent_slow.py`).
  - 알려진 한계: 위험요인 섹션을 실제로 쓸지, 쓴다면 뭘 위험요인으로 꼽을지는 전적으로 LLM 판단이고 그 판단의 사실성을
    검증하는 코드가 없다. 섹션 헤더 형식 준수도 모델 성향에 달려 있어 100% 보장되지 않는다(llama3.1:8b는 로컬 소형
    모델이라 README "답변 품질" 이슈와 같은 불안정성을 공유한다).
