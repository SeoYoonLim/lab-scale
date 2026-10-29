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


class TokenRevoked(Exception):
    """비밀번호를 바꾸는 사이에 같은 사용자의 토큰이 폐기됐다(다른 기기의 로그아웃 등)."""


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


def validate_password(password: str) -> str:
    if not PASSWORD_MIN_LEN <= len(password) <= PASSWORD_MAX_LEN:
        raise ValueError(f"비밀번호는 {PASSWORD_MIN_LEN}~{PASSWORD_MAX_LEN}자여야 합니다.")
    return password


# ---------- 비밀번호 해시 ----------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """일치하면 True. 불일치/손상된 해시 등 어떤 실패든 False(예외를 밖으로 내지 않는다)."""
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
            options={"require": ["sub", "exp", "iat"]},
        )
        user_id = int(claims["sub"])
    except (jwt.PyJWTError, ValueError, TypeError) as e:
        raise InvalidToken(type(e).__name__) from None
    tv = claims.get("tv", 0)
    if type(tv) is not int or tv < 0:  # bool(True == 1)이나 문자열 "1"은 받지 않는다
        raise InvalidToken("tv")
    return user_id, tv


def decode_access_token(token: str) -> int:
    """검증된 토큰의 user id(tv 비교는 get_current_user가 한다)."""
    return decode_token_claims(token)[0]


# ---------- 사용자 저장/조회 ----------


def create_user(db, username: str, password: str) -> CurrentUser:
    """username은 normalize_username을 거친 값이어야 한다. 이미 있으면 UsernameTaken."""
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # 예외 원문에는 INSERT 파라미터(해시 포함)가 들어 있으므로 체인을 끊고 버린다.
        db.rollback()
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
    if not verify_password(password, user.password_hash):
        return None
    return _current(user)


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
    """
    row = db.execute(select(User).where(User.id == user.id).with_for_update()).scalar_one_or_none()
    if row is None or row.token_version != user.token_version:
        db.rollback()
        raise TokenRevoked()
    if not verify_password(current_password, row.password_hash):
        db.rollback()
        return None
    row.password_hash = hash_password(new_password)
    row.token_version += 1
    db.commit()
    return _current(row)
