# 프론트엔드 인계: ETF 실질 노출(look-through)과 포트폴리오 집중도 (2026-11-06)

받는 사람: 서윤 님(프론트엔드). 11월 9일부터 이 문서만 보고 화면을 만들 수 있게 썼습니다. 백엔드 규칙은 `backend/API.md`의 "ETF 실질 노출(look-through)" 절에 있습니다.

## 1. 바로 알아야 할 것 6가지

1. **새 라우트는 없습니다.** 기존 `GET /api/portfolio/exposure`에 `view=look_through`를 붙이면 새 응답이 옵니다. `view`가 없으면 예전 응답 그대로입니다(지금 화면은 깨지지 않습니다).
2. **비중의 분모는 총자산**입니다(현금 + 직접 주식 + ETF). 예전 기본 응답의 `effective_weight_pct`는 "보유 평가액 대비"였고, 새 응답의 `total_weight_pct`는 "총자산 대비"입니다. 둘을 섞지 마세요.
3. **ETF 금액은 총자산에 한 번만** 들어갑니다. 구성종목 금액은 기업별로 나눠 보여 줄 뿐, 총자산에 다시 더하지 않습니다.
4. **계산하지 못한 부분은 "미확인 노출"로 따로** 옵니다(`summary.unidentified_amount`). 기존 종목에 나눠 주거나 100%로 맞추지 않았습니다. 기업 비중을 다 더해도 100%가 안 되는 것이 정상입니다.
5. **포트폴리오 진단**(`POST /api/portfolio/diagnosis`) 결과에 `look_through`(진단 당시 스냅샷)가 더해졌습니다. 이 기능 전에 저장한 진단은 `look_through: null`입니다.
6. **진단 비교**(`GET /api/portfolio/diagnosis/{id}/compare?view=record`)의 `exposure_changes`는 두 진단 모두 스냅샷이 있을 때만 채워지고, 아니면 null입니다.

## 2. 개념과 계산 공식

ETF는 여러 기업 주식을 담은 상품이라, ETF를 사면 그 안의 기업에도 간접적으로 투자한 셈입니다. "실질 노출"은 직접 산 주식과 ETF를 통해 간접으로 가진 몫을 기업별로 합친 값입니다.

| 값 | 공식 |
| --- | --- |
| 직접 노출 | 주식 보유 수량 × 최신 종가 |
| ETF 평가액 | ETF 보유 수량 × 최신 종가 |
| ETF 간접 노출 | ETF 평가액 × 구성 비중(%) ÷ 100 (양수 비중만). 같은 기업을 담은 ETF가 여럿이면 합산 |
| 실질 노출 합계 | 직접 + 간접 |
| 총자산 | 현금 + 직접 주식 평가액 + ETF 평가액 |
| 총자산 대비 비중 | 실질 노출 합계 ÷ 총자산 × 100 |

예: 삼성전자 직접 100만 원 + 삼성전자를 20% 담은 ETF 200만 원 + 현금 100만 원이면 다음과 같습니다.
- 직접 노출 100만 원
- 간접 노출 40만 원
- 합계 140만 원
- 총자산 400만 원(ETF 200만 원은 한 번만 셈)
- 비중 35%

**ETF 평가액은 다음 넷으로 나뉩니다.** 합이 ETF 평가액과 같습니다.
- 기업 배분액(`via_etf_amount`)
- 중첩 ETF 배분액(`nested_etf_amount`)
- 구성 비중 밖 잔여(`residual_amount`: 비중 합계가 100%보다 작으면 양수, 크면 음수)
- 분해하지 않은 ETF 금액(`undecomposed_etf_amount`)

미확인 노출 = 중첩 ETF + 양수 잔여 + 분해하지 않은 ETF입니다.

## 3. API

### 3-1. `GET /api/portfolio/exposure?view=look_through`

| 파라미터 | 기본값 | 설명 |
| --- | --- | --- |
| `view` | `default` | `look_through`면 이 문서의 응답. 그 밖의 값은 422 |
| `limit` | 10 | `companies` 목록 길이(1~50). 합계(`summary`)와 `overlaps`는 전체 기준 |
| `locale` | 계정 설정 | 문장 언어. 숫자는 같다 |

로그인이 필요합니다(없으면 401). 본인 보유만 계산하고 계좌를 만들지 않습니다. 쿼리는 보유 종목 수와 관계없이 6개 이하이고, dev DB의 구성종목 4,926행 전체를 읽는 데 35ms였습니다.

### 3-2. 응답 최상위

| 필드 | 설명 |
| --- | --- |
| `calc_version` | `"look_through.v1"` 계산 규칙 버전 |
| `status` | `ok` / `empty`(보유 없음) / `no_prices`(보유는 있지만 종가가 하나도 없음) |
| `message` | empty·no_prices일 때 안내 문장, ok면 null |
| `has_account` | 모의투자 계좌가 있는가 |
| `valuation_date` | 평가에 쓴 종가 중 가장 최근 날짜 |
| `expected_date`, `price_lag_business_days` | 종가가 있어야 할 거래일과 지연 영업일 수 |
| `stale`, `stale_notice` | 종가가 지연됐는가와 그 안내 문장 |
| `etf_holdings_stale` | 오래된 구성 기준일을 쓴 ETF가 있는가 |
| `summary` | 3-3 |
| `companies[]` | 기업별 실질 노출(3-4, 총자산 대비 비중 큰 순, `limit`개) |
| `overlaps[]` | 직접 + ETF 둘 다로 가진 기업(전체) |
| `etfs[]` | ETF별 기여도와 데이터 품질(3-5) |
| `unpriced[]` | 종가가 없어 평가하지 못한 보유 `{ticker, name, asset_type, quantity}`(금액 없음) |
| `sector` | 업종 집중도(3-7) |
| `flags[]` | 집중 경고(3-6) |
| `data_warnings[]` | 데이터 문제 안내(4절) |
| `disclaimer` | 계산 기준 안내 + 서비스 면책 |

### 3-3. `summary`

| 필드 | 뜻 |
| --- | --- |
| `cash_balance`, `holdings_value`, `total_asset` | 현금, 평가 가능한 보유 합계, 총자산(= 현금 + holdings_value) |
| `stock_amount`, `etf_amount` | 직접 주식 합계, ETF 평가액 합계 |
| `direct_amount` | 직접 노출 합계(= stock_amount) |
| `via_etf_amount` | ETF를 통해 기업에 배분한 합계 |
| `linked_via_amount`, `unlinked_via_amount` | 그중 종목 목록과 연결된 구성종목 / 연결되지 않은 구성종목(해외 주식·채권·CP·스왑 등) |
| `companies_amount`, `companies_weight_pct` | 기업 노출 합계(직접 + 간접)와 총자산 대비 비중 |
| `nested_etf_amount` | ETF 안의 다른 ETF 몫(분해 안 함) |
| `residual_amount`, `over_allocated_amount` | 구성 비중 밖 잔여(음수 가능), 비중 합계가 100%를 넘어 생긴 초과분 |
| `undecomposed_etf_amount` | 분해하지 않은 ETF(레버리지·인버스, 데이터 없음, 비중 오류) 평가액 |
| `unidentified_amount`, `unidentified_weight_pct` | 미확인 노출과 총자산 대비 비중 |
| `cash_weight_pct` | 현금 비중 |
| `holding_count`, `priced_holding_count`, `unpriced_count`, `company_count` | 보유 수, 평가한 수, 평가 못 한 수, 기업 수 |
| `direct_top1_weight_pct` | 직접 보유 주식 중 가장 큰 비중 |
| `look_through_top1` | 실질 노출 1위 `{name, code, total_weight_pct}` |

### 3-4. 기업별 실질 노출 `companies[]`

| 필드 | 뜻 |
| --- | --- |
| `rank`, `name`, `code`, `ticker`, `linked` | 순위, 이름(원문), 운용사 코드, 우리 종목코드(연결된 경우), 종목 목록과 연결됐는가 |
| `exposure_type` | `direct`(직접만) / `etf`(ETF로만) / `both`(둘 다) |
| `direct_amount`, `via_etf_amount`, `total_amount` | 직접·간접·합계(원) |
| `direct_weight_pct`, `via_etf_weight_pct`, `total_weight_pct` | 총자산 대비(%) |
| `holdings_weight_pct` | 보유 평가액 대비(%, 예전 기본 응답의 비중과 같은 정의, 참고용) |
| `sector` | 업종(지금은 모두 null) |
| `via_etfs[]` | ETF별 기여 `{ticker, name, amount, constituent_weight_pct, asset_weight_pct}`(금액 큰 순). 여러 ETF의 기여와 합계를 둘 다 보여 줄 수 있다 |

`linked: false`는 우리 종목 목록에 없는 구성종목이라 이름으로 묶은 것입니다. 해외 주식일 수도 있고 채권·CP·스왑·선물일 수도 있습니다. 화면에 "종목 목록에 없는 구성종목" 표시를 붙여 주세요(`look_through.unlinked`).

### 3-5. ETF별 기여도 `etfs[]`

| 필드 | 뜻 |
| --- | --- |
| `ticker`, `name`, `eval_amount`, `asset_weight_pct` | ETF와 보유 평가액, 총자산 대비 |
| `status`, `reason_code` | `decomposed` / `not_decomposed`, 이유 `leveraged_inverse` · `no_holdings_data` · `invalid_weights` |
| `leverage_kind`, `notice` | 레버리지·인버스 종류(이름 원문 값)와 안내 문장(3-8) |
| `holdings_as_of`, `holdings_lag_business_days`, `holdings_stale` | 구성 기준일, 지연 영업일, 오래됨 여부 |
| `constituent_count`, `linked_count`, `unlinked_count`, `nested_etf_count` | 구성종목 수와 연결 상태별 수 |
| `positive_weight_sum_pct`, `negative_weight_count`, `negative_weight_sum_pct`, `zero_weight_count`, `null_weight_count`, `over_100_count`, `duplicate_count` | 데이터 품질 값 |
| `allocated_amount`, `nested_etf_amount`, `residual_amount` | 분해한 ETF의 금액 나눔(분해 안 했으면 null) |
| `constituents[]` | 비중 큰 상위 10개 `{name, code, linked_ticker, weight_pct, amount, kind: company\|nested_etf}`. **분해 안 한 ETF는 `amount: null`**(공시 비중만) |
| `nested_etfs[]` | 이 ETF가 담은 다른 ETF `{name, code, linked_ticker, weight_pct, amount}` |

### 3-6. 플래그 `flags[]`

| `kind` | 조건(모두 총자산 대비) |
| --- | --- |
| `concentration` | 한 기업 실질 노출 ≥ 40% |
| `etf_inflates_weight` | 한 기업의 ETF 간접 노출 ≥ 10%p |
| `single_etf_exposure` | 한 ETF 하나를 통한 한 기업 노출 ≥ 10% (`etf_ticker`, `etf_name`) |

각 플래그에 `value_pct`, `threshold_pct`, `message`(그 언어)가 있습니다.

### 3-7. 업종 `sector`

`{status, covered_weight_pct, items[], message}`입니다.
- 지금은 company.sector가 비어 있어 **`unavailable`**입니다.
- 업종이 있는 기업이 기업 노출의 95% 이상이면 `available`, 일부면 `partial`입니다.
- 업종 뉴스용 반도체·2차전지 분류는 전체 업종 분류가 아니라서 쓰지 않습니다.
- `unavailable`이면 업종 차트를 그리지 말고 `message`만 보여 주세요.

### 3-8. 레버리지·인버스 ETF

- 구성종목이 있어도 기업별로 나누지 않고, 평가액 전체를 미확인 노출로 둡니다.
- 배수(2배, -1배)를 곱하지 않습니다.
- `constituents[]`에는 운용사가 공시한 구성 비중만 있고 금액은 null입니다.
- 화면에서는 "공시 구성 비중이며 경제적 노출이 아닙니다"(`notice`)를 꼭 함께 보여 주고, 기업 노출 표에는 넣지 마세요.

## 4. 데이터 누락·지연·계산 불가

`data_warnings[]`는 `{kind, ticker?, name?, …값, message}`입니다. 문장은 그 언어로 오니 그대로 보여 주면 됩니다.

| `kind` | 뜻 | 화면 처리 |
| --- | --- | --- |
| `no_price` | 보유 종목 종가 없음(평가 불가, 0원 아님) | 평가 불가 목록(`unpriced`)에 표시 |
| `older_price` | 이 종목의 종가가 다른 종목보다 오래됨 | 종목 옆 작은 배지 |
| `etf_leveraged_inverse` | 레버리지·인버스라 분해 안 함 | ETF 카드 경고 |
| `etf_no_holdings_data` | 구성 데이터 없음 | ETF 카드 "구성 데이터 없음" |
| `etf_invalid_weights` | 한 종목 비중 100% 초과 등 오류라 분해 안 함 | ETF 카드 경고 |
| `etf_weight_under_100` | 비중 합계 95% 미만, 나머지는 미확인 | ETF 카드 안내(`pct`, `rest`, `amount`) |
| `etf_weight_over_100` | 비중 합계 100.5% 초과(정규화 안 함, 잔여 음수) | ETF 카드 안내 |
| `etf_negative_weights` | 음수 비중 구성종목(기업 노출에서 뺌) | 데이터 품질 접기 |
| `etf_null_weights` | 비중이 비어 있는 구성종목(뺌) | 데이터 품질 접기 |
| `etf_duplicate_constituents` | 같은 종목 중복 행(합산함) | 데이터 품질 접기 |
| `etf_nested_etf` | 다른 ETF를 담음(분해 안 함, 미확인) | ETF 카드 안내 |
| `etf_unlinked_constituents` | 종목 목록에 없는 구성종목(이름으로 묶음) | 데이터 품질 접기 |
| `etf_holdings_stale` | 구성 기준일이 오래됨(2영업일 이상) | ETF 카드 "오래된 구성" 배지 |

- **종가 지연**: `stale`이 true면 화면 위쪽에 `stale_notice`를 노란 띠로 보여 주세요.
- **구성 기준일 지연**: dev DB의 구성 기준일은 2026-10-29·30이라 지금은 모든 ETF가 `holdings_stale: true`입니다. 구성 데이터를 다시 모으기 전까지 계속 이 상태입니다.
- **계산 불가 표시**: 숫자가 null이면 0이 아니라 "—"로 표시하세요. 미확인 노출은 회색 막대 등으로 "확인되지 않은 몫"임을 구분해 주세요.

## 5. 다국어

- 숫자·금액·비중·종목코드·구성 비중·기준일·상태 코드는 모든 언어에서 같습니다(테스트로 확인).
- 문장 필드는 `message`, `notice`, `stale_notice`, `sector.message`, `disclaimer`이고 백엔드가 그 언어로 만듭니다.
- 종목명·ETF 이름은 원문 그대로입니다.
- 열 이름·배지·코드 라벨은 `docs/i18n/{locale}.json`의 `look_through` 묶음에 있습니다.
  - 열 이름: `direct`, `via_etf`, `total`, `total_weight`, `unidentified`, `nested_etf`, `residual`, `unlinked`, `unpriced`, `holdings_as_of` …
  - 코드 라벨: `exposure_type.*`, `etf_status.*`, `reason.*`, `sector_status.*`

## 6. 포트폴리오 진단과 진단 비교

### 6-1. 진단 결과의 `look_through`

`POST /api/portfolio/diagnosis` 응답과 `GET /api/portfolio/diagnosis/{id}`에 `look_through`가 더해졌습니다. 기존 필드(metrics·flags·holdings 등)의 값과 뜻은 그대로입니다.

| 필드 | 설명 |
| --- | --- |
| `status` | `ok` / `empty` / `no_prices` / `unavailable`(계산 실패. 진단은 정상) |
| `calc_version`, `computed_at`, `valuation_basis` | 계산 버전, 계산 시각, `diagnosis_prices`(진단이 쓴 현재가로 평가) |
| `summary` | 3-3과 같은 필드. `holding_top1_weight_pct`가 더 있다(기존 진단의 "상위 1종목 비중", 직접 보유 기준) |
| `companies[]` | 상위 20개 기업(3-4) |
| `overlaps[]` | 직접·간접이 겹치는 기업(상위 20) |
| `etfs[]` | 진단 당시 ETF별 기여도, 구성 기준일, **당시 상위 구성종목과 비중** |
| `flags[]`, `data_warnings[]`, `sector`, `unpriced[]` | 3절과 같음 |

화면 권장 구성은 다음과 같습니다.
- **직접 보유 집중도:** 기존 `metrics.top1_weight_pct`
- **실질 집중도:** `look_through.summary.look_through_top1`
- 위 둘을 나란히 보여 주세요.
- 그 아래에 겹치는 기업(`overlaps`), 특정 ETF로 과도한 노출(`flags`의 `single_etf_exposure`), 미확인 비중(`unidentified_weight_pct`), 데이터 경고를 둡니다.

**과거 진단을 다시 열면 그때 저장한 값 그대로**입니다(현재 구성종목으로 다시 계산하지 않음). 이 기능 전에 만든 진단은 `look_through: null`이므로 "이 진단에는 실질 노출 기록이 없습니다"라고 표시해 주세요.

### 6-2. 진단 비교의 `exposure_changes`

`GET /api/portfolio/diagnosis/{id}/compare?view=record`에서 다음 구조로 옵니다.
- `calc_version`, `valuation_date`: 각 `{previous, current}`
- `summary`: `{total_asset, via_etf_amount, unidentified_weight_pct, companies_weight_pct, direct_top1_weight_pct}` 각각 `{previous, current, change}`
- `look_through_top1`
- `companies[]`: 기업별 `{name, code, previous_total_weight_pct, current_total_weight_pct, change_pct, previous_total_amount, current_total_amount, status: added|removed|changed}`(변화 큰 순)

**null 조건:** 둘 중 하나라도 스냅샷이 없으면(이 기능 전 진단) null입니다. 비교 대상 기업은 각 저장본에 남긴 상위 20개끼리입니다(`companies_compared: "top_saved"`).

## 7. 로딩·오류·빈 데이터

| 상태 | 처리 |
| --- | --- |
| 로딩 | 보통 0.1초 이내(DB만 읽음) |
| 401 | 로그인 화면 |
| 422 | 잘못된 `view`·`limit` |
| `status: empty` | `message`(보유 없음) + 모의투자 안내 |
| `status: no_prices` | `message` + `unpriced` 목록 |
| `has_account: false` | 계좌 없음(보유도 없으므로 empty와 같이 처리) |
| `companies`가 비었지만 ok | ETF가 모두 분해 안 됨(레버리지 등). 미확인 노출과 `etfs` 카드만 보여 준다 |
| 진단 `look_through: null` | 옛 진단이라 기록 없음 |
| 진단 `look_through.status: unavailable` | 계산 실패(진단의 나머지는 정상) |

## 8. 추천 화면 구성

1. **요약 카드**: 총자산, 기업 노출 합계(%), 미확인 노출(%), 현금(%)을 100% 막대 하나로 표시. 회색이 미확인입니다.
2. **경고 띠**: `stale_notice`, `etf_holdings_stale`이면 "ETF 구성 기준일이 오래됐습니다"
3. **기업별 표**: 기업 / 직접 / ETF 간접 / 합계 / 총자산 대비. 행을 펼치면 `via_etfs`(ETF별 기여), `exposure_type` 배지
4. **겹치는 기업**: `overlaps`(직접 + ETF)
5. **ETF 카드 목록**: 평가액, 상태 배지(분해함·분해 안 함·레버리지), 구성 기준일, 상위 구성종목 표, 중첩 ETF, 데이터 품질 접기
6. **업종**: `unavailable`이면 문장만
7. **플래그**와 **데이터 경고**
8. **면책**: `disclaimer`

## 9. 실제 응답 사례(합성 데이터)

입력(합성):
- 현금 100만 원
- 삼성전자 10주 × 100,000원(직접)
- TIGER 합성200 100주 × 20,000원. 구성: 삼성전자 20%, SK하이닉스 15%, 현대차 40%, NVIDIA 5%, 하위 ETF(TIGER 200) 10%, 합계 90%
- KODEX 레버리지 10주 × 50,000원
- SK하이닉스 직접 1주(종가 없음)

계산:
- 총자산 = 100만 + 100만 + 200만 + 50만 = 450만 원
- 삼성전자 = 직접 100만 + 간접 40만 = 140만 원(31.11%)
- 미확인 = 중첩 ETF 20만 + 잔여 20만 + 레버리지 50만 = 90만 원(20%)

```json
{
  "generated_at": "2026-11-06T20:00:00+09:00",
  "calc_version": "look_through.v1",
  "status": "ok",
  "has_account": true,
  "valuation_date": "2026-11-06",
  "expected_date": "2026-11-06",
  "price_lag_business_days": 0,
  "etf_holdings_stale": false,
  "summary": {
    "cash_balance": 1000000.0,
    "holdings_value": 3500000.0,
    "total_asset": 4500000.0,
    "stock_amount": 1000000.0,
    "etf_amount": 2500000.0,
    "direct_amount": 1000000.0,
    "via_etf_amount": 1600000.0,
    "companies_amount": 2600000.0,
    "linked_via_amount": 1500000.0,
    "unlinked_via_amount": 100000.0,
    "nested_etf_amount": 200000.0,
    "residual_amount": 200000.0,
    "undecomposed_etf_amount": 500000.0,
    "unidentified_amount": 900000.0,
    "unidentified_weight_pct": 20.0,
    "over_allocated_amount": 0.0,
    "cash_weight_pct": 22.22,
    "companies_weight_pct": 57.78,
    "holding_count": 4,
    "priced_holding_count": 3,
    "unpriced_count": 1,
    "company_count": 4,
    "direct_top1_weight_pct": 22.22,
    "look_through_top1": {
      "name": "삼성전자",
      "code": "005930",
      "total_weight_pct": 31.11
    }
  },
  "companies": [
    {
      "rank": 1,
      "name": "삼성전자",
      "code": "005930",
      "ticker": "005930",
      "linked": true,
      "sector": null,
      "exposure_type": "both",
      "direct_amount": 1000000.0,
      "via_etf_amount": 400000.0,
      "total_amount": 1400000.0,
      "direct_weight_pct": 22.22,
      "via_etf_weight_pct": 8.89,
      "total_weight_pct": 31.11,
      "holdings_weight_pct": 40.0,
      "via_etfs": [
        {
          "ticker": "111111",
          "name": "TIGER 합성200",
          "amount": 400000.0,
          "constituent_weight_pct": 20.0,
          "asset_weight_pct": 8.89
        }
      ]
    },
    {
      "rank": 2,
      "name": "현대차",
      "code": "005380",
      "ticker": "005380",
      "linked": true,
      "sector": null,
      "exposure_type": "etf",
      "direct_amount": 0.0,
      "via_etf_amount": 800000.0,
      "total_amount": 800000.0,
      "direct_weight_pct": 0.0,
      "via_etf_weight_pct": 17.78,
      "total_weight_pct": 17.78,
      "holdings_weight_pct": 22.86,
      "via_etfs": [
        {
          "ticker": "111111",
          "name": "TIGER 합성200",
          "amount": 800000.0,
          "constituent_weight_pct": 40.0,
          "asset_weight_pct": 17.78
        }
      ]
    },
    {
      "rank": 3,
      "name": "SK하이닉스",
      "code": "000660",
      "ticker": "000660",
      "linked": true,
      "sector": null,
      "exposure_type": "etf",
      "direct_amount": 0.0,
      "via_etf_amount": 300000.0,
      "total_amount": 300000.0,
      "direct_weight_pct": 0.0,
      "via_etf_weight_pct": 6.67,
      "total_weight_pct": 6.67,
      "holdings_weight_pct": 8.57,
      "via_etfs": [
        {
          "ticker": "111111",
          "name": "TIGER 합성200",
          "amount": 300000.0,
          "constituent_weight_pct": 15.0,
          "asset_weight_pct": 6.67
        }
      ]
    },
    {
      "rank": 4,
      "name": "NVIDIA Corp",
      "code": "NVDA US EQUITY",
      "ticker": null,
      "linked": false,
      "sector": null,
      "exposure_type": "etf",
      "direct_amount": 0.0,
      "via_etf_amount": 100000.0,
      "total_amount": 100000.0,
      "direct_weight_pct": 0.0,
      "via_etf_weight_pct": 2.22,
      "total_weight_pct": 2.22,
      "holdings_weight_pct": 2.86,
      "via_etfs": [
        {
          "ticker": "111111",
          "name": "TIGER 합성200",
          "amount": 100000.0,
          "constituent_weight_pct": 5.0,
          "asset_weight_pct": 2.22
        }
      ]
    }
  ],
  "overlaps": [
    {
      "rank": 1,
      "name": "삼성전자",
      "code": "005930",
      "ticker": "005930",
      "linked": true,
      "sector": null,
      "exposure_type": "both",
      "direct_amount": 1000000.0,
      "via_etf_amount": 400000.0,
      "total_amount": 1400000.0,
      "direct_weight_pct": 22.22,
      "via_etf_weight_pct": 8.89,
      "total_weight_pct": 31.11,
      "holdings_weight_pct": 40.0,
      "via_etfs": [
        {
          "ticker": "111111",
          "name": "TIGER 합성200",
          "amount": 400000.0,
          "constituent_weight_pct": 20.0,
          "asset_weight_pct": 8.89
        }
      ]
    }
  ],
  "etfs": [
    {
      "ticker": "111111",
      "name": "TIGER 합성200",
      "eval_amount": 2000000.0,
      "asset_weight_pct": 44.44,
      "leverage_kind": null,
      "holdings_as_of": "2026-11-05",
      "holdings_lag_business_days": 1,
      "holdings_stale": false,
      "constituent_count": 5,
      "null_weight_count": 0,
      "zero_weight_count": 0,
      "negative_weight_count": 0,
      "negative_weight_sum_pct": 0,
      "over_100_count": 0,
      "duplicate_count": 0,
      "positive_weight_sum_pct": 90.0,
      "linked_count": 3,
      "unlinked_count": 1,
      "nested_etf_count": 1,
      "allocated_amount": 1600000.0,
      "nested_etf_amount": 200000.0,
      "residual_amount": 200000.0,
      "nested_etfs": [
        {
          "name": "TIGER 200",
          "code": "102110",
          "linked_ticker": "102110",
          "weight_pct": 10.0,
          "amount": 200000.0
        }
      ],
      "status": "decomposed",
      "reason_code": null,
      "constituents": [
        {
          "name": "현대차",
          "code": "005380",
          "linked_ticker": "005380",
          "weight_pct": 40.0,
          "amount": 800000.0,
          "kind": "company"
        },
        {
          "name": "삼성전자",
          "code": "005930",
          "linked_ticker": "005930",
          "weight_pct": 20.0,
          "amount": 400000.0,
          "kind": "company"
        },
        {
          "name": "SK하이닉스",
          "code": "000660",
          "linked_ticker": "000660",
          "weight_pct": 15.0,
          "amount": 300000.0,
          "kind": "company"
        },
        {
          "name": "TIGER 200",
          "code": "102110",
          "linked_ticker": "102110",
          "weight_pct": 10.0,
          "amount": 200000.0,
          "kind": "nested_etf"
        },
        {
          "name": "NVIDIA Corp",
          "code": "NVDA US EQUITY",
          "linked_ticker": null,
          "weight_pct": 5.0,
          "amount": 100000.0,
          "kind": "company"
        }
      ],
      "notice": null
    },
    {
      "ticker": "122630",
      "name": "KODEX 레버리지",
      "eval_amount": 500000.0,
      "asset_weight_pct": 11.11,
      "leverage_kind": "레버리지",
      "holdings_as_of": "2026-11-05",
      "holdings_lag_business_days": 1,
      "holdings_stale": false,
      "constituent_count": 2,
      "null_weight_count": 0,
      "zero_weight_count": 0,
      "negative_weight_count": 0,
      "negative_weight_sum_pct": 0,
      "over_100_count": 0,
      "duplicate_count": 0,
      "positive_weight_sum_pct": 52.69,
      "linked_count": 1,
      "unlinked_count": 0,
      "nested_etf_count": 1,
      "allocated_amount": null,
      "nested_etf_amount": null,
      "residual_amount": null,
      "nested_etfs": [],
      "status": "not_decomposed",
      "reason_code": "leveraged_inverse",
      "constituents": [
        {
          "name": "삼성전자",
          "code": "005930",
          "linked_ticker": "005930",
          "weight_pct": 30.0,
          "amount": null,
          "kind": "company"
        },
        {
          "name": "KODEX 200",
          "code": "069500",
          "linked_ticker": "069500",
          "weight_pct": 22.69,
          "amount": null,
          "kind": "nested_etf"
        }
      ],
      "notice": "공시 구성 비중이며 경제적 노출이 아닙니다(배수를 곱하지 않음)."
    }
  ],
  "unpriced": [
    {
      "ticker": "000660",
      "name": "SK하이닉스",
      "asset_type": "stock",
      "quantity": 1
    }
  ],
  "sector": {
    "status": "unavailable",
    "covered_weight_pct": 0.0,
    "items": [],
    "message": "업종 분류 데이터가 없어 업종별 집중도는 계산하지 않았습니다."
  },
  "flags": [
    {
      "kind": "etf_inflates_weight",
      "name": "현대차",
      "code": "005380",
      "value_pct": 17.78,
      "threshold_pct": 10.0,
      "total_weight_pct": 17.78,
      "direct_weight_pct": 0.0,
      "message": "현대차의 ETF를 통한 노출이 총자산의 17.78%라 실질 노출(17.78%)이 직접 보유(0.0%)보다 큽니다(기준 10%p 이상)."
    },
    {
      "kind": "single_etf_exposure",
      "name": "현대차",
      "code": "005380",
      "etf_ticker": "111111",
      "etf_name": "TIGER 합성200",
      "value_pct": 17.78,
      "threshold_pct": 10.0,
      "message": "TIGER 합성200 한 상품을 통한 현대차 노출이 총자산의 17.78%입니다(기준 10% 이상)."
    }
  ],
  "data_warnings": [
    {
      "kind": "no_price",
      "ticker": "000660",
      "name": "SK하이닉스",
      "message": "SK하이닉스(000660)의 최신 종가가 없어 평가 불가로 두었습니다(0원으로 계산하지 않음)."
    },
    {
      "ticker": "111111",
      "name": "TIGER 합성200",
      "kind": "etf_weight_under_100",
      "pct": 90.0,
      "rest": 10.0,
      "amount": 200000.0,
      "message": "TIGER 합성200의 구성 비중 합계가 90%입니다. 나머지 10%(200,000원)는 기존 종목에 나누지 않고 미확인 노출로 두었습니다."
    },
    {
      "ticker": "111111",
      "name": "TIGER 합성200",
      "kind": "etf_nested_etf",
      "count": 1,
      "amount": 200000.0,
      "message": "TIGER 합성200의 구성종목 중 다른 ETF가 1개 있습니다. 중첩 ETF는 다시 분해하지 않아 그 금액 200,000원을 미확인 노출로 두었습니다."
    },
    {
      "ticker": "111111",
      "name": "TIGER 합성200",
      "kind": "etf_unlinked_constituents",
      "count": 1,
      "message": "TIGER 합성200의 구성종목 1건은 종목 목록과 연결되지 않아(해외 종목·채권·선물 등) 이름으로 묶었습니다."
    },
    {
      "kind": "etf_leveraged_inverse",
      "ticker": "122630",
      "name": "KODEX 레버리지",
      "leverage_kind": "레버리지",
      "over_100_count": 0,
      "message": "레버리지 ETF KODEX 레버리지(122630)의 구성종목은 분해하지 않았습니다. 배수·선물·스왑 구조 때문에 공시 구성 비중은 실제 경제적 노출과 다르고, 배수를 곱해 노출을 확정하지 않습니다."
    }
  ],
  "limit": 10,
  "locale": "ko-KR",
  "message": null,
  "stale": false,
  "stale_notice": null,
  "disclaimer": "실질 노출은 보유 평가액과 운용사가 공시한 ETF 구성 비중(구성 기준일 값)으로 계산한 참고 값입니다. 비중의 분모는 총자산(현금 + 주식 + ETF)이고 ETF 구성종목 금액을 총자산에 다시 더하지 않습니다. 매수·매도 권유가 아닙니다. 이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
}
```

같은 결과의 en-US 문장 일부(숫자는 같음):

```json
{
  "data_warnings": [
    {
      "kind": "no_price",
      "ticker": "000660",
      "name": "SK하이닉스",
      "message": "SK하이닉스(000660) has no latest closing price, so it is marked as not valued (not counted as zero)."
    },
    {
      "ticker": "111111",
      "name": "TIGER 합성200",
      "kind": "etf_weight_under_100",
      "pct": 90.0,
      "rest": 10.0,
      "amount": 200000.0,
      "message": "TIGER 합성200's constituent weights sum to 90%. The remaining 10% (KRW 200,000) is not spread over the listed constituents and is treated as unidentified exposure."
    }
  ],
  "flags": [
    {
      "kind": "etf_inflates_weight",
      "name": "현대차",
      "code": "005380",
      "value_pct": 17.78,
      "threshold_pct": 10.0,
      "total_weight_pct": 17.78,
      "direct_weight_pct": 0.0,
      "message": "현대차's exposure through ETFs is 17.78% of total assets, so its look-through exposure (17.78%) exceeds its direct holding (0.0%) (threshold 10%p or more)."
    }
  ],
  "sector": {
    "status": "unavailable",
    "covered_weight_pct": 0.0,
    "items": [],
    "message": "Sector concentration was not calculated because sector classification data is unavailable."
  }
}
```

진단 비교 `exposure_changes` 예시(이전 진단: 삼성전자 직접 100만 + 현금 350만, 이후 진단: 위 포트폴리오):

```json
{
  "calc_version": {
    "current": "look_through.v1",
    "previous": "look_through.v1"
  },
  "valuation_date": {
    "current": "2026-11-06",
    "previous": "2026-11-05"
  },
  "summary": {
    "total_asset": {
      "previous": 4500000.0,
      "current": 4500000.0,
      "change": 0.0
    },
    "via_etf_amount": {
      "previous": 0.0,
      "current": 1600000.0,
      "change": 1600000.0
    },
    "unidentified_weight_pct": {
      "previous": 0.0,
      "current": 20.0,
      "change": 20.0
    },
    "companies_weight_pct": {
      "previous": 22.22,
      "current": 57.78,
      "change": 35.56
    },
    "direct_top1_weight_pct": {
      "previous": 22.22,
      "current": 22.22,
      "change": 0.0
    }
  },
  "look_through_top1": {
    "previous": {
      "name": "삼성전자",
      "code": "005930",
      "total_weight_pct": 22.22
    },
    "current": {
      "name": "삼성전자",
      "code": "005930",
      "total_weight_pct": 31.11
    }
  },
  "companies": [
    {
      "name": "현대차",
      "code": "005380",
      "previous_total_weight_pct": null,
      "current_total_weight_pct": 17.78,
      "change_pct": 17.78,
      "previous_total_amount": null,
      "current_total_amount": 800000.0,
      "status": "added"
    },
    {
      "name": "삼성전자",
      "code": "005930",
      "previous_total_weight_pct": 22.22,
      "current_total_weight_pct": 31.11,
      "change_pct": 8.89,
      "previous_total_amount": 1000000.0,
      "current_total_amount": 1400000.0,
      "status": "changed"
    },
    {
      "name": "SK하이닉스",
      "code": "000660",
      "previous_total_weight_pct": null,
      "current_total_weight_pct": 6.67,
      "change_pct": 6.67,
      "previous_total_amount": null,
      "current_total_amount": 300000.0,
      "status": "added"
    },
    {
      "name": "NVIDIA Corp",
      "code": "NVDA US EQUITY",
      "previous_total_weight_pct": null,
      "current_total_weight_pct": 2.22,
      "change_pct": 2.22,
      "previous_total_amount": null,
      "current_total_amount": 100000.0,
      "status": "added"
    }
  ],
  "note_code": "stored_snapshots",
  "companies_compared": "top_saved"
}
```

## 10. 아직 안 된 것과 한계

- **업종 집중도:** company.sector가 비어 있어 `unavailable`입니다.
- **중첩 ETF:** 재귀로 분해하지 않습니다. 하위 ETF가 담은 기업 노출은 미확인으로 남습니다. dev DB 기준 7개 ETF가 해당합니다(커버드콜·채권혼합·금리형 등).
- **레버리지·인버스:** 경제적 노출(배수 반영)은 계산하지 않습니다.
- **구성 기준일:** dev DB 구성 데이터가 2026-10-29·30 기준이라 모두 오래됨으로 나옵니다. 구성종목을 새로 모으는 일은 이번 범위가 아닙니다.
- **연결 안 된 구성종목:** 해외 주식·채권·CP·스왑은 종목 목록에 없어 이름으로 묶습니다. 기업인지 아닌지 자동으로 가리지 않습니다.
- **기본 응답:** `view` 없는 기본 응답(예전 계약)은 예전 계산 그대로입니다. 보유 평가액 대비이고, 하위 ETF를 기업처럼 셉니다. 새 화면은 `view=look_through`를 쓰세요.
- **일일 브리핑:** 위험 점검 영역은 아직 기본 응답의 노출 플래그를 씁니다.
