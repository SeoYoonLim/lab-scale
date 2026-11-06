# 프론트엔드 인계: 포트폴리오 수익률과 시장 지수 비교 (2026-11-06)

받는 사람: 서윤 님(프론트엔드). 11월 9일부터 이 문서만 보고 화면을 만들 수 있게 썼습니다. 백엔드 규칙은 `backend/API.md`의 "성과·벤치마크 비교" 절에 있습니다.

## 1. 바로 알아야 할 것 6가지

1. **새 라우트는 없습니다.** 기존 `GET /api/portfolio/performance`에 `view=benchmark`를 붙이면 새 응답이 옵니다. `view`가 없으면 예전 응답 그대로입니다.
2. **과거 보유는 실제 체결 내역을 날짜순으로 다시 재생해서 만듭니다.** 현재 보유를 과거에 붙이지 않습니다. 재생 결과(현금·보유 수량)가 지금 계좌와 맞지 않으면 `status: "history_unavailable"`이고 그래프를 주지 않습니다.
3. **상태(`status`)를 먼저 보세요.** `ok`·`partial`일 때만 그래프와 수익률이 있습니다. 나머지 상태에서는 수익률이 null이고, 0으로 그리면 안 됩니다.
4. **비교는 공통 거래일만 씁니다.** 포트폴리오와 지수를 둘 다 첫 공통 거래일에 100으로 맞춘 시계열(`comparison.points`)을 그대로 그리면 됩니다.
5. **데이터 지연을 꼭 표시하세요.** 지금 dev DB 시세는 2026-11-03까지라 `data_lag_business_days`가 0보다 크고, `warnings`에 `data_delayed`가 옵니다.
6. **배당·분배금은 포함하지 않는 가격 수익률**입니다. 분할·병합 등 기업행사도 보정하지 않습니다. `disclaimer`에 이 문장이 있으니 그래프 아래에 보여 주세요.

## 2. 계산 원리

### 2-1. 초기 현금과 거래 이력
- 계좌는 처음 만들 때 초기 자금 10,000,000원입니다(`initial_balance`).
- 현금은 매수(−체결가 × 수량)와 매도(+체결가 × 수량)로만 바뀝니다. **수수료·세금은 없고, 입금·출금 기능도 없습니다.**
- 계좌 초기화(`POST /api/portfolio/reset`)를 하면 체결 내역·보유가 지워지고 현금이 초기 자금으로 돌아갑니다. 그래서 성과는 마지막 초기화 이후부터입니다.
- 백엔드는 모든 체결을 다시 재생해 지금 계좌와 비교합니다. 현금이나 보유 수량이 다르면 과거를 믿을 수 없다고 보고 `history_unavailable`을 돌려줍니다. 임의의 시작 금액을 가정하지 않습니다.

### 2-2. 날짜별 보유 수량 복원과 총자산
- 거래일마다 그날까지의 체결을 순서대로 반영해 보유 수량과 현금을 만듭니다. 같은 날 여러 건이면 체결 시각, 그다음 체결 id 순서입니다.
- 주말·휴일에 체결한 주문은 다음 거래일 종가에 반영됩니다.
- **총자산 = 현금 + Σ(그날 보유 수량 × 그날 종가)** 이고, 주식과 ETF를 같은 방식으로 계산합니다.
- 그날 종가가 없는 보유 종목은 직전 종가(없으면 마지막 체결가)로 평가하고, 그 날짜 점에 `estimated: true`와 `estimated_tickers`를 붙입니다.
- 마지막 종가일 뒤에 체결된 주문은 아직 종가가 없어 반영하지 않습니다(`pending_trades` 경고).

### 2-3. 수익률
- 기간 수익률(%) = (기간 끝 총자산 ÷ 기간 시작 금액 − 1) × 100
- 시작 금액은 전체 기간(`all`)이면 초기 자금, 그 밖에는 실제 계산 시작일의 총자산입니다.
- 누적 수익률(%) = (현재 총자산 ÷ 초기 자금 − 1) × 100(`cumulative_return_pct`, 마지막 초기화 이후)
- 일별 수익률(%) = (당일 총자산 ÷ 전 거래일 총자산 − 1) × 100
- 매수·매도는 자산 안에서 현금과 주식이 바뀌는 것이라 수익률에 영향을 주지 않습니다. 입출금이 없으니 단순 수익률이 시간가중수익률(TWR)과 같습니다(`cash_flows.return_method: "simple"`).

### 2-4. 기간
- `period`: `1m`·`3m`·`6m`·`1y`·`all`(소문자). 기준일(`data_date`)에서 그 개월 수를 뺀 날 이하의 마지막 거래일부터입니다.
- 요청 시작이 첫 체결 전이면 첫 체결 직전 거래일부터 계산하고 `requested_start_date`와 `start_date`를 둘 다 줍니다(`period_before_inception` 경고). 거래하지 않은 기간을 성과에 넣지 않습니다.

### 2-5. 지수 비교
- 지원 지수는 DB에 실제로 있는 **코스피(KS11)·코스닥(KQ11)**뿐입니다. KOSPI200 지수 데이터는 없어서 지원하지 않습니다.
- `benchmark`는 `KS11` | `KQ11` | `KOSPI` | `KOSDAQ`이고, 응답 코드는 항상 `KS11`/`KQ11`입니다.
- 공통 거래일(포트폴리오와 지수가 둘 다 있는 날)만 씁니다. 지수가 없는 날을 채워 넣지 않습니다(`benchmark_missing_days`).
- 첫 공통 거래일을 양쪽 모두 100으로 정규화합니다. 그날이 포트폴리오 시작일과 다르면 그날로 다시 맞춥니다(`start_aligned: true`).
- 지수 수익률(%) = (끝 지수 ÷ 시작 지수 − 1) × 100이고, 초과 수익률(%p) = 포트폴리오 수익률 − 지수 수익률입니다.

### 2-6. 지표(표본이 충분할 때만)
| 지표 | 조건 | 없으면 |
| --- | --- | --- |
| 최고·최저 총자산 | 1개 이상 | null |
| 최대 낙폭 | 2개 이상 | null |
| 일간·연환산 변동성 | 일간 수익률 20개 이상 | null |
| 연환산 수익률 | 일간 수익률 252개 이상 | null |

## 3. API

```http
GET /api/portfolio/performance?view=benchmark&period=3m&benchmark=KS11&locale=ko-KR
Authorization: Bearer <token>
```

| 파라미터 | 기본값 | 값 |
| --- | --- | --- |
| `view` | `default` | `benchmark` |
| `period` | `all` | `1m`, `3m`, `6m`, `1y`, `all` (대문자는 422) |
| `benchmark` | `KS11` | `KS11`, `KQ11`, `KOSPI`, `KOSDAQ` |
| `locale` | 계정 설정 | `ko-KR`, `en-US`, `ja-JP`, `zh-CN` (숫자는 같음) |

로그인이 필요합니다(없으면 401). 본인 계좌만 읽고 계좌를 만들지 않습니다. 쿼리는 체결 수와 관계없이 7개 이하입니다.

### 3-1. 응답 최상위

| 필드 | 설명 |
| --- | --- |
| `status` | `ok` / `partial`(일부 날짜 추정) / `no_account` / `no_trades` / `pending_only` / `no_prices` / `history_unavailable` |
| `message` | 상태 안내 문장(ok면 null) |
| `history_verified` | 체결 재생이 지금 계좌와 맞았는가 |
| `period`, `requested_start_date`, `start_date`, `end_date` | 기간과 실제 계산 구간 |
| `data_date`, `expected_date`, `data_lag_business_days` | 마지막 종가일, 종가가 있어야 할 거래일, 지연 영업일 |
| `initial_balance` | 초기 자금 |
| `benchmark_code`, `benchmark_name` | 비교 지수 |
| `summary` | `{current_cash, start_value, end_value, profit_loss, period_return_pct, cumulative_return_pct, eval_amount, cash_balance}` |
| `metrics` | `{highest, lowest, max_drawdown, daily_volatility_pct, annualized_volatility_pct, annualized_return_pct, daily_return_count, trading_days, sample_rules}` |
| `series` | 내 포트폴리오 시계열(3-2) |
| `comparison` | 지수 비교(3-3) |
| `estimated_dates` | 추정값이 들어간 날짜 |
| `cash_flows` | `{supported: false, external_flow_count: 0, return_method: "simple"}` |
| `price_basis` | `{source, dividends_included: false, corporate_actions_adjusted: "not_verified"}` |
| `warnings[]` | `{kind, …값, message}` (5절) |
| `calc_version`, `locale`, `generated_at`, `disclaimer` | |

### 3-2. `series.points[]`(그래프용)

`{date, total_asset, cash_balance, eval_amount, daily_return_pct, cumulative_return_pct, portfolio_index, estimated, estimated_tickers}`

- `cumulative_return_pct`는 그 기간 시작 금액 대비 수익률이고, `portfolio_index`는 시작 = 100으로 맞춘 값입니다.
- 점이 400개를 넘으면 고르게 줄입니다. 이때 `sampled: true`이고 `total_points`가 원래 점 수입니다. 첫·끝·최고·최저·최대 낙폭 고점과 저점은 남깁니다.
- `estimated: true`인 점은 점선이나 흐린 점으로 구분해 주세요.

### 3-3. `comparison`

| 필드 | 설명 |
| --- | --- |
| `status` | `ok` / `partial`(지수 빠진 날 또는 포트폴리오 추정값) / `no_data` / `insufficient_overlap` / `portfolio_unavailable` |
| `message` | ok가 아니면 안내 |
| `start_date`, `end_date`, `common_days`, `start_aligned` | 비교 구간 |
| `benchmark_start_value`, `benchmark_end_value`, `benchmark_last_date`, `benchmark_lag_business_days` | 지수 값과 기준일 |
| `portfolio_return_pct`, `benchmark_return_pct`, `excess_return_pp` | 비교 구간 수익률과 초과 수익률(%p) |
| `points[]` | `{date, portfolio_index, benchmark_index, benchmark_close, portfolio_return_pct, benchmark_return_pct, excess_return_pp, estimated}` |

**`comparison.status`가 `ok`·`partial`일 때만** 수익률 차이를 보여 주세요. `partial`이면 "참고용" 배지를 붙입니다.

## 4. 응답 예시

### 4-1. 정상(실제 dev DB 시세, 3개월, 코스피)

임시 계정에 과거 날짜 체결 4건을 넣고 만든 결과입니다(점 목록은 앞 2개와 끝 1개만 남겼습니다).
- 체결: 삼성전자 10주 매수(9/1), KODEX 200 20주 매수(9/15), 삼성전자 4주 매도(10/15), SK하이닉스 1주 매수(10/20)
- 각 체결가는 그날 DB 종가입니다.
- 요청 시작(8/3)이 첫 체결 전이라 8/31부터 계산했습니다.
- 시세가 11-03까지라 3영업일 지연 경고가 붙었습니다.
- 지수 수익률은 SQL로 따로 계산한 값과 같습니다.

```json
{
  "generated_at": "2026-11-06T15:35:22.938111+09:00",
  "calc_version": "performance.v2",
  "locale": "ko-KR",
  "status": "ok",
  "message": null,
  "history_verified": true,
  "period": "3m",
  "requested_start_date": "2026-08-03",
  "start_date": "2026-08-31",
  "end_date": "2026-11-03",
  "data_date": "2026-11-03",
  "expected_date": "2026-11-06",
  "data_lag_business_days": 3,
  "initial_balance": 10000000.0,
  "benchmark_code": "KS11",
  "benchmark_name": "코스피",
  "summary": {
    "current_cash": 4947460.0,
    "start_value": 10000000.0,
    "end_value": 9479960.0,
    "profit_loss": -520040.0,
    "period_return_pct": -5.2,
    "cumulative_return_pct": -5.2,
    "eval_amount": 4532500.0,
    "cash_balance": 4947460.0
  },
  "metrics": {
    "highest": {
      "value": 10432140.0,
      "date": "2026-09-23"
    },
    "lowest": {
      "value": 9120480.0,
      "date": "2026-10-15"
    },
    "max_drawdown": {
      "pct": -12.57,
      "peak_date": "2026-09-23",
      "trough_date": "2026-10-15",
      "recovery_date": null,
      "duration_days": 12
    },
    "daily_volatility_pct": 1.6129,
    "annualized_volatility_pct": 25.6044,
    "annualized_return_pct": null,
    "daily_return_count": 42,
    "trading_days": 43,
    "sample_rules": {
      "volatility_min_returns": 20,
      "annualized_return_min_returns": 252
    }
  },
  "series": {
    "points": [
      {
        "date": "2026-08-31",
        "total_asset": 10000000.0,
        "cash_balance": 10000000.0,
        "eval_amount": 0.0,
        "daily_return_pct": null,
        "cumulative_return_pct": 0.0,
        "portfolio_index": 100.0,
        "estimated": false,
        "estimated_tickers": []
      },
      {
        "date": "2026-09-01",
        "total_asset": 10000000.0,
        "cash_balance": 7390000.0,
        "eval_amount": 2610000.0,
        "daily_return_pct": 0.0,
        "cumulative_return_pct": 0.0,
        "portfolio_index": 100.0,
        "estimated": false,
        "estimated_tickers": []
      },
      {
        "date": "2026-11-03",
        "total_asset": 9479960.0,
        "cash_balance": 4947460.0,
        "eval_amount": 4532500.0,
        "daily_return_pct": -1.93,
        "cumulative_return_pct": -5.2,
        "portfolio_index": 94.7996,
        "estimated": false,
        "estimated_tickers": []
      }
    ],
    "total_points": 43,
    "sampled": false,
    "max_points": 400
  },
  "comparison": {
    "benchmark_code": "KS11",
    "benchmark_name": "코스피",
    "status": "ok",
    "start_date": "2026-08-31",
    "end_date": "2026-11-03",
    "common_days": 43,
    "benchmark_start_value": 6820.02,
    "benchmark_end_value": 5637.63,
    "benchmark_last_date": "2026-11-03",
    "benchmark_lag_business_days": 3,
    "portfolio_return_pct": -5.2,
    "benchmark_return_pct": -17.34,
    "excess_return_pp": 12.14,
    "start_aligned": false,
    "points": [
      {
        "date": "2026-08-31",
        "portfolio_index": 100.0,
        "benchmark_index": 100.0,
        "benchmark_close": 6820.02,
        "portfolio_return_pct": 0.0,
        "benchmark_return_pct": 0.0,
        "excess_return_pp": 0.0,
        "estimated": false
      },
      {
        "date": "2026-09-01",
        "portfolio_index": 100.0,
        "benchmark_index": 100.2314,
        "benchmark_close": 6835.8,
        "portfolio_return_pct": 0.0,
        "benchmark_return_pct": 0.23,
        "excess_return_pp": -0.23,
        "estimated": false
      },
      {
        "date": "2026-11-03",
        "portfolio_index": 94.7996,
        "benchmark_index": 82.663,
        "benchmark_close": 5637.63,
        "portfolio_return_pct": -5.2,
        "benchmark_return_pct": -17.34,
        "excess_return_pp": 12.14,
        "estimated": false
      }
    ],
    "total_points": 43,
    "sampled": false,
    "message": null
  },
  "estimated_dates": [],
  "cash_flows": {
    "supported": false,
    "external_flow_count": 0,
    "return_method": "simple"
  },
  "price_basis": {
    "source": "stock_price.close_price",
    "dividends_included": false,
    "corporate_actions_adjusted": "not_verified"
  },
  "warnings": [
    {
      "kind": "data_delayed",
      "date": "2026-11-03",
      "expected": "2026-11-06",
      "lag": 3,
      "message": "마지막 종가는 2026-11-03이며 기대 거래일(2026-11-06)보다 3영업일 지연됐습니다. 오늘 시장 상황이 아닙니다."
    },
    {
      "kind": "period_before_inception",
      "requested": "2026-08-03",
      "start": "2026-08-31",
      "message": "요청한 기간 시작(2026-08-03)이 첫 체결 전이라 2026-08-31부터 계산했습니다."
    }
  ],
  "disclaimer": "거래일 종가 기준의 가격 수익률입니다. 배당·분배금은 포함하지 않고, 분할·병합 등 기업행사를 따로 보정하지 않습니다. 외부 입출금 기능이 없어 단순 수익률이며 시간가중수익률과 같습니다. 과거 성과는 미래 성과를 보장하지 않으며 매수·매도 권유가 아닙니다. 이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
}
```

### 4-2. 빈 데이터(거래 없음)

```json
{
  "generated_at": "2026-11-06T16:00:00+09:00",
  "calc_version": "performance.v2",
  "period": "all",
  "benchmark_code": "KS11",
  "benchmark_name": "코스피",
  "data_date": "2026-10-30",
  "expected_date": "2026-10-30",
  "data_lag_business_days": 0,
  "initial_balance": 10000000.0,
  "cash_flows": {
    "supported": false,
    "external_flow_count": 0,
    "return_method": "simple"
  },
  "price_basis": {
    "source": "stock_price.close_price",
    "dividends_included": false,
    "corporate_actions_adjusted": "not_verified"
  },
  "warnings": [],
  "status": "no_trades",
  "requested_start_date": null,
  "start_date": null,
  "end_date": "2026-10-30",
  "summary": {
    "current_cash": 10000000.0,
    "start_value": null,
    "end_value": null,
    "profit_loss": null,
    "period_return_pct": null,
    "cumulative_return_pct": null
  },
  "metrics": {
    "highest": null,
    "lowest": null,
    "max_drawdown": null,
    "daily_volatility_pct": null,
    "annualized_volatility_pct": null,
    "annualized_return_pct": null,
    "daily_return_count": 0,
    "trading_days": 0,
    "sample_rules": {
      "volatility_min_returns": 20,
      "annualized_return_min_returns": 252
    }
  },
  "series": {
    "points": [],
    "total_points": 0,
    "sampled": false,
    "max_points": 400
  },
  "comparison": {
    "status": "portfolio_unavailable",
    "points": [],
    "benchmark_name": "코스피",
    "message": "포트폴리오 성과를 계산하지 못해 지수와 비교하지 않았습니다."
  },
  "estimated_dates": [],
  "history_verified": true,
  "locale": "ko-KR",
  "message": "체결된 주문이 없어 성과를 계산할 수 없습니다. 주문이 체결되면 거래일 종가 기준으로 계산합니다.",
  "disclaimer": "거래일 종가 기준의 가격 수익률입니다. 배당·분배금은 포함하지 않고, 분할·병합 등 기업행사를 따로 보정하지 않습니다. 외부 입출금 기능이 없어 단순 수익률이며 시간가중수익률과 같습니다. 과거 성과는 미래 성과를 보장하지 않으며 매수·매도 권유가 아닙니다. 이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
}
```

### 4-3. 계산 불가(이력 불일치)

체결 재생 현금(9,000,000원)과 실제 현금(8,500,000원)이 달라서 수익률·그래프를 주지 않습니다.

```json
{
  "generated_at": "2026-11-06T16:00:00+09:00",
  "calc_version": "performance.v2",
  "period": "3m",
  "benchmark_code": "KS11",
  "benchmark_name": "코스피",
  "data_date": "2026-10-30",
  "expected_date": "2026-10-30",
  "data_lag_business_days": 0,
  "initial_balance": 10000000.0,
  "cash_flows": {
    "supported": false,
    "external_flow_count": 0,
    "return_method": "simple"
  },
  "price_basis": {
    "source": "stock_price.close_price",
    "dividends_included": false,
    "corporate_actions_adjusted": "not_verified"
  },
  "warnings": [
    {
      "kind": "history_mismatch",
      "parts": [
        "cash"
      ],
      "replayed_cash": 9000000.0,
      "actual_cash": 8500000.0,
      "message": "체결 내역을 다시 계산한 현금(9,000,000원)과 현재 현금(8,500,000원) 또는 보유 수량이 맞지 않습니다."
    }
  ],
  "status": "history_unavailable",
  "requested_start_date": null,
  "start_date": null,
  "end_date": "2026-10-30",
  "summary": {
    "current_cash": 8500000.0,
    "start_value": null,
    "end_value": null,
    "profit_loss": null,
    "period_return_pct": null,
    "cumulative_return_pct": null
  },
  "metrics": {
    "highest": null,
    "lowest": null,
    "max_drawdown": null,
    "daily_volatility_pct": null,
    "annualized_volatility_pct": null,
    "annualized_return_pct": null,
    "daily_return_count": 0,
    "trading_days": 0,
    "sample_rules": {
      "volatility_min_returns": 20,
      "annualized_return_min_returns": 252
    }
  },
  "series": {
    "points": [],
    "total_points": 0,
    "sampled": false,
    "max_points": 400
  },
  "comparison": {
    "status": "portfolio_unavailable",
    "points": [],
    "benchmark_name": "코스피",
    "message": "포트폴리오 성과를 계산하지 못해 지수와 비교하지 않았습니다."
  },
  "estimated_dates": [],
  "history_verified": false,
  "locale": "ko-KR",
  "message": "체결 내역으로 현재 계좌를 다시 만들 수 없어(현금 또는 보유 수량 불일치) 과거 성과를 계산하지 않았습니다. 현재 보유를 과거에 소급해 수익률을 만들지 않습니다.",
  "disclaimer": "거래일 종가 기준의 가격 수익률입니다. 배당·분배금은 포함하지 않고, 분할·병합 등 기업행사를 따로 보정하지 않습니다. 외부 입출금 기능이 없어 단순 수익률이며 시간가중수익률과 같습니다. 과거 성과는 미래 성과를 보장하지 않으며 매수·매도 권유가 아닙니다. 이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."
}
```

### 4-4. 가격 지연

4-1의 `data_lag_business_days: 3`과 `warnings[0]`(`data_delayed`)가 지연 응답입니다. 값은 계산하지만 오늘 시장이 아니라는 문장이 함께 옵니다.

## 5. 경고 `warnings[].kind`

| kind | 뜻 | 화면 |
| --- | --- | --- |
| `data_delayed` | 마지막 종가가 기대 거래일보다 늦음 | 상단 노란 띠 |
| `history_mismatch` | 체결 재생과 계좌 불일치(`parts`: cash·holdings·unreplayable_trades) | 계산 불가 화면 |
| `pending_trades` | 마지막 종가일 뒤 체결(아직 반영 안 됨) | 작은 안내 |
| `period_before_inception` | 요청 시작이 첫 체결 전이라 시작일을 늦춤 | 기간 선택 옆 안내 |
| `estimated_prices` | 직전 종가로 평가한 날이 있음 | "추정값" 배지 |
| `price_discontinuity` | 보유 종목 종가가 하루에 ±30% 넘게 변함(분할·병합 가능성) | 경고 목록 |
| `benchmark_no_data` / `benchmark_insufficient_overlap` / `benchmark_missing_days` / `benchmark_start_aligned` | 지수 비교 관련 | 비교 카드 안내 |

## 6. 다국어

- 숫자·지수 코드·날짜·상태 코드는 모든 언어에서 같습니다(테스트로 확인).
- 문장은 `message`, `comparison.message`, `warnings[].message`, `disclaimer`이고, `benchmark_name`도 언어별(코스피/KOSPI)로 옵니다.
- 화면 라벨은 `docs/i18n/{locale}.json`의 `performance` 묶음입니다.
  - 일반 라벨: `total_asset`, `period_return`, `cumulative_return`, `benchmark_return`, `excess_return`, `max_drawdown`, `estimated_badge` …
  - 코드 라벨: `period.*`, `status.*`, `benchmark_status.*`, `index.*`

## 7. 로딩·오류·빈 화면

| 상황 | 처리 |
| --- | --- |
| 로딩 | 보통 0.5초 이내(DB만 읽음) |
| 401 / 422 | 로그인 화면 / 잘못된 파라미터 |
| `no_account` | "첫 주문 후 성과가 계산됩니다" + 모의투자 이동 |
| `no_trades` | 같은 안내. 현재 현금(`summary.current_cash`)만 표시 |
| `pending_only` | "다음 거래일 종가 수집 후 반영" |
| `no_prices` | "시세가 없어 계산할 수 없습니다" |
| `history_unavailable` | 그래프 영역에 "거래 이력으로 과거 성과를 복원할 수 없습니다"(`message`). 0% 그래프를 그리지 마세요 |
| `partial` | 그래프는 그리고 추정 점을 구분, "일부 추정" 배지 |
| `comparison.status`가 ok·partial이 아님 | 비교 카드에 `comparison.message`만 |
| 지표 null | "데이터 부족"(`not_enough_data`) |

## 8. 추천 화면 구성

1. **상단**: 기간 탭(1개월·3개월·6개월·1년·전체), 비교 지수 선택(코스피·코스닥), 데이터 기준일, 지연 띠
2. **요약 카드**: 총자산(`summary.end_value`), 기준 금액(`start_value`), 손익, 기간 수익률, 누적 수익률
3. **비교 카드**: 내 수익률 / 지수 수익률 / 초과 수익률(%p). 양수·음수 색만 다르게 하고 "앞으로도…" 같은 설명은 붙이지 않습니다.
4. **그래프**: `comparison.points`의 `portfolio_index`와 `benchmark_index` 두 선(시작 100), 보조로 `excess_return_pp` 막대. 금액 그래프가 필요하면 `series.points`의 `total_asset`을 씁니다. 정규화 지수와 금액을 한 축에 섞지 마세요.
5. **지표**: 최고·최저 총자산, 최대 낙폭(기간·회복일), 변동성(없으면 "데이터 부족")
6. **경고 목록**과 **면책**

## 9. 한계

- **수익률 범위:** 배당·ETF 분배금을 포함하지 않는 가격 수익률입니다. 분할·병합·증자 보정은 하지 않고, 하루 ±30% 초과 변동만 경고합니다. 수집 시세가 수정주가인지는 이 기능에서 확인하지 않았습니다(`corporate_actions_adjusted: "not_verified"`).
- **비교 지수:** KOSPI200 등 다른 지수는 데이터가 없어 지원하지 않습니다.
- **수수료·세금·입출금:** 기능이 없어서 반영할 것이 없습니다. 기능이 생기면 계산 방식을 다시 정해야 합니다.
- **계좌 초기화:** 초기화하면 그 전 성과는 사라집니다(체결 내역이 지워짐).
- **추정값:** 시세가 빠진 날은 직전 종가로 추정하고 표시만 합니다.
- **소급 변경:** 일별 스냅샷을 저장하지 않고 체결 재생으로 매번 계산합니다. 과거 시세가 나중에 수정되면 과거 곡선도 바뀝니다.
- **실시간 아님:** 장중 실시간 평가가 아니라 거래일 종가 기준입니다.
