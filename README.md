# lab-scale — 국내 주식 AI 리서치 에이전트

[![CI (main)](https://github.com/SeoYoonLim/lab-scale/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SeoYoonLim/lab-scale/actions/workflows/ci.yml)

한국 상장 주식 300종목과 ETF 50종목에 대해 "삼성전자 최근 5거래일 수익률은?" 같은 자연어 질문을 받으면,
시세·뉴스·공시를 조회하고 근거 링크와 함께 답하는 리서치 서비스입니다. 수치는 DB 값을 코드가 계산하고, 로컬 LLM(Ollama)은 문장 정리와 도구 선택에만 씁니다.
해외 종목은 범위 밖입니다(질문하면 "분석 대상이 아닙니다"로 답하고 다른 종목으로 대신 분석하지 않습니다).
2인 프로젝트로 백엔드·데이터·에이전트(정민세)와 프론트엔드(서윤)를 나눠 진행했고, 최종 코드는 `main`에 있습니다.

## 주요 기능

- **AI 리서치**: 시세·수익률·최고/최저 종가, 뉴스·공시 요약, 사건 이후 주가 반응, 전망 질문. 답변마다 근거(출처)와 데이터 기준일, "분석 기준" 종목을 표시하고, 후속 질문은 "이어서 묻기"를 켰을 때만 이전 대화를 이어받습니다. 종목을 못 찾으면 되묻습니다.
- **종목·시장**: 종목 검색, 상세(차트·이동평균·뉴스·공시), 시장 개요, 급등락 탐색, ETF 구성종목·실질 노출.
- **모의투자**: 매수·매도, 체결 내역, 관심종목, 포트폴리오 성과, AI 진단과 이력, 오늘의 브리핑.
- **계정**: 가입·로그인, 이메일 인증, 아이디 찾기·비밀번호 재설정, 소셜 로그인(구글·네이버), 회원 탈퇴.
- **화면 언어**: 한국어·영어(질문 번역과 오류 메시지 포함). 접근성(스크린리더 안내, 키보드, 대비)을 고려했습니다.

## 구조

```
브라우저(React + Vite + TypeScript, react-query, zod)  ── frontend/
        │ HTTP/JSON
FastAPI(SQLAlchemy 2, Alembic)                          ── backend/
        ├─ 에이전트: 질문 해석(종목·기간) → 코드 경로 또는 LLM 도구 호출(주가·뉴스·공시·검색·환율·시장)
        └─ 답변 검증: 숫자·기간·면책·도구 이름 노출 차단
PostgreSQL 16 + pgvector (Docker)                       ── docker-compose.yml
Ollama: llama3.1:8b(답변·도구 선택), bge-m3(임베딩)
```

데이터 소스는 주가·지수 FinanceDataReader, 뉴스 네이버 검색 API, 공시 DART OpenAPI입니다. 무료 소스라 **데이터가 며칠 늦게 반영**되므로, 모든 답변과 화면에 기준일을 함께 표시합니다.

## 실행하기

필요한 것: Docker, Python 3.13, Node 24, [Ollama](https://ollama.com/download).

```bash
# 1. DB (저장소 루트, 호스트 포트 15432)
docker compose up -d

# 2. 백엔드 (backend/)
cp .env.example .env               # DATABASE_URL, JWT_SECRET(32자 이상, 공유·커밋 금지) 등을 채운다. 뉴스·공시 수집에만 NAVER_*, DART_API_KEY 필요
python -m venv .venv && .venv\Scripts\activate    # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
ollama pull llama3.1:8b && ollama pull bge-m3
uvicorn app.main:app --port 8000   # http://localhost:8000/docs

# 3. 프론트엔드 (frontend/)
npm ci
npm run dev                         # http://localhost:5173 (mock 없이 쓰려면 VITE_USE_MOCK=false, VITE_API_BASE_URL=http://localhost:8000/api)
```

- 데이터가 비어 있으면 평일 15:30(KST) 이후 `python -m scripts.refresh_data`로 채웁니다(약 15분, Ollama 필요). 수집 순서와 옵션은 [전체 README 보관본](docs/README_FULL_ARCHIVE_20261121.md)의 "데이터 수집"을 보세요.
- 첫 요청은 모델 적재 때문에 느리고 `/api/health`가 잠깐 `degraded`일 수 있습니다. 질문은 사용자당 1분 6개까지입니다(429).
- Windows에서 Ollama 기본 포트(11434)가 막히면 [보관본](docs/README_FULL_ARCHIVE_20261121.md)의 "재부팅 뒤 11434가 막혔을 때"를 보세요.

## 테스트와 CI

```bash
cd backend  && pytest -m "not slow and not integration"   # 단위(DB·Ollama 불필요)
cd backend  && pytest -m "integration and not slow"       # 개발 DB 통합
cd backend  && python -m evals.run_eval                   # AI 품질 평가(offline), --mode all 은 Ollama 필요
cd frontend && npm test && npx tsc -b && npm run lint && npm run build
```

최종 점검 기준(2026-11-21): 백엔드 단위 6,866개, 통합 645개, AI 품질 평가 244/244, 프론트 292개가 통과했고 GitHub Actions 6개 job(`backend-unit`, `secret-scan`, `detect-frontend`, `frontend`, `backend-integration`, `eval-offline`)이 성공했습니다.
시크릿 검사(`scripts/secret_scan.py`)가 push마다 돌기 때문에 키·토큰 값은 코드와 문서에 넣지 않습니다.

## 문서

| 무엇 | 어디 |
| --- | --- |
| API 명세(인증·엔드포인트·오류 형식·스키마) | [backend/API.md](backend/API.md) |
| 보안 통제 | [backend/SECURITY.md](backend/SECURITY.md) |
| AI 품질 평가 방법과 기준선 | [backend/evals/README.md](backend/evals/README.md), [docs/AI_QUALITY_EVALUATION.md](docs/AI_QUALITY_EVALUATION.md) |
| 최종 릴리스·데이터 품질 보고 | [docs/FINAL_RELEASE_REPORT.md](docs/FINAL_RELEASE_REPORT.md), [docs/FINAL_DATA_QUALITY_REPORT.md](docs/FINAL_DATA_QUALITY_REPORT.md) |
| 마지막 답변 품질 수정(2026-11-21) | [docs/FINAL_DEMO_QUALITY_FIXES_20261121.md](docs/FINAL_DEMO_QUALITY_FIXES_20261121.md) |
| 교수 시연 절차와 피할 질문 | [docs/PROFESSOR_DEMO_CHECKLIST.md](docs/PROFESSOR_DEMO_CHECKLIST.md) |
| 프론트엔드 연동·실환경 검증 | [docs/FRONTEND_INTEGRATION_HANDOFF.md](docs/FRONTEND_INTEGRATION_HANDOFF.md), [docs/FRONTEND_REAL_ENV_VERIFICATION.md](docs/FRONTEND_REAL_ENV_VERIFICATION.md) |
| 이전의 긴 README(구현 상태·FR 대응·알려진 이슈·CI 상세 전체) | [docs/README_FULL_ARCHIVE_20261121.md](docs/README_FULL_ARCHIVE_20261121.md) |

## 알려진 한계

- 데이터는 무료 API 특성상 수일 지연됩니다. 기간 질문("최근 30일")은 오늘 날짜 기준이라 마지막 며칠의 데이터가 비어 있을 수 있고, 답변에 실제 기준일이 적힙니다. 정확히 보려면 "최근 5거래일"처럼 거래일로 묻는 편이 좋습니다.
- 원인·전망처럼 LLM이 문장을 쓰는 답은 매번 표현이 다르고, 근거가 약하면 설명이 어색할 수 있습니다. 숫자는 코드가 검증합니다.
- 영어 질문은 한국어로 해석해 처리하며, 번역을 검증하지 못한 설명은 숨기고 데이터 요약만 보여 줍니다.
- 해외 종목, 일본어·중국어 화면 검증, 모바일 화면은 현재 범위 밖입니다.
- 투자 판단을 대신하지 않으며, 모든 답변에 면책 문구가 붙습니다.
