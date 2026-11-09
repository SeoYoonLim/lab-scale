"""GET /api/portfolio/exposure를 실제 dev DB로 검증한다: 읽기 전용, 소유자 격리, 쿼리 수 고정, 숫자 대조, 속도.

- 테스트 계정은 captest_ + 임의값이고, signup 픽스처가 끝나면 계정·모의투자 계좌(보유는 FK CASCADE)를 지운다. 가짜 종목·ETF(company 행)와
  그 시세·구성종목도 끝나면 지운다(FK CASCADE). 스스로 데이터를 만들므로 수집 데이터가 없는 CI에서도 돈다.
- 실제 수집 데이터(삼성전자 시세 + TIGER 200 etf_holding)를 읽는 테스트만 needs_collected_data다. 응답의 삼성전자 실질 금액을
  app.exposure를 쓰지 않고 SQL만으로 다시 계산해 대조한다.
- 주문은 실제 주문 API로 내되 체결가는 외부 시세 대신 DB 종가로 고정한다(이 테스트는 외부 사이트에 요청하지 않는다).
"""

import secrets
import time
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

import app.portfolio as portfolio_module
from app.briefing import KST
from app.db.session import SessionLocal, engine
from app.exposure_db import get_exposure
from app.main import app
from app.models import Company, EtfHolding, Holding, StockPrice, VirtualAccount
from app.models.virtual_account import INITIAL_BALANCE
from evals.metrics import forbidden_hits

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration

MAX_QUERIES = 3  # 보유 1 + 종가 1 + 구성종목 1
PRICE_DAY = datetime.now(KST).date() - timedelta(days=5)


@pytest.fixture
def captest(signup):
    def _make():
        return signup(f"captest_{secrets.token_hex(6)}")

    return _make


def key(user) -> str:
    return f"user:{user['id']}"


@pytest.fixture
def fake_company(dev_db):
    """captest 가짜 종목/ETF. 끝나면 company(와 FK CASCADE로 시세·구성종목·보유)를 지운다."""
    made = []

    def _make(name, asset_type="stock", price=None):
        ticker = ("T" + secrets.token_hex(4))[:9].upper()
        c = Company(ticker=ticker, name=f"{name} {secrets.token_hex(2)}", asset_type=asset_type)
        dev_db.add(c)
        dev_db.flush()
        if price is not None:
            dev_db.add(StockPrice(company_id=c.id, price_date=PRICE_DAY, close_price=price, volume=1))
        dev_db.commit()
        made.append(c.id)
        return c

    yield _make
    dev_db.rollback()
    for cid in made:
        dev_db.query(Company).filter(Company.id == cid).delete()
    dev_db.commit()


def hold(dev_db, owner, rows, cash=1_000_000):
    """rows: (company, quantity)."""
    dev_db.add(VirtualAccount(device_id=owner, cash_balance=cash))
    dev_db.flush()
    for c, q in rows:
        dev_db.add(Holding(device_id=owner, company_id=c.id, quantity=q, avg_price=1))
    dev_db.commit()


def put_constituents(dev_db, etf, rows, as_of=PRICE_DAY, source="mirae_tiger"):
    for name, code, w in rows:
        dev_db.add(EtfHolding(company_id=etf.id, as_of_date=as_of, holding_name=name, holding_code=code, weight_pct=w, source=source))
    dev_db.commit()


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


def run(owner_key, limit=10):
    db = SessionLocal()
    try:
        return get_exposure(db, owner_key, limit)
    finally:
        db.close()


class TestExposureDevDB:
    def test_requires_login(self):
        assert client.get("/api/portfolio/exposure").status_code == 401
        assert client.get("/api/portfolio/exposure?limit=0").status_code == 401

    def test_limit_out_of_range_is_422(self, captest):
        user = captest()
        for q in ("0", "51", "abc"):
            assert client.get(f"/api/portfolio/exposure?limit={q}", headers=user["headers"]).status_code == 422

    def test_new_user_gets_message_and_no_account_is_created(self, captest, dev_db):
        user = captest()
        r = client.get("/api/portfolio/exposure", headers=user["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["message"] is not None and body["companies"] == [] and body["summary"]["total_eval_amount"] == 0
        dev_db.rollback()
        assert dev_db.execute(text("SELECT count(*) FROM virtual_account WHERE device_id = :k"), {"k": key(user)}).scalar() == 0

    def test_hand_example_with_fake_companies(self, captest, dev_db, fake_company):
        """삼성전자(가짜) 10주 x 70,000 + 가짜 ETF 100주 x 30,000: 손계산과 같은 숫자가 DB를 거쳐도 나온다."""
        user = captest()
        s = fake_company("captest삼성", price=70_000)
        s2 = fake_company("captest하이닉스")
        etf = fake_company("TIGER captest200", "etf", price=30_000)
        lev = fake_company("KODEX captest레버리지", "etf", price=20_000)
        nodata = fake_company("RISE captest", "etf", price=10_000)
        put_constituents(dev_db, etf, [(s.name, s.ticker, 30.0), (s2.name, s2.ticker, 20.0), ("Apple Inc", "AAPL US EQUITY", 5.0), ("해외 코드없음", None, 5.0)])
        put_constituents(dev_db, lev, [(s.name, s.ticker, 50.0)])  # 데이터가 있어도 레버리지는 분해하지 않는다
        # 옛 기준일 스냅샷은 무시되어야 한다
        put_constituents(dev_db, etf, [(s.name, s.ticker, 99.0)], as_of=PRICE_DAY - timedelta(days=30))
        hold(dev_db, key(user), [(s, 10), (etf, 100), (lev, 10), (nodata, 10)])

        body = client.get("/api/portfolio/exposure?limit=50", headers=user["headers"]).json()
        total = 700_000 + 3_000_000 + 200_000 + 100_000
        assert body["summary"]["total_eval_amount"] == total and body["summary"]["undecomposed_etf_amount"] == 300_000
        top = body["companies"][0]
        assert top["name"] == s.name and top["code"] == s.ticker and top["linked"] is True
        assert top["direct_amount"] == 700_000 and top["via_etf_amount"] == 900_000 and top["total_amount"] == 1_600_000
        assert top["effective_weight_pct"] == round(1_600_000 / total * 100, 2) and top["via_etfs"][0]["ticker"] == etf.ticker
        by = {c["name"]: c for c in body["companies"]}
        assert by[s2.name]["total_amount"] == 600_000 and by["Apple Inc"]["linked"] is False and by["해외 코드없음"]["code"] is None
        assert [o["name"] for o in body["overlaps"]] == [s.name]
        assert {u["ticker"]: u["reason_code"] for u in body["undecomposed_etfs"]} == {lev.ticker: "leveraged_inverse", nodata.ticker: "no_holdings_data"}
        sm = body["summary"]
        assert sm["other_amount"] == 3_000_000 * 0.40 == 1_200_000
        assert abs(sm["companies_amount"] + sm["other_amount"] + sm["undecomposed_etf_amount"] - total) <= 0.02
        assert body["decomposed_etfs"][0]["holdings_as_of"] == PRICE_DAY.isoformat() and body["decomposed_etfs"][0]["constituent_count"] == 4
        for f in body["flags"]:
            assert forbidden_hits(f["message"]) == []

    def test_other_users_holdings_never_appear(self, captest, dev_db, fake_company):
        a, b = captest(), captest()
        x = fake_company("captest보유", price=1000)
        hold(dev_db, key(b), [(x, 5)])
        mine = client.get("/api/portfolio/exposure", headers=a["headers"]).json()
        theirs = client.get("/api/portfolio/exposure", headers=b["headers"]).json()
        assert mine["companies"] == [] and x.name not in str(mine)
        assert [c["name"] for c in theirs["companies"]] == [x.name]

    def test_is_read_only(self, captest, dev_db, fake_company):
        user = captest()
        x = fake_company("captest읽기", price=1000)
        hold(dev_db, key(user), [(x, 5)])
        tables = ["holding", "virtual_account", "trade", "etf_holding", "stock_price", "company", "data_refresh_log"]
        before = {t: dev_db.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in tables}
        dev_db.rollback()
        assert client.get("/api/portfolio/exposure", headers=user["headers"]).status_code == 200
        after = {t: dev_db.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in tables}
        dev_db.rollback()
        assert before == after

    def test_query_count_is_fixed_and_does_not_grow_with_holdings(self, captest, dev_db, fake_company):
        small, large = captest(), captest()
        etf1, etf2 = fake_company("TIGER captestA", "etf", price=1000), fake_company("TIGER captestB", "etf", price=1000)
        stocks = [fake_company(f"captest종목{i}", price=100 + i) for i in range(10)]
        put_constituents(dev_db, etf1, [(s.name, s.ticker, 5.0) for s in stocks])
        put_constituents(dev_db, etf2, [(s.name, s.ticker, 5.0) for s in stocks[:5]])
        hold(dev_db, key(small), [(etf1, 1)])
        hold(dev_db, key(large), [(etf1, 1), (etf2, 2)] + [(s, 3) for s in stocks])
        n_small, _ = count_queries(lambda: run(key(small)))
        n_large, body = count_queries(lambda: run(key(large)))
        assert n_small == n_large == MAX_QUERIES, (n_small, n_large)
        n_none, _ = count_queries(lambda: run("user:0"))
        assert n_none == 1  # 보유가 없으면 쿼리 1개
        assert body["summary"]["holding_count"] == 12

    def test_response_time_with_200_constituents(self, captest, dev_db, fake_company):
        user = captest()
        stocks = [fake_company(f"captest속도{i}", price=1000 + i) for i in range(200)]
        etfs = [fake_company(f"TIGER captest속도{i}", "etf", price=5000) for i in range(3)]
        for e in etfs:
            put_constituents(dev_db, e, [(s.name, s.ticker, 0.5) for s in stocks])
        hold(dev_db, key(user), [(s, 2) for s in stocks[:10]] + [(e, 3) for e in etfs])
        client.get("/api/portfolio/exposure", headers=user["headers"])  # 워밍업
        times = []
        for _ in range(5):
            t = time.perf_counter()
            r = client.get("/api/portfolio/exposure", headers=user["headers"])
            times.append(time.perf_counter() - t)
            assert r.status_code == 200
        assert max(times) < 1.0, times  # 느슨한 상한(CI 러너 여유). 로컬 실측은 API.md 참고
        assert r.json()["summary"]["company_count"] == 200


@pytest.mark.needs_collected_data
class TestAgainstCollectedData:
    def test_samsung_exposure_matches_an_independent_sql_recalculation(self, captest, dev_db, monkeypatch):
        user = captest()
        now = datetime.now(KST)

        def latest_close(ticker):
            return float(dev_db.execute(text(
                "SELECT p.close_price FROM stock_price p JOIN company c ON c.id = p.company_id WHERE c.ticker = :t "
                "AND NOT (:open AND p.price_date = :today) ORDER BY p.price_date DESC LIMIT 1"),
                {"t": ticker, "open": now.weekday() < 5 and now.hour * 60 + now.minute < 15 * 60 + 30, "today": now.date()}).scalar())

        cs, ct = latest_close("005930"), latest_close("102110")
        qs, qt = max(1, int(2_000_000 // cs)), max(1, int(3_000_000 // ct))
        assert dev_db.execute(text("SELECT count(*) FROM etf_holding h JOIN company c ON c.id = h.company_id WHERE c.ticker = '102110'")).scalar() > 100, \
            "TIGER 200 구성종목이 수집돼 있어야 한다(python -m scripts.collect_etf_holdings --tickers 102110)"

        def fake_price(db, company):
            c = {"005930": cs, "102110": ct}[company.ticker]
            return {"current_price": c, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t", "is_realtime": False, "source": "fallback"}

        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price)
        for ticker, q in (("005930", qs), ("102110", qt)):
            r = client.post("/api/portfolio/orders", headers=user["headers"], json={"ticker": ticker, "side": "buy", "quantity": q})
            assert r.status_code == 200, r.text

        body = client.get("/api/portfolio/exposure?limit=50", headers=user["headers"]).json()

        # ---- 독립 재계산(SQL만, app.exposure 미사용) ----
        params = {"qs": qs, "qt": qt, "cs": cs, "ct": ct}
        want = dev_db.execute(text("""
            WITH d AS (SELECT max(h.as_of_date) AS d FROM etf_holding h JOIN company e ON e.id = h.company_id WHERE e.ticker = '102110'),
                 w AS (SELECT h.holding_code AS code, sum(h.weight_pct) AS w FROM etf_holding h JOIN company e ON e.id = h.company_id, d
                       WHERE e.ticker = '102110' AND h.as_of_date = d.d AND h.weight_pct > 0 GROUP BY h.holding_code)
            SELECT :qs * :cs AS direct,
                   :qt * :ct * COALESCE((SELECT w FROM w WHERE code = '005930'), 0) / 100 AS via,
                   :qt * :ct * COALESCE((SELECT w FROM w WHERE code = '000660'), 0) / 100 AS hynix,
                   :qt * :ct * (100 - COALESCE((SELECT sum(w) FROM w), 0)) / 100 AS other,
                   :qs * :cs + :qt * :ct AS total,
                   (SELECT d FROM d) AS as_of
        """), params).one()
        dev_db.rollback()
        direct, via, hynix, other, total = (float(want[i]) for i in range(5))
        assert via > 0 and hynix > 0
        s = next(c for c in body["companies"] if c["code"] == "005930")
        assert s["direct_amount"] == pytest.approx(direct, abs=0.01) and s["via_etf_amount"] == pytest.approx(via, abs=0.01)
        assert s["total_amount"] == pytest.approx(direct + via, abs=0.01)
        assert s["effective_weight_pct"] == pytest.approx((direct + via) / total * 100, abs=0.005)
        assert s["direct_weight_pct"] == pytest.approx(direct / total * 100, abs=0.005) and s["linked"] is True
        h = next(c for c in body["companies"] if c["code"] == "000660")
        assert h["direct_amount"] == 0 and h["via_etf_amount"] == pytest.approx(hynix, abs=0.01)
        assert body["summary"]["total_eval_amount"] == pytest.approx(total, abs=0.01)
        assert body["summary"]["other_amount"] == pytest.approx(other, abs=0.01)
        assert body["decomposed_etfs"][0]["holdings_as_of"] == want[5].isoformat()
        assert "005930" in [o["code"] for o in body["overlaps"]]
        # 합계 불변식: 모든 기업(limit 밖 포함) + 기타 + 분해하지 않은 ETF = 전체 평가금액
        sm = body["summary"]
        assert abs(sm["companies_amount"] + sm["other_amount"] + sm["undecomposed_etf_amount"] - total) <= 0.02
        assert sm["company_count"] > 50 and len(body["companies"]) == 50  # limit=50은 목록만 자른다
