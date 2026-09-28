# lab-scale — AI 투자 리서치 에이전트

한국 상장사(시가총액 상위 300종목)에 대해 "삼성전자 오늘 왜 올랐어? 주가랑 뉴스 같이 확인해줘" 같은 자연어 질문을 받으면,
로컬 LLM이 필요한 조회(주가·뉴스·공시·의미 검색)를 스스로 골라 실행하고 그 결과로 답변과 근거 링크를 돌려주는 리서치 서비스입니다.
2인 팀 프로젝트로, 백엔드/데이터/에이전트와 프론트엔드를 나눠서 진행하고 있습니다.

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
                                     └─ rag_search_tool  bge-m3 임베딩 유사도 검색(뉴스+공시)
                                             │
                                     PostgreSQL 16 + pgvector (docker)
                                     company · stock_price · news · disclosure
                                     research_report · tool_call_log
```

- **백엔드:** FastAPI, SQLAlchemy 2, Alembic. 질문/답변/tool 호출 이력은 매 요청마다 DB에 저장되고 `GET /api/research`로 다시 볼 수 있습니다.
- **모델:** 답변·tool 선택은 `llama3.1:8b`, 임베딩은 `bge-m3`(1024차원, HNSW 인덱스). 모두 로컬 Ollama에서 돌아갑니다.
- **데이터 소스:** 주가는 FinanceDataReader, 뉴스는 네이버 검색 API, 공시는 DART OpenAPI(목록 + 원문).
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
python -m scripts.collect_universe --report r.json   # 종목별 뉴스 + DART 공시 목록
python -m app.embeddings --apply                # 임베딩이 비어 있는 뉴스/공시에 bge-m3 임베딩 채우기 (--apply 없으면 dry-run)
python scripts/collect_disclosure_content.py --apply   # 공시 원문 수집 (이어서 실행 가능, DART 일일 한도 약 2만 건)
```

공시 원문은 `disclosure.content`에 저장되어 `rag_search_tool`이 근거 요약문으로 LLM에 보여줍니다. 임베딩 텍스트에는 일부러 넣지 않습니다
(재본 결과 검색 적중이 떨어졌습니다. 근거는 `backend/app/embeddings.py`의 `_disclosure_text` 주석 참고).

## 테스트

```bash
pytest        # backend/ 에서
```

구성(단위 / DB 통합 / LLM을 호출하는 slow)과 실행 옵션은 [backend/API.md](backend/API.md)의 "테스트 실행" 절을 참고하세요.
`scripts/test_tool_calling.py`는 실제 Ollama로 에이전트를 몇 번 호출해보는 수동 스모크 스크립트이고,
`scripts/benchmark_models.py`는 tool 호출 성공률 벤치마크입니다(둘 다 pytest 대상이 아닙니다).

## API

엔드포인트(`POST /api/research`, `GET /api/research`, `GET /api/research/{report_id}`), 요청/응답 스키마, 에러 형식, CORS 안내는
**[backend/API.md](backend/API.md)** 에 있습니다.

## 구현 상태

- [x] 데이터 수집: 300종목 주가 · 뉴스(네이버, 비금융 도메인 필터·동명 종목 검색 보정) · DART 공시와 공시 원문
- [x] 임베딩 + 유사도 검색: bge-m3 / pgvector HNSW, 뉴스·공시 전체 임베딩 완료
- [x] 에이전트: 4개 tool 호출, 종목명 보정(오타/공백/우선주 처리), few-shot, 실패 시 안내 응답
- [x] 리포트 저장·조회: 질문/답변/근거/tool 호출 이력
- [x] REST API + 에러 처리(Ollama/DB 장애 시 502/503) + CORS
- [x] 자동 테스트(pytest)와 API 문서
- [ ] 프론트엔드 연동: 서윤님 담당, API 스펙은 확정(API.md), 실제 연동은 진행 필요
- [ ] 모의투자 기능: 미구현, 범위 논의 필요
- [ ] 데이터 정기 갱신: 지금은 수동 실행이라 자동화 여부 논의 필요
- [ ] 아래 Known Issues (tool 선택 신뢰도)

## Known Issues / TODO (tool-calling 신뢰도, llama3.1:8b)

`app/agent.py`의 tool 선택 신뢰도 관련 이슈. 재현/벤치마크 방법은
`backend/scripts/benchmark_models.py`, `backend/scripts/test_tool_calling.py`,
`backend/scripts/benchmark_routing.py` 참고.

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
  부족했음. 남은 이슈는 no_tool 남발 하나(위 항목: 프롬프트로는 못 풀어 보류). tool을 더 추가할 때는 이번처럼
  `scripts/benchmark_routing.py`로 변경 전/후를 interleaved A/B 측정해서 회귀
  여부를 확인할 것 (특히 multi처럼 노이즈가 큰 그룹은 n을 충분히 늘려서 판단).
  장기적으로 로컬 8B 모델 하나의 프롬프트 튜닝만으로 감당하기 어려워지면
  코드 레벨 사전 분류(키워드/임베딩 기반 라우터로 tool 후보를 먼저 좁히기)나
  disclosure_tool과 rag_search_tool을 하나의 tool로 통합하는 방안을 검토할 것.
