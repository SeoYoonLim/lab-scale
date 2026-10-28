"""회원가입/로그인/내 정보.

보안 메모:
- 로그인 실패는 아이디가 없든 비밀번호가 틀리든 같은 401 + 같은 메시지다(authenticate가 더미 해시 검증으로 시간도 맞춘다).
- 요청 검증(422) 응답에서 입력값(`input`)을 빼는 처리는 app.main의 RequestValidationError 핸들러가 /api/auth 경로에 한다.
- DB 오류는 전역 DBAPIError 핸들러로 보내지 않고 여기서 503으로 끊는다. 전역 핸들러는 예외 원문을 로그에 남기는데,
  원문에 INSERT 파라미터(password_hash)가 섞일 수 있기 때문이다.
- 레이트 리밋(app/rate_limit, 2026-10-28): 로그인·가입은 IP당 횟수 제한을 먼저 보고(429), 로그인은 같은 아이디+IP의 연속 실패가
  LOGIN_LOCK_FAILURES번이면 LOGIN_LOCK_SECONDS 동안 429다. 아이디가 없어도 똑같이 세므로 응답으로 계정 존재 여부가 드러나지 않는다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import DBAPIError

from app import rate_limit
from app.api.deps import client_ip, get_current_user
from app.auth import (
    PASSWORD_MAX_LEN,
    USERNAME_MAX_LEN,
    CurrentUser,
    UsernameTaken,
    authenticate,
    create_access_token,
    create_user,
    normalize_username,
    validate_password,
)
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED_DETAIL = "아이디 또는 비밀번호가 올바르지 않습니다"


class SignupRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        return normalize_username(v)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password(v)


class LoginRequest(BaseModel):
    # 형식 검사는 하지 않는다(규칙에 안 맞는 아이디도 401로 같은 경로를 탄다). 길이 상한만 둬서 거대한 입력을 막는다.
    username: str = Field(min_length=1, max_length=USERNAME_MAX_LEN * 5)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LEN)


class UserOut(BaseModel):
    id: int
    username: str


class AuthResponse(BaseModel):
    user: UserOut
    access_token: str
    token_type: str = "bearer"


def _auth_response(user: CurrentUser) -> AuthResponse:
    return AuthResponse(user=UserOut(id=user.id, username=user.username), access_token=create_access_token(user.id))


def _db_unavailable(e: DBAPIError) -> HTTPException:
    logger.error("auth DB 오류: %s", type(e).__name__)
    return HTTPException(status_code=503, detail="데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.")


@router.post("/signup", response_model=AuthResponse, status_code=201)
def signup(request: SignupRequest, http_request: Request) -> AuthResponse:
    """가입하고 바로 로그인 상태(토큰)를 돌려준다. 아이디가 이미 있으면 409. IP당 가입 횟수를 넘으면 429."""
    rate_limit.check(rate_limit.signup_ip, client_ip(http_request), "RATE_LIMIT_SIGNUP_PER_HOUR")
    db = SessionLocal()
    try:
        user = create_user(db, request.username, request.password)
    except UsernameTaken:
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.") from None
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    return _auth_response(user)


@router.post("/login", response_model=AuthResponse)
def login(request: LoginRequest, http_request: Request) -> AuthResponse:
    ip = client_ip(http_request)
    rate_limit.check(rate_limit.login_ip, ip, "RATE_LIMIT_LOGIN_PER_MIN")
    lock_key = f"{request.username.strip().casefold()}|{ip}"
    rate_limit.check_login_lock(lock_key)
    db = SessionLocal()
    try:
        user = authenticate(db, request.username, request.password)
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    rate_limit.record_login(lock_key, success=user is not None)
    if user is None:
        raise HTTPException(status_code=401, detail=LOGIN_FAILED_DETAIL, headers={"WWW-Authenticate": "Bearer"})
    return _auth_response(user)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, username=user.username)
