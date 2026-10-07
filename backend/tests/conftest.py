import secrets

import pytest
from sqlalchemy import text

from app.models import Company


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch):
    """모든 테스트에 테스트마다 새로 만든 임의 JWT_SECRET을 넣는다(.env의 실제 값을 쓰지 않는다)."""
    secret = secrets.token_urlsafe(48)
    monkeypatch.setenv("JWT_SECRET", secret)
    return secret


@pytest.fixture
def login_as():
    """라우터 테스트용: get_current_user를 가짜 사용자로 바꾼다(토큰/DB 없이). 테스트가 끝나면 원래대로 돌린다."""
    from app.api.deps import get_current_user
    from app.auth import CurrentUser
    from app.main import app

    def _login(user_id: int = 1, username: str = "tester") -> CurrentUser:
        user = CurrentUser(id=user_id, username=username)
        app.dependency_overrides[get_current_user] = lambda: user
        return user

    yield _login
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def make_user(dev_db):
    """dev DB에 테스트용 사용자를 만들고 테스트가 끝나면 지운다(리포트는 FK CASCADE, 관심종목/모의투자는 owner key로 정리).

    반환: (CurrentUser, 평문 비밀번호). username은 `pt_` 접두어 + 임의값.
    """
    from app.auth import create_user
    from app.db.session import SessionLocal
    from app.models import User, VirtualAccount, Watchlist

    created = []

    def _make():
        password = secrets.token_urlsafe(12)
        db = SessionLocal()
        try:
            user = create_user(db, f"pt_{secrets.token_hex(6)}", password)
        finally:
            db.close()
        created.append(user)
        return user, password

    yield _make

    dev_db.rollback()
    for user in created:
        dev_db.query(Watchlist).filter(Watchlist.device_id == user.owner_key).delete()
        # holding/trade는 virtual_account FK ON DELETE CASCADE로 함께 지워진다.
        dev_db.query(VirtualAccount).filter(VirtualAccount.device_id == user.owner_key).delete()
        dev_db.query(User).filter(User.id == user.id).delete()
    dev_db.commit()


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeDB:
    """resolve_company/extract_from_text가 쓰는 db.query(Company).all()만 흉내낸다."""

    def __init__(self, companies):
        self._companies = companies

    def query(self, model):
        assert model is Company
        return FakeQuery(self._companies)


@pytest.fixture
def make_db():
    def _make(*names):
        companies = [Company(id=i, name=n, ticker=f"{i:06d}") for i, n in enumerate(names, start=1)]
        return FakeDB(companies)

    return _make


@pytest.fixture(scope="session")
def dev_db():
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db.close()
        pytest.skip(f"dev DB에 연결할 수 없음: {type(e).__name__}")
    yield db
    db.close()
