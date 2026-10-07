"""app.companies.search_companies를 실제 dev DB로 검증한다(검색+최근 종가 조인이라 단위 테스트보다 이쪽이 적합)."""

import pytest

from app.companies import search_companies


@pytest.mark.integration
class TestSearchCompaniesDevDB:
    def test_no_query_returns_top_limit(self, dev_db):
        items = search_companies(dev_db, None, limit=5)
        assert len(items) == 5
        for it in items:
            assert it["ticker"] and it["name"]

    def test_blank_query_behaves_like_no_query(self, dev_db):
        assert search_companies(dev_db, "   ", limit=5) == search_companies(dev_db, None, limit=5)

    def test_partial_name_match(self, dev_db):
        items = search_companies(dev_db, "삼성전자", limit=10)
        assert any(it["name"] == "삼성전자" for it in items)

    def test_ticker_match(self, dev_db):
        items = search_companies(dev_db, "005930", limit=10)
        assert any(it["ticker"] == "005930" for it in items)

    def test_no_results_for_unknown_query(self, dev_db):
        assert search_companies(dev_db, "존재하지않는종목이름xyz", limit=10) == []

    def test_alias_query_finds_registered_name(self, dev_db):
        items = search_companies(dev_db, "네이버", limit=10)
        assert any(it["name"] == "NAVER" for it in items)

    def test_limit_is_respected(self, dev_db):
        assert len(search_companies(dev_db, None, limit=3)) <= 3

    def test_latest_close_is_filled_when_price_data_exists(self, dev_db):
        items = search_companies(dev_db, "삼성전자", limit=1)
        assert items[0]["latest_close"] is not None
