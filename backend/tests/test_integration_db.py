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

    # 소유자 없는(로그인 도입 전) 리포트 범위로 형식만 확인한다.
    total, items = list_reports(user_id=None, limit=1, offset=0)
    assert isinstance(total, int)
    assert len(items) <= 1


def test_stock_tool_single_day_response_is_unchanged():
    """하루치 조회에는 기간 요약 키가 붙지 않는다(few-shot 예시·기존 프롬프트와 같은 모양)."""
    r = stock_tool("삼성전자", period_days=1)
    assert set(r) == {"ticker", "company_name", "period_days", "found", "change_pct", "volume", "prices"}
    assert len(r["prices"]) == 1 and r["change_pct"] == r["prices"][0]["change_pct"]


def test_stock_tool_period_summary_matches_the_stored_closes(dev_db):
    """여러 거래일 조회의 기간 등락률은 '구간 직전 거래일 종가 대비'이고, 진단의 20거래일 수익률과 같은 값이다."""
    from app.diagnosis import RETURN_WINDOW_DAYS, _recent_returns
    from app.models import StockPrice

    company = resolve_company(dev_db, "삼성전자").company
    closes = [
        (d, float(c))
        for d, c in dev_db.query(StockPrice.price_date, StockPrice.close_price)
        .filter(StockPrice.company_id == company.id)
        .order_by(StockPrice.price_date.desc())
        .limit(RETURN_WINDOW_DAYS + 1)
    ]
    assert len(closes) == RETURN_WINDOW_DAYS + 1, "dev DB에 삼성전자 주가가 21거래일 이상 있어야 한다"

    r = stock_tool("삼성전자", period_days=RETURN_WINDOW_DAYS)

    assert len(r["prices"]) == r["trading_days"] == RETURN_WINDOW_DAYS  # 기준으로 쓴 직전 거래일은 prices에 없다
    assert r["period_end_date"] == closes[0][0].isoformat() == r["prices"][-1]["price_date"]
    assert r["period_start_date"] == closes[-1][0].isoformat() < r["prices"][0]["price_date"]
    expected = round((closes[0][1] / closes[-1][1] - 1) * 100, 2)
    assert r["period_change_pct"] == expected
    assert r["period_change_pct"] == round(_recent_returns(dev_db, [company.id])[company.id], 2)
    assert f"{RETURN_WINDOW_DAYS}거래일간 {expected:+.2f}%" in r["summary"]
    # 최상위 change_pct는 여전히 마지막 하루 등락률이다
    assert r["change_pct"] == r["prices"][-1]["change_pct"]
