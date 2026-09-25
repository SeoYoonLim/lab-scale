"""dev DB(docker, localhost:15432)에 저장된 실제 데이터에 기대는 테스트. DB가 없으면 skip된다."""

import pytest

from app.tools.company_resolver import resolve_company
from app.tools.news_tool import news_tool
from app.tools.stock_tool import stock_tool

pytestmark = pytest.mark.integration


def test_known_companies_resolve(dev_db):
    for name in ["삼성전자", "SK하이닉스", "카카오", "KCC", "SK네트웍스"]:
        assert resolve_company(dev_db, name).company.name == name


def test_fewshot_company_is_absent_from_db(dev_db):
    # agent.FEW_SHOT_MESSAGES는 롯데칠성이 DB에 없어서 found=false가 실제 응답과 같다는 전제로 쓰였다.
    # 롯데칠성이 수집되면 예시가 거짓이 되므로 few-shot 종목을 다시 골라야 한다.
    assert resolve_company(dev_db, "롯데칠성").company is None


def test_fewshot_tool_results_match_real_responses():
    from app.agent import FEW_SHOT_MESSAGES
    import json

    stock_msg, news_msg = [json.loads(m["content"]) for m in FEW_SHOT_MESSAGES if m["role"] == "tool"]
    assert stock_tool("롯데칠성", period_days=1) == stock_msg
    assert news_tool("롯데칠성", limit=5) == news_msg


def test_stock_tool_found_shape():
    r = stock_tool("삼성전자", period_days=3)
    assert r["found"] is True
    assert r["company_name"] == "삼성전자"
    assert 1 <= len(r["prices"]) <= 3
    assert {"price_date", "close_price", "volume", "change_pct"} <= set(r["prices"][0])


def test_news_tool_found_shape():
    r = news_tool("삼성전자", limit=3)
    assert r["found"] is True
    assert 1 <= len(r["news"]) <= 3
    assert {"title", "url", "published_at"} <= set(r["news"][0])


def test_list_reports_envelope(dev_db):
    from app.reports import list_reports

    total, items = list_reports(limit=1, offset=0)
    assert isinstance(total, int)
    assert len(items) <= 1
