"""소셜 로그인(구글·네이버, Authorization Code)의 순수 로직: 공급자 설정, state·쿠키 서명, PKCE, 계정 결정 규칙(DB·네트워크 없음).

흐름(상세는 API.md "소셜 로그인"):
  1) GET /api/auth/oauth/{provider}/login: 서명된 state(공급자, nonce, 만료 10분)를 만들어 공급자 인증 페이지로 302.
     같은 nonce와 PKCE verifier(구글)는 서명된 HttpOnly·SameSite=Lax 쿠키에 둔다.
  2) GET .../callback: state 서명·만료·종류와 쿠키 서명·만료, 둘의 nonce·공급자 일치를 확인한다(로그인 CSRF 방지).
     통과하면 서버가 code를 토큰으로 바꾸고 프로필을 받아(app/oauth_providers.py) 계정을 정한다(decide_account).
  3) 우리 JWT는 URL에 싣지 않는다. 60초 1회용 코드를 프론트로 넘기고(fragment), 프론트가 POST /api/auth/oauth/exchange로 바꾼다.

보안 메모
- 서명 키는 JWT_SECRET에서 목적을 붙여 파생한다(HMAC(JWT_SECRET, "oauth-state-v1")). JWT와 키를 공유하지 않고 형식도 달라 서로 바꿔 쓸 수 없다.
  state와 쿠키는 payload의 "t"(종류)로 구분해 쿠키 값을 state로(또는 그 반대로) 쓸 수 없다.
- PKCE verifier는 state가 아니라 쿠키에만 둔다. state는 공급자 URL과 콜백 URL에 실려 브라우저 기록·로그에 남을 수 있으므로,
  거기에 verifier가 있으면 code를 가로챈 쪽이 verifier도 함께 얻는다.
- 비교는 hmac.compare_digest. 오류 메시지·예외에 state·쿠키·비밀값을 넣지 않는다.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

PROVIDERS = ("google", "naver")
PROVIDER_NAMES = {"google": "Google", "naver": "네이버"}
STATE_TTL = timedelta(minutes=10)
COOKIE_NAME = "oauth_state"
COOKIE_PATH = "/api/auth/oauth"
DEFAULT_REDIRECT_BASE = "http://localhost:8000"
USERNAME_RANDOM_HEX = 4  # google_ + 8 hex = 15자(아이디 규칙 3~20자, [a-z0-9_])
NONCE_BYTES = 24
VERIFIER_BYTES = 48  # token_urlsafe(48) = 64자(RFC 7636: 43~128자, unreserved 문자)

ERROR_DENIED = "oauth_denied"
ERROR_FAILED = "oauth_failed"
ERROR_UNAVAILABLE = "oauth_unavailable"

_VERIFIER_RE = re.compile(r"[A-Za-z0-9\-._~]{43,128}")


class InvalidState(Exception):
    """state 또는 쿠키가 없거나, 서명·만료·종류·nonce가 맞지 않는다(메시지에 값을 넣지 않는다)."""


# ---------- 설정 ----------


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    client_id: str
    client_secret: str

    def __repr__(self) -> str:  # client secret이 repr·로그로 새지 않게
        return f"<ProviderConfig {self.provider} client_id_set={bool(self.client_id)} secret_set={bool(self.client_secret)}>"


# 환경변수 이름. 네이버는 NAVER_CLIENT_ID/SECRET이 이미 뉴스 검색 API(app/collectors.py) 키라서, 그 키가 로그인 키로 잘못 쓰여
# 로그인 버튼이 켜지는 일이 없게 로그인 전용 이름을 쓴다.
ENV_NAMES = {
    "google": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    "naver": ("NAVER_LOGIN_CLIENT_ID", "NAVER_LOGIN_CLIENT_SECRET"),
}


def provider_config(provider: str) -> ProviderConfig | None:
    """ENV_NAMES의 client id와 secret이 둘 다 있으면 설정, 아니면 None(그 공급자는 꺼짐). 호출 때마다 읽는다."""
    if provider not in PROVIDERS:
        return None
    id_name, secret_name = ENV_NAMES[provider]
    cid = (os.getenv(id_name) or "").strip()
    secret = (os.getenv(secret_name) or "").strip()
    return ProviderConfig(provider, cid, secret) if cid and secret else None


def configured_providers() -> list[str]:
    return [p for p in PROVIDERS if provider_config(p) is not None]


def redirect_base() -> str:
    return (os.getenv("OAUTH_REDIRECT_BASE") or DEFAULT_REDIRECT_BASE).strip().rstrip("/")


def callback_url(provider: str, base: str | None = None) -> str:
    """공급자 콘솔에 등록할 리디렉션 URI: {OAUTH_REDIRECT_BASE}/api/auth/oauth/{provider}/callback."""
    return f"{(base or redirect_base()).rstrip('/')}/api/auth/oauth/{provider}/callback"


def cookie_secure(base: str | None = None) -> bool:
    """백엔드 공개 주소가 https면 쿠키에 Secure를 붙인다(로컬 http 개발에서는 붙이면 쿠키가 저장되지 않는다)."""
    return (base or redirect_base()).lower().startswith("https://")


# ---------- 서명(state·쿠키) ----------


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), b"oauth-state-v1", hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict, secret: str) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    mac = _b64(hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{mac}"


def unsign(value: str | None, secret: str, kind: str, now: datetime) -> dict:
    """서명·종류·만료를 확인한 payload. 하나라도 어긋나면 InvalidState."""
    if not value or not isinstance(value, str) or len(value) > 4096 or value.count(".") != 1:
        raise InvalidState()
    body, mac = value.split(".")
    want = _b64(hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(mac, want):
        raise InvalidState()
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        raise InvalidState() from None
    if not isinstance(payload, dict) or payload.get("t") != kind:
        raise InvalidState()
    exp = payload.get("e")
    if type(exp) is not int or exp <= int(now.timestamp()):
        raise InvalidState()
    return payload


@dataclass(frozen=True)
class LoginStart:
    state: str  # 공급자 URL에 싣는 값(verifier 없음)
    cookie: str  # HttpOnly 쿠키 값(nonce + verifier)
    nonce: str
    code_verifier: str | None
    code_challenge: str | None


def start_login(provider: str, secret: str, now: datetime) -> LoginStart:
    nonce = secrets.token_urlsafe(NONCE_BYTES)
    exp = int((now + STATE_TTL).timestamp())
    verifier = challenge = None
    if provider == "google":
        verifier = secrets.token_urlsafe(VERIFIER_BYTES)
        challenge = pkce_challenge(verifier)
    state = sign({"t": "state", "p": provider, "n": nonce, "e": exp}, secret)
    cookie_payload = {"t": "cookie", "p": provider, "n": nonce, "e": exp}
    if verifier:
        cookie_payload["v"] = verifier
    return LoginStart(state, sign(cookie_payload, secret), nonce, verifier, challenge)


@dataclass(frozen=True)
class VerifiedCallback:
    nonce: str
    code_verifier: str | None


def verify_callback(provider: str, state: str | None, cookie: str | None, secret: str, now: datetime) -> VerifiedCallback:
    """콜백의 state와 쿠키를 함께 확인한다: 둘 다 서명·만료가 맞고, 같은 공급자·같은 nonce여야 한다. 아니면 InvalidState."""
    s = unsign(state, secret, "state", now)
    c = unsign(cookie, secret, "cookie", now)
    if s.get("p") != provider or c.get("p") != provider:
        raise InvalidState()
    if not isinstance(s.get("n"), str) or not hmac.compare_digest(s["n"], str(c.get("n", ""))):
        raise InvalidState()
    verifier = c.get("v")
    if provider == "google" and not (isinstance(verifier, str) and _VERIFIER_RE.fullmatch(verifier)):
        raise InvalidState()
    return VerifiedCallback(s["n"], verifier if provider == "google" else None)


# ---------- PKCE (RFC 7636, S256) ----------


def pkce_challenge(verifier: str) -> str:
    if not _VERIFIER_RE.fullmatch(verifier or ""):
        raise ValueError("code_verifier 형식이 올바르지 않습니다.")
    return _b64(hashlib.sha256(verifier.encode("ascii")).digest())


# ---------- 계정 결정 ----------


@dataclass(frozen=True)
class Profile:
    provider: str
    provider_user_id: str
    email: str | None  # 정규화한 값(형식이 틀리면 None)
    email_verified: bool  # 구글 email_verified, 네이버는 이메일이 있으면 True
    name: str | None = None


@dataclass(frozen=True)
class LocalAccount:
    user_id: int
    email_verified: bool


@dataclass(frozen=True)
class Decision:
    kind: str  # "login" | "link" | "create"
    user_id: int | None = None  # login·link 대상
    email: str | None = None  # create일 때 새 계정에 넣을 이메일(없으면 None)
    email_verified: bool = False


def decide_account(profile: Profile, identity_user_id: int | None, same_email_account: LocalAccount | None) -> Decision:
    """소셜 로그인 계정 결정 규칙.

    1) 이미 연결된 identity(provider, provider_user_id)가 있으면 그 사용자로 로그인한다(이메일은 보지 않는다).
    2) 공급자 이메일이 검증됐고, 같은 이메일의 로컬 계정이 있고 그 계정의 이메일도 인증돼 있으면 그 계정에 연결한다
       (둘 다 같은 주소의 소유를 확인한 경우에만 같은 사람으로 본다).
    3) 그 밖에 같은 이메일의 로컬 계정이 있으면(어느 한쪽이라도 미검증) 연결하지 않고 이메일 없이 새 계정을 만든다.
       미검증 이메일로 남의 계정에 붙는 계정 탈취를 막고, 이메일 유일성도 지킨다.
    4) 충돌이 없으면 새 계정. 공급자 이메일이 검증됐으면 이메일과 인증 상태를 채우고, 아니면 이메일을 비운다.
    """
    if identity_user_id is not None:
        return Decision("login", identity_user_id)
    if same_email_account is not None:
        if profile.email_verified and same_email_account.email_verified:
            return Decision("link", same_email_account.user_id)
        return Decision("create")
    if profile.email and profile.email_verified:
        return Decision("create", email=profile.email, email_verified=True)
    return Decision("create")


def new_username(provider: str) -> str:
    """google_a1b2c3d4 / naver_a1b2c3d4. 아이디 규칙(3~20자, [a-z0-9_])을 지키고 유일성은 DB 계층이 재시도로 보장한다."""
    return f"{provider}_{secrets.token_hex(USERNAME_RANDOM_HEX)}"


def now() -> datetime:
    return datetime.now(timezone.utc)
