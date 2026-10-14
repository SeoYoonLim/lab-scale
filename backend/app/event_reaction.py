"""뉴스·공시 같은 사건 이후 주가가 실제로 어떻게 움직였는지를 계산하는 순수 함수 모듈(DB·네트워크 없음).

이 모듈은 "사건 이후의 주가 움직임"만 보여준다. 시간적 선후관계일 뿐 인과관계가 아니며, 같은 기간의 다른 요인이
영향을 줬을 수 있다. 그래서 같은 구간의 다른 사건 수(other_events_in_window)와 시장 지수 대비 초과수익률을 함께 준다.

정의 (backend/API.md에도 같은 내용이 있다)
1. 거래일 달력: 해당 종목의 stock_price 날짜. 휴장일/주말은 달력에 없는 날이다.
2. 기준 종가와 반응 시작일
   - 시각을 아는 사건(뉴스):
       * 거래일 15:30(KST) 이후 발행 -> 기준일 = 그날, 반응 시작일 = 다음 거래일
       * 거래일 장 시작 전·장중(15:30 전) 발행 -> 기준일 = 직전 거래일, 반응 시작일 = 그날
       * 휴장일(주말 포함) 발행 -> 기준일 = 직전 거래일, 반응 시작일 = 다음 거래일
   - 날짜만 아는 사건(공시, time_known=False): 시각을 모르므로 보수적으로 기준일 = 직전 거래일, 반응 시작일 = 접수일
     (접수일이 휴장일이면 다음 거래일). 장 마감 후 접수된 공시도 접수일 반응으로 세므로 반응이 하루 빠르게 잡힐 수 있다.
3. N거래일 수익률(N = 1, 5, 20): 반응 시작일을 1일째로 센 N일째 종가 / 기준 종가 - 1 (%). 아직 N거래일이 지나지
   않았거나 기준 종가가 없으면 null, available=False, reason에 사유.
4. 시장 지수 수익률: 같은 기준일과 같은 N일째 날짜(종목 달력)의 지수 종가로 계산한다. 초과수익률 = 종목 - 지수(%p).
   지수가 없거나(ETF 등 market 미상) 그 날짜의 지수 값이 없으면 index_pct/excess_pct는 null이고 reason에 사유.
5. 거래량 비율: 반응 시작일 거래량 / 기준일까지(기준일 포함) 최근 20거래일 평균 거래량. 20일치가 안 되면 있는 만큼으로
   계산하고 표본 일수(volume_sample_days)를 함께 준다. 표본이 0이거나 평균이 0이면 null.
6. 가격 조정 경고: 기준일부터 20일째(또는 마지막 거래일)까지 종가 기준 일간 등락률 절댓값이 30%를 넘는 날이 있으면
   note에 "가격 조정(분할 등) 가능성" 경고를 넣는다. 수정주가 여부는 알 수 없다.
7. other_events_in_window: 같은 종목의 다른 사건 중 반응 시작일이 이 사건의 [반응 시작일, 20일째(또는 마지막 거래일)]
   안에 드는 건수. 인과가 아님을 보여주는 근거 수치다.
모든 반환 숫자는 소수 둘째 자리로 반올림하고, 입력은 수정하지 않는다.
"""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Sequence
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
MARKET_CLOSE = time(15, 30)
HORIZONS = (1, 5, 20)
VOLUME_WINDOW = 20
ADJUSTMENT_WARN_PCT = 30.0

DISCLAIMER = (
    "사건 이후의 주가 움직임은 시간적 선후관계일 뿐 사건이 원인이라는 뜻이 아닙니다. 같은 기간의 시장 전체 흐름이나 "
    "다른 뉴스·공시 등 다른 요인이 영향을 줬을 수 있고, 표본이 적을 수 있습니다. 투자 판단의 근거로 단정하지 마세요."
)


@dataclass(frozen=True)
class Bar:
    """종목의 하루 시세(종가·거래량). volume은 없을 수 있다."""

    date: date
    close: float
    volume: float | None = None


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


def resolve_base_and_start(
    dates: Sequence[date], event_at: datetime | None, event_date: date | None
) -> tuple[date | None, date | None]:
    """거래일 달력(오름차순)에서 (기준일, 반응 시작일)을 정한다. 없으면 해당 값은 None.

    event_at(시각을 아는 사건)이 있으면 KST로 바꿔 장 마감 전후를 가르고, 없으면 event_date(날짜만)로 보수적으로 정한다.
    """
    if event_at is not None:
        at = event_at.astimezone(KST) if event_at.tzinfo else event_at.replace(tzinfo=KST)
        d, after_close = at.date(), at.time() >= MARKET_CLOSE
        known = True
    else:
        d, after_close, known = event_date, False, False
    i_left = bisect_left(dates, d)  # d 이상인 첫 거래일 위치
    is_trading = i_left < len(dates) and dates[i_left] == d
    prev_day = dates[i_left - 1] if i_left > 0 else None

    if is_trading and known and after_close:
        nxt = dates[i_left + 1] if i_left + 1 < len(dates) else None
        return d, nxt
    if is_trading:
        return prev_day, d
    # 휴장일: 직전 거래일이 기준, 다음 거래일이 반응 시작
    nxt = dates[i_left] if i_left < len(dates) else None
    return prev_day, nxt


def _horizon(
    bars: Sequence[Bar], i_start: int | None, base: Bar | None, n: int,
    index_closes: dict[date, float] | None, index_label: str | None,
) -> dict:
    empty = {"pct": None, "index_pct": None, "excess_pct": None, "available": False}
    if base is None:
        return {**empty, "reason": "기준 종가 없음(시세 데이터 시작 전)"}
    if i_start is None:
        return {**empty, "reason": "반응 시작일 전(다음 거래일 데이터 없음)"}
    i_n = i_start + n - 1
    if i_n >= len(bars):
        return {**empty, "reason": f"{n}거래일 경과 전"}
    end = bars[i_n]
    pct = (end.close / base.close - 1) * 100
    out = {"pct": _r(pct), "index_pct": None, "excess_pct": None, "available": True, "reason": None}
    if index_closes is None:
        out["reason"] = index_label or "지수 없음"
        return out
    b_idx, e_idx = index_closes.get(base.date), index_closes.get(end.date)
    if not b_idx or e_idx is None:
        out["reason"] = "지수 데이터 없음(해당 날짜)"
        return out
    idx_pct = (e_idx / b_idx - 1) * 100
    out["index_pct"] = _r(idx_pct)
    out["excess_pct"] = _r(pct - idx_pct)
    return out


def _volume_ratio(bars: Sequence[Bar], i_base: int | None, i_start: int | None) -> tuple[float | None, int]:
    if i_base is None or i_start is None or i_start >= len(bars) or bars[i_start].volume is None:
        return None, 0
    window = [b.volume for b in bars[max(0, i_base - VOLUME_WINDOW + 1): i_base + 1] if b.volume is not None]
    if not window:
        return None, 0
    avg = sum(window) / len(window)
    if avg <= 0:
        return None, len(window)
    return _r(bars[i_start].volume / avg), len(window)


def _adjustment_warning(bars: Sequence[Bar], i_base: int | None, i_start: int | None) -> bool:
    if i_base is None:
        return False
    last = len(bars) - 1 if i_start is None else min(i_start + max(HORIZONS) - 1, len(bars) - 1)
    for i in range(i_base + 1, last + 1):
        if bars[i - 1].close and round(abs(bars[i].close / bars[i - 1].close - 1) * 100, 6) > ADJUSTMENT_WARN_PCT:
            return True
    return False


def window_end_date(dates: Sequence[date], start: date | None) -> date | None:
    """반응 시작일 기준 20일째 거래일(없으면 마지막 거래일). 시작일이 없으면 None."""
    if start is None:
        return None
    i = bisect_left(dates, start)
    return dates[min(i + max(HORIZONS) - 1, len(dates) - 1)]


def count_other_events(own_index: int, starts: Sequence[date | None], window: tuple[date | None, date | None]) -> int:
    """starts[own_index]를 뺀 다른 사건 중 반응 시작일이 [window 시작, window 끝]에 드는 건수."""
    lo, hi = window
    if lo is None or hi is None:
        return 0
    return sum(1 for j, s in enumerate(starts) if j != own_index and s is not None and lo <= s <= hi)


def compute_reaction(
    bars: Sequence[Bar],
    *,
    event_at: datetime | None = None,
    event_date: date | None = None,
    index_closes: dict[date, float] | None = None,
    index_missing_reason: str | None = None,
    other_events_in_window: int = 0,
) -> dict:
    """사건 하나의 반응 지표. bars는 날짜 오름차순 종목 시세. event_at이 있으면 시각을 아는 사건(뉴스)으로 본다."""
    if event_at is None and event_date is None:
        raise ValueError("event_at 또는 event_date가 필요합니다.")
    dates = [b.date for b in bars]
    base_date, start_date = resolve_base_and_start(dates, event_at, event_date)
    i_base = bisect_left(dates, base_date) if base_date else None
    i_start = bisect_left(dates, start_date) if start_date else None
    base = bars[i_base] if i_base is not None else None

    returns = {
        f"d{n}": _horizon(bars, i_start, base, n, index_closes, index_missing_reason) for n in HORIZONS
    }
    ratio, sample = _volume_ratio(bars, i_base, i_start)
    notes = []
    if _adjustment_warning(bars, i_base, i_start):
        notes.append("가격 조정(분할 등) 가능성: 구간 안에 일간 등락률이 30%를 넘는 날이 있습니다.")
    return {
        "time_known": event_at is not None,
        "base_date": base_date,
        "base_close": _r(base.close) if base else None,
        "reaction_start_date": start_date,
        "returns": returns,
        "volume_ratio": ratio,
        "volume_sample_days": sample,
        "other_events_in_window": other_events_in_window,
        "note": " ".join(notes) or None,
    }


def summarize(reactions: Sequence[dict]) -> dict:
    """사건별 결과의 요약. 평균은 available인 사건만 대상으로 하고 표본 수(with_*)를 같이 준다."""
    out: dict = {"event_count": len(reactions)}
    for n in HORIZONS:
        key = f"d{n}"
        pcts = [r["returns"][key]["pct"] for r in reactions if r["returns"][key]["available"]]
        excess = [r["returns"][key]["excess_pct"] for r in reactions if r["returns"][key]["excess_pct"] is not None]
        out[f"with_{key}"] = len(pcts)
        out[f"avg_{key}_pct"] = _r(sum(pcts) / len(pcts)) if pcts else None
        out[f"positive_{key}_count"] = sum(1 for p in pcts if p > 0)
        out[f"with_excess_{key}"] = len(excess)
        out[f"avg_excess_{key}_pct"] = _r(sum(excess) / len(excess)) if excess else None
    return out
