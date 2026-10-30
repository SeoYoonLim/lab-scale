"""회원가입/로그인/내 정보/로그아웃/비밀번호 변경 + 계정 복구(이메일 등록·인증, 아이디 찾기, 비밀번호 재설정, 2026-10-30).

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
- 계정 복구(2026-10-30, 상세는 API.md "계정 복구"): 복구 메일은 인증된 이메일에만 보낸다. 아이디 찾기·비밀번호 찾기는 이메일이
  있든 없든 인증 전이든 같은 200 + 같은 본문이고, 조회·토큰 발급·발송은 응답 뒤 BackgroundTasks에서 한다(app.account_recovery_db.deliver_*).
  토큰은 원문 대신 SHA-256만 저장하고, 잘못됐거나 만료됐거나 이미 쓴 토큰은 모두 같은 400이다.
"""

import logging

import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.exc import DBAPIError

from app import account_recovery_db as recovery_db
from app import rate_limit
from app import security_log
from app.account_recovery import INVALID_LINK, RECOVERY_ACCEPTED, TOKEN_MAX_LEN, normalize_email
from app.account_recovery import PURPOSE_RESET as R_PURPOSE_RESET
from app.api.deps import _bearer_token, client_ip, get_current_user, unauthorized
from app.auth import (
    PASSWORD_MAX_LEN,
    USERNAME_MAX_LEN,
    CurrentUser,
    EmailTaken,
    PasswordNotSet,
    TokenRevoked,
    UsernameTaken,
    authenticate,
    change_password,
    create_access_token,
    create_user,
    hash_password,
    normalize_username,
    password_context_problem,
    token_issued_at,
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
EMAIL_TAKEN_DETAIL = "이미 사용 중인 이메일입니다."
SOCIAL_NO_PASSWORD_DETAIL = (
    "소셜 계정은 비밀번호가 없습니다. 인증된 이메일이 있으면 비밀번호 찾기로 새 비밀번호를 정할 수 있습니다."
)
NO_PASSWORD_DETAIL = (
    "비밀번호가 설정되지 않은 계정은 여기서 이메일을 바꿀 수 없습니다. 이 계정의 이메일은 로그인에 쓴 외부 계정(소셜 로그인)에서 관리됩니다."
)
EMAIL_INPUT_MAX_LEN = 320  # 정규화 전 입력 상한(앞뒤 공백 포함). 정규화 뒤에는 254자
RECENT_LOGIN_S = 600  # 비밀번호·인증 이메일이 모두 없는 소셜 계정의 탈퇴는 10분 안에 로그인한 토큰만 허용
DELETE_NEEDS_RECENT_LOGIN = (
    "이 계정은 비밀번호와 인증된 이메일이 없어 본인 확인을 위해 방금(10분 안) 다시 로그인한 뒤, 확인용으로 아이디를 함께 보내야 탈퇴할 수 있습니다."
)


class SignupRequest(BaseModel):
    username: str
    password: str
    email: str | None = Field(default=None, max_length=EMAIL_INPUT_MAX_LEN)  # 선택(2026-10-30). 없으면 이전과 같다

    @model_validator(mode="after")
    def _password_context(self):
        # 비밀번호에 아이디나 이메일 앞부분을 넣지 못하게 한다(2026-10-30). 요청 안의 값끼리만 비교한다.
        problem = password_context_problem(self.password, self.username, self.email)
        if problem:
            raise PydanticCustomError("password_context", problem)
        return self

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        return normalize_username(v)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password(v)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return None if v is None else normalize_email(v)


class EmailRequest(BaseModel):
    email: str = Field(max_length=EMAIL_INPUT_MAX_LEN)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)


class ChangeEmailRequest(BaseModel):
    """PUT /api/auth/email(2026-10-30 보강): 현재 비밀번호가 필수다(처음 등록할 때도). 탈취한 토큰만으로 복구 주소를 바꾸지 못하게."""

    email: str = Field(max_length=EMAIL_INPUT_MAX_LEN)
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LEN)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=TOKEN_MAX_LEN)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=TOKEN_MAX_LEN)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _new_password(cls, v: str) -> str:
        return validate_password(v)  # 가입과 같은 규칙


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


class DeleteAccountRequest(BaseModel):
    """DELETE /api/auth/me 본문. 비밀번호가 있는 계정은 current_password가 필수다(없으면 422).
    비밀번호도 인증 이메일도 없는 소셜 계정만 confirm_username(+ 10분 안의 로그인)을 쓴다."""

    current_password: str | None = Field(default=None, min_length=1, max_length=PASSWORD_MAX_LEN)
    confirm_username: str | None = Field(default=None, min_length=1, max_length=USERNAME_MAX_LEN * 5)


class AccountDeleteResponse(BaseModel):
    ok: bool = True
    status: str  # "deleted" | "confirmation_sent"
    message: str


class UserOut(BaseModel):
    id: int
    username: str


class MeOut(UserOut):
    """GET /api/auth/me: 기존 id·username에 이메일 상태만 더한다(본인에게는 마스킹하지 않는다). 가입·로그인 응답의 user는 그대로다."""

    email: str | None
    email_verified: bool


class MessageResponse(BaseModel):
    ok: bool = True
    message: str


class EmailStatus(BaseModel):
    email: str | None
    email_verified: bool
    verification_sent: bool
    message: str


class AuthResponse(BaseModel):
    user: UserOut
    access_token: str
    token_type: str = "bearer"


def password_problem_error(problem: str) -> list[dict]:
    """비밀번호에 아이디·이메일이 들어 있을 때의 422 detail(검증 오류와 같은 배열 형식, 입력값 없음)."""
    return [{"type": "password_context", "loc": ["body", "new_password"], "msg": problem}]


def _auth_response(user: CurrentUser) -> AuthResponse:
    return AuthResponse(
        user=UserOut(id=user.id, username=user.username),
        access_token=create_access_token(user.id, token_version=user.token_version),
    )


def _db_unavailable(e: DBAPIError) -> HTTPException:
    logger.error("auth DB 오류: %s", type(e).__name__)
    return HTTPException(status_code=503, detail="데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.")


@router.post("/signup", response_model=AuthResponse, status_code=201)
def signup(request: SignupRequest, http_request: Request, background: BackgroundTasks) -> AuthResponse:
    """가입하고 바로 로그인 상태(토큰)를 돌려준다. 아이디가 이미 있으면 409. IP당 가입 횟수를 넘으면 429.

    email(선택)을 주면 형식을 검사하고(422), 다른 계정이 쓰면 409, 저장한 뒤 인증 메일을 응답 뒤에 보낸다. 응답 형식은 같다.
    """
    rate_limit.check(rate_limit.signup_ip, client_ip(http_request), "RATE_LIMIT_SIGNUP_PER_HOUR")
    db = SessionLocal()
    raw = None
    try:
        if request.email is None:
            user = create_user(db, request.username, request.password)
        else:
            user = create_user(db, request.username, request.password, email=request.email)
            _, raw, _, _ = recovery_db.new_verification(db, user.id, recovery_db.now())
    except UsernameTaken:
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.") from None
    except EmailTaken:
        raise HTTPException(status_code=409, detail=EMAIL_TAKEN_DETAIL) from None
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    if raw:
        background.add_task(recovery_db.send_verification, request.email, user.username, raw)
    security_log.event("signup", user_id=user.id, ip=client_ip(http_request))
    return _auth_response(user)


@router.post("/login", response_model=AuthResponse)
def login(request: LoginRequest, http_request: Request) -> AuthResponse:
    ip = client_ip(http_request)
    rate_limit.check(rate_limit.login_ip, ip, "RATE_LIMIT_LOGIN_PER_MIN")
    lock_key = f"{request.username.strip().casefold()}|{ip}"
    try:
        rate_limit.check_login_lock(lock_key)
    except HTTPException:
        security_log.event("login_locked", result="locked", ip=ip)
        raise
    db = SessionLocal()
    try:
        user = authenticate(db, request.username, request.password)
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    rate_limit.record_login(lock_key, success=user is not None)
    if user is None:
        security_log.event("login", result="fail", ip=ip)  # 아이디는 남기지 않는다(비밀번호를 아이디 칸에 친 경우)
        raise HTTPException(status_code=401, detail=LOGIN_FAILED_DETAIL, headers={"WWW-Authenticate": "Bearer"})
    security_log.event("login", user_id=user.id, ip=ip)
    return _auth_response(user)


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser = Depends(get_current_user)) -> MeOut:
    db = SessionLocal()
    try:
        email, verified = recovery_db.account_email(db, user.id)
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    return MeOut(id=user.id, username=user.username, email=email, email_verified=verified)


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
    security_log.event("logout", user_id=user.id)
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
        email, _ = recovery_db.account_email(db, user.id)
        problem = password_context_problem(request.new_password, user.username, email)
        if problem:
            raise HTTPException(status_code=422, detail=password_problem_error(problem))
        updated = change_password(db, user, request.current_password, request.new_password)
    except PasswordNotSet:
        raise HTTPException(status_code=400, detail=SOCIAL_NO_PASSWORD_DETAIL) from None
    except TokenRevoked:
        raise unauthorized() from None
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    rate_limit.record_password_change(key, success=updated is not None)
    security_log.event("password_change", user_id=user.id, result="ok" if updated else "fail")
    if updated is None:
        raise HTTPException(status_code=422, detail=CURRENT_PASSWORD_WRONG)
    return _auth_response(updated)


# ---------- 계정 복구(2026-10-30) ----------


@router.put("/email", response_model=EmailStatus)
def put_email(request: ChangeEmailRequest, background: BackgroundTasks, user: CurrentUser = Depends(get_current_user)) -> EmailStatus:
    """이메일 등록·변경(현재 비밀번호 필수). 바꾸면 인증 상태를 비우고 남은 인증·재설정 링크를 무효로 하고 새 주소로 인증 메일을,
    이전 주소가 인증돼 있었으면 그 주소로 변경 알림을 보낸다. 지금과 같은 주소면 아무것도 바꾸지 않는다.

    현재 비밀번호 오류는 422(비밀번호 변경과 같은 CURRENT_PASSWORD_WRONG), 연속 오류는 비밀번호 변경과 같은 사용자 잠금(합산)으로 429.
    비밀번호가 없는 계정은 400. 다른 계정이 쓰는 주소면 409. 사용자당 인증 메일 한도(재발송과 합산)를 넘으면 429.
    """
    key = user.owner_key
    rate_limit.check(rate_limit.verification_mail, key, "RATE_LIMIT_VERIFICATION_MAIL_PER_HOUR")
    rate_limit.check_password_change_lock(key)
    db = SessionLocal()
    try:
        change = recovery_db.set_email(db, user, request.email, request.current_password, recovery_db.now())
        if change.status != "wrong_password":
            email, verified = recovery_db.account_email(db, user.id)
    except PasswordNotSet:
        raise HTTPException(status_code=400, detail=NO_PASSWORD_DETAIL) from None
    except EmailTaken:
        rate_limit.record_password_change(key, success=True)  # 비밀번호는 맞았다(유일 위반은 그 뒤에 난다)
        raise HTTPException(status_code=409, detail=EMAIL_TAKEN_DETAIL) from None
    except TokenRevoked:
        raise unauthorized() from None
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    rate_limit.record_password_change(key, success=change.status != "wrong_password")
    security_log.event("email_change", user_id=user.id, result={"wrong_password": "fail", "changed": "ok"}.get(change.status, "unchanged"))
    if change.status == "wrong_password":
        raise HTTPException(status_code=422, detail=CURRENT_PASSWORD_WRONG)
    if change.status == "changed":
        background.add_task(recovery_db.send_verification, request.email, change.username, change.raw_token)
        if change.previous_verified_email:
            background.add_task(recovery_db.send_email_changed_notice, change.previous_verified_email, change.username, request.email)
        message = "인증 메일을 보냈습니다. 메일의 링크를 열면 인증이 끝납니다."
    else:
        message = "이미 등록된 이메일입니다." + ("" if verified else " 인증 메일이 필요하면 재발송을 요청해주세요.")
    return EmailStatus(email=email, email_verified=verified, verification_sent=change.status == "changed", message=message)


@router.post("/resend-verification", response_model=MessageResponse)
def resend_verification(background: BackgroundTasks, user: CurrentUser = Depends(get_current_user)) -> MessageResponse:
    """인증 메일 재발송(이전 인증 링크는 무효). 이메일이 없거나 이미 인증됐으면 400(본인 계정 상태만 알린다)."""
    rate_limit.check(rate_limit.verification_mail, user.owner_key, "RATE_LIMIT_VERIFICATION_MAIL_PER_HOUR")
    db = SessionLocal()
    try:
        status, raw, email, username = recovery_db.new_verification(db, user.id, recovery_db.now())
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    if status == "no_email":
        raise HTTPException(status_code=400, detail="등록된 이메일이 없습니다. 먼저 이메일을 등록해주세요.")
    if status == "verified":
        raise HTTPException(status_code=400, detail="이미 인증된 이메일입니다.")
    background.add_task(recovery_db.send_verification, email, username, raw)
    return MessageResponse(message="인증 메일을 다시 보냈습니다. 이전에 받은 인증 링크는 더 이상 쓸 수 없습니다.")


@router.post("/verify-email", response_model=MessageResponse)
def verify_email(request: TokenRequest, http_request: Request) -> MessageResponse:
    """메일 링크의 토큰으로 이메일을 인증한다(공개). 잘못됐거나 만료됐거나 이미 쓴 토큰은 모두 같은 400."""
    rate_limit.check(rate_limit.token_ip, f"verify|{client_ip(http_request)}", "RATE_LIMIT_TOKEN_PER_MIN")
    db = SessionLocal()
    try:
        ok = recovery_db.verify_email(db, request.token, recovery_db.now())
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    security_log.event("email_verify", result="ok" if ok else "fail", ip=client_ip(http_request))
    if not ok:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    return MessageResponse(message="이메일 인증이 끝났습니다.")


def _recovery_limits(kind: str, email: str, http_request: Request) -> None:
    rate_limit.check(rate_limit.recovery_ip, f"{kind}|{client_ip(http_request)}", "RATE_LIMIT_RECOVERY_PER_MIN")
    rate_limit.check(rate_limit.recovery_email, f"{kind}|{email}", "RATE_LIMIT_RECOVERY_EMAIL_PER_HOUR")


@router.post("/find-username", response_model=MessageResponse)
def find_username(request: EmailRequest, http_request: Request, background: BackgroundTasks) -> MessageResponse:
    """아이디 찾기(공개). 응답에는 아이디를 절대 넣지 않는다. 인증된 이메일이면 응답 뒤에 아이디를 메일로 보낸다.
    이메일이 없든 인증 전이든 같은 200 + 같은 본문이다(사용자 열거 방지)."""
    _recovery_limits("find_username", request.email, http_request)
    background.add_task(recovery_db.deliver_username, request.email)
    return MessageResponse(message=RECOVERY_ACCEPTED)


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(request: EmailRequest, http_request: Request, background: BackgroundTasks) -> MessageResponse:
    """비밀번호 찾기(공개). 인증된 이메일이면 응답 뒤에 재설정 링크(30분, 1회용)를 보낸다. 응답은 find-username과 같다."""
    _recovery_limits("forgot_password", request.email, http_request)
    background.add_task(recovery_db.deliver_reset, request.email)
    return MessageResponse(message=RECOVERY_ACCEPTED)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(request: ResetPasswordRequest, http_request: Request) -> MessageResponse:
    """재설정 토큰으로 비밀번호를 바꾼다(공개). 성공하면 기존 토큰(JWT)이 모든 기기에서 무효가 되고, 남은 재설정 링크도 무효,
    로그인 실패 잠금이 풀린다. 새 비밀번호 규칙 위반은 422, 잘못됐거나 만료됐거나 이미 쓴 토큰은 400. 로그인 토큰은 주지 않는다."""
    rate_limit.check(rate_limit.token_ip, f"reset|{client_ip(http_request)}", "RATE_LIMIT_TOKEN_PER_MIN")
    new_hash = hash_password(request.new_password)  # 토큰이 틀려도 같은 만큼 시간을 쓴다
    db = SessionLocal()
    try:
        at = recovery_db.now()
        owner = recovery_db.token_owner(db, request.token, R_PURPOSE_RESET, at)  # 토큰은 아직 쓰지 않는다
        problem = password_context_problem(request.new_password, *owner) if owner else None
        if problem:  # 422면 토큰이 그대로 남아 다른 비밀번호로 다시 시도할 수 있다
            raise HTTPException(status_code=422, detail=password_problem_error(problem))
        user = recovery_db.reset_password(db, request.token, new_hash, at)
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    security_log.event("password_reset", user_id=user.id if user else None, result="ok" if user else "fail",
                       ip=client_ip(http_request))
    if user is None:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    rate_limit.clear_locks_after_password_reset(user.username, user.owner_key)
    return MessageResponse(message="비밀번호를 바꿨습니다. 모든 기기에서 로그아웃됐으니 새 비밀번호로 로그인해주세요.")


# ---------- 회원 탈퇴(2026-10-30) ----------


@router.delete("/me", response_model=AccountDeleteResponse)
def delete_me(
    request: DeleteAccountRequest, http_request: Request, background: BackgroundTasks, user: CurrentUser = Depends(get_current_user)
) -> AccountDeleteResponse:
    """회원 탈퇴: 그 사용자의 모든 데이터를 지운다(되돌릴 수 없음). 본인 확인 방식은 계정 종류에 따라 다르다.

    - 비밀번호가 있는 계정: current_password 필수(없으면 422, 틀리면 비밀번호 변경과 같은 422, 연속 오류 잠금 공유). 맞으면 바로 삭제 200.
    - 비밀번호가 없고 인증된 이메일이 있는 소셜 계정: 그 주소로 30분 1회용 확인 링크를 보내고 202(status="confirmation_sent").
      링크(프론트 /delete-account#token=...)에서 POST /api/auth/delete-account/confirm으로 확정한다(재설정과 같은 메일 소유 확인).
    - 비밀번호도 인증 이메일도 없는 소셜 계정: 10분 안에 로그인해 받은 토큰 + confirm_username(아이디 일치)일 때만 삭제(아니면 400).
    사용자당 시간당 RATE_LIMIT_ACCOUNT_DELETE_PER_HOUR회.
    """
    key = user.owner_key
    rate_limit.check(rate_limit.account_delete, key, "RATE_LIMIT_ACCOUNT_DELETE_PER_HOUR")
    rate_limit.check_password_change_lock(key)
    ip = client_ip(http_request)
    db = SessionLocal()
    try:
        has_password, email, email_verified, username = recovery_db.account_state(db, user.id)
        db.rollback()
        if username is None:
            raise unauthorized()
        if has_password:
            if request.current_password is None:
                raise HTTPException(status_code=422, detail=[
                    {"type": "missing", "loc": ["body", "current_password"], "msg": "Field required"}])
            ok = recovery_db.password_matches(db, user.id, request.current_password)
            rate_limit.record_password_change(key, success=ok)
            if not ok:
                security_log.event("account_delete", user_id=user.id, result="fail", ip=ip)
                raise HTTPException(status_code=422, detail=CURRENT_PASSWORD_WRONG)
            recovery_db.delete_user_data(db, user.id)
        elif email and email_verified:
            raw = recovery_db.request_delete_mail(db, user.id, email, username, recovery_db.now())
            background.add_task(recovery_db.send_delete_confirmation, email, username, raw)
            security_log.event("account_delete_requested", user_id=user.id, ip=ip)
            return JSONResponse(status_code=202, content=AccountDeleteResponse(
                status="confirmation_sent",
                message="등록된 이메일로 탈퇴 확인 링크를 보냈습니다. 30분 안에 링크에서 확인하면 계정과 모든 데이터가 지워집니다.").model_dump())
        else:
            iat = token_issued_at(_bearer_token(http_request.headers.get("authorization")))
            recent = iat is not None and time.time() - iat <= RECENT_LOGIN_S
            if not recent or (request.confirm_username or "").strip().lower() != username:
                security_log.event("account_delete", user_id=user.id, result="fail", ip=ip)
                raise HTTPException(status_code=400, detail=DELETE_NEEDS_RECENT_LOGIN)
            recovery_db.delete_user_data(db, user.id)
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    security_log.event("account_delete", user_id=user.id, ip=ip)
    return AccountDeleteResponse(status="deleted", message="회원 탈퇴가 끝났습니다. 계정과 모든 데이터를 지웠습니다.")


@router.post("/delete-account/confirm", response_model=AccountDeleteResponse)
def confirm_delete_account(request: TokenRequest, http_request: Request) -> AccountDeleteResponse:
    """탈퇴 확인 메일 링크의 토큰으로 탈퇴를 확정한다(공개: 링크를 연 브라우저는 로그인돼 있지 않을 수 있다). 토큰 오류는 같은 400."""
    ip = client_ip(http_request)
    rate_limit.check(rate_limit.token_ip, f"delete|{ip}", "RATE_LIMIT_TOKEN_PER_MIN")
    db = SessionLocal()
    try:
        user_id = recovery_db.confirm_delete(db, request.token, recovery_db.now())
    except DBAPIError as e:
        raise _db_unavailable(e) from None
    finally:
        db.close()
    security_log.event("account_delete", user_id=user_id, result="ok" if user_id else "fail", ip=ip)
    if user_id is None:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    return AccountDeleteResponse(status="deleted", message="회원 탈퇴가 끝났습니다. 계정과 모든 데이터를 지웠습니다.")
