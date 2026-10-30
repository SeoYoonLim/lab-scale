"""소셜 로그인의 DB 계층: 계정 결정 적용(로그인·연결·생성)과 1회용 로그인 코드.

- 계정 결정 규칙은 app.oauth.decide_account(순수)가 정하고 여기서는 조회·저장만 한다.
- 새 계정의 사용자명은 provider_ + 무작위 8자리 hex이고 유일 위반이면 최대 USERNAME_ATTEMPTS번 다시 뽑는다. 비밀번호는 없다(NULL).
- 같은 공급자 계정으로 동시에 두 번 콜백이 와서 identity 유일 위반이 나면, 되돌린 뒤 이미 생긴 identity로 로그인한다.
- 1회용 로그인 코드는 account_token(purpose='oauth_login', 60초)을 쓴다. 원문 대신 SHA-256만 저장하고, 사용은 조건부 UPDATE 한 문장이라
  동시에 두 번 바꿔도 한 번만 성공한다(app.account_recovery_db.consume_token). 메모리가 아니라 DB라서 워커가 여럿이거나 재시작해도 맞다.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import account_recovery as R
from app.account_recovery_db import consume_token, issue_token
from app.auth import CurrentUser, is_email_conflict
from app.models import OAuthIdentity, User
from app.oauth import Decision, LocalAccount, Profile, decide_account, new_username

USERNAME_ATTEMPTS = 5


def _current(row: User) -> CurrentUser:
    return CurrentUser(id=row.id, username=row.username, token_version=row.token_version)


def _identity_user(db, profile: Profile) -> User | None:
    return db.execute(
        select(User).join(OAuthIdentity, OAuthIdentity.user_id == User.id)
        .where(OAuthIdentity.provider == profile.provider, OAuthIdentity.provider_user_id == profile.provider_user_id)
    ).scalar_one_or_none()


def _same_email_account(db, email: str | None) -> LocalAccount | None:
    if not email:
        return None
    row = db.execute(select(User.id, User.email_verified_at).where(User.email == email)).one_or_none()
    return LocalAccount(row.id, row.email_verified_at is not None) if row else None


def resolve_login(db, profile: Profile, at: datetime) -> tuple[CurrentUser, Decision]:
    """공급자 프로필로 우리 사용자를 정한다(로그인·연결·생성). (사용자, 적용한 결정)."""
    for _ in range(2):  # identity 유일 위반(동시 콜백)이면 한 번 더: 그때는 기존 identity로 로그인된다
        existing = _identity_user(db, profile)
        decision = decide_account(profile, existing.id if existing else None, _same_email_account(db, profile.email))
        if decision.kind == "login":
            db.rollback()
            return _current(existing), decision
        try:
            if decision.kind == "link":
                user = db.get(User, decision.user_id)
            else:
                user = _create_user(db, profile.provider, decision, at)
            db.add(OAuthIdentity(user_id=user.id, provider=profile.provider, provider_user_id=profile.provider_user_id,
                                 email=profile.email))
            db.commit()
            return _current(user), decision
        except IntegrityError:
            db.rollback()
    raise RuntimeError("oauth identity conflict")


def _create_user(db, provider: str, decision: Decision, at: datetime) -> User:
    """비밀번호 없는 새 계정. 이메일 유일 위반(그 사이 같은 이메일이 생김)이면 이메일 없이 만든다(연결하지 않는다)."""
    email = decision.email
    for _ in range(USERNAME_ATTEMPTS):
        nested = db.begin_nested()
        user = User(username=new_username(provider), password_hash=None, email=email,
                    email_verified_at=at if (email and decision.email_verified) else None)
        db.add(user)
        try:
            nested.commit()
            return user
        except IntegrityError as e:
            nested.rollback()
            if email and is_email_conflict(e):
                email = None  # 다른 계정이 그 사이 같은 이메일을 가졌다: 연결하지 않고 이메일 없이 만든다
    raise RuntimeError("could not allocate username")


def issue_login_code(db, user_id: int, at: datetime) -> str:
    """60초 1회용 로그인 코드(원문). 같은 사용자의 이전 미사용 코드는 무효가 된다."""
    raw = issue_token(db, user_id, R.PURPOSE_OAUTH_LOGIN, at)
    db.commit()
    return raw


def exchange_login_code(db, raw: str, at: datetime) -> CurrentUser | None:
    """코드를 1회 사용 처리하고 사용자. 없거나·만료·이미 씀·다른 목적이면 None."""
    user_id = consume_token(db, raw, R.PURPOSE_OAUTH_LOGIN, at)
    if user_id is None:
        db.rollback()
        return None
    row = db.get(User, user_id)
    db.commit()
    return _current(row) if row else None
