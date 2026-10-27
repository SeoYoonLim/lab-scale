"""(F) 도구 실행 전 종목 인자 검증: 종목을 받는 호출만 있고 하나도 해석되지 않으면 코드가 "찾지 못함"으로 답한다."""

import pytest

from app import agent


class _DB:
    def __init__(self, inner):
        self._inner = inner

    def query(self, model):
        return self._inner.query(model)

    def close(self):
        pass


@pytest.fixture
def db_names(monkeypatch, make_db):
    def _use(*names):
        fake = make_db(*names)
        monkeypatch.setattr(agent, "SessionLocal", lambda: _DB(fake))

    return _use


def call(name, **args):
    return {"function": {"name": name, "arguments": args}}


def test_garbled_or_missing_company_gets_code_answer(db_names):
    db_names("삼성전자", "롯데쇼핑")
    assert agent._unresolved_company_answer([call("stock_tool", ticker="한뫸입해")]) == agent.UNRESOLVED_COMPANY_ANSWER
    assert agent._unresolved_company_answer([call("stock_tool", ticker="롯데칠성")]) == agent.UNRESOLVED_COMPANY_ANSWER
    assert agent._unresolved_company_answer([call("stock_tool", ticker="애플"), call("news_tool", company_name="애플")]) \
        == agent.UNRESOLVED_COMPANY_ANSWER


def test_candidates_are_listed(db_names):
    db_names("SK바이오팜", "SK바이오사이언스")
    text = agent._unresolved_company_answer([call("stock_tool", ticker="SK바이오")])
    assert text.startswith(agent.UNRESOLVED_COMPANY_ANSWER) and "SK바이오사이언스, SK바이오팜 중 하나인가요?" in text


@pytest.mark.parametrize("calls", [
    [call("stock_tool", ticker="삼성전자")],
    [call("stock_tool", ticker="삼성전자"), call("news_tool", company_name="롯데칠성")],  # 하나라도 해석되면 기존 흐름
    [call("fx_tool", period_days=5)],
    [call("stock_tool", ticker="롯데칠성"), call("fx_tool")],  # 종목 없는 호출이 섞이면 기존 흐름
    [call("rag_search_tool", query="PER")],  # 종목 필터 없는 RAG 검색
    [],
])
def test_existing_flow_otherwise(db_names, calls):
    db_names("삼성전자")
    assert agent._unresolved_company_answer(calls) is None


def test_db_error_falls_back_to_existing_flow(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(agent, "SessionLocal", boom)
    assert agent._unresolved_company_answer([call("stock_tool", ticker="롯데칠성")]) is None
