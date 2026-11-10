"""공급자별 어댑터(구글·네이버): 인증 URL 만들기, code → 토큰 교환, 프로필(Profile) 얻기.

HTTP는 모듈 변수 HTTP(get/post)를 통해서만 한다. 기본은 app.http_retry(timeout (5, 10)초, 일시 오류만 1번 재시도, 합계 15초 상한)이고
테스트는 HTTP를 가짜로 바꿔 실제 구글·네이버에 요청하지 않는다.

구글 ID 토큰: 토큰 엔드포인트(oauth2.googleapis.com)에서 서버끼리 TLS로 직접 받은 응답의 id_token만 쓴다. OpenID Connect Core 3.1.3.7(6)에 따라
이렇게 직접 받은 토큰은 서명 검증 대신 TLS 서버 인증으로 발급자를 확인할 수 있어서 서명(JWKS) 검증은 하지 않고, 대신 iss(구글),
aud(우리 client_id), exp(만료 전), nonce(이번 로그인의 nonce)를 반드시 확인한다. 브라우저를 거쳐 온 id_token은 받지 않는다.

예외: 어떤 실패든 OAuthProviderError(reason)로 바꾼다. 메시지에는 짧은 사유 코드만 넣고 공급자 응답 본문·토큰·client secret은 넣지 않는다.
네이버 토큰 요청은 GET 쿼리가 아니라 POST 본문으로 보내 client secret이 URL(로그·예외 메시지)에 남지 않게 한다.
"""

import base64
import json
from datetime import datetime
from urllib.parse import urlencode

from app import http_retry
from app.account_recovery import normalize_email
from app.oauth import Profile, ProviderConfig, callback_url

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_EXCHANGE_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_SCOPE = "openid email profile"
NAVER_AUTH_URL = "https://nid.naver.com/oauth2.0/authorize"
NAVER_EXCHANGE_URL = "https://nid.naver.com/oauth2.0/token"
NAVER_PROFILE_URL = "https://openapi.naver.com/v1/nid/me"
TIMEOUT = (5, 10)
RETRY = {"retries": 1, "max_total": 15.0}


class OAuthProviderError(Exception):
    """공급자 통신·응답 오류. args[0]은 짧은 사유 코드(token_http, token_error, id_token_invalid, profile_http, profile_invalid)."""


class _DefaultHTTP:
    def post(self, url, data):
        return http_retry.post(url, data=data, timeout=TIMEOUT, **RETRY)

    def get(self, url, headers):
        return http_retry.get(url, headers=headers, timeout=TIMEOUT, **RETRY)


HTTP = _DefaultHTTP()


def authorize_url(cfg: ProviderConfig, state: str, nonce: str, code_challenge: str | None) -> str:
    redirect = callback_url(cfg.provider)
    if cfg.provider == "google":
        params = {"client_id": cfg.client_id, "redirect_uri": redirect, "response_type": "code", "scope": GOOGLE_SCOPE,
                  "state": state, "nonce": nonce, "code_challenge": code_challenge, "code_challenge_method": "S256",
                  "prompt": "select_account"}
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    # 네이버는 요청 항목(이름·이메일)을 개발자 센터에서 고른다(scope 파라미터 없음)
    params = {"response_type": "code", "client_id": cfg.client_id, "redirect_uri": redirect, "state": state}
    return f"{NAVER_AUTH_URL}?{urlencode(params)}"


def _json(resp, reason: str) -> dict:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - 본문을 로그에 남기지 않는다
        raise OAuthProviderError(reason) from None
    if not isinstance(body, dict):
        raise OAuthProviderError(reason)
    return body


def _email(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        return normalize_email(raw)
    except ValueError:
        return None


def fetch_profile(cfg: ProviderConfig, code: str, state: str, nonce: str, code_verifier: str | None, now: datetime) -> Profile:
    if cfg.provider == "google":
        return _google(cfg, code, nonce, code_verifier, now)
    return _naver(cfg, code, state)


def _google(cfg: ProviderConfig, code: str, nonce: str, code_verifier: str | None, now: datetime) -> Profile:
    data = {"code": code, "client_id": cfg.client_id, "client_secret": cfg.client_secret, "redirect_uri": callback_url("google"),
            "grant_type": "authorization_code", "code_verifier": code_verifier}
    try:
        resp = HTTP.post(GOOGLE_EXCHANGE_URL, data)
    except Exception:  # noqa: BLE001
        raise OAuthProviderError("token_http") from None
    body = _json(resp, "token_error")
    claims = id_token_claims(body.get("id_token"))
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    exp = claims.get("exp")
    ok = (
        claims.get("iss") in GOOGLE_ISSUERS and cfg.client_id in auds
        and isinstance(exp, (int, float)) and exp > now.timestamp()
        and claims.get("nonce") == nonce and isinstance(claims.get("sub"), str) and claims["sub"]
    )
    if not ok:
        raise OAuthProviderError("id_token_invalid")
    verified = claims.get("email_verified") in (True, "true")
    return Profile("google", claims["sub"], _email(claims.get("email")), verified and _email(claims.get("email")) is not None,
                   claims.get("name") if isinstance(claims.get("name"), str) else None)


def id_token_claims(token) -> dict:
    """JWT payload(가운데 부분)만 읽는다(서명 검증 없음: 위 모듈 설명). 형식이 틀리면 OAuthProviderError."""
    if not isinstance(token, str) or token.count(".") != 2:
        raise OAuthProviderError("id_token_invalid")
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, TypeError):
        raise OAuthProviderError("id_token_invalid") from None
    if not isinstance(claims, dict):
        raise OAuthProviderError("id_token_invalid")
    return claims


def _naver(cfg: ProviderConfig, code: str, state: str) -> Profile:
    data = {"grant_type": "authorization_code", "client_id": cfg.client_id, "client_secret": cfg.client_secret,
            "code": code, "state": state}
    try:
        resp = HTTP.post(NAVER_EXCHANGE_URL, data)
    except Exception:  # noqa: BLE001
        raise OAuthProviderError("token_http") from None
    body = _json(resp, "token_error")
    token = body.get("access_token")
    if body.get("error") or not isinstance(token, str) or not token:  # 네이버는 실패도 200 + {"error": ...}로 줄 수 있다
        raise OAuthProviderError("token_error")
    try:
        resp = HTTP.get(NAVER_PROFILE_URL, {"Authorization": f"Bearer {token}"})
    except Exception:  # noqa: BLE001
        raise OAuthProviderError("profile_http") from None
    body = _json(resp, "profile_invalid")
    info = body.get("response")
    if body.get("resultcode") != "00" or not isinstance(info, dict) or not isinstance(info.get("id"), str) or not info["id"]:
        raise OAuthProviderError("profile_invalid")
    email = _email(info.get("email"))
    # 네이버가 주는 이메일은 네이버가 확인한 계정 이메일로 취급한다(요구사항). 이메일 제공에 동의하지 않으면 없다.
    return Profile("naver", info["id"], email, email is not None, info.get("name") if isinstance(info.get("name"), str) else None)
