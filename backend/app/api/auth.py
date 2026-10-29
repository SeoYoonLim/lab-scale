"""회원가입/로그인/내 정보/로그아웃/비밀번호 변경.

보안 메모:
- 로그인 실패는 아이디가 없든 비밀번호가 틀리든 같은 401 + 같은 메시지다(authenticate가 더미 해시 검증으로 시간도 맞춘다).
- 요청 검증(422) 응답에서 입력값(`input`)을 빼는 처리는 app.main의 RequestValidationError 핸들러가 /api/auth 경로에 한다.
- DB 오류는 전역 DBAPIError 핸들러로 보내지 않고 여기서 503으로 끊는다. 전역 핸들러는 예외 원문을 로그에 남기는데,
  원문에 INSERT 파라미터(password_hash)가 섞일 수 있기 때문이다.
- 레이트 리밋(app/rate_limit, 2026-10-28): 로그인·가입은 IP당 횟수 제한을 먼저 보고(429), 로그인은 같은 아이디+IP의 연속 실패가
  LOGIN_LOCK_FAILURES번이면 LOGIN_LOCK_SECONDS 동안 429다. 아이디가 없어도 똑같이 세므로 응답으로 계정 존재 여부가 드러나지 않는다.
- 토큰 폐기(2026-10-29): 로그아웃과 비밀번호 변경은 users.token_version을 올려 그 사용자의 기존 토큰을 전부(모든 기기) 무효로 한다.
  비밀번호 변경은 새 tv로 만든 토큰을 응답에 담아 현재 세션만 이어지게 한다.
- 비밀번호 변경에서 현재 비밀번호가 틀리면 401이 아니라 422(검증 오류와 같은 detail 배열)다. 이 API에서 인증 필요 라우트의
  401은 "토큰이 무효하니 다시 로그인"이라는 뜻이고 프론트는 401을 받으면 로그인 상태를 지운다. 토큰은 유효하고 입력 하나가
  틀린 것이므로 입력 오류(422)로 돌려준다. 계정은 토큰으로 이미 특정돼 있어 존재 여부가 새는 문제는 없고, 응답에는 어떤
  비밀번호도 넣지 않는다. 연속 오류는 사용자 id 기준으로 세어 잠근다(아래 rate_limit). 아이디+IP가 아닌 사용자 기준인 이유:
  토큰을 훔친 쪽이 IP를 바꿔 가며 현재 비밀번호를 대입하는 것을 막기 위해서다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.exc import DBAPIError

from app import rate_limit
from app.api.deps import client_ip, get_current_user, unauthorized
from app.auth import (
    PASSWORD_MAX_LEN,
    USERNAME_MAX_LEN,
    CurrentUser,
    TokenRevoked,
    UsernameTaken,
    authenticate,
    change_password,
    create_access_token,
    create_user,
    normalize_username,
    revoke_tokens,
    validate_password,
)
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED_DETAIL = "아이디 또는 비밀번호가 올바르지 않습니다"
CURRENT_PASSWORD_WRONG = [
    {"type": "invalid_current_password", "loc": ["body", "current_password"], "msg": "현재 비밀번호가 올바르지 않습니다."}
]


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


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LEN)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _new_password(cls, v: str) -> str:
        return validate_password(v)  # 가입과 같은 규칙

    @model_validator(mode="after")
    def _must_differ(self):
        # 요청에 담긴 두 값끼리만 비교하므로 저장된 비밀번호에 대해서는 아무것도 드러내지 않는다.
        if self.new_password == self.current_password:
            raise PydanticCustomError("same_password", "새 비밀번호는 현재 비밀번호와 달라야 합니다.")
        return self


class LogoutResponse(BaseModel):
    ok: bool = True


class UserOut(BaseModel):
    id: int
    username: str


class AuthResponse(BaseModel):
    user: UserOut
    access_token: str
    token_type: str = "bearer"


def _auth_response(user: CurrentUser) -> AuthResponse:
    return AuthResponse(
        user=UserOut(id=user.id, username=user.username),
        access_token=create_access_token(user.id, token_version=user.token_version),
    )


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


@router.post("/logout", response_model=LogoutResponse)
def logout(user: CurrentUser = Depends(get_current_user)) -> LogoutResponse:
    """token_version을 올려 이 사용자의 모든 토큰(다른 기기 포함)을 무효로 한다. 이미 무효인 토큰이면 401."""
    rate_limit.check(rate_limit.logout, user.owner_key, "RATE_LIMIT_LOGOUT_PER_MIN")
    db = SessionLocal()
    try:
        new_version = revoke_tokens(db, user.id)
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    if new_version is None:  # 토큰 검증과 이 사이에 사용자가 지워졌다
        raise unauthorized()
    return LogoutResponse()


@router.post("/change-password", response_model=AuthResponse)
def change_password_route(
    request: ChangePasswordRequest, user: CurrentUser = Depends(get_current_user)
) -> AuthResponse:
    """현재 비밀번호를 확인하고 바꾼다. 기존 토큰은 전부 무효가 되고, 응답의 새 토큰으로 현재 세션을 이어 간다.

    현재 비밀번호 오류는 422(CURRENT_PASSWORD_WRONG), 같은 사용자의 연속 오류가 PASSWORD_CHANGE_LOCK_FAILURES번이면 429.
    """
    key = user.owner_key
    rate_limit.check(rate_limit.password_change, key, "RATE_LIMIT_PASSWORD_CHANGE_PER_HOUR")
    rate_limit.check_password_change_lock(key)
    db = SessionLocal()
    try:
        updated = change_password(db, user, request.current_password, request.new_password)
    except TokenRevoked:
        raise unauthorized() from None
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    rate_limit.record_password_change(key, success=updated is not None)
    if updated is None:
        raise HTTPException(status_code=422, detail=CURRENT_PASSWORD_WRONG)
    return _auth_response(updated)
