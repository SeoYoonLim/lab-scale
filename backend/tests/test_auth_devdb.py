"""로그인과 사용자별 데이터 분리를 실제 dev DB + 실제 토큰으로 검증한다(get_current_user를 override하지 않는다).

- 가입 → 로그인 → /me, DB의 password_hash가 평문이 아닌지
- 사용자 A의 리포트를 B가 조회/삭제/이어 쓰기 하면 404, 목록에도 안 보이는지
- A/B의 관심종목과 모의투자가 섞이지 않는지
테스트가 만든 사용자는 끝나면 지운다(리포트는 FK CASCADE, 관심종목/모의투자는 owner key로 정리).
"""

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.api.research as research_api
import app.portfolio as portfolio_module
from app.main import app
from app.models import User, VirtualAccount, Watchlist

client = TestClient(app, raise_server_exceptions=False)

pytestmark = pytest.mark.integration


@pytest.fixture
def signup(dev_db):
    """실제 /api/auth/signup으로 가입하고 {"id","username","password","headers"}를 돌려준다. 끝나면 정리한다."""
    created = []

    def _signup(username: str | None = None):
        username = username or f"pt_{secrets.token_hex(6)}"
        password = secrets.token_urlsafe(12)
        r = client.post("/api/auth/signup", json={"username": username, "password": password})
        assert r.status_code == 201, r.text
        body = r.json()
        created.append(body["user"]["id"])
        return {
            **body["user"],
            "password": password,
            "headers": {"Authorization": f"Bearer {body['access_token']}"},
        }

    yield _signup

    dev_db.rollback()
    for user_id in created:
        key = f"user:{user_id}"
        dev_db.query(Watchlist).filter(Watchlist.device_id == key).delete()
        dev_db.query(VirtualAccount).filter(VirtualAccount.device_id == key).delete()
        dev_db.query(User).filter(User.id == user_id).delete()
    dev_db.commit()


class TestSignupLoginMe:
    def test_signup_then_login_then_me(self, signup):
        raw_name = f"PT_{secrets.token_hex(6).upper()}"
        user = signup(raw_name)
        assert user["username"] == raw_name.lower()

        # 대문자로 로그인해도 정규화돼서 같은 계정이다
        r = client.post("/api/auth/login", json={"username": raw_name, "password": user["password"]})
        assert r.status_code == 200
        body = r.json()
        assert body["user"] == {"id": user["id"], "username": raw_name.lower()}
        assert body["token_type"] == "bearer"

        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200 and me.json() == {"id": user["id"], "username": raw_name.lower()}

    def test_duplicate_username_case_insensitive_is_409(self, signup):
        user = signup()
        r = client.post("/api/auth/signup", json={"username": user["username"].upper(), "password": "another-pass-1"})
        assert r.status_code == 409

    def test_wrong_password_and_unknown_user_are_identical_401(self, signup):
        user = signup()
        wrong = client.post("/api/auth/login", json={"username": user["username"], "password": "wrong-password"})
        unknown = client.post("/api/auth/login", json={"username": f"pt_none_{secrets.token_hex(4)}", "password": "x" * 12})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.content == unknown.content

    def test_password_is_stored_as_argon2_hash_not_plaintext(self, signup, dev_db):
        user = signup()
        stored = dev_db.execute(text("SELECT password_hash FROM users WHERE id = :i"), {"i": user["id"]}).scalar()
        dev_db.rollback()
        assert stored.startswith("$argon2id$")
        assert user["password"] not in stored

    def test_deleted_user_token_is_401(self, signup, dev_db):
        user = signup()
        dev_db.query(User).filter(User.id == user["id"]).delete()
        dev_db.commit()
        assert client.get("/api/auth/me", headers=user["headers"]).status_code == 401


class TestReportIsolation:
    def _save(self, user_id: int, question: str) -> int:
        from app.reports import save_report

        report_id = save_report(question, "답변", [], user_id=user_id)
        assert report_id is not None
        return report_id

    def test_other_users_report_is_404_for_get_delete_and_follow_up(self, signup, monkeypatch, dev_db):
        a, b = signup(), signup()
        report_id = self._save(a["id"], "[pytest] A의 리포트")

        assert client.get(f"/api/research/{report_id}", headers=a["headers"]).status_code == 200

        # B 입장에서는 없는 리포트와 구분되지 않는다(같은 404, 같은 본문 형식)
        r_b = client.get(f"/api/research/{report_id}", headers=b["headers"])
        missing = client.get("/api/research/9223372036854775807", headers=b["headers"])
        assert r_b.status_code == missing.status_code == 404
        assert set(r_b.json()) == set(missing.json()) == {"detail"}

        assert client.delete(f"/api/research/{report_id}", headers=b["headers"]).status_code == 404

        # 이어 쓰기도 본인 리포트만. 남의 것이면 LLM을 부르기 전에 404
        monkeypatch.setattr(research_api, "ask_question", lambda *a, **k: pytest.fail("호출되면 안 됨"))
        r = client.post(
            "/api/research", json={"question": "그럼 뉴스는?", "previous_report_id": report_id}, headers=b["headers"]
        )
        assert r.status_code == 404

        # B의 삭제 시도 후에도 A의 리포트는 그대로다
        assert client.get(f"/api/research/{report_id}", headers=a["headers"]).status_code == 200
        assert client.delete(f"/api/research/{report_id}", headers=a["headers"]).status_code == 204

    def test_list_shows_only_own_reports(self, signup):
        a, b = signup(), signup()
        a_ids = {self._save(a["id"], "[pytest] A-1"), self._save(a["id"], "[pytest] A-2")}
        b_id = self._save(b["id"], "[pytest] B-1")

        la = client.get("/api/research", headers=a["headers"]).json()
        lb = client.get("/api/research", headers=b["headers"]).json()
        assert la["total"] == 2 and {i["report_id"] for i in la["items"]} == a_ids
        assert lb["total"] == 1 and [i["report_id"] for i in lb["items"]] == [b_id]

    def test_legacy_reports_without_owner_are_hidden(self, signup, dev_db):
        user = signup()
        legacy = dev_db.execute(text("SELECT id FROM research_report WHERE user_id IS NULL LIMIT 1")).scalar()
        dev_db.rollback()
        if legacy is None:
            pytest.skip("소유자 없는 기존 리포트가 없음")
        assert client.get(f"/api/research/{legacy}", headers=user["headers"]).status_code == 404
        assert client.get("/api/research", headers=user["headers"]).json()["total"] == 0

    def test_deleting_user_cascades_their_reports(self, signup, dev_db):
        user = signup()
        report_id = self._save(user["id"], "[pytest] 탈퇴 cascade")
        dev_db.query(User).filter(User.id == user["id"]).delete()
        dev_db.commit()
        assert dev_db.execute(text("SELECT count(*) FROM research_report WHERE id = :i"), {"i": report_id}).scalar() == 0
        dev_db.rollback()


class TestWatchlistAndPortfolioIsolation:
    def test_watchlists_do_not_mix(self, signup):
        a, b = signup(), signup()
        assert client.post("/api/watchlist", json={"ticker": "005930"}, headers=a["headers"]).status_code == 201
        assert client.post("/api/watchlist", json={"ticker": "000660"}, headers=b["headers"]).status_code == 201

        wa = [i["ticker"] for i in client.get("/api/watchlist", headers=a["headers"]).json()["items"]]
        wb = [i["ticker"] for i in client.get("/api/watchlist", headers=b["headers"]).json()["items"]]
        assert wa == ["005930"] and wb == ["000660"]

        # B가 A의 종목을 지우려 해도 B 목록에 없으니 404, A 목록은 그대로
        assert client.delete("/api/watchlist/005930", headers=b["headers"]).status_code == 404
        assert len(client.get("/api/watchlist", headers=a["headers"]).json()["items"]) == 1

    def test_watchlist_is_stored_under_user_owner_key(self, signup, dev_db):
        a = signup()
        client.post("/api/watchlist", json={"ticker": "005930"}, headers=a["headers"])
        keys = [k for (k,) in dev_db.query(Watchlist.device_id).filter(Watchlist.device_id == f"user:{a['id']}")]
        dev_db.rollback()
        assert keys == [f"user:{a['id']}"]

    def test_portfolios_do_not_mix(self, signup, monkeypatch):
        def fake_price(db, company):
            return {"current_price": 1000.0, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t",
                    "is_realtime": True, "source": "naver"}

        monkeypatch.setattr(portfolio_module, "get_realtime_price_data", fake_price)
        a, b = signup(), signup()

        order = {"ticker": "005930", "side": "buy", "quantity": 3}
        assert client.post("/api/portfolio/orders", json=order, headers=a["headers"]).status_code == 200

        pa = client.get("/api/portfolio", headers=a["headers"]).json()
        pb = client.get("/api/portfolio", headers=b["headers"]).json()
        assert [h["quantity"] for h in pa["holdings"]] == [3]
        assert pb["holdings"] == [] and pb["cash_balance"] > pa["cash_balance"]

        # B는 A의 보유 종목을 팔 수 없다(B에게는 보유 수량이 없음)
        sell = {"ticker": "005930", "side": "sell", "quantity": 1}
        assert client.post("/api/portfolio/orders", json=sell, headers=b["headers"]).status_code == 400
        assert [h["quantity"] for h in client.get("/api/portfolio", headers=a["headers"]).json()["holdings"]] == [3]
