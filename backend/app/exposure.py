"""포트폴리오 실질 노출: 보유 개별 종목과 보유 ETF의 구성종목을 합쳐 기업별로 실제로 얼마나 노출돼 있는지 계산한다(DB 없음, LLM 없음).

입력은 평범한 값이다(보유 목록과 최신 종가, ETF별 구성종목). DB 조회는 app/exposure_db.py가 한다.
모든 문구는 관찰 사실만 담는다(투자 권유 없음). 금지 표현 검사는 tests/test_exposure.py가 한다.

규칙
- 보유 평가금액 = round(수량 x 최신 종가, 2). 종가가 없는 보유는 평가에서 빼고 data_warnings에 남긴다. 비중(%)의 분모는 이 평가금액 합계다
  (현금은 넣지 않는다: 계좌 총자산 대비가 아니라 보유 평가금액 대비다).
- 개별 주식은 평가금액 전부가 그 기업의 직접 노출이다.
- 구성종목 데이터가 있는 ETF는 평가금액 x 구성종목 비중% / 100을 그 구성종목에 배분하고(ETF 경유 노출), 같은 기업의 직접 노출과 합산한다.
  기업은 company와 연결된 종목코드로 묶는다. 코드가 없거나 company에 없는 구성종목(해외 종목·선물 등)은 이름(공백·대소문자 무시)으로 묶고 linked=False로 표시한다.
- 양(+) 비중만 기업에 배분한다. 그 ETF 평가금액에서 배분한 금액을 뺀 나머지가 "기타(현금·파생 등)"다. 양의 비중 합계가 100%보다 작으면 기타는 양수,
  100%보다 크면 기타가 음수로 잡힌다(매도 옵션·스왑 등이 음수 비중으로 공시되는 경우 등). 합계가 100.5%를 넘거나 95% 아래면 data_warnings를 낸다.
- 분해하지 않는 ETF: 구성종목 데이터가 없는 ETF, 레버리지·인버스 ETF(이름으로 판별, app.etf.leverage_kind). 레버리지·인버스는 배수·선물·스왑 때문에 비중을 그대로
  곱하면 실제 노출을 오해하게 하므로 구성종목 데이터가 있어도 분해하지 않는다. 이 ETF들은 평가금액과 이유를 따로 모은다.
- 합계 불변식: 모든 기업의 합계 금액 + 기타 + 분해하지 않은 ETF = 전체 보유 평가금액. 계산은 반올림하지 않은 값으로 하고 출력만 소수 둘째 자리로
  반올림하므로, 반올림한 항목을 더한 값과 전체 평가금액의 차이는 항목 수 x 0.005원 이내다(ROUNDING_TOLERANCE_PER_ITEM, tests/test_exposure.py가 보장).
- 플래그: 실질 비중이 CONCENTRATION_PCT(40%, app.briefing의 집중 기준과 같은 값) 이상인 기업, ETF 경유 때문에 실질 비중이 직접 비중보다 10%p 이상 커진 기업.
"""

from dataclasses import dataclass, field
from datetime import date

from app.data_status import is_stale
from app.disclaimer import DISCLAIMER
from app.etf import leverage_kind
from app.korean import josa

CONCENTRATION_PCT = 40.0
ETF_GAP_PCT = 10.0  # ETF 경유 노출이 이 %p 이상이면 플래그
WEIGHT_OVER_PCT = 100.5  # 양의 비중 합계가 이를 넘으면 경고(공시 반올림 오차보다 큰 초과)
WEIGHT_UNDER_PCT = 95.0  # 이 아래면 나머지를 기타로 두었다고 경고
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
ROUNDING_TOLERANCE_PER_ITEM = 0.005

MSG_NO_HOLDINGS = "보유 중인 종목이 없습니다. 모의투자로 종목이나 ETF를 매수하면 기업별 실질 노출을 계산합니다."
MSG_NO_PRICES = "보유 종목의 최신 종가를 구하지 못해 실질 노출을 계산할 수 없습니다."
EXPOSURE_NOTE = (
    "실질 노출은 보유 평가금액(종가 기준)과 ETF 구성종목 비중으로 계산한 참고 값이며 투자 판단 보조용입니다. "
    "구성종목과 비중은 운용사가 공시한 기준일의 값이라 현재와 다를 수 있습니다."
)

REASON_NO_DATA = "no_holdings_data"
REASON_LEVERAGED = "leveraged_inverse"
REASON_TEXT = {
    REASON_NO_DATA: "구성종목 데이터가 없어 분해하지 못했습니다.",
    REASON_LEVERAGED: "레버리지·인버스 ETF는 배수와 선물·스왑 구조 때문에 구성종목 비중을 그대로 곱하면 실제 노출을 오해하게 해서 분해하지 않았습니다.",
}


@dataclass(frozen=True)
class HoldingIn:
    ticker: str
    name: str
    asset_type: str  # "stock" | "etf"
    quantity: int
    price: float | None  # 최신 종가. 없으면 None
    price_date: date | None = None


@dataclass(frozen=True)
class ConstituentIn:
    name: str
    code: str | None  # 운용사가 준 식별자 그대로(없으면 None)
    weight_pct: float
    linked_ticker: str | None = None  # code가 company.ticker와 일치할 때 그 종목코드(DB 계층이 채운다)
    linked_name: str | None = None


@dataclass
class EtfIn:
    ticker: str
    name: str
    as_of: date | None
    constituents: list[ConstituentIn] = field(default_factory=list)


@dataclass
class _Company:
    key: tuple
    name: str
    code: str | None
    linked: bool
    direct: float = 0.0
    via: dict = field(default_factory=dict)  # etf_ticker -> [etf_name, amount, weight_pct 합]

    @property
    def via_total(self) -> float:
        return sum(v[1] for v in self.via.values())


def _r(v: float | None, n: int = 2) -> float | None:
    return None if v is None else round(v, n)


def _pct(part: float, total: float) -> float:
    return round(part / total * 100, 2) if total > 0 else 0.0


def _name_key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def empty_result(message: str, today: date) -> dict:
    return {
        "message": message,
        "valuation_date": None,
        "summary": {"total_eval_amount": 0.0, "holding_count": 0, "stock_amount": 0.0, "etf_amount": 0.0,
                    "decomposed_etf_amount": 0.0, "undecomposed_etf_amount": 0.0, "companies_amount": 0.0,
                    "other_amount": 0.0, "other_weight_pct": 0.0, "company_count": 0},
        "companies": [], "overlaps": [], "decomposed_etfs": [], "undecomposed_etfs": [],
        "flags": [], "data_warnings": [], "etf_holdings_stale": False, "limit": DEFAULT_LIMIT,
        "disclaimer": f"{EXPOSURE_NOTE} {DISCLAIMER}",
    }


def compute_exposure(holdings: list[HoldingIn], etfs: dict[str, EtfIn], today: date, limit: int = DEFAULT_LIMIT) -> dict:
    """기업별 실질 노출. etfs는 ETF 종목코드 -> 구성종목(분해할 수 있는 ETF만 DB 계층이 담는다; 없으면 데이터 없음으로 본다)."""
    if not holdings:
        out = empty_result(MSG_NO_HOLDINGS, today)
        out["limit"] = limit
        return out

    warnings: list[dict] = []
    priced: list[tuple[HoldingIn, float]] = []
    for h in holdings:
        if h.price is None or h.price <= 0 or h.quantity <= 0:
            warnings.append({"kind": "no_price", "message": f"{h.name}({h.ticker})의 최신 종가가 없어 실질 노출 계산에서 뺐습니다."})
            continue
        priced.append((h, h.quantity * h.price))
    if not priced:
        out = empty_result(MSG_NO_PRICES, today)
        out["limit"] = limit
        out["summary"]["holding_count"] = len(holdings)
        out["data_warnings"] = warnings
        return out

    total = sum(v for _, v in priced)
    dates = [h.price_date for h, _ in priced if h.price_date]
    valuation_date = max(dates) if dates else None
    for h, _ in priced:
        if valuation_date and h.price_date and h.price_date < valuation_date:
            warnings.append({"kind": "older_price", "message": f"{h.name}({h.ticker})의 최신 종가는 {h.price_date.isoformat()} 기준입니다(다른 종목은 {valuation_date.isoformat()})."})

    companies: dict[tuple, _Company] = {}
    stock_amount = etf_amount = decomposed_amount = undecomposed_amount = other = 0.0
    decomposed: list[dict] = []
    undecomposed: list[dict] = []

    def company_for(key: tuple, name: str, code: str | None, linked: bool) -> _Company:
        c = companies.get(key)
        if c is None:
            c = companies[key] = _Company(key, name, code, linked)
        return c

    for h, value in priced:
        if h.asset_type != "etf":
            stock_amount += value
            company_for(("code", h.ticker), h.name, h.ticker, True).direct += value
            continue
        etf_amount += value
        etf = etfs.get(h.ticker)
        reason = REASON_LEVERAGED if leverage_kind(h.name) else (REASON_NO_DATA if etf is None or not etf.constituents else None)
        if reason:
            undecomposed_amount += value
            undecomposed.append({"ticker": h.ticker, "name": h.name, "eval_amount": value, "weight_pct": _pct(value, total),
                                 "reason_code": reason, "reason": REASON_TEXT[reason]})
            continue

        decomposed_amount += value
        allocated = 0.0
        pos_sum = 0.0
        for c in etf.constituents:
            if c.weight_pct <= 0:
                continue
            pos_sum += c.weight_pct
            amount = value * c.weight_pct / 100
            allocated += amount
            if c.linked_ticker:
                co = company_for(("code", c.linked_ticker), c.linked_name or c.name, c.linked_ticker, True)
            else:
                co = company_for(("name", _name_key(c.name)), c.name, c.code, False)
            slot = co.via.setdefault(h.ticker, [h.name, 0.0, 0.0])
            slot[1] += amount
            slot[2] += c.weight_pct
        other += value - allocated
        stale = is_stale(etf.as_of, today)
        decomposed.append({
            "ticker": h.ticker, "name": h.name, "eval_amount": value, "weight_pct": _pct(value, total),
            "holdings_as_of": etf.as_of.isoformat() if etf.as_of else None, "stale": stale,
            "stale_notice": (f"{h.name}의 구성종목 기준일은 {etf.as_of.isoformat()}입니다." if etf.as_of else f"{h.name}의 구성종목 기준일을 알 수 없습니다.") if stale else None,
            "constituent_count": len(etf.constituents), "weight_sum_pct": round(pos_sum, 2),
        })
        if pos_sum > WEIGHT_OVER_PCT:
            warnings.append({"kind": "etf_weight_over_100", "message": (
                f"{h.name}의 구성종목 양(+) 비중 합계가 {pos_sum:.2f}%로 100%를 넘습니다. 배분은 공시 비중 그대로 하고 "
                f"초과분만큼 '기타'가 음수({_r(value - allocated)}원)로 잡힙니다(매도 옵션·스왑 등 음수 비중 포지션이 있을 수 있습니다).")})
        elif pos_sum < WEIGHT_UNDER_PCT:
            warnings.append({"kind": "etf_weight_under_100", "message": (
                f"{h.name}의 구성종목 비중 합계가 {pos_sum:.2f}%입니다. 나머지 {100 - pos_sum:.2f}%({_r(value - allocated)}원)는 '기타(현금·파생 등)'로 뒀습니다.")})

    ordered = sorted(companies.values(), key=lambda c: (-(c.direct + c.via_total), c.name))
    items = [_item(c, total) for c in ordered]
    for n, it in enumerate(items, 1):
        it["rank"] = n

    flags = []
    for c in ordered:
        eff = _pct(c.direct + c.via_total, total)
        via_pct = _pct(c.via_total, total)
        if (c.direct + c.via_total) / total * 100 >= CONCENTRATION_PCT:
            flags.append({"kind": "concentration", "name": c.name, "code": c.code, "value_pct": eff, "threshold_pct": CONCENTRATION_PCT,
                          "message": f"{c.name}의 실질 비중이 보유 평가금액의 {eff:.2f}%입니다(기준 {CONCENTRATION_PCT:g}% 이상)."})
        if c.via_total / total * 100 >= ETF_GAP_PCT:
            flags.append({"kind": "etf_inflates_weight", "name": c.name, "code": c.code, "value_pct": via_pct, "threshold_pct": ETF_GAP_PCT,
                          "message": f"{josa(c.name, '은/는')} ETF를 통한 노출 때문에 실질 비중({eff:.2f}%)이 직접 비중({_pct(c.direct, total):.2f}%)보다 "
                                     f"{via_pct:.2f}%p 큽니다(기준 {ETF_GAP_PCT:g}%p 이상)."})

    limit = max(1, min(limit, MAX_LIMIT))
    overlaps = [it for it in items if it["direct_amount"] > 0 and it["via_etf_amount"] > 0]
    companies_amount = sum(c.direct + c.via_total for c in companies.values())
    for d in decomposed:
        d["eval_amount"] = _r(d["eval_amount"])
    for u in undecomposed:
        u["eval_amount"] = _r(u["eval_amount"])

    return {
        "message": None,
        "valuation_date": valuation_date.isoformat() if valuation_date else None,
        "summary": {
            "total_eval_amount": _r(total), "holding_count": len(priced), "stock_amount": _r(stock_amount), "etf_amount": _r(etf_amount),
            "decomposed_etf_amount": _r(decomposed_amount), "undecomposed_etf_amount": _r(undecomposed_amount),
            "companies_amount": _r(companies_amount), "other_amount": _r(other), "other_weight_pct": round(other / total * 100, 2),
            "company_count": len(items),
        },
        "companies": items[:limit], "overlaps": overlaps, "decomposed_etfs": decomposed, "undecomposed_etfs": undecomposed,
        "flags": flags, "data_warnings": warnings, "etf_holdings_stale": any(d["stale"] for d in decomposed), "limit": limit,
        "disclaimer": f"{EXPOSURE_NOTE} {DISCLAIMER}",
    }


def _item(c: _Company, total: float) -> dict:
    amount = c.direct + c.via_total
    via = [{"ticker": t, "name": v[0], "amount": _r(v[1]), "etf_weight_pct": round(v[2], 2)}
           for t, v in sorted(c.via.items(), key=lambda kv: -kv[1][1])]
    return {
        "name": c.name, "code": c.code, "linked": c.linked,
        "direct_amount": _r(c.direct), "via_etf_amount": _r(c.via_total), "via_etfs": via, "total_amount": _r(amount),
        "effective_weight_pct": _pct(amount, total), "direct_weight_pct": _pct(c.direct, total),
    }
