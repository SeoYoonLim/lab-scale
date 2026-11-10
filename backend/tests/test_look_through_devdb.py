"""ETF 실질 노출을 실제 DB로 검증한다: 인증, 사용자 분리, 고정 쿼리 수, 진단 저장본의 스냅샷 보존, 진단 비교의 exposure_changes,
기존 진단(스냅샷 없음)의 null, 언어별 숫자 일치.

보유·계좌·진단 저장은 실제 DB(임시 captest_ 계정, 끝나면 signup 픽스처가 정리)이고, ETF 구성종목 읽기(exposure_db.load_constituents)는
합성 값으로 바꾼다. 그래서 수집 데이터가 없는 CI DB에서도 같은 결과이고, 공용 etf_holding 데이터는 건드리지 않는다.
시세(실시간)와 LLM도 가짜다(외부 호출 없음).
"""

import json
import secrets
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

import app.diagnosis as d
import app.exposure_db as exposure_db
import app.portfolio as portfolio_module
from app.db.session import SessionLocal, engine
from app.exposure import ConstituentIn, EtfIn
from app.main import app
from app.models import Holding, PortfolioDiagnosis, VirtualAccount

client = TestClient(app, raise_server_exceptions=False)
pytestmark = pytest.mark.integration
PRICE = 10_000.0
LT = "/api/portfolio/exposure"


@pytest.fixture
def captest(signup):
    return lambda: signup(f"captest_{secrets.token_hex(5)}")


@pytest.fixture
def ids(dev_db):
    stocks = [r for r in dev_db.execute(text("SELECT id, ticker, name FROM company WHERE asset_type = 'stock' ORDER BY id LIMIT 3"))]
    etf = dev_db.execute(text("SELECT id, ticker, name FROM company WHERE asset_type = 'etf' AND name !~ '레버리지|인버스' ORDER BY id LIMIT 1")).first()
    assert len(stocks) == 3 and etf is not None
    return stocks, etf


@pytest.fixture(autouse=True)
def fakes(monkeypatch, ids):
    """구성종목: 보유 ETF는 첫 주식 40%, 둘째 주식 30%, 연결 안 된 해외 종목 20%(합계 90%). 시세·LLM은 가짜."""
    stocks, etf = ids
    state = {"weights": (40.0, 30.0, 20.0)}

    def constituents(db, etf_rows):
        w1, w2, w3 = state["weights"]
        return {t: EtfIn(t, n, date(2026, 11, 5), [
            ConstituentIn(stocks[0].name, stocks[0].ticker, w1, stocks[0].ticker, stocks[0].name, "stock"),
            ConstituentIn(stocks[1].name, stocks[1].ticker, w2, stocks[1].ticker, stocks[1].name, "stock"),
            ConstituentIn("Apple Inc", "AAPL US EQUITY", w3)]) for _, t, n in etf_rows if t == etf.ticker}

    monkeypatch.setattr(exposure_db, "load_constituents", constituents)

    def price(db, company):
        return {"current_price": PRICE, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t", "is_realtime": True, "source": "naver"}

    monkeypatch.setattr(portfolio_module, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("down")))  # 규칙 기반 설명
    return state


def seed(dev_db, user, rows, cash=1_000_000):
    key = f"user:{user['id']}"
    dev_db.add(VirtualAccount(device_id=key, cash_balance=cash))
    dev_db.flush()
    for cid, qty in rows:
        dev_db.add(Holding(device_id=key, company_id=cid, quantity=qty, avg_price=PRICE))
    dev_db.commit()


def lt(user, **params):
    return client.get(LT, params={"view": "look_through", **params}, headers=user["headers"])


class TestApi:
    def test_requires_login(self):
        assert client.get(LT, params={"view": "look_through"}).status_code == 401

    def test_bad_view_is_422(self, captest):
        assert client.get(LT, params={"view": "nope"}, headers=captest()["headers"]).status_code == 422

    def test_users_are_isolated_and_default_view_is_unchanged(self, captest, dev_db, ids):
        stocks, etf = ids
        a, b = captest(), captest()
        seed(dev_db, a, [(stocks[0].id, 10), (etf.id, 20)])
        mine, theirs = lt(a).json(), lt(b).json()
        assert theirs["status"] == "empty" and theirs["companies"] == [] and theirs["has_account"] is False
        assert stocks[0].ticker not in json.dumps(theirs)
        has_price = dev_db.execute(text("SELECT count(*) FROM stock_price WHERE company_id IN (:a, :b)"), {"a": stocks[0].id, "b": etf.id}).scalar() > 0
        # 시세가 있으면 ok, 없으면(CI의 빈 DB) no_prices: 0원으로 계산하지 않는다
        assert mine["status"] == ("ok" if has_price else "no_prices") and mine["calc_version"] == "look_through.v1"
        assert mine["has_account"] is True and mine["summary"]["holding_count"] == 2
        # 기본 응답(기존 계약)은 키가 그대로다
        default = client.get(LT, headers=a["headers"]).json()
        assert list(default) == ["generated_at", "valuation_date", "stale", "stale_notice", "etf_holdings_stale", "message", "limit", "summary",
                                 "companies", "overlaps", "decomposed_etfs", "undecomposed_etfs", "flags", "data_warnings", "disclaimer"]

    @pytest.mark.parametrize("with_prices", [False, True])
    def test_numbers_by_hand(self, captest, dev_db, ids, with_prices):
        """직접 10주 + ETF 20주, 현금 100만. 시세가 있으면 그 종가로, 없으면(CI의 빈 DB) 평가 불가로."""
        stocks, etf = ids
        user = captest()
        seed(dev_db, user, [(stocks[0].id, 10), (etf.id, 20)])
        body = lt(user).json()
        closes = {}
        for cid in (stocks[0].id, etf.id):
            row = dev_db.execute(text("SELECT close_price FROM stock_price WHERE company_id = :c ORDER BY price_date DESC LIMIT 1"), {"c": cid}).first()
            closes[cid] = float(row[0]) if row else None
        if closes[stocks[0].id] is None or closes[etf.id] is None:
            assert body["summary"]["unpriced_count"] >= 1  # 0원으로 치지 않는다
            return
        direct, etf_value = 10 * closes[stocks[0].id], 20 * closes[etf.id]
        s = next(c for c in body["companies"] if c["ticker"] == stocks[0].ticker)
        assert s["direct_amount"] == round(direct, 2) and s["via_etf_amount"] == round(etf_value * 0.4, 2)
        assert body["summary"]["total_asset"] == round(1_000_000 + direct + etf_value, 2)
        assert body["summary"]["residual_amount"] == round(etf_value * 0.1, 2)  # 합계 90%: 나머지는 미확인

    def test_query_count_is_constant(self, captest, dev_db, ids):
        stocks, etf = ids
        small, large = captest(), captest()
        seed(dev_db, small, [(stocks[0].id, 1)])
        seed(dev_db, large, [(s.id, 1) for s in stocks] + [(etf.id, 3)])

        def count(user):
            seen = []
            listener = lambda *a: seen.append(1)  # noqa: E731
            event.listen(engine, "before_cursor_execute", listener)
            try:
                db = SessionLocal()
                try:
                    exposure_db.get_look_through(db, f"user:{user['id']}", 10)
                finally:
                    db.close()
            finally:
                event.remove(engine, "before_cursor_execute", listener)
            return len(seen)

        assert count(large) <= 6 and count(small) <= 6


class TestDiagnosis:
    def _diag(self, user, **params):
        r = client.post("/api/portfolio/diagnosis", params=params, headers=user["headers"])
        assert r.status_code == 200, r.text
        return r.json()

    def test_snapshot_is_saved_and_not_recalculated(self, captest, dev_db, ids, fakes):
        stocks, etf = ids
        user = captest()
        seed(dev_db, user, [(stocks[0].id, 10), (etf.id, 20)])
        first = self._diag(user)
        snap = first["look_through"]
        assert snap["status"] == "ok" and snap["valuation_basis"] == "diagnosis_prices"
        s0 = next(c for c in snap["companies"] if c["ticker"] == stocks[0].ticker)
        # 직접 10 x 10,000 + ETF 20 x 10,000 x 40% = 180,000, 총자산 = 현금 100만 + 30만
        assert (s0["direct_amount"], s0["via_etf_amount"], s0["total_amount"]) == (100_000, 80_000, 180_000)
        assert snap["summary"]["total_asset"] == 1_300_000 and s0["total_weight_pct"] == round(180_000 / 1_300_000 * 100, 2)
        assert snap["summary"]["holding_top1_weight_pct"] == first["metrics"]["top1_weight_pct"]
        assert snap["etfs"][0]["constituents"][0]["weight_pct"] == 40.0
        # ETF 구성이 바뀐 뒤에도 과거 진단은 저장 당시 그대로다
        fakes["weights"] = (10.0, 10.0, 80.0)
        again = client.get(f"/api/portfolio/diagnosis/{first['diagnosis_id']}", headers=user["headers"]).json()
        assert again["look_through"] == snap
        second = self._diag(user)
        assert next(c for c in second["look_through"]["companies"] if c["ticker"] == stocks[0].ticker)["via_etf_amount"] == 20_000
        cmp = client.get(f"/api/portfolio/diagnosis/{second['diagnosis_id']}/compare", params={"view": "record"}, headers=user["headers"]).json()
        ch = cmp["exposure_changes"]
        row = next(r for r in ch["companies"] if r["code"] == stocks[0].ticker)
        assert row["previous_total_amount"] == 180_000 and row["current_total_amount"] == 120_000
        assert row["change_pct"] == round(row["current_total_weight_pct"] - row["previous_total_weight_pct"], 2)

    def test_old_diagnosis_without_snapshot_is_null(self, captest, dev_db, ids):
        stocks, _ = ids
        user = captest()
        seed(dev_db, user, [(stocks[0].id, 10)])
        new = self._diag(user)
        row = dev_db.query(PortfolioDiagnosis).filter(PortfolioDiagnosis.id == new["diagnosis_id"]).one()
        old_result = {k: v for k, v in row.result.items() if k != "look_through"}  # 이 기능 전 저장본 형태
        dev_db.add(PortfolioDiagnosis(user_id=user["id"], source=row.source, total_asset=row.total_asset, holdings_count=row.holdings_count,
                                      result=old_result, created_at=row.created_at - timedelta(days=1)))
        dev_db.commit()
        old_id = dev_db.execute(text("SELECT max(id) FROM portfolio_diagnosis WHERE user_id = :u"), {"u": user["id"]}).scalar()
        assert client.get(f"/api/portfolio/diagnosis/{old_id}", headers=user["headers"]).json()["look_through"] is None
        cmp = client.get(f"/api/portfolio/diagnosis/{new['diagnosis_id']}/compare", params={"view": "record"}, headers=user["headers"]).json()
        assert cmp["previous"]["id"] == old_id and cmp["exposure_changes"] is None

    def test_other_users_diagnosis_is_404(self, captest, dev_db, ids):
        stocks, _ = ids
        a, b = captest(), captest()
        seed(dev_db, a, [(stocks[0].id, 1)])
        mine = self._diag(a)
        assert client.get(f"/api/portfolio/diagnosis/{mine['diagnosis_id']}", headers=b["headers"]).status_code == 404
        assert client.get(f"/api/portfolio/diagnosis/{mine['diagnosis_id']}/compare", headers=b["headers"]).status_code == 404

    def test_locales_keep_numbers(self, captest, dev_db, ids):
        stocks, etf = ids
        user = captest()
        seed(dev_db, user, [(stocks[0].id, 10), (etf.id, 20)])
        snaps = {L: self._diag(user, locale=L)["look_through"] for L in ("ko-KR", "en-US", "ja-JP", "zh-CN")}
        ko = snaps["ko-KR"]
        for L, s in snaps.items():
            assert s["locale"] == L and s["summary"] == ko["summary"]
            assert [(c["code"], c["total_amount"], c["total_weight_pct"]) for c in s["companies"]] == \
                   [(c["code"], c["total_amount"], c["total_weight_pct"]) for c in ko["companies"]]
            assert [w["kind"] for w in s["data_warnings"]] == [w["kind"] for w in ko["data_warnings"]]
        assert snaps["ja-JP"]["data_warnings"][0]["message"] != ko["data_warnings"][0]["message"]
