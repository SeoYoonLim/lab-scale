"""품질 평가용 합성 데이터와 독립 정답 계산(2026-11-07). DB·app 패키지를 쓰지 않는다.

실제 사용자·계정·보유 내역은 쓰지 않는다. 종목 이름은 합성 이름(가나다전자 등)이거나, 종목 식별 문항에서만 CI 시드(공개 종목 목록,
scripts/ci_seed_companies.json)의 이름을 쓴다. 정답은 원자료(일별 종가·거래량, 보유 수량·가격, 지수 종가)에서 여기서 따로 계산한다:
제품의 계산 함수(app/price_flow.compute, app/look_through.compute 등)를 부르지 않으므로 제품 계산이 틀리면 정답과 어긋난다.
"""

from __future__ import annotations

from datetime import date, timedelta

from evals.quality_judges import Truth


def trading_days(end: date, n: int) -> list[date]:
    """end 이하의 평일 n개(오름차순). 공휴일은 모른다(제품과 같은 한계)."""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def price_rows(spec: dict) -> list[tuple]:
    """spec: {"shape": up|down|flat|rebound|custom, "n": 행 수, "end": "YYYY-MM-DD", "start": 시작 종가, "step": 원,
    "volume": 평소 거래량, "volume_last": 기준일 거래량, "closes": custom 종가 목록}. -> [(date, close, volume)] 오름차순."""
    end = date.fromisoformat(spec["end"])
    if spec.get("shape") == "custom":
        closes = [float(c) for c in spec["closes"]]
        n = len(closes)
    else:
        n, start, step = spec.get("n", 80), float(spec.get("start", 10_000)), float(spec.get("step", 100))
        shape = spec.get("shape", "up")
        if shape == "up":
            closes = [start + i * step for i in range(n)]
        elif shape == "down":
            closes = [start - i * step for i in range(n)]
        elif shape == "flat":
            closes = [start + (step if i % 2 else 0) for i in range(n)]
        elif shape == "rebound":  # 앞 n-6행은 하락, 뒤 6행은 반등
            closes = [start - i * step for i in range(n - 6)]
            closes += [closes[-1] + (i + 1) * step * 0.6 for i in range(6)]
        else:
            raise ValueError(f"모르는 shape: {shape}")
    days = trading_days(end, n)
    vol, vol_last = int(spec.get("volume", 1_000_000)), int(spec.get("volume_last", spec.get("volume", 1_000_000)))
    return [(d, c, vol if i < n - 1 else vol_last) for i, (d, c) in enumerate(zip(days, closes))]


def _pct(new: float, old: float) -> float | None:
    return None if not old else (new / old - 1) * 100


def price_truth(rows: list[tuple], window: int = 20, volume_avg: int = 20, windows=(1, 5, 20, 60), mas=(5, 20, 60)) -> Truth:
    """종가·거래량 행에서 정답 지표. 행이 모자란 지표는 None(계산 불가). 기준일 = 마지막 행.
    수익률(n) = 기준일 종가 / n행 전 종가 - 1, 이동평균(n) = 마지막 n행 종가 평균, 평균 거래량(n) = 기준일을 뺀 직전 n행 평균."""
    closes = [float(r[1]) for r in rows]
    vols = [r[2] for r in rows]
    c = closes[-1]
    v: dict = {("close", None): c}
    for n in sorted(set(windows) | {window}):
        v[("return", n)] = _pct(c, closes[-1 - n]) if len(closes) > n else None
    for n in mas:
        v[("ma", n)] = sum(closes[-n:]) / n if len(closes) >= n else None
        v[("ma_gap", n)] = _pct(c, v[("ma", n)]) if v[("ma", n)] else None
    v[("volume", None)] = float(vols[-1]) if vols[-1] is not None else None
    prior = vols[-1 - volume_avg:-1]
    if len(prior) == volume_avg and all(x is not None for x in prior) and sum(prior):
        avg = sum(prior) / volume_avg
        v[("avg_volume", volume_avg)] = avg
        v[("volume_ratio", None)] = vols[-1] / avg
    else:
        v[("avg_volume", volume_avg)] = None
        v[("volume_ratio", None)] = None
    k = min(window, len(closes) - 1)
    seg = closes[-(k + 1):]
    v[("period_return", None)] = _pct(c, seg[0])
    # 기간 최고·최저(2026-11-07 35차 통일): 기간 안의 거래일 종가(첫날·마지막 날 포함)만. 비교 기준일(seg[0]) 종가는 수익률에만 쓴다.
    # 33차에는 경로마다 정의가 달라 두 정의를 다 받았다(alts). 제품을 한 정의로 맞췄으므로 평가도 한 정의만 받는다(더 엄격).
    inside = seg[1:] or seg
    v[("high", None)] = max(inside)
    v[("low", None)] = min(inside)
    v[("from_low", 20)] = _pct(c, min(closes[-21:]))
    return Truth(v, {r[0].isoformat(): float(r[1]) for r in rows}, rows[-1][0].isoformat())


def look_through_truth(holdings: list[dict], etfs: dict, cash: float) -> dict:
    """보유(stock/etf)와 ETF 구성 비중에서 기업별 직접·ETF 경유·합계 비중(총자산 대비 %)을 따로 계산한다.
    holdings: [{"ticker","name","asset_type","quantity","price"}], etfs: {ticker: {"constituents": [{"ticker","name","weight_pct"}]}}.
    레버리지·인버스 이름의 ETF는 분해하지 않는다(제품 정책)."""
    total = cash + sum(h["quantity"] * h["price"] for h in holdings if h.get("price"))
    direct, via = {}, {}
    for h in holdings:
        if not h.get("price"):
            continue
        value = h["quantity"] * h["price"]
        if h["asset_type"] == "stock":
            direct[h["ticker"]] = direct.get(h["ticker"], 0.0) + value
            continue
        if any(w in h["name"] for w in ("레버리지", "인버스")):
            continue
        for c in (etfs.get(h["ticker"]) or {}).get("constituents", []):
            if c.get("weight_pct") and c["weight_pct"] > 0:
                via[c["ticker"]] = via.get(c["ticker"], 0.0) + value * c["weight_pct"] / 100
    out = {}
    for t in set(direct) | set(via):
        d, e = direct.get(t, 0.0), via.get(t, 0.0)
        out[t] = {"direct_weight_pct": d / total * 100, "via_etf_weight_pct": e / total * 100, "total_weight_pct": (d + e) / total * 100}
    return {"total_asset": total, "companies": out}


def benchmark_truth(points: list[dict], index: list[tuple]) -> dict:
    """포트폴리오 일별 총자산과 지수 종가의 공통 거래일에서 첫 공통일 대비 수익률과 초과 수익률(%p)."""
    by = {d: v for d, v in index}
    common = [p for p in points if p["date"] in by]
    if len(common) < 2:
        return {"common_days": len(common)}
    p0, b0 = common[0]["total_asset"], by[common[0]["date"]]
    pr = (common[-1]["total_asset"] / p0 - 1) * 100
    br = (by[common[-1]["date"]] / b0 - 1) * 100
    return {"common_days": len(common), "portfolio_return_pct": pr, "benchmark_return_pct": br, "excess_return_pp": pr - br,
            "missing_days": len(points) - len(common)}
