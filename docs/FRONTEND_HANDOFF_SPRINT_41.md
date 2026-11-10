# 프런트엔드 인수인계: 41차(2026-11-10) 시장 요약·종목 뉴스·공시 API와 주가 이력 365거래일

대상: 프런트엔드 담당(서윤). 기준 프런트 커밋: `origin/feature/frontend` `86a9aa8`(읽기만 했다, 프런트 코드는 고치지 않았다).
필드·오류의 전체 정의는 `backend/API.md`의 각 절(`GET /api/market/overview`, `GET /api/stocks/{ticker}/news`,
`GET /api/stocks/{ticker}/disclosures`, `GET /api/stocks/{ticker}/price-history`)에 있다. 자동 생성 타입은 `docs/frontend-api-types.ts`에
추가돼 있다(`MarketOverviewResponse`, `NewsListResponse`, `DisclosureListResponse`, 바뀐 `PriceHistoryResponse`). 기존 타입은 그대로다.

## 1. 이번 차수에 바뀐 것

| API | 전 | 후 | 프런트가 할 일 |
| --- | --- | --- | --- |
| `GET /api/market/overview` | 없음(시장은 일일 브리핑 `sections.market`에만) | **신규**, 공개 | `MarketSummaryCard`를 이 API로 바꾸면 로그인·브리핑 생성 없이 보인다 |
| `GET /api/stocks/{ticker}/news` | 없음(사건 반응 카드의 제목·링크로 대신) | **신규**, 공개 | 종목 상세에 뉴스 목록 카드 |
| `GET /api/stocks/{ticker}/disclosures` | 없음 | **신규**, 공개 | 종목 상세에 공시 목록 카드(ETF는 `supported=false`) |
| `GET /api/stocks/{ticker}/price-history` | `days` 1~90 | `days` 1~365, 응답 필드 3개 추가 | 180·365 버튼, 새 필드로 버튼 켜고 끄기. **기존 호출은 그대로 동작한다** |

모두 GET이고 인증이 필요 없다(토큰을 보내도 된다). 공개 GET 공통 레이트 리밋(IP당 분당 600)만 걸린다. LLM을 부르지 않고 DB 읽기만 한다.

## 2. `GET /api/market/overview`

파라미터 없음. 데이터가 없어도 항상 200이고 `status`로 구분한다(`ok`/`partial`/`no_data`).

- **기준일과 수집 시각은 다르다.** `as_of`·`indices[].date`는 종가 날짜, `indices[].first_collected_at`은 그 행을 처음 저장한 시각(KST)이다.
- **신선도:** `freshness.status`는 일일 브리핑 `content.freshness.status`와 같은 값(`latest`/`delayed`/`stale`/`no_data`)과 같은 기준
  (기대 KRX 거래일 대비 0 / 1~5 / 6 이상 거래일 지연)이다. 이미 있는 `freshnessTone()`(`src/api/contracts/briefing.ts`)을 그대로 쓰면 된다.
  2026-11-10 현재 dev DB는 11-03 종가라 `delayed`(5거래일)다.
- **등락이 null일 수 있다.** 직전 저장 행이 직전 거래일이 아니면(수집 공백) `change`·`change_pct`가 `null`이고 `change_unavailable_reason`이 온다.
  0%로 그리지 말고 "—"로 보여준다.
- **상승·하락 수는 수집 대상 주식(시가총액 상위 300) 기준**이다(`breadth.universe = "collected_stocks"`). 시장 전체 종목 수가 아니라고 화면에 적는다.
  비율 분모는 `up + down + flat`(등락률 결측 `change_missing` 제외).
- **수집 실패 구분:** `last_collection.index`·`.prices`의 `status`가 `failed`면 "최근 수집이 실패해 오래된 값"이라고 안내할 수 있다. `null`은 기록 없음.
- **제공하지 않는 값:** 거래대금·시장 거래량은 저장하지 않아 없다(`unavailable`). 화면에 자리를 만들지 않는다.
- 이름(`indices[].name`)은 한국어("코스피"/"코스닥")다. 다른 언어 화면은 `code`(`KS11`/`KQ11`)로 라벨을 고른다.

권장 zod(필요한 필드만, `z.object`라 나머지 필드는 무시된다):

```ts
const marketIndexSchema = z.object({
  code: z.enum(['KS11', 'KQ11']), name: z.string(), date: z.string().nullable(), close: z.number().nullable(),
  previous_date: z.string().nullable(), change: z.number().nullable(), change_pct: z.number().nullable(),
  change_unavailable_reason: z.enum(['no_previous_row', 'previous_trading_day_missing']).nullable(),
  lag_business_days: z.number().nullable(),
})
const breadthCountsSchema = z.object({
  total: z.number(), up: z.number(), down: z.number(), flat: z.number(), change_missing: z.number(), no_price: z.number(),
  registered: z.number(), up_ratio_pct: z.number().nullable(), down_ratio_pct: z.number().nullable(), flat_ratio_pct: z.number().nullable(),
})
export const marketOverviewSchema = z.object({
  as_of: z.string().nullable(), expected_date: z.string(), status: z.enum(['ok', 'partial', 'no_data']), missing: z.array(z.string()),
  freshness: z.object({ status: z.enum(['latest', 'delayed', 'stale', 'no_data']), lag_business_days: z.number().nullable(),
                        intraday_excluded: z.boolean(), dates_differ: z.boolean() }),
  indices: z.array(marketIndexSchema),
  breadth: breadthCountsSchema.extend({ date: z.string(), universe: z.literal('collected_stocks'),
                                        by_market: z.record(z.string(), breadthCountsSchema) }).nullable(),
  unavailable: z.array(z.string()), note: z.string(),
})
```

## 3. `GET /api/stocks/{ticker}/news`, `/disclosures`

쿼리: `limit`(1~50, 기본 20), `offset`(0~), `date_from`·`date_to`(`YYYY-MM-DD`, **KST 날짜, 양끝 포함**), `order`(`desc` 기본·`asc`),
공시만 `correction`(`all` 기본·`only`·`exclude`).

- **페이지:** `total`, `limit`, `offset`, `has_next`, `total_pages`. 기존 목록 API(리서치·체결)와 같은 규칙이라 `Pagination` 컴포넌트를 그대로 쓴다.
- **종목 해석은 엄격 모드**(주문과 같은 `allow_fuzzy=False`). URL의 티커로 부르면 문제없다. 오타 이름은 404 + `detail` 후보 안내 문장.
- **날짜 범위 오류:** `date_from > date_to`면 422 `{"detail": [{"loc": ["query", "date_from"], "msg": "date_from이 date_to보다 늦습니다.", ...}]}`
  (기록 검색 API와 같은 형식). 보내기 전에 막으면 된다(기록 화면에서 이미 하는 방식).
- **시각:** 뉴스 `published_at`(게시)·`collected_at`(저장)은 KST ISO 8601이다. 공시는 접수일만 있어 `disclosed_date`(`YYYY-MM-DD`)를 쓰고,
  `disclosed_at`(`T00:00:00+09:00`)은 시각으로 보여주지 않는다. 일일 브리핑 화면의 공시 날짜 처리와 같다.
- **`summary`는 네이버 검색 API가 준 기사 앞부분 발췌**다. "AI 요약"이라고 표시하지 않는다. `null`이면 숨긴다.
- **같은 기사가 여러 종목에 연결**될 수 있다(`related_tickers`). 다른 종목 링크를 달 수 있고, 없으면 무시해도 된다.
- **정정 공시:** `is_correction=true`면 "정정" 배지. 원본과 별개 공시라 둘 다 목록에 나온다.
- **ETF 공시:** 200 + `supported=false` + `message`. 공시 카드를 숨기거나 `message`를 보여준다. ETF 뉴스는 된다.
- **빈 목록과 수집 실패:** `items: []`이면 `freshness.last_collection.status`를 본다. `failed`면 "최근 수집에 실패했어요", 아니면 "이 기간에 저장된
  뉴스(공시)가 없어요". `freshness.stale`이 true면(전체 수집이 2평일 이상 멈춤) 데이터 지연 띠.
- 관련성: `is_relevant=false` 기사는 서버가 뺀다. 제목에 종목명이 없는 시황 기사가 섞일 수 있다(사건 반응 카드보다 넓다).

권장 zod:

```ts
const feedFreshnessSchema = z.object({
  last_collected_at: z.string().nullable(), collection_lag_weekdays: z.number().nullable(), stale: z.boolean(),
  company_latest_at: z.string().nullable(),
  last_collection: z.object({ step: z.string(), status: z.enum(['success', 'partial', 'failed']), ok: z.boolean(),
                              finished_at: z.string().nullable(), error_code: z.string().nullable() }).nullable(),
})
const pageSchema = { ticker: z.string(), company_name: z.string(), asset_type: z.enum(['stock', 'etf']), total: z.number(),
                     limit: z.number(), offset: z.number(), has_next: z.boolean(), total_pages: z.number(), freshness: feedFreshnessSchema }
export const stockNewsSchema = z.object({ ...pageSchema, items: z.array(z.object({
  id: z.number(), title: z.string(), summary: z.string().nullable(), source: z.string().nullable(), url: z.string().nullable(),
  published_at: z.string().nullable(), collected_at: z.string().nullable(), is_relevant: z.boolean().nullable(),
  related_tickers: z.array(z.string()) })) })
export const stockDisclosuresSchema = z.object({ ...pageSchema, supported: z.boolean(), message: z.string().nullable(),
  items: z.array(z.object({ id: z.number(), title: z.string(), disclosure_type: z.string().nullable(), rcept_no: z.string().nullable(),
    disclosed_date: z.string().nullable(), disclosed_at: z.string().nullable(), source_url: z.string().nullable(),
    is_correction: z.boolean(), collected_at: z.string().nullable() })) })
```

## 4. `GET /api/stocks/{ticker}/price-history` 확장

- `days` 상한 90 -> 365. 기본값 90, 기존 필드·순서는 그대로다. 프런트 zod(`priceHistoryResponseSchema`)는 `z.object`라 새 필드가 있어도 통과한다
  (dev DB 실제 응답으로 확인).
- 새 필드: `available_count`(이 종목 저장 행 전체 수, `days`와 무관), `first_date`(가장 이른 저장 날짜), `missing_trading_days`(응답 구간 안의
  KRX 거래일 결측 수).
- **버튼 켜고 끄기:** `available_count < 180`이면 180, `< 365`면 365 버튼을 끄거나 "N거래일만 있어요"로 안내. 2026-11-10 dev DB는 350종목 중
  342종목이 365행 이상, 가장 짧은 종목 52행.
- **이동평균:** 60일선은 60행이 쌓여야 그려진다. 90일 화면에서 60일선을 처음부터 그리려면 `days`를 화면 기간 + 59로 요청하고 앞부분을 잘라
  그리면 된다(지금은 `PriceChartCard`가 받은 행 안에서만 계산한다). 선택 사항이다.
- **문구 수정 필요:** `src/components/PriceChartCard.tsx:273` "분할·배당 보정은 하지 않은 값이에요."는 사실과 다를 수 있다. 저장 종가는 수집 소스가
  준 값이고 그 소스가 수정주가로 준 구간이 섞여 있다(예: 삼성바이오로직스 2026-10-01 1,417,007원처럼 호가 단위가 아닌 값). 권장 문구:
  "수집 소스(FinanceDataReader)가 준 일별 종가예요. 일부 구간은 수정주가일 수 있어요."
- **mock:** `src/api/mock.ts:735`가 `days > 90`을 422로 막는다. 180·365 버튼을 붙일 때 mock 상한도 365로, 응답에 새 필드 3개를 같이 넣어야 한다.

## 5. 날짜·시간대 규칙(이번 API)

| 값 | 의미 | 표시 |
| --- | --- | --- |
| `as_of`, `indices[].date`, `breadth.date`, `price_as_of`, `items[].date`(주가) | 종가 날짜(`YYYY-MM-DD`) | 그대로 날짜 |
| `expected_date` | 지금 종가가 있어야 할 KRX 거래일 | 지연 안내에 |
| `*_at`(뉴스 게시·저장, 수집 시도) | KST 오프셋이 붙은 ISO 8601 | 이미 있는 `formatDate`(Asia/Seoul) |
| `disclosed_date` | 공시 접수일 | 날짜만 |
| `date_from`·`date_to`(요청) | KST 날짜, 양끝 포함 | 사용자 시간대와 무관(공개 API라 시간대 파라미터 없음) |

## 6. mock에서 실제 API로 바꿀 때

1. `Api` 인터페이스(`src/api/index.ts`)에 `getMarketOverview()`, `getStockNews(ticker, params)`, `getStockDisclosures(ticker, params)`를 추가하고
   `http.ts`·`mock*.ts` 양쪽에 같은 모양으로 구현한다. 쿼리 키는 `queryKeys`에 티커·페이지·필터를 넣는다.
2. 실제 응답 예시는 `backend/API.md` 각 절에 있다(2026-11-10 dev DB). 계약 테스트 fixtures로 뽑아 쓰면 된다.
3. 시장 요약은 로그인과 무관해서, 로그아웃 때 캐시를 비우는 대상에서 빼도 된다(지금 브리핑 기반 카드는 사용자별 캐시다).
4. 빈 상태: 시장 `status="no_data"`, 목록 `items=[]`는 오류가 아니다(`EmptyState`). 404(종목 없음)·422(파라미터)만 `ErrorNotice`.

## 7. 백엔드에서 확인한 것과 하지 않은 것

- 확인: 단위·라우터·dev DB 통합 테스트(`tests/test_market_overview_api.py`, `tests/test_stock_feed_api.py`, `tests/test_price_history_api.py`),
  OpenAPI 계약 테스트, 자동 생성 타입 갱신, dev DB 실제 응답(위 예시). 통합 테스트는 테스트 전용 종목을 만들고 끝나면 지운다(CI 빈 DB에서도 돈다).
- 하지 않음: 실제 외부 수집(시세·뉴스·공시 새로 받기), 프런트 화면 연결·브라우저 확인, 장중 시각의 실제 응답(`intraday_excluded=true`)은 단위 테스트로만.
