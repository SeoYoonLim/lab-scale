"""전망 신호 백테스트(2026-11-03). DB의 전 종목·전 기간 종가로 매 거래일 app.outlook의 신호와 판정을 계산하고,
기간별 판정 뒤 수익률(1주일 -> 5거래일, 1개월 -> 20, 3개월 -> 60)이 양수였던 비율을 같은 구간 전체 비율과 비교한다.

- 신호: 실제 답변과 같은 함수(app.outlook.compute_indicators/classify_signals)를 신호일까지의 PRICE_ROWS(260)거래일에만 적용한다.
  수익률(미래 종가)은 신호 계산에 들어가지 않는다(tests/test_backtest_outlook.py가 누출을 확인한다).
- 구간(측정 전에 정함): 학습 = 신호일과 수익률 구간 끝이 모두 TRAIN_END 이하, 검증 = 신호일이 VALID_START 이상.
  묶음·기준값은 학습 구간에서만 정하고 검증 구간은 마지막에 한 번 잰다.
- 신뢰구간: 같은 날 여러 종목이 겹치고, 수익률 구간(h거래일)도 날짜끼리 겹친다. 그래서 거래일을 h개씩 묶은 블록을 군집으로 보고
  군집 강건 분산(비율 추정량의 선형화)으로 "라벨 상승 비율 - 전체 상승 비율"의 95% 구간을 낸다.
- 한계: 현재 상장 350종목만 있다(생존 편향). 거래비용·세금은 반영하지 않는다. 데이터 점검(시세 지연·거래일 누락)의 판단 유보는 과거에 적용하지 않는다.

사용:
  python -m scripts.backtest_outlook --collect CACHE.npz        # 신호·미래 수익률 계산(DB 읽기만, 몇 분)
  python -m scripts.backtest_outlook --analyze CACHE.npz        # 학습 구간: 지표 상관, 묶음 후보, 기준값 비교(검증 구간은 보지 않는다)
  python -m scripts.backtest_outlook --final CACHE.npz          # 검증 구간 측정 -> app/data/outlook_backtest.json
"""

import argparse
import bisect
import json
import math
import sys
from datetime import date, datetime
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from app import outlook

TRAIN_END = date(2025, 12, 31)
VALID_START = date(2026, 1, 2)
HORIZON = {"1주일": 5, "1개월": 20, "3개월": 60}
KINDS = ("close_vs_ma_short", "close_vs_ma_long", "ma_alignment", "cross", "period_return", "rsi", "macd", "volume", "relative")
MISSING = -2
CORR_GROUP_THRESHOLD = 0.5
OUT_JSON = Path(__file__).resolve().parents[1] / "app" / "data" / "outlook_backtest.json"
SIDE_NUM = {outlook.UP: 1, outlook.DOWN: -1, outlook.NEUTRAL: 0}
LABELS = (outlook.LABEL_UP, outlook.LABEL_DOWN, outlook.LABEL_NEUTRAL)

_DATA: dict = {}


def _init(data):
    _DATA.update(data)


def signals_for_stock(rows: list[tuple], index_rows: list[tuple] | None, period: outlook.Period, horizon: int):
    """한 종목의 매 거래일 (날짜, 지표 종류별 신호 숫자, 미래 수익률). rows는 날짜 오름차순 (date, close, volume).
    t일 신호는 rows[t-259 .. t]만 본다. 미래 수익률은 rows[t + horizon]의 종가로 따로 계산한다."""
    n = len(rows)
    idx_dates = [d for d, _ in index_rows] if index_rows else []
    out = []
    for t in range(outlook.PRICE_ROWS - 1, n):
        window = rows[t - outlook.PRICE_ROWS + 1: t + 1]
        idx = None
        if index_rows:
            lo, hi = bisect.bisect_left(idx_dates, window[0][0]), bisect.bisect_right(idx_dates, window[-1][0])
            idx = index_rows[lo:hi]
        ind = outlook.compute_indicators(window, period, idx)
        sides = {s["kind"]: SIDE_NUM[s["side"]] for s in outlook.classify_signals(ind, period)}
        fwd = (rows[t + horizon][1] / rows[t][1] - 1) if t + horizon < n else math.nan
        out.append((rows[t][0], [sides.get(k, MISSING) for k in KINDS], fwd))
    return out


def _work(args):
    cid, label = args
    rows = _DATA["prices"][cid]
    idx = _DATA["index"].get(_DATA["market"][cid])
    return cid, label, signals_for_stock(rows, idx, outlook.PERIODS[label], HORIZON[label])


def load_data():
    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.tools.market_tool import index_code_for_market

    db = SessionLocal()
    try:
        prices: dict[int, list] = {}
        for cid, d, c, v in db.execute(text("SELECT company_id, price_date, close_price, volume FROM stock_price "
                                            "ORDER BY company_id, price_date")):
            prices.setdefault(cid, []).append((d, float(c), v))
        market = {cid: index_code_for_market(m) for cid, m in db.execute(text("SELECT id, market FROM company"))}
        index: dict[str, list] = {}
        for code, d, c in db.execute(text("SELECT index_code, price_date, close_price FROM market_index ORDER BY index_code, price_date")):
            index.setdefault(code, []).append((d, float(c)))
        calendar = sorted({d for d, _ in index.get("KS11", [])})
    finally:
        db.close()
    return {"prices": prices, "market": market, "index": index, "calendar": calendar}


def collect(cache: Path) -> None:
    data = load_data()
    jobs = [(cid, label) for label in HORIZON for cid in data["prices"]]
    arrays = {label: {"date": [], "cid": [], "sides": [], "fwd": []} for label in HORIZON}
    with Pool(processes=10, initializer=_init, initargs=(data,)) as pool:
        for i, (cid, label, rows) in enumerate(pool.imap_unordered(_work, jobs, chunksize=4)):
            a = arrays[label]
            for d, sides, fwd in rows:
                a["date"].append(d.toordinal())
                a["cid"].append(cid)
                a["sides"].append(sides)
                a["fwd"].append(fwd)
            if i % 100 == 0:
                print(f"[{i}/{len(jobs)}]", flush=True)
    out = {}
    for label, a in arrays.items():
        out[f"{label}_date"] = np.array(a["date"], dtype=np.int32)
        out[f"{label}_cid"] = np.array(a["cid"], dtype=np.int32)
        out[f"{label}_sides"] = np.array(a["sides"], dtype=np.int8)
        out[f"{label}_fwd"] = np.array(a["fwd"], dtype=np.float64)
    out["calendar"] = np.array([d.toordinal() for d in data["calendar"]], dtype=np.int32)
    np.savez_compressed(cache, **out)
    print("saved", cache, {k: v.shape for k, v in out.items()})


# ---------- 판정과 측정(순수 함수) ----------


def labels_from_sides(sides: np.ndarray, groups: dict[str, str], margin: int, min_groups: int) -> np.ndarray:
    """신호 숫자 행렬(행 = 종목·날짜, 열 = KINDS)에서 app.outlook.judge와 같은 규칙으로 라벨 번호를 만든다.
    0 상승 우위, 1 하락 우위, 2 중립, 3 판단 유보."""
    names = sorted(set(groups.values()))
    votes = np.zeros((sides.shape[0], len(names)), dtype=np.int8)
    present = np.zeros((sides.shape[0], len(names)), dtype=bool)
    for j, k in enumerate(KINDS):
        g = names.index(groups[k])
        col = sides[:, j]
        ok = col != MISSING
        counted = ok
        other = outlook.SAME_FACT_AS.get(k)
        if other in KINDS and groups[other] == groups[k]:
            # app.outlook.counted_elsewhere: 같은 묶음에 같은 사실을 나타내는 지표가 있으면 묶음에는 보이되 표에는 넣지 않는다
            counted = ok & (sides[:, KINDS.index(other)] == MISSING)
        votes[:, g] += np.where(counted, col, 0)
        present[:, g] |= ok
    gv = np.sign(votes) * present
    n_up = (gv > 0).sum(1)
    n_down = (gv < 0).sum(1)
    n_groups = present.sum(1)
    lab = np.full(sides.shape[0], 2, dtype=np.int8)
    lab[n_up - n_down >= margin] = 0
    lab[n_down - n_up >= margin] = 1
    lab[n_groups < min_groups] = 3
    return lab


def cluster_ids(dates: np.ndarray, calendar: np.ndarray, horizon: int) -> np.ndarray:
    """거래일 순번을 horizon개씩 묶은 블록 번호(군집)."""
    pos = np.searchsorted(calendar, dates)
    return pos // horizon


def compare(up: np.ndarray, in_label: np.ndarray, clusters: np.ndarray) -> dict:
    """라벨 표본의 상승 비율과 전체 상승 비율의 차이, 군집 강건 95% 구간."""
    n_l, n_a = int(in_label.sum()), int(up.size)
    if n_l == 0:
        return {"n": 0}
    p_l, p_a = float(up[in_label].mean()), float(up.mean())
    uniq, inv = np.unique(clusters, return_inverse=True)
    g = uniq.size
    a = np.bincount(inv, weights=np.where(in_label, up - p_l, 0.0), minlength=g) / n_l
    b = np.bincount(inv, weights=up - p_a, minlength=g) / n_a
    d = a - b
    se = math.sqrt(g / (g - 1) * float((d ** 2).sum())) if g > 1 else math.nan
    se_l = math.sqrt(g / (g - 1) * float((a ** 2).sum())) if g > 1 else math.nan
    diff = p_l - p_a
    return {"n": n_l, "up_rate": round(p_l, 4), "base_rate": round(p_a, 4), "diff": round(diff, 4),
            "diff_ci95": [round(diff - 1.96 * se, 4), round(diff + 1.96 * se, 4)],
            "up_rate_ci95": [round(p_l - 1.96 * se_l, 4), round(p_l + 1.96 * se_l, 4)],
            "clusters": int(g), "significant": bool(abs(diff) > 1.96 * se)}


def split_mask(dates: np.ndarray, calendar: np.ndarray, horizon: int, which: str) -> np.ndarray:
    """학습: 신호일과 수익률 구간 끝(신호일 + horizon 거래일)이 모두 TRAIN_END 이하. 검증: 신호일 VALID_START 이상."""
    if which == "valid":
        return dates >= VALID_START.toordinal()
    pos = np.searchsorted(calendar, dates)
    end_pos = np.minimum(pos + horizon, calendar.size - 1)
    return (dates <= TRAIN_END.toordinal()) & (calendar[end_pos] <= TRAIN_END.toordinal())


def measure(cache: dict, label: str, which: str, groups: dict[str, str], margin: int, min_groups: int) -> dict:
    h = HORIZON[label]
    dates, sides, fwd = cache[f"{label}_date"], cache[f"{label}_sides"], cache[f"{label}_fwd"]
    ok = split_mask(dates, cache["calendar"], h, which) & ~np.isnan(fwd)
    dates, sides, fwd = dates[ok], sides[ok], fwd[ok]
    lab = labels_from_sides(sides, groups, margin, min_groups)
    scored = lab != 3
    up = (fwd[scored] > 0).astype(np.float64)
    clusters = cluster_ids(dates[scored], cache["calendar"], h)
    out = {"horizon": h, "n_scored": int(scored.sum()), "n_hold": int((~scored).sum()),
           "first_date": date.fromordinal(int(dates.min())).isoformat() if dates.size else None,
           "last_date": date.fromordinal(int(dates.max())).isoformat() if dates.size else None, "labels": {}}
    for i, name in enumerate(LABELS):
        out["labels"][name] = compare(up, lab[scored] == i, clusters)
    return out


def correlations(cache: dict, label: str) -> dict:
    """학습 구간의 지표 신호(-1/0/+1) 피어슨 상관. 두 지표가 모두 계산된 표본만 쓴다."""
    h = HORIZON[label]
    dates, sides = cache[f"{label}_date"], cache[f"{label}_sides"]
    ok = split_mask(dates, cache["calendar"], h, "train")
    s = sides[ok].astype(np.float64)
    out = {}
    for i, a in enumerate(KINDS):
        for j, b in enumerate(KINDS):
            if j <= i:
                continue
            m = (sides[ok][:, i] != MISSING) & (sides[ok][:, j] != MISSING)
            x, y = s[m, i], s[m, j]
            r = float(np.corrcoef(x, y)[0, 1]) if m.sum() > 2 and x.std() > 0 and y.std() > 0 else math.nan
            out[f"{a}|{b}"] = round(r, 3)
    return out


def groups_from_corr(corr: dict, threshold: float = CORR_GROUP_THRESHOLD) -> list[list[str]]:
    """|r| >= threshold인 지표끼리 이은 연결 요소."""
    parent = {k: k for k in KINDS}

    def find(k):
        while parent[k] != k:
            k = parent[k]
        return k

    for pair, r in corr.items():
        if not math.isnan(r) and abs(r) >= threshold:
            a, b = pair.split("|")
            parent[find(a)] = find(b)
    comps: dict[str, list[str]] = {}
    for k in KINDS:
        comps.setdefault(find(k), []).append(k)
    return sorted(comps.values(), key=lambda c: KINDS.index(c[0]))


def _load(cache_path: Path) -> dict:
    z = np.load(cache_path)
    return {k: z[k] for k in z.files}


def analyze(cache_path: Path) -> None:
    cache = _load(cache_path)
    for label in HORIZON:
        corr = correlations(cache, label)
        print(f"== {label} 학습 구간 상관(|r|>=0.3)")
        for pair, r in sorted(corr.items(), key=lambda x: -abs(x[1]) if not math.isnan(x[1]) else 0):
            if not math.isnan(r) and abs(r) >= 0.3:
                print(f"   {pair}: {r:+.3f}")
        print("   연결 요소(|r|>=0.5):", groups_from_corr(corr))
        for margin in (1, 2):
            m = measure(cache, label, "train", outlook.SIGNAL_GROUPS, margin, outlook.MIN_GROUPS)
            print(f"   학습 margin={margin}:", {k: (v.get("n"), v.get("up_rate"), v.get("base_rate"), v.get("diff"), v.get("diff_ci95"))
                                             for k, v in m["labels"].items()})
        m = measure(cache, label, "train", {k: k for k in KINDS}, outlook.SUMMARY_MARGIN, outlook.MIN_SIGNAL_INDICATORS)
        print("   학습 묶음 없이(지표마다 1표, 차이 2):", {k: (v.get("n"), v.get("up_rate"), v.get("diff")) for k, v in m["labels"].items()})


def final(cache_path: Path) -> None:
    cache = _load(cache_path)
    cal = cache["calendar"]
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "method": "app.outlook 신호를 신호일까지의 260거래일 종가로 매 거래일 계산, 판정 뒤 h거래일 종가 수익률 > 0 비율을 같은 구간 전체와 비교. "
                     "95% 구간은 거래일 h개 블록 군집 강건 분산",
           "train_end": TRAIN_END.isoformat(), "valid_start": VALID_START.isoformat(),
           "data_first_date": date.fromordinal(int(cal.min())).isoformat(), "data_last_date": date.fromordinal(int(cal.max())).isoformat(),
           "groups": outlook.SIGNAL_GROUPS, "group_margin": outlook.GROUP_MARGIN, "min_groups": outlook.MIN_GROUPS,
           "limits": ["현재 상장 350종목만 포함(생존 편향)", "거래비용·세금 미반영", "과거 빈도이며 확률·예측이 아님",
                      "데이터 지연·누락에 따른 판단 유보는 과거에 적용하지 않음"],
           "periods": {}}
    for label in HORIZON:
        out["periods"][label] = {
            "train": measure(cache, label, "train", outlook.SIGNAL_GROUPS, outlook.GROUP_MARGIN, outlook.MIN_GROUPS),
            "valid": measure(cache, label, "valid", outlook.SIGNAL_GROUPS, outlook.GROUP_MARGIN, outlook.MIN_GROUPS),
            "valid_before_groups": measure(cache, label, "valid", {k: k for k in KINDS}, outlook.SUMMARY_MARGIN,
                                           outlook.MIN_SIGNAL_INDICATORS),
        }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["periods"], ensure_ascii=False, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--collect", type=Path)
    g.add_argument("--analyze", type=Path)
    g.add_argument("--final", type=Path)
    a = ap.parse_args(argv)
    if a.collect:
        collect(a.collect)
    elif a.analyze:
        analyze(a.analyze)
    else:
        final(a.final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
