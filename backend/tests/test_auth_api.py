"""/api/auth 라우트와 get_current_user 의존성(DB 없이 mock). 실제 DB 흐름은 test_auth_devdb.py."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.api.auth as auth_api
import app.api.deps as deps
from app.auth import ACCESS_TOKEN_TTL, CurrentUser, UsernameTaken, create_access_token
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

USER = CurrentUser(id=3, username="alice")
PASSWORD = "s3cret-pass-word"
LOGIN_FAILED = {"detail": "아이디 또는 비밀번호가 올바르지 않습니다"}

PROTECTED = [
    ("GET", "/api/research"),
    ("POST", "/api/research"),
    ("GET", "/api/research/1"),
    ("DELETE", "/api/research/1"),
    ("GET", "/api/watchlist"),
    ("POST", "/api/watchlist"),
    ("DELETE", "/api/watchlist/005930"),
    ("GET", "/api/portfolio"),
    ("POST", "/api/portfolio/orders"),
    ("GET", "/api/auth/me"),
]


def _call(method, path, **kw):
    return client.request(method, path, **kw)


@pytest.fixture
def no_db(monkeypatch):
    """auth 라우트가 SessionLocal을 열어도 실제 DB에 붙지 않게 한다."""
    monkeypatch.setattr(auth_api, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())


@pytest.fixture
def known_user(monkeypatch):
    """토큰의 user id가 USER면 DB 조회 없이 USER를 돌려준다."""
    monkeypatch.setattr(deps, "load_user", lambda user_id: USER if user_id == USER.id else None)


class TestSignup:
    def test_success_is_201_with_token(self, monkeypatch, no_db, known_user):
        seen = {}

        def fake_create(db, username, password):
            seen.update(username=username, password=password)
            return USER

        monkeypatch.setattr(auth_api, "create_user", fake_create)
        r = client.post("/api/auth/signup", json={"username": "Alice", "password": PASSWORD})

        assert r.status_code == 201
        body = r.json()
        assert set(body) == {"user", "access_token", "token_type"}
        assert body["user"] == {"id": 3, "username": "alice"}
        assert body["token_type"] == "bearer"
        assert seen["username"] == "alice"  # 대문자는 소문자로 정규화돼서 저장된다
        assert PASSWORD not in r.text
        # 발급된 토큰으로 바로 /me가 된다
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.json() == {"id": 3, "username": "alice"}

    def test_duplicate_is_409(self, monkeypatch, no_db):
        def taken(db, username, password):
            raise UsernameTaken()

        monkeypatch.setattr(auth_api, "create_user", taken)
        r = client.post("/api/auth/signup", json={"username": "alice", "password": PASSWORD})
        assert r.status_code == 409
        assert "access_token" not in r.json()

    @pytest.mark.parametrize(
        "username, password",
        [
            ("ab", PASSWORD), ("a" * 21, PASSWORD), ("bad-name", PASSWORD), ("한글", PASSWORD),
            ("alice", "short7!"), ("alice", "p" * 129), (123, PASSWORD), ("alice", None),
        ],
    )
    def test_invalid_input_is_422_and_never_echoes_input(self, monkeypatch, username, password):
        monkeypatch.setattr(auth_api, "create_user", lambda *a: pytest.fail("호출되면 안 됨"))
        r = client.post("/api/auth/signup", json={"username": username, "password": password})
        assert r.status_code == 422
        for err in r.json()["detail"]:
            assert "input" not in err
        if isinstance(password, str):
            assert password not in r.text

    def test_non_object_body_does_not_echo_body(self):
        r = client.post("/api/auth/signup", json=["alice", PASSWORD])
        assert r.status_code == 422
        assert PASSWORD not in r.text

    def test_db_error_is_503_without_leaking(self, monkeypatch, no_db):
        def boom(db, username, password):
            raise OperationalError("INSERT INTO users ...", {"password_hash": "$argon2id$leak"}, Exception("down"))

        monkeypatch.setattr(auth_api, "create_user", boom)
        r = client.post("/api/auth/signup", json={"username": "alice", "password": PASSWORD})
        assert r.status_code == 503
        assert "argon2" not in r.text and PASSWORD not in r.text


class TestLogin:
    def test_success_is_200_same_shape_as_signup(self, monkeypatch, no_db):
        monkeypatch.setattr(auth_api, "authenticate", lambda db, u, p: USER if (u, p) == ("alice", PASSWORD) else None)
        r = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"user", "access_token", "token_type"}
        assert body["user"] == {"id": 3, "username": "alice"}

    def test_unknown_user_and_wrong_password_are_identical(self, monkeypatch, no_db):
        """아이디 없음 / 비밀번호 틀림 응답이 상태코드, 본문, 헤더까지 완전히 같아야 한다."""
        monkeypatch.setattr(auth_api, "authenticate", lambda db, u, p: None)
        unknown = client.post("/api/auth/login", json={"username": "nobody", "password": PASSWORD})
        wrong = client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"})

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json() == LOGIN_FAILED
        assert unknown.headers.get("www-authenticate") == wrong.headers.get("www-authenticate") == "Bearer"
        assert unknown.content == wrong.content

    @pytest.mark.parametrize("body", [{}, {"username": "alice"}, {"username": "", "password": "x"},
                                      {"username": "alice", "password": "p" * 129}])
    def test_malformed_is_422_without_echo(self, body):
        r = client.post("/api/auth/login", json=body)
        assert r.status_code == 422
        for err in r.json()["detail"]:
            assert "input" not in err

    def test_db_error_is_503(self, monkeypatch, no_db):
        def boom(db, u, p):
            raise OperationalError("SELECT", {}, Exception("down"))

        monkeypatch.setattr(auth_api, "authenticate", boom)
        assert client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 503


class TestBearerDependency:
    """토큰 없음/형식 오류/서명 오류/만료/없는 사용자 → 전부 같은 401 + WWW-Authenticate: Bearer."""

    def _assert_401(self, r):
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
        assert r.json() == {"detail": deps.UNAUTHORIZED_DETAIL}

    @pytest.mark.parametrize("method, path", PROTECTED)
    def test_no_token_is_401_everywhere(self, method, path):
        self._assert_401(_call(method, path))

    @pytest.mark.parametrize("method, path", PROTECTED)
    def test_device_id_header_alone_is_401(self, method, path):
        self._assert_401(_call(method, path, headers={"X-Device-Id": "legacy-device"}))

    @pytest.mark.parametrize(
        "header",
        ["Bearer", "Bearer ", "Basic abc", "Token abc", "bearer", "Bearer a b", "abc.def.ghi"],
    )
    def test_malformed_header_is_401(self, known_user, header):
        self._assert_401(client.get("/api/auth/me", headers={"Authorization": header}))

    def test_scheme_is_case_insensitive(self, known_user):
        token = create_access_token(USER.id)
        assert client.get("/api/auth/me", headers={"Authorization": f"bearer {token}"}).status_code == 200

    def test_bad_signature_is_401(self, known_user):
        token = create_access_token(USER.id)
        self._assert_401(client.get("/api/auth/me", headers={"Authorization": f"Bearer {token[:-3]}xyz"}))

    def test_expired_is_401(self, known_user):
        old = datetime.now(timezone.utc) - ACCESS_TOKEN_TTL - timedelta(minutes=1)
        token = create_access_token(USER.id, now=old)
        self._assert_401(client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}))

    def test_token_signed_with_rotated_secret_is_401(self, known_user, monkeypatch):
        token = create_access_token(USER.id)
        monkeypatch.setenv("JWT_SECRET", "r" * 48)
        self._assert_401(client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}))

    def test_deleted_user_is_401(self, known_user):
        token = create_access_token(999)  # known_user는 999를 모른다
        self._assert_401(client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}))

    def test_valid_token_returns_me(self, known_user):
        token = create_access_token(USER.id)
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json() == {"id": 3, "username": "alice"}


def _requires_login(dependant) -> bool:
    return any(d.call is deps.get_current_user or _requires_login(d) for d in dependant.dependencies)


class TestRouteCoverage:
    """앱에 등록된 모든 /api 라우트가 의도한 쪽(인증 필요/공개)에 있는지 의존성 그래프로 확인한다.
    새 라우트를 추가하면 여기 목록에도 넣어야 통과한다(인증 누락을 조용히 넘기지 않기 위해)."""

    PUBLIC = {
        ("POST", "/api/auth/signup"),
        ("POST", "/api/auth/login"),
        ("GET", "/api/companies"),
        ("GET", "/api/discovery/trending"),
        ("GET", "/api/stocks/{ticker}/realtime-price"),
    }
    PROTECTED_ROUTES = {
        ("GET", "/api/auth/me"),
        ("POST", "/api/research"),
        ("GET", "/api/research"),
        ("GET", "/api/research/{report_id}"),
        ("DELETE", "/api/research/{report_id}"),
        ("GET", "/api/watchlist"),
        ("POST", "/api/watchlist"),
        ("DELETE", "/api/watchlist/{ticker}"),
        ("GET", "/api/portfolio"),
        ("POST", "/api/portfolio/orders"),
    }

    def _routes(self):
        # 이 FastAPI 버전은 include_router한 라우터를 래퍼(original_router)로 감싸 app.routes에 넣는다.
        api_routes = []
        for route in app.routes:
            if isinstance(route, APIRoute):
                api_routes.append(route)
            elif hasattr(route, "original_router"):
                api_routes.extend(r for r in route.original_router.routes if isinstance(r, APIRoute))
        for route in api_routes:
            if route.path.startswith("/api"):
                for method in route.methods:
                    yield (method, route.path), _requires_login(route.dependant)

    def test_every_api_route_is_classified(self):
        seen = {key for key, _ in self._routes()}
        assert seen  # 라우트 수집이 비어서 아래 검사가 공허하게 통과하는 일이 없게
        assert seen == self.PUBLIC | self.PROTECTED_ROUTES

    def test_protected_routes_require_login_and_public_ones_do_not(self):
        for key, needs_login in self._routes():
            assert needs_login == (key in self.PROTECTED_ROUTES), key


class TestStartup:
    def test_missing_secret_fails_startup(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET")
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            with TestClient(app):
                pass

    def test_short_secret_fails_startup(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "too-short")
        with pytest.raises(RuntimeError, match="32자 이상"):
            with TestClient(app):
                pass

    def test_valid_secret_starts(self):
        with TestClient(app) as c:
            assert c.get("/api/auth/me").status_code == 401
