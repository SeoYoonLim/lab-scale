"""목록 API 4개(research, portfolio/trades, portfolio/diagnosis/history, companies)의 정렬·페이징·검색 파라미터.

DB·Ollama 없이 돈다(라우터는 조회 함수를 mock). 실제 DB 동작(중복·누락 없는 순회, 정렬 방향, 검색)은
tests/test_list_params_devdb.py가 검증한다. 7주차 과제(페이징, 정렬, 필터/검색, 예외 처리/유효성 검증).
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base

import app.api.companies as companies_api
import app.api.portfolio as portfolio_api
import app.api.research as research_api
import app.companies as companies_module
import app.config as config
import app.diagnosis_history as history
from app.main import app
from app.pagination import escape_like, order_by_clauses, page_meta

client = TestClient(app, raise_server_exceptions=False)

USER_ID = 7


# ---------- 순수 함수 ----------

class TestPageMeta:
    @pytest.mark.parametrize(
        "total, limit, offset, has_next, total_pages",
        [
            (0, 20, 0, False, 0),  # 비어 있으면 페이지 0
            (5, 20, 0, False, 1),
            (40, 20, 0, True, 2),  # limit의 배수: 첫 페이지
            (40, 20, 20, False, 2),  # limit의 배수: 마지막 페이지
            (41, 20, 40, False, 3),  # 마지막 페이지에 1건만
            (41, 20, 20, True, 3),
            (3, 1, 0, True, 3),
            (3, 1, 2, False, 3),
            (40, 20, 40, False, 2),  # offset == total: 빈 페이지, 다음 없음
            (5, 2, 99, False, 3),  # offset > total
            (10, 4, 3, True, 3),  # offset이 limit의 배수가 아니어도 정확하다(3+4=7 < 10)
            (10, 4, 6, False, 3),  # 6+4=10, 남은 게 없다
        ],
    )
    def test_cases(self, total, limit, offset, has_next, total_pages):
        assert page_meta(total, limit, offset) == {"has_next": has_next, "total_pages": total_pages}


Base = declarative_base()


class Row(Base):
    __tablename__ = "row"
    id = Column(Integer, primary_key=True)
    at = Column(Integer)
    name = Column(String)


def _sql(clauses) -> str:
    from sqlalchemy import select

    return str(select(Row.id).order_by(*clauses).compile(dialect=postgresql.dialect())).split("ORDER BY ")[1]


class TestOrderByClauses:
    def test_desc_puts_id_desc_after_the_sort_column(self):
        # 기본 정렬(예전: created_at desc, id desc)과 같다.
        assert _sql(order_by_clauses(Row.at, Row.id, "desc")) == "row.at DESC, row.id DESC"

    def test_asc_puts_id_asc(self):
        assert _sql(order_by_clauses(Row.at, Row.id, "asc")) == "row.at ASC, row.id ASC"

    def test_sorting_by_id_has_no_duplicate_key(self):
        assert _sql(order_by_clauses(Row.id, Row.id, "desc")) == "row.id DESC"


class TestEscapeLike:
    @pytest.mark.parametrize(
        "term, expected",
        [("abc", "abc"), ("100%", "100\\%"), ("a_b", "a\\_b"), ("a\\b", "a\\\\b"), ("%_\\", "\\%\\_\\\\"), ("it's \"q\"", "it's \"q\"")],
    )
    def test_wildcards_and_backslash_are_escaped_quotes_are_not(self, term, expected):
        assert escape_like(term) == expected


# ---------- 라우터: 조회 함수를 mock ----------

@pytest.fixture
def logged_in(login_as):
    login_as(USER_ID)


@pytest.fixture
def backend(monkeypatch):
    """네 조회 함수를 가짜로 바꾼다. state["total"]이 전체 건수고, 받은 인자는 state["calls"][이름]에 쌓는다."""
    state = {"total": 0, "calls": {"research": [], "trades": [], "diagnosis": [], "companies": []}}

    def research(**kw):
        state["calls"]["research"].append(kw)
        return state["total"], []

    def trades(db, key, limit, offset, company_id=None, sort="executed_at", order="desc"):
        state["calls"]["trades"].append(dict(key=key, limit=limit, offset=offset, sort=sort, order=order))
        return {"total": state["total"], "limit": limit, "offset": offset, "items": []}

    def diagnosis(user_id, limit, offset, sort="created_at", order="desc"):
        state["calls"]["diagnosis"].append(dict(user_id=user_id, limit=limit, offset=offset, sort=sort, order=order))
        return state["total"], []

    def companies(db, q, limit, asset_type=None, offset=0, sort="id", order="asc"):
        state["calls"]["companies"].append(dict(q=q, limit=limit, asset_type=asset_type, offset=offset, sort=sort, order=order))
        return state["total"], []

    monkeypatch.setattr(research_api, "list_reports", research)
    monkeypatch.setattr(portfolio_api, "list_trades", trades)
    monkeypatch.setattr(history, "list_diagnoses", diagnosis)
    monkeypatch.setattr(companies_api, "search_companies_page", companies)
    monkeypatch.setattr(portfolio_api, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(companies_api, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    return state


# 이름: (url, 인증 필요, 기본 limit, 최대 limit, 허용 sort, 기본 sort, 기본 order)
API = {
    "research": ("/api/research", True, 20, 100, ["created_at", "id"], "created_at", "desc"),
    "trades": ("/api/portfolio/trades", True, 50, 200, ["executed_at", "amount", "price", "quantity", "ticker"], "executed_at", "desc"),
    "diagnosis": (
        "/api/portfolio/diagnosis/history", True, 20, 100, ["created_at", "id", "total_asset", "holdings_count"], "created_at", "desc",
    ),
    "companies": ("/api/companies", False, 50, 300, ["id", "name", "ticker"], "id", "asc"),
}
LIST_ENVELOPE = ["research", "trades", "diagnosis"]  # total/has_next/total_pages가 본문에 있는 API


def _url(name):
    return API[name][0]


@pytest.mark.usefixtures("logged_in")
class TestBoundaries:
    @pytest.mark.parametrize("name", API)
    def test_defaults_are_the_old_behavior(self, backend, name):
        _, _, default_limit, _, _, default_sort, default_order = API[name]
        assert client.get(_url(name)).status_code == 200
        (call,) = backend["calls"][name]
        assert (call["limit"], call["offset"], call["sort"], call["order"]) == (default_limit, 0, default_sort, default_order)

    @pytest.mark.parametrize("name", API)
    def test_limit_and_offset_edges_are_accepted(self, backend, name):
        max_limit = API[name][3]
        for query in ("limit=1", f"limit={max_limit}", "offset=0", "offset=1000000", "offset=9223372036854775807"):
            assert client.get(f"{_url(name)}?{query}").status_code == 200, query

    @pytest.mark.parametrize("name", API)
    def test_out_of_range_and_non_integer_are_422_and_never_reach_the_query(self, backend, name):
        max_limit = API[name][3]
        for query in ("limit=0", f"limit={max_limit + 1}", "limit=-1", "limit=abc", "limit=1.5", "limit=", "offset=-1", "offset=abc", "offset=1.5", "offset=9223372036854775808"):
            r = client.get(f"{_url(name)}?{query}")
            assert r.status_code == 422, query
            assert isinstance(r.json()["detail"], list)  # FastAPI 기본 형식 유지
        assert backend["calls"][name] == []

    @pytest.mark.parametrize("name", API)
    def test_every_whitelisted_sort_and_both_orders_pass_through(self, backend, name):
        for sort in API[name][4]:
            for order in ("asc", "desc"):
                assert client.get(f"{_url(name)}?sort={sort}&order={order}").status_code == 200
                assert (backend["calls"][name][-1]["sort"], backend["calls"][name][-1]["order"]) == (sort, order)

    @pytest.mark.parametrize("name", API)
    @pytest.mark.parametrize(
        "query",
        ["sort=bogus", "sort=", "sort=CREATED_AT", "sort=created_at;drop table users", "sort=password", "order=up", "order=ASC", "order=", "order=1"],
    )
    def test_unknown_sort_or_order_is_422(self, backend, name, query):
        r = client.get(f"{_url(name)}?{query}")
        assert r.status_code == 422
        assert backend["calls"][name] == []

    def test_each_api_rejects_the_other_apis_sort_fields(self, backend):
        assert client.get("/api/research?sort=amount").status_code == 422
        assert client.get("/api/portfolio/trades?sort=created_at").status_code == 422
        assert client.get("/api/portfolio/diagnosis/history?sort=executed_at").status_code == 422
        assert client.get("/api/companies?sort=created_at").status_code == 422

    def test_research_q_length_limit_is_50(self, backend):
        assert client.get("/api/research", params={"q": "가" * 50}).status_code == 200
        assert client.get("/api/research", params={"q": "가" * 51}).status_code == 422
        assert [c["q"] for c in backend["calls"]["research"]] == ["가" * 50]

    def test_research_blank_q_is_passed_for_the_query_layer_to_ignore(self, backend):
        assert client.get("/api/research", params={"q": "   "}).status_code == 200
        assert client.get("/api/research").status_code == 200
        assert [c["q"] for c in backend["calls"]["research"]] == ["   ", None]

    def test_research_q_special_characters_reach_the_query_unchanged(self, backend):
        for q in ("100%", "a_b", "it's", 'say "hi"', "a\\b", "%' OR 1=1 --"):
            assert client.get("/api/research", params={"q": q}).status_code == 200
        assert [c["q"] for c in backend["calls"]["research"]] == ["100%", "a_b", "it's", 'say "hi"', "a\\b", "%' OR 1=1 --"]


@pytest.mark.usefixtures("logged_in")
class TestPagingMeta:
    @pytest.mark.parametrize("name", LIST_ENVELOPE)
    @pytest.mark.parametrize(
        "total, query, has_next, total_pages",
        [
            (0, "", False, 0),
            (40, "limit=20", True, 2),
            (40, "limit=20&offset=20", False, 2),
            (41, "limit=20&offset=40", False, 3),
            (40, "limit=20&offset=40", False, 2),
            (5, "limit=2&offset=99", False, 3),
        ],
    )
    def test_has_next_and_total_pages(self, backend, name, total, query, has_next, total_pages):
        backend["total"] = total
        body = client.get(f"{_url(name)}?{query}").json()
        assert (body["total"], body["has_next"], body["total_pages"]) == (total, has_next, total_pages)
        assert body["items"] == []  # offset이 total 이상이어도 200과 빈 items

    @pytest.mark.parametrize("name", LIST_ENVELOPE)
    def test_old_fields_are_kept(self, backend, name):
        backend["total"] = 3
        body = client.get(f"{_url(name)}?limit=2&offset=1").json()
        assert {"total", "items"} <= set(body)
        if name != "research":  # research는 limit/offset을 본문에 싣지 않았다(예전 그대로)
            assert (body["limit"], body["offset"]) == (2, 1)
        else:
            assert set(body) == {"total", "has_next", "total_pages", "items"}


# ---------- 401이 검증보다 먼저 ----------

class TestAuthBeforeValidation:
    @pytest.mark.parametrize("name", LIST_ENVELOPE)
    @pytest.mark.parametrize("query", ["", "limit=0", "offset=-1", "sort=bogus", "order=up", "q=" + "a" * 51])
    def test_no_token_is_401_even_with_bad_params(self, backend, name, query):
        r = client.get(f"{_url(name)}?{query}")
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert backend["calls"][name] == []

    @pytest.mark.parametrize("name", LIST_ENVELOPE)
    def test_garbage_bearer_is_401(self, backend, name):
        assert client.get(_url(name) + "?limit=0", headers={"Authorization": "Bearer nope"}).status_code == 401

    def test_companies_is_public_so_bad_params_are_422(self, backend):
        assert client.get("/api/companies?limit=0").status_code == 422


# ---------- companies: 배열 유지 + X-Total-Count ----------

class TestCompaniesTotalHeader:
    def test_body_stays_an_array_and_total_is_in_the_header(self, backend):
        backend["total"] = 123
        r = client.get("/api/companies?limit=5&offset=10")
        assert r.json() == [] and r.headers["x-total-count"] == "123"
        assert backend["calls"]["companies"][-1]["offset"] == 10

    def test_header_is_the_total_not_the_page_size(self, monkeypatch, backend):
        item = {"ticker": "005930", "name": "삼성전자", "market": "KOSPI", "sector": None, "asset_type": "stock",
                "latest_close": 1.0, "change_pct": None}
        monkeypatch.setattr(companies_api, "search_companies_page", lambda *a, **k: (350, [item]))
        r = client.get("/api/companies?limit=1")
        assert r.json() == [item] and r.headers["x-total-count"] == "350"

    def test_offset_past_the_end_is_200_empty_array_with_total(self, backend):
        backend["total"] = 4
        r = client.get("/api/companies?offset=4")
        assert r.status_code == 200 and r.json() == [] and r.headers["x-total-count"] == "4"

    def test_q_asset_type_and_old_params_still_pass_through(self, backend):
        client.get("/api/companies", params={"q": "코덱스", "asset_type": "etf", "limit": 5})
        assert backend["calls"]["companies"][-1] == dict(q="코덱스", limit=5, asset_type="etf", offset=0, sort="id", order="asc")

    def test_cors_exposes_the_header_to_the_frontend_origin(self, backend):
        r = client.get("/api/companies", headers={"Origin": "http://localhost:5173"})
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        exposed = [h.strip().lower() for h in r.headers["access-control-expose-headers"].split(",")]
        assert "x-total-count" in exposed

    def test_other_origins_are_not_allowed(self, backend):
        # expose-headers 헤더는 붙어도 allow-origin이 없으면 브라우저가 응답을 읽지 못한다.
        r = client.get("/api/companies", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in r.headers


class TestCorsExposeHeadersConfig:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("CORS_EXPOSE_HEADERS", raising=False)
        assert config.cors_expose_headers() == ["X-Total-Count"]

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_falls_back_to_default(self, monkeypatch, raw):
        monkeypatch.setenv("CORS_EXPOSE_HEADERS", raw)
        assert config.cors_expose_headers() == ["X-Total-Count"]

    def test_override_is_comma_separated(self, monkeypatch):
        monkeypatch.setenv("CORS_EXPOSE_HEADERS", "X-Total-Count, X-Request-Id ,")
        assert config.cors_expose_headers() == ["X-Total-Count", "X-Request-Id"]


# ---------- search_companies_page: 정렬·offset·total (DB 없이) ----------

class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a):
        return self

    def order_by(self, *a):
        return self

    def all(self):
        return list(self.rows)


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _Query(self.rows)


def _company(id_, name, ticker):
    return SimpleNamespace(id=id_, name=name, ticker=ticker, market="KOSPI", sector=None, asset_type="stock")


COMPANIES = [
    _company(1, "나", "000030"),
    _company(2, "가", "000020"),
    _company(3, "다", "000010"),
    _company(4, "가", "000040"),  # 이름이 2번과 같다(동률)
]


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(companies_module, "_latest_prices", lambda db, ids: {})

    def _page(q=None, limit=50, offset=0, sort="id", order="asc"):
        total, items = companies_module.search_companies_page(_FakeDB(COMPANIES), q, limit, None, offset, sort, order)
        return total, [i["ticker"] for i in items]

    return _page


class TestSearchCompaniesPage:
    def test_default_is_id_asc_like_before(self, page):
        assert page() == (4, ["000030", "000020", "000010", "000040"])

    def test_name_ties_are_broken_by_id_in_the_same_direction(self, page):
        assert page(sort="name", order="asc")[1] == ["000020", "000040", "000030", "000010"]
        assert page(sort="name", order="desc")[1] == ["000010", "000030", "000040", "000020"]

    def test_ticker_sort(self, page):
        assert page(sort="ticker")[1] == ["000010", "000020", "000030", "000040"]
        assert page(sort="ticker", order="desc")[1] == ["000040", "000030", "000020", "000010"]

    def test_total_ignores_limit_and_offset(self, page):
        assert page(limit=1, offset=1)[0] == 4

    def test_offset_walks_the_sorted_list_without_overlap(self, page):
        seen = [t for off in range(0, 4, 3) for t in page(limit=3, offset=off, sort="ticker")[1]]
        assert seen == page(sort="ticker")[1]

    def test_offset_at_or_past_total_is_empty(self, page):
        assert page(offset=4) == (4, [])
        assert page(offset=999) == (4, [])

    def test_q_narrows_total(self, page):
        assert page(q="가") == (2, ["000020", "000040"])

    def test_search_companies_keeps_the_old_list_signature(self, monkeypatch):
        monkeypatch.setattr(companies_module, "_latest_prices", lambda db, ids: {})
        items = companies_module.search_companies(_FakeDB(COMPANIES), None, 2)
        assert [i["ticker"] for i in items] == ["000030", "000020"]
