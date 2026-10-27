"""evals/dataset.jsonl 검증: 필드, id 중복, split 비율, 카테고리·mode 분포, 기대 종목이 실제 company 테이블에 있는지."""

import json
from collections import Counter
from pathlib import Path

import pytest

DATASET = Path(__file__).resolve().parents[1] / "evals" / "dataset.jsonl"
CATEGORY_MODE = {"entity": "offline", "period": "offline", "routing_gate": "offline",
                 "tool_selection": "live", "grounding": "live", "refusal": "live", "etf_safety": "live",
                 "freshness": "live", "stability": "live"}
CATEGORY_SIZE = {"entity": 28, "period": 14, "routing_gate": 10, "tool_selection": 26, "grounding": 25,
                 "refusal": 13, "etf_safety": 10, "freshness": 4, "stability": 4}
EXPECT_KEYS = {"companies", "must_not_companies", "tools_all", "tools_forbidden", "period_days", "must_refuse",
               "must_contain", "must_not_contain", "gates", "ref_date", "start", "end", "must_partial",
               "must_state_data_date", "must_answer"}
TOOLS = {"stock_tool", "news_tool", "disclosure_tool", "rag_search_tool", "market_tool", "fx_tool",
         "discovery_tool", "event_reaction_tool", "*"}


@pytest.fixture(scope="module")
def rows():
    with open(DATASET, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_required_fields(rows):
    for r in rows:
        assert {"id", "split", "category", "mode", "question", "expect"} <= r.keys(), r.get("id")
        assert r["split"] in ("dev", "holdout")
        assert r["mode"] in ("offline", "live")
        assert r["question"].strip()
        assert r.get("batch", 1) in (1, 2, 3)
        assert isinstance(r["expect"], dict) and r["expect"], r["id"]
        assert set(r["expect"]) <= EXPECT_KEYS, r["id"]


def test_ids_unique(rows):
    dup = [i for i, n in Counter(r["id"] for r in rows).items() if n > 1]
    assert dup == []


def test_category_mode_and_size(rows):
    assert Counter(r["category"] for r in rows) == CATEGORY_SIZE
    for r in rows:
        assert r["mode"] == CATEGORY_MODE[r["category"]], r["id"]
    assert Counter(r["mode"] for r in rows) == {"offline": 52, "live": 82}
    assert Counter(r.get("batch", 1) for r in rows) == {1: 80, 2: 30, 3: 24}


def test_split_is_60_40_per_category(rows):
    for cat, size in CATEGORY_SIZE.items():
        holdout = sum(1 for r in rows if r["category"] == cat and r["split"] == "holdout")
        assert holdout == round(size * 0.4), cat


@pytest.mark.parametrize("batch", [2, 3])
def test_added_batch_split_is_60_40_per_category(rows, batch):
    """수정 라운드 전에 추가한 batch 문항도 카테고리별로 holdout 40% 안팎(그 라운드의 unseen holdout)."""
    for cat in CATEGORY_SIZE:
        new = [r for r in rows if r["category"] == cat and r.get("batch") == batch]
        if new:
            assert sum(r["split"] == "holdout" for r in new) == round(len(new) * 0.4), (batch, cat)


def test_expectations_are_well_formed(rows):
    for r in rows:
        e = r["expect"]
        for t in e.get("tools_all", []) + e.get("tools_forbidden", []):
            assert t in TOOLS, (r["id"], t)
        if r["category"] == "period":
            assert "ref_date" in e and "period_days" in e
        if r["category"] == "routing_gate":
            assert e["gates"]
        if r["category"] == "refusal":
            assert e.get("must_refuse") is True or e.get("must_partial") is True
        if "period_days" in e and r["mode"] == "live":
            assert e["period_days"] == "parser" or isinstance(e["period_days"], int)


def test_routing_gate_names_exist(rows):
    from app import routing

    for r in rows:
        for name in r["expect"].get("gates", {}):
            assert callable(getattr(routing, name, None)), name


@pytest.mark.integration
def test_expected_companies_exist_in_company_table(rows, dev_db):
    from app.models import Company

    names = {n for (n,) in dev_db.query(Company.name)}
    for r in rows:
        for c in r["expect"].get("companies", []) + r["expect"].get("must_not_companies", []):
            assert c in names, (r["id"], c)


@pytest.mark.integration
def test_not_in_db_entities_really_are_absent(rows, dev_db):
    """'DB에 없는 종목'(기대 종목이 빈 entity)이 실제로도 company 테이블에 없어야 한다."""
    from app.models import Company

    names = {n for (n,) in dev_db.query(Company.name)}
    for r in rows:
        if r["category"] == "entity" and r["expect"]["companies"] == []:
            assert r["question"] not in names, r["id"]
