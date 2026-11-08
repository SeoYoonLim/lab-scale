# 프론트엔드 인계: 개인화 일일 투자 브리핑 (2026-11-06)

받는 사람: 서윤 님(프론트엔드). 이 문서만 보고 화면을 만들 수 있게 썼습니다. 백엔드 쪽 상세 규칙은 `backend/API.md`의 "개인화 일일 투자 브리핑" 절에 있습니다.

## 1. 바로 알아야 할 것 6가지

1. **새 API 4개**이고 모두 로그인이 필요합니다(Bearer 토큰). 토큰이 없으면 401입니다.
   - `POST /api/briefings/daily/generate`: 오늘 브리핑 만들기(이미 있으면 저장본을 그대로 돌려줌)
   - `GET /api/briefings/daily/current`: 오늘 브리핑(없으면 404)
   - `GET /api/briefings/daily/history`: 지난 브리핑 목록
   - `GET /api/briefings/daily/{id}`: 브리핑 상세(남의 것·없는 id는 404)
2. **기존 `GET /api/briefing`은 그대로**입니다. 그건 요청할 때마다 다시 계산하는 "지금 화면"이고 저장하지 않습니다. 새 API는 날짜별로 저장되는 스냅샷입니다.
3. **브리핑은 불변 스냅샷**입니다. 과거 브리핑을 열어도 현재 시세로 다시 계산하지 않습니다. "다시 만들기"는 기존 것을 덮어쓰지 않고 `version`을 올린 새 브리핑을 만듭니다(하루 5개까지).
4. **데이터 지연 경고를 맨 위에** 보여 주세요. `content.market_data_status`가 `latest`가 아니면 `content.freshness.headline`을 눈에 띄는 경고 띠로 첫 줄에 둡니다(6절).
5. **문장은 모두 백엔드가 그 언어로 만듭니다**(`content.locale`). 화면에서 번역하지 말고 받은 문자열을 그대로 보여 주세요. 버튼·배지 이름만 `docs/i18n/{locale}.json`의 `daily_briefing` 묶음을 씁니다.
6. **JSON 키 순서에 기대지 마세요.** 저장본(PostgreSQL JSONB)은 키 순서를 보존하지 않습니다. 영역 순서는 `content.section_order`를 따르세요.

## 2. API

### 2-1. 오늘 브리핑 만들기: `POST /api/briefings/daily/generate`

요청 본문(모두 선택):

| 필드 | 기본값 | 설명 |
| --- | --- | --- |
| `regenerate` | `false` | `true`면 오늘 브리핑이 있어도 새 버전을 만든다(기존 버전 보존) |
| `locale` | 계정 설정 → `ko-KR` | `ko-KR`·`en-US`·`ja-JP`·`zh-CN`. 모르는 값은 `ko-KR` |
| `timezone` | 계정 설정 → `Asia/Seoul` | IANA 이름. "오늘"을 정하는 기준. 모르는 이름은 422 |
| `use_llm` | `true` | `false`면 AI 요약 없이 코드 요약만(빠름) |

응답:

| 상황 | 코드 | 본문 |
| --- | --- | --- |
| 오늘 처음 만듦 | 201 | 브리핑(`created: true`, `version: 1`) |
| 오늘 것이 이미 있음(`regenerate: false`) | 200 | 저장된 마지막 버전 그대로(`created: false`). 다시 계산하지 않는다 |
| 다시 만들기(`regenerate: true`) | 201 | 새 버전(`version` + 1) |
| 하루 5개를 넘김 | 409 | `{"detail": "오늘은 브리핑을 5번까지 만들 수 있습니다. ..."}` |
| 1분에 3번 넘게 요청 | 429 | `Retry-After` 헤더 |
| 로그인 안 됨 | 401 | |

생성은 dev 환경에서 4~15초 걸렸고 대부분 AI 요약 시간입니다. `use_llm: false`면 1초 안팎입니다. Ollama가 꺼져 있어도 실패하지 않고 코드 요약으로 만듭니다(`summary_source: "code"`).

```http
POST /api/briefings/daily/generate
Authorization: Bearer <token>
Content-Type: application/json

{"regenerate": false}
```

### 2-2. 오늘 브리핑: `GET /api/briefings/daily/current?timezone=Asia/Seoul`

오늘(시간대 기준) 브리핑의 마지막 버전입니다. 없으면 **404**(`{"detail": "오늘의 브리핑이 아직 없습니다. ..."}`)이고 자동으로 만들지 않습니다. 404일 때 "오늘 브리핑 만들기" 버튼을 보여 주세요.

### 2-3. 지난 브리핑 목록: `GET /api/briefings/daily/history`

| 파라미터 | 기본값 | 설명 |
| --- | --- | --- |
| `date_from`, `date_to` | 없음 | 브리핑 날짜(`local_date`) 범위, YYYY-MM-DD, 양 끝 포함. 거꾸로면 422 |
| `timezone` | 계정 설정 | `local_time`·`date_group` 표시용 |
| `latest_only` | `true` | 날짜마다 마지막 버전만. `false`면 모든 버전 |
| `page`, `size` | 1, 20 | size 최대 100 |

응답은 `{items, page, size, total, has_next, total_pages, timezone, latest_only, date_from, date_to}`입니다. 정렬은 날짜 최신순, 같은 날은 버전 최신순입니다.

`items[]`(본문 없이 목록용):

```json
{
  "id": 47, "version": 3, "versions_on_date": 3,
  "local_date": "2026-11-06", "local_time": "14:23", "date_group": "today",
  "generated_at": "2026-11-06T14:23:31+09:00", "locale": "ja-JP",
  "data_as_of": "2026-11-03", "market_data_status": "delayed", "summary_source": "llm",
  "title": "2026-11-06 デイリー投資ブリーフィング",
  "headline": "株価が2営業日遅れています。…",
  "summary": "2026-11-03株価が2営業日遅れています。…",
  "section_status": {"market": "ok", "holdings": "ok", "watchlist": "ok", "risk": "ok", "checklist": "ok"},
  "ticker_count": 4, "detail_path": "/api/briefings/daily/47"
}
```

`date_group`(today / yesterday / last_7_days / last_30_days / older)은 리서치·진단 기록과 같은 규칙이라 같은 라벨(`date_group.*`)을 쓰면 됩니다. `local_date`는 **브리핑을 만든 날짜**(저장값)입니다.

### 2-4. 상세: `GET /api/briefings/daily/{id}`

저장본 그대로입니다. 남의 브리핑이나 없는 id는 404입니다. 응답 형식은 generate와 같고 `created`만 null입니다.

## 3. 응답 구조(브리핑 한 건)

| 필드 | 설명 |
| --- | --- |
| `id`, `version` | 같은 날 다시 만들면 version이 2, 3, … |
| `local_date`, `timezone` | 만든 날짜와 그 기준 시간대 |
| `locale` | 만든 언어. 나중에 계정 언어를 바꿔도 **바뀌지 않는다** |
| `generated_at` | 만든 시각(ISO, +09:00) |
| `data_as_of` | 브리핑에 쓴 시세 중 가장 최근 날짜. 시세가 하나도 없으면 null |
| `market_data_status` | `latest` / `delayed` / `stale` / `no_data` (6절) |
| `summary_source` | `llm`(AI 요약, 숫자 검증 통과) / `code`(코드 요약) |
| `section_status` | 영역별 `ok` / `partial` / `no_data` / `empty` |
| `tickers[]` | 브리핑에 쓴 종목 `{ticker, name, scope: holding\|watchlist}`(보유와 겹치는 관심 종목은 holding으로 한 번만) |
| `sources[]` | 근거 `{kind: news\|disclosure\|indirect_news\|market_news, ticker, title, url, date}` |
| `content` | 화면에 그릴 브리핑(아래) |
| `original_content` | `locale`이 ko-KR이 아닐 때만: 같은 계산 결과의 한국어 브리핑. "한국어 원문 보기"용 |
| `date_group` | 표시용 날짜 그룹 |

`content`:

| 필드 | 설명 |
| --- | --- |
| `title` | "2026-11-06 일일 투자 브리핑" |
| `freshness` | 데이터 신선도(6절). `headline`은 맨 위 한 문장, `notices[]`는 추가 안내 |
| `summary` | `{text, source, note}`. note는 "AI가 위 계산 결과만으로 쓴 요약입니다(숫자 검증 통과)." 같은 출처 안내 |
| `section_order` | `["market", "holdings", "watchlist", "risk", "checklist"]` |
| `sections` | 5개 영역(4절) |
| `highlights[]` | 기존 `/api/briefing`과 같은 flag 상위 5개(종류는 `briefing_flag.*`) |
| `not_advice` | "확인된 데이터를 정리한 것이며 매수·매도 권유나 가격 예측이 아닙니다." |
| `disclaimer` | 면책 문구(그 언어) |

## 4. 영역별 데이터

모든 영역에 `title`(그 언어)과 `status`가 있습니다. `status`가 `empty`면 `message`(risk는 `texts`)만 보여 주면 됩니다.

### 4-1. `market`: 오늘의 시장 요약

- `indices[]`: 코스피·코스닥·원/달러 `{code: kospi|kosdaq|usd_krw, name, date, close, change_pct, week_change_pct, lag_business_days, text}`. 데이터가 없으면 `date: null`이고 `text`는 "코스닥: 데이터 없음"입니다. `text`를 그대로 한 줄로 써도 되고, 숫자 필드로 카드를 그려도 됩니다.
- `news[]`: 제목에 시장 키워드(코스피·코스닥·증시·환율 등)가 있는 최근 3일 기사, 최대 3개 `{title, source, url, published_at, similar_count}`. 지수 이름이 들어간 기사가 먼저 옵니다. **제목만 보이고 해석하지 않으니** 위에 `news_label`을 붙여 주세요. 없으면 `news_empty_text`입니다.
- 상승·하락을 단정하는 문장은 없습니다.

### 4-2. `holdings`: 내 보유 종목 주요 변화

`items[]`(비중 큰 순):

| 필드 | 설명 |
| --- | --- |
| `ticker`, `name`, `asset_type` | 종목코드, 공식 명칭(번역 안 함), stock\|etf |
| `latest_date`, `lag_business_days` | 이 종목의 마지막 시세 날짜와 지연 영업일 수 |
| `latest_close`, `day_change_pct`, `week_change_pct`, `return_20d_pct` | 종가, 1일·5거래일·20거래일 등락률(%). 모자라면 null |
| `volume`, `avg_volume_20d`, `volume_ratio` | 거래량과 직전 20거래일 평균 대비 배수 |
| `quantity`, `avg_price`, `eval_amount`, `weight_pct`, `profit_loss`, `profit_loss_pct`, `day_profit_loss` | 보유 수량, 평균 매입가, 평가액(DB 종가 × 수량), 총자산 대비 비중, 손익 |
| `price_text`, `position_text` | 위 숫자를 한 줄로 쓴 문장(그 언어) |
| `risk_factors[]` | 이 종목의 주요 위험 요인 문장(큰 등락, 거래량 급증, 큰 손실, 레버리지·인버스 안내, 시세가 다른 종목보다 오래됨) |
| `news_label`, `news[]`, `news_empty_text` | **직접 관련 뉴스**: 제목에 종목명(통칭 포함)이 있는 최근 7일 기사, 최대 3묶음. `similar_count`가 2 이상이면 "비슷한 기사 n건" 배지 |
| `indirect_news` | **간접 관련 뉴스** `{kind: sector\|index, label, topic, items[]}` 또는 null. 직접 뉴스와 **다른 상자**로 보여 주세요 |
| `disclosures[]`, `disclosure_count`, `disclosure_empty_text` | 최근 7일 공시(최대 3건). ETF는 공시를 수집하지 않는다는 안내가 empty text로 온다 |
| `flags[]` | 기존 브리핑 flag(종류·값·기준) |

보유가 없으면 `status: "empty"`와 `message`(계좌 없음 또는 보유 없음 안내)만 옵니다.

### 4-3. `watchlist`: 관심 종목 업데이트

`items[]`는 4-2와 같은 시세·뉴스·공시 필드에 아래가 더 있고, 주목할 변화가 있는 종목이 먼저 옵니다.

| 필드 | 설명 |
| --- | --- |
| `notable` | 규칙 기준 변화(큰 등락·거래량 급증·새 공시)가 있으면 true. "주목할 변화" 배지 |
| `changes[]` | 그 변화 문장들 |
| `status_text` | notable이 false면 "특별한 업데이트가 없습니다." |
| `since_research` | 내가 이 종목을 리서치한 적이 있으면 `{last_research_at, news_after, disclosures_after, text}`, 없으면 null |

그 밖에 `notable_count`, `dedup_text`(보유와 겹쳐 뺀 관심 종목 안내, 없으면 null), 비었을 때의 `message`가 있습니다.

### 4-4. `risk`: 포트폴리오 위험 점검

| 필드 | 설명 |
| --- | --- |
| `metrics` | `{total_asset, cash_weight_pct, holding_count, priced_holding_count, top1_weight_pct, top3_weight_pct, herfindahl_index, market_weights_pct}`(포트폴리오 진단과 같은 정의) |
| `texts[]` | 지표 요약 줄, 시세 없는 종목 안내, 위험 없음 안내 |
| `flags[]` | 진단 규칙 플래그 `{code, text, value, threshold, ticker, company_name}`. code는 `diagnosis_flag.*`와 같다 |
| `sector` | `{status: "unavailable", text}`. 업종 분류 데이터가 없어 업종 집중도는 계산하지 않는다 |
| `etf_exposure` | `{status: computed\|not_computed\|no_etf, text, flags[], top_companies[], undecomposed[]}` |
| `cautions[]` | 시세 지연 시 기준일 안내, 보유 종목의 큰 등락 문장 |

**ETF 실질 노출은 `status`가 `computed`일 때만** 기업별 노출(`top_companies`, `flags`)을 보여 주세요. `not_computed`면 `text`(계산하지 않은 이유)만 보여 주고 노출 숫자를 그리지 마세요.

### 4-5. `checklist`: 오늘 확인할 사항

`items[]`는 최대 3개 `{kind, ticker, text, url?}`이고 우선순위는 다음 순서입니다.
1. 데이터 지연(`data_delay`) 또는 시세 없음(`data_missing`)
2. 보유 종목의 실적 관련 공시(`earnings_disclosure`)와 그 밖의 공시(`holding_disclosure`)
3. 위험 플래그(`risk_concentration_high`, `risk_big_loss`, `risk_leveraged_inverse_etf`)
4. 관심 종목 공시(`watchlist_disclosure`)

없으면 `status: "empty"`와 `message`("오늘 따로 확인할 항목이 없습니다.")가 옵니다. `url`이 있으면 공시 원문 링크로 쓰세요.

## 5. 로딩·오류·빈 데이터

| 상태 | 처리 |
| --- | --- |
| `current` 404 | "오늘 브리핑이 아직 없습니다" + 만들기 버튼(`daily_briefing.not_generated`, `generate`) |
| generate 진행 중 | 버튼 잠금 + "브리핑을 만드는 중입니다…"(최대 약 20초). 두 번 누르지 않게 |
| generate 200(`created: false`) | 이미 있는 오늘 브리핑을 그대로 보여 준다(오류 아님) |
| 409 | "오늘은 더 만들 수 없습니다"(`version_limit`) + 지난 버전 목록 |
| 429 | `Retry-After`초 뒤 다시 시도하라는 안내(`errors.429`) |
| 401 | 기존 규칙대로 로그인 화면 |
| 422 | 잘못된 시간대·날짜 범위(설정 화면 값 확인) |
| 영역 `status: empty` | 그 영역의 `message`만 회색 안내로 |
| 영역 `status: partial`·`no_data` | 영역 제목 옆에 "일부 데이터 부족"·"데이터 부족" 배지(`daily_briefing.section_status.*`), 값이 null인 칸은 "계산 불가" |
| 숫자 null | 0으로 그리지 말고 "—" 또는 "계산 불가" |

## 6. 데이터 지연 경고 규칙

판정은 백엔드가 전망 기능과 같은 규칙으로 합니다. 기대 거래일은 오늘 이하의 가장 최근 거래일이고, 장 마감(15:30 KST) 전이면 직전 거래일입니다. 2026-11-08(36차)부터 KRX 거래일 달력(공휴일·대체공휴일·연말 휴장)을 쓰고, 그 전에는 평일 기준이었습니다. 달력에 없는 이유(미수집·수집 실패 등)는 수집 기록으로 추정합니다.

| `market_data_status` | 뜻 | 화면 |
| --- | --- | --- |
| `latest` | 마지막 시세 = 기대 거래일 | 경고 없음. `freshness.headline`을 작은 안내로 |
| `delayed` | 1~5영업일 늦음 | **노란 경고 띠를 맨 위에** `freshness.headline` |
| `stale` | 5영업일 초과 | **빨간 경고 띠**. 숫자 영역을 흐리게 하는 것도 좋습니다 |
| `no_data` | 시세가 하나도 없음 | 빨간 경고 띠 + 가격 칸은 "계산 불가" |

- `freshness.warning`이 true면(`latest`가 아닌 모든 경우) 경고 띠를 보여 주세요.
- `freshness.notices[]`는 지수·환율 기준일이 다를 때, 마지막 수집에 실패 단계가 있을 때, 장중일 때의 안내입니다. 경고 띠 아래 작은 글씨로 보여 주세요.
- `freshness.price_status`는 코드값(`미수집`, `수집 실패`, `휴장일 추정`, `종목만 누락`)입니다. 화면 문구는 이미 headline에 그 언어로 들어 있으니 따로 번역하지 마세요.
- `freshness.price_status_detail`(2026-11-08 문서 보완)은 그 이유의 세부 기록 문장(한국어, 예: "장 마감 뒤 시세 수집 기록 없음, 마지막 시세 수집 2026-11-03 19:45")이거나 `null`입니다. 운영 확인용이라 화면에 꼭 보여 줄 필요는 없습니다.
- 2026-11-08(36차)부터 지연 영업일은 KRX 거래일 달력 기준입니다(공휴일 다음 날의 정상 데이터는 `latest`).
- `freshness.sources.{kospi,kosdaq,usd_krw}`는 각 기준일과 지연 영업일입니다. `news_collected_at`·`disclosure_collected_at`은 마지막 뉴스·공시 수집 시각, `last_refresh`는 마지막 데이터 갱신 실행 `{finished_at, ok, failed_steps}`입니다.
- 종목별 `lag_business_days`가 0보다 크면 그 종목 카드에 작은 "지연" 배지를 달아 주세요.

## 7. 날짜별 이력 화면

1. 목록은 `GET /history?page=1&size=20`(날짜마다 마지막 버전)입니다. `date_group`으로 묶어 "오늘 / 어제 / 최근 7일 …" 헤더를 붙입니다.
2. `versions_on_date`가 2 이상이면 "이 날짜에 n개 버전" 링크를 답니다. 누르면 `GET /history?latest_only=false&date_from=그날&date_to=그날`입니다.
3. 항목을 누르면 `detail_path`(= `/api/briefings/daily/{id}`)로 상세를 엽니다.
4. 과거 브리핑 상세에는 "{local_date}에 만든 브리핑이며 그 뒤 시세는 반영되지 않았습니다" 같은 안내를 붙이는 것을 권합니다. 백엔드는 다시 계산하지 않습니다.
5. 날짜 범위 필터는 `date_from`·`date_to`(브리핑 날짜 기준)입니다.

## 8. 다국어 표시 규칙

- 언어는 요청의 `locale` → 계정 설정(`PUT /api/auth/preferences`의 `preferred_locale`) → ko-KR 순서로 정해집니다. 보통은 locale을 보내지 않고 계정 설정을 따르면 됩니다.
- 브리핑의 문장(제목·경고·요약·영역 문장·확인할 사항)은 **만든 당시 언어로 저장**됩니다. 계정 언어를 바꿔도 과거 브리핑은 그 언어 그대로이고 자동 번역하지 않습니다. 새 언어로 보려면 `regenerate: true`와 `locale`로 다시 만들면 됩니다.
- 숫자, 날짜, 종목코드, 회사 공식 명칭(예: "삼성전자"), 뉴스·공시 제목은 어느 언어에서도 원문 그대로입니다. 화면에서 바꾸지 마세요.
- ko-KR이 아닌 브리핑에는 `original_content`(한국어)가 함께 옵니다. "한국어 원문 보기"(`daily_briefing.original_toggle`) 토글로 쓰세요.
- 버튼·배지·상태 이름은 `docs/i18n/{locale}.json`의 `daily_briefing` 묶음입니다(`market_data_status.*`, `section_status.*`, `summary_llm`, `summary_code`, `notable`, `direct_news`, `indirect_news`, `similar_grouped`, `version` 등).
- HTTP 오류 `detail`은 다른 API와 같이 한국어입니다. 화면에는 상태 코드에 맞는 `errors.*` 리소스를 쓰세요.

## 9. 추천 화면 구성

1. **상단**: 제목(`content.title`), 생성 시각, "데이터 기준일 {data_as_of}", [다시 만들기]
2. **경고 띠**(`freshness.warning`일 때): `freshness.headline`, 그 아래 `notices`
3. **요약 카드**: `summary.text` + 출처 배지(AI 요약/자동 요약) + `summary.note`
4. **오늘 확인할 사항**(`checklist`, 최대 3개): 행동에 가장 가까운 정보라 요약 바로 아래를 권합니다
5. **오늘의 시장 요약**: 지수 3칸 카드 + 시장 기사 목록
6. **내 보유 종목**: 종목 카드(가격 줄, 보유 줄, 위험 요인, 직접 뉴스, 간접 뉴스 상자, 공시)
7. **관심 종목**: 주목할 변화가 있는 종목 카드 먼저, 나머지는 "특별한 업데이트가 없습니다" 한 줄
8. **포트폴리오 위험 점검**: 지표 줄, 플래그, 업종(계산 안 함 안내), ETF 실질 노출(computed일 때만 표), 주의 사항
9. **하단**: `not_advice`, `disclaimer`, 근거 자료(`sources`) 접기
10. **지난 브리핑** 탭(7절)

`section_order`는 영역 데이터의 기본 순서입니다. 위처럼 확인할 사항을 위로 올리는 배치는 화면에서 정해도 됩니다.

## 10. 실제 응답 예시

dev DB의 실제 시세·뉴스로 만든 응답을 줄인 것입니다(목록은 한두 개만 남겼고, 테스트 계정은 지웠습니다).
- 보유: SK하이닉스·삼성전자·KODEX 200
- 관심: NAVER·삼성전자(삼성전자는 보유와 겹쳐 보유 영역에만 나옴)
- 2026-11-06 14시에 만들었고, 시세가 11-03까지라 `delayed`입니다.
- 보유 수량과 평균 매입가는 테스트용 값입니다.

```json
{
  "id": 45,
  "version": 1,
  "local_date": "2026-11-06",
  "timezone": "Asia/Seoul",
  "locale": "ko-KR",
  "generated_at": "2026-11-06T14:23:01.587027+09:00",
  "data_as_of": "2026-11-03",
  "market_data_status": "delayed",
  "summary_source": "llm",
  "section_status": {
    "risk": "ok",
    "market": "ok",
    "holdings": "ok",
    "checklist": "ok",
    "watchlist": "ok"
  },
  "date_group": "today",
  "created": true,
  "tickers": [
    {
      "name": "SK하이닉스",
      "scope": "holding",
      "ticker": "000660"
    },
    {
      "name": "삼성전자",
      "scope": "holding",
      "ticker": "005930"
    },
    {
      "name": "KODEX 200",
      "scope": "holding",
      "ticker": "069500"
    },
    {
      "name": "NAVER",
      "scope": "watchlist",
      "ticker": "035420"
    }
  ],
  "sources": [
    {
      "url": "https://n.news.naver.com/mnews/article/056/0012286460?sid=101",
      "date": "2026-11-03T19:18:00+09:00",
      "kind": "news",
      "title": "코스피, 3.59% 하락…삼전 4.2%·하이닉스 5.4%↓",
      "ticker": "000660"
    },
    {
      "url": "http://www.4th.kr/news/articleView.html?idxno=2120972",
      "date": "2026-11-02T18:32:00+09:00",
      "kind": "news",
      "title": "SK하닉 AI해커톤 망친 크래프톤, \"10만원 받고 다시 봐\"",
      "ticker": "000660"
    }
  ],
  "original_content": null,
  "content": {
    "title": "2026-11-06 일일 투자 브리핑",
    "locale": "ko-KR",
    "local_date": "2026-11-06",
    "timezone": "Asia/Seoul",
    "generated_at": "2026-11-06T14:23:01.587027+09:00",
    "data_as_of": "2026-11-03",
    "market_data_status": "delayed",
    "section_order": [
      "market",
      "holdings",
      "watchlist",
      "risk",
      "checklist"
    ],
    "section_status": {
      "risk": "ok",
      "market": "ok",
      "holdings": "ok",
      "checklist": "ok",
      "watchlist": "ok"
    },
    "not_advice": "확인된 데이터를 정리한 것이며 매수·매도 권유나 가격 예측이 아닙니다.",
    "freshness": {
      "status": "delayed",
      "warning": true,
      "headline": "시세가 2영업일 지연됐습니다. 마지막 종가는 2026-11-03, 기대 거래일은 2026-11-05입니다(미수집). 아래 숫자는 2026-11-03 종가 기준이며 오늘의 시장 상황이 아닙니다.",
      "notices": [
        "코스피 지수 데이터는 2026-11-03 기준입니다(2영업일 지연).",
        "코스닥 지수 데이터는 2026-11-03 기준입니다(2영업일 지연).",
        "원/달러 환율 데이터는 2026-11-02 기준입니다(3영업일 지연).",
        "장 마감 전이라 오늘 시세는 쓰지 않고 직전 거래일 종가를 씁니다."
      ],
      "data_as_of": "2026-11-03",
      "expected_date": "2026-11-05",
      "lag_business_days": 2,
      "price_status": "미수집",
      "sources": {
        "kospi": {
          "date": "2026-11-03",
          "lag_business_days": 2
        },
        "kosdaq": {
          "date": "2026-11-03",
          "lag_business_days": 2
        },
        "usd_krw": {
          "date": "2026-11-02",
          "lag_business_days": 3
        }
      },
      "news_collected_at": "2026-11-03T19:54:42.431681+09:00",
      "disclosure_collected_at": "2026-11-03T19:53:51.961770+09:00",
      "last_refresh": {
        "ok": true,
        "finished_at": "2026-11-03T20:03:27.072135+09:00",
        "failed_steps": []
      },
      "intraday": true
    },
    "summary": {
      "note": "AI가 위 계산 결과만으로 쓴 요약입니다(숫자 검증 통과).",
      "text": "시세가 2영업일 지연된 2026-11-03 기준입니다. 코스피는 5,637.63, 전일 대비 -3.78%이며 5거래일 -0.01%입니다. 코스닥은 704.79, 전일 대비 -4.03%, 5거래일 -5.25%입니다. 원/달러는 1,325.52원, 전일 대비 -0.72%, 5거래일 +0.06%입니다. SK하이닉스는 2026-11-03 종가 1,414,000원, 전일 대비 -4.46%, 5거래일 +2.91%입니다. 삼성전자도 2026-11-03 종가 222,000원, 전일 대비 -3.48%, 5거래일 -1.33%입니다. KODEX 200은 2026-11-03 종가 89,325원, 전일 대비 -3.89%, 5거래일 +0.62%입니다. NAVER의 공시는 최근 7일 안에 3건 있습니다.",
      "source": "llm"
    },
    "sections": {
      "market": {
        "news": [
          {
            "url": "https://www.vegannews.co.kr/news/article.html?no=399999",
            "title": "AI 투자 수익성 우려에 코스피 3.59% 하락",
            "source": "www.vegannews.co.kr",
            "published_at": "2026-11-03T19:18:00+09:00",
            "similar_count": 3
          }
        ],
        "title": "오늘의 시장 요약",
        "status": "ok",
        "indices": [
          {
            "code": "kospi",
            "date": "2026-11-03",
            "name": "코스피",
            "text": "코스피 2026-11-03 종가 5,637.63 · 전일 대비 -3.78% · 5거래일 -0.01%",
            "close": 5637.63,
            "change_pct": -3.78,
            "week_change_pct": -0.01,
            "lag_business_days": 2
          },
          {
            "code": "kosdaq",
            "date": "2026-11-03",
            "name": "코스닥",
            "text": "코스닥 2026-11-03 종가 704.79 · 전일 대비 -4.03% · 5거래일 -5.25%",
            "close": 704.79,
            "change_pct": -4.03,
            "week_change_pct": -5.25,
            "lag_business_days": 2
          },
          {
            "code": "usd_krw",
            "date": "2026-11-02",
            "name": "원/달러",
            "text": "원/달러 2026-11-02 종가 1,325.52원 · 전일 대비 -0.72% · 5거래일 +0.06%",
            "close": 1325.52,
            "change_pct": -0.72,
            "week_change_pct": 0.06,
            "lag_business_days": 3
          }
        ],
        "news_label": "시장 관련 기사(제목에 시장 키워드가 있는 기사, 제목만 보이며 해석하지 않습니다)",
        "news_empty_text": null
      },
      "holdings": {
        "items": [
          {
            "ticker": "000660",
            "name": "SK하이닉스",
            "asset_type": "stock",
            "latest_date": "2026-11-03",
            "lag_business_days": 2,
            "latest_close": 1414000.0,
            "day_change_pct": -4.46,
            "week_change_pct": 2.91,
            "return_20d_pct": -23.24,
            "volume_ratio": 0.63,
            "quantity": 2,
            "avg_price": 1500000.0,
            "eval_amount": 2828000.0,
            "weight_pct": 28.76,
            "profit_loss_pct": -5.73,
            "price_text": "2026-11-03 종가 1,414,000원 · 전일 대비 -4.46% · 5거래일 +2.91% · 20거래일 -23.24%",
            "position_text": "보유 2주 · 평가액 2,828,000원 · 비중 28.76% · 평균 매입가 대비 -5.73%",
            "risk_factors": [
              "SK하이닉스의 2026-11-03 종가는 전 거래일 대비 -4.46% 하락했습니다."
            ],
            "notes": [],
            "news_label": "직접 관련 뉴스",
            "news": [
              {
                "url": "https://n.news.naver.com/mnews/article/056/0012286460?sid=101",
                "title": "코스피, 3.59% 하락…삼전 4.2%·하이닉스 5.4%↓",
                "source": "news.kbs.co.kr",
                "published_at": "2026-11-03T19:18:00+09:00",
                "similar_count": 1
              }
            ],
            "news_empty_text": null,
            "indirect_news": {
              "kind": "sector",
              "items": [
                {
                  "url": "http://www.kjdaily.com/article.php?aid=1793701912691119008",
                  "title": "여수시, 석유·화학 기반 ‘반도체 클러스터’ 연계 시동",
                  "source": "www.kjdaily.com",
                  "published_at": "2026-11-03T10:36:00+00:00",
                  "similar_count": 1
                }
              ],
              "label": "업종·산업 뉴스(간접)",
              "topic": "반도체"
            },
            "disclosures": [],
            "disclosure_empty_text": "최근 7일 공시가 없습니다."
          }
        ],
        "title": "내 보유 종목 주요 변화",
        "status": "ok",
        "message": null
      },
      "watchlist": {
        "items": [
          {
            "ticker": "035420",
            "name": "NAVER",
            "latest_date": "2026-11-03",
            "day_change_pct": -2.9,
            "week_change_pct": -5.84,
            "price_text": "2026-11-03 종가 170,800원 · 전일 대비 -2.90% · 5거래일 -5.84% · 20거래일 -11.13%",
            "notable": true,
            "changes": [
              "NAVER의 공시가 최근 7일 안에 3건 있습니다."
            ],
            "status_text": null,
            "since_research": null,
            "disclosures": [
              {
                "title": "[기재정정]장래사업ㆍ경영계획(공정공시)",
                "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20261103800603",
                "disclosed_at": "2026-11-03T00:00:00+09:00",
                "disclosure_type": "[기재정정]장래사업ㆍ경영계획(공정공시)"
              }
            ],
            "news": [
              {
                "url": "https://www.gamevu.co.kr/news/articleView.html?idxno=61979",
                "title": "[ICT 종합] 네이버 'AI 탭' 국제표준 인증 및 두나무 소식 외",
                "source": "www.gamevu.co.kr",
                "published_at": "2026-11-03T17:46:00+09:00",
                "similar_count": 1
              }
            ]
          }
        ],
        "title": "관심 종목 업데이트",
        "status": "ok",
        "message": null,
        "dedup_text": "보유 종목과 겹치는 관심 종목 1개는 보유 종목 영역에만 표시했습니다.",
        "notable_count": 1
      },
      "risk": {
        "flags": [
          {
            "code": "MARKET_SKEW",
            "text": "주식 평가금액의 100.0%가 KOSPI에 몰려 있습니다(기준 80%).",
            "value": 100.0,
            "ticker": null,
            "threshold": 80.0,
            "company_name": null
          }
        ],
        "texts": [
          "상위 1종목 비중 28.76% · 상위 3종목 비중 69.50% · 현금 비중 30.50% · 보유 종목 3개"
        ],
        "title": "포트폴리오 위험 점검",
        "sector": {
          "text": "업종 분류 데이터가 없어 업종 집중도는 계산하지 않았습니다.",
          "status": "unavailable"
        },
        "status": "ok",
        "metrics": {
          "total_asset": 9834500.0,
          "holding_count": 3,
          "cash_weight_pct": 30.5,
          "top1_weight_pct": 28.76,
          "top3_weight_pct": 69.5,
          "herfindahl_index": 0.3451,
          "market_weights_pct": {
            "KOSPI": 100.0
          },
          "priced_holding_count": 3
        },
        "cautions": [
          "시세가 2영업일 지연돼 위험 점검도 2026-11-03 종가 기준입니다.",
          "SK하이닉스의 2026-11-03 종가는 전 거래일 대비 -4.46% 하락했습니다.",
          "삼성전자의 2026-11-03 종가는 전 거래일 대비 -3.48% 하락했습니다.",
          "KODEX 200의 2026-11-03 종가는 전 거래일 대비 -3.89% 하락했습니다."
        ],
        "etf_exposure": {
          "text": "ETF 구성종목 비중을 반영한 기업별 실질 노출을 계산했습니다(구성종목 기준일 2026-10-30).",
          "flags": [
            {
              "kind": "concentration",
              "text": "SK하이닉스의 실질 비중이 보유 평가금액의 48.54%입니다(기준 40% 이상).",
              "value_pct": 48.54,
              "threshold_pct": 40.0
            },
            {
              "kind": "concentration",
              "text": "삼성전자의 실질 비중이 보유 평가금액의 41.42%입니다(기준 40% 이상).",
              "value_pct": 41.42,
              "threshold_pct": 40.0
            }
          ],
          "status": "computed",
          "undecomposed": [],
          "top_companies": [
            {
              "code": "000660",
              "name": "SK하이닉스",
              "weight_pct": 48.54
            },
            {
              "code": "005930",
              "name": "삼성전자",
              "weight_pct": 41.42
            }
          ]
        }
      },
      "checklist": {
        "items": [
          {
            "kind": "data_delay",
            "text": "시세가 2영업일 지연돼 있어 최신 거래일(2026-11-05) 내용이 반영되지 않았습니다.",
            "ticker": null
          },
          {
            "url": "https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20261103800603",
            "kind": "watchlist_disclosure",
            "text": "NAVER(035420)의 최근 공시: [기재정정]장래사업ㆍ경영계획(공정공시) (2026-11-03)",
            "scope": "watchlist",
            "ticker": "035420"
          }
        ],
        "title": "오늘 확인할 사항",
        "status": "ok",
        "message": null
      }
    },
    "highlights": [
      {
        "date": "2026-11-03",
        "name": "SK하이닉스",
        "text": "SK하이닉스의 2026-11-03 종가는 전 거래일 대비 -4.46% 하락했습니다.",
        "type": "day_move",
        "unit": "%",
        "scope": "holding",
        "value": -4.46,
        "ticker": "000660",
        "direction": "down",
        "threshold": 3.0
      }
    ],
    "disclaimer": "이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
  }
}
```

## 11. 아직 안 된 것(백엔드 확인 결과)

- **업종 집중도**: company.sector가 300종목 모두 비어 있어 계산하지 않습니다(`risk.sector.status: "unavailable"`). 업종 뉴스는 업종 ETF 구성종목으로 반도체·2차전지·전력설비만 매핑합니다.
- **실적 발표 일정**: 미래 일정 데이터가 없습니다. 이미 접수된 실적 관련 공시만 "확인할 사항"에 올립니다.
- **시장 주요 이벤트**: 제목에 시장 키워드가 있는 기사 목록뿐이고, 사건을 해석하거나 묶어 설명하지 않습니다.
- **자동 생성**: 매일 정해진 시각에 미리 만들어 두지 않습니다. 사용자가 만들기를 눌러야 생깁니다.
- **통합 검색**: `/api/history/search`에는 브리핑이 나오지 않습니다(리서치·진단만). 브리핑 이력은 `/api/briefings/daily/history`를 쓰세요.
- **AI 요약**: 숫자 검증을 통과해도 문장이 숫자 나열처럼 길 수 있습니다. 통과하지 못하면 코드 요약이고, `summary_source`로 구분됩니다.
