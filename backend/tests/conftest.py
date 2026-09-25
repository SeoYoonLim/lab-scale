import pytest
from sqlalchemy import text

from app.models import Company


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
