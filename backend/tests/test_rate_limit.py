"""레이트 리밋(app/rate_limit). 리미터 단위(주입한 시계로 결정적)와 라우트 연결(TestClient, DB·Ollama는 가짜).

conftest의 rate_limit_off가 기본으로 끄므로 여기서는 RATE_LIMIT_ENABLED=1로 켠 상태를 검증한다."""

import threading

import pytest
from fastapi.testclient import TestClient

import app.api.auth as auth_api
import app.api.portfolio as portfolio_api
import app.api.research as research_api
from app import rate_limit as RL
from app.auth import CurrentUser
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    RL.reset_all()
    yield
    RL.reset_all()


# ---------- 리미터 단위 ----------


class TestSlidingWindow:
    def test_limit_and_retry_after(self):
        clock = Clock()
        w = RL.SlidingWindow(60, clock=clock)
        assert [w.hit("a", 3)[0] for _ in range(3)] == [True] * 3
        ok, wait = w.hit("a", 3)
        assert ok is False and wait == 60
        clock.t += 59.9
        assert w.hit("a", 3)[0] is False
        clock.t += 0.2  # 첫 요청이 창 밖으로 나감
        assert w.hit("a", 3)[0] is True

    def test_keys_are_independent(self):
        w = RL.SlidingWindow(60, clock=Clock())
        assert w.hit("a", 1)[0] and not w.hit("a", 1)[0] and w.hit("b", 1)[0]

    def test_key_count_is_capped_and_expired_keys_are_dropped(self):
        clock = Clock()
        w = RL.SlidingWindow(60, clock=clock, max_keys=100)
        for i in range(500):
            w.hit(f"k{i}", 5)
        assert w.size() <= 100
        clock.t += 120  # 모두 만료
        for i in range(20):
            w.hit(f"new{i}", 5)
        assert w.size() < 100

    def test_thread_safe(self):
        w = RL.SlidingWindow(60, clock=Clock())
        allowed = []

        def worker():
            for _ in range(100):
                allowed.append(w.hit("same", 250)[0])

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(allowed) == 250  # 800번 중 정확히 한도만큼만 허용


class TestFailureLock:
    def test_locks_after_threshold_and_success_resets(self):
        clock = Clock()
        f = RL.FailureLock(clock=clock)
        for _ in range(4):
            f.failure("u|ip", 5, 300)
        assert f.locked_for("u|ip") == 0
        f.reset("u|ip")  # 성공하면 처음부터
        for _ in range(4):
            f.failure("u|ip", 5, 300)
        assert f.locked_for("u|ip") == 0
        f.failure("u|ip", 5, 300)
        assert f.locked_for("u|ip") == 300
        clock.t += 301
        assert f.locked_for("u|ip") == 0  # 잠금이 끝나면 다시 센다
        f.failure("u|ip", 5, 300)
        assert f.locked_for("u|ip") == 0

    def test_old_failures_are_forgotten(self):
        clock = Clock()
        f = RL.FailureLock(clock=clock)
        for _ in range(4):
            f.failure("k", 5, 300)
        clock.t += 400
        f.failure("k", 5, 300)
        assert f.locked_for("k") == 0


class TestConcurrency:
    def test_acquire_release(self):
        c = RL.Concurrency()
        assert c.acquire("u", 1) and not c.acquire("u", 1) and c.acquire("v", 1)
        c.release("u")
        assert c.acquire("u", 1)


def test_disabled_by_default_in_tests():
    """conftest가 테스트 전체에서 끈다(다른 테스트가 한도에 걸리지 않게). 운영 기본값은 켜짐."""
    assert RL.enabled() is False


def test_enabled_by_default_without_env(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_ENABLED")
    assert RL.enabled() is True


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ORDERS_PER_MIN", "7")
    assert RL.setting("RATE_LIMIT_ORDERS_PER_MIN") == 7
    monkeypatch.setenv("RATE_LIMIT_ORDERS_PER_MIN", "abc")
    assert RL.setting("RATE_LIMIT_ORDERS_PER_MIN") == RL.DEFAULTS["RATE_LIMIT_ORDERS_PER_MIN"]


# ---------- 라우트 ----------


def _fail_auth(monkeypatch, ok_password="right"):
    user = CurrentUser(id=5, username="alice")
    monkeypatch.setattr(auth_api, "authenticate", lambda db, u, p: user if p == ok_password else None)


def login(username="alice", password="wrong"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


class TestLoginLimits:
    def test_lock_after_five_failures_same_detail_shape(self, on, monkeypatch):
        _fail_auth(monkeypatch)
        assert [login().status_code for _ in range(5)] == [401] * 5
        r = login()
        assert r.status_code == 429
        assert r.json() == {"detail": "요청이 너무 많습니다. 300초 후 다시 시도해주세요."}
        assert r.headers["retry-after"] == "300"
        assert login(password="right").status_code == 429  # 잠금 중에는 맞는 비밀번호도 기다린다

    def test_unknown_account_is_locked_the_same_way(self, on, monkeypatch):
        _fail_auth(monkeypatch)
        codes = [login(username="nobody").status_code for _ in range(6)]
        assert codes == [401] * 5 + [429]  # 계정이 없어도 같은 순서·같은 응답(존재 여부가 드러나지 않음)

    def test_success_resets_failures(self, on, monkeypatch):
        _fail_auth(monkeypatch)
        for _ in range(4):
            login()
        assert login(password="right").status_code == 200
        assert [login().status_code for _ in range(4)] == [401] * 4  # 다시 처음부터 센다

    def test_other_username_is_not_locked(self, on, monkeypatch):
        _fail_auth(monkeypatch)
        for _ in range(5):
            login()
        assert login(username="bob").status_code == 401

    def test_ip_limit_counts_every_login(self, on, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_LOGIN_PER_MIN", "3")
        _fail_auth(monkeypatch)
        codes = [login(username=f"u{i}").status_code for i in range(4)]
        assert codes == [401, 401, 401, 429]

    def test_signup_ip_limit(self, on, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_SIGNUP_PER_HOUR", "2")
        made = []
        monkeypatch.setattr(auth_api, "create_user", lambda db, u, p: made.append(u) or CurrentUser(id=len(made), username=u))
        codes = [client.post("/api/auth/signup", json={"username": f"user{i}", "password": "pw-12345678"}).status_code
                 for i in range(3)]
        assert codes == [201, 201, 429] and len(made) == 2


class TestUserLimits:
    def test_research_requires_login_before_rate_limit(self, on):
        for _ in range(10):
            assert client.post("/api/research", json={"question": "삼성전자 주가"}).status_code == 401

    def test_research_per_minute(self, on, monkeypatch, login_as):
        login_as(9)
        monkeypatch.setenv("RATE_LIMIT_RESEARCH_PER_MIN", "2")
        monkeypatch.setattr(research_api, "ask_question", lambda q, **kw: {
            "answer": "a", "used_tools": [], "sources": [], "report_id": None, "previous_report_id": None})
        codes = [client.post("/api/research", json={"question": "q"}).status_code for _ in range(3)]
        assert codes == [200, 200, 429]

    def test_research_one_at_a_time_per_user(self, on, monkeypatch, login_as):
        login_as(9)
        started, release = threading.Event(), threading.Event()

        def slow(q, **kw):
            started.set()
            release.wait(5)
            return {"answer": "a", "used_tools": [], "sources": [], "report_id": None, "previous_report_id": None}

        monkeypatch.setattr(research_api, "ask_question", slow)
        first = {}
        t = threading.Thread(target=lambda: first.update(r=client.post("/api/research", json={"question": "q"})))
        t.start()
        assert started.wait(5)
        second = client.post("/api/research", json={"question": "q2"})
        release.set()
        t.join(5)
        assert first["r"].status_code == 200
        assert second.status_code == 429 and second.headers["retry-after"] == str(RL.CONCURRENT_RETRY_AFTER_S)
        assert client.post("/api/research", json={"question": "q3"}).status_code == 200  # 끝나면 다시 가능

    def test_orders_per_minute(self, on, monkeypatch, login_as):
        login_as(9)
        monkeypatch.setenv("RATE_LIMIT_ORDERS_PER_MIN", "1")
        from app.tools.company_resolver import Resolution

        monkeypatch.setattr(portfolio_api, "resolve_company", lambda db, t, allow_fuzzy=False: Resolution(company=None, message="x"))
        codes = [client.post("/api/portfolio/orders", json={"ticker": "X", "side": "buy", "quantity": 1}).status_code
                 for _ in range(2)]
        assert codes == [404, 429]
