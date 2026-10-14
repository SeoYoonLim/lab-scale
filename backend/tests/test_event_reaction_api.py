"""GET /api/stocks/{ticker}/event-reactions: 라우터(mock)와 개발 DB(integration)."""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app.api.stocks as stocks_api
from app.event_reaction import DISCLAIMER
from app.event_reaction_service import _dedupe, _name_forms, _title_mentions
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
KST = ZoneInfo("Asia/Seoul")


def _h(pct=None, avail=False, reason="20거래일 경과 전"):
    return {"pct": pct, "index_pct": None, "excess_pct": None, "available": avail, "reason": reason}


EVENT = {
    "event_type": "news", "title": "t", "url": None, "event_at": "2026-10-07T10:00:00+09:00", "event_date": None,
    "time_known": True, "base_date": date(2026, 10, 6), "base_close": 100.0, "reaction_start_date": date(2026, 10, 7),
    "returns": {"d1": _h(1.0, True, None), "d5": _h(), "d20": _h()},
    "volume_ratio": 1.2, "volume_sample_days": 20, "other_events_in_window": 2, "note": None,
}
PAYLOAD = {
    "ticker": "005930", "company_name": "삼성전자", "asset_type": "stock", "price_as_of": date(2026, 10, 14),
    "market_index": "KS11", "events": [EVENT], "summary": {"event_count": 1, "avg_d1_pct": 1.0, "avg_d5_pct": None},
    "disclaimer": DISCLAIMER,
}


@pytest.fixture
def patched(monkeypatch):
    calls = []
    monkeypatch.setattr(stocks_api, "resolve_company", lambda db, t: SimpleNamespace(company=SimpleNamespace(), message=None))

    def fake(db, company, event_type, limit, since_days):
        calls.append((event_type, limit, since_days))
        return PAYLOAD

    monkeypatch.setattr(stocks_api, "build_event_reactions", fake)
    return calls


class TestRouter:
    def test_public_and_shape(self, patched):
        r = client.get("/api/stocks/005930/event-reactions")  # 토큰 없이
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {
            "ticker", "company_name", "asset_type", "price_as_of", "market_index", "events", "summary", "disclaimer"
        }
        e = body["events"][0]
        assert set(e["returns"]) == {"d1", "d5", "d20"}
        assert set(e["returns"]["d1"]) == {"pct", "index_pct", "excess_pct", "available", "reason"}
        assert e["base_date"] == "2026-10-06" and e["time_known"] is True
        assert body["disclaimer"] == DISCLAIMER

    def test_defaults(self, patched):
        client.get("/api/stocks/005930/event-reactions")
        assert patched == [("all", 10, 60)]

    def test_query_params_are_passed(self, patched):
        client.get("/api/stocks/005930/event-reactions?event_type=disclosure&limit=30&since_days=120")
        assert patched == [("disclosure", 30, 120)]

    @pytest.mark.parametrize(
        "qs", ["limit=0", "limit=31", "since_days=0", "since_days=121", "event_type=blog", "limit=abc"]
    )
    def test_invalid_query_is_422(self, patched, qs):
        assert client.get(f"/api/stocks/005930/event-reactions?{qs}").status_code == 422
        assert patched == []

    def test_unknown_company_is_404_with_detail(self, monkeypatch):
        monkeypatch.setattr(
            stocks_api, "resolve_company", lambda db, t: SimpleNamespace(company=None, message="'없는종목' 종목을 찾지 못했습니다.")
        )
        r = client.get("/api/stocks/없는종목/event-reactions")
        assert r.status_code == 404 and "찾지 못했습니다" in r.json()["detail"]

    def test_only_get(self):
        assert client.post("/api/stocks/005930/event-reactions").status_code == 405

    def test_disclaimer_is_separate_from_service_disclaimer(self):
        from app.disclaimer import DISCLAIMER as SERVICE

        assert DISCLAIMER != SERVICE and "인과" not in SERVICE
        assert "원인" in DISCLAIMER and "표본" in DISCLAIMER


class TestDedupe:
    def _e(self, kind, day, title):
        return {"event_type": kind, "day": day, "title": title}

    def test_same_day_same_title_is_removed_keeping_first(self):
        a = self._e("news", date(2026, 10, 7), "제목")
        b = self._e("news", date(2026, 10, 7), " 제목 ")
        assert _dedupe([a, b]) == [a]

    def test_different_day_or_type_is_kept(self):
        es = [
            self._e("news", date(2026, 10, 7), "제목"),
            self._e("news", date(2026, 10, 8), "제목"),
            self._e("disclosure", date(2026, 10, 7), "제목"),
        ]
        assert _dedupe(es) == es


@pytest.mark.integration
class TestDevDb:
    def test_samsung_real_response(self, dev_db):
        r = client.get("/api/stocks/005930/event-reactions?since_days=120&limit=30")
        assert r.status_code == 200
        b = r.json()
        assert b["ticker"] == "005930" and b["asset_type"] == "stock" and b["market_index"] == "KS11"
        assert b["price_as_of"] is not None and b["events"]
        kinds = {e["event_type"] for e in b["events"]}
        assert "disclosure" in kinds
        for e in b["events"]:
            assert (e["event_at"] is None) != (e["event_date"] is None)
            assert e["time_known"] == (e["event_at"] is not None)
            if e["event_type"] == "disclosure":
                assert e["time_known"] is False
            for k in ("d1", "d5", "d20"):
                h = e["returns"][k]
                assert h["available"] == (h["pct"] is not None)
                if not h["available"]:
                    assert h["reason"] and h["excess_pct"] is None
        s = b["summary"]
        assert s["event_count"] == len(b["events"])
        assert s["with_d20"] <= s["with_d5"] <= s["with_d1"] <= s["event_count"]
        # 최신 사건이 앞에 온다(유형별)
        for kind in ("disclosure", "news"):
            days = [(e["event_at"] or e["event_date"])[:10] for e in b["events"] if e["event_type"] == kind]
            assert days == sorted(days, reverse=True)

    def test_event_type_filter_and_limit(self, dev_db):
        d = client.get("/api/stocks/005930/event-reactions?event_type=disclosure&limit=3").json()
        assert 0 < len(d["events"]) <= 3 and {e["event_type"] for e in d["events"]} == {"disclosure"}
        n = client.get("/api/stocks/005930/event-reactions?event_type=news&limit=2").json()
        assert len(n["events"]) <= 2 and {e["event_type"] for e in n["events"]} <= {"news"}

    def test_no_duplicate_same_day_title(self, dev_db):
        evs = client.get("/api/stocks/005930/event-reactions?since_days=120&limit=30").json()["events"]
        keys = [(e["event_type"], (e["event_at"] or e["event_date"])[:10], e["title"].strip()) for e in evs]
        assert len(keys) == len(set(keys))

    def test_etf_has_no_disclosures_and_no_excess(self, dev_db):
        b = client.get("/api/stocks/069500/event-reactions?since_days=120&limit=30").json()
        assert b["asset_type"] == "etf" and b["market_index"] is None
        assert all(e["event_type"] == "news" for e in b["events"])
        for e in b["events"]:
            for k in ("d1", "d5", "d20"):
                assert e["returns"][k]["excess_pct"] is None and e["returns"][k]["index_pct"] is None
            if e["returns"]["d1"]["available"]:
                assert "ETF" in e["returns"]["d1"]["reason"]

    def test_alias_and_name_lookup(self, dev_db):
        assert client.get("/api/stocks/삼성전자/event-reactions?limit=1").json()["ticker"] == "005930"

    def test_unknown_ticker_404(self, dev_db):
        r = client.get("/api/stocks/999999/event-reactions")
        assert r.status_code == 404 and isinstance(r.json()["detail"], str)

    def test_query_count_is_small(self, dev_db):
        from sqlalchemy import event

        from app.db.session import SessionLocal, engine
        from app.event_reaction_service import build_event_reactions
        from app.models import Company

        company = dev_db.query(Company).filter(Company.ticker == "005930").one()
        n = []
        listener = lambda *a, **k: n.append(1)  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            build_event_reactions(dev_db, company, "all", 30, 120)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert len(n) <= 5  # 시세, 지수, 공시, 뉴스(+여유 1)

    def test_other_events_counts_include_other_type(self, dev_db):
        evs = client.get("/api/stocks/005930/event-reactions?event_type=disclosure&since_days=120&limit=30").json()["events"]
        # 뉴스가 있는 기간의 공시는 유형 필터와 무관하게 다른 사건(뉴스 포함) 수를 센다
        assert all(e["other_events_in_window"] >= 0 for e in evs)
        assert any(e["other_events_in_window"] > 0 for e in evs)


class TestNewsNameFilter:
    """뉴스 사건의 종목 관련도 필터(순수 로직): 제목에 종목 이름(또는 별칭)이 독립된 이름으로 들어 있어야 한다."""

    def _forms(self, name):
        return _name_forms(SimpleNamespace(name=name))

    def test_title_with_name(self):
        assert _title_mentions("삼성전자, 신제품 공개", ["삼성전자"], [])
        assert _title_mentions("[속보] 삼성전자가 신제품을 공개", ["삼성전자"], [])  # 조사가 붙어도 같은 이름
        assert _title_mentions("SKT·KT 주가 급락", ["KT"], [])

    def test_title_without_name(self):
        assert not _title_mentions("코스피, 6000선 바닥까지 '추락'", ["삼성전자"], [])
        assert not _title_mentions("용인 일자리박람회 첫 '반도체관'", ["삼성전자"], [])
        assert not _title_mentions("SK하이닉스 신고가", ["삼성전자"], [])  # 다른 종목 기사

    def test_other_company_name_that_contains_this_name_does_not_count(self):
        assert not _title_mentions("삼성전자우 급등", ["삼성전자"], ["삼성전자우"])
        assert not _title_mentions("SK하이닉스 신고가", ["SK"], ["SK하이닉스"])
        assert not _title_mentions("KT지니뮤직 적자", ["KT"], [])  # 뒤에 다른 이름이 이어짐
        assert _title_mentions("삼성전자우와 삼성전자 동반 상승", ["삼성전자"], ["삼성전자우"])

    def test_alias_counts(self):
        forms = self._forms("삼성전자")
        assert "삼전" in forms and forms[0] == "삼성전자"
        assert _title_mentions('"삼전 조정은 매수 기회"라는데', forms, [])
        assert not _title_mentions('"삼전 조정은 매수 기회"라는데', ["삼성전자"], [])

    def test_unrelated_company_has_only_its_own_name(self):
        assert self._forms("없는회사") == ["없는회사"]

    def test_title_mentioning_both_companies_counts_for_each(self):
        t = "삼성전자·SK하이닉스, 반도체 동반 상승"
        assert _title_mentions(t, ["삼성전자"], []) and _title_mentions(t, ["SK하이닉스"], [])

    def test_etf_name_variants(self):
        assert _title_mentions("코덱스200 신고가", ["KODEX 200"], [])
        assert not _title_mentions("KODEX 200TR 순매수", ["KODEX 200"], ["KODEX 200TR"])
