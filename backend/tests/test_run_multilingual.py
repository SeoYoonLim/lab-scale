"""evals/run_multilingual.py(48차 4개 언어 실답 검사)의 판정 함수와 문항 파일 형식. DB·Ollama 없이 돈다."""

from pathlib import Path

import pytest

from app.i18n import SUPPORTED_LOCALES
from evals import run_multilingual as M

CASES = Path(__file__).resolve().parents[1] / "evals" / "multilingual_cases.txt"
NAMES = ["삼성전자", "SK하이닉스", "SK", "KODEX 200"]


def stock(name="삼성전자", end="2026-11-12"):
    return {"tool_name": "stock_tool", "result": {"found": True, "company_name": name,
                                                  "prices": [{"price_date": end, "close_price": 1.0}]}}


def test_case_file_format():
    rows = [line.split("|") for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    assert len(rows) >= 50
    assert all(len(r) == 5 and r[0] in ("dev", "holdout") and r[2] in SUPPORTED_LOCALES for r in rows)
    assert len({r[1] for r in rows}) == len(rows)
    assert {r[2] for r in rows} == set(SUPPORTED_LOCALES)  # 4개 언어가 모두 있다


def test_holdings_detection():
    assert M.has_holdings("상위 구성종목\n1. 삼성전자 (005930) 34.19%")
    assert M.has_holdings("KODEX 200에서 삼성전자(005930)의 비중은 34.19%입니다.")
    assert not M.has_holdings("KODEX 200의 2026-11-12 종가는 84,240원, 20거래일간 -3.06%입니다.")


def test_clean_answer_has_no_checks():
    resp = {"answer": "삼성전자의 2026-11-12 종가는 208,000원입니다.", "sources": [], "meta": {}}
    assert M.check_one("d01", "ko-KR", ["삼성전자"], "삼성전자 어때?", resp, [stock()], resp["answer"], NAMES) == {}


def test_wrong_resolution_missing_date_and_changed_number_are_flagged():
    resp = {"answer": "SK closed at 1,254,001 won.", "sources": [{"url": "https://made.up/1"}],
            "meta": {"translation_status": "translated"}}
    checks = M.check_one("n04", "en-US", ["SK하이닉스"], "SK海力士最近股价怎么样？", resp, [stock("SK")],
                         "SK의 종가는 1,254,000원입니다.", NAMES)
    assert set(checks) == {"wrong_company", "bad_source_id", "number_changed", "base_date_missing"}


@pytest.mark.parametrize("answer", ["삼성전자는 2026년 11월 12일 기준 208,000원", "삼성전자 11월 12일 종가 208,000원"])
def test_korean_date_forms_count_as_base_date(answer):
    resp = {"answer": answer, "sources": [], "meta": {}}
    assert "base_date_missing" not in M.check_one("d01", "ko-KR", ["삼성전자"], "q", resp, [stock()], answer, NAMES)


def test_holding_names_from_the_template_data_are_allowed():
    answer = "KODEX 200의 구성종목과 비중입니다.\n\n상위 구성종목\n1. 삼성전자 (005930) 34.19%\n2. SK하이닉스 (000660) 27.40%"
    resp = {"answer": answer, "sources": [], "meta": {"etf_holdings": {"kind": "constituents", "views": [
        {"name": "KODEX 200", "items": [{"name": "삼성전자"}, {"name": "SK하이닉스"}]}]}}}
    assert M.check_one("e01", "ko-KR", ["KODEX 200"], "KODEX 200 구성종목", resp, [], answer, NAMES) == {}
