"""과거 시세 백필(scripts/backfill_history.py)의 순수 부분과 가격 품질 점검(app/price_quality.py). DB·외부 호출 없음."""

import json
from datetime import date, datetime

import pytest

import scripts.backfill_history as bf
from app.data_status import REFRESH_STEPS
from app.price_quality import check_series
from scripts.refresh_data import KST, STEP_ORDER


class TestPeriod:
    @pytest.mark.parametrize("now, end", [
        (datetime(2026, 10, 29, 15, 29, tzinfo=KST), date(2026, 10, 28)),  # 목요일 장중 → 어제
        (datetime(2026, 10, 29, 8, 0, tzinfo=KST), date(2026, 10, 28)),    # 개장 전 → 어제
        (datetime(2026, 10, 29, 15, 30, tzinfo=KST), date(2026, 10, 29)),  # 마감 후 → 오늘
        (datetime(2026, 10, 31, 11, 0, tzinfo=KST), date(2026, 10, 31)),   # 토요일
    ])
    def test_end_never_includes_an_open_session(self, now, end):
        assert bf.period(now, 3) == (end.replace(year=end.year - 3), end)

    def test_leap_day(self):
        assert bf.period(datetime(2028, 2, 29, 18, 0, tzinfo=KST), 3) == (date(2025, 2, 28), date(2028, 2, 29))

    def test_refuses_market_hours_and_bad_years(self, monkeypatch):
        class Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 29, 10, 0, tzinfo=KST)

        monkeypatch.setattr(bf, "datetime", Fixed)
        with pytest.raises(SystemExit, match="장중"):
            bf.main(["--dry-run"])
        with pytest.raises(SystemExit, match="1~10"):
            bf.main(["--years", "0"])

    def test_end_option_cannot_be_later_than_the_default(self, monkeypatch):
        class Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 29, 10, 0, tzinfo=KST)

        monkeypatch.setattr(bf, "datetime", Fixed)
        with pytest.raises(SystemExit, match="이전이어야"):
            bf.main(["--allow-market-hours", "--end", "2026-10-29"])


class TestState:
    def test_resume_only_for_the_same_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bf, "STATE_FILE", tmp_path / "state.json")
        s = bf.load_state(date(2023, 10, 28), date(2026, 10, 28), resume=True)
        assert s == {"range": "2023-10-28~2026-10-28", "done": {}}
        s["done"]["005930"] = {"ok": True}
        bf.save_state(s)
        assert bf.load_state(date(2023, 10, 28), date(2026, 10, 28), resume=True)["done"] == {"005930": {"ok": True}}
        assert bf.load_state(date(2023, 10, 28), date(2026, 10, 28), resume=False)["done"] == {}
        assert bf.load_state(date(2021, 10, 28), date(2026, 10, 28), resume=True)["done"] == {}  # 기간이 다르면 처음부터
        assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["range"] == "2023-10-28~2026-10-28"


class TestQuality:
    D = [date(2026, 1, d) for d in range(5, 10)] + [date(2026, 1, d) for d in range(12, 17)]

    def test_clean(self):
        assert check_series(list(zip(self.D, [100, 101, 99, 102, 103, 104, 103, 105, 106, 107]))) == []

    def test_jump_over_30_percent(self):
        issues = check_series(list(zip(self.D[:4], [100, 100, 20, 21])))
        assert [(i.kind, i.date, i.value) for i in issues] == [("jump", date(2026, 1, 7), -80.0)]
        assert check_series(list(zip(self.D[:2], [100, 130]))) == []  # 정확히 30%는 가격제한폭 안
        assert check_series(list(zip(self.D[:2], [100, 130.4]))) == []  # 수정주가로 본 상한가(30.0x%)는 여유 안
        assert [i.value for i in check_series(list(zip(self.D[:2], [100, 131])))] == [31.0]
        assert [i.value for i in check_series(list(zip(self.D[:2], [100, 69])))] == [-31.0]

    def test_duplicates_out_of_order_non_positive(self):
        rows = [(self.D[0], 100), (self.D[1], 0), (self.D[1], 101), (self.D[0], 100), (self.D[2], -5)]
        kinds = sorted(i.kind for i in check_series(rows))
        assert kinds == ["duplicate_date", "duplicate_date", "non_positive", "non_positive"]
        rows = [(self.D[2], 100), (self.D[1], 100)]
        assert [i.kind for i in check_series(rows)] == ["out_of_order"]

    def test_flat_run(self):
        issues = check_series(list(zip(self.D, [100, 50, 50, 50, 50, 50, 51, 52, 53, 54])))
        flat = [i for i in issues if i.kind == "flat_run"]
        assert len(flat) == 1 and flat[0].date == self.D[1] and flat[0].value == 5
        assert check_series(list(zip(self.D[:4], [50, 50, 50, 50]))) == []  # 4일은 기준(5) 미만


def test_status_reads_only_refresh_steps():
    """/api/status의 last_refresh는 refresh_data 단계만 읽는다. 백필 행('backfill')이 '마지막 갱신'으로 보이지 않게."""
    assert REFRESH_STEPS == STEP_ORDER
    assert "backfill" not in REFRESH_STEPS
