"""기록 관리(2026-11-05)의 DB 없는 부분: 날짜 그룹·범위, 검색 낱말, 분석 유형, 진단 기록 필드·비교, 라우트 검증.
DB를 쓰는 검증은 tests/test_history_devdb.py."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app.api.history as history_api
import app.diagnosis_history as h
from app.history_dates import DATE_GROUPS, date_fields, date_group, resolve_timezone, utc_range
from app.history_terms import ANY_FLAG, MAX_TERMS, flag_codes, split_terms, term_variants
from app.main import app
from app.reports import ANALYSIS_TYPES, _title, analysis_types

client = TestClient(app, raise_server_exceptions=False)
SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)  # 12:00 KST


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class TestDates:
    @pytest.mark.parametrize("created, group", [
        (utc(2026, 11, 5, 2, 59), "today"),
        (utc(2026, 11, 4, 15, 0), "today"),  # 11-05 00:00 KST
        (utc(2026, 11, 4, 14, 59, 59), "yesterday"),  # 11-04 23:59:59 KST
        (utc(2026, 11, 3, 15, 0), "yesterday"),
        (utc(2026, 11, 3, 14, 59), "last_7_days"),  # 2일 전
        (utc(2026, 10, 30, 3, 0), "last_7_days"),  # 6일 전
        (utc(2026, 10, 29, 3, 0), "last_30_days"),  # 7일 전
        (utc(2026, 10, 7, 3, 0), "last_30_days"),  # 29일 전
        (utc(2026, 10, 6, 3, 0), "older"),  # 30일 전
        (utc(2026, 11, 6, 0, 0), "today"),  # 미래(시계 차이)는 today
    ])
    def test_group_boundaries_in_seoul(self, created, group):
        assert date_group(created, SEOUL, NOW) == group

    def test_groups_depend_on_timezone(self):
        created = utc(2026, 11, 4, 15, 0)
        assert date_group(created, SEOUL, NOW) == "today"
        assert date_group(created, ZoneInfo("UTC"), NOW) == "yesterday"
        assert date_fields(created, SEOUL, NOW) == {"local_date": "2026-11-05", "local_time": "00:00", "date_group": "today"}
        assert set(DATE_GROUPS) == {"today", "yesterday", "last_7_days", "last_30_days", "older"}

    def test_utc_range_covers_whole_local_days(self):
        start, end = utc_range(date(2026, 11, 1), date(2026, 11, 4), SEOUL)
        assert start == utc(2026, 10, 31, 15, 0) and end == utc(2026, 11, 4, 15, 0)
        assert utc_range(None, None, SEOUL) == (None, None)
        start, end = utc_range(date(2026, 3, 8), date(2026, 3, 8), ZoneInfo("America/New_York"))  # 서머타임 시작일(23시간)
        assert (end - start).total_seconds() == 23 * 3600

    def test_reversed_range_and_unknown_timezone(self):
        with pytest.raises(ValueError):
            utc_range(date(2026, 11, 5), date(2026, 11, 4), SEOUL)
        assert utc_range(date(2026, 11, 5), date(2026, 11, 5), SEOUL)[0] is not None  # 같은 날은 된다
        for bad in ("Mars/Base", "../../etc/passwd", "x" * 65):
            with pytest.raises(ValueError):
                resolve_timezone(bad)
        assert resolve_timezone(None).key == resolve_timezone("  ").key == "Asia/Seoul"
        assert resolve_timezone("UTC").key == "UTC"


class TestTerms:
    def test_split(self):
        assert split_terms(None) == split_terms("   ") == []
        assert split_terms(" ETF  중복 ") == ["ETF", "중복"]
        assert split_terms("etf ETF Etf") == ["etf"]
        assert len(split_terms("a b c d e f g")) == MAX_TERMS

    def test_alias_variants(self):
        assert term_variants("삼전") == ["삼전", "삼성전자"]
        assert term_variants("하닉") == ["하닉", "SK하이닉스"]
        assert term_variants("삼성전자") == ["삼성전자"]
        assert term_variants("배당") == ["배당"]

    def test_flag_keywords(self):
        assert flag_codes("집중도") == {"CONCENTRATION_HIGH", "MARKET_SKEW"}
        assert flag_codes("현금비중") == {"CASH_HIGH", "CASH_LOW"}
        assert flag_codes("위험") == {ANY_FLAG}
        assert flag_codes("Concentration") == {"CONCENTRATION_HIGH", "MARKET_SKEW"}
        assert flag_codes("삼성전자") == set()


class TestAnalysisTypes:
    def test_types_follow_fixed_order(self):
        rows = [("news_tool", None, None), ("stock_tool", "forecast_brief", None), ("market_tool", "forecast_brief", None)]
        assert analysis_types(rows) == ["outlook", "news", "price", "market"]
        assert analysis_types([("stock_tool", None, "etf")]) == ["etf", "price"]
        assert analysis_types([("stock_tool", "period_history", None)]) == ["price"]
        assert analysis_types([("event_reaction_tool", None, None)]) == ["event"]
        assert analysis_types([]) == ["general"]
        assert analysis_types([("unknown_tool", None, None)]) == ["general"]
        assert ANALYSIS_TYPES[-1] == "general"

    def test_title(self):
        assert _title("  SK하이닉스\n전망  알려줘 ") == "SK하이닉스 전망 알려줘"
        long = "가" * 100
        assert len(_title(long)) == 60 and _title(long).endswith("…")


def _record(id_, created, result):
    return {"id": id_, "created_at": created, "source": "rule_based", "total_asset": result["metrics"]["total_asset"],
            "holdings_count": len(result["holdings"]), "result": result}


def _result(cash_pct, top1, hhi, holdings, flags=(), risks=("규칙 기준으로 확인된 위험 항목은 없습니다.",)):
    return {
        "generated_at": "2026-11-05T00:30:00+00:00",
        "metrics": {"total_asset": 10_000_000.0, "cash_balance": 1_000_000.0, "cash_weight_pct": cash_pct,
                    "stock_eval_amount": 9_000_000.0, "holding_count": len(holdings), "top1_weight_pct": top1,
                    "top3_weight_pct": top1, "herfindahl_index": hhi, "total_profit_loss_pct": 0.0},
        "holdings": holdings, "flags": list(flags), "risks": list(risks), "summary": "s",
    }


def _h(ticker, name, w):
    return {"ticker": ticker, "company_name": name, "weight_pct": w, "return_20d_pct": None}


class TestDiagnosisRecord:
    def test_record_fields(self):
        res = _result(10.0, 50.0, 0.5, [_h("000660", "SK하이닉스", 30.0), _h("005930", "삼성전자", 50.0),
                                         _h("035720", "카카오", None), _h("247540", "에코프로비엠", 10.0)],
                      flags=[{"code": "CONCENTRATION_HIGH"}, {"code": "BIG_LOSS"}, {"code": "BIG_LOSS"}], risks=["첫 위험", "둘째"])
        f = h.record_fields(res, 4)
        assert [x["ticker"] for x in f["top_holdings"]] == ["005930", "000660", "247540"]  # 비중을 모르는 종목은 뒤
        assert f["title"] == "삼성전자 외 3종목 진단"
        assert f["risk_summary"] == "첫 위험" and f["flag_codes"] == ["CONCENTRATION_HIGH", "BIG_LOSS"]
        assert f["cash_weight_pct"] == 10.0 and f["generated_at"] == "2026-11-05T00:30:00+00:00"
        assert h.diagnosis_title([], 0) == "포트폴리오 진단"

    def test_metric_changes_and_basis(self):
        prev = _record(1, utc(2026, 11, 3, 2), _result(9.09, 90.91, 1.0, [_h("005930", "삼성전자", 90.91)]))
        cur = _record(2, utc(2026, 11, 5, 0, 30), _result(33.33, 22.22, 0.3334, [_h("005930", "삼성전자", 22.22)]))
        cur["result"]["metrics"]["total_profit_loss_pct"] = None
        out = h.build_record_comparison(cur, prev)
        m = out["metric_changes"]
        assert m["cash_weight_pct"] == {"previous": 9.09, "current": 33.33, "change": 24.24}
        assert m["top1_weight_pct"]["change"] == -68.69
        assert m["herfindahl_index"]["change"] == -0.6666  # HHI는 소수 4자리
        assert m["total_profit_loss_pct"]["change"] is None  # 한쪽이 null이면 null
        assert out["basis"]["recomputed"] is False and out["basis"]["note"]
        assert out["basis"]["previous_created_at"] == "2026-11-03T02:00:00+00:00"
        assert out["exposure_changes"] is None
        assert {k: out[k] for k in ("current", "previous", "changes")} == h.build_comparison(cur, prev)
        alone = h.build_record_comparison(cur, None)
        assert alone["metric_changes"] is None and alone["basis"]["note"] is None


class TestSearchRoute:
    @pytest.fixture(autouse=True)
    def _logged_in(self, login_as):
        login_as(7)

    def test_passes_parsed_params_to_search(self, monkeypatch):
        seen = {}

        def fake(user_id, **kw):
            seen.update(user_id=user_id, **kw)
            return {"counts": {"research": 0, "diagnosis": 0}, "total": 0, "items": []}

        monkeypatch.setattr(history_api.history_search, "search", fake)
        body = client.get("/api/history/search", params={"keyword": " SK하이닉스  전망 ", "date_from": "2026-11-01",
                                                          "date_to": "2026-11-04", "page": 3, "size": 10,
                                                          "sort": "created_at,asc", "type": "diagnosis"}).json()
        assert seen["user_id"] == 7 and seen["kind"] == "diagnosis" and seen["terms"] == ["SK하이닉스", "전망"]
        assert seen["start"] == utc(2026, 10, 31, 15) and seen["end"] == utc(2026, 11, 4, 15)
        assert (seen["limit"], seen["offset"], seen["order"]) == (10, 20, "asc")
        assert body["counts"] == {"all": 0, "research": 0, "diagnosis": 0} and body["items"] == []
        assert body["total_pages"] == 0 and body["has_next"] is False and body["timezone"] == "Asia/Seoul"

    @pytest.mark.parametrize("params", [
        {"type": "news"}, {"page": 0}, {"page": 10001}, {"size": 0}, {"size": 101}, {"sort": "id,desc"},
        {"keyword": "가" * 51}, {"timezone": "Mars/Base"}, {"date_from": "2026-02-30"},
        {"date_from": "2026-11-05", "date_to": "2026-11-04"},
    ])
    def test_bad_params_are_422(self, monkeypatch, params):
        monkeypatch.setattr(history_api.history_search, "search", lambda *a, **k: pytest.fail("검색하면 안 됨"))
        assert client.get("/api/history/search", params=params).status_code == 422


def test_search_requires_login(monkeypatch):
    monkeypatch.setattr(history_api.history_search, "search", lambda *a, **k: pytest.fail("검색하면 안 됨"))
    assert client.get("/api/history/search").status_code == 401
    assert client.get("/api/history/search", params={"size": 0}).status_code == 401  # 401이 422보다 먼저
