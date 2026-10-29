"""GET /api/portfolio/performance를 실제 dev DB로 검증한다: 읽기 전용, 소유자 격리, 쿼리 수 상한, 응답 숫자의 독립 재계산 대조.

테스트 계정은 captest_ + 임의값이고, signup 픽스처가 끝나면 계정·모의투자 계좌(체결은 FK CASCADE)를 지운다.
과거 체결은 captest 계좌에 실제 종가로 직접 넣고(주문 API는 지금 시각으로만 체결된다), 오늘 주문은 주문 API로 낸다.
독립 재계산은 app.performance를 쓰지 않고 SQL로 읽은 값으로 다시 계산한다. 시세가 필요한 테스트만 needs_collected_data.
"""

import calendar as calendar_mod
import math
import secrets
import statistics
from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

import app.portfolio as portfolio_module
from app.briefing import KST, session_is_open
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import Trade, VirtualAccount
from app.models.virtual_account import INITIAL_BALANCE
from app.performance_db import get_performance
from evals.metrics import forbidden_hits

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration

MAX_QUERIES = 5  # 계좌 1 + 체결 1 + 달력 1 + 종가 1 + 지수 1


@pytest.fixture
def captest(signup):
    def _make():
        return signup(f"captest_{secrets.token_hex(6)}")

    return _make


def key(user) -> str:
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


def run(owner_key, period="all"):
    db = SessionLocal()
    try:
        return get_performance(db, owner_key, period)
    finally:
        db.close()


def insert_trades(dev_db, owner_key, rows, cash):
    """rows: (company_id, side, qty, price, date). 체결 시각은 그날 10:00 KST."""
    dev_db.add(VirtualAccount(device_id=owner_key, cash_balance=cash))
    dev_db.flush()
    for cid, side, qty, price, d in rows:
        at = datetime.combine(d, time(10, 0), tzinfo=KST)
        dev_db.add(Trade(device_id=owner_key, company_id=cid, side=side, quantity=qty, price=price, executed_at=at))
    dev_db.commit()


class TestPerformanceDevDB:
    def test_requires_login(self):
        assert client.get("/api/portfolio/performance").status_code == 401

    def test_new_user_gets_message_and_no_account_is_created(self, captest, dev_db):
        user = captest()
        r = client.get("/api/portfolio/performance", headers=user["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["message"] is not None and body["trades"]["count"] == 0
        assert len(body["curve"]["points"]) <= 1
        dev_db.rollback()
        assert dev_db.execute(text("SELECT count(*) FROM virtual_account WHERE device_id = :k"), {"k": key(user)}).scalar() == 0

    def test_other_users_trades_never_appear(self, captest, dev_db):
        a, b = captest(), captest()
        cid = dev_db.execute(text("SELECT id FROM company ORDER BY id LIMIT 1")).scalar()
        day = datetime.now(KST).date()
        insert_trades(dev_db, key(b), [(cid, "buy", 1, 1000, day)], INITIAL_BALANCE - 1000)
        mine = client.get("/api/portfolio/performance", headers=a["headers"]).json()
        theirs = client.get("/api/portfolio/performance", headers=b["headers"]).json()
        assert mine["trades"]["count"] + mine["trades"]["pending_count"] == 0
        assert theirs["trades"]["count"] + theirs["trades"]["pending_count"] == 1

    def test_query_count_does_not_grow_with_the_number_of_instruments(self, captest, dev_db):
        small, large = captest(), captest()
        ids = [i for (i,) in dev_db.execute(text("SELECT id FROM company ORDER BY id LIMIT 11"))]
        assert len(ids) == 11
        day = datetime.now(KST).date()
        insert_trades(dev_db, key(small), [(ids[0], "buy", 1, 100, day)], INITIAL_BALANCE - 100)
        insert_trades(dev_db, key(large), [(cid, "buy", 1, 100, day) for cid in ids[1:]], INITIAL_BALANCE - 1000)
        n_small, _ = count_queries(lambda: run(key(small)))
        n_large, _ = count_queries(lambda: run(key(large)))
        assert n_small == n_large <= MAX_QUERIES, (n_small, n_large)

    @pytest.mark.needs_collected_data
    def test_numbers_match_an_independent_recalculation(self, captest, dev_db, monkeypatch):
        user = captest()
        owner = key(user)
        now = datetime.now(KST)

        cal = [d for (d,) in dev_db.execute(text("SELECT DISTINCT price_date FROM stock_price ORDER BY 1"))]
        if session_is_open(now):
            cal = [d for d in cal if d != now.date()]
        assert len(cal) >= 25
        d_a, d_b, d_c = cal[-20], cal[-10], cal[-5]
        # 세 날짜 모두 종가가 있는 주식 3종목
        x, y, z = dev_db.execute(text(
            "SELECT p.company_id FROM stock_price p JOIN company c ON c.id = p.company_id "
            "WHERE c.asset_type = 'stock' AND p.price_date IN (:a, :b, :c) GROUP BY p.company_id HAVING count(*) = 3 "
            "ORDER BY p.company_id LIMIT 3"), {"a": d_a, "b": d_b, "c": d_c}).scalars().all()

        def close(cid, d):
            return float(dev_db.execute(text(
                "SELECT close_price FROM stock_price WHERE company_id = :c AND price_date <= :d ORDER BY price_date DESC LIMIT 1"
            ), {"c": cid, "d": d}).scalar())

        # 수량은 종목당 약 200만원어치(최소 2주)라 가격대와 상관없이 잔고 안에서 체결된다.
        qx, qy, qz = (max(2, int(2_000_000 // close(cid, d_a))) for cid in (x, y, z))
        trades = [
            (x, "buy", qx, close(x, d_a), d_a), (y, "buy", qy, close(y, d_a), d_a),
            (x, "sell", qx // 2, close(x, d_b), d_b),
            (z, "buy", qz, close(z, d_c), d_c), (y, "sell", qy, close(y, d_c), d_c),
        ]
        cash = float(INITIAL_BALANCE)
        for _, side, q, p, _ in trades:
            cash = round(cash - p * q if side == "buy" else cash + p * q, 2)
        insert_trades(dev_db, owner, trades, cash)

        # 오늘 주문은 실제 주문 API로(체결가는 외부 시세 대신 DB 최근 종가로 고정). 종가가 아직 없으니 pending이어야 한다.
        def fake_price(db, company):
            return {"current_price": close(company.id, now.date()), "change_amount": 0.0, "change_pct": 0.0,
                    "as_of": "t", "is_realtime": False, "source": "fallback"}

        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price)
        ticker_x = dev_db.execute(text("SELECT ticker FROM company WHERE id = :i"), {"i": x}).scalar()
        dev_db.rollback()
        order = client.post("/api/portfolio/orders", headers=user["headers"], json={"ticker": ticker_x, "side": "buy", "quantity": 1})
        assert order.status_code == 200, order.text

        body = client.get("/api/portfolio/performance", headers=user["headers"]).json()
        data_date = cal[-1]
        assert body["data_date"] == data_date.isoformat()
        assert body["trades"]["pending_count"] == 1 and body["trades"]["count"] == 5
        assert not any("현재 현금 잔고" in n for n in body["notices"])  # 재생 현금 = 실제 잔고

        # ---- 독립 재계산 ----
        start = max(d for d in cal if d < d_a)
        days = [d for d in cal if start <= d <= data_date]
        held = {}
        avg = {}
        c = float(INITIAL_BALANCE)
        expected = []
        realized = []
        flows = {x: 0.0, y: 0.0, z: 0.0}
        for d in days:
            for cid, side, q, p, td in trades:
                if td != d:
                    continue
                if side == "buy":
                    avg[cid] = (avg.get(cid, 0) * held.get(cid, 0) + p * q) / (held.get(cid, 0) + q)
                    held[cid] = held.get(cid, 0) + q
                    c -= p * q
                    flows[cid] -= p * q
                else:
                    realized.append((p - avg[cid]) * q)
                    held[cid] -= q
                    c += p * q
                    flows[cid] += p * q
            expected.append((d.isoformat(), c + sum(q * close(cid, d) for cid, q in held.items() if q)))

        got = [(p["date"], p["total_asset"]) for p in body["curve"]["points"]]
        assert body["curve"]["sampled"] is False and len(got) == len(expected)
        for (gd, gv), (ed, ev) in zip(got, expected):
            assert gd == ed and gv == pytest.approx(ev, abs=0.05), gd

        values = [v for _, v in expected]
        end = values[-1]
        assert body["summary"]["end_value"] == pytest.approx(end, abs=0.05)
        assert body["summary"]["cumulative_return_pct"] == pytest.approx(round((end / INITIAL_BALANCE - 1) * 100, 2), abs=0.011)

        peak, worst = values[0], 0.0
        for v in values:
            peak = max(peak, v)
            worst = min(worst, (v / peak - 1) * 100)
        assert body["risk"]["max_drawdown"]["pct"] == pytest.approx(round(worst, 2), abs=0.011)

        rets = [(b / a - 1) * 100 for a, b in zip(values, values[1:])]
        assert body["risk"]["daily_volatility_pct"] == pytest.approx(statistics.stdev(rets), abs=1e-4)
        assert body["risk"]["annualized_volatility_pct"] == pytest.approx(statistics.stdev(rets) * math.sqrt(252), abs=1e-3)

        wins = sum(1 for r in realized if r > 0)
        assert body["trades"]["sells"] == 2 and body["trades"]["wins"] == wins
        assert body["trades"]["realized_profit_loss"] == pytest.approx(sum(realized), abs=0.05)

        pnl = {cid: flows[cid] + held.get(cid, 0) * close(cid, data_date) for cid in flows}
        tick = dict(dev_db.execute(text("SELECT id, ticker FROM company WHERE id IN (:x, :y, :z)"), {"x": x, "y": y, "z": z}).all())
        got_pnl = {cc["ticker"]: cc["profit_loss"] for cc in body["contributions"]}
        for cid, v in pnl.items():
            assert got_pnl[tick[cid]] == pytest.approx(v, abs=0.05)
        assert sum(got_pnl.values()) == pytest.approx(end - INITIAL_BALANCE, abs=0.05)
        assert [cc["profit_loss"] for cc in body["contributions"]] == sorted(got_pnl.values(), reverse=True)
        assert body["summary"]["cash_weight_pct"] == pytest.approx(round(c / end * 100, 2), abs=0.011)

        # 벤치마크: KS11의 겹치는 구간
        idx = dev_db.execute(text("SELECT price_date, close_price FROM market_index WHERE index_code = 'KS11' ORDER BY 1")).all()
        dev_db.rollback()
        idx = [(d, float(v)) for d, v in idx if not (session_is_open(now) and d == now.date())]
        lo, hi = max(start, idx[0][0]), min(data_date, idx[-1][0])
        i_lo = [v for d, v in idx if d <= lo][-1]
        i_hi = [v for d, v in idx if d <= hi][-1]
        p_lo = INITIAL_BALANCE if lo == start else [v for d, v in expected if d <= lo.isoformat()][-1]
        p_hi = [v for d, v in expected if d <= hi.isoformat()][-1]
        b = body["benchmark"]
        assert (b["start_date"], b["end_date"]) == (lo.isoformat(), hi.isoformat())
        assert b["index_return_pct"] == pytest.approx(round((i_hi / i_lo - 1) * 100, 2), abs=0.011)
        assert b["portfolio_return_pct"] == pytest.approx(round((p_hi / p_lo - 1) * 100, 2), abs=0.011)
        assert b["covers_full_period"] == (lo == start and hi == data_date)

        # 1m: 시작일과 기간 수익률
        one = client.get("/api/portfolio/performance?period=1m", headers=user["headers"]).json()
        y_, m_ = (data_date.year, data_date.month - 1) if data_date.month > 1 else (data_date.year - 1, 12)
        target = date(y_, m_, min(data_date.day, calendar_mod.monthrange(y_, m_)[1]))
        s1 = max(d for d in cal if d <= target)
        assert one["start_date"] == s1.isoformat()
        v_s1 = [v for d, v in expected if d == s1.isoformat()]
        base = v_s1[0] if v_s1 else float(INITIAL_BALANCE)  # 첫 체결 전 날이면 초기 자금
        assert one["summary"]["period_return_pct"] == pytest.approx(round((end / base - 1) * 100, 2), abs=0.011)
        for t in [one.get("message"), *one["notices"], *body["notices"]]:
            if t:
                assert forbidden_hits(t) == [], t

    def test_existing_accounts_are_only_read(self, dev_db):
        """기존 계정 데이터는 읽기만 한다(행 수가 그대로)."""
        before = dev_db.execute(text("SELECT (SELECT count(*) FROM trade), (SELECT count(*) FROM virtual_account)")).one()
        dev_db.rollback()
        owners = [k for (k,) in dev_db.execute(text("SELECT device_id FROM virtual_account WHERE device_id LIKE 'user:%' LIMIT 3"))]
        dev_db.rollback()
        for o in owners:
            assert run(o)["disclaimer"]
        after = dev_db.execute(text("SELECT (SELECT count(*) FROM trade), (SELECT count(*) FROM virtual_account)")).one()
        dev_db.rollback()
        assert tuple(before) == tuple(after)
