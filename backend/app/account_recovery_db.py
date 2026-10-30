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
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app import account_recovery as R
from app import mailer
from app.auth import CurrentUser, EmailTaken, PasswordNotSet, TokenRevoked, check_current_password, is_email_conflict, verify_password
from app.db.session import SessionLocal
from app.models import AccountToken, User, VirtualAccount, Watchlist

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


@dataclass(frozen=True)
class EmailChange:
    status: str  # "changed" | "unchanged" | "wrong_password"
    raw_token: str | None = None  # 새 주소로 보낼 인증 토큰 원문(changed일 때만)
    username: str | None = None
    previous_verified_email: str | None = None  # 바뀌기 전 주소가 인증돼 있었으면 그 주소(변경 알림을 보낸다)


def set_email(db, user: CurrentUser, email: str, current_password: str, at: datetime) -> EmailChange:
    """현재 비밀번호를 확인하고 이메일을 등록·변경한다. 행을 FOR UPDATE로 잠근 채 확인·변경을 한 트랜잭션에서 한다.

    - 비밀번호가 틀리면 아무것도 바꾸지 않고 status="wrong_password". 비밀번호 없는 계정이면 PasswordNotSet(app.auth).
    - 사용자가 없거나 그 사이 토큰이 폐기됐으면(token_version 불일치) TokenRevoked.
    - 지금과 같은 주소면 status="unchanged"(메일 없음).
    - 바꾸면 email_verified_at을 비우고, 그 사용자의 미사용 인증 토큰과 **미사용 재설정 토큰**을 모두 무효로 한다
      (옛 주소로 간 재설정 링크가 이메일 변경 뒤에도 살아 있지 않게). 다른 계정이 쓰는 주소면 EmailTaken.
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
        return EmailChange("wrong_password")
    if row.email == email:
        db.rollback()
        return EmailChange("unchanged", username=row.username)
    previous = row.email if row.email_verified_at is not None else None
    row.email, row.email_verified_at = email, None
    try:
        db.flush()
        invalidate_tokens(db, user.id, R.PURPOSE_RESET, at)
        raw = issue_token(db, user.id, R.PURPOSE_VERIFY_EMAIL, at)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        if is_email_conflict(e):
            raise EmailTaken() from None
        raise RuntimeError("account email integrity error") from None
    return EmailChange("changed", raw, row.username, previous)


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
    user_id = consume_token(db, raw, R.PURPOSE_RESET, at)
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
    invalidate_tokens(db, user_id, R.PURPOSE_RESET, at)
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
        raw = issue_token(db, found[0], R.PURPOSE_RESET, now())
        db.commit()
        mailer.send_safely(R.reset_mail(email, found[1], raw))
    _with_session(job, "forgot_password", email)


def send_verification(email: str, username: str, raw: str) -> None:
    """이미 발급한 인증 토큰을 담아 보낸다(가입·이메일 변경·재발송)."""
    mailer.send_safely(R.verify_mail(email, username, raw))


def send_email_changed_notice(previous_email: str, username: str, new_email: str) -> None:
    """이전 인증 주소로 변경 알림(best-effort, 실패해도 응답과 무관)."""
    mailer.send_safely(R.email_changed_mail(previous_email, username, new_email))


# ---------- 회원 탈퇴(2026-10-30) ----------


def account_state(db, user_id: int) -> tuple[bool, str | None, bool, str | None]:
    """(비밀번호 있음, 이메일, 이메일 인증됨, username). 사용자가 없으면 (False, None, False, None)."""
    row = db.execute(select(User.password_hash, User.email, User.email_verified_at, User.username).where(User.id == user_id)).one_or_none()
    if row is None:
        return False, None, False, None
    return bool(row.password_hash), row.email, row.email_verified_at is not None, row.username


def password_matches(db, user_id: int, password: str) -> bool:
    """현재 비밀번호 확인(탈퇴). 해시는 이 함수 밖으로 내보내지 않는다. 해시가 없으면 더미 검증 후 False."""
    stored = db.execute(select(User.password_hash).where(User.id == user_id)).scalar_one_or_none()
    db.rollback()
    return verify_password(password, stored)


def token_owner(db, raw: str, purpose: str, at: datetime) -> tuple[str, str | None] | None:
    """토큰을 쓰지 않고 주인(username, email)만 본다(재설정 비밀번호가 아이디·이메일을 포함하는지 미리 검사할 때). 쓸 수 없는 토큰이면 None."""
    row = db.execute(
        select(User.username, User.email).join(AccountToken, AccountToken.user_id == User.id)
        .where(AccountToken.token_hash == R.hash_token(raw), AccountToken.purpose == purpose,
               AccountToken.used_at.is_(None), AccountToken.expires_at > at)
    ).one_or_none()
    db.rollback()  # 읽기만 했다(뒤이은 reset_password가 새 트랜잭션에서 토큰을 쓴다)
    return (row.username, row.email) if row else None


def delete_user_data(db, user_id: int) -> bool:
    """그 사용자의 데이터를 한 트랜잭션에서 지운다. 소유자 키(user:{id})로 묶인 관심종목·모의투자 계좌(보유·체결은 FK CASCADE)를
    먼저 지우고, users 행을 지우면 리서치 리포트(+tool_call_log)·진단·account_token·oauth_identity가 FK CASCADE로 지워진다.
    다른 사용자 행은 WHERE가 그 사용자의 id/소유자 키뿐이라 건드리지 않는다. 사용자가 없으면 False. commit까지 한다."""
    row = db.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if row is None:
        db.rollback()
        return False
    owner = f"user:{user_id}"
    db.execute(delete(Watchlist).where(Watchlist.device_id == owner))
    db.execute(delete(VirtualAccount).where(VirtualAccount.device_id == owner))
    db.execute(delete(User).where(User.id == user_id))
    db.commit()
    return True


def request_delete_mail(db, user_id: int, email: str, username: str, at: datetime) -> str:
    """탈퇴 확인 토큰(30분, 1회용)을 발급하고 원문을 돌려준다(메일은 호출 쪽이 응답 뒤에 보낸다)."""
    raw = issue_token(db, user_id, R.PURPOSE_DELETE, at)
    db.commit()
    return raw


def confirm_delete(db, raw: str, at: datetime) -> int | None:
    """탈퇴 확인 토큰을 쓰고 그 사용자의 데이터를 지운다. 지운 user id, 토큰이 맞지 않으면 None."""
    user_id = consume_token(db, raw, R.PURPOSE_DELETE, at)
    if user_id is None:
        db.rollback()
        return None
    db.commit()
    return user_id if delete_user_data(db, user_id) else None


def send_delete_confirmation(email: str, username: str, raw: str) -> None:
    mailer.send_safely(R.delete_account_mail(email, username, raw))
