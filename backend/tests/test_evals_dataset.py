"""evals/dataset.jsonl 검증: 필드, id 중복, split 비율, 카테고리·mode 분포, 기대 종목이 실제 company 테이블에 있는지."""

import json
from collections import Counter
from pathlib import Path

import pytest

DATASET = Path(__file__).resolve().parents[1] / "evals" / "dataset.jsonl"
CATEGORY_MODE = {"entity": "offline", "period": "offline", "routing_gate": "offline",
                 "tool_selection": "live", "grounding": "live", "refusal": "live", "etf_safety": "live",
                 "freshness": "live", "stability": "live", "briefing": "live", "attachment": "live",
                 "period_history": "live", "narrative": "live", "news_relevance": "live", "safety": "live",
                 "forecast": "live", "forecast_followup": "live", "glossary": "live"}
CATEGORY_SIZE = {"entity": 35, "period": 14, "routing_gate": 10, "tool_selection": 26, "grounding": 35,
                 "refusal": 21, "etf_safety": 20, "freshness": 4, "stability": 12, "briefing": 6, "attachment": 8,
                 "period_history": 72, "narrative": 100, "news_relevance": 12, "safety": 23,
                 "forecast": 88, "forecast_followup": 86, "glossary": 12}
# batch 10·11·13·14·16·18·20·22·24는 전부 holdout, batch 12·15·17·19·21·23·25는 전부 dev다. 카테고리 비율 계산에서 뺀다.
ALL_HOLDOUT_BATCHES = (10, 11, 13, 14, 16, 18, 20, 22, 24)
ALL_DEV_BATCHES = (12, 15, 17, 19, 21, 23, 25)
EXPECT_KEYS = {"companies", "must_not_companies", "tools_all", "tools_forbidden", "period_days", "must_refuse",
               "must_contain", "must_not_contain", "gates", "ref_date", "start", "end", "must_partial",
               "must_state_data_date", "must_answer", "must_briefing", "check_attachment", "etf_holdings", "number_scored",
               "must_forecast", "must_followup", "period", "must_backtest_line", "must_indirect", "must_earnings", "must_layout23",
               "must_short", "must_judgment24", "earnings_quarter", "must_direction25",
               "must_forecast_v5", "must_v24_content", "must_mode26", "must_layout26",
               "must_evidence26", "must_dates26", "must_consistency26", "must_filing26", "must_signal26"}
TOOLS = {"stock_tool", "news_tool", "disclosure_tool", "rag_search_tool", "market_tool", "fx_tool",
         "discovery_tool", "event_reaction_tool", "*"}


@pytest.fixture(scope="module")
def rows():
    with open(DATASET, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_required_fields(rows):
    for r in rows:
        assert {"id", "split", "category", "mode", "question", "expect"} <= r.keys(), r.get("id")
        assert set(r) <= {"id", "split", "category", "mode", "batch", "question", "expect", "turns"}, r["id"]
        assert all(isinstance(t, str) and t.strip() for t in r.get("turns", [])), r["id"]
        assert r["split"] in ("dev", "holdout")
        assert r["mode"] in ("offline", "live")
        assert r["question"].strip()
        assert r.get("batch", 1) in range(1, 26)
        assert isinstance(r["expect"], dict) and r["expect"], r["id"]
        assert set(r["expect"]) <= EXPECT_KEYS, r["id"]


def test_ids_unique(rows):
    dup = [i for i, n in Counter(r["id"] for r in rows).items() if n > 1]
    assert dup == []


def test_category_mode_and_size(rows):
    assert Counter(r["category"] for r in rows) == CATEGORY_SIZE
    for r in rows:
        assert r["mode"] == CATEGORY_MODE[r["category"]], r["id"]
    assert Counter(r["mode"] for r in rows) == {"offline": 59, "live": 525}
    assert Counter(r.get("batch", 1) for r in rows) == {1: 80, 2: 30, 3: 24, 4: 24, 5: 6, 6: 22, 7: 20, 8: 40, 9: 12, 10: 40, 11: 40,
                                                        12: 12, 13: 30, 14: 30, 15: 12, 16: 30, 17: 12, 18: 20, 19: 10, 20: 20, 21: 10, 22: 20, 23: 10, 24: 20, 25: 10}


def test_split_is_60_40_per_category(rows):
    for cat in CATEGORY_SIZE:
        items = [r for r in rows if r["category"] == cat and r.get("batch") not in (*ALL_HOLDOUT_BATCHES, *ALL_DEV_BATCHES)]
        if items:
            assert sum(r["split"] == "holdout" for r in items) == round(len(items) * 0.4), cat


@pytest.mark.parametrize("number", ALL_HOLDOUT_BATCHES)
def test_all_holdout_batch_has_new_questions(rows, number):
    batch = [r for r in rows if r.get("batch") == number]
    size = 20 if number in (18, 20, 22, 24) else 30 if number in (13, 14, 16) else 40
    assert len(batch) == size and all(r["split"] == "holdout" for r in batch)
    if number == 14:  # 전망 10, 2~3턴 후속 8, 용어 회귀 6, 기타 회귀 6
        counts = Counter(r["category"] for r in batch)
        assert (counts["forecast"], counts["forecast_followup"], counts["glossary"]) == (10, 8, 6)
        assert all(1 <= len(r["turns"]) <= 2 for r in batch if r["category"] == "forecast_followup")
    elif number == 16:  # 전망 10(과거 검증 줄), 이름 6, 간접 뉴스 4, 주문 4, 실적 3, 기타 회귀 3
        e = [r["expect"] for r in batch]
        assert sum(bool(x.get("must_backtest_line")) for x in e) == 10
        assert sum(bool(x.get("must_indirect")) for x in e) == 4 and sum(bool(x.get("must_earnings")) for x in e) == 3
        assert sum("채팅에서는 주문을 실행하지 않습니다" in x.get("must_contain", []) for x in e) == 4
    elif number == 18:  # 단독 전망 8 + 다른 종목 혼입 회귀 1, 2~3턴 후속 8(판단 4·위험 2·확률 2), 용어·주문·기간 수익률 회귀 3
        e = [r["expect"] for r in batch]
        assert sum(bool(x.get("must_layout23")) for x in e) == 9
        # 2026-11-04(승인 뒤 기준 조정): 판단·확률 6개의 must_short(옛 첫 문장 문구)는 must_direction25로 바꿨다. 위험 2개는 그대로
        assert Counter(x.get("must_short") for x in e if x.get("must_short")) == {"risk": 2}
        assert Counter(x.get("must_direction25") for x in e if x.get("must_direction25")) == {"verdict": 4, "probability": 2}
        assert all(1 <= len(r["turns"]) <= 2 for r in batch if r["category"] == "forecast_followup")
        assert {x["period"] for x in e if x.get("must_layout23")} == {"1주일", "1개월", "3개월"}
    elif number == 20:  # 단독 전망 5(현재 판단·강도), 후속 12(판단 3·고르기 2·비교·확률·매수·실적 3·데이터), 종목 되묻기·주문·뉴스 회귀 3
        e = [r["expect"] for r in batch]
        # 2026-11-04(승인 뒤 기준 조정): must_judgment24(현재 판단 문구·24차 규칙)는 must_direction25와 내용 검사 must_v24_content로 바꿨다
        assert Counter(x.get("must_v24_content") for x in e if x.get("must_v24_content")) == {
            "standalone": 5, "verdict": 3, "pick": 2, "compare": 1, "probability": 1, "buy": 1, "earnings": 3, "data": 1}
        assert all(x.get("must_direction25") == x.get("must_v24_content") for x in e if x.get("must_v24_content"))
        assert all(x.get("must_layout23") for x in e if x.get("must_v24_content") == "standalone")
        assert all(1 <= len(r["turns"]) <= 2 for r in batch if r["category"] == "forecast_followup" and r.get("turns"))
        assert sum(bool(x.get("earnings_quarter")) for x in e) == 2
    elif number == 22:  # 지시의 질문 10개(SK하이닉스 대화) + 하락·상승 추세·중립·하락 우위 종목 후속 + 단독 3 + 실적·종목 되묻기 회귀
        e = [r["expect"] for r in batch]
        assert Counter(x.get("must_direction25") for x in e if x.get("must_direction25")) == {
            "standalone": 3, "verdict": 7, "pick": 2, "buy": 1, "why": 2, "oneline": 2, "probability": 1, "earnings": 1}
        assert all(x.get("must_layout23") for x in e if x.get("must_direction25") == "standalone")
    elif number == 24:  # 간결 8·상세 6(후속 2 포함), 이유·신호 의미·데이터 기준일·공시 접수일·매수·방향 재확인 후속 6
        e = [r["expect"] for r in batch]
        assert Counter(x.get("must_mode26") for x in e if x.get("must_mode26")) == {"brief": 8, "detail": 6}
        assert sum(bool(x.get("must_filing26")) for x in e) == 1 and sum(bool(x.get("must_signal26")) for x in e) == 1
        assert all(x.get("must_layout26") and x.get("must_evidence26") for x in e if x.get("must_mode26"))
    else:
        assert sum(r["category"] == "narrative" for r in batch) >= 12
    if number >= 20:
        # 지시가 정한 질문("굳이 하나만 고르면?" 등)은 batch 20·22에 같은 문장이 있어, 앞 턴까지 같은 문항이 없는지로 본다
        others = {(r["question"], tuple(r.get("turns", []))) for r in rows if r.get("batch") != number}
        assert not {(r["question"], tuple(r.get("turns", []))) for r in batch} & others
        assert len({(r["question"], tuple(r.get("turns", []))) for r in batch}) == size
        return
    others = {r["question"] for r in rows if r.get("batch") != number}
    assert not {r["question"] for r in batch} & others
    assert len({r["question"] for r in batch}) == size


@pytest.mark.parametrize("number", ALL_DEV_BATCHES)
def test_dev_only_batches(rows, number):
    batch = [r for r in rows if r.get("batch") == number]
    assert 10 <= len(batch) <= 15 and all(r["split"] == "dev" for r in batch)


@pytest.mark.parametrize("batch", [2, 3, 4, 5, 6, 7, 8, 9])
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
            # 2026-10-31: 3년치 백필로 데이터가 생긴 기간 문항(ref_05, 10, 11, 13)은 거절이 아니라 값(must_contain)을 기대한다
            assert e.get("must_refuse") is True or e.get("must_partial") is True or e.get("must_contain")
        if r["category"] == "briefing":
            assert e.get("must_briefing") is True and not e.get("companies")
        if r["category"] == "attachment":
            assert e.get("check_attachment") is True and len(e.get("companies", [])) >= 2
        if "period_days" in e and r["mode"] == "live":
            assert e["period_days"] == "parser" or isinstance(e["period_days"], int)


def test_etf_branches_have_ticker_and_both_expectations(rows):
    branched = [r for r in rows if "etf_holdings" in r["expect"]]
    assert {r["id"] for r in branched} == {"etf_03", "etf_06", "etf_08", "etf_11", "etf_12", "etf_16"}
    for r in branched:
        b = r["expect"]["etf_holdings"]
        assert b["ticker"].isdigit() and len(b["ticker"]) == 6
        assert b["with_data"]["must_contain"] and b["without_data"]["must_refuse"] is True
        assert set(b["with_data"]) | set(b["without_data"]) <= EXPECT_KEYS


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
