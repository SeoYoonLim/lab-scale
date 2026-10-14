"""진단 이력 저장/조회/비교/삭제를 실제 dev DB + 실제 토큰으로 검증한다(가격과 LLM만 고정). 만든 사용자는 끝나면 지운다
(portfolio_diagnosis는 users FK CASCADE로 함께 지워진다)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.diagnosis as d
import app.diagnosis_history as history
import app.portfolio as portfolio_module
from app.db.session import SessionLocal
from app.main import app
from app.models import PortfolioDiagnosis, User

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration

PRICE = 1000.0
LLM_OK = {"summary": "현금 비중을 확인했습니다.", "suggestions": ["종목별 뉴스를 점검"]}
BASE = "/api/portfolio/diagnosis"
BODY_KEYS = {"generated_at", "source", "model", "metrics", "holdings", "flags", "summary", "strengths", "risks",
             "suggestions", "notes", "disclaimer"}


@pytest.fixture(autouse=True)
def fixed_price_and_llm(monkeypatch):
    def price(db, company):
        return {"current_price": PRICE, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t",
                "is_realtime": True, "source": "naver"}

    monkeypatch.setattr(portfolio_module, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "_chat", lambda m: json.dumps(LLM_OK, ensure_ascii=False))


def _buy(user, ticker, qty):
    r = client.post("/api/portfolio/orders", json={"ticker": ticker, "side": "buy", "quantity": qty},
                    headers=user["headers"])
    assert r.status_code == 200, r.text


def _diagnose(user):
    return client.post(BASE, headers=user["headers"])


def _get(user, path, **params):
    return client.get(f"{BASE}{path}", headers=user["headers"], params=params)


def _rows(user_id):
    db = SessionLocal()
    try:
        return db.query(PortfolioDiagnosis).filter(PortfolioDiagnosis.user_id == user_id).order_by(PortfolioDiagnosis.id).all()
    finally:
        db.close()


def _insert_rows(user_id, count, start=None, step_minutes=1):
    """가짜 진단 행을 직접 넣는다(오래된 것부터 created_at이 step_minutes씩 늘어남). id 목록을 돌려준다."""
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        rows = [
            PortfolioDiagnosis(
                user_id=user_id, created_at=start + timedelta(minutes=step_minutes * i), source="rule_based",
                total_asset=10_000_000 + i, holdings_count=1,
                result={"source": "rule_based", "summary": f"seed {i}", "flags": [], "metrics": {"total_asset": 1}},
            )
            for i in range(count)
        ]
        db.add_all(rows)
        db.commit()
        return [r.id for r in rows]
    finally:
        db.close()


class TestSave:
    def test_post_stores_the_whole_body_and_returns_id_and_created_at(self, signup):
        user = signup()
        _buy(user, "005930", 3)
        r = _diagnose(user)
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["diagnosis_id"], int) and body["created_at"]

        (row,) = _rows(user["id"])
        assert row.id == body["diagnosis_id"] and row.user_id == user["id"]
        assert row.created_at.isoformat() == body["created_at"]
        assert row.source == body["source"] == "llm"
        assert float(row.total_asset) == body["metrics"]["total_asset"]
        assert row.holdings_count == body["metrics"]["holding_count"] == 1
        # result는 응답 본문 그대로(diagnosis_id/created_at 제외)
        assert row.result == {k: v for k, v in body.items() if k in BODY_KEYS}
        assert set(row.result) == BODY_KEYS

    def test_existing_response_fields_are_unchanged(self, signup):
        user = signup()
        _buy(user, "005930", 3)
        body = _diagnose(user).json()
        assert set(body) == BODY_KEYS | {"diagnosis_id", "created_at"}

    def test_rule_based_result_is_saved_with_its_source(self, signup, monkeypatch):
        monkeypatch.setattr(d, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("down")))
        user = signup()
        _buy(user, "005930", 1)
        assert _diagnose(user).json()["source"] == "rule_based"
        assert [r.source for r in _rows(user["id"])] == ["rule_based"]

    def test_no_holdings_is_400_and_nothing_saved(self, signup):
        user = signup()
        assert _diagnose(user).status_code == 400
        assert _rows(user["id"]) == []

    def test_save_failure_still_returns_the_diagnosis(self, signup, monkeypatch):
        user = signup()
        _buy(user, "005930", 3)

        def broken():
            raise ConnectionError("history db down")

        monkeypatch.setattr(history, "SessionLocal", broken)  # 저장만 실패시킨다(진단 조회는 portfolio 쪽 세션)
        r = _diagnose(user)
        assert r.status_code == 200
        body = r.json()
        assert body["diagnosis_id"] is None and body["created_at"] is None
        assert body["source"] == "llm" and body["metrics"]["holding_count"] == 1
        assert _rows(user["id"]) == []

    def test_rejected_row_does_not_break_the_response(self, signup, monkeypatch):
        """DB가 행을 거절해도(여기서는 NOT NULL 위반) 진단은 200이고 diagnosis_id만 null이다."""
        user = signup()
        _buy(user, "005930", 3)
        real = history.PortfolioDiagnosis
        monkeypatch.setattr(history, "PortfolioDiagnosis", lambda **kw: real(**{**kw, "source": None}))
        r = _diagnose(user)
        assert r.status_code == 200 and r.json()["diagnosis_id"] is None
        monkeypatch.setattr(history, "PortfolioDiagnosis", real)
        assert _rows(user["id"]) == []


class TestRetention:
    def test_keeps_only_the_newest_50_and_drops_the_oldest(self, signup):
        user = signup()
        _buy(user, "005930", 1)
        seeded = _insert_rows(user["id"], 50)
        r = _diagnose(user)
        new_id = r.json()["diagnosis_id"]
        ids = [row.id for row in _rows(user["id"])]
        assert len(ids) == 50
        assert seeded[0] not in ids and new_id in ids  # 가장 오래된 것이 지워지고 방금 저장한 것이 남는다
        assert set(ids) == set(seeded[1:]) | {new_id}
        assert _get(user, "/history").json()["total"] == 50

    def test_trims_a_larger_backlog_down_to_50(self, signup):
        user = signup()
        _buy(user, "005930", 1)
        seeded = _insert_rows(user["id"], 58)
        new_id = _diagnose(user).json()["diagnosis_id"]
        ids = {row.id for row in _rows(user["id"])}
        assert len(ids) == 50 and ids == set(seeded[-49:]) | {new_id}

    def test_under_the_limit_nothing_is_deleted(self, signup):
        user = signup()
        _buy(user, "005930", 1)
        seeded = _insert_rows(user["id"], 49)
        new_id = _diagnose(user).json()["diagnosis_id"]
        assert {row.id for row in _rows(user["id"])} == set(seeded) | {new_id}

    def test_other_users_rows_are_not_pruned(self, signup):
        a, b = signup(), signup()
        _buy(a, "005930", 1)
        _insert_rows(a["id"], 50)
        others = _insert_rows(b["id"], 5)
        assert _diagnose(a).status_code == 200
        assert [row.id for row in _rows(b["id"])] == others

    def test_prune_failure_keeps_the_saved_row(self, signup, monkeypatch):
        user = signup()
        _buy(user, "005930", 1)

        def broken(user_id, keep=50):
            raise RuntimeError("prune failed")

        monkeypatch.setattr(history, "_prune", broken)
        body = _diagnose(user).json()
        assert body["diagnosis_id"] is not None
        assert [row.id for row in _rows(user["id"])] == [body["diagnosis_id"]]


class TestListAndDetail:
    def test_list_is_newest_first_with_item_fields(self, signup):
        user = signup()
        _buy(user, "005930", 3)
        first = _diagnose(user).json()
        _buy(user, "247540", 2)
        second = _diagnose(user).json()

        body = _get(user, "/history").json()
        assert body["total"] == 2 and body["limit"] == 20 and body["offset"] == 0
        assert [i["id"] for i in body["items"]] == [second["diagnosis_id"], first["diagnosis_id"]]
        item = body["items"][0]
        assert item["created_at"] == second["created_at"] and item["source"] == "llm"
        assert item["total_asset"] == second["metrics"]["total_asset"] and item["holdings_count"] == 2
        assert item["summary_preview"] == second["summary"][:80]
        assert [f["code"] for f in item["flags"]] == [f["code"] for f in second["flags"]]
        assert set(item) == {"id", "created_at", "source", "total_asset", "holdings_count", "flags", "summary_preview"}

        page = _get(user, "/history", limit=1, offset=1).json()
        assert page["total"] == 2 and [i["id"] for i in page["items"]] == [first["diagnosis_id"]]

    def test_long_summary_is_cut_to_80(self, signup, monkeypatch):
        monkeypatch.setattr(d, "_chat", lambda m: json.dumps({"summary": "현금 비중을 확인했습니다. " * 15, "suggestions": ["점검"]},
                                                              ensure_ascii=False))
        user = signup()
        _buy(user, "005930", 1)
        full = _diagnose(user).json()["summary"]
        assert len(full) > 80
        assert _get(user, "/history").json()["items"][0]["summary_preview"] == full[:80]

    def test_empty_history(self, signup):
        user = signup()
        assert _get(user, "/history").json() == {"total": 0, "limit": 20, "offset": 0, "items": []}

    def test_history_is_not_taken_as_an_id(self, signup):
        assert _get(signup(), "/history").status_code == 200

    def test_detail_returns_the_stored_result_plus_id_and_created_at(self, signup):
        user = signup()
        _buy(user, "005930", 3)
        posted = _diagnose(user).json()
        r = _get(user, f"/{posted['diagnosis_id']}")
        assert r.status_code == 200
        detail = r.json()
        assert detail["id"] == posted["diagnosis_id"] and detail["created_at"] == posted["created_at"]
        assert {k: v for k, v in detail.items() if k in BODY_KEYS} == {k: v for k, v in posted.items() if k in BODY_KEYS}
        assert set(detail) == BODY_KEYS | {"id", "created_at"}

    def test_detail_unaffected_by_later_portfolio_changes(self, signup):
        user = signup()
        _buy(user, "005930", 3)
        posted = _diagnose(user).json()
        _buy(user, "247540", 2)
        detail = _get(user, f"/{posted['diagnosis_id']}").json()
        assert detail["metrics"]["holding_count"] == 1 and len(detail["holdings"]) == 1


class TestOwnership:
    def test_other_users_diagnosis_is_404_everywhere_and_looks_like_missing(self, signup):
        a, b = signup(), signup()
        _buy(a, "005930", 3)
        _buy(b, "005930", 1)
        a_id = _diagnose(a).json()["diagnosis_id"]
        b_id = _diagnose(b).json()["diagnosis_id"]
        missing = max(a_id, b_id) + 100000

        # 남의 id와 없는 id는 같은 404
        for path in (f"/{a_id}", f"/{a_id}/compare"):
            other, none = _get(b, path), _get(b, path.replace(str(a_id), str(missing)))
            assert other.status_code == none.status_code == 404
            assert other.json()["detail"].replace(str(a_id), "N") == none.json()["detail"].replace(str(missing), "N")
        # 내 진단과 남의 진단을 base로 비교하려 해도 404
        assert _get(b, f"/{b_id}/compare", base=a_id).status_code == 404
        assert _get(a, f"/{a_id}/compare", base=b_id).status_code == 404
        # 남의 진단 삭제도 404이고 지워지지 않는다
        assert client.delete(f"{BASE}/{a_id}", headers=b["headers"]).status_code == 404
        assert [r.id for r in _rows(a["id"])] == [a_id]
        # 목록에는 내 것만
        assert [i["id"] for i in _get(b, "/history").json()["items"]] == [b_id]

    def test_other_users_diagnosis_is_never_the_default_previous(self, signup):
        a, b = signup(), signup()
        _buy(a, "005930", 1)
        _buy(b, "005930", 1)
        _diagnose(a)
        b_id = _diagnose(b).json()["diagnosis_id"]
        body = _get(b, f"/{b_id}/compare").json()
        assert body["previous"] is None and body["changes"] is None

    def test_no_token_is_401(self, signup):
        user = signup()
        _buy(user, "005930", 1)
        d_id = _diagnose(user).json()["diagnosis_id"]
        assert client.get(f"{BASE}/history").status_code == 401
        assert client.get(f"{BASE}/{d_id}").status_code == 401
        assert client.get(f"{BASE}/{d_id}/compare").status_code == 401
        assert client.delete(f"{BASE}/{d_id}").status_code == 401
        assert len(_rows(user["id"])) == 1


class TestCompare:
    def test_with_the_previous_diagnosis(self, signup):
        user = signup()
        _buy(user, "005930", 3)                       # KOSPI 한 종목 → MARKET_SKEW
        first = _diagnose(user).json()
        _buy(user, "247540", 2)                       # KOSDAQ 추가 → MARKET_SKEW 해소
        second = _diagnose(user).json()
        assert {f["code"] for f in first["flags"]} >= {"MARKET_SKEW"}
        assert "MARKET_SKEW" not in {f["code"] for f in second["flags"]}

        r = _get(user, f"/{second['diagnosis_id']}/compare")
        assert r.status_code == 200
        body = r.json()
        assert body["current"]["id"] == second["diagnosis_id"] and body["previous"]["id"] == first["diagnosis_id"]

        t = body["changes"]["total_asset"]  # 같은 가격으로 샀으니 총자산은 그대로
        assert t["previous"] == t["current"] == first["metrics"]["total_asset"]
        assert t["change_amount"] == 0 and t["change_pct"] == 0

        rows = {h["ticker"]: h for h in body["changes"]["holdings"]}
        assert rows["247540"]["status"] == "added" and rows["247540"]["previous_weight_pct"] is None
        assert rows["247540"]["current_weight_pct"] == rows["247540"]["weight_change_pp"] == 0.02
        assert rows["005930"]["status"] == "kept" and rows["005930"]["weight_change_pp"] == 0.0

        flags = body["changes"]["flags"]
        assert [f["code"] for f in flags["resolved"]] == ["MARKET_SKEW"] and flags["resolved"][0]["market"] == "KOSPI"
        assert flags["added"] == []

        # 20거래일 수익률은 양쪽 모두 있는 삼성전자만(에코프로비엠은 이전에 없음)
        assert [r["ticker"] for r in body["changes"]["returns_20d"]] == (
            ["005930"] if first["holdings"][0]["return_20d_pct"] is not None else []
        )
        assert body["disclaimer"] == first["disclaimer"]

    def test_removed_holding(self, signup):
        user = signup()
        _buy(user, "005930", 3)
        _buy(user, "247540", 2)
        first = _diagnose(user).json()
        assert client.post("/api/portfolio/orders", json={"ticker": "247540", "side": "sell", "quantity": 2},
                           headers=user["headers"]).status_code == 200
        second = _diagnose(user).json()
        body = _get(user, f"/{second['diagnosis_id']}/compare", base=first["diagnosis_id"]).json()
        rows = {h["ticker"]: h for h in body["changes"]["holdings"]}
        assert rows["247540"]["status"] == "removed" and rows["247540"]["current_weight_pct"] is None
        assert rows["247540"]["weight_change_pp"] == -0.02
        assert {f["code"] for f in body["changes"]["flags"]["added"]} == {"MARKET_SKEW"}

    def test_first_diagnosis_has_null_previous_and_changes(self, signup):
        user = signup()
        _buy(user, "005930", 1)
        d_id = _diagnose(user).json()["diagnosis_id"]
        r = _get(user, f"/{d_id}/compare")
        assert r.status_code == 200
        body = r.json()
        assert body["previous"] is None and body["changes"] is None and body["current"]["id"] == d_id

    def test_base_can_be_any_of_my_diagnoses(self, signup):
        user = signup()
        _buy(user, "005930", 1)
        ids = [_diagnose(user).json()["diagnosis_id"] for _ in range(3)]
        body = _get(user, f"/{ids[2]}/compare", base=ids[0]).json()
        assert body["previous"]["id"] == ids[0]
        default = _get(user, f"/{ids[2]}/compare").json()
        assert default["previous"]["id"] == ids[1]

    def test_missing_base_is_404(self, signup):
        user = signup()
        _buy(user, "005930", 1)
        d_id = _diagnose(user).json()["diagnosis_id"]
        assert _get(user, f"/{d_id}/compare", base=d_id + 100000).status_code == 404

    def test_default_previous_follows_created_at_not_id(self, signup):
        """id 순서와 created_at 순서가 어긋나도 created_at 기준으로 바로 이전을 고른다."""
        user = signup()
        t = datetime(2026, 3, 1, tzinfo=timezone.utc)
        db = SessionLocal()
        try:
            rows = [
                PortfolioDiagnosis(user_id=user["id"], created_at=t + timedelta(days=2), source="llm", total_asset=3,
                                   holdings_count=1, result=_minimal(3)),   # id 가장 작음, 시각은 가장 늦음
                PortfolioDiagnosis(user_id=user["id"], created_at=t, source="llm", total_asset=1, holdings_count=1,
                                   result=_minimal(1)),
                PortfolioDiagnosis(user_id=user["id"], created_at=t + timedelta(days=1), source="llm", total_asset=2,
                                   holdings_count=1, result=_minimal(2)),
            ]
            db.add_all(rows)
            db.commit()
            latest, oldest, middle = (r.id for r in rows)
        finally:
            db.close()
        assert _get(user, f"/{latest}/compare").json()["previous"]["id"] == middle
        assert _get(user, f"/{middle}/compare").json()["previous"]["id"] == oldest
        assert _get(user, f"/{oldest}/compare").json()["previous"] is None

    def test_same_created_at_is_broken_by_id(self, signup):
        user = signup()
        t = datetime(2026, 3, 1, tzinfo=timezone.utc)
        db = SessionLocal()
        try:
            rows = [PortfolioDiagnosis(user_id=user["id"], created_at=t, source="llm", total_asset=i, holdings_count=1,
                                       result=_minimal(i)) for i in (1, 2)]
            db.add_all(rows)
            db.commit()
            first, second = (r.id for r in rows)
        finally:
            db.close()
        assert _get(user, f"/{second}/compare").json()["previous"]["id"] == first
        assert _get(user, f"/{first}/compare").json()["previous"] is None


def _minimal(total_asset):
    """비교에 필요한 최소 본문(응답 모델 검증을 통과하는 형태가 아니라 compare 계산용)."""
    return {
        "source": "llm", "summary": "s", "flags": [], "holdings": [],
        "metrics": {"total_asset": float(total_asset), "holding_count": 1},
    }


class TestDelete:
    def test_delete_one(self, signup):
        user = signup()
        _buy(user, "005930", 1)
        keep = _diagnose(user).json()["diagnosis_id"]
        gone = _diagnose(user).json()["diagnosis_id"]
        r = client.delete(f"{BASE}/{gone}", headers=user["headers"])
        assert r.status_code == 204 and r.content == b""
        assert _get(user, f"/{gone}").status_code == 404
        assert [row.id for row in _rows(user["id"])] == [keep]
        assert client.delete(f"{BASE}/{gone}", headers=user["headers"]).status_code == 404

    def test_default_previous_skips_a_deleted_diagnosis(self, signup):
        user = signup()
        _buy(user, "005930", 1)
        ids = [_diagnose(user).json()["diagnosis_id"] for _ in range(3)]
        client.delete(f"{BASE}/{ids[1]}", headers=user["headers"])
        assert _get(user, f"/{ids[2]}/compare").json()["previous"]["id"] == ids[0]

    def test_deleting_the_user_deletes_the_diagnoses(self, signup, dev_db):
        user = signup()
        _buy(user, "005930", 1)
        _diagnose(user)
        assert len(_rows(user["id"])) == 1
        dev_db.query(User).filter(User.id == user["id"]).delete()
        dev_db.commit()
        assert _rows(user["id"]) == []


class TestSchema:
    def test_index_is_on_user_id_and_created_at_desc(self, dev_db):
        dev_db.rollback()
        indexdef = dev_db.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'portfolio_diagnosis' "
            "AND indexname = 'ix_portfolio_diagnosis_user_id_created_at'")).scalar()
        assert indexdef is not None and "(user_id, created_at DESC)" in indexdef

    def test_columns(self, dev_db):
        dev_db.rollback()
        cols = {r[0]: (r[1], r[2]) for r in dev_db.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'portfolio_diagnosis'"))}
        assert cols == {
            "id": ("bigint", "NO"), "user_id": ("bigint", "NO"), "created_at": ("timestamp with time zone", "NO"),
            "source": ("character varying", "NO"), "total_asset": ("numeric", "NO"),
            "holdings_count": ("integer", "NO"), "result": ("jsonb", "NO"),
        }

    def test_user_fk_cascades(self, dev_db):
        dev_db.rollback()
        rule = dev_db.execute(text(
            "SELECT rc.delete_rule FROM information_schema.referential_constraints rc "
            "JOIN information_schema.table_constraints tc ON tc.constraint_name = rc.constraint_name "
            "WHERE tc.table_name = 'portfolio_diagnosis' AND tc.constraint_type = 'FOREIGN KEY'")).scalar()
        assert rule == "CASCADE"
