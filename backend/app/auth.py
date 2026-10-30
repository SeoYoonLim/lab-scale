"""아이디/비밀번호 로그인: 비밀번호 해시, JWT 발급/검증, 사용자 저장/조회.

- 비밀번호는 argon2id(argon2-cffi 기본 파라미터)로 해시한다. 평문과 해시는 응답/로그/예외 메시지에 넣지 않는다.
- 토큰은 HS256 JWT(claims: sub=user id 문자열, iat, exp, tv). 만료 24시간. 검증 시 알고리즘을 HS256으로 고정해
  alg=none이나 다른 알고리즘으로 바꿔치기한 토큰을 거부한다.
- 토큰 폐기(2026-10-29): tv는 발급 시점의 users.token_version이다. 로그아웃·비밀번호 변경이 token_version을 1 올리면 그 전에
  발급된 토큰은 전부 tv가 달라져 401이 된다(get_current_user가 비교). tv가 없는 옛 토큰은 tv=0으로 본다. 모든 사용자의
  token_version이 0에서 시작하므로 배포 직후에도 옛 토큰이 그대로 통하고, 그 사용자의 첫 폐기 때 함께 무효가 된다.
- 서명 키(JWT_SECRET)는 .env(환경 변수)에서만 읽는다. 없거나 MIN_SECRET_LEN자 미만이면 RuntimeError.
  앱 시작 시(app.main lifespan)에 한 번 검사하고, 토큰을 만들거나 검증할 때마다 다시 읽는다(빈 키로 서명되는 일이 없게).
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from dotenv import load_dotenv
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models import User

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = timedelta(hours=24)
MIN_SECRET_LEN = 32

USERNAME_MIN_LEN = 3
USERNAME_MAX_LEN = 20
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 128
_USERNAME_RE = re.compile(r"[a-z0-9_]+", re.ASCII)

_hasher = PasswordHasher()


class InvalidToken(Exception):
    """토큰이 없거나/형식이 틀렸거나/서명·만료 검증에 실패했다. 메시지에 토큰 값은 넣지 않는다."""


class UsernameTaken(Exception):
    pass


class EmailTaken(Exception):
    """다른 계정이 이미 쓰는 이메일(대소문자 무시, users의 uq_users_email_lower 인덱스)."""


EMAIL_UNIQUE_INDEX = "uq_users_email_lower"


def is_email_conflict(e: IntegrityError) -> bool:
    """IntegrityError가 이메일 유일 인덱스 위반인지(예외 원문은 로그에 남기지 않는다)."""
    diag = getattr(getattr(e, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None) == EMAIL_UNIQUE_INDEX


class TokenRevoked(Exception):
    """비밀번호를 바꾸는 사이에 같은 사용자의 토큰이 폐기됐다(다른 기기의 로그아웃 등)."""


class PasswordNotSet(Exception):
    """비밀번호가 없는 계정(password_hash가 비어 있음). 지금은 users.password_hash가 NOT NULL이라 생기지 않지만,
    소셜 로그인처럼 비밀번호 없이 만든 계정이 생기면 "현재 비밀번호 확인"을 할 수 없으므로 그 확인이 필요한 동작을 거부한다."""


def check_current_password(password_hash: str | None, password: str) -> bool:
    """현재 비밀번호 확인(DB 없음). 맞으면 True, 틀리면 False. 해시가 없으면 PasswordNotSet(빈 해시를 통과시키지 않는다)."""
    if not password_hash:
        raise PasswordNotSet()
    return verify_password(password, password_hash)


@dataclass(frozen=True)
class CurrentUser:
    """인증된 요청의 사용자. ORM 객체 대신 이걸 넘겨 세션 수명과 password_hash 노출을 신경 쓰지 않게 한다."""

    id: int
    username: str
    token_version: int = 0  # 토큰을 만들 때 tv로 넣고, 검증할 때 토큰의 tv와 비교한다

    @property
    def owner_key(self) -> str:
        """watchlist/virtual_account/holding/trade의 device_id 컬럼에 저장하는 소유자 키."""
        return f"user:{self.id}"


# ---------- 설정 ----------


def get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET 환경 변수가 설정되지 않았습니다. backend/.env에 JWT_SECRET을 추가하세요.")
    if len(secret) < MIN_SECRET_LEN:
        raise RuntimeError(f"JWT_SECRET은 {MIN_SECRET_LEN}자 이상이어야 합니다(현재 {len(secret)}자).")
    if len(secret.encode("utf-8")) < MIN_SECRET_LEN:
        raise RuntimeError(f"JWT_SECRET은 {MIN_SECRET_LEN}바이트 이상이어야 합니다.")
    stripped = secret.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        # .env.example의 자리표시(<...로 교체>)를 그대로 복사한 값. 모든 모드에서 거부한다(2026-10-30).
        raise RuntimeError("JWT_SECRET이 .env.example의 예시 자리표시 그대로입니다. secrets.token_urlsafe(48)로 새로 만드세요.")
    return secret


# ---------- 입력 규칙 ----------


def normalize_username(username: str) -> str:
    """소문자로 정규화하고 규칙(3~20자, [a-z0-9_])을 검사한다. 어긋나면 ValueError(메시지에 입력값은 넣지 않음)."""
    v = username.lower()
    if not USERNAME_MIN_LEN <= len(v) <= USERNAME_MAX_LEN:
        raise ValueError(f"아이디는 {USERNAME_MIN_LEN}~{USERNAME_MAX_LEN}자여야 합니다.")
    if not _USERNAME_RE.fullmatch(v):
        raise ValueError("아이디는 영문 소문자, 숫자, 밑줄(_)만 쓸 수 있습니다.")
    return v


# 흔한 비밀번호(작은 내장 목록, 2026-10-30). 소문자로 비교한다. 유출 목록 상위와 한국에서 흔한 키보드 패턴 위주.
_COMMON_CHOICES = frozenset("""
password password1 password12 password123 passw0rd p@ssw0rd p@ssword 12345678 123456789 1234567890 0123456789 87654321
11111111 00000000 88888888 99999999 12341234 11223344 123123123 qwerty123 qwertyui qwertyuiop qwer1234 qwer1234!
1q2w3e4r 1q2w3e4r! 1q2w3e4r5t 1qaz2wsx 1qaz2wsx3edc q1w2e3r4 q1w2e3r4t5 asdf1234 asdfghjk asdfasdf zxcv1234 zxcvbnm1
a1234567 a12345678 abc12345 abcd1234 abcd1234! abcdefgh aaaaaaaa iloveyou iloveyou1 sunshine princess football
baseball welcome1 welcome123 letmein1 admin123 admin1234 administrator changeme changeme1 trustno1 dragon12 monkey12
superman batman123 starwars computer internet samsung1 samsung123 korea123 seoul123 love1234 test1234 testtest guest123
""".split())


def validate_password(password: str) -> str:
    """가입·비밀번호 변경·재설정이 같이 쓰는 규칙: 8~128자, 흔한 비밀번호 거부, 한 문자 반복 거부. 메시지에 입력값을 넣지 않는다.
    아이디·이메일이 들어 있는지는 그 값을 아는 곳에서 password_context_problem으로 따로 본다."""
    if not PASSWORD_MIN_LEN <= len(password) <= PASSWORD_MAX_LEN:
        raise ValueError(f"비밀번호는 {PASSWORD_MIN_LEN}~{PASSWORD_MAX_LEN}자여야 합니다.")
    if password.lower() in _COMMON_CHOICES or len(set(password)) == 1:
        raise ValueError("너무 흔한 비밀번호입니다. 다른 비밀번호를 정해주세요.")
    return password


def password_context_problem(password: str, username: str | None = None, email: str | None = None) -> str | None:
    """비밀번호에 아이디나 이메일 앞부분(3자 이상)이 들어 있으면 이유, 아니면 None(대소문자 무시)."""
    low = password.lower()
    if username and len(username) >= 3 and username.lower() in low:
        return "비밀번호에 아이디를 넣을 수 없습니다."
    local = (email or "").split("@")[0].lower()
    if len(local) >= 3 and local in low:
        return "비밀번호에 이메일 주소 앞부분을 넣을 수 없습니다."
    return None


# ---------- 비밀번호 해시 ----------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """일치하면 True. 불일치/손상된 해시/해시 없음(NULL, 소셜 계정) 등 어떤 실패든 False(예외를 밖으로 내지 않는다).
    해시가 없을 때는 더미 해시로 검증을 한 번 해서 응답 시간으로 "비밀번호 없는 계정"이 드러나지 않게 한다."""
    if not password_hash:
        try:
            _hasher.verify(_dummy_hash(), password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


@cache
def _dummy_hash() -> str:
    # 없는 아이디로 로그인할 때도 해시 검증 1회만큼 시간을 쓰게 하는 용도. 값 자체는 의미 없다.
    return _hasher.hash("dummy-password-for-constant-time-login")


# ---------- 토큰 ----------


def create_access_token(user_id: int, now: datetime | None = None, token_version: int = 0) -> str:
    now = now or datetime.now(timezone.utc)
    claims = {"sub": str(user_id), "iat": now, "exp": now + ACCESS_TOKEN_TTL, "tv": token_version}
    return jwt.encode(claims, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token_claims(token: str) -> tuple[int, int]:
    """검증된 토큰의 (user id, tv). 서명/만료/형식/필수 claim 중 하나라도 어긋나면 InvalidToken.

    tv가 없으면(폐기 기능 이전에 발급된 토큰) 0. 있는데 0 이상의 정수가 아니면 InvalidToken.
    """
    try:
        claims = jwt.decode(
            token,
            get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat", "tv"]},
        )
        user_id = int(claims["sub"])
    except (jwt.PyJWTError, ValueError, TypeError) as e:
        raise InvalidToken(type(e).__name__) from None
    # tv는 필수다(2026-10-30). tv 없이 발급된 옛 토큰은 수명(24시간)이 지나 남아 있지 않다.
    tv = claims.get("tv")
    if type(tv) is not int or tv < 0:  # bool(True == 1)이나 문자열 "1"은 받지 않는다
        raise InvalidToken("tv")
    return user_id, tv


def token_issued_at(token: str) -> int | None:
    """검증된 토큰의 iat(초). 탈퇴처럼 "최근 로그인"을 요구하는 동작이 쓴다. 검증 실패면 None."""
    try:
        decode_token_claims(token)
        claims = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except (InvalidToken, jwt.PyJWTError):
        return None
    iat = claims.get("iat")
    return int(iat) if isinstance(iat, (int, float)) else None


def decode_access_token(token: str) -> int:
    """검증된 토큰의 user id(tv 비교는 get_current_user가 한다)."""
    return decode_token_claims(token)[0]


# ---------- 사용자 저장/조회 ----------


def create_user(db, username: str, password: str, email: str | None = None) -> CurrentUser:
    """username은 normalize_username, email은 app.account_recovery.normalize_email을 거친 값이어야 한다.
    아이디가 이미 있으면 UsernameTaken, 이메일이 이미 있으면 EmailTaken(email_verified_at은 비운 채로 만든다)."""
    user = User(username=username, password_hash=hash_password(password), email=email)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as e:
        # 예외 원문에는 INSERT 파라미터(해시 포함)가 들어 있으므로 체인을 끊고 버린다.
        db.rollback()
        if email is not None and is_email_conflict(e):
            raise EmailTaken() from None
        raise UsernameTaken() from None
    return _current(user)


def authenticate(db, username: str, password: str) -> CurrentUser | None:
    """아이디/비밀번호가 맞으면 사용자, 아니면 None.

    아이디가 없을 때도 더미 해시로 검증을 한 번 수행해, 응답 시간 차이로 아이디 존재 여부가 드러나지 않게 한다.
    규칙에 안 맞는 아이디도 같은 경로(조회 실패 → 더미 검증)로 처리한다.
    """
    try:
        username = normalize_username(username)
    except ValueError:
        user = None
    else:
        user = db.query(User).filter(User.username == username).one_or_none()

    if user is None:
        verify_password(password, _dummy_hash())
        return None
    # 비밀번호 없는 계정(소셜 로그인, password_hash NULL)은 verify_password가 더미 검증 후 False → 같은 401, 같은 시간
    if not verify_password(password, user.password_hash):
        return None
    _rehash_if_needed(db, user, password)
    return _current(user)


def _rehash_if_needed(db, user: User, password: str) -> None:
    """저장된 해시의 argon2 파라미터가 지금 기본값보다 약하면 맞은 비밀번호로 새로 해시한다(실패해도 로그인은 그대로)."""
    try:
        if _hasher.check_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            db.commit()
    except Exception:  # noqa: BLE001 - 재해시는 최선 노력. 원문(해시 포함)은 남기지 않는다
        db.rollback()


def _current(user: User) -> CurrentUser:
    return CurrentUser(id=user.id, username=user.username, token_version=user.token_version)


def get_user(db, user_id: int) -> CurrentUser | None:
    user = db.get(User, user_id)
    return _current(user) if user else None


# ---------- 토큰 폐기 ----------


def revoke_tokens(db, user_id: int) -> int | None:
    """token_version을 1 올려 이 사용자의 기존 토큰을 전부 무효로 만든다. 새 token_version(사용자가 없으면 None)."""
    new_version = db.execute(
        update(User).where(User.id == user_id).values(token_version=User.token_version + 1).returning(User.token_version)
    ).scalar_one_or_none()
    db.commit()
    return new_version


def change_password(db, user: CurrentUser, current_password: str, new_password: str) -> CurrentUser | None:
    """현재 비밀번호가 맞으면 해시를 바꾸고 token_version을 올린 사용자를 돌려준다. 틀리면 None(아무것도 바꾸지 않음).

    new_password는 validate_password를 거친 값이어야 한다. 행을 FOR UPDATE로 잠그고, 그 사이 token_version이 요청 토큰의
    것과 달라졌으면(다른 곳에서 로그아웃) TokenRevoked. 사용자가 지워졌으면 TokenRevoked.
    비밀번호가 없는 계정(소셜 로그인)이면 PasswordNotSet(라우트가 400으로 알린다. 비밀번호 찾기로 새로 정할 수 있다).
    """
    row = db.execute(select(User).where(User.id == user.id).with_for_update()).scalar_one_or_none()
    if row is None or row.token_version != user.token_version:
        db.rollback()
        raise TokenRevoked()
    try:
        ok = check_current_password(row.password_hash, current_password)
    except PasswordNotSet:
        db.rollback()
        raise
    if not ok:
        db.rollback()
        return None
    row.password_hash = hash_password(new_password)
    row.token_version += 1
    db.commit()
    return _current(row)
