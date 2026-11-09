"""진단 이력 라우트(GET /diagnosis/history, GET /diagnosis/{id}, GET /diagnosis/{id}/compare, DELETE /diagnosis/{id}).

DB는 소유자 조건을 실제처럼 적용하는 인메모리 저장소로 바꿔 끼운다. 실제 DB 동작(소유자 쿼리, 보관 50건 등)은
test_diagnosis_history_devdb.py. 저장되는 진단 본문은 실제 build_diagnosis 결과를 응답 모델로 검증한 것이다."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_api
import app.diagnosis as d
import app.diagnosis_history as history
from app.api.portfolio import DiagnosisBody
from app.disclaimer import DISCLAIMER
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

ME, OTHER = 7, 8
BASE_TIME = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)
BIGINT_OVER = 9223372036854775808


def samsung(price=270_000.0, ret=-2.0):
    return d.HoldingInput("005930", "삼성전자", "KOSPI", 10, 280_000.0, price, True, ret)


def kakao(price=45_000.0, ret=5.0):
    return d.HoldingInput("035720", "카카오", "KOSPI", 20, 45_000.0, price, True, ret)


def eco(price=150_000.0, ret=None):
    return d.HoldingInput("247540", "에코프로비엠", "KOSDAQ", 5, 150_000.0, price, True, ret)


class Store:
    """portfolio_diagnosis를 흉내 낸 인메모리 저장소. 모든 조회는 user_id가 맞는 행만 본다."""

    def __init__(self, monkeypatch):
        self.rows = []  # [{"user_id", "id", "created_at", "result", ...}]
        self.calls = []
        self.sorts = []  # list에 전달된 (sort, order)
        self._next_id = 1
        monkeypatch.setattr(d, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("no llm")))  # → 규칙 기반 폴백
        monkeypatch.setattr(history, "list_diagnoses", self.list)
        monkeypatch.setattr(history, "get_diagnosis", self.get)
        monkeypatch.setattr(history, "get_previous_diagnosis", self.previous)
        monkeypatch.setattr(history, "delete_diagnosis", self.delete)

    def add(self, user_id, cash, holdings, id_=None, minutes=None):
        body = DiagnosisBody(**d.build_diagnosis(cash, holdings)).model_dump(mode="json")
        id_ = id_ or self._next_id
        self._next_id = max(self._next_id, id_) + 1
        created = BASE_TIME + timedelta(minutes=id_ if minutes is None else minutes)
        row = {
            "user_id": user_id, "id": id_, "created_at": created, "source": body["source"],
            "total_asset": body["metrics"]["total_asset"], "holdings_count": body["metrics"]["holding_count"],
            "result": body,
        }
        self.rows.append(row)
        return row

    def _mine(self, user_id):
        return sorted((r for r in self.rows if r["user_id"] == user_id), key=lambda r: (r["created_at"], r["id"]),
                      reverse=True)

    def list(self, user_id, limit, offset, sort="created_at", order="desc"):
        self.calls.append(("list", user_id, limit, offset))
        self.sorts.append((sort, order))
        mine = self._mine(user_id)
        return len(mine), [history.history_item(r) for r in mine[offset: offset + limit]]

    def get(self, user_id, diagnosis_id):
        self.calls.append(("get", user_id, diagnosis_id))
        return next((r for r in self.rows if r["user_id"] == user_id and r["id"] == diagnosis_id), None)

    def previous(self, user_id, record):
        self.calls.append(("previous", user_id, record["id"]))
        older = [r for r in self._mine(user_id) if (r["created_at"], r["id"]) < (record["created_at"], record["id"])]
        return older[0] if older else None

    def delete(self, user_id, diagnosis_id):
        self.calls.append(("delete", user_id, diagnosis_id))
        row = self.get(user_id, diagnosis_id)
        if row is None:
            return False
        self.rows.remove(row)
        return True


@pytest.fixture(autouse=True)
def logged_in(login_as):
    login_as(ME)


@pytest.fixture
def store(monkeypatch):
    return Store(monkeypatch)


@pytest.fixture
def three(store):
    """내 진단 3건(id 1, 2, 3 — 오래된 순)과 남의 진단 1건(id 10)."""
    store.add(ME, 5_000_000.0, [samsung(price=300_000.0), kakao(price=100_000.0)], id_=1)
    store.add(ME, 4_000_000.0, [samsung(price=350_000.0), eco(price=500_000.0)], id_=2)
    store.add(ME, 6_000_000.0, [samsung(price=280_000.0, ret=4.5), eco(price=300_000.0, ret=9.0)], id_=3)
    store.add(OTHER, 1_000_000.0, [kakao()], id_=10)
    return store


ME_ONLY_URLS = [
    ("get", "/api/portfolio/diagnosis/history"),
    ("get", "/api/portfolio/diagnosis/1"),
    ("get", "/api/portfolio/diagnosis/1/compare"),
    ("delete", "/api/portfolio/diagnosis/1"),
]


class TestAuth:
    @pytest.mark.parametrize("method, url", ME_ONLY_URLS)
    def test_no_token_is_401(self, store, method, url):
        app.dependency_overrides.clear()
        r = getattr(client, method)(url)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert store.calls == []


class TestRouteOrder:
    def test_history_is_defined_before_the_id_route(self):
        paths = [r.path for r in portfolio_api.router.routes if "GET" in r.methods]
        assert paths.index("/api/portfolio/diagnosis/history") < paths.index("/api/portfolio/diagnosis/{diagnosis_id}")

    def test_history_is_not_swallowed_by_the_id_route(self, three):
        r = client.get("/api/portfolio/diagnosis/history")
        assert r.status_code == 200 and set(r.json()) == {"total", "limit", "offset", "has_next", "total_pages", "items"}
        assert ("get", ME, "history") not in three.calls

    def test_non_numeric_id_is_422_not_history(self, three):
        assert client.get("/api/portfolio/diagnosis/abc").status_code == 422
        assert client.get("/api/portfolio/diagnosis/abc/compare").status_code == 422

    def test_history_with_trailing_path_is_not_a_route(self, three):
        assert client.get("/api/portfolio/diagnosis/history/compare").status_code in (404, 405, 422)


class TestList:
    def test_newest_first_with_item_shape(self, three):
        r = client.get("/api/portfolio/diagnosis/history")
        body = r.json()
        assert body["total"] == 3 and body["limit"] == 20 and body["offset"] == 0
        assert [i["id"] for i in body["items"]] == [3, 2, 1]
        item = body["items"][0]
        assert set(item) == {"id", "created_at", "source", "total_asset", "holdings_count", "flags", "summary_preview"}
        assert item["source"] == "rule_based" and item["holdings_count"] == 2
        assert item["total_asset"] == three.rows[2]["total_asset"]
        assert len(item["summary_preview"]) <= 80 and item["summary_preview"] == three.rows[2]["result"]["summary"][:80]
        assert all(set(f) == {"code", "ticker", "company_name", "market"} for f in item["flags"])
        assert {f["code"] for f in body["items"][1]["flags"]} >= {"FEW_HOLDINGS"}

    def test_only_my_rows_and_my_user_id_is_used(self, three):
        ids = [i["id"] for i in client.get("/api/portfolio/diagnosis/history").json()["items"]]
        assert 10 not in ids
        assert three.calls[-1] == ("list", ME, 20, 0)  # users.id이지 owner key("user:7")가 아니다

    def test_pagination(self, three):
        body = client.get("/api/portfolio/diagnosis/history", params={"limit": 2, "offset": 1}).json()
        assert body["total"] == 3 and body["limit"] == 2 and body["offset"] == 1
        assert [i["id"] for i in body["items"]] == [2, 1]
        assert client.get("/api/portfolio/diagnosis/history", params={"offset": 5}).json()["items"] == []

    def test_empty_history(self, store):
        assert client.get("/api/portfolio/diagnosis/history").json() == {
            "total": 0, "limit": 20, "offset": 0, "has_next": False, "total_pages": 0, "items": [],
        }

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"limit": -1}, {"offset": -1}, {"limit": "x"}])
    def test_bad_paging_is_422(self, store, params):
        assert client.get("/api/portfolio/diagnosis/history", params=params).status_code == 422

    def test_limit_100_is_allowed(self, store):
        assert client.get("/api/portfolio/diagnosis/history", params={"limit": 100}).status_code == 200


class TestDetail:
    def test_returns_stored_result_plus_id_and_created_at(self, three):
        r = client.get("/api/portfolio/diagnosis/2")
        assert r.status_code == 200
        body = r.json()
        row = three.rows[1]
        assert body["id"] == 2 and body["created_at"] == row["created_at"].isoformat()
        assert {k: v for k, v in body.items() if k not in ("id", "created_at")} == row["result"]
        assert body["disclaimer"] == DISCLAIMER
        assert "diagnosis_id" not in body

    def test_own_lookup_uses_my_user_id(self, three):
        client.get("/api/portfolio/diagnosis/2")
        assert three.calls == [("get", ME, 2)]

    def test_other_users_and_missing_look_the_same(self, three):
        other = client.get("/api/portfolio/diagnosis/10")   # 남의 것(존재함)
        missing = client.get("/api/portfolio/diagnosis/999")  # 없는 것
        assert other.status_code == missing.status_code == 404
        assert other.json()["detail"].replace("10", "N") == missing.json()["detail"].replace("999", "N")
        assert set(other.json()) == set(missing.json()) == {"detail"}

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", str(BIGINT_OVER)])
    def test_bad_id_is_422(self, store, bad):
        assert client.get(f"/api/portfolio/diagnosis/{bad}").status_code == 422


class TestCompare:
    URL = "/api/portfolio/diagnosis/{}/compare"

    def test_default_previous_is_the_one_just_before(self, three):
        r = client.get(self.URL.format(3))
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"current", "previous", "changes", "disclaimer"}
        assert body["current"]["id"] == 3 and body["previous"]["id"] == 2
        assert body["disclaimer"] == DISCLAIMER
        assert body["changes"]["total_asset"] == {
            "previous": three.rows[1]["total_asset"], "current": three.rows[2]["total_asset"],
            "change_amount": three.rows[2]["total_asset"] - three.rows[1]["total_asset"],
            "change_pct": body["changes"]["total_asset"]["change_pct"],
        }
        assert set(body["changes"]) == {"total_asset", "holdings", "flags", "returns_20d"}

    def test_response_equals_the_pure_comparison(self, three):
        body = client.get(self.URL.format(3)).json()
        expected = history.build_comparison(three.rows[2], three.rows[1])
        assert body["current"] == expected["current"] and body["previous"] == expected["previous"]
        assert body["changes"]["holdings"] == expected["changes"]["holdings"]
        assert body["changes"]["returns_20d"] == expected["changes"]["returns_20d"]
        # 응답 모델은 플래그에 null 키를 채워 내보낸다(저장본과 같은 모양)
        assert [f["code"] for f in body["changes"]["flags"]["added"]] == [f["code"] for f in expected["changes"]["flags"]["added"]]

    def test_base_overrides_the_previous(self, three):
        body = client.get(self.URL.format(3), params={"base": 1}).json()
        assert body["previous"]["id"] == 1
        rows = {h["ticker"]: h for h in body["changes"]["holdings"]}
        assert rows["247540"]["status"] == "added" and rows["035720"]["status"] == "removed"

    def test_first_diagnosis_has_null_previous_and_changes(self, three):
        r = client.get(self.URL.format(1))
        assert r.status_code == 200
        body = r.json()
        assert body["current"]["id"] == 1 and body["previous"] is None and body["changes"] is None

    def test_other_users_diagnosis_is_never_the_default_previous(self, store):
        store.add(OTHER, 1_000_000.0, [kakao()], id_=1)
        store.add(ME, 5_000_000.0, [samsung()], id_=2)
        assert client.get(self.URL.format(2)).json()["previous"] is None

    def test_current_missing_or_other_users_is_404(self, three):
        assert client.get(self.URL.format(999)).status_code == 404
        assert client.get(self.URL.format(10)).status_code == 404

    def test_base_other_users_or_missing_is_404_and_the_same(self, three):
        other = client.get(self.URL.format(3), params={"base": 10})
        missing = client.get(self.URL.format(3), params={"base": 999})
        assert other.status_code == missing.status_code == 404
        assert other.json()["detail"].replace("10", "N") == missing.json()["detail"].replace("999", "N")

    @pytest.mark.parametrize("base", ["0", "-1", "abc", str(BIGINT_OVER)])
    def test_bad_base_is_422(self, three, base):
        assert client.get(self.URL.format(3), params={"base": base}).status_code == 422

    def test_every_lookup_carries_my_user_id(self, three):
        client.get(self.URL.format(3), params={"base": 1})
        client.get(self.URL.format(3))
        assert {c[1] for c in three.calls} == {ME}

    def test_does_not_call_the_llm(self, three, monkeypatch):
        def boom(messages):
            raise AssertionError("LLM을 부르면 안 된다")

        monkeypatch.setattr(d, "_chat", boom)
        assert client.get(self.URL.format(3)).status_code == 200

    def test_compare_with_itself_is_all_zero(self, three):
        body = client.get(self.URL.format(3), params={"base": 3}).json()
        assert body["changes"]["total_asset"]["change_amount"] == 0
        assert {h["weight_change_pp"] for h in body["changes"]["holdings"]} == {0.0}
        assert body["changes"]["flags"] == {"added": [], "resolved": []}


class TestDelete:
    def test_204_and_gone(self, three):
        r = client.delete("/api/portfolio/diagnosis/2")
        assert r.status_code == 204 and r.content == b""
        assert client.get("/api/portfolio/diagnosis/2").status_code == 404
        assert [i["id"] for i in client.get("/api/portfolio/diagnosis/history").json()["items"]] == [3, 1]

    def test_other_users_and_missing_are_404_and_nothing_is_deleted(self, three):
        assert client.delete("/api/portfolio/diagnosis/10").status_code == 404
        assert client.delete("/api/portfolio/diagnosis/999").status_code == 404
        assert len(three.rows) == 4 and any(r["id"] == 10 for r in three.rows)

    def test_second_delete_is_404(self, three):
        assert client.delete("/api/portfolio/diagnosis/1").status_code == 204
        assert client.delete("/api/portfolio/diagnosis/1").status_code == 404

    def test_delete_uses_my_user_id(self, three):
        client.delete("/api/portfolio/diagnosis/1")
        assert three.calls == [("delete", ME, 1), ("get", ME, 1)]

    def test_deleted_one_is_skipped_by_default_previous(self, three):
        client.delete("/api/portfolio/diagnosis/2")
        assert client.get("/api/portfolio/diagnosis/3/compare").json()["previous"]["id"] == 1

    @pytest.mark.parametrize("bad", ["0", "abc", str(BIGINT_OVER)])
    def test_bad_id_is_422(self, store, bad):
        assert client.delete(f"/api/portfolio/diagnosis/{bad}").status_code == 422
