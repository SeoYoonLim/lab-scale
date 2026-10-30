"""계정 복구의 DB 계층과 메일 발송 작업. 순수 로직은 app/account_recovery.py.

- 토큰 사용은 조건부 UPDATE 한 문장이다: `UPDATE account_token SET used_at=now WHERE token_hash=? AND purpose=? AND used_at IS NULL
  AND expires_at > now RETURNING user_id`. 같은 토큰으로 동시에 두 요청이 와도 두 번째는 첫 번째의 행 잠금을 기다린 뒤 조건을 다시
  평가해(PostgreSQL READ COMMITTED) 0행이 되므로 한 번만 성공한다.
- 같은 목적의 새 토큰을 발급하면 그 사용자의 남은 미사용 토큰에 used_at을 채워 무효로 한다(같은 트랜잭션).
- 복구 메일(아이디 찾기·비밀번호 재설정)은 인증된 이메일에만 보낸다. 조회·토큰 발급·발송은 모두 응답 뒤의 백그라운드 작업에서 한다
  (deliver_*): 이메일 존재 여부에 따라 응답 시간·본문이 달라지지 않게 하려는 것이다. 작업 안의 실패는 로그(마스킹)만 남기고 삼킨다.
- 시계는 now()를 통해서만 읽는다(테스트가 monkeypatch로 바꿔 만료를 검증한다).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app import account_recovery as R
from app import mailer
from app.auth import CurrentUser, EmailTaken, is_email_conflict
from app.db.session import SessionLocal
from app.models import AccountToken, User

logger = logging.getLogger(__name__)

# EmailTaken은 가입도 쓰므로 app.auth에 있다(set_email이 같은 예외를 올린다).
EmailTaken = EmailTaken


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- 토큰 ----------


def issue_token(db, user_id: int, purpose: str, at: datetime) -> str:
    """같은 목적의 미사용 토큰을 무효로 하고 새 토큰을 넣는다(commit은 호출 쪽). 원문을 돌려준다."""
    invalidate_tokens(db, user_id, purpose, at)
    raw, digest = R.new_token()
    db.add(AccountToken(user_id=user_id, purpose=purpose, token_hash=digest, expires_at=R.expires_at(purpose, at)))
    db.flush()
    return raw


def invalidate_tokens(db, user_id: int, purpose: str, at: datetime) -> None:
    db.execute(
        update(AccountToken)
        .where(AccountToken.user_id == user_id, AccountToken.purpose == purpose, AccountToken.used_at.is_(None))
        .values(used_at=at)
    )


def consume_token(db, raw: str, purpose: str, at: datetime) -> int | None:
    """토큰을 1회 사용 처리하고 user_id를 돌려준다. 없거나·다른 목적이거나·만료됐거나·이미 썼으면 None."""
    return db.execute(
        update(AccountToken)
        .where(AccountToken.token_hash == R.hash_token(raw), AccountToken.purpose == purpose,
               AccountToken.used_at.is_(None), AccountToken.expires_at > at)
        .values(used_at=at)
        .returning(AccountToken.user_id)
    ).scalar_one_or_none()


# ---------- 이메일 등록·인증 ----------


def account_email(db, user_id: int) -> tuple[str | None, bool]:
    row = db.execute(select(User.email, User.email_verified_at).where(User.id == user_id)).one_or_none()
    if row is None:
        return None, False
    return row.email, row.email_verified_at is not None


def set_email(db, user_id: int, email: str, at: datetime) -> tuple[str | None, str | None]:
    """이메일을 등록·변경한다. (인증 메일용 원문 토큰, username). 지금 이메일과 같으면 바꾸지 않고 (None, username).

    바꾸면 email_verified_at을 비우고, 옛 이메일로 보낸 인증 토큰은 무효가 된다(issue_token). 다른 계정이 쓰는 이메일이면 EmailTaken.
    사용자가 없으면 LookupError.
    """
    row = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if row is None:
        db.rollback()
        raise LookupError()
    if row.email == email:
        db.rollback()
        return None, row.username
    row.email, row.email_verified_at = email, None
    try:
        db.flush()
        raw = issue_token(db, user_id, R.PURPOSE_VERIFY_EMAIL, at)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        if is_email_conflict(e):
            raise EmailTaken() from None
        raise RuntimeError("account email integrity error") from None
    return raw, row.username


def new_verification(db, user_id: int, at: datetime) -> tuple[str, str | None, str | None, str | None]:
    """인증 메일 재발송. ("sent"|"no_email"|"verified", 원문 토큰, email, username)."""
    row = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if row is None or row.email is None:
        db.rollback()
        return "no_email", None, None, None
    if row.email_verified_at is not None:
        db.rollback()
        return "verified", None, row.email, row.username
    raw = issue_token(db, user_id, R.PURPOSE_VERIFY_EMAIL, at)
    db.commit()
    return "sent", raw, row.email, row.username


def verify_email(db, raw: str, at: datetime) -> bool:
    user_id = consume_token(db, raw, R.PURPOSE_VERIFY_EMAIL, at)
    if user_id is None:
        db.rollback()
        return False
    n = db.execute(
        update(User).where(User.id == user_id, User.email.is_not(None)).values(email_verified_at=at)
    ).rowcount
    db.commit()
    return n == 1


# ---------- 비밀번호 재설정 ----------


def reset_password(db, raw: str, new_password_hash: str, at: datetime) -> CurrentUser | None:
    """토큰을 쓰고 해시를 바꾸고 token_version을 올려 기존 JWT를 전부 폐기한다. 그 사용자의 남은 재설정 토큰도 무효로 한다.
    한 트랜잭션이다. 토큰이 맞지 않으면 None(아무것도 바꾸지 않음)."""
    user_id = consume_token(db, raw, R.PURPOSE_RESET_PASSWORD, at)
    if user_id is None:
        db.rollback()
        return None
    row = db.execute(
        update(User).where(User.id == user_id)
        .values(password_hash=new_password_hash, token_version=User.token_version + 1)
        .returning(User.id, User.username, User.token_version)
    ).one_or_none()
    if row is None:
        db.rollback()
        return None
    invalidate_tokens(db, user_id, R.PURPOSE_RESET_PASSWORD, at)
    db.commit()
    return CurrentUser(id=row.id, username=row.username, token_version=row.token_version)


def verified_account(db, email: str) -> tuple[int, str] | None:
    """인증된 이메일의 (user id, username). 없거나 인증 전이면 None."""
    row = db.execute(
        select(User.id, User.username).where(User.email == email, User.email_verified_at.is_not(None))
    ).one_or_none()
    return (row.id, row.username) if row else None


# ---------- 메일 발송 작업(응답 뒤 BackgroundTasks) ----------


def _with_session(job, kind: str, email: str) -> None:
    db = SessionLocal()
    try:
        job(db)
    except Exception as e:  # DB 오류 포함, 백그라운드 작업은 어떤 예외도 밖으로 내지 않는다(원문은 남기지 않는다)
        db.rollback()
        logger.warning("계정 복구 작업 실패: kind=%s to=%s error=%s", kind, R.mask_email(email), type(e).__name__)
    finally:
        db.close()


def deliver_username(email: str) -> None:
    def job(db):
        found = verified_account(db, email)
        db.rollback()
        if found:
            mailer.send_safely(R.username_mail(email, found[1]))
    _with_session(job, "find_username", email)


def deliver_reset(email: str) -> None:
    def job(db):
        found = verified_account(db, email)
        if not found:
            db.rollback()
            return
        raw = issue_token(db, found[0], R.PURPOSE_RESET_PASSWORD, now())
        db.commit()
        mailer.send_safely(R.reset_mail(email, found[1], raw))
    _with_session(job, "forgot_password", email)


def send_verification(email: str, username: str, raw: str) -> None:
    """이미 발급한 인증 토큰을 담아 보낸다(가입·이메일 변경·재발송)."""
    mailer.send_safely(R.verify_mail(email, username, raw))
