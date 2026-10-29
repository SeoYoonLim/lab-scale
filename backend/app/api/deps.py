"""라우터 간에 공유하는 FastAPI 의존성.

`Authorization: Bearer <JWT>`로 사용자를 인증한다(POST /api/auth/login|signup이 발급).
헤더가 없거나, 형식이 틀렸거나, 서명/만료 검증에 실패했거나, 토큰의 사용자가 DB에 없거나, 폐기된 토큰이면
(토큰의 tv ≠ users.token_version: 로그아웃·비밀번호 변경 이후) 전부 같은 401(WWW-Authenticate: Bearer)이다.
실패 사유는 응답에 구분해서 드러내지 않는다. tv 비교는 원래 하던 사용자 조회(load_user) 결과로 하므로 DB 조회가 늘지 않는다.
"""

from fastapi import Depends, Header, HTTPException, Request

from app.auth import CurrentUser, InvalidToken, decode_token_claims, get_user
from app.db.session import SessionLocal

# 실패 사유와 무관하게 응답은 하나로 통일한다.
UNAUTHORIZED_DETAIL = "인증이 필요합니다. 다시 로그인해주세요."


def unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=UNAUTHORIZED_DETAIL, headers={"WWW-Authenticate": "Bearer"})


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise unauthorized()
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or " " in token:
        raise unauthorized()
    return token


def load_user(user_id: int) -> CurrentUser | None:
    db = SessionLocal()
    try:
        return get_user(db, user_id)
    finally:
        db.close()


def get_current_user(request: Request, authorization: str | None = Header(default=None)) -> CurrentUser:
    token = _bearer_token(authorization)
    try:
        user_id, token_version = decode_token_claims(token)
    except InvalidToken:
        raise unauthorized() from None
    user = load_user(user_id)
    if user is None or user.token_version != token_version:
        raise unauthorized()
    request.state.user_id = user.id  # 요청 로그(app.main)에 user id만 남긴다(토큰은 남기지 않음)
    return user


def client_ip(request: Request) -> str:
    """요청을 직접 보낸 주소(request.client.host). X-Forwarded-For는 위조될 수 있어 쓰지 않는다(프록시 뒤에서는 모두 같은 IP)."""
    return request.client.host if request.client else "unknown"


def get_owner_key(user: CurrentUser = Depends(get_current_user)) -> str:
    """관심종목/모의투자 테이블의 device_id 컬럼에 저장하는 소유자 키(`user:{id}`)."""
    return user.owner_key
