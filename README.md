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

`app/agent.py`의 tool 선택 신뢰도 관련 미해결 이슈. 재현/벤치마크 방법은
`backend/scripts/benchmark_models.py`, `backend/scripts/test_tool_calling.py` 참고.

- **no_tool 남발**: "주식 기본 용어 알려줘" 같은 개념성 질문에도 매번 불필요하게
  tool을 호출함 (반복 테스트 기준 0/5 ~ 6/6 실패 유지, rag_search_tool 도입 전엔
  stock_tool/disclosure_tool을, 도입 후엔 주로 rag_search_tool을 잘못 호출).
  답변 내용 자체는 안전(사실 왜곡 없음)하고, DB 조회 낭비(지연/비용)만 있는
  효율성 문제. 우선순위 낮음 — 아래 "4-tool 프롬프트 재설계"와 함께 처리.
- **특정 주제 질문이 rag_search_tool 대신 disclosure_tool로 라우팅됨**:
  "삼성전자 자사주 매입 관련 공시 내용 자세히 알려줘"류 질문은 rag_search_tool이
  적합한데, "공시"라는 단어가 disclosure_tool의 트리거 문구와 겹쳐서 대부분
  disclosure_tool(최신순 5건)로 감 (반복 테스트 기준 10회 중 1회만 rag_search_tool
  성공). disclosure_tool도 실제 DB 데이터를 반환하므로 사실 왜곡은 없지만, 의도한
  의미 기반 검색이 아니라서 답변 완성도가 떨어질 수 있음.
- **rag_search_tool 도입 시 SYSTEM_PROMPT/few-shot 국소 수정만으로는 위 이슈를
  못 고침**: disclosure_tool 트리거 문구와 안 겹치게 SYSTEM_PROMPT를 다듬고
  few-shot 예시(LG에너지솔루션)를 추가해봤지만, 목표 케이스는 거의 개선 안 됐고
  (0/5 -> 1/10) 오히려 기존에 안정적이던 multi-tool 케이스(stock_tool+news_tool
  동시 호출)가 80%대에서 50%로 떨어지는 회귀가 발생 (일부는 종목명이 깨진
  문자열로 환각되기도 함). 해당 변경은 되돌렸고, 그 뒤 multi-tool 케이스는
  100%(5/5)로 복구 확인. (이후 few-shot은 뉴스 헤드라인이 답변에 복사되는 오염을
  없애려고 "뉴스 없음" 예시로 한 번 더 바꿨고, 시스템 프롬프트 문장 추가는 multi-tool
  성공률을 떨어뜨려서 넣지 않음 — 근거는 `app/agent.py`의 FEW_SHOT_MESSAGES 주석.)
- **결론 / 다음 작업**: tool이 3개 -> 4개로 늘면서, 로컬 8B 모델 하나에
  국소적인 프롬프트 패치를 반복하는 방식은 한 곳을 고치면 다른 곳이 깨지는
  패턴이 반복됨 (whack-a-mole). 다음에 tool을 더 추가하기 전에 SYSTEM_PROMPT/
  TOOLS/FEW_SHOT_MESSAGES 전체를 한 번에 재설계하고,
  `scripts/benchmark_models.py` 같은 재현 가능한 벤치마크로 검증할 것. 위
  두 이슈(no_tool 남발, 공시 라우팅)는 그때 함께 처리.
