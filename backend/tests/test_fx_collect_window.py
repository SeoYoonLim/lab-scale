"""환율 수집 레코드(app.collectors.fx_records, 2026-11-23): 끝나지 않은 일봉 제외, 첫 행 등락률은 DB 직전 종가로."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from app.collectors import fx_bar_complete, fx_records

KST = ZoneInfo("Asia/Seoul")


def frame(dates, closes):
    return pd.DataFrame({"Close": closes}, index=pd.to_datetime(dates))


def test_bar_complete_uses_london_midnight():
    # 11월은 런던 GMT(UTC+0): 11-23 봉은 11-24 00:00 UTC = 09:00 KST에 끝난다
    assert not fx_bar_complete(date(2026, 11, 23), datetime(2026, 11, 23, 21, 30, tzinfo=KST))
    assert not fx_bar_complete(date(2026, 11, 23), datetime(2026, 11, 24, 8, 59, tzinfo=KST))
    assert fx_bar_complete(date(2026, 11, 23), datetime(2026, 11, 24, 9, 0, tzinfo=KST))
    # 영국 서머타임(UTC+1) 중에는 한 시간 빠르다
    assert fx_bar_complete(date(2026, 7, 1), datetime(2026, 7, 1, 23, 0, tzinfo=timezone.utc))
    assert not fx_bar_complete(date(2026, 7, 1), datetime(2026, 7, 1, 22, 59, tzinfo=timezone.utc))


def test_live_bar_is_not_stored_and_finished_days_are():
    df = frame(["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23"], [1333.01, 1330.40, 1334.79, 1346.38])
    recs, incomplete = fx_records(df, 1335.67, datetime(2026, 11, 23, 21, 30, tzinfo=KST))
    assert incomplete == [date(2026, 11, 23)]
    assert [(r["price_date"], r["close_price"], r["change_pct"]) for r in recs] == [
        (date(2026, 11, 18), 1333.01, -0.2),   # 11-17 1335.67 대비(DB 직전 종가)
        (date(2026, 11, 19), 1330.40, -0.2),
        (date(2026, 11, 20), 1334.79, 0.33),
    ]


def test_first_row_change_uses_db_previous_close_instead_of_null():
    # 예전 동작: 첫 행 등락률 None -> upsert가 이미 저장된 값(-0.04)을 NULL로 덮어씀
    df = frame(["2026-11-16", "2026-11-17"], [1333.25, 1335.67])
    recs, _ = fx_records(df, 1333.78, datetime(2026, 11, 23, 21, 30, tzinfo=KST))
    assert recs[0]["change_pct"] == -0.04 and recs[1]["change_pct"] == 0.18


def test_no_previous_close_keeps_first_row_change_none():
    df = frame(["2026-11-04", "2026-11-05"], [1400.0, 1410.5])
    recs, incomplete = fx_records(df, None, datetime(2026, 11, 23, 21, 30, tzinfo=KST))
    assert incomplete == [] and recs[0]["change_pct"] is None and recs[1]["change_pct"] == 0.75


def test_nan_close_rows_are_dropped_and_dates_are_not_shifted():
    df = frame(["2026-11-19", "2026-11-20"], [float("nan"), 1334.79])
    recs, _ = fx_records(df, 1330.40, datetime(2026, 11, 23, 21, 30, tzinfo=KST))
    assert [(r["price_date"], r["change_pct"]) for r in recs] == [(date(2026, 11, 20), 0.33)]
