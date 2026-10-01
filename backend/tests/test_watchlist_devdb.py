"""watchlist.add_item/remove_item/list_items를 실제 dev DB로 검증한다(CRUD라 단위 테스트보다 이쪽이 적합)."""

import pytest

from app.db.session import SessionLocal
from app.models import Watchlist
from app.tools.company_resolver import resolve_company
from app.watchlist import add_item, list_items, remove_item

TEST_DEVICE = "pytest-watchlist-devdb"


@pytest.fixture
def watch_db(dev_db):
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        dev_db.rollback()
        dev_db.query(Watchlist).filter(Watchlist.device_id == TEST_DEVICE).delete()
        dev_db.commit()


@pytest.mark.integration
class TestWatchlistDevDB:
    def test_add_then_list_then_remove(self, watch_db):
        company = resolve_company(watch_db, "삼성전자").company

        item, already_existed = add_item(watch_db, TEST_DEVICE, company.id)
        assert already_existed is False
        assert item.company_id == company.id

        items = list_items(watch_db, TEST_DEVICE)
        assert len(items) == 1
        assert items[0]["ticker"] == company.ticker
        assert items[0]["company_name"] == "삼성전자"
        # 주가가 실제로 수집돼 있으므로(README: 9/23까지) 최근 종가가 채워져야 한다
        assert items[0]["latest_close"] is not None

        assert remove_item(watch_db, TEST_DEVICE, company.id) is True
        assert list_items(watch_db, TEST_DEVICE) == []

    def test_adding_twice_reports_already_existed_without_duplicating(self, watch_db):
        company = resolve_company(watch_db, "삼성전자").company
        add_item(watch_db, TEST_DEVICE, company.id)
        _, already_existed = add_item(watch_db, TEST_DEVICE, company.id)
        assert already_existed is True
        assert len(list_items(watch_db, TEST_DEVICE)) == 1

    def test_removing_unregistered_item_returns_false(self, watch_db):
        company = resolve_company(watch_db, "삼성전자").company
        assert remove_item(watch_db, TEST_DEVICE, company.id) is False

    def test_list_is_empty_for_unknown_device(self, watch_db):
        assert list_items(watch_db, "pytest-device-with-nothing") == []
