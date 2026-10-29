"""POST /api/backtest를 실제 dev DB로 검증한다. 테스트 계정은 captest_ + 임의값(signup 픽스처가 정리). 결과는 저장되지 않는다.

시세가 필요한 대조 테스트만 needs_collected_data(CI의 빈 DB에는 시세가 없다).
"""

import math
import secrets
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.backtest import DEFAULT_FEE_RATE
from app.briefing import KST, session_is_open
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration


@pytest.fixture
def user(signup):
    return signup(f"captest_{secrets.token_hex(6)}")


class TestBacktestDevDB:
    def test_requires_login(self):
        assert client.post("/api/backtest", json={}).status_code == 401

    def test_unknown_ticker_is_404_with_hint(self, user):
        r = client.post("/api/backtest", headers=user["headers"], json={
            "tickers": ["SK하이"], "strategy": "buy_and_hold", "start": "2025-01-02", "end": "2025-12-30"})
        assert r.status_code == 404 and "혹시 SK하이닉스" in r.json()["detail"]  # 엄격 모드: 유사 이름은 안내만

    def test_too_short_period_is_400_with_reason(self, user):
        r = client.post("/api/backtest", headers=user["headers"], json={
            "tickers": ["005930"], "strategy": "buy_and_hold", "start": "2025-06-02", "end": "2025-06-13"})
        assert r.status_code == 400 and "최소 30일" in r.json()["detail"]

    def test_nothing_is_saved(self, user, dev_db):
        before = dev_db.execute(text("SELECT (SELECT count(*) FROM research_report), (SELECT count(*) FROM trade), "
                                     "(SELECT count(*) FROM virtual_account)")).one()
        dev_db.rollback()
        client.post("/api/backtest", headers=user["headers"], json={
            "tickers": ["005930"], "strategy": "dca", "start": "2024-01-02", "end": "2025-12-30"})
        after = dev_db.execute(text("SELECT (SELECT count(*) FROM research_report), (SELECT count(*) FROM trade), "
                                    "(SELECT count(*) FROM virtual_account)")).one()
        dev_db.rollback()
        assert tuple(before) == tuple(after)

    @pytest.mark.needs_collected_data
    def test_buy_and_hold_matches_an_independent_recalculation(self, user, dev_db):
        tickers = ["005930", "069500"]  # 삼성전자, KODEX 200
        start, end = date(2024, 1, 2), date(2026, 10, 28)
        r = client.post("/api/backtest", headers=user["headers"], json={
            "tickers": tickers, "strategy": "buy_and_hold", "start": start.isoformat(), "end": end.isoformat(),
            "initial_cash": 10_000_000})
        assert r.status_code == 200, r.text
        body = r.json()

        rows = dev_db.execute(text(
            "SELECT c.ticker, p.price_date, p.close_price FROM stock_price p JOIN company c ON c.id = p.company_id "
            "WHERE c.ticker = ANY(:t) AND p.price_date BETWEEN :s AND :e ORDER BY p.price_date"),
            {"t": tickers, "s": start, "e": end}).all()
        dev_db.rollback()
        now = datetime.now(KST)
        closes = {t: {d: float(v) for tt, d, v in rows if tt == t and not (session_is_open(now) and d == now.date())}
                  for t in tickers}
        days = sorted(set(closes[tickers[0]]) & set(closes[tickers[1]]))
        assert len(days) > 400
        assert (body["period"]["start"], body["period"]["end"]) == (days[0].isoformat(), days[-1].isoformat())

        final = 0.0
        for t in tickers:
            cash = 10_000_000 / 2
            p1 = closes[t][days[1]]  # 첫날 신호 → 다음 거래일 종가 체결
            qty = math.floor(cash / (p1 * (1 + DEFAULT_FEE_RATE)))
            cash -= qty * p1 * (1 + DEFAULT_FEE_RATE)
            final += cash + qty * closes[t][days[-1]]
            got = next(x for x in body["trades"]["items"] if x["ticker"] == t)
            assert (got["date"], got["quantity"], got["price"]) == (days[1].isoformat(), qty, p1)
        expected = round((final / 10_000_000 - 1) * 100, 2)
        assert body["metrics"]["final_value"] == pytest.approx(final, abs=0.05)
        assert body["metrics"]["total_return_pct"] == pytest.approx(expected, abs=0.011)
        assert body["metrics"]["trade_count"] == 2

        idx = dev_db.execute(text(
            "SELECT price_date, close_price FROM market_index WHERE index_code = 'KS11' AND price_date BETWEEN :s AND :e "
            "ORDER BY price_date"), {"s": days[0], "e": days[-1]}).all()
        dev_db.rollback()
        b = body["benchmark"]
        assert b["start_date"] == idx[0][0].isoformat() and b["end_date"] == idx[-1][0].isoformat()
        assert b["return_pct"] == pytest.approx(round((float(idx[-1][1]) / float(idx[0][1]) - 1) * 100, 2), abs=0.011)
