"""50차: 49차 평가 문항 v1 보존과 v2(1개월 = 24거래일) 차이, 재채점 도구(evals/rescore_routes.py)."""
import hashlib
from datetime import date
from pathlib import Path

from evals import rescore_routes as rr

EVALS = Path(__file__).resolve().parents[1] / "evals"
V1 = EVALS / "multilingual_cases_49.txt"
V2 = EVALS / "multilingual_cases_49_v2.txt"


def test_v1_file_is_unchanged():
    # 49차에 구현 전 고정한 해시(docs/LLM_ANSWER_QUALITY_49.md)
    assert hashlib.sha256(V1.read_bytes()).hexdigest().startswith("29a21346ada5050b")


def test_v2_differs_only_in_one_month_days():
    v1, v2 = rr.read_cases(str(V1)), rr.read_cases(str(V2))
    assert v1.keys() == v2.keys() and len(v1) == 44
    diff = {k for k in v1 if v1[k] != v2[k]}
    assert diff == {"P08", "P03", "P05", "L01", "P07"}
    for k in diff:
        assert v1[k]["spec"].replace("days=19-23", "days=24") == v2[k]["spec"]
        assert {**v1[k], "spec": None} == {**v2[k], "spec": None}


def test_service_month_definition_matches_v2_on_run_date():
    assert rr.month_days(date(2026, 11, 13)) == rr.V2_MONTH_DAYS


def test_score_counts_by_expectation_set():
    results = [
        {"id": "P03", "observed": {"route": "past", "days": 24, "companies": ["SK텔레콤"]}},
        {"id": "P01", "observed": {"route": "past", "days": 5, "companies": ["신한지주"]}},
    ]
    s1, s2 = rr.score(results, rr.read_cases(str(V1))), rr.score(results, rr.read_cases(str(V2)))
    assert (s1["intent_ok"], s1["judged"], s1["period_wrong"]) == (1, 2, 1)
    assert (s2["intent_ok"], s2["judged"], s2["period_wrong"]) == (2, 2, 0)
    assert s1["fails"][0]["id"] == "P03"
