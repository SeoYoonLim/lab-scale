"""GET /api/briefing을 실제 dev DB로 검증한다: 읽기 전용, 소유자 격리, 쿼리 수 상한, 응답 숫자의 독립 재계산 대조.

테스트 계정은 captest_ + 임의값이고(signup 픽스처가 끝나면 계정·관심종목·모의투자 계좌를 지운다) 시세·뉴스는 읽기만 한다.
수집 데이터(시세·뉴스)가 필요한 테스트에만 needs_collected_data를 붙인다(CI의 빈 DB에는 시세가 없다).
"""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

import app.portfolio as portfolio_module
from app.briefing_db import get_briefing
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import Company, Holding, VirtualAccount, Watchlist
from briefing_recompute import recheck_briefing

client = TestClient(app, raise_server_exceptions=False)

MAX_QUERIES = 10  # 계좌 1 + 보유 1 + 관심 1 + 시세 1 + 뉴스 1 + 공시 1 + 지수 1 + 환율 1 (+ ETF 이름 1)


@pytest.fixture
def captest(signup):
    def _make():
        return signup(f"captest_{secrets.token_hex(5)}")

    return _make


def owner_key(user) -> str:
    return f"user:{user['id']}"


def count_queries(fn):
    statements = []

    def on_execute(conn, cursor, statement, *_):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    return len(statements), result


def seed_rows(dev_db, key, holding_ids, watch_ids):
    """계좌, 보유종목, 관심종목을 DB에 직접 넣는다(주문 체결 로직과 무관하게 행 수만 맞추려는 용도)."""
    dev_db.add(VirtualAccount(device_id=key, cash_balance=5_000_000))
    dev_db.flush()
    for cid in holding_ids:
        dev_db.add(Holding(device_id=key, company_id=cid, quantity=3, avg_price=10_000))
    for cid in watch_ids:
        dev_db.add(Watchlist(device_id=key, company_id=cid))
    dev_db.commit()


@pytest.mark.integration
class TestBriefingDevDB:
    def test_requires_login(self):
        assert client.get("/api/briefing").status_code == 401

    def test_brand_new_user_gets_empty_state_and_no_account_is_created(self, captest, dev_db):
        user = captest()
        r = client.get("/api/briefing", headers=user["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["message"] == "관심종목을 추가하면 개인화된 브리핑을 볼 수 있습니다."
        assert body["holdings"] == [] and body["watchlist"] == []
        assert body["portfolio"]["has_account"] is False and body["portfolio"]["total_asset"] is None
        # 읽기 전용: 브리핑을 불러도 모의투자 계좌가 생기지 않는다(GET /api/portfolio와 다르다)
        dev_db.rollback()
        assert dev_db.execute(text("SELECT count(*) FROM virtual_account WHERE device_id = :k"), {"k": owner_key(user)}).scalar() == 0

    def test_other_users_holdings_and_watchlist_never_appear(self, captest, dev_db):
        a, b = captest(), captest()
        ids = [i for (i,) in dev_db.execute(text("SELECT id FROM company ORDER BY id LIMIT 4"))]
        seed_rows(dev_db, owner_key(b), holding_ids=ids[:2], watch_ids=ids[2:4])
        tickers_b = {t for (t,) in dev_db.execute(text("SELECT ticker FROM company WHERE id = ANY(:ids)"), {"ids": ids})}

        mine = client.get("/api/briefing", headers=a["headers"]).json()
        assert mine["holdings"] == [] and mine["watchlist"] == [] and mine["portfolio"]["has_account"] is False
        theirs = client.get("/api/briefing", headers=b["headers"]).json()
        assert {i["ticker"] for i in theirs["holdings"] + theirs["watchlist"]} == tickers_b
        assert {i["ticker"] for i in theirs["holdings"]} == tickers_b - {i["ticker"] for i in theirs["watchlist"]}

    def test_query_count_does_not_grow_with_the_number_of_instruments(self, captest, dev_db):
        small, large = captest(), captest()
        ids = [i for (i,) in dev_db.execute(text("SELECT id FROM company ORDER BY id LIMIT 22"))]
        assert len(ids) == 22
        seed_rows(dev_db, owner_key(small), holding_ids=ids[:1], watch_ids=ids[1:2])
        seed_rows(dev_db, owner_key(large), holding_ids=ids[2:12], watch_ids=ids[12:22])

        def run(user):
            db = SessionLocal()
            try:
                return get_briefing(db, owner_key(user), 3)
            finally:
                db.close()

        n_small, r_small = count_queries(lambda: run(small))
        n_large, r_large = count_queries(lambda: run(large))
        assert len(r_small["holdings"]) + len(r_small["watchlist"]) == 2
        assert len(r_large["holdings"]) + len(r_large["watchlist"]) == 20
        assert n_large == n_small <= MAX_QUERIES, (n_small, n_large)

    @pytest.mark.needs_collected_data
    def test_numbers_match_an_independent_recalculation(self, captest, dev_db, monkeypatch):
        user = captest()
        key = owner_key(user)

        def latest_close(ticker):
            return float(
                dev_db.execute(
                    text(
                        "SELECT p.close_price FROM stock_price p JOIN company c ON c.id = p.company_id "
                        "WHERE c.ticker = :t ORDER BY p.price_date DESC LIMIT 1"
                    ),
                    {"t": ticker},
                ).scalar()
            )

        # 모의 주문은 실제 체결 로직(place_order)을 쓰되, 체결가는 장중 시세 대신 DB 최근 종가로 고정한다.
        def fake_price(db, company):
            return {"current_price": latest_close(company.ticker), "change_amount": 0.0, "change_pct": 0.0,
                    "as_of": "t", "is_realtime": False, "source": "fallback"}

        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price)
        db = SessionLocal()
        try:
            for ticker, qty in (("005930", 10), ("069500", 20), ("122630", 5)):  # 삼성전자, KODEX 200, KODEX 레버리지
                company = db.query(Company).filter(Company.ticker == ticker).one()
                portfolio_module.place_order(db, key, company, "buy", qty)
        finally:
            db.close()
        for ticker in ("000660", "114800", "005930"):  # SK하이닉스, KODEX 인버스, (보유와 겹침)
            dev_db.add(Watchlist(device_id=key, company_id=dev_db.query(Company.id).filter(Company.ticker == ticker).scalar()))
        dev_db.commit()

        for news_limit in (3, 5, 1):
            r = client.get("/api/briefing", headers=user["headers"], params={"news_limit": news_limit})
            assert r.status_code == 200
            body = r.json()
            assert {i["ticker"] for i in body["holdings"]} == {"005930", "069500", "122630"}
            assert {i["ticker"] for i in body["watchlist"]} == {"000660", "114800"}  # 겹친 삼성전자는 holdings에만
            rc = recheck_briefing(dev_db, key, body, news_limit)
            dev_db.rollback()
            assert rc.problems == [], rc.problems
            assert rc.checked > 100
            by = {i["ticker"]: i for i in body["holdings"] + body["watchlist"]}
            assert by["122630"]["notice"] and by["114800"]["notice"]  # 레버리지, 인버스 ETF 주의 문구
            assert by["069500"]["notice"] is None and by["005930"]["notice"] is None
            assert body["portfolio"]["has_account"] is True
            assert body["portfolio"]["initial_balance"] == 10_000_000
            assert body["disclaimer"]
