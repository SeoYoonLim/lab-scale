"""50차: 코스피·코스닥 지수 저장 전 확정 검증(app/index_confirm.py, collectors.confirm_index_records).

시나리오 값은 2026-11-12~13 실제 관찰값이다(지수 캐시 커밋 기록·네이버 지수 일별):
- 11-12 14:43 캐시 코스피 5,305.47(장중) -> 23:01 5,342.73(확정, 네이버와 같음)
- 11-13 14:00 캐시 코스피 5,315.45(장중), 같은 시각 네이버 5,301.38(실시간)
"""
from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from app import collectors, index_confirm as ic, krx_calendar
from app.market_snapshot import cutoff_date

KST = krx_calendar.KST


def rec(d: str, v: float) -> dict:
    return {"price_date": date.fromisoformat(d), "close_price": v, "change_pct": None}


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=KST)


NAVER_1112_EVENING = {date(2026, 11, 11): 5375.36, date(2026, 11, 12): 5342.73}


# ---------- 장중 / 장 마감 전후 ----------

def test_today_row_before_close_is_intraday_even_if_naver_agrees():
    # 장중에는 두 원천이 우연히 같아도 확정값이 아니다
    recs = [rec("2026-11-12", 5342.73), rec("2026-11-13", 5315.45)]
    keep, held = ic.split(recs, {date(2026, 11, 12): 5342.73, date(2026, 11, 13): 5315.45}, at("2026-11-13T14:48"))
    assert [r["price_date"] for r in keep] == [date(2026, 11, 12)]
    assert [(h.day, h.status) for h in held] == [(date(2026, 11, 13), ic.INTRADAY)]


def test_just_after_close_is_still_intraday_until_delay():
    assert ic.intraday(date(2026, 11, 13), at("2026-11-13T15:35")) is True
    assert ic.intraday(date(2026, 11, 13), at("2026-11-13T15:40")) is False
    assert ic.intraday(date(2026, 11, 12), at("2026-11-13T09:00")) is False


def test_after_close_cache_still_intraday_snapshot_is_held_as_mismatch():
    # 11-12 15:40~23:00: 캐시는 14:43 장중 값, 네이버는 확정 종가
    recs = [rec("2026-11-11", 5375.36), rec("2026-11-12", 5305.47)]
    keep, held = ic.split(recs, NAVER_1112_EVENING, at("2026-11-12T19:48"))
    assert [r["price_date"] for r in keep] == [date(2026, 11, 11)]
    assert held[0].status == ic.MISMATCH and held[0].value == 5305.47 and held[0].reference == 5342.73


def test_after_evening_cache_update_confirmed_value_is_stored():
    recs = [rec("2026-11-11", 5375.36), rec("2026-11-12", 5342.73)]
    keep, held = ic.split(recs, NAVER_1112_EVENING, at("2026-11-12T23:30"))
    assert len(keep) == 2 and held == []
    assert {d.status for d in ic.decide(recs, NAVER_1112_EVENING, at("2026-11-12T23:30"))} == {ic.CONFIRMED}


def test_next_morning_run_confirms_previous_day():
    recs = [rec("2026-11-11", 5375.36), rec("2026-11-12", 5342.73)]
    keep, held = ic.split(recs, NAVER_1112_EVENING, at("2026-11-13T09:10"))
    assert len(keep) == 2 and held == []


# ---------- 주말·휴장일 ----------

def test_weekend_run_keeps_friday_as_latest_confirmed():
    recs = [rec("2026-11-12", 5342.73), rec("2026-11-13", 5300.00)]
    keep, held = ic.split(recs, {date(2026, 11, 12): 5342.73, date(2026, 11, 13): 5300.00}, at("2026-11-14T10:00"))
    assert len(keep) == 2 and held == []
    # 주말의 기대 거래일은 금요일이다(휴장일을 최신 거래일로 세지 않는다)
    assert krx_calendar.expected_trading_day(at("2026-11-14T10:00")) == date(2026, 11, 13)
    assert cutoff_date(at("2026-11-14T10:00")) == date(2026, 11, 14)


def test_holiday_is_not_expected_trading_day_and_latest_before_holiday_is_confirmed():
    # 2026-12-25(성탄절, KRX 휴장) 오전: 기대 거래일 12-24, 12-24 행은 확정
    assert krx_calendar.is_trading_day(date(2026, 12, 25)) is False
    assert krx_calendar.expected_trading_day(at("2026-12-25T10:00")) == date(2026, 12, 24)
    recs = [rec("2026-12-23", 5000.0), rec("2026-12-24", 5010.0)]
    keep, held = ic.split(recs, {date(2026, 12, 23): 5000.0, date(2026, 12, 24): 5010.0}, at("2026-12-25T10:00"))
    assert len(keep) == 2 and held == []


def test_holiday_row_from_source_is_still_rejected_by_validation():
    from app import ingest_validation as V

    checked = V.check_index("KS11", [rec("2026-12-25", 5010.0)], {}, 5000.0, date(2026, 12, 26))
    assert checked[0].status == V.QUARANTINED and "non_trading_day" in [i.code for i in checked[0].verdict.issues]


# ---------- 원천 장애·대조 불가 ----------

def test_naver_unavailable_holds_only_latest_row():
    recs = [rec("2026-11-10", 5239.13), rec("2026-11-11", 5375.36), rec("2026-11-12", 5342.73)]
    keep, held = ic.split(recs, None, at("2026-11-13T09:10"))
    assert [r["price_date"] for r in keep] == [date(2026, 11, 10), date(2026, 11, 11)]
    assert [(h.day, h.status) for h in held] == [(date(2026, 11, 12), ic.UNVERIFIED)]


def test_rows_older_than_naver_window_are_trusted_only_before_latest():
    recs = [rec("2026-10-01", 3000.0), rec("2026-11-12", 5342.73)]
    ds = {d.day: d.status for d in ic.decide(recs, {date(2026, 11, 12): 5342.73}, at("2026-11-13T09:10"))}
    assert ds == {date(2026, 10, 1): ic.TRUSTED_OLDER, date(2026, 11, 12): ic.CONFIRMED}


def test_latest_missing_in_naver_is_unverified():
    recs = [rec("2026-11-11", 5375.36), rec("2026-11-12", 5342.73)]
    keep, held = ic.split(recs, {date(2026, 11, 11): 5375.36}, at("2026-11-13T09:10"))
    assert len(keep) == 1 and held[0].status == ic.UNVERIFIED


def test_older_row_that_disagrees_with_naver_is_held_too():
    recs = [rec("2026-11-11", 5346.07), rec("2026-11-12", 5342.73)]
    keep, held = ic.split(recs, NAVER_1112_EVENING, at("2026-11-13T09:10"))
    assert [h.day for h in held] == [date(2026, 11, 11)] and held[0].status == ic.MISMATCH


def test_tolerance_is_two_decimals():
    recs = [rec("2026-11-12", 5342.734)]
    assert ic.split(recs, {date(2026, 11, 12): 5342.73}, at("2026-11-13T09:10"))[1] == []
    recs = [rec("2026-11-12", 5342.74)]
    assert ic.split(recs, {date(2026, 11, 12): 5342.73}, at("2026-11-13T09:10"))[1][0].status == ic.MISMATCH


def test_fetch_naver_closes_parses_and_rejects_bad_payload():
    rows = [{"localTradedAt": "2026-11-12", "closePrice": "5,342.73"}, {"localTradedAt": "2026-11-11", "closePrice": "5,375.36"}]
    calls = []
    got = ic.fetch_naver_closes("KS11", get=lambda url, params: calls.append((url, params)) or rows)
    assert got == NAVER_1112_EVENING and "KOSPI" in calls[0][0]
    assert ic.fetch_naver_closes("XX99", get=lambda u, p: rows) is None
    with pytest.raises(ValueError):
        ic.fetch_naver_closes("KQ11", get=lambda u, p: {"error": "x"})


def test_confirm_index_records_survives_naver_failure(capsys):
    def boom(url, params):
        raise ConnectionError("naver down")

    recs = [rec("2026-11-11", 5375.36), rec("2026-11-12", 5342.73)]
    keep, held = collectors.confirm_index_records("KS11", recs, at("2026-11-13T09:10"), naver_get=boom)
    assert len(keep) == 1 and held == [{"date": "2026-11-12", "value": 5342.73, "status": ic.UNVERIFIED, "reference": None}]
    out = capsys.readouterr().out
    assert "확정 대조 실패" in out and "저장 보류 2026-11-12" in out


def _frame(rows: list[tuple[str, float]]):
    idx = pd.to_datetime([d for d, _ in rows])
    return pd.DataFrame({"Close": [v for _, v in rows], "Change": [0.0] * len(rows)}, index=idx)


def test_collector_returns_without_db_when_every_row_is_held(monkeypatch):
    # 장중에 받은 행이 오늘 하나뿐이면 DB를 열지 않고(기존 값 유지) 보류만 돌려준다
    monkeypatch.setattr(collectors, "SessionLocal", lambda: pytest.fail("DB를 열면 안 된다"))
    res = collectors.fetch_and_save_market_index(
        "KS11", start_date="2026-11-13", end_date="2026-11-13", now=at("2026-11-13T14:48"),
        reader=lambda s, a, b: _frame([("2026-11-13", 5315.45)]),
        naver_get=lambda u, p: [{"localTradedAt": "2026-11-13", "closePrice": "5,301.38"}])
    assert res.ok and res.saved == 0 and res.held_dates[0]["status"] == ic.INTRADAY


def test_collector_reports_all_source_failure_without_writing(monkeypatch):
    monkeypatch.setattr(collectors, "SessionLocal", lambda: pytest.fail("DB를 열면 안 된다"))

    def down(symbol, a, b):
        raise ConnectionError("cache down")

    res = collectors.fetch_and_save_market_index("KS11", start_date="2026-11-10", end_date="2026-11-13",
                                                 now=at("2026-11-13T09:10"), reader=down)
    assert res.ok is False and "모든 지수 소스 실패" in res.error


def test_collect_script_prints_held(monkeypatch, capsys):
    from scripts import collect_market_index as cmi

    held = [{"date": "2026-11-13", "value": 5315.45, "status": "intraday", "reference": None}]
    monkeypatch.setattr(cmi, "fetch_and_save_market_index",
                        lambda code, days, dry_run: SimpleNamespace(ok=True, fetched=0, saved=0, source="fdr:KS11", held_dates=held))
    monkeypatch.setattr(cmi, "backfill_company_market", lambda dry_run: (0, 0))
    monkeypatch.setattr("sys.argv", ["collect_market_index", "--dry-run"])
    with pytest.raises(SystemExit) as e:
        cmi.main()
    assert e.value.code == 0 and "저장 보류 1건: 2026-11-13(intraday)" in capsys.readouterr().out


# ---------- DB: 확정되지 않은 값이 기존 확정값을 덮어쓰지 않는다 ----------

@pytest.mark.integration
def test_held_row_keeps_existing_confirmed_value():
    """2020-01-02·03(달력 범위 밖, 거래일로 취급)에 기존 확정 행을 넣고, 장중 스냅숏이 섞인 수집을 돌린다. 끝나면 지운다."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM market_index WHERE index_code='KS11' AND price_date < '2021-01-01'"))
        db.execute(text("INSERT INTO market_index (index_code, price_date, close_price, change_pct) "
                        "VALUES ('KS11', '2020-01-03', 2176.46, 0.0)"))
        db.commit()
        res = collectors.fetch_and_save_market_index(
            "KS11", start_date="2020-01-02", end_date="2020-01-03", now=at("2020-01-03T14:00"),
            reader=lambda s, a, b: _frame([("2020-01-02", 2175.17), ("2020-01-03", 2170.00)]),
            naver_get=lambda u, p: [{"localTradedAt": "2020-01-02", "closePrice": "2,175.17"},
                                    {"localTradedAt": "2020-01-03", "closePrice": "2,168.11"}])
        assert res.ok and res.saved == 1 and [h["date"] for h in res.held_dates] == ["2020-01-03"]
        rows = dict(db.execute(text("SELECT price_date::text, close_price FROM market_index "
                                    "WHERE index_code='KS11' AND price_date < '2021-01-01'")).all())
        assert {k: float(v) for k, v in rows.items()} == {"2020-01-02": 2175.17, "2020-01-03": 2176.46}
    finally:
        db.rollback()
        db.execute(text("DELETE FROM market_index WHERE index_code='KS11' AND price_date < '2021-01-01'"))
        db.commit()
        db.close()


# ---------- 화면(시장 요약): 보류된 날은 직전 확정일로 보이고, 값이 없으면 0이 아니라 null ----------

def test_market_overview_after_close_shows_previous_confirmed_day_as_delayed():
    from app import market_snapshot as ms

    now = at("2026-11-13T16:00")  # 오늘 지수는 아직 보류(캐시 장중 값) -> 저장된 최신은 11-12
    rows = {"KS11": [(date(2026, 11, 12), 5342.73, None), (date(2026, 11, 11), 5375.36, None)]}
    out = ms.build(rows, date(2026, 11, 12), {"KOSPI": (100, 150, 10, 0, 260)}, now)
    ks = out["indices"][0]
    assert ks["date"] == date(2026, 11, 12) and ks["lag_business_days"] == 1 and ks["change"] == -32.63
    assert out["freshness"]["status"] == ms.STATUS_DELAYED and out["expected_date"] == date(2026, 11, 13)
    kq = out["indices"][1]
    assert kq["date"] is None and kq["close"] is None and kq["change"] is None  # 값을 만들지 않는다
    assert out["status"] == ms.PARTIAL and "KQ11" in out["missing"]


def test_market_overview_during_session_excludes_today_rows():
    from app import market_snapshot as ms

    assert ms.cutoff_date(at("2026-11-13T14:48")) == date(2026, 11, 12)
    assert ms.cutoff_date(at("2026-11-13T15:40")) == date(2026, 11, 13)
