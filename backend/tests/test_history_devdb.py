"""기록 관리(2026-11-05): 리서치 목록·진단 이력의 view=record·검색·날짜 범위와 통합 검색(GET /api/history/search)을
실제 dev DB와 실제 토큰으로 검증한다. 사용자 2명(pt_ 접두어)과 기록을 직접 넣고 끝나면 사용자를 지운다(리포트·tool 이력·진단은
users FK CASCADE로 함께 지워진다). 수집 데이터는 쓰지 않는다(종목 시드만 있으면 된다).

"지금"은 2026-11-05 12:00 KST(03:00 UTC)로 고정한다(app.history_dates.now).
"""

import secrets
from collections.abc import Mapping
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

import app.diagnosis as d
import app.history_dates as history_dates
from app.api.portfolio import DiagnosisBody
from app.auth import create_access_token, create_user
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import Company, PortfolioDiagnosis, ResearchReport, ToolCallLog, User

pytestmark = pytest.mark.integration

client = TestClient(app, raise_server_exceptions=False)
NOW = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)
SEARCH = "/api/history/search"
OLD_LIST_KEYS = {"report_id", "previous_report_id", "question", "summary", "company_name", "created_at", "used_tools"}
OLD_DETAIL_KEYS = OLD_LIST_KEYS | {"answer", "sources", "disclaimer"}
OLD_DIAG_KEYS = {"id", "created_at", "source", "total_asset", "holdings_count", "flags", "summary_preview"}


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _news(*titles):
    return [{"title": t, "company_name": "SK하이닉스", "url": f"https://example.com/{i}", "date": "2026-11-04"}
            for i, t in enumerate(titles)]


# (키, 질문, 답변, created_at, 종목코드(research_report.company_id), tool 이력[(tool, result)])
REPORTS = [
    ("r1", "SK하이닉스 전망 알려줘", "SK하이닉스 단기 방향은 상승 쪽입니다.", utc(2026, 11, 5, 1, 0), "000660", [
        ("stock_tool", {"company_name": "SK하이닉스", "ticker": "000660", "found": True, "called_via": "forecast_brief",
                        "indicators": {"base_date": "2026-11-04"}, "data": {"lag_business_days": 0}}),
        ("news_tool", {"company_name": "SK하이닉스", "found": True, "news": _news("HBM 수요 확대", "실적 발표 예정")}),
    ]),
    # 2026-11-04 23:59 KST: 서울 기준 어제, UTC 기준으로도 11-04
    ("r2", "하닉 오늘 왜 올랐어?", "관련 기사를 확인했습니다.", utc(2026, 11, 4, 14, 59), "000660", [
        ("news_tool", {"company_name": "SK하이닉스", "found": True, "news": _news("외국인 순매수")}),
    ]),
    # 2026-11-05 00:00 KST: 서울 기준 오늘, UTC 기준으로는 11-04
    ("r3", "삼성전자랑 카카오 비교해줘", "두 종목의 최근 등락률입니다.", utc(2026, 11, 4, 15, 0), None, [
        ("stock_tool", {"company_name": "삼성전자", "ticker": "005930", "found": True,
                        "period_start_date": "2026-10-06", "period_end_date": "2026-11-04"}),
        ("stock_tool", {"company_name": "카카오", "ticker": "035720", "found": True,
                        "period_start_date": "2026-10-06", "period_end_date": "2026-11-04"}),
    ]),
    ("r4", "KODEX 200 어떤 상품이야?", "코스피200 지수를 따라가는 ETF입니다.", utc(2026, 11, 1, 3, 0), "069500", [
        ("stock_tool", {"company_name": "KODEX 200", "ticker": "069500", "found": True, "asset_type": "etf",
                        "latest_date": "2026-10-31"}),
    ]),
    ("r5", "PER이 뭐야?", "주가수익비율은 주가를 주당순이익으로 나눈 값입니다.", utc(2026, 10, 20, 3, 0), None, []),
    ("r6", "예전 질문", "배당 기준일을 확인했습니다.", utc(2026, 9, 1, 3, 0), None, []),
]


def _holding(ticker, name, market, qty, avg, price):
    return d.HoldingInput(ticker, name, market, qty, avg, price, True, None)


# (키, created_at, 현금, 보유)
DIAGNOSES = [
    # 삼성전자 한 종목 90.9% → CONCENTRATION_HIGH, FEW_HOLDINGS, MARKET_SKEW
    ("d1", utc(2026, 11, 3, 2, 0), 1_000_000, [_holding("005930", "삼성전자", "KOSPI", 100, 100_000.0, 100_000.0)]),
    # 고르게 3종목, 현금 33% → 플래그 없음(위험 문장은 "위험 항목은 없습니다")
    ("d2", utc(2026, 11, 5, 0, 30), 3_000_000, [
        _holding("005930", "삼성전자", "KOSPI", 20, 100_000.0, 100_000.0),
        _holding("000660", "SK하이닉스", "KOSPI", 10, 200_000.0, 200_000.0),
        _holding("247540", "에코프로비엠", "KOSDAQ", 20, 100_000.0, 100_000.0),
    ]),
    # 카카오 손실 -33%, 현금 과다 → BIG_LOSS, CASH_HIGH, FEW_HOLDINGS, MARKET_SKEW
    ("d3", utc(2026, 10, 1, 3, 0), 9_000_000, [_holding("035720", "카카오", "KOSPI", 20, 45_000.0, 30_000.0)]),
]


def _diagnosis_body(cash, holdings):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(d, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("no llm")))  # 규칙 기반 문장
        return DiagnosisBody(**d.build_diagnosis(cash, holdings)).model_dump(mode="json")


class _Bearer(Mapping):
    """요청할 때마다 토큰을 새로 만드는 헤더. JWT_SECRET이 테스트마다 바뀌어서(conftest.jwt_secret) 모듈 픽스처에서 한 번 만든
    토큰은 다음 테스트에서 401이 된다."""

    def __init__(self, user_id):
        self.user_id = user_id

    def __getitem__(self, key):
        if key != "Authorization":
            raise KeyError(key)
        return f"Bearer {create_access_token(self.user_id)}"

    def __iter__(self):
        return iter(["Authorization"])

    def __len__(self):
        return 1


def _user(db):
    user = create_user(db, f"pt_{secrets.token_hex(6)}", secrets.token_urlsafe(12))
    return user, _Bearer(user.id)


def _snapshot(user_ids):
    """사용자들의 기록 원본 전부(리포트·tool 이력·진단). 조회 전후가 같아야 한다."""
    db = SessionLocal()
    try:
        ids = list(user_ids)
        q = lambda sql: [tuple(r) for r in db.execute(text(sql), {"ids": ids})]  # noqa: E731
        return {
            "research_report": q("SELECT id, company_id, previous_report_id, user_id, question, summary, content, created_at "
                                 "FROM research_report WHERE user_id = ANY(:ids) ORDER BY id"),
            "tool_call_log": q("SELECT t.id, t.report_id, t.tool_name, t.arguments::text, t.result::text, t.called_at "
                               "FROM tool_call_log t JOIN research_report r ON r.id = t.report_id "
                               "WHERE r.user_id = ANY(:ids) ORDER BY t.id"),
            "portfolio_diagnosis": q("SELECT id, user_id, created_at, source, total_asset, holdings_count, result::text "
                                     "FROM portfolio_diagnosis WHERE user_id = ANY(:ids) ORDER BY id"),
        }
    finally:
        db.close()


@pytest.fixture(scope="module")
def world(dev_db):
    db = SessionLocal()
    users = []
    try:
        me, me_headers = _user(db)
        other, other_headers = _user(db)
        users = [me, other]
        company_ids = dict(db.execute(text("SELECT ticker, id FROM company")).all())
        missing = {t for *_, t, _ in REPORTS if t} - set(company_ids)
        assert not missing, f"종목 시드가 없음: {missing}"
        ids = {}
        for key, question, answer, created, ticker, tools in REPORTS:
            report = ResearchReport(user_id=me.id, question=question, summary=answer[:200], content=answer,
                                    created_at=created, company_id=company_ids.get(ticker))
            db.add(report)
            db.flush()
            for name, result in tools:
                db.add(ToolCallLog(report_id=report.id, tool_name=name, arguments={}, result=result, called_at=created))
            ids[key] = report.id
        other_report = ResearchReport(user_id=other.id, question="SK하이닉스 전망 알려줘", summary="남의 리포트",
                                      content="남의 리포트 SK하이닉스", created_at=utc(2026, 11, 5, 2, 0),
                                      company_id=company_ids["000660"])
        db.add(other_report)
        db.flush()
        ids["other_r"] = other_report.id
        for key, created, cash, holdings in DIAGNOSES:
            body = _diagnosis_body(cash, holdings)
            row = PortfolioDiagnosis(user_id=me.id, created_at=created, source=body["source"],
                                     total_asset=body["metrics"]["total_asset"],
                                     holdings_count=body["metrics"]["holding_count"], result=body)
            db.add(row)
            db.flush()
            ids[key] = row.id
        body = _diagnosis_body(1_000_000, [_holding("005930", "삼성전자", "KOSPI", 100, 100_000.0, 100_000.0)])
        other_diag = PortfolioDiagnosis(user_id=other.id, created_at=utc(2026, 11, 5, 2, 0), source=body["source"],
                                        total_asset=body["metrics"]["total_asset"], holdings_count=1, result=body)
        db.add(other_diag)
        db.commit()
        ids["other_d"] = other_diag.id
        before = _snapshot([me.id, other.id])
        yield {"me": me, "h": me_headers, "other": other, "oh": other_headers, "ids": ids, "before": before}
    finally:
        db.rollback()
        for user in users:
            db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(history_dates, "now", lambda: NOW)


def _get(world, path, headers=None, **params):
    r = client.get(path, headers=headers or world["h"], params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _key_of(world):
    return {v: k for k, v in world["ids"].items()}


def _research_keys(world, body):
    names = _key_of(world)
    return [names[i["report_id"]] for i in body["items"]]


def _search_keys(world, body):
    names = _key_of(world)
    return [names[i["id"]] for i in body["items"]]


class TestResearchList:
    def test_default_view_shape_is_unchanged(self, world):
        body = _get(world, "/api/research")
        assert set(body) == {"total", "has_next", "total_pages", "items"}
        assert body["total"] == 6
        assert all(set(i) == OLD_LIST_KEYS for i in body["items"])
        assert _research_keys(world, body) == ["r1", "r3", "r2", "r4", "r5", "r6"]

    def test_record_view_date_groups_and_local_dates(self, world):
        body = _get(world, "/api/research", view="record")
        assert body["timezone"] == "Asia/Seoul"
        got = {_key_of(world)[i["report_id"]]: (i["local_date"], i["local_time"], i["date_group"]) for i in body["items"]}
        assert got == {
            "r1": ("2026-11-05", "10:00", "today"),
            "r2": ("2026-11-04", "23:59", "yesterday"),
            "r3": ("2026-11-05", "00:00", "today"),
            "r4": ("2026-11-01", "12:00", "last_7_days"),
            "r5": ("2026-10-20", "12:00", "last_30_days"),
            "r6": ("2026-09-01", "12:00", "older"),
        }
        # created_at은 저장된 UTC 그대로
        r2 = next(i for i in body["items"] if i["report_id"] == world["ids"]["r2"])
        assert r2["created_at"] == "2026-11-04T14:59:00+00:00"

    def test_record_view_utc_timezone_moves_boundary_rows(self, world):
        body = _get(world, "/api/research", view="record", timezone="UTC")
        got = {_key_of(world)[i["report_id"]]: i["local_date"] for i in body["items"]}
        assert got["r2"] == "2026-11-04" and got["r3"] == "2026-11-04"

    def test_record_fields(self, world):
        items = {_key_of(world)[i["report_id"]]: i for i in _get(world, "/api/research", view="record")["items"]}
        assert items["r3"]["company_names"] == ["삼성전자", "카카오"] and items["r3"]["tickers"] == ["005930", "035720"]
        assert items["r3"]["company_name"] is None  # 기존 필드 규칙(종목이 1개일 때만)은 그대로
        assert items["r1"]["company_names"] == ["SK하이닉스"] and items["r1"]["tickers"] == ["000660"]
        assert items["r1"]["analysis_type"] == "outlook" and set(items["r1"]["analysis_types"]) == {"outlook", "news", "price"}
        assert items["r4"]["analysis_types"] == ["etf", "price"]
        assert items["r5"]["analysis_type"] == "general" and items["r5"]["used_tools"] == []
        assert items["r1"]["source_count"] == 2 and items["r2"]["source_count"] == 1 and items["r3"]["source_count"] == 0
        assert items["r1"]["title"] == "SK하이닉스 전망 알려줘"

    @pytest.mark.parametrize("params, expected", [
        ({"date_from": "2026-11-04", "date_to": "2026-11-04"}, ["r2"]),
        ({"date_from": "2026-11-01", "date_to": "2026-11-04"}, ["r2", "r4"]),
        ({"date_from": "2026-11-04", "date_to": "2026-11-04", "timezone": "UTC"}, ["r3", "r2"]),
        ({"date_from": "2026-11-05"}, ["r1", "r3"]),
        ({"date_to": "2026-10-31"}, ["r5", "r6"]),
    ])
    def test_date_range(self, world, params, expected):
        body = _get(world, "/api/research", **params)
        assert _research_keys(world, body) == expected and body["total"] == len(expected)

    @pytest.mark.parametrize("analysis_type, expected", [
        ("outlook", ["r1"]), ("etf", ["r4"]), ("news", ["r1", "r2"]), ("price", ["r1", "r3", "r4"]),
        ("general", ["r5", "r6"]), ("disclosure", []),
    ])
    def test_analysis_type_filter(self, world, analysis_type, expected):
        assert _research_keys(world, _get(world, "/api/research", analysis_type=analysis_type)) == expected

    @pytest.mark.parametrize("q, expected", [
        ("하이닉스", ["r1", "r2"]),  # 기존 q: 질문·요약·리포트 종목
        ("000660", ["r1", "r2"]),
        ("PER", ["r5"]),
    ])
    def test_existing_q_still_works_with_new_filters(self, world, q, expected):
        assert _research_keys(world, _get(world, "/api/research", q=q)) == expected
        assert _research_keys(world, _get(world, "/api/research", q=q, date_from="2026-11-05")) == [
            k for k in expected if k in ("r1", "r3")]

    def test_pagination_walks_everything_once(self, world):
        seen, offset = [], 0
        while True:
            body = _get(world, "/api/research", limit=2, offset=offset, view="record")
            seen += [i["report_id"] for i in body["items"]]
            assert body["total"] == 6 and body["total_pages"] == 3
            if not body["has_next"]:
                break
            offset += 2
        assert seen == [world["ids"][k] for k in ("r1", "r3", "r2", "r4", "r5", "r6")]
        asc = _get(world, "/api/research", order="asc", limit=100)
        assert [i["report_id"] for i in asc["items"]] == seen[::-1]

    def test_bad_params_are_422(self, world):
        for params in ({"timezone": "Mars/Base"}, {"date_from": "2026-11-05", "date_to": "2026-11-01"},
                       {"date_from": "2026-13-01"}, {"analysis_type": "bogus"}, {"view": "full"}):
            assert client.get("/api/research", headers=world["h"], params=params).status_code == 422, params


class TestResearchDetail:
    def test_default_detail_shape_is_unchanged(self, world):
        body = _get(world, f"/api/research/{world['ids']['r1']}")
        assert set(body) == OLD_DETAIL_KEYS

    def test_record_detail_keeps_original_answer_and_basis(self, world):
        body = _get(world, f"/api/research/{world['ids']['r1']}", view="record")
        assert body["answer"] == "SK하이닉스 단기 방향은 상승 쪽입니다."
        assert body["question"] == "SK하이닉스 전망 알려줘"
        assert body["source_count"] == len(body["sources"]) == 2
        assert [s["title"] for s in body["sources"]] == ["HBM 수요 확대", "실적 발표 예정"]  # 출처 제목 원문 그대로
        assert body["data_basis"][0]["base_date"] == "2026-11-04" and body["data_basis"][0]["ticker"] == "000660"
        r3 = _get(world, f"/api/research/{world['ids']['r3']}", view="record")
        assert [(b["company_name"], b["period_end_date"]) for b in r3["data_basis"]] == [("삼성전자", "2026-11-04"), ("카카오", "2026-11-04")]
        assert r3["local_date"] == "2026-11-05" and r3["date_group"] == "today"


class TestIsolation:
    def test_other_users_research_is_404_everywhere(self, world):
        other_id = world["ids"]["other_r"]
        for view in ("default", "record"):
            r = client.get(f"/api/research/{other_id}", headers=world["h"], params={"view": view})
            assert r.status_code == 404
        mine = _get(world, "/api/research", limit=100, view="record")
        assert other_id not in [i["report_id"] for i in mine["items"]]
        assert _get(world, "/api/research", headers=world["oh"])["total"] == 1

    def test_other_users_diagnosis_is_404_everywhere(self, world):
        other_id, mine = world["ids"]["other_d"], world["ids"]["d1"]
        assert client.get(f"/api/portfolio/diagnosis/{other_id}", headers=world["h"]).status_code == 404
        assert client.get(f"/api/portfolio/diagnosis/{mine}/compare", headers=world["h"],
                          params={"base": other_id, "view": "record"}).status_code == 404
        assert client.get(f"/api/portfolio/diagnosis/{other_id}/compare", headers=world["h"]).status_code == 404
        hist = _get(world, "/api/portfolio/diagnosis/history", view="record", q="삼성전자")
        assert other_id not in [i["id"] for i in hist["items"]]

    def test_search_never_returns_other_users_rows(self, world):
        body = _get(world, SEARCH, keyword="SK하이닉스", size=100)
        assert world["ids"]["other_r"] not in [i["id"] for i in body["items"] if i["kind"] == "research"]
        body = _get(world, SEARCH, keyword="삼성전자", size=100)
        assert world["ids"]["other_d"] not in [i["id"] for i in body["items"] if i["kind"] == "diagnosis"]
        theirs = _get(world, SEARCH, headers=world["oh"], size=100)
        assert theirs["counts"] == {"all": 2, "research": 1, "diagnosis": 1}
        assert {(i["kind"], i["id"]) for i in theirs["items"]} == {("research", world["ids"]["other_r"]),
                                                                  ("diagnosis", world["ids"]["other_d"])}

    def test_no_token_is_401_even_with_bad_params(self):
        for path in (SEARCH, "/api/research", "/api/portfolio/diagnosis/history"):
            assert client.get(path, params={"timezone": "Mars/Base", "date_from": "x"}).status_code == 401, path


class TestDiagnosisHistory:
    def test_default_view_shape_is_unchanged(self, world):
        body = _get(world, "/api/portfolio/diagnosis/history")
        assert set(body) == {"total", "limit", "offset", "has_next", "total_pages", "items"}
        assert all(set(i) == OLD_DIAG_KEYS for i in body["items"])
        assert [_key_of(world)[i["id"]] for i in body["items"]] == ["d2", "d1", "d3"]

    def test_record_view_fields_come_from_saved_result(self, world):
        body = _get(world, "/api/portfolio/diagnosis/history", view="record")
        items = {_key_of(world)[i["id"]]: i for i in body["items"]}
        db = SessionLocal()
        try:
            saved = {k: db.get(PortfolioDiagnosis, world["ids"][k]).result for k in ("d1", "d2", "d3")}
        finally:
            db.close()
        for k, item in items.items():
            assert item["cash_weight_pct"] == saved[k]["metrics"]["cash_weight_pct"]
            assert item["generated_at"] == saved[k]["generated_at"]
            assert item["risk_summary"] == saved[k]["risks"][0]
        assert items["d1"]["title"] == "삼성전자 진단" and items["d2"]["title"] == "삼성전자 외 2종목 진단"
        assert [h["company_name"] for h in items["d2"]["top_holdings"]] == ["삼성전자", "SK하이닉스", "에코프로비엠"]
        assert items["d1"]["flag_codes"] == ["CONCENTRATION_HIGH", "FEW_HOLDINGS", "MARKET_SKEW"]
        assert (items["d2"]["local_date"], items["d2"]["date_group"]) == ("2026-11-05", "today")
        assert (items["d1"]["local_date"], items["d1"]["date_group"]) == ("2026-11-03", "last_7_days")
        assert items["d3"]["date_group"] == "older"  # 2026-10-01, 35일 전

    @pytest.mark.parametrize("q, expected", [
        ("삼성전자", ["d2", "d1"]),
        ("삼전", ["d2", "d1"]),  # 별칭
        ("005930", ["d2", "d1"]),
        ("집중도", ["d1", "d3"]),  # 플래그 코드로(d1 한 종목 집중, d3 시장 편중)
        ("손실", ["d3"]),
        ("위험", ["d1", "d3"]),  # 플래그가 있는 진단만(d2의 "위험 항목은 없습니다"는 검색 대상이 아님)
        ("카카오 손실", ["d3"]),  # 낱말 AND
        ("삼성전자 손실", []),
    ])
    def test_q(self, world, q, expected):
        body = _get(world, "/api/portfolio/diagnosis/history", q=q)
        assert [_key_of(world)[i["id"]] for i in body["items"]] == expected and body["total"] == len(expected)

    def test_date_range_and_pagination(self, world):
        body = _get(world, "/api/portfolio/diagnosis/history", date_from="2026-11-01", date_to="2026-11-04")
        assert [_key_of(world)[i["id"]] for i in body["items"]] == ["d1"]
        page1 = _get(world, "/api/portfolio/diagnosis/history", limit=2)
        page2 = _get(world, "/api/portfolio/diagnosis/history", limit=2, offset=2)
        assert page1["has_next"] and not page2["has_next"] and page1["total_pages"] == 2
        assert [i["id"] for i in page1["items"] + page2["items"]] == [world["ids"][k] for k in ("d2", "d1", "d3")]
        asc = _get(world, "/api/portfolio/diagnosis/history", order="asc")
        assert [_key_of(world)[i["id"]] for i in asc["items"]] == ["d3", "d1", "d2"]

    def test_detail_is_saved_result_as_is(self, world):
        body = _get(world, f"/api/portfolio/diagnosis/{world['ids']['d1']}")
        db = SessionLocal()
        try:
            saved = db.get(PortfolioDiagnosis, world["ids"]["d1"]).result
        finally:
            db.close()
        assert {k: v for k, v in body.items() if k not in ("id", "created_at")} == saved

    def test_compare_default_is_unchanged_and_record_adds_basis(self, world):
        path = f"/api/portfolio/diagnosis/{world['ids']['d2']}/compare"
        default = _get(world, path)
        assert set(default) == {"current", "previous", "changes", "disclaimer"}
        assert set(default["changes"]) == {"total_asset", "holdings", "flags", "returns_20d"}
        record = _get(world, path, view="record")
        assert {k: record[k] for k in default} == default
        m = record["metric_changes"]
        db = SessionLocal()
        try:
            cur = db.get(PortfolioDiagnosis, world["ids"]["d2"]).result["metrics"]
            prev = db.get(PortfolioDiagnosis, world["ids"]["d1"]).result["metrics"]
        finally:
            db.close()
        assert m["cash_weight_pct"] == {"previous": prev["cash_weight_pct"], "current": cur["cash_weight_pct"],
                                        "change": round(cur["cash_weight_pct"] - prev["cash_weight_pct"], 2)}
        assert m["top1_weight_pct"]["change"] == round(cur["top1_weight_pct"] - prev["top1_weight_pct"], 2)
        assert m["herfindahl_index"]["change"] == round(cur["herfindahl_index"] - prev["herfindahl_index"], 4)
        basis = record["basis"]
        assert basis["recomputed"] is False and basis["note"]
        assert basis["previous_created_at"] == "2026-11-03T02:00:00+00:00"
        assert record["exposure_changes"] is None
        assert {f["code"] for f in record["changes"]["flags"]["resolved"]} == {"CONCENTRATION_HIGH", "FEW_HOLDINGS", "MARKET_SKEW"}


class TestSearch:
    @pytest.mark.parametrize("keyword, expected", [
        ("SK하이닉스", ["r1", "d2", "r2"]),
        ("하이닉스", ["r1", "d2", "r2"]),
        ("하닉", ["r1", "d2", "r2"]),  # 별칭 → SK하이닉스
        ("000660", ["r1", "d2", "r2"]),
        ("삼전", ["d2", "r3", "d1"]),  # r3는 tool 결과의 종목(리포트 종목은 NULL)
        ("카카오", ["r3", "d3"]),
        ("SK하이닉스 전망", ["r1"]),  # 낱말 AND
        ("ETF", ["r4"]),
        ("배당", ["r6"]),  # 답변 전문
        ("집중도", ["d1", "d3"]),
        ("위험", ["d1", "d3"]),
        ("삼성%전자", []),  # %는 와일드카드가 아니라 글자 그대로(진단 문장의 "33.33%"처럼 실제 % 글자만 찾는다)
    ])
    def test_keyword_all(self, world, keyword, expected):
        body = _get(world, SEARCH, keyword=keyword, size=100)
        assert _search_keys(world, body) == expected
        assert body["total"] == len(expected) == body["counts"]["all"]

    def test_type_tabs(self, world):
        everything = _get(world, SEARCH, size=100)
        assert everything["counts"] == {"all": 9, "research": 6, "diagnosis": 3}
        research = _get(world, SEARCH, type="research", size=100)
        diagnosis = _get(world, SEARCH, type="diagnosis", size=100)
        assert {i["kind"] for i in research["items"]} == {"research"} and research["total"] == 6
        assert {i["kind"] for i in diagnosis["items"]} == {"diagnosis"} and diagnosis["total"] == 3
        assert research["counts"] == diagnosis["counts"] == everything["counts"]
        assert _search_keys(world, everything) == ["r1", "d2", "r3", "r2", "d1", "r4", "r5", "d3", "r6"]

    def test_date_range(self, world):
        body = _get(world, SEARCH, date_from="2026-11-01", date_to="2026-11-04", size=100)
        assert _search_keys(world, body) == ["r2", "d1", "r4"]
        body = _get(world, SEARCH, keyword="SK하이닉스", date_from="2026-11-05", size=100)
        assert _search_keys(world, body) == ["r1", "d2"]

    def test_pagination_matches_full_list(self, world):
        full = [(i["kind"], i["id"]) for i in _get(world, SEARCH, size=100)["items"]]
        pages, page = [], 1
        while True:
            body = _get(world, SEARCH, size=2, page=page)
            assert body["total"] == 9 and body["total_pages"] == 5
            pages += [(i["kind"], i["id"]) for i in body["items"]]
            if not body["has_next"]:
                break
            page += 1
        assert pages == full and len(set(pages)) == 9
        asc = [(i["kind"], i["id"]) for i in _get(world, SEARCH, size=100, sort="created_at,asc")["items"]]
        assert asc == full[::-1]
        assert _get(world, SEARCH, size=2, page=99)["items"] == []

    def test_items_link_to_existing_detail_apis(self, world):
        body = _get(world, SEARCH, size=100)
        for item in body["items"]:
            detail = _get(world, item["detail_path"])
            assert (detail.get("report_id") or detail.get("id")) == item["id"]
            part = item[item["kind"]]
            assert part is not None and item["diagnosis" if item["kind"] == "research" else "research"] is None
        r1 = next(i for i in body["items"] if i["id"] == world["ids"]["r1"] and i["kind"] == "research")
        assert r1["detail_path"] == f"/api/research/{world['ids']['r1']}"
        assert r1["research"]["analysis_type"] == "outlook" and r1["research"]["source_count"] == 2
        d1 = next(i for i in body["items"] if i["id"] == world["ids"]["d1"] and i["kind"] == "diagnosis")
        assert d1["detail_path"] == f"/api/portfolio/diagnosis/{world['ids']['d1']}"
        assert d1["diagnosis"]["flag_codes"] == ["CONCENTRATION_HIGH", "FEW_HOLDINGS", "MARKET_SKEW"]
        assert d1["company_names"] == ["삼성전자"] and d1["tickers"] == ["005930"]

    def test_bad_params_are_422(self, world):
        for params in ({"type": "news"}, {"page": 0}, {"size": 101}, {"sort": "id,desc"}, {"keyword": "가" * 51},
                       {"timezone": "Nowhere/City"}, {"date_from": "2026-11-05", "date_to": "2026-11-04"}):
            assert client.get(SEARCH, headers=world["h"], params=params).status_code == 422, params

    def test_query_count_does_not_grow_with_page_size(self, world):
        counts = []
        for size in (2, 9):  # 두 페이지 모두 리서치·진단이 섞여 있다
            statements = []
            listener = lambda conn, cursor, statement, *a: statements.append(statement)  # noqa: E731
            event.listen(engine, "before_cursor_execute", listener)
            try:
                _get(world, SEARCH, size=size)
            finally:
                event.remove(engine, "before_cursor_execute", listener)
            counts.append(len([s for s in statements if not s.lstrip().upper().startswith(("SELECT 1", "SET", "SHOW"))]))
        # 인증 사용자 조회 1 + 건수 2 + 페이지 1 + 리서치 3 + 진단 1
        assert counts[0] == counts[1] <= 8, counts


def test_reads_never_modify_saved_records(world):
    """모든 조회·검색 뒤에도 기록 원본(리포트·tool 이력·진단)은 넣은 그대로다(이 모듈의 다른 테스트가 먼저 돈다)."""
    for path, params in ((SEARCH, {"size": 100}), ("/api/research", {"view": "record"}),
                         ("/api/portfolio/diagnosis/history", {"view": "record"})):
        _get(world, path, **params)
    assert _snapshot([world["me"].id, world["other"].id]) == world["before"]
