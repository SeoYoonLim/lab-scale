"""소셜 로그인(구글·네이버) 라우트. 순수 로직은 app/oauth.py, 공급자 통신은 app/oauth_providers.py, DB는 app/oauth_db.py.

- GET  /api/auth/oauth/providers           : 설정된 공급자 목록(키 값은 내보내지 않는다)
- GET  /api/auth/oauth/{provider}/login    : 브라우저를 공급자 인증 페이지로 302(서명된 state + HttpOnly 쿠키, 구글은 PKCE)
- GET  /api/auth/oauth/{provider}/callback : state·쿠키 검증 → code 교환·프로필 → 계정 결정 → 프론트로 302(#code=1회용 코드)
- POST /api/auth/oauth/exchange            : 1회용 코드 → 아이디/비밀번호 로그인과 같은 응답(access token)

브라우저가 이동하는 두 GET은 오류를 JSON이 아니라 프론트 화면으로 보낸다: {FRONTEND_BASE_URL}/login?error=oauth_denied|oauth_failed|oauth_unavailable.
상세 원인은 URL에 넣지 않고 서버 로그에 사유 코드만 남긴다(공급자 응답 본문·토큰·client secret은 남기지 않는다).
키가 없는 공급자의 login은 503이 아니라 oauth_unavailable 리디렉션이다(브라우저 이동이라 JSON을 보여줄 화면이 없다).
리디렉션 응답에는 Cache-Control: no-store, Referrer-Policy: no-referrer를 붙인다.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app import oauth, oauth_db, oauth_providers, rate_limit, security_log
from app.account_recovery import TOKEN_MAX_LEN, frontend_base_url
from app.api.auth import AuthResponse, _auth_response
from app.api.deps import client_ip
from app.auth import get_jwt_secret
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/oauth", tags=["auth"])

INVALID_CODE = "로그인 코드가 올바르지 않거나 만료됐습니다. 다시 로그인해주세요."
UNKNOWN_PROVIDER = "지원하지 않는 로그인 방식입니다."
MAX_PARAM_LEN = 2048
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class ProviderOut(BaseModel):
    id: str
    name: str
    login_path: str


class ProvidersResponse(BaseModel):
    providers: list[ProviderOut]


class ExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=TOKEN_MAX_LEN)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=302, headers=NO_STORE)


def _error_redirect(kind: str) -> RedirectResponse:
    resp = _redirect(f"{frontend_base_url()}/login?error={kind}")
    resp.delete_cookie(oauth.COOKIE_NAME, path=oauth.COOKIE_PATH)
    return resp


def _provider(provider: str) -> str:
    if provider not in oauth.PROVIDERS:
        raise HTTPException(status_code=404, detail=UNKNOWN_PROVIDER)
    return provider


@router.get("/providers", response_model=ProvidersResponse)
def providers() -> ProvidersResponse:
    """client id·secret이 둘 다 설정된 공급자만. 키 값·설정 여부 외의 정보는 내보내지 않는다."""
    return ProvidersResponse(providers=[
        ProviderOut(id=p, name=oauth.PROVIDER_NAMES[p], login_path=f"/api/auth/oauth/{p}/login") for p in oauth.configured_providers()
    ])


@router.get("/{provider}/login")
def login(provider: str, http_request: Request) -> RedirectResponse:
    provider = _provider(provider)
    rate_limit.check(rate_limit.oauth_ip, f"login|{client_ip(http_request)}", "RATE_LIMIT_OAUTH_PER_MIN")
    cfg = oauth.provider_config(provider)
    if cfg is None:
        return _error_redirect(oauth.ERROR_UNAVAILABLE)
    start = oauth.start_login(provider, get_jwt_secret(), oauth.now())
    resp = _redirect(oauth_providers.authorize_url(cfg, start.state, start.nonce, start.code_challenge))
    resp.set_cookie(oauth.COOKIE_NAME, start.cookie, max_age=int(oauth.STATE_TTL.total_seconds()), path=oauth.COOKIE_PATH,
                    httponly=True, samesite="lax", secure=oauth.cookie_secure())
    return resp


@router.get("/{provider}/callback")
def callback(provider: str, http_request: Request) -> RedirectResponse:
    provider = _provider(provider)
    rate_limit.check(rate_limit.oauth_ip, f"callback|{client_ip(http_request)}", "RATE_LIMIT_OAUTH_PER_MIN")
    cfg = oauth.provider_config(provider)
    if cfg is None:
        return _error_redirect(oauth.ERROR_UNAVAILABLE)
    q = http_request.query_params
    error, code, state = q.get("error"), q.get("code"), q.get("state")
    if error:
        logger.info("소셜 로그인 중단: provider=%s reason=%s", provider, "access_denied" if error == "access_denied" else "provider_error")
        return _error_redirect(oauth.ERROR_DENIED if error == "access_denied" else oauth.ERROR_FAILED)
    if not code or not state or len(code) > MAX_PARAM_LEN or len(state) > MAX_PARAM_LEN:
        return _error_redirect(oauth.ERROR_FAILED)
    at = oauth.now()
    try:
        checked = oauth.verify_callback(provider, state, http_request.cookies.get(oauth.COOKIE_NAME), get_jwt_secret(), at)
    except oauth.InvalidState:
        logger.warning("소셜 로그인 실패: provider=%s reason=invalid_state", provider)
        return _error_redirect(oauth.ERROR_FAILED)
    try:
        profile = oauth_providers.fetch_profile(cfg, code, state, checked.nonce, checked.code_verifier, at)
    except oauth_providers.OAuthProviderError as e:
        logger.warning("소셜 로그인 실패: provider=%s reason=%s", provider, e.args[0] if e.args else "provider")
        return _error_redirect(oauth.ERROR_FAILED)
    db = SessionLocal()
    try:
        user, decision = oauth_db.resolve_login(db, profile, at)
        raw = oauth_db.issue_login_code(db, user.id, at)
    except Exception as e:  # noqa: BLE001 - DB 오류 등. 원문(파라미터 포함)은 남기지 않는다
        db.rollback()
        logger.warning("소셜 로그인 실패: provider=%s reason=account error=%s", provider, type(e).__name__)
        return _error_redirect(oauth.ERROR_FAILED)
    finally:
        db.close()
    logger.info("소셜 로그인: provider=%s user=%s decision=%s", provider, user.id, decision.kind)
    security_log.event("oauth_login", user_id=user.id, result=decision.kind, ip=client_ip(http_request))
    resp = _redirect(f"{frontend_base_url()}/oauth/callback#code={raw}")
    resp.delete_cookie(oauth.COOKIE_NAME, path=oauth.COOKIE_PATH)
    return resp


@router.post("/exchange", response_model=AuthResponse)
def exchange(request: ExchangeRequest, http_request: Request) -> AuthResponse:
    """1회용 코드(60초)를 로그인 응답으로 바꾼다. 잘못됐거나 만료됐거나 이미 쓴 코드는 모두 같은 400."""
    rate_limit.check(rate_limit.oauth_exchange, client_ip(http_request), "RATE_LIMIT_OAUTH_EXCHANGE_PER_MIN")
    db = SessionLocal()
    try:
        user = oauth_db.exchange_login_code(db, request.code, oauth.now())
    finally:
        db.close()
    if user is None:
        raise HTTPException(status_code=400, detail=INVALID_CODE)
    return _auth_response(user)
