# 프런트엔드 통합 공백 보고서(41차, 2026-11-10)

대상: 프런트엔드 담당(서윤)과 백엔드 담당. 조사 기준: 프런트 `origin/feature/frontend` `86a9aa8`(2026-11-10 12:50, 서윤),
백엔드 `feature/backend`(41차 커밋). 프런트 코드는 `git archive`로 내보낸 사본을 **읽기만** 했고 고치지 않았다. 행 번호는 `86a9aa8` 기준이다.

## 1. 백엔드 API 공백

| 프런트 화면 | 지금 | 41차 뒤 | 상태 |
| --- | --- | --- | --- |
| 리서치 홈 "오늘의 시장"(`src/components/MarketSummaryCard.tsx:11-18`) | 오늘 일일 브리핑의 `sections.market`을 읽는다. 로그인 + 오늘 브리핑이 있어야 보인다 | `GET /api/market/overview`(공개) | 백엔드 완료, **프런트 연결 필요** |
| 종목 상세 뉴스 목록 | 없음(사건 반응 카드의 제목·링크, 최근 60일·최대 30건) | `GET /api/stocks/{ticker}/news` | 백엔드 완료, **프런트 연결 필요** |
| 종목 상세 공시 목록 | 없음 | `GET /api/stocks/{ticker}/disclosures` | 백엔드 완료, **프런트 연결 필요** |
| 주가 차트 180·365거래일(`src/components/PriceChartCard.tsx:14-16`) | 20/60/최대(90) | `days` 1~365 + `available_count` | 백엔드 완료, **프런트 버튼·mock 필요** |
| 시장 거래대금·거래량 | 없음 | 제공 안 함(DB에 없음, `unavailable`에 명시) | 데이터 부족으로 불가 |
| 업종별 노출·업종 뉴스 | `company.sector`가 비어 있음 | 변화 없음 | 데이터 부족으로 불가 |
| 요인 분석(상승 요인 비율) | 백엔드 응답 없음(README에 명시) | 변화 없음 | 이번 범위 밖 |

이미 있어서 재사용하면 되는 API: `GET /api/status`(푸터 기준일), 일일 브리핑 5개, `GET /api/stocks/{ticker}/event-reactions`,
`GET /api/etf/{ticker}/holdings`, `GET /api/companies`, `GET /api/discovery/trending`. 세부 계약은 `docs/FRONTEND_HANDOFF_SPRINT_41.md`.

### 프런트가 이미 쓰는 API와 41차 변경의 호환성

- `price-history`: 프런트 zod(`src/api/schemas.ts:451-459`)는 `z.object`(엄격 모드 아님)라 새 필드 3개가 있어도 통과한다. 기본 `days=90`과
  기존 필드·순서는 그대로다. **깨지는 호출 없음.**
- 그 밖의 기존 API는 41차에서 바꾸지 않았다(자동 생성 타입 `docs/frontend-api-types.ts`의 기존 줄은 그대로이고 추가만 있다).
- 기존 스키마 이름과 겹치지 않도록 새 모델 이름을 `StockNewsItem`·`StockDisclosureItem`·`MarketSource`·`MarketFreshness`로 정했다.
  일일 브리핑의 `NewsItem`·`DisclosureItem` 타입 이름은 바뀌지 않는다.

## 2. 프런트 다국어 미완료(읽기 전용 점검)

구조: 라벨은 `src/i18n/{ko-KR,en-US,ja-JP,zh-CN}.json`(백엔드 `docs/i18n/*.json` 복사본, 4개 언어 키 일치)에서 먼저 찾고, 없으면
`src/i18n/extra.ts`(**영어만** 있음, `extra.ts:3-6`)를 거쳐 코드의 한국어 fallback으로 간다(`src/i18n/index.ts:45-60`).
그래서 일본어·중국어 화면은 아래 41개 키가 **영어**로, 6개 키가 **한국어**로 보인다. 화면 문장(답변·브리핑·성과 등)은 API가 그 언어로 주므로 해당 없음.

집계(스크립트로 `t('키')` 호출 149개를 전부 확인): 일본어·중국어에서 영어로 보이는 키 41개, 모든 비한국어에서 한국어로 보이는 키 6개.
`extra.ts`의 영어 56개 중 14개는 코드에서 쓰지 않고(`nav.market`, `common.back`, `common.next`, `common.prev`, `common.dash`,
`common.original_korean`, `common.error_title`, `performance.benchmark_label`, `performance.before_inception`, `exposure.title`,
`briefing.title`, `briefing.checklist`, `briefing.created_note`, `settings.language_saved_note`), `history.title`은 기본 JSON에 이미 있어
`extra.ts`와 겹친다(JSON이 먼저라 `extra.ts` 쪽은 쓰이지 않는다. 지워도 된다).

### 2-1. 일본어·중국어 번역이 없는 키(`extra.ts`에 `ja-JP`·`zh-CN` 객체를 추가하면 된다)

| 키 | 화면 위치(처음 쓰는 곳) | 영어(extra.ts) |
| --- | --- | --- |
| `briefing.no_use_llm` | `src/pages/BriefingPage.tsx:90` | Quick (no AI summary) |
| `briefing.regenerate_confirm` | `src/pages/BriefingPage.tsx:97` | Create a new version of today's briefing? |
| `briefing.tab_history` | `src/pages/BriefingPage.tsx:232` | Past briefings |
| `briefing.tab_today` | `src/pages/BriefingPage.tsx:229` | Today |
| `common.calculation_unavailable` | `src/components/ResearchAnswerBody.tsx:79` | Not available |
| `common.data_as_of` | `src/pages/portfolio/ExposureSection.tsx:47` | Data as of |
| `common.delayed_badge` | `src/components/briefing/BriefingView.tsx:90` 외 1곳 | Delayed |
| `common.stale_title` | `src/pages/portfolio/ExposureSection.tsx:36` 외 1곳 | Data may be out of date |
| `exposure.bar_title` | `src/components/LookThroughPanels.tsx:43` | Share of total assets |
| `exposure.cash` | `src/components/LookThroughPanels.tsx:37` 외 1곳 | Cash |
| `exposure.companies` | `src/components/LookThroughPanels.tsx:35` | Companies |
| `exposure.direct_top1` | `src/components/LookThroughPanels.tsx:77` | Largest direct holding |
| `exposure.etf_cards` | `src/components/LookThroughPanels.tsx:288` | ETF breakdown |
| `exposure.flags` | `src/components/LookThroughPanels.tsx:259` | Concentration alerts |
| `exposure.hide_via` | `src/components/LookThroughPanels.tsx:111` | Hide ETFs |
| `exposure.look_through_top1` | `src/components/LookThroughPanels.tsx:81` | Largest look-through exposure |
| `exposure.no_companies` | `src/components/LookThroughPanels.tsx:276` | No ETF could be broken down by company. Only unidentified exposure is shown. |
| `exposure.overlaps` | `src/components/LookThroughPanels.tsx:281` | Companies held directly and through ETFs |
| `exposure.show_via` | `src/components/LookThroughPanels.tsx:111` | Show ETFs |
| `exposure.type` | `src/components/LookThroughPanels.tsx:143` | Type |
| `nav.companies` | `src/components/Layout.tsx:21` | Stocks |
| `nav.login` | `src/components/Layout.tsx:68` | Log in |
| `nav.logout` | `src/components/Layout.tsx:63` | Log out |
| `nav.portfolio` | `src/components/Layout.tsx:22` | Portfolio |
| `performance.chart_title` | `src/pages/portfolio/PerformanceSection.tsx:113` 외 1곳 | Portfolio vs. index (start = 100) |
| `performance.empty_cta` | `src/pages/portfolio/ExposureSection.tsx:57` 외 1곳 | Go to paper trading |
| `performance.metrics_title` | `src/pages/portfolio/PerformanceSection.tsx:288` | Risk and range |
| `performance.partial_note` | `src/pages/portfolio/PerformanceSection.tsx:268` | Some days are estimated. |
| `performance.period_label` | `src/pages/portfolio/PerformanceSection.tsx:234` | Period |
| `performance.reference_only` | `src/pages/portfolio/PerformanceSection.tsx:80` | For reference |
| `performance.warnings_title` | `src/pages/portfolio/PerformanceSection.tsx:295` | Notes |
| `portfolio.tabs.diagnosis` | `src/pages/PortfolioPage.tsx:12` | AI diagnosis |
| `portfolio.tabs.exposure` | `src/pages/PortfolioPage.tsx:11` | Look-through |
| `portfolio.tabs.holdings` | `src/pages/PortfolioPage.tsx:9` | Holdings & orders |
| `portfolio.tabs.performance` | `src/pages/PortfolioPage.tsx:10` | Performance |
| `portfolio.tabs.trades` | `src/pages/PortfolioPage.tsx:13` | Trades |
| `portfolio.tabs.watchlist` | `src/pages/PortfolioPage.tsx:14` | Watchlist |
| `portfolio.title` | `src/pages/PortfolioPage.tsx:19` 외 1곳 | Portfolio |
| `research.fallback_summary_notice` | `src/components/ResearchAnswerBody.tsx:68` | The translation could not be verified, so only a data summary is shown. You can view the Korean original. |
| `research.hide_original` | `src/components/ResearchAnswerBody.tsx:61` | Show translation |
| `settings.timezone_placeholder` | `src/pages/SettingsPage.tsx:84` | e.g. Asia/Seoul |

### 2-2. 어느 언어에도 라벨이 없어 한국어 fallback이 보이는 키(4개 언어 모두 필요)

| 키 | 위치 | 코드 fallback(한국어) |
| --- | --- | --- |
| `briefing.market_title` | `src/components/MarketSummaryCard.tsx:27` | 오늘의 시장 |
| `diagnosis.herfindahl` | `src/components/DiagnosisCompareView.tsx:65` | 집중도(허핀달) |
| `diagnosis.holding_count` | `src/components/DiagnosisCompareView.tsx:66` | 보유 종목 수 |
| `diagnosis.top1_weight` | `src/components/DiagnosisCompareView.tsx:63` | 상위 1종목 비중 |
| `diagnosis.top3_weight` | `src/components/DiagnosisCompareView.tsx:64` | 상위 3종목 비중 |
| `exposure.etf_stale_note` | `src/pages/portfolio/ExposureSection.tsx:42` | ETF 구성종목 기준일이 오래됐어요. 현재 구성과 다를 수 있어요. |

### 2-3. `t()`를 거치지 않는 한국어 하드코딩(주석·`fallback`·`t()` 인자 제외, 37개 파일 310줄)

| 파일 | 하드코딩 한국어 줄 수 |
| --- | --- |
| `src/pages/portfolio/HoldingsSection.tsx` | 33 |
| `src/components/EventReactionsCard.tsx` | 27 |
| `src/components/PriceChartCard.tsx` | 25 |
| `src/components/DiagnosisResultView.tsx` | 23 |
| `src/pages/CompanyPage.tsx` | 21 |
| `src/pages/ResearchPage.tsx` | 15 |
| `src/pages/portfolio/DiagnosisHistorySection.tsx` | 13 |
| `src/pages/portfolio/WatchlistSection.tsx` | 13 |
| `src/components/TrendingWidget.tsx` | 12 |
| `src/components/DiagnosisCompareView.tsx` | 10 |
| `src/pages/CompanyListPage.tsx` | 10 |
| `src/pages/portfolio/TradesSection.tsx` | 10 |
| `src/components/EtfHoldingsCard.tsx` | 9 |
| `src/api/http.ts` | 8 |
| `src/components/ResearchProgress.tsx` | 8 |
| `src/pages/SignupPage.tsx` | 8 |
| `src/pages/portfolio/DiagnosisSection.tsx` | 8 |
| `src/components/DataToolCards.tsx` | 7 |
| `src/components/Layout.tsx` | 7 |
| `src/pages/LoginPage.tsx` | 6 |
| `src/components/DiagnosisLookThrough.tsx` | 5 |
| `src/components/SourceFootnotes.tsx` | 5 |
| `src/utils/toolLabels.ts` | 5 |
| `src/components/DisclaimerFooter.tsx` | 3 |
| `src/components/Pagination.tsx` | 3 |
| `src/components/AuthBrand.tsx` | 2 |
| `src/components/RequireAuth.tsx` | 2 |
| `src/components/briefing/BriefingView.tsx` | 2 |
| `src/pages/HistoryPage.tsx` | 2 |
| `src/App.tsx` | 1 |
| `src/api/index.ts` | 1 |
| `src/auth/AuthContext.tsx` | 1 |
| `src/auth/useAuth.ts` | 1 |
| `src/components/LookThroughPanels.tsx` | 1 |
| `src/pages/BriefingPage.tsx` | 1 |
| `src/pages/portfolio/PerformanceSection.tsx` | 1 |
| `src/utils/format.ts` | 1 |

요청받은 파일의 줄 단위 목록(문구 원문은 사본에서 확인):

- `src/pages/CompanyPage.tsx`(21줄): 73·79(리서치 질문 prefill, 질문 문장이라 의도일 수 있음), 95, 102, 103, 110, 124, 136, 138, 140, 141, 151, 158, 164
  ("원" 단위), 169, 173, 178, 190, 191, 195, 198
- `src/components/EventReactionsCard.tsx`(27줄): 10-12(필터 라벨), 16-18(기간), 33-34, 51, 63, 65-66, 69, 83-85, 92, 96-100(표 머리), 122, 144-145, 160, 166
- `src/components/PriceChartCard.tsx`(25줄): 14-16(기간 버튼), 28-30, 105-106, 124, 135, 140, 143, 151-153, 156, 166, 169, 181(차트 `aria-label`), 254, 266, 268,
  270, 272, 273(수정주가 문구, 내용도 고쳐야 함: `FRONTEND_HANDOFF_SPRINT_41.md` 4절)
- `src/pages/ResearchPage.tsx`(15줄): 36-39(예시 질문), 105, 130, 132, 144, 146, 148, 163, 165, 167, 193, 205
- `src/pages/HistoryPage.tsx`(2줄): 179("삭제"), 227(삭제 확인 `confirm` 문장)
- `src/components/briefing/BriefingView.tsx`(2줄): 96("5일"), 121("평가"·"비중")
- `src/components/Layout.tsx`(7줄): 30(본문 건너뛰기), 36-37(브랜드), 41(`aria-label="주 메뉴"`), 57, 72-73(mock 표시)
- `src/i18n/LocaleProvider.tsx`, `src/i18n/index.ts`: 하드코딩 없음. `LocaleProvider.tsx:26-28`이 `<html lang>`을 바꾼다(`index.html:2`의 `lang="ko"`는 초기값).
- `src/components/LineChart.tsx`, `src/components/ui/EmptyState.tsx`, `src/components/ui/ErrorNotice.tsx`: 하드코딩 없음.

언어 전환 때 반영되지 않는 것: 위 하드코딩 전부(언어를 바꿔도 한국어 그대로), `MarketSummaryCard.tsx:27`의 제목은 브리핑이 있으면 서버 문장
(`market.title`)이라 바뀌지만 없으면 한국어 fallback, 숫자 서식 `toLocaleString('ko-KR')`(`MarketSummaryCard.tsx:54` 등)은 언어와 무관하게 한국식이다.

권장 순서: ① `extra.ts`에 `ja-JP`·`zh-CN` 41개 추가(가장 적은 수정으로 새 화면 전체가 바뀐다) ② 2-2의 6개 키를 기본 JSON에 넣도록 백엔드에 요청하거나
`extra.ts`에 4개 언어로 추가 ③ 종목 상세(`CompanyPage`·`PriceChartCard`·`EventReactionsCard`) 하드코딩 → `t()` ④ `Layout`·`ResearchPage`
⑤ 나머지 포트폴리오 섹션. `keys.test.ts`가 "기본 JSON에 없는 키는 fallback 필수"를 검사하므로 새 키에도 fallback을 같이 쓴다.

## 3. 접근성·화면 검수(읽기 전용 점검)

이미 구현돼 있어 다시 만들 필요 없음: `:focus-visible`(`App.css:1240`), `prefers-reduced-motion`(`App.css:1262`), 다크 모드(`index.css:43`),
반응형 구간 860/640px(`App.css:1054` 등), 본문 건너뛰기 링크(`Layout.tsx:30`), 차트 키보드 이동과 읽어 주기(`LineChart.tsx:64-77`,
`PriceChartCard.tsx:146-185`의 `tabIndex`·`onKeyDown`·`aria-live="polite"`), 오류 알림 `role="alert"`(`ErrorNotice.tsx`).

실제로 확인이 필요한 것(코드로는 판단 못 함):

- 차트 요소가 `role="img"`이면서 키보드로 조작된다(`LineChart.tsx:71-77`, `PriceChartCard.tsx:179-185`). 화면 읽기 프로그램은 `img`를 한 덩어리로
  읽어 화살표 조작을 알리지 않을 수 있다. NVDA·VoiceOver로 읽어 보고 필요하면 `role="application"` 또는 버튼형 조작을 검토.
- `EmptyState`(`ui/EmptyState.tsx:12`)와 "불러오는 중…" 문구에 `role="status"`·`aria-live`가 없어 로딩이 끝난 뒤 빈 상태가 읽히지 않을 수 있다.
- 차트 `aria-label`이 한국어 하드코딩이다(`PriceChartCard.tsx:181`, 2-3절).
- 새 목록 카드(뉴스·공시)를 붙이면 365거래일·50건 페이지에서 모바일 390px 폭 표시를 다시 확인.

## 4. 실제 데이터·LLM 검증 상태

| 항목 | 상태 | 내용 |
| --- | --- | --- |
| 시나리오 A 주가(20·60·90·120·180·365) | 완료(dev DB 읽기) | 삼성전자 365: `count` 365, `available_count` 733, `first_date` 2023-10-30, 결측 0. 에임드바이오 365: 222행. 366은 422 |
| 시나리오 B 뉴스·공시 | 완료(dev DB 읽기) | 삼성전자 뉴스 60건(`is_relevant=false` 제외), 공시 52건 중 정정 3건, KODEX 200 공시 `supported=false`, 날짜는 KST, URL·접수번호 확인. dev DB에 같은 종목·같은 URL 중복 0건(뉴스·공시) |
| 시장 요약 | 완료(dev DB 읽기) | 2026-11-03 코스피 5,637.63(−3.78%), 코스닥 704.79(−4.03%), 상승 67·하락 230·보합 3, 5거래일 지연 `delayed` |
| 시나리오 C 일일 브리핑 실데이터 생성 | 미실행 | 생성은 `daily_briefing`에 저장(쓰기)이고 사용자 계정이 필요하다. 승인 없이 하지 않았다 |
| 시나리오 D Ollama(4개 언어 답변) | 미실행(사전 점검만) | `127.0.0.1:13434` 응답, `llama3.1:8b`·`bge-m3` 설치 확인(로드 안 됨). 답변 생성은 하지 않았다 |
| 실제 외부 수집 | 미실행 | 데이터는 2026-11-03(시세·지수·뉴스), 2026-11-02(공시 접수일), 2026-10-30(ETF 구성) 그대로 |
| 시나리오 E 프런트 화면 연결 | 사용자·프런트 담당 대기 | 신규 API는 프런트가 아직 부르지 않는다. 기존 `price-history` zod 호환은 확인 |

환경 메모: 이 PC에서 새 프로세스의 첫 DB 연결이 약 5초 걸린다(`localhost:15432`, 두 번째부터 1ms). 신규 코드와 무관하고 실행 중 서버는 연결 풀이 있어
영향이 작다. `DATABASE_URL`의 호스트를 `127.0.0.1`로 두면 줄어들 가능성이 있다(`localhost`가 IPv6를 먼저 시도하는 경우). `.env`는 열어 보지 않았다.

## 5. 담당과 우선순위

| 우선 | 작업 | 담당 |
| --- | --- | --- |
| P1 | `MarketSummaryCard`를 `GET /api/market/overview`로(로그인 없이 보이게) | 프런트 |
| P1 | 종목 상세에 뉴스·공시 목록 카드(페이지·기간·정정 배지·ETF 처리) | 프런트 |
| P1 | `PriceChartCard.tsx:273` 수정주가 문구 고치기 | 프런트 |
| P1 | `extra.ts` 일본어·중국어 41개 | 프런트(번역 검수 필요) |
| P2 | 180·365 버튼 + `available_count`로 켜고 끄기, mock 상한 365(`mock.ts:735`) | 프런트 |
| P2 | 한국어 fallback 6개 키를 기본 라벨 리소스(`docs/i18n/*.json`)에 넣기 | 백엔드(요청 받으면) |
| P2 | 일일 브리핑 실데이터 생성·Ollama 4개 언어 답변 실측 | 백엔드(사용자 승인 뒤) |
| P3 | 하드코딩 한국어 310줄 `t()` 전환, 차트 접근성 실기기 확인 | 프런트 |
| P3 | 첫 DB 연결 5초 원인 확인(`localhost` 해석) | 백엔드·사용자(`.env`) |
