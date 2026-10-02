"""app.discovery 단위 테스트. rank_* 함수는 DB 없는 순수 로직, get_trending은 실제 dev DB로 검증한다."""

import pytest

from app.discovery import VOLUME_SURGE_MIN_RATIO, get_trending, rank_gainers, rank_losers, rank_volume_surge


def item(ticker, change_pct=None, volume_ratio=None):
    return {
        "ticker": ticker,
        "company_name": f"회사{ticker}",
        "close_price": 1000.0,
        "change_pct": change_pct,
        "volume": 100,
        "avg_volume": 50.0,
        "volume_ratio": volume_ratio,
    }


class TestRankGainers:
    def test_sorts_descending_by_change_pct(self):
        items = [item("a", 1.0), item("b", 5.0), item("c", 3.0)]
        ranked = rank_gainers(items, limit=10)
        assert [i["ticker"] for i in ranked] == ["b", "c", "a"]

    def test_limit_is_respected(self):
        items = [item("a", 1.0), item("b", 5.0), item("c", 3.0)]
        assert [i["ticker"] for i in rank_gainers(items, limit=2)] == ["b", "c"]

    def test_items_without_change_pct_are_excluded(self):
        items = [item("a", None), item("b", 2.0)]
        assert [i["ticker"] for i in rank_gainers(items, limit=10)] == ["b"]

    def test_empty_input(self):
        assert rank_gainers([], limit=10) == []


class TestRankLosers:
    def test_sorts_ascending_by_change_pct(self):
        items = [item("a", -1.0), item("b", -8.0), item("c", 2.0)]
        ranked = rank_losers(items, limit=10)
        assert [i["ticker"] for i in ranked] == ["b", "a", "c"]

    def test_items_without_change_pct_are_excluded(self):
        items = [item("a", None), item("b", -2.0)]
        assert [i["ticker"] for i in rank_losers(items, limit=10)] == ["b"]


class TestRankVolumeSurge:
    def test_filters_below_min_ratio_and_sorts_descending(self):
        items = [item("a", volume_ratio=1.5), item("b", volume_ratio=4.0), item("c", volume_ratio=2.0)]
        ranked = rank_volume_surge(items, limit=10)
        assert [i["ticker"] for i in ranked] == ["b", "c"]  # a(1.5)는 기본 임계값(2.0) 미만이라 제외

    def test_items_without_volume_ratio_are_excluded(self):
        items = [item("a", volume_ratio=None), item("b", volume_ratio=3.0)]
        assert [i["ticker"] for i in rank_volume_surge(items, limit=10)] == ["b"]

    def test_custom_min_ratio(self):
        items = [item("a", volume_ratio=1.5), item("b", volume_ratio=4.0)]
        ranked = rank_volume_surge(items, limit=10, min_ratio=1.0)
        assert [i["ticker"] for i in ranked] == ["b", "a"]

    def test_exactly_at_threshold_is_included(self):
        items = [item("a", volume_ratio=VOLUME_SURGE_MIN_RATIO)]
        assert len(rank_volume_surge(items, limit=10)) == 1

    def test_limit_is_respected(self):
        items = [item(str(i), volume_ratio=float(i)) for i in range(2, 10)]
        assert len(rank_volume_surge(items, limit=3)) == 3


class TestGetTrendingValidation:
    def test_unknown_category_is_not_found(self):
        r = get_trending(db=None, category="bogus")
        assert r == {
            "category": "bogus",
            "found": False,
            "message": "알 수 없는 category입니다. gainers, losers, volume_surge 중 하나를 지정해주세요.",
        }


@pytest.mark.integration
class TestGetTrendingDevDB:
    @pytest.mark.parametrize("category", ["gainers", "losers", "volume_surge"])
    def test_real_data_shape_and_order(self, dev_db, category):
        from sqlalchemy import text

        if dev_db.execute(text("SELECT count(*) FROM stock_price")).scalar() == 0:
            pytest.skip("stock_price가 비어 있음")

        r = get_trending(dev_db, category, limit=10)
        assert r["found"] is True
        assert r["category"] == category
        assert r["price_date"]
        assert r["window_days"] > 0

        items = r["items"]
        assert len(items) <= 10
        for it in items:
            assert it["ticker"] and it["company_name"]
            assert it["close_price"] > 0

        if category == "gainers":
            pcts = [i["change_pct"] for i in items]
            assert pcts == sorted(pcts, reverse=True)
        elif category == "losers":
            pcts = [i["change_pct"] for i in items]
            assert pcts == sorted(pcts)
        else:
            ratios = [i["volume_ratio"] for i in items]
            assert ratios == sorted(ratios, reverse=True)
            assert all(r >= VOLUME_SURGE_MIN_RATIO for r in ratios)

    def test_latest_date_matches_max_stock_price_date(self, dev_db):
        from sqlalchemy import text

        if dev_db.execute(text("SELECT count(*) FROM stock_price")).scalar() == 0:
            pytest.skip("stock_price가 비어 있음")

        r = get_trending(dev_db, "gainers", limit=5)
        expected = dev_db.execute(text("SELECT MAX(price_date) FROM stock_price")).scalar()
        assert r["price_date"] == expected.isoformat()
