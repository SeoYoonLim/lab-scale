"""계정 복구(이메일 인증, 아이디 찾기, 비밀번호 재설정)의 순수 로직: 토큰, 이메일 정규화, 메일 본문(DB·네트워크 없음).

보안 규칙
- 토큰 원문은 secrets.token_urlsafe(32)(256비트)이고 DB에는 SHA-256 hex만 저장한다. 원문은 메일 링크에만 있다.
  해시라서 DB가 새도 원문으로 되돌릴 수 없고, 원문이 높은 엔트로피라 솔트·느린 해시가 필요 없다(비밀번호와 다름).
- 링크의 토큰은 쿼리가 아니라 fragment(#token=...)에 넣는다. fragment는 브라우저가 서버로 보내지 않으므로
  프론트 서버의 접근 로그나 다른 사이트로 가는 Referer에 남지 않는다. 프론트가 읽어서 POST 본문으로 보낸다.
- 만료: 이메일 인증 24시간, 비밀번호 재설정 30분. 1회용(used_at). 만료·사용 판정은 now를 받아서 한다(시계 주입).
- 이메일은 앞뒤 공백을 빼고 소문자로 저장한다. 형식 검사는 보수적인 ASCII 규칙이다(국제화 도메인·따옴표 로컬파트는 받지 않는다).
  오류 메시지에 입력값을 넣지 않는다.
"""

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

PURPOSE_VERIFY_EMAIL = "verify_email"
PURPOSE_RESET = "reset_password"
TOKEN_TTL = {PURPOSE_VERIFY_EMAIL: timedelta(hours=24), PURPOSE_RESET: timedelta(minutes=30)}
TOKEN_BYTES = 32
TOKEN_MAX_LEN = 128  # 요청 본문의 토큰 길이 상한(token_urlsafe(32)는 43자)
EMAIL_MAX_LEN = 254
EMAIL_LOCAL_MAX_LEN = 64
DEFAULT_FRONTEND_BASE_URL = "http://localhost:5173"
LINK_PATHS = {PURPOSE_VERIFY_EMAIL: "/verify-email", PURPOSE_RESET: "/reset-password"}

_LOCAL_RE = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*", re.ASCII)
_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)

# ---------- 응답 문구(열거 방지: 이메일이 있든 없든 같은 문구) ----------

RECOVERY_ACCEPTED = (
    "입력한 이메일이 인증된 계정의 이메일이면 안내 메일을 보냈습니다. "
    "몇 분 안에 받지 못했다면 스팸함을 확인하거나, 로그인한 뒤 이메일 등록·인증 상태를 확인해주세요."
)
INVALID_LINK = "링크가 올바르지 않거나 만료됐습니다. 메일을 다시 요청해주세요."


# ---------- 토큰 ----------


def new_token() -> tuple[str, str]:
    """(원문, SHA-256 hex). 원문은 메일 링크에만 쓰고 저장하지 않는다."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def expires_at(purpose: str, now: datetime) -> datetime:
    return now + TOKEN_TTL[purpose]


def is_usable(purpose: str, expected_purpose: str, expires: datetime, used: datetime | None, now: datetime) -> bool:
    """목적이 맞고, 아직 안 썼고, 만료 전(expires > now)이면 True."""
    return purpose == expected_purpose and used is None and expires > now


# ---------- 이메일 ----------


def normalize_email(raw: str) -> str:
    """앞뒤 공백 제거 + 소문자. 형식이 틀리면 ValueError(메시지에 입력값을 넣지 않는다)."""
    if not isinstance(raw, str):
        raise ValueError("이메일 형식이 올바르지 않습니다.")
    v = raw.strip().lower()
    if not v or len(v) > EMAIL_MAX_LEN:
        raise ValueError(f"이메일은 1~{EMAIL_MAX_LEN}자여야 합니다.")
    local, sep, domain = v.rpartition("@")
    labels = domain.split(".")
    ok = (
        sep == "@" and 0 < len(local) <= EMAIL_LOCAL_MAX_LEN and _LOCAL_RE.fullmatch(local) is not None
        and len(labels) >= 2 and all(_LABEL_RE.fullmatch(lb) for lb in labels) and not labels[-1].isdigit()
    )
    if not ok:
        raise ValueError("이메일 형식이 올바르지 않습니다.")
    return v


def mask_email(email: str | None) -> str:
    """로그·콘솔용: ab***@ex***.com. 비어 있으면 "-"."""
    if not email or "@" not in email:
        return "-"
    local, _, domain = email.rpartition("@")
    host, dot, tld = domain.rpartition(".")
    host = host or domain
    return f"{local[:2]}***@{host[:2]}***{dot}{tld if dot else ''}"


# ---------- 링크와 메일 본문 ----------


def frontend_base_url() -> str:
    return (os.getenv("FRONTEND_BASE_URL") or DEFAULT_FRONTEND_BASE_URL).strip().rstrip("/")


def link(purpose: str, raw_token: str, base_url: str | None = None) -> str:
    """{FRONTEND_BASE_URL}/verify-email#token=... 또는 /reset-password#token=... (토큰은 fragment에만)."""
    return f"{(base_url or frontend_base_url()).rstrip('/')}{LINK_PATHS[purpose]}#token={raw_token}"


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    body: str


_IGNORE = "이 메일을 요청한 적이 없다면 무시하세요. 계정에는 아무 변화도 없습니다."


def verify_mail(to: str, username: str, raw_token: str, base_url: str | None = None) -> Mail:
    hours = int(TOKEN_TTL[PURPOSE_VERIFY_EMAIL].total_seconds() // 3600)
    return Mail(to, "[AI 투자 리서치] 이메일 인증", (
        f"안녕하세요, {username}님.\n\n아래 링크를 열면 이 이메일이 계정에 인증됩니다. 링크는 {hours}시간 동안 한 번만 쓸 수 있습니다.\n\n"
        f"{link(PURPOSE_VERIFY_EMAIL, raw_token, base_url)}\n\n{_IGNORE}\n"
    ))


def reset_mail(to: str, username: str, raw_token: str, base_url: str | None = None) -> Mail:
    minutes = int(TOKEN_TTL[PURPOSE_RESET].total_seconds() // 60)
    return Mail(to, "[AI 투자 리서치] 비밀번호 재설정", (
        f"안녕하세요, {username}님.\n\n비밀번호 재설정을 요청하셨습니다. 아래 링크에서 새 비밀번호를 정할 수 있습니다. "
        f"링크는 {minutes}분 동안 한 번만 쓸 수 있습니다. 재설정하면 모든 기기에서 로그아웃됩니다.\n\n"
        f"{link(PURPOSE_RESET, raw_token, base_url)}\n\n{_IGNORE}\n"
    ))


def username_mail(to: str, username: str) -> Mail:
    return Mail(to, "[AI 투자 리서치] 아이디 안내", (
        f"요청하신 아이디는 다음과 같습니다.\n\n아이디: {username}\n\n{_IGNORE}\n"
    ))


def email_changed_mail(to: str, username: str, new_email: str) -> Mail:
    """이전(인증된) 주소로 보내는 이메일 변경 알림. 링크·토큰을 넣지 않는다(피싱 메일과 구분되게, 이 메일로는 아무것도 할 수 없다).
    새 주소는 가려서 쓴다."""
    return Mail(to, "[AI 투자 리서치] 계정 이메일이 변경되었습니다", (
        f"안녕하세요, {username}님.\n\n계정의 이메일이 {mask_email(new_email)}(으)로 변경되었습니다. "
        "이제 아이디 찾기·비밀번호 재설정 메일은 새 주소로만 갑니다.\n\n"
        "본인이 변경했다면 이 메일은 무시하세요. 본인이 아니라면 바로 로그인해 비밀번호를 바꾸고 이메일을 되돌리세요. "
        "로그인할 수 없다면 서비스 관리자에게 문의하세요.\n"
    ))
