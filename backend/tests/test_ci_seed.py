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


@pytest.mark.integration
def test_load_is_idempotent_and_leaves_the_dev_db_untouched(dev_db):
    """같은 트랜잭션에서 두 번 적재하면 두 번째는 0개를 넣는다. 끝나면 롤백해서 개발 DB에는 아무것도 남지 않는다."""
    total = len(ci_seed.read_seed())
    try:
        first_new, first_existing = ci_seed.load_seed(dev_db, commit=False)
        assert first_new + first_existing == total
        assert ci_seed.load_seed(dev_db, commit=False) == (0, total)
    finally:
        dev_db.rollback()
