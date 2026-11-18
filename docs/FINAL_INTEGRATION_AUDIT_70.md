# 70차 최종 통합 점검: 투자 리서치 에이전트 완성 계획 (2026-11-18)

이번 차수는 조사와 계획만 했다. 코드 수정, 커밋, push, 병합, 재시작, DB 쓰기, 외부 대량 수집은 하지 않았다.

조사 방법

- DB는 `SET default_transaction_read_only=on` 또는 `PGOPTIONS=-c default_transaction_read_only=on`으로만 읽었다.
- 실행 중인 백엔드에는 공개 GET만 보냈다. 외부 원천을 부르는 `realtime-price`는 제외했다.
- 최신 프런트엔드는 스크래치 worktree(origin/main)에서 따로 의존성을 설치해 검증했다. 사용자 폴더는 건드리지 않았다.

## 요약

| 구분 | 결론 |
| --- | --- |
| 시연을 막는 코드 결함(P0) | **확인된 것 없음**. 백엔드 단위 6,604, 프런트 246 테스트 통과, 공개 API 18개가 최신 프런트 스키마와 일치 |
| 시연 환경 조건(P0 조건부) | ① 프런트가 `VITE_USE_MOCK=false` 없이 뜨면 조용히 mock 데이터로 동작 ② 로컬 프런트 worktree가 main보다 38커밋 뒤처짐 ③ 실행 중인 백엔드는 69-A 반영 전 코드 |
| 핵심 남은 일(P1) | 기간을 붙인 원인 질문("지난주 왜 급등", "한 달 동안 왜 떨어졌어")이 아직 LLM 경로라 전제 확인·원인 특정 불가 안내가 없고, LLM이 지수 수치를 종목에 붙인 문장이 나왔다 / 시세 6일·환율 16일 지연 / main 병합 |
| 브랜치 | main의 백엔드는 41차(`4eb9763`) 상태. `feature/backend`(69차)를 통째로 병합하면 충돌 0. 69차 커밋만 골라 올리는 것은 불가(68차 파일에 의존) |
| 분봉·라이선스 | 69차 중단 상태 유지 확인. main에는 분봉 workflow가 없어 예약 실행 불가. 공개 원본 아티팩트 7개는 2026-12-17 만료 예정(삭제 안 함) |

다음 차수에 할 일 하나: **71차 = 기간을 붙인 원인 질문을 69차 원인 코드 경로로 처리**(11절에 지시서).

---

## 1. 현재 전체 프로젝트 상태

| 영역 | 위치 | 상태 |
| --- | --- | --- |
| 백엔드 | `C:\project\lab-scale\backend`(브랜치 `feature/backend`, HEAD `a614f8c`) | 69차까지 완료. 작업 트리 깨끗함. CI 3커밋 모두 성공(run 44207432775·44211462282·44211925353) |
| 실행 중 백엔드 | 127.0.0.1:8000, PID 37588, 2026-11-18 09:16 시작, `--reload` 없음 | **69-A 커밋(10시대) 전 코드가 메모리에 올라가 있다.** 원인 단독 질문은 지금 서버에서 아직 수정 전 동작이다(재시작은 이번 지시상 금지) |
| 프런트엔드 최신본 | origin/main `frontend/`(PR #16 `549f161`, 2026-11-17) | 타입 검사·oxlint·빌드 성공, vitest 246 통과·8 건너뜀(실제 백엔드 연동 테스트는 계정을 만들어 DB에 쓰므로 실행하지 않음) |
| 로컬 프런트 worktree | `C:\project\lab-scale-frontend`(브랜치 `feature/frontend` `77504c3`, 2026-11-10) | **main보다 38커밋 뒤처짐**. origin의 `feature/frontend`는 이미 지워졌다. 미추적 파일 2개(`how --stat 2fe2022`, `package-lock.json`)는 그대로 두었다 |
| 그 밖의 worktree | `C:\project\lab-scale-main`(`main-local` `f19f50a`, origin/main보다 7커밋 뒤), `C:\project\lab-scale-base35`(35차 기준선) | 조사만 함 |
| 프런트 실행 | 5173 | 실행 중이 아님 |
| Ollama | 127.0.0.1:13434, PID 40128 | 실행 중(답변 검증에 질문만 보냄) |
| DB | 컨테이너 `ai_investment_db`(pgvector/pg16, 16.15), DB `ai_investment`, alembic `c7e2a9f4d158` | 23개 테이블, 402,023행 |

## 2. 백엔드 완성도

### 2.1 API

- 실제 라우트: 56개 연산(`app.openapi()`로 추출)
- main(41차)과 `feature/backend`(69차)의 경로 집합과 OpenAPI 컴포넌트 스키마는 **같다**. 42~69차는 응답 필드 추가(dict 응답)와 내부 로직 변경이었다.
- `backend/API.md` 요약표(193행~)에 `GET /api/etf/{ticker}/holdings`, `GET /api/portfolio/exposure` 두 줄이 빠져 있다. 본문 절(154행, 4268행)과 21~22행 인증 표에는 있다.

| 영역 | API | 프런트 사용 |
| --- | --- | --- |
| A 시장 | `GET /api/market/overview`, `/api/status`, `/api/discovery/trending` | 사용 |
| B 종목 | `/api/companies`, `/api/stocks/{t}/price-history`(1~365거래일, 기본 90, `app/api/stocks.py` `MAX_PRICE_HISTORY_DAYS = 365`), `/api/stocks/{t}/realtime-price`, `/api/stocks/{t}/event-reactions`, `/api/etf/{t}/holdings` | 사용 |
| C 뉴스·공시 | `/api/stocks/{t}/news`, `/api/stocks/{t}/disclosures` | 사용 |
| D 리서치 | `POST/GET /api/research`, `GET/DELETE /api/research/{id}`(`view=record`), `/api/history/search`, `/api/briefings/daily/*` 4개 | 사용 |
| E 사용자 | 인증 14개, `/api/watchlist` 3개, `/api/portfolio*` 11개 | 사용 |
| 백엔드만 있음 | `/api/auth/oauth/*` 4개(소셜 로그인), `POST /api/backtest`, `GET /api/briefing`(채팅 브리핑), `GET /api/health` | **프런트 호출 없음** |

프런트가 필요로 하는데 백엔드에 없는 API는 **없다**. 대조 범위는 `frontend/src/api/http.ts` 219~495행 `httpApi`의 모든 경로다.

### 2.2 테스트·평가(69차 결과, 이번에 바뀐 것 없음)

| 항목 | 결과 |
| --- | --- |
| 단위(`-m "not slow and not integration"`) | 6,604 통과 |
| 69차 신규 / 68차 회귀 | 48 / 34 통과 |
| 품질 평가 `evals.run_quality` | 244/244, 회귀 0 |
| 오프라인 평가 `evals.run_eval --mode offline` | 59문항, 알려진 실패 11건(`ent_08·14·15·17·21~27`, 엄격 모드의 의도된 거절) |
| CI | backend-unit·backend-integration·eval-offline·secret-scan 성공, `MAX_EXCLUDED=100` |

## 3. 프런트엔드 연동 상태

### 3.1 정적 검증(최신 origin/main 프런트, 스크래치 복사본)

| 검사 | 결과 |
| --- | --- |
| `npx tsc -b` | 성공 |
| `npx oxlint` | 성공 |
| `npx vitest run` | 파일 29 통과·1 건너뜀, 테스트 246 통과·8 건너뜀 |
| `npx vite build` | 성공(가장 큰 청크 `index` 436KB, gzip 137KB) |

- 건너뛴 8개는 `src/api/http.integration.test.ts`다(`VITE_REAL_BACKEND=1`일 때만 돈다). 가입·주문·브리핑 생성으로 DB에 쓰므로 이번에는 돌리지 않았다.
- 접근성(`a11y*.test.tsx`)·포커스·언어 설정·4개 언어 커버리지(`src/i18n/coverage.test.ts`) 테스트는 통과했다.

### 3.2 실제 응답 계약(읽기 전용)

스크래치 테스트(`live70.test.ts`, 저장소 밖)를 만들었다. 최신 프런트의 `httpApi`를 그대로 호출해, 응답을 프런트의 zod 스키마로 검증했다. 대상은 실행 중인 백엔드(127.0.0.1:8000)다.

- **18/18 통과**: status, disclaimer, companies(주식·ETF), trending(gainers·volume_surge), price-history(5·60·365·ETF 20), event-reactions, ETF holdings(069500·360750), news(주식·ETF), disclosures(주식·ETF), market overview
- 응답 시간 3~64ms

### 3.3 확인한 위험

| 항목 | 근거 | 영향 |
| --- | --- | --- |
| mock 기본값 | `frontend/src/api/index.ts:115` `USE_MOCK = rawUseMock !== 'false'`. `.env.local`(`VITE_USE_MOCK=false`)은 오래된 `C:\project\lab-scale-frontend\frontend`에만 있고 `lab-scale-main`에는 없다 | 시연 PC에서 env 없이 띄우면 실제 백엔드 대신 가짜 데이터가 나온다(콘솔 경고는 오타일 때만) |
| 프록시 | `frontend/vite.config.ts` `'/api': 'http://localhost:8000'`, 백엔드는 127.0.0.1에만 바인딩 | Node에서 `localhost:8000` 실측 41ms 정상. 문제없음 |
| 오래된 worktree | `C:\project\lab-scale-frontend`가 main보다 38커밋 뒤 | 이 폴더로 시연하면 11-10 이후 화면(주문 개선, 진단 디자인 등)이 빠진다 |
| 브라우저 확인 | 5173이 꺼져 있고 이번 지시는 빌드·정적 검증만 허용 | 콘솔 오류·모바일 화면·키보드 동작은 **브라우저에서 확인하지 못했다**(프런트 담당 수동 확인 필요) |
| 지연 표시 | `MarketSummaryCard.tsx`, `PriceChartCard.tsx`, `BriefingView.tsx`, `EtfHoldingsCard.tsx`, `EventReactionsCard.tsx` 등이 지연·기준일 필드를 쓴다 | 정적 확인만 함 |
| 60차 제안 | `docs/FRONTEND_API_HANDOFF_60.md` 153행 `market_session`·`indices[].close_status` | 백엔드 미구현, 프런트 미사용(선택 필드 제안) |

## 4. DB 및 실제 데이터 상태(읽기 전용, 2026-11-18 10시대)

| 데이터 | 범위·최신 | 비고 |
| --- | --- | --- |
| 종목 | 350(주식 300, ETF 50) | `sector`는 전부 NULL |
| 시세 | 2023-10-30 ~ **2026-11-12**, 740거래일, 350종목 모두 11-12 있음 | 11-13·16·17(·18) 없음. 마지막 시세 수집 기록 2026-11-03 19:45, 이후 `prices` 단계 skipped |
| 지수 | KS11·KQ11 각 740행, ~**2026-11-12** | |
| 환율 | USD/KRW 1종, 153행, 2026-04-02 ~ **2026-11-02** | 시세보다 더 늦다(16일) |
| 뉴스 | 130,640건(임베딩 130,640, `is_relevant=false` 1,558), 2020-04-13 ~ **2026-11-17 09:32 KST**, 350종목 | 63차 복구분 포함. 종목당 1,000건 한계로 11-04~11-15 일부 공백(68차 확인) |
| 공시 | 9,936건(임베딩 9,936, 본문 9,885), 2026-06-24 ~ **2026-11-17**, 299종목 | |
| ETF 구성 | 4,926행, **50개 중 38개**, 기준일 2026-10-29~30 | 12개 ETF는 구성 없음 |
| 실적 | 2,925행, 298종목, 최신 접수 **2026-10-30** | 11월 3분기 실적 시즌 반영 안 됨 |
| 갱신 기록 | 마지막 실행 87번(2026-11-17 09:17, recovery, 뉴스·공시 복구) | 정기 스케줄러 없음 |
| 사용자 데이터 | users 2, research_report 40, daily_briefing 1, 진단 2, trade 3 | 값은 보지 않았다 |
| 분봉 | `stock_price_session` 테이블 없음 | 서비스 미사용(8절) |

**지연 표시 확인**

- 전망·원인·안전 답변: "마지막 시세(2026-11-12)가 기대 거래일(2026-11-17)보다 3영업일 지연"과 늦은 이유("미수집: 장 마감 뒤 시세 수집 기록 없음")를 붙인다(5절 3~5, 10b).
- 단, LLM 경로의 주가 흐름 답(5절 1번)은 "마지막 데이터는 2026-11-12 기준입니다"만 쓰고 **지연 경고가 없다**.

## 5. RAG 및 LLM 품질 상태

### 5.1 구조

- **RAG**: `app/tools/rag_search_tool.py`
  - 질문을 bge-m3(1024차원)로 임베딩해 HNSW 코사인 색인(`idx_news_embedding_hnsw`, `idx_disclosure_embedding_hnsw`, m=16)에서 찾는다.
  - 종목이 정해지면 후보를 30건 이상 넓게 뽑고, 제목 또는 본문 첫 60자에 종목명이 있는 기사만 남긴다(68차 `news_relevance.subject_match`).
  - 뉴스 임베딩 원문은 `(제목 + "\n" + 요약)[:500]`이고, 공시는 `회사명 + 제목 + (접수일)`이다(`app/embeddings.py`).
- **원인 질문**(69차)
  - 기준 시점은 기준일 정규장 마감(`answer_cause.cutoff_at`)이다.
  - 원인 단독 질문(종목 1개, 기간 없음·오늘·어제)은 코드 답이다(`agent._forecast_subject` 모드 `cause`).
  - 나머지 원인 질문은 LLM 경로에서 `answer_cause.filter_records`가 LLM에 넘기기 전에 거른다.

### 5.2 질문 유형 실검증

조건: 현재 작업 트리 코드, Ollama `llama3.1:8b` 실제 호출, DB 읽기 전용, 리포트 저장 없음.

| # | 질문 | 경로 | 결과 | 판정 |
| --- | --- | --- | --- | --- |
| 1 | 삼성전자 최근 주가 흐름 | LLM + stock_tool | 11-12 종가 208,000원, 20거래일 -0.95% | 수치 정확. **지연 경고 없음**(P2) |
| 2 | SK하이닉스 왜 떨어졌어? | 코드(cause_only) | 5거래일 -0.79%, 시장과 비슷, 원인 특정 불가, 기준일 뒤 995건 제외 | 정상 |
| 2b | 카카오 지난 한 달 동안 왜 떨어졌어? | LLM + 필터(기준일 뒤 4건 제외·다시 읽기) | 주가·시장 비교만 답하고 원인 항목이 없다. "카카오는 코스피 … 23거래일간 -16.31% 움직였습니다"라고 **코스피 수치를 카카오 주어에 붙였다**(실제 카카오 -0.94%, 코스피 -16.31%). 숫자 검증은 숫자가 도구 결과에 있어 통과시켰다 | **P1** |
| 3 | 삼성전자 향후 5거래일 전망 | 코드(forecast_brief) | 중립, 묶음 근거, 과거 검증 49.0%, 지연 경고 | 정상 |
| 4 | 현대차 20거래일 | 코드 | 중립, 20일선·60일선, 과거 검증 | 정상 |
| 5 | NAVER 3개월 후 | 코드 | 60거래일 하락 쪽(신뢰도 낮음) | 정상 |
| 6 | 카카오 최신 뉴스 | LLM + news_tool | 11-17 기사 4건 근거, 게시일 표시 | 정상 |
| 7 | LG에너지솔루션 최근 공시 | LLM + disclosure_tool | 목록 5번이 비었다("5."), "공시 내역의 내용은 위에서 언급된 것과 같습니다" 같은 군말 | **P2** |
| 8 | KODEX 200 구성 종목·비중 | 코드 | 10-30 기준 상위 10, 합계, 기준일·수동 수집 안내 | 정상 |
| 9 | 삼성전자 HBM 기사 근거 실적 영향 | LLM + RAG | 09-24~11-17 기사, 게시일 범위 표시 | 정상(원인 질문 아님) |
| 10 | 셀트리온 **지난주** 급등 이유 | LLM + 필터(기준일 뒤 5건 제외) | 실제 -0.71%(코스피 +5.59%)인데 **전제('급등')를 바로잡지 않았다**. "이러한 흐름은 시장 흐름과도 관련이 있습니다"라는 근거 없는 인과를 쓰고, 허가 신청 기사를 원인처럼 붙였다 | **P1** |
| 10b | 에코프로비엠 무조건 오르지? | 코드(request_safety) | 보장 불가 안내, 대안 질문, 기준일·지연 | 정상 |
| 11 | TIGER 미국S&P500 흐름·특징 | LLM + stock·news | 20거래일 흐름만 답하고 "특징"(추종지수·구성)은 없다 | P2 |

정리

- **코드 경로**(전망·원인 단독·ETF 구성·안전 안내)는 종목·수치·기준일·지연 표시가 모두 맞다.
- **문제는 기간을 붙인 원인 질문(2b·10)이다.**
  - 69차가 "한 달·지난주·이번 주" 같은 기간 원인 질문을 LLM 경로에 남겼다(`answer_cause.is_cause_only_question`의 `_CAUSE_ONLY_PERIODS = {"오늘", "어제"}`).
  - 미래 기사는 필터가 막았다. 그러나 전제 확인, "원인 특정 불가" 안내, 주어-수치 결합은 LLM에 맡겨져 있다.

## 6. API 누락 및 불일치 목록

| 구분 | 항목 | 근거 |
| --- | --- | --- |
| 프런트 필요·백엔드 없음 | 없음 | 2.1 |
| 계약 불일치 | 없음(공개 GET 18/18, 인증 API는 실행하지 않음) | 3.2 |
| 백엔드만 있음(UI 없음) | 소셜 로그인 4개, 백테스트, 채팅 브리핑, health | 2.1 |
| 문서 | `API.md` 요약표 2줄 누락(ETF holdings, exposure) | 2.1 |
| 제안 미구현 | `market_session`, `indices[].close_status`(60차) | 3.3 |
| 인증 API 계약 | 실행 검증 못 함(가입·주문이 DB에 쓴다). 69차까지 CI의 백엔드 통합 테스트와 프런트 단위 테스트(fixture)로만 확인됨 | 3.1 |

## 7. 시연 장애 요소

| 요소 | 상태 | 시연 전 조치 |
| --- | --- | --- |
| 프런트 mock 모드 | env가 없으면 mock | 시연 폴더에 `VITE_USE_MOCK=false` 확인 |
| 어느 프런트 폴더로 띄우나 | 로컬 worktree는 11-10 | origin/main 최신 `frontend/`로 띄우기 |
| 백엔드 코드 | 실행 중 서버는 69-A 전 | 시연 전 재시작(사용자 승인) |
| 시세 지연 | 11-12(시연일이 늦을수록 커짐) | 시세·지수·환율 갱신(사용자 승인, `scripts/refresh_data`) 또는 "저장 데이터 기준 시연"으로 안내 |
| 실시간 시세 | `realtime-price`는 네이버 polling(비공식) 호출 | 네트워크 없으면 실패 화면 확인 필요. 라이선스는 8절 |
| 기간 원인 질문 | 2b·10 | 71차에서 수정 |
| LLM 응답 시간 | LLM 경로 4~14초, 코드 경로 0.1초 이하 | 시연 질문을 코드 경로 위주로 준비 |

## 8. 자동화 및 외부 데이터 사용 제한

| 항목 | 현황(2026-11-18 조회) |
| --- | --- |
| 분봉 workflow 파일 | `feature/backend`에만 `.github/workflows/session-minute-actions.yml`(push·schedule 없음, `RAW_UPLOAD_ENABLED: "false"`). **origin/main에는 `ci.yml`뿐**이다. 예약 실행은 기본 브랜치 파일만 돌기 때문에 실행될 수 없다 |
| GitHub 등록 workflow | CI, Session minute artifacts, Session minute cloud preservation(67차, 파일이 어느 브랜치에도 없음) 모두 상태 표시는 `active` | 실행 기록은 분봉 3건 그대로(69차 이후 0) |
| 중단 조치의 main 반영 | 반영 안 됨. 그러나 main에는 분봉 workflow 자체가 없다 |
| 공개 원본 아티팩트 | `session-minute-2026-11-09/11/12/13/16/17` + `session-minute-unsealed-2026-11-10` = 7개, 14,291,711B, 모두 미만료, 만료 2026-12-17 | 삭제 안 함(사용자 판단) |
| 파생 CSV | `docs/session_audit_51/`(coverage CSV 2개·sha256 2개·metadata.json), `docs/session_minute/2026-11-16.sha256` | `feature/backend`에만 있다(main 0개). 51차 세션 감사용 집계(종목·날짜별 봉 수·분봉 거래량 합계) |
| 서비스 미사용 분봉 코드 | `app/session_ohlcv.py`, `app/session_bars.py`, `app/price_basis.py`, `app/models/stock_price_session.py`(서로만 참조), `scripts/fetch_/preserve_/load_session_minute.py`, `scripts/run_cloud_session_preservation.py`, `scripts/run_actions_minute_preservation.py`, `tests_cloud/`, `requirements-cloud.txt` | API·답변 경로에서 import 없음 |
| 그 밖의 비공식 원천(참고) | 일봉 FDR = 네이버 일봉, 실시간 `polling.finance.naver.com`, 지수 `m.stock.naver.com` | 69차 보고서 5절과 같은 이용 조건 질문이 남아 있다. 법률 판단은 하지 않았다 |

## 9. 브랜치 병합 계획(제안만, 실행하지 않음)

사실 관계

- 공통 조상은 `4eb9763`(41차)이다.
- `feature/backend`에만 있는 커밋 45개(42~69차): 백엔드·문서·분봉·CI
- main에만 있는 커밋 72개: 프런트 PR, `scripts/secret_scan.py` 오탐 허용 1건(`ff626dd`), feature/backend를 frontend로 받은 병합들
- `git merge-tree --write-tree origin/main origin/feature/backend`: **충돌 없음**
- 69차 커밋을 main에 단독으로 올리면 충돌한다
  - `68c89eb`: 분봉 파일 3개가 main에 없다
  - `5af3e60`: `answer_cause.py`가 main에 없고 `agent.py`도 충돌한다
  - `a614f8c`: 68차 문서가 main에 없다
  - 69-A는 68차(`3eee803`)와 그 이전 백엔드 변경에 의존한다.
- 69-A의 프런트 영향은 없다. 응답 키가 그대로이고, 새로 생긴 `meta.cause_cutoff`·`called_via: cause_only`는 프런트가 쓰지 않는다(`schemas.ts`에 없음).

권장 순서(사용자 승인 후)

1. origin/main에서 병합 전용 브랜치를 만든다(예: `merge/backend-69`).
2. `git merge --no-ff origin/feature/backend`
3. 같은 브랜치에서 분봉 자동화를 main에 넣지 않는다.
   - `.github/workflows/session-minute-actions.yml`을 지운다.
   - 파생 CSV(`docs/session_audit_51/`, `docs/session_minute/`)는 사용자 결정에 따라 지우거나 둔다.
   - 분봉 코드(`scripts/*session_minute*`, `app/session_*.py` 등)는 서비스에 영향이 없어 남겨도 되지만, 정리 여부를 함께 정한다.
4. CI(backend-unit·integration·eval-offline·secret-scan·frontend)를 확인한다.
5. 프런트 담당이 PR을 검토한다. 프런트 변경이 없으므로 화면 회귀 확인 정도면 된다.
6. 병합한 뒤 main 기준으로 백엔드를 재시작한다(승인).

피할 것

- `feature/backend`를 분봉 workflow째 main에 넣지 않는다. main에 들어가면 예약 추가 한 줄로 공개 업로드가 다시 열린다.
- 커밋을 골라 옮기는 방식은 쓰지 않는다. 의존성 때문에 실패한다.

## 10. 최종 작업 목록과 남은 순서

| 항목 | 현재 상태 | 발견한 문제 | 필요한 조치 | 담당 | 우선순위 |
| --- | --- | --- | --- | --- | --- |
| 프런트 mock 기본값 | `index.ts:115` env 없으면 mock | 시연 때 가짜 데이터 위험 | 시연 폴더 `.env.local`에 `VITE_USE_MOCK=false`, 실행 직후 실제 API 호출 확인 | 프런트 | P0(조건부) |
| 시연용 프런트 폴더 | 로컬 worktree 11-10 | 최신 화면 누락 | origin/main 최신으로 띄우기(worktree 정리는 담당자 판단) | 프런트 | P0(조건부) |
| 백엔드 재시작 | 69-A 전 코드 실행 중 | 원인 단독 질문이 수정 전 동작 | 사용자 승인 후 재시작(8000 유지) | 사용자 | P0(조건부) |
| 기간 원인 질문 | LLM 경로(+미래 기사 필터) | 전제 미확인, 원인 특정 불가 안내 없음, 지수 수치를 종목에 붙임(5.2의 2b·10) | 69차 원인 코드 경로를 기간 창으로 확장(71차) | 백엔드 | P1 |
| 데이터 갱신 | 시세·지수 11-12, 환율 11-02, 실적 10-30, ETF 구성 38/50·10-30 | 모든 답에 지연 경고 | 시연 직전 `refresh_data` 정기 단계(승인). ETF 12개 구성 수집 여부 결정 | 사용자 승인 → 백엔드 | P1 |
| main 병합 | main 백엔드 41차 | 42~69차 미반영 | 9절 순서(분봉 workflow 제외) | 사용자 승인 → 백엔드 | P1 |
| 주가 흐름 답 지연 경고 | LLM 경로 | 날짜만 있고 지연 경고 없음(5.2-1) | 지연 상태일 때 `answer_basis`/`_with_freshness`가 경고를 붙이게 | 백엔드 | P2 |
| 공시 목록 답 | LLM 경로 | 빈 항목 "5.", 군말(5.2-7) | 공시 목록 질문을 코드 답으로(뉴스·ETF 구성처럼) | 백엔드 | P2 |
| ETF 특징 질문 | LLM 경로 | 특징 미답변(5.2-11) | ETF 구성·추종 정보가 있으면 덧붙이기 | 백엔드 | P2 |
| 주어-수치 결합 검증 | `answer_guard`는 숫자가 결과에 있으면 통과 | 2b 오류를 못 잡음 | 시장 비교 문장의 주어 검증 규칙 검토 | 백엔드 | P2 |
| API.md 요약표 | 2줄 누락 | 문서 불일치 | 요약표에 2줄 추가 | 백엔드 | P2 |
| `market_session`·`close_status` | 60차 제안, 미구현 | 선택 필드 | 시연 필요성 낮음. 하면 프런트와 함께 | 백엔드+프런트 | P2 |
| 브라우저 QA | 미확인 | 콘솔·모바일·키보드 | 시연 화면 순서대로 수동 점검(4개 언어) | 프런트 | P2 |
| 소셜 로그인 UI | 백엔드만 있음 | 실제 키 필요 | 시연 범위에서 빼거나 키 확보 뒤 | 프런트+사용자 | P3 |
| 백테스트 UI | 백엔드만 있음 | 화면 없음 | 포트폴리오 기능으로 필요하면 프런트 | 프런트 | P3 |
| 분봉 자동화 | 중단 | 원천 허가 없음 | 유지 | 사용자 | P3 |
| 공개 아티팩트 7개 | 미삭제, 12-17 만료 | 공개 상태 | 삭제 여부 결정 | 사용자 | P3 |
| 파생 CSV | feature/backend에만 | 원천 파생 집계 | main 병합 때 포함 여부 결정 | 사용자 | P3 |
| 네이버 일봉·실시간·지수 원천 | 사용 중 | 이용 조건 미확인 | 공식 원천 전환 검토(서비스 공개 전) | 사용자 | P3 |

남은 순서

1. (71차, 백엔드) 기간 원인 질문 코드 경로
2. (사용자 승인) main 병합 브랜치 + CI
3. (사용자 승인) 데이터 갱신
4. (사용자 승인) 백엔드 재시작
5. (프런트) 시연 환경 점검·브라우저 QA
6. P2 항목
7. 시연 리허설

## 11. 다음 차수 작업 지시서(71차)

> # 71차: 기간을 붙인 원인 질문을 원인 코드 경로로 처리
>
> ## 목표
> "카카오 지난 한 달 동안 왜 떨어졌어?", "셀트리온 지난주 급등한 이유가 뭐야?", "삼성전자 이번 주 왜 올랐어?", "현대차 최근 20거래일 동안 왜 빠졌어?"처럼 기간을 붙인 원인 질문을 69차 원인 코드 경로(`app/answer_cause.py`)로 답한다. 70차 실검증에서 이 질문들은 LLM 경로로 가서 (1) 질문의 전제('급등')를 실제 등락(-0.71%)과 대조하지 않았고 (2) 근거 없는 인과("시장 흐름과도 관련")를 썼고 (3) 코스피 수치를 종목 주어에 붙였다(docs/FINAL_INTEGRATION_AUDIT_70.md 5.2).
>
> ## 환경 보호(68~70차와 같음)
> 백엔드 8000·프런트 5173 포트 변경 금지, 실행 중 백엔드·Ollama 재시작 금지, DB 쓰기·마이그레이션 금지, 프런트 파일 수정 금지, `.env` 열기·출력 금지, main 병합 금지, 분봉 자동화 잠금 유지. 실제 DB 확인은 읽기 전용.
>
> ## 구현
> 1. `answer_cause.is_cause_only_question`의 허용 기간을 넓힌다: `period_parser.parse_period` 결과가 있으면 그 기간(시작~끝)을 원인 창으로 쓴다(지난주, 이번 주, N일·N주·한 달·N개월, N거래일). 3개월을 넘는 기간은 기존 LLM 경로에 둔다(원인 단락이 길어진다). 사건 뒤 반응("발표 후")·두 종목·시장 질문 제외는 69차와 같다.
> 2. `answer_cause.movement`/`section`에 창 길이를 인자로 받게 한다(지금은 `CAUSE_DAYS = 5` 고정). 창 시작은 기간 시작일 직전 거래일 종가, 끝은 기준일(시세가 기간 끝보다 늦게 끝나면 기간 끝 거래일로 자르기 - 69차 `rows_until`과 같은 방식). 시장 대비는 같은 창의 지수 등락으로 계산한다(`outlook.compute_indicators`의 relative가 창과 다르면 직접 계산).
> 3. 근거 창은 기간 전체(시작일~기준일 장 마감 전)다. 기사 수가 많으면 69차 `key_news`/`filter_titles` 규칙으로 상위 N개만, 나머지는 건수로 밝힌다. 기준 시점 규칙(`cutoff_at`, `time_state`, 당일 공시·자정 기사 제외)은 그대로다.
> 4. 전제 확인(`premise_line`)은 창 등락 기준이다. "지난주 급등" + 실제 하락이면 그렇다고 첫 줄에 밝힌다.
> 5. 질문의 기간 표현과 실제 창(거래일 수, 시작·끝 날짜)을 제목 줄에 쓴다. 시세가 기간 끝보다 이르면 "요청 기간 끝(…) 시세가 없어 …까지"라고 밝힌다.
> 6. LLM은 부르지 않는다(69차 원인 단독과 같은 코드 답). 기존 `filter_records`는 그대로 둔다(LLM 경로에 남는 원인 질문용).
>
> ## 테스트(신규, DB·Ollama 없이)
> - 지난주·이번 주·한 달·20거래일·2주 질문이 코드 경로로 간다, 3개월 초과·사건 뒤·두 종목·시장 질문은 가지 않는다
> - 창 시작·끝·거래일 수, 창 등락, 같은 창 지수 대비가 계산과 맞다(가짜 시세)
> - 전제와 반대 등락이면 바로잡는 줄이 나온다
> - 창 안 기사만 후보, 기준 시점 뒤·당일 시각 모름 제외, 근거 없으면 원인 특정 불가
> - 시세가 기간 끝보다 이르면 안내 줄
> - 69차 48개·68차 34개 회귀 유지, `test_event_reaction_agent.py` 등 기존 기대가 바뀌면 이유를 보고서에 적는다
>
> ## 검증
> - 단위 전체(CI 조건, 로컬은 DATABASE_URL `0.0.0.0:1`), 품질 평가 244/244, 오프라인 59(같은 11건) 수정 전후 비교(평가 DB `ai_investment_eval68`, OLLAMA_HOST 닫힌 포트)
> - 실데이터 읽기 전용으로 70차 5.2의 2b·10번 질문 수정 전후 답 비교(PGOPTIONS read only, 리포트 저장 없음)
> - `MAX_EXCLUDED=100` 유지, devdb 테스트 추가 금지(실DB 확인은 일회성 스크립트)
>
> ## Git
> `feature/backend`에 목적별 커밋(구현+테스트, 보고서). `scripts/secret_scan.sh` 통과 후 push, CI 성공 확인. main 병합 금지.
>
> ## 보고서
> `docs/BACKEND_71_REPORT.md`: 원인, 변경, 수정 전후 답(2b·10), 테스트·평가, 남은 LLM 경로 원인 질문 범위, 70차 작업 목록 갱신.
