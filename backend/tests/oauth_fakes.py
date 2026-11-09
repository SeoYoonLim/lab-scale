"""소셜 로그인 테스트용 가짜 공급자(구글·네이버 HTTP 응답)와 흐름 헬퍼. 실제 공급자에는 요청하지 않는다."""

import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import requests

GOOGLE_ID_DUMMY = "dummy-google-client-id.apps.example"
GOOGLE_SECRET_DUMMY = "dummy-google-client-secret-value"
NAVER_ID_DUMMY = "dummy-naver-client-id"
NAVER_SECRET_DUMMY = "dummy-naver-client-secret-value"


def b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def unsigned_jwt(claims: dict) -> str:
    return f"{b64({'alg': 'RS256', 'kid': 'dummy'})}.{b64(claims)}.c2lnLWR1bW15"


def google_claims(sub="g-123", email="user@gmail.com", email_verified=True, nonce=None, aud=GOOGLE_ID_DUMMY,
                  iss="https://accounts.google.com", exp_in=600, name="Gil Dong"):
    c = {"iss": iss, "aud": aud, "sub": sub, "exp": int(time.time()) + exp_in, "iat": int(time.time()), "name": name, "nonce": nonce}
    if email is not None:
        c["email"] = email
        c["email_verified"] = email_verified
    return c


class FakeResponse:
    def __init__(self, status=200, body=None, text=None):
        self.status_code, self._body, self.text = status, body, text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeProvider:
    """oauth_providers.HTTP 대신 쓴다. 호출 기록(calls)과 다음 응답을 테스트가 정한다.
    google_claims_fn(nonce)가 id_token 클레임을 만든다(nonce는 로그인 시작에서 읽은 값)."""

    def __init__(self):
        self.calls = []
        self.google_claims = google_claims()
        self.google_token_error = None  # 예외 또는 FakeResponse
        self.naver_token = {"access_token": "dummy-naver-access", "token_type": "bearer"}
        self.naver_token_error = None
        self.naver_profile = {"resultcode": "00", "message": "success",
                              "response": {"id": "n-123", "email": "user@naver.com", "name": "홍길동"}}
        self.naver_profile_error = None

    def post(self, url, data):
        self.calls.append(("POST", url, dict(data)))
        if "googleapis" in url:
            if isinstance(self.google_token_error, Exception):
                raise self.google_token_error
            if self.google_token_error is not None:
                return self.google_token_error
            return FakeResponse(200, {"access_token": "dummy-google-access", "id_token": unsigned_jwt(self.google_claims),
                                      "token_type": "Bearer", "expires_in": 3599})
        if isinstance(self.naver_token_error, Exception):
            raise self.naver_token_error
        return self.naver_token_error or FakeResponse(200, self.naver_token)

    def get(self, url, headers):
        self.calls.append(("GET", url, dict(headers)))
        if isinstance(self.naver_profile_error, Exception):
            raise self.naver_profile_error
        return self.naver_profile_error or FakeResponse(200, self.naver_profile)


def configure(monkeypatch, google=True, naver=True):
    if google:
        monkeypatch.setenv("GOOGLE_CLIENT_ID", GOOGLE_ID_DUMMY)
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", GOOGLE_SECRET_DUMMY)
    if naver:
        monkeypatch.setenv("NAVER_LOGIN_CLIENT_ID", NAVER_ID_DUMMY)
        monkeypatch.setenv("NAVER_LOGIN_CLIENT_SECRET", NAVER_SECRET_DUMMY)


def state_payload(state: str) -> dict:
    body = state.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def start(client, provider):
    """login을 호출해 (응답, 공급자 URL의 쿼리 dict, state, nonce)를 돌려준다. 쿠키는 client에 남는다."""
    r = client.get(f"/api/auth/oauth/{provider}/login", follow_redirects=False)
    assert r.status_code == 302, r.text
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    return r, q, q["state"], state_payload(q["state"])["n"]


def callback(client, provider, state, code="dummy-provider-code", **extra):
    params = {"code": code, "state": state, **extra}
    return client.get(f"/api/auth/oauth/{provider}/callback", params=params, follow_redirects=False)


def code_from(location: str) -> str:
    u = urlsplit(location)
    assert u.path == "/oauth/callback" and u.query == "" and u.fragment.startswith("code="), location
    return u.fragment[len("code="):]
