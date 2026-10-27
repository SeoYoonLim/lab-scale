"""scripts/ci_seed.py와 ci_seed_companies.json: CI의 빈 pgvector DB를 채우는 종목 시드."""

import json
from pathlib import Path

import pytest

from scripts import ci_seed

DATASET = Path(__file__).resolve().parents[1] / "evals" / "dataset.jsonl"


@pytest.fixture(scope="module")
def seed():
    return ci_seed.read_seed()


def test_seed_rows_have_only_public_company_fields(seed):
    assert len(seed) >= 300
    for row in seed:
        assert set(row) == set(ci_seed.FIELDS)  # 시세·뉴스·리포트·id·시각 같은 건 없다
        assert row["ticker"] and row["name"] and row["asset_type"] in ("stock", "etf")


def test_seed_tickers_are_unique_and_include_etfs(seed):
    tickers = [r["ticker"] for r in seed]
    assert len(tickers) == len(set(tickers))
    assert sum(r["asset_type"] == "etf" for r in seed) >= 40
    assert {"005930", "000660", "035420"} <= set(tickers)


def test_etf_rows_have_no_market_or_sector(seed):
    """company 모델의 규칙: ETF는 market/sector가 NULL이다."""
    assert all(r["market"] is None and r["sector"] is None for r in seed if r["asset_type"] == "etf")


def test_every_company_the_eval_dataset_expects_is_in_the_seed(seed):
    """CI의 offline 평가가 시드만으로 판정할 수 있어야 한다: 데이터셋이 기대하거나 금지하는 종목이 전부 시드에 있다."""
    names = {r["name"] for r in seed}
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for c in row["expect"].get("companies", []) + row["expect"].get("must_not_companies", []):
            assert c in names, (row["id"], c)


def test_seed_file_has_no_secret_like_content():
    text = ci_seed.SEED_PATH.read_text(encoding="utf-8")
    assert "eyJ" not in text and "password" not in text.lower() and "secret" not in text.lower()


class FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeSession:
    """db.execute(insert ... ON CONFLICT DO NOTHING RETURNING ticker)만 흉내낸다: 이미 있는 ticker면 행이 없다."""

    def __init__(self, existing):
        self.tickers = set(existing)
        self.commits = 0

    def execute(self, stmt):
        from sqlalchemy.dialects import postgresql

        ticker = stmt.compile(dialect=postgresql.dialect()).params["ticker"]
        if ticker in self.tickers:
            return FakeResult(None)
        self.tickers.add(ticker)
        return FakeResult((ticker,))

    def commit(self):
        self.commits += 1


def test_load_counts_new_and_existing_and_is_idempotent(seed):
    total = len(seed)
    db = FakeSession(existing={"005930", "000660"})
    assert ci_seed.load_seed(db) == (total - 2, 2) and db.commits == 1
    assert ci_seed.load_seed(db) == (0, total) and db.commits == 2  # 두 번째는 아무것도 넣지 않는다
    assert ci_seed.load_seed(FakeSession(set()), commit=False) == (total, 0)


def test_load_does_not_commit_when_told_not_to(seed):
    db = FakeSession(set())
    ci_seed.load_seed(db, commit=False)
    assert db.commits == 0


def test_ci_integration_job_checks_idempotency_on_the_real_db():
    """진짜 DB에서의 멱등성(ON CONFLICT DO NOTHING)은 CI backend-integration job이 시드를 두 번 적재해 확인한다."""
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "새로 0개" in workflow
