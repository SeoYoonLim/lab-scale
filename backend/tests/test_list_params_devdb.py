"""목록 API 4개의 정렬·페이징·검색을 실제 DB로 검증한다(7주차 과제). 사용자는 captest_ + 임의값으로 만들고 끝나면 전부 지운다.

- 페이지를 끝까지 걸어서 중복·누락 없이 전체 집합과 같은지(동률 정렬 값 포함)
- 정렬 방향, 같은 값끼리의 순서(id가 방향을 따른다), 기본 정렬이 예전과 같은지
- research q 검색: 특수문자(%, _, ', ", \\)가 리터럴인지, 대소문자 무시, 종목명/종목코드, 다른 사용자의 리포트가 안 나오는지
- companies: X-Total-Count, offset, 정렬

시세·뉴스 같은 수집 데이터는 필요 없다(company 시드만 있으면 된다). 직접 넣는 행의 시각은 고정값이라 실행 시점과 무관하다.
"""

import math
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.companies import search_companies
from app.main import app
from app.models import Company, PortfolioDiagnosis, ResearchReport, Trade, User, VirtualAccount, Watchlist

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration

BASE = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def captest_user(dev_db):
    """실제 /api/auth/signup으로 만든 captest_ 사용자 팩토리. 끝나면 계정과 딸린 행(리포트·진단·모의투자·관심종목)을 전부 지운다."""
    created = []

    def _make():
        username = f"captest_{secrets.token_hex(6)}"
        password = secrets.token_urlsafe(12)
        r = client.post("/api/auth/signup", json={"username": username, "password": password})
        assert r.status_code == 201, r.text
        body = r.json()
        user = {"id": body["user"]["id"], "headers": {"Authorization": f"Bearer {body['access_token']}"}}
        user["owner_key"] = f"user:{user['id']}"
        created.append(user)
        return user

    yield _make

    dev_db.rollback()
    for user in created:
        dev_db.query(ResearchReport).filter(ResearchReport.user_id == user["id"]).delete()
        dev_db.query(PortfolioDiagnosis).filter(PortfolioDiagnosis.user_id == user["id"]).delete()
        dev_db.query(Watchlist).filter(Watchlist.device_id == user["owner_key"]).delete()
        dev_db.query(VirtualAccount).filter(VirtualAccount.device_id == user["owner_key"]).delete()  # trade/holding은 CASCADE
        dev_db.query(User).filter(User.id == user["id"]).delete()
    dev_db.commit()


def _company(dev_db, ticker):
    company = dev_db.query(Company).filter(Company.ticker == ticker).one()
    return company


def _walk(user, url, limit, **params):
    """limit씩 끝까지 걸으면서 페이징 메타를 검증하고 모든 items를 모아 돌려준다."""
    items, offset, pages = [], 0, 0
    while True:
        r = client.get(url, params={**params, "limit": limit, "offset": offset}, headers=user["headers"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_pages"] == math.ceil(body["total"] / limit)
        assert body["has_next"] == (offset + limit < body["total"])
        items += body["items"]
        pages += 1
        assert len(items) <= body["total"], "total보다 많이 돌려줬다(중복)"
        if not body["has_next"]:
            break
        offset += limit
        assert pages < 100, "끝나지 않는다"
    assert len(items) == body["total"], "누락이 있다"
    return items


# ---------- research ----------

QUESTIONS = [
    # (질문, 요약, 종목코드, 분 단위 시각 오프셋) — 시각이 같은 행이 둘씩 있다(동률)
    ("삼성전자 실적 알려줘", "영업이익 요약", "005930", 0),
    ("수익률 100% 달성 방법", "퍼센트 기호가 있는 질문", None, 0),
    ("snake_case 같은 밑줄 질문", "밑줄 질문", None, 10),
    ("it's a quote question", "작은따옴표", None, 10),
    ('say "hi" please', "큰따옴표", None, 20),
    ("경로 a\\b 질문", "백슬래시", None, 20),
    ("카카오 전망", "SUMMARYWORD 포함", "035720", 30),
    ("종목 없는 일반 질문", "일반", None, 40),
]
OTHER_USER_TERM = "OTHERUSERONLYZZ"


@pytest.fixture
def reports(dev_db, captest_user):
    """me에게 8건(시각 동률 포함), other에게 1건을 넣는다. 돌려주는 rows는 [{id, created_at, company_name, question}]."""
    me, other = captest_user(), captest_user()
    rows = []
    for question, summary, ticker, minutes in QUESTIONS:
        company = _company(dev_db, ticker) if ticker else None
        report = ResearchReport(
            user_id=me["id"], question=question, summary=summary, content="본문",
            company_id=company.id if company else None, created_at=BASE + timedelta(minutes=minutes),
        )
        dev_db.add(report)
        dev_db.flush()
        rows.append({"id": report.id, "created_at": report.created_at, "company_name": company.name if company else None,
                     "question": question})
    dev_db.add(ResearchReport(user_id=other["id"], question=f"{OTHER_USER_TERM} 질문", summary="남의 리포트", content="x",
                              created_at=BASE))
    dev_db.commit()
    return {"me": me, "other": other, "rows": rows}


def _expected_report_order(rows, sort, order):
    reverse = order == "desc"
    key = (lambda r: (r["created_at"], r["id"])) if sort == "created_at" else (lambda r: r["id"])
    return sorted(rows, key=key, reverse=reverse)


class TestResearchList:
    def test_default_order_is_newest_first_like_before(self, reports):
        r = client.get("/api/research", headers=reports["me"]["headers"])
        ids = [i["report_id"] for i in r.json()["items"]]
        assert ids == [x["id"] for x in _expected_report_order(reports["rows"], "created_at", "desc")]
        assert r.json()["total"] == len(QUESTIONS)

    @pytest.mark.parametrize("limit", [1, 3, 8, 100])
    def test_walking_every_page_has_no_duplicates_or_gaps(self, reports, limit):
        items = _walk(reports["me"], "/api/research", limit)
        assert sorted(i["report_id"] for i in items) == sorted(x["id"] for x in reports["rows"])

    @pytest.mark.parametrize("sort", ["created_at", "id"])
    @pytest.mark.parametrize("order", ["asc", "desc"])
    def test_sort_and_direction_with_ties_across_pages(self, reports, sort, order):
        items = _walk(reports["me"], "/api/research", 3, sort=sort, order=order)
        assert [i["report_id"] for i in items] == [x["id"] for x in _expected_report_order(reports["rows"], sort, order)]

    def test_company_name_is_not_a_sort_field(self, reports):
        # DB collation에 따라 한글 순서가 달라져서 허용하지 않는다.
        assert client.get("/api/research", params={"sort": "company_name"}, headers=reports["me"]["headers"]).status_code == 422

    def test_offset_at_or_past_total_is_200_with_empty_items(self, reports):
        for offset in (len(QUESTIONS), len(QUESTIONS) + 1, 1000):
            r = client.get("/api/research", params={"offset": offset}, headers=reports["me"]["headers"])
            assert r.status_code == 200
            assert r.json() == {"total": len(QUESTIONS), "has_next": False, "total_pages": 1, "items": []}

    def test_other_users_reports_are_never_listed(self, reports):
        items = _walk(reports["me"], "/api/research", 4)
        assert all(OTHER_USER_TERM not in i["question"] for i in items)
        theirs = _walk(reports["other"], "/api/research", 4)
        assert [i["question"] for i in theirs] == [f"{OTHER_USER_TERM} 질문"]


class TestResearchSearch:
    def _search(self, user, q, **params):
        r = client.get("/api/research", params={"q": q, **params}, headers=user["headers"])
        assert r.status_code == 200, r.text
        return r.json()

    def _questions(self, user, q):
        return sorted(i["question"] for i in _walk(user, "/api/research", 3, q=q))

    def test_blank_q_is_ignored(self, reports):
        for q in ("", "   ", "\t"):
            assert self._search(reports["me"], q)["total"] == len(QUESTIONS)

    def test_substring_match_on_question_is_case_insensitive(self, reports):
        assert self._questions(reports["me"], "SNAKE_CASE") == ["snake_case 같은 밑줄 질문"]
        assert self._questions(reports["me"], "QUOTE QUESTION") == ["it's a quote question"]

    def test_matches_summary(self, reports):
        assert self._questions(reports["me"], "summaryword") == ["카카오 전망"]
        assert self._questions(reports["me"], "영업이익") == ["삼성전자 실적 알려줘"]

    def test_matches_company_name_and_ticker_even_when_the_question_lacks_it(self, reports, dev_db):
        # "카카오 전망" 질문 문구와 상관없이 company_id로 연결된 종목명/종목코드로도 찾는다.
        kakao = _company(dev_db, "035720")
        report = dev_db.query(ResearchReport).filter(ResearchReport.question == "카카오 전망").one()
        report.question, report.summary = "전망 알려줘", "요약"
        dev_db.commit()
        assert self._questions(reports["me"], kakao.name) == ["전망 알려줘"]
        assert self._questions(reports["me"], "035720") == ["전망 알려줘"]

    def test_search_term_is_trimmed(self, reports):
        assert self._questions(reports["me"], "  snake_case  ") == ["snake_case 같은 밑줄 질문"]

    def test_percent_is_a_literal_not_a_wildcard(self, reports):
        assert self._questions(reports["me"], "%") == ["수익률 100% 달성 방법"]
        assert self._questions(reports["me"], "100%") == ["수익률 100% 달성 방법"]
        assert self._search(reports["me"], "%")["total"] == 1  # 전부 매칭(8건)이 아니다

    def test_underscore_is_a_literal_not_a_single_char_wildcard(self, reports):
        assert self._questions(reports["me"], "_") == ["snake_case 같은 밑줄 질문"]
        assert self._questions(reports["me"], "snake_case") == ["snake_case 같은 밑줄 질문"]
        assert self._search(reports["me"], "snake case")["total"] == 0  # _가 공백과 매칭되지 않는다
        assert self._search(reports["me"], "snakeXcase")["total"] == 0

    def test_quotes_are_literals(self, reports):
        assert self._questions(reports["me"], "it's") == ["it's a quote question"]
        assert self._questions(reports["me"], '"hi"') == ['say "hi" please']
        assert self._search(reports["me"], "'")["total"] == 1
        assert self._search(reports["me"], '"')["total"] == 1

    def test_backslash_is_a_literal(self, reports):
        assert self._questions(reports["me"], "a\\b") == ["경로 a\\b 질문"]
        assert self._search(reports["me"], "\\")["total"] == 1

    @pytest.mark.parametrize("q", ["%' OR '1'='1", "'; DROP TABLE research_report; --", "\" OR 1=1 --", "%%", "__"])
    def test_injection_shaped_input_matches_nothing_and_breaks_nothing(self, reports, dev_db, q):
        assert self._search(reports["me"], q)["total"] == 0
        assert dev_db.query(ResearchReport).filter(ResearchReport.user_id == reports["me"]["id"]).count() == len(QUESTIONS)

    def test_total_and_has_next_follow_the_search(self, reports):
        body = self._search(reports["me"], "질문", limit=2)  # 질문 문구에 '질문'이 들어간 리포트
        expected = sum("질문" in q or "질문" in s for q, s, _, _ in QUESTIONS)
        assert body["total"] == expected and body["total_pages"] == math.ceil(expected / 2)
        assert body["has_next"] == (expected > 2)

    def test_search_results_can_be_sorted_and_walked(self, reports):
        items = _walk(reports["me"], "/api/research", 2, q="질문", sort="id", order="asc")
        ids = [i["report_id"] for i in items]
        assert ids == sorted(ids) and len(ids) == len(set(ids))

    def test_other_users_report_is_not_found_by_search(self, reports):
        assert self._search(reports["me"], OTHER_USER_TERM)["total"] == 0
        assert self._search(reports["other"], OTHER_USER_TERM)["total"] == 1
        assert self._search(reports["other"], "snake")["total"] == 0


# ---------- portfolio/trades ----------

TRADE_SPECS = [
    # (종목코드, side, 수량, 가격, 분 오프셋) — 시각 동률, 금액 동률(2*500 == 1*1000), 가격·수량 동률이 섞여 있다
    ("005930", "buy", 2, 500, 0),
    ("005930", "buy", 1, 1000, 0),
    ("000660", "buy", 3, 100, 5),
    ("000660", "sell", 3, 100, 5),
    ("035720", "buy", 10, 70, 5),
    ("035720", "buy", 1, 700, 9),
    ("005930", "sell", 1, 1000, 12),
]


@pytest.fixture
def trades(dev_db, captest_user):
    user = captest_user()
    assert client.get("/api/portfolio", headers=user["headers"]).status_code == 200  # 계좌 자동 생성
    other = captest_user()
    assert client.get("/api/portfolio", headers=other["headers"]).status_code == 200
    rows = []
    for ticker, side, qty, price, minutes in TRADE_SPECS:
        company = _company(dev_db, ticker)
        trade = Trade(device_id=user["owner_key"], company_id=company.id, side=side, quantity=qty, price=price,
                      executed_at=BASE + timedelta(minutes=minutes))
        dev_db.add(trade)
        dev_db.flush()
        rows.append({"id": trade.id, "executed_at": BASE + timedelta(minutes=minutes), "amount": qty * price, "price": price,
                     "quantity": qty, "ticker": ticker})
    dev_db.add(Trade(device_id=other["owner_key"], company_id=_company(dev_db, "005930").id, side="buy", quantity=1, price=1,
                     executed_at=BASE))
    dev_db.commit()
    return {"user": user, "rows": rows}


class TestTradesList:
    def test_default_order_is_newest_first_like_before(self, trades):
        body = client.get("/api/portfolio/trades", headers=trades["user"]["headers"]).json()
        expected = sorted(trades["rows"], key=lambda r: (r["executed_at"], r["id"]), reverse=True)
        assert [i["id"] for i in body["items"]] == [r["id"] for r in expected]
        assert (body["total"], body["has_next"], body["total_pages"]) == (len(TRADE_SPECS), False, 1)

    @pytest.mark.parametrize("limit", [1, 2, 7, 200])
    def test_walking_has_no_duplicates_or_gaps(self, trades, limit):
        items = _walk(trades["user"], "/api/portfolio/trades", limit)
        assert sorted(i["id"] for i in items) == sorted(r["id"] for r in trades["rows"])

    @pytest.mark.parametrize("sort", ["executed_at", "amount", "price", "quantity", "ticker"])
    @pytest.mark.parametrize("order", ["asc", "desc"])
    def test_sort_and_direction_with_ties_across_pages(self, trades, sort, order):
        items = _walk(trades["user"], "/api/portfolio/trades", 2, sort=sort, order=order)
        expected = sorted(trades["rows"], key=lambda r: (r[sort], r["id"]), reverse=order == "desc")
        assert [i["id"] for i in items] == [r["id"] for r in expected]
        if sort == "amount":
            assert [i["amount"] for i in items] == [float(r["amount"]) for r in expected]

    def test_sort_composes_with_the_ticker_filter(self, trades):
        items = _walk(trades["user"], "/api/portfolio/trades", 2, ticker="005930", sort="amount", order="desc")
        expected = sorted((r for r in trades["rows"] if r["ticker"] == "005930"), key=lambda r: (r["amount"], r["id"]), reverse=True)
        assert [i["id"] for i in items] == [r["id"] for r in expected]

    def test_offset_at_or_past_total_is_200_with_empty_items(self, trades):
        for offset in (len(TRADE_SPECS), 500):
            body = client.get("/api/portfolio/trades", params={"offset": offset}, headers=trades["user"]["headers"]).json()
            assert body["items"] == [] and body["total"] == len(TRADE_SPECS) and body["has_next"] is False

    def test_other_users_trades_are_not_listed(self, trades):
        assert _walk(trades["user"], "/api/portfolio/trades", 3)[0]["id"] in {r["id"] for r in trades["rows"]}
        assert len(_walk(trades["user"], "/api/portfolio/trades", 3)) == len(TRADE_SPECS)


# ---------- portfolio/diagnosis/history ----------

DIAGNOSIS_SPECS = [
    # (총자산, 보유 종목 수, 분 오프셋) — 시각 동률과 총자산 동률이 있다
    (1_000_000, 2, 0),
    (1_000_000, 3, 0),
    (2_000_000, 3, 10),
    (500_000, 1, 10),
    (2_000_000, 2, 20),
    (750_000, 1, 30),
]


@pytest.fixture
def diagnoses(dev_db, captest_user):
    user, other = captest_user(), captest_user()
    rows = []
    for total_asset, holdings, minutes in DIAGNOSIS_SPECS:
        row = PortfolioDiagnosis(
            user_id=user["id"], created_at=BASE + timedelta(minutes=minutes), source="rule_based", total_asset=total_asset,
            holdings_count=holdings, result={"flags": [], "summary": f"요약 {total_asset}"},
        )
        dev_db.add(row)
        dev_db.flush()
        rows.append({"id": row.id, "created_at": row.created_at, "total_asset": total_asset, "holdings_count": holdings})
    dev_db.add(PortfolioDiagnosis(user_id=other["id"], created_at=BASE, source="rule_based", total_asset=1, holdings_count=1,
                                  result={"flags": [], "summary": "남의 진단"}))
    dev_db.commit()
    return {"user": user, "rows": rows}


URL = "/api/portfolio/diagnosis/history"


class TestDiagnosisHistoryList:
    def test_default_order_is_newest_first_like_before(self, diagnoses):
        body = client.get(URL, headers=diagnoses["user"]["headers"]).json()
        expected = sorted(diagnoses["rows"], key=lambda r: (r["created_at"], r["id"]), reverse=True)
        assert [i["id"] for i in body["items"]] == [r["id"] for r in expected]
        assert (body["total"], body["limit"], body["offset"], body["has_next"], body["total_pages"]) == (6, 20, 0, False, 1)

    @pytest.mark.parametrize("limit", [1, 4, 100])
    def test_walking_has_no_duplicates_or_gaps(self, diagnoses, limit):
        items = _walk(diagnoses["user"], URL, limit)
        assert sorted(i["id"] for i in items) == sorted(r["id"] for r in diagnoses["rows"])

    @pytest.mark.parametrize("sort", ["created_at", "id", "total_asset", "holdings_count"])
    @pytest.mark.parametrize("order", ["asc", "desc"])
    def test_sort_and_direction_with_ties_across_pages(self, diagnoses, sort, order):
        items = _walk(diagnoses["user"], URL, 2, sort=sort, order=order)
        key = (lambda r: r["id"]) if sort == "id" else (lambda r: (r[sort], r["id"]))
        expected = sorted(diagnoses["rows"], key=key, reverse=order == "desc")
        assert [i["id"] for i in items] == [r["id"] for r in expected]

    def test_offset_past_total_is_200_with_empty_items(self, diagnoses):
        body = client.get(URL, params={"offset": 6}, headers=diagnoses["user"]["headers"]).json()
        assert body["items"] == [] and body["total"] == 6 and body["has_next"] is False

    def test_history_route_is_not_taken_as_an_id(self, diagnoses):
        assert client.get(URL, params={"sort": "total_asset"}, headers=diagnoses["user"]["headers"]).status_code == 200


# ---------- companies ----------

class TestCompaniesList:
    def _page(self, **params):
        r = client.get("/api/companies", params=params)
        assert r.status_code == 200, r.text
        return r

    def _all(self, **params):
        total = int(self._page(limit=1, **params).headers["x-total-count"])
        items, offset = [], 0
        while offset < total:
            r = self._page(limit=100, offset=offset, **params)
            assert int(r.headers["x-total-count"]) == total
            items += r.json()
            offset += 100
        assert len(items) == total
        return items

    def test_default_listing_is_unchanged(self, dev_db):
        """파라미터를 안 주면 예전과 같은 항목·같은 순서(id 순)다."""
        old = search_companies(dev_db, None, 300)
        r = self._page(limit=300)
        assert r.json() == old
        assert int(r.headers["x-total-count"]) >= len(old)

    def test_total_header_counts_all_matches_regardless_of_limit(self, dev_db):
        total = dev_db.query(Company).count()
        assert int(self._page(limit=5).headers["x-total-count"]) == total
        assert int(self._page(limit=5, offset=total + 10).headers["x-total-count"]) == total

    def test_walking_has_no_duplicates_or_gaps(self, dev_db):
        tickers = [c["ticker"] for c in self._all()]
        assert len(tickers) == len(set(tickers)) == dev_db.query(Company).count()

    @pytest.mark.parametrize("sort", ["id", "name", "ticker"])
    @pytest.mark.parametrize("order", ["asc", "desc"])
    def test_walking_with_sort_covers_the_same_set_in_order(self, dev_db, sort, order):
        items = self._all(sort=sort, order=order)
        assert {c["ticker"] for c in items} == {c.ticker for c in dev_db.query(Company).all()}
        if sort != "id":
            keys = [c[{"name": "name", "ticker": "ticker"}[sort]] for c in items]
            assert keys == sorted(keys, reverse=order == "desc")

    def test_sorted_pages_do_not_overlap(self):
        first = [c["ticker"] for c in self._page(sort="name", limit=50, offset=0).json()]
        second = [c["ticker"] for c in self._page(sort="name", limit=50, offset=50).json()]
        assert not set(first) & set(second)

    def test_offset_at_or_past_total_is_200_empty_array_with_the_total(self, dev_db):
        total = dev_db.query(Company).count()
        r = self._page(offset=total)
        assert r.json() == [] and r.headers["x-total-count"] == str(total)

    def test_q_and_asset_type_narrow_the_total(self, dev_db):
        r = self._page(q="삼성전자", limit=1)
        assert 1 <= int(r.headers["x-total-count"]) <= dev_db.query(Company).count()
        etf_total = dev_db.query(Company).filter(Company.asset_type == "etf").count()
        r = self._page(asset_type="etf", limit=5)
        assert int(r.headers["x-total-count"]) == etf_total and all(c["asset_type"] == "etf" for c in r.json())
        assert self._page(q="존재하지않는종목이름xyz").headers["x-total-count"] == "0"

    def test_cors_exposes_the_total_header(self):
        r = client.get("/api/companies", headers={"Origin": "http://localhost:5173"}, params={"limit": 1})
        assert "x-total-count" in r.headers["access-control-expose-headers"].lower()


# ---------- 큰 offset ----------

class TestHugeOffset:
    """offset이 DB의 bigint 범위를 넘으면 DB 오류(503)가 아니라 검증 오류(422)여야 하고, 최대값은 빈 목록(200)이다."""

    @pytest.mark.parametrize("path", ["/api/research", "/api/portfolio/trades", URL, "/api/companies"])
    def test_offset_beyond_bigint_is_422_and_the_max_is_an_empty_page(self, captest_user, path):
        headers = captest_user()["headers"]
        assert client.get(path, params={"offset": 10**20}, headers=headers).status_code == 422
        assert client.get(path, params={"offset": 2**63}, headers=headers).status_code == 422
        r = client.get(path, params={"offset": 2**63 - 1}, headers=headers)
        assert r.status_code == 200, r.text
        assert (r.json() if path == "/api/companies" else r.json()["items"]) == []
