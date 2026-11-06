"""ETF 실질 노출(look-through)과 포트폴리오 집중도(2026-11-06). DB·LLM 없이 평범한 값만 받아 dict를 만든다.

기존 app/exposure.compute_exposure(GET /api/portfolio/exposure 기본 응답)는 그대로 둔다. 그 계산은 보유 평가액 대비 비중이고,
다른 ETF를 담은 ETF의 구성종목(예: TIGER 배당커버드콜액티브 안의 TIGER 200)을 "기업"처럼 합쳤다. 이 모듈은 같은 입력 형식
(exposure.HoldingIn·EtfIn·ConstituentIn)으로 다음 규칙의 확장판을 계산한다(GET /api/portfolio/exposure?view=look_through, 진단 저장본).

계산 규칙(calc_version = CALC_VERSION)
- 평가액 = 수량 x 최신 종가(DB 종가, 진단에서는 진단이 쓴 현재가). 종가가 없으면 평가 불가로 따로 센다(0원으로 치지 않는다).
- 총자산 = 현금 + 평가 가능한 직접 주식 + 평가 가능한 ETF. ETF 구성종목 금액을 총자산에 다시 더하지 않는다.
- 직접 노출 = 주식 평가액. ETF 간접 노출 = ETF 평가액 x 구성종목 비중% / 100(양수 비중만). 같은 기업은 여러 ETF·직접 보유를 합산한다.
- 기업별 실질 노출 = 직접 + 간접, 비중 = 실질 노출 / 총자산 x 100. 합계를 100%에 맞추지 않는다.
- ETF 평가액 = 기업 배분액 + 중첩 ETF 배분액 + 잔여(미확인, 비중 합계가 100%보다 작으면 양수, 크면 음수) — 분해한 ETF.
  분해하지 않은 ETF(레버리지·인버스, 구성 데이터 없음, 비중 데이터 오류)는 평가액 전체가 미확인이다.
- 중첩 ETF: 구성종목이 ETF(company.asset_type = 'etf')면 재귀로 분해하지 않고 "중첩 ETF(미지원)" 금액으로 따로 둔다(순환·중복 집계 없음).
- 레버리지·인버스: 구성종목이 있어도 분해하지 않는다. 공시 구성 비중은 참고(constituents)로만 보이고 금액은 매기지 않는다(배수를 곱하지 않는다).
- 데이터 품질: null·0·음수 비중, 한 종목 비중 100% 초과(→ 분해하지 않음), 같은 종목 중복 행(합산하고 경고), 비중 합계 95% 미만·100.5% 초과(정규화하지 않음),
  company와 연결되지 않은 구성종목(이름으로 묶음), 오래된 구성 기준일. 데이터를 고치지 않고 경고로 알린다.
- 업종: company.sector가 있는 기업의 실질 노출이 기업 노출 합계의 SECTOR_AVAILABLE_PCT 이상이면 available, 0보다 크면 partial, 없으면 unavailable.
문장은 app/look_through_i18n이 언어별로 붙인다(숫자는 한 번만 계산).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.data_status import business_days_behind, is_stale
from app.etf import leverage_kind
from app.exposure import ConstituentIn, EtfIn, HoldingIn, WEIGHT_OVER_PCT, WEIGHT_UNDER_PCT, _name_key

CALC_VERSION = "look_through.v1"
CONCENTRATION_PCT = 40.0  # 기업 실질 비중(총자산 대비) 기준. exposure·briefing·diagnosis와 같은 값
ETF_GAP_PCT = 10.0  # ETF 경유 비중(총자산 대비)이 이 %p 이상이면 플래그
SINGLE_ETF_PCT = 10.0  # 한 ETF를 통한 한 기업 노출이 총자산의 이 % 이상이면 플래그
SECTOR_AVAILABLE_PCT = 95.0
TOP_CONSTITUENTS = 10
DEFAULT_LIMIT, MAX_LIMIT = 10, 50

STATUS_DECOMPOSED, STATUS_NOT_DECOMPOSED = "decomposed", "not_decomposed"
REASON_NO_DATA, REASON_LEVERAGED, REASON_INVALID = "no_holdings_data", "leveraged_inverse", "invalid_weights"
KIND_COMPANY, KIND_NESTED = "company", "nested_etf"


@dataclass
class _Co:
    key: tuple
    name: str
    code: str | None
    ticker: str | None
    linked: bool
    sector: str | None = None
    direct: float = 0.0
    via: dict = field(default_factory=dict)  # etf ticker -> [etf name, amount, weight 합]

    @property
    def via_total(self) -> float:
        return sum(v[1] for v in self.via.values())


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _pct(part: float, total: float) -> float | None:
    return round(part / total * 100, 2) if total > 0 else None


def _dup_key(c: ConstituentIn) -> tuple:
    """중복 행 판정 키: 연결 종목코드 > 운용사 코드 > 이름. 같은 발행사 이름의 서로 다른 증권(CP·전단채 ISIN이 다름)은 중복이 아니다."""
    if c.linked_ticker:
        return ("ticker", c.linked_ticker)
    if c.code:
        return ("code", c.code)
    return ("name", _name_key(c.name))


def _quality(cons: list[ConstituentIn], self_ticker: str) -> dict:
    """구성종목 데이터 점검 값(경고 판단과 응답의 품질 필드)."""
    seen: dict[tuple, int] = {}
    for c in cons:
        seen[_dup_key(c)] = seen.get(_dup_key(c), 0) + 1
    pos = [c.weight_pct for c in cons if c.weight_pct is not None and c.weight_pct > 0]
    neg = [c.weight_pct for c in cons if c.weight_pct is not None and c.weight_pct < 0]
    return {
        "constituent_count": len(cons),
        "null_weight_count": sum(1 for c in cons if c.weight_pct is None),
        "zero_weight_count": sum(1 for c in cons if c.weight_pct == 0),
        "negative_weight_count": len(neg),
        "negative_weight_sum_pct": round(sum(neg), 4),
        "over_100_count": sum(1 for c in cons if c.weight_pct is not None and c.weight_pct > 100),
        "duplicate_count": sum(n - 1 for n in seen.values() if n > 1),
        "positive_weight_sum_pct": round(sum(pos), 4),
        "linked_count": sum(1 for c in cons if c.linked_ticker and not _nested(c, self_ticker)),
        "unlinked_count": sum(1 for c in cons if not c.linked_ticker),
        "nested_etf_count": sum(1 for c in cons if _nested(c, self_ticker)),
    }


def _nested(c: ConstituentIn, self_ticker: str) -> bool:
    return bool(c.linked_ticker) and (c.linked_asset_type == "etf" or c.linked_ticker == self_ticker)


def _preview(cons: list[ConstituentIn], self_ticker: str, value: float | None) -> list[dict]:
    """비중 큰 순 상위 TOP_CONSTITUENTS(양수 비중). value가 있으면 금액도(분해한 ETF), 없으면 null(분해하지 않은 ETF)."""
    top = sorted((c for c in cons if c.weight_pct is not None and c.weight_pct > 0), key=lambda c: (-c.weight_pct, c.name))[:TOP_CONSTITUENTS]
    return [{"name": c.linked_name or c.name, "code": c.code, "linked_ticker": c.linked_ticker, "weight_pct": round(float(c.weight_pct), 4),
             "amount": _r(value * c.weight_pct / 100) if value is not None else None,
             "kind": KIND_NESTED if _nested(c, self_ticker) else KIND_COMPANY} for c in top]


def compute(holdings: list[HoldingIn], etfs: dict[str, EtfIn], cash: float | None, now: datetime, expected_date: date,
            limit: int = DEFAULT_LIMIT) -> dict:
    """cash가 None이면 모의투자 계좌가 없다. expected_date는 시세가 있어야 할 거래일(outlook.expected_trading_day)."""
    today = now.date()
    has_account = cash is not None
    cash = float(cash or 0.0)
    warnings: list[dict] = []
    priced, unpriced = [], []
    for h in holdings:
        if h.price is None or h.price <= 0 or h.quantity <= 0:
            unpriced.append({"ticker": h.ticker, "name": h.name, "asset_type": h.asset_type, "quantity": h.quantity})
            warnings.append({"kind": "no_price", "ticker": h.ticker, "name": h.name})
            continue
        priced.append((h, h.quantity * h.price))
    holdings_value = sum(v for _, v in priced)
    total_asset = cash + holdings_value
    dates = [h.price_date for h, _ in priced if h.price_date]
    valuation_date = max(dates) if dates else None
    for h, _ in priced:
        if valuation_date and h.price_date and h.price_date < valuation_date:
            warnings.append({"kind": "older_price", "ticker": h.ticker, "name": h.name, "date": h.price_date.isoformat(),
                             "other": valuation_date.isoformat()})
    price_lag = business_days_behind(valuation_date, expected_date) if valuation_date else None

    companies: dict[tuple, _Co] = {}

    def company(key, name, code, ticker, linked, sector=None) -> _Co:
        co = companies.get(key)
        if co is None:
            co = companies[key] = _Co(key, name, code, ticker, linked, sector)
        return co

    stock_amount = etf_amount = via_amount = nested_amount = residual_amount = undecomposed_amount = unlinked_amount = 0.0
    etf_rows: list[dict] = []
    for h, value in priced:
        if h.asset_type != "etf":
            stock_amount += value
            company(("code", h.ticker), h.name, h.ticker, h.ticker, True).direct += value
            continue
        etf_amount += value
        etf = etfs.get(h.ticker)
        cons = list(etf.constituents) if etf else []
        q = _quality(cons, h.ticker)
        lev = leverage_kind(h.name)
        as_of = etf.as_of if etf else None
        row = {"ticker": h.ticker, "name": h.name, "eval_amount": value, "asset_weight_pct": _pct(value, total_asset),
               "leverage_kind": lev, "holdings_as_of": as_of.isoformat() if as_of else None,
               "holdings_lag_business_days": business_days_behind(as_of, expected_date) if as_of else None,
               "holdings_stale": bool(cons) and is_stale(as_of, today), **q,
               "allocated_amount": None, "nested_etf_amount": None, "residual_amount": None, "nested_etfs": []}
        reason = (REASON_LEVERAGED if lev else REASON_NO_DATA if not cons else
                  REASON_INVALID if q["over_100_count"] or q["positive_weight_sum_pct"] <= 0 else None)
        if reason:
            undecomposed_amount += value
            row.update(status=STATUS_NOT_DECOMPOSED, reason_code=reason, constituents=_preview(cons, h.ticker, None))
            warnings.append({"kind": f"etf_{reason}", "ticker": h.ticker, "name": h.name,
                             "leverage_kind": lev, "over_100_count": q["over_100_count"]})
            etf_rows.append(row)
            continue
        allocated = nested = 0.0
        for c in cons:
            if c.weight_pct is None or c.weight_pct <= 0:
                continue
            amount = value * c.weight_pct / 100
            if _nested(c, h.ticker):
                nested += amount
                row["nested_etfs"].append({"name": c.linked_name or c.name, "code": c.code, "linked_ticker": c.linked_ticker,
                                           "weight_pct": round(float(c.weight_pct), 4), "amount": amount})
                continue
            allocated += amount
            if c.linked_ticker:
                co = company(("code", c.linked_ticker), c.linked_name or c.name, c.linked_ticker, c.linked_ticker, True, c.linked_sector)
            else:
                co = company(("name", _name_key(c.name)), c.name, c.code, None, False)
                unlinked_amount += amount
            slot = co.via.setdefault(h.ticker, [h.name, 0.0, 0.0])
            slot[1] += amount
            slot[2] += c.weight_pct
        residual = value - allocated - nested
        via_amount += allocated
        nested_amount += nested
        residual_amount += residual
        row.update(status=STATUS_DECOMPOSED, reason_code=None, allocated_amount=allocated, nested_etf_amount=nested, residual_amount=residual,
                   constituents=_preview(cons, h.ticker, value))
        for n in row["nested_etfs"]:
            n["amount"] = _r(n["amount"])
        base = {"ticker": h.ticker, "name": h.name}
        ps = q["positive_weight_sum_pct"]
        if ps > WEIGHT_OVER_PCT:
            warnings.append({**base, "kind": "etf_weight_over_100", "pct": round(ps, 2), "amount": _r(residual)})
        elif ps < WEIGHT_UNDER_PCT:
            warnings.append({**base, "kind": "etf_weight_under_100", "pct": round(ps, 2), "rest": round(100 - ps, 2), "amount": _r(residual)})
        if q["negative_weight_count"]:
            warnings.append({**base, "kind": "etf_negative_weights", "count": q["negative_weight_count"], "pct": round(q["negative_weight_sum_pct"], 2)})
        if q["null_weight_count"]:
            warnings.append({**base, "kind": "etf_null_weights", "count": q["null_weight_count"]})
        if q["duplicate_count"]:
            warnings.append({**base, "kind": "etf_duplicate_constituents", "count": q["duplicate_count"]})
        if q["nested_etf_count"]:
            warnings.append({**base, "kind": "etf_nested_etf", "count": q["nested_etf_count"], "amount": _r(nested)})
        if q["unlinked_count"]:
            warnings.append({**base, "kind": "etf_unlinked_constituents", "count": q["unlinked_count"]})
        if row["holdings_stale"]:
            warnings.append({**base, "kind": "etf_holdings_stale", "date": row["holdings_as_of"]})
        etf_rows.append(row)

    ordered = sorted(companies.values(), key=lambda c: (-(c.direct + c.via_total), c.name))
    items = []
    for n, c in enumerate(ordered, 1):
        total = c.direct + c.via_total
        via = sorted(c.via.items(), key=lambda kv: -kv[1][1])
        items.append({
            "rank": n, "name": c.name, "code": c.code, "ticker": c.ticker, "linked": c.linked, "sector": c.sector,
            "exposure_type": "both" if c.direct > 0 and c.via_total > 0 else "direct" if c.direct > 0 else "etf",
            "direct_amount": _r(c.direct), "via_etf_amount": _r(c.via_total), "total_amount": _r(total),
            "direct_weight_pct": _pct(c.direct, total_asset), "via_etf_weight_pct": _pct(c.via_total, total_asset),
            "total_weight_pct": _pct(total, total_asset), "holdings_weight_pct": _pct(total, holdings_value),
            "via_etfs": [{"ticker": t, "name": v[0], "amount": _r(v[1]), "constituent_weight_pct": round(v[2], 4),
                          "asset_weight_pct": _pct(v[1], total_asset)} for t, v in via],
        })

    flags = []
    if total_asset > 0:
        for c, it in zip(ordered, items):
            total = c.direct + c.via_total
            if total / total_asset * 100 >= CONCENTRATION_PCT:
                flags.append({"kind": "concentration", "name": c.name, "code": c.code, "value_pct": it["total_weight_pct"],
                              "threshold_pct": CONCENTRATION_PCT, "direct_weight_pct": it["direct_weight_pct"]})
            if c.via_total / total_asset * 100 >= ETF_GAP_PCT:
                flags.append({"kind": "etf_inflates_weight", "name": c.name, "code": c.code, "value_pct": it["via_etf_weight_pct"],
                              "threshold_pct": ETF_GAP_PCT, "total_weight_pct": it["total_weight_pct"], "direct_weight_pct": it["direct_weight_pct"]})
            for t, v in c.via.items():
                if v[1] / total_asset * 100 >= SINGLE_ETF_PCT:
                    flags.append({"kind": "single_etf_exposure", "name": c.name, "code": c.code, "etf_ticker": t, "etf_name": v[0],
                                  "value_pct": _pct(v[1], total_asset), "threshold_pct": SINGLE_ETF_PCT})

    for row in etf_rows:
        for k in ("eval_amount", "allocated_amount", "nested_etf_amount", "residual_amount"):
            row[k] = _r(row[k])

    unidentified = undecomposed_amount + nested_amount + max(residual_amount, 0.0)
    limit = max(1, min(limit, MAX_LIMIT))
    companies_amount = stock_amount + via_amount
    return {
        "calc_version": CALC_VERSION,
        "status": "empty" if not holdings else "no_prices" if not priced else "ok",
        "has_account": has_account,
        "valuation_date": valuation_date.isoformat() if valuation_date else None,
        "expected_date": expected_date.isoformat(),
        "price_lag_business_days": price_lag,
        "etf_holdings_stale": any(r["holdings_stale"] for r in etf_rows),
        "summary": {
            "cash_balance": _r(cash), "holdings_value": _r(holdings_value), "total_asset": _r(total_asset),
            "stock_amount": _r(stock_amount), "etf_amount": _r(etf_amount),
            "direct_amount": _r(stock_amount), "via_etf_amount": _r(via_amount), "companies_amount": _r(companies_amount),
            # 간접 노출 중 종목 목록(company)과 연결된 구성종목 / 연결되지 않은 구성종목(해외 주식·채권·CP·스왑·선물 등, 이름으로 묶음)
            "linked_via_amount": _r(via_amount - unlinked_amount), "unlinked_via_amount": _r(unlinked_amount),
            "nested_etf_amount": _r(nested_amount), "residual_amount": _r(residual_amount), "undecomposed_etf_amount": _r(undecomposed_amount),
            "unidentified_amount": _r(unidentified), "unidentified_weight_pct": _pct(unidentified, total_asset),
            "over_allocated_amount": _r(max(-residual_amount, 0.0)),
            "cash_weight_pct": _pct(cash, total_asset), "companies_weight_pct": _pct(companies_amount, total_asset),
            "holding_count": len(holdings), "priced_holding_count": len(priced), "unpriced_count": len(unpriced),
            "company_count": len(items),
            "direct_top1_weight_pct": max((it["direct_weight_pct"] or 0 for it in items if it["direct_amount"]), default=None),
            "look_through_top1": ({"name": items[0]["name"], "code": items[0]["code"], "total_weight_pct": items[0]["total_weight_pct"]}
                                  if items else None),
        },
        "companies": items[:limit],
        "overlaps": [it for it in items if it["exposure_type"] == "both"],
        "etfs": etf_rows,
        "unpriced": unpriced,
        "sector": sector_summary(items, companies_amount),
        "flags": flags,
        "data_warnings": warnings,
        "limit": limit,
    }


def sector_summary(items: list[dict], companies_amount: float) -> dict:
    known = [it for it in items if it.get("sector")]
    covered = sum(it["total_amount"] for it in known)
    pct = round(covered / companies_amount * 100, 2) if companies_amount > 0 else None
    if not known or not pct:
        return {"status": "unavailable", "covered_weight_pct": 0.0 if companies_amount > 0 else None, "items": []}
    by: dict[str, float] = {}
    for it in known:
        by[it["sector"]] = by.get(it["sector"], 0.0) + it["total_amount"]
    return {"status": "available" if pct >= SECTOR_AVAILABLE_PCT else "partial", "covered_weight_pct": pct,
            "items": [{"sector": k, "amount": round(v, 2)} for k, v in sorted(by.items(), key=lambda kv: -kv[1])]}


def snapshot(result: dict, top: int = 20) -> dict:
    """진단 저장본에 넣는 요약(당시 구성 기준일·비중을 보존). 기업은 상위 top개, ETF는 상위 구성종목까지."""
    return {
        **{k: result[k] for k in ("calc_version", "status", "valuation_date", "expected_date", "price_lag_business_days",
                                  "etf_holdings_stale", "summary", "sector", "flags", "data_warnings")},
        "companies": result["companies"][:top],
        "overlaps": result["overlaps"][:top],
        "etfs": result["etfs"],
        "unpriced": result["unpriced"],
    }


def compare(current: dict | None, previous: dict | None) -> dict | None:
    """두 진단 저장본의 실질 노출 변화. 둘 다 계산된 스냅샷(status ok)이 있을 때만, 아니면 None."""
    if not current or not previous or current.get("status") != "ok" or previous.get("status") != "ok":
        return None
    cur = {(c["code"], c["name"]): c for c in current.get("companies", [])}
    prev = {(c["code"], c["name"]): c for c in previous.get("companies", [])}

    def diff(a, b):
        return None if a is None or b is None else round(a - b, 2)

    rows = []
    for key in list(cur) + [k for k in prev if k not in cur]:
        c, p = cur.get(key), prev.get(key)
        rows.append({"name": (c or p)["name"], "code": (c or p)["code"],
                     "previous_total_weight_pct": p["total_weight_pct"] if p else None, "current_total_weight_pct": c["total_weight_pct"] if c else None,
                     "change_pct": diff(c["total_weight_pct"] if c else 0.0, p["total_weight_pct"] if p else 0.0),
                     "previous_total_amount": p["total_amount"] if p else None, "current_total_amount": c["total_amount"] if c else None,
                     "status": "added" if p is None else "removed" if c is None else "changed"})
    rows.sort(key=lambda r: -abs(r["change_pct"] or 0))
    cs, ps = current["summary"], previous["summary"]
    return {
        "calc_version": {"current": current.get("calc_version"), "previous": previous.get("calc_version")},
        "valuation_date": {"current": current.get("valuation_date"), "previous": previous.get("valuation_date")},
        "summary": {k: {"previous": ps.get(k), "current": cs.get(k), "change": diff(cs.get(k), ps.get(k))}
                    for k in ("total_asset", "via_etf_amount", "unidentified_weight_pct", "companies_weight_pct", "direct_top1_weight_pct")},
        "look_through_top1": {"previous": ps.get("look_through_top1"), "current": cs.get("look_through_top1")},
        "companies": rows,
        "note_code": "stored_snapshots",  # 각 저장본 당시의 구성종목·시세로 계산한 값끼리 비교(다시 계산하지 않음)
        "companies_compared": "top_saved",  # 저장본에 남긴 상위 기업끼리만 비교한다
    }
