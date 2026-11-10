"""시장 지수 소스 폴백 체인(collectors.fetch_index_frame). 네트워크 없이 reader를 주입해 확인한다."""

from datetime import date

import pandas as pd

from app.collectors import MARKET_INDEX_SOURCES, fetch_index_frame

TODAY = date(2026, 10, 14)


def _df(last: str, n: int = 3):
    idx = pd.date_range(end=last, periods=n, freq="D")
    return pd.DataFrame({"Close": [100.0 + i for i in range(n)]}, index=idx)


def _reader(table: dict, calls: list):
    def read(symbol, start, end):
        calls.append(symbol)
        v = table[symbol]
        if isinstance(v, Exception):
            raise v
        return v

    return read


def test_first_source_wins_and_later_ones_are_not_called():
    calls = []
    df, source, errors = fetch_index_frame("KS11", "a", "b", reader=_reader({"KS11": _df("2026-10-14")}, calls), today=TODAY)
    assert source == "fdr:KS11" and calls == ["KS11"] and errors == [] and len(df) == 3


def test_falls_back_when_first_raises():
    calls = []
    table = {"KS11": ConnectionError("down"), "KOSPI": _df("2026-10-14")}
    df, source, errors = fetch_index_frame("KS11", "a", "b", reader=_reader(table, calls), today=TODAY)
    assert source == "fdr:KOSPI" and calls == ["KS11", "KOSPI"]
    assert len(errors) == 1 and "fdr:KS11" in errors[0]


def test_falls_back_when_first_is_empty_or_stale():
    calls = []
    table = {"KS11": pd.DataFrame(), "KOSPI": _df("2026-09-17"), "^KS11": _df("2026-10-13")}
    df, source, errors = fetch_index_frame("KS11", "a", "b", reader=_reader(table, calls), today=TODAY)
    assert source == "fdr-yahoo:^KS11" and calls == ["KS11", "KOSPI", "^KS11"]
    assert len(errors) == 2 and "오래됨" in errors[1]


def test_kosdaq_uses_its_own_symbols():
    calls = []
    table = {"KQ11": ConnectionError("x"), "KOSDAQ": _df("2026-10-14")}
    _, source, _ = fetch_index_frame("KQ11", "a", "b", reader=_reader(table, calls), today=TODAY)
    assert source == "fdr:KOSDAQ" and calls == ["KQ11", "KOSDAQ"]


def test_all_fail_returns_none_with_every_reason():
    calls = []
    table = {"KS11": ValueError("a"), "KOSPI": ValueError("b"), "^KS11": pd.DataFrame()}
    df, source, errors = fetch_index_frame("KS11", "a", "b", reader=_reader(table, calls), today=TODAY)
    assert df is None and source is None and len(errors) == 3


def test_error_text_masks_api_keys():
    table = {s: ConnectionError("GET https://h/p?crtfc_key=SECRET99&x=1") for _, s in MARKET_INDEX_SOURCES["KS11"]}
    _, _, errors = fetch_index_frame("KS11", "a", "b", reader=_reader(table, []), today=TODAY)
    assert errors and all("SECRET99" not in e for e in errors)


def test_rows_with_nan_close_are_dropped():
    df = _df("2026-10-14", 4)
    df.iloc[-1, 0] = float("nan")
    out, source, _ = fetch_index_frame("KS11", "a", "b", reader=_reader({"KS11": df}, []), today=TODAY)
    assert len(out) == 3 and out.index.max().date() == date(2026, 10, 13)
