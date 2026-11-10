"""뉴스 관련성 판정(app/news_relevance), 검색어, 저장 시 표시. DB·Ollama 없이 돈다."""

import json
from pathlib import Path

import pytest

from app import collectors
from app.collectors import news_search_query
from app.news_relevance import title_mentions
from app.models import Company
from app.news_relevance import is_relevant_news, name_forms, needs_context

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "news_relevance_cases.json").read_text(encoding="utf-8"))
LISTED = FIXTURE["listed_names"]


def judge(name, title, content="", ticker=None, asset_type="stock"):
    return is_relevant_news(name, ticker, asset_type, title, content, LISTED)


@pytest.mark.parametrize("name, title, content", [
    # 이름이 다른 말의 일부
    ("태성", "이태성, 재혼 후 2세 바람", "배우 이태성이 조희진과 데이트에 나섰다."),
    ("태성", "태성무역, 커피 기어 브랜드 공개", "태성무역이 전기드립포트를 선보였다."),
    ("태성", "기관주의·과태료 제재", "태성자산운용이 총 6100만원의 제재를 받았다."),
    ("태성", "엑소 수호, 스크린 컴백", "그룹 엑소 수호가 영화에서 아들 태성 역을 맡았다."),
    ("태성", "영화 아버지의 비밀", "지난 실적 없이 평범한 삶을 살아가던 청년 태성이 비밀을 알게 된다."),
    ("스피어", "셀린 디옹 잭팟", "U2가 미국 라스베이거스 스피어 공연에서 기록한 매출을 넘었다."),
    ("NAVER", "딘토, 화장품 기부", "기자 break9874@naver.com"),
    ("NAVER", "태성 주가 상승", "네이버페이 증권에 따르면 태성 주가는 상승했다."),
    ("DL", "1형 당뇨병 환자 연구", "목표혈당범위는 70~180mg/dL로 설정했다."),
    ("DL", "감기약 속 카페인", "판콜에 들어 있는 디엘(DL)-메틸에페드린은 교감신경을 자극한다."),
    ("동서", "경기남부광역철도", "경기남부 동서횡단선을 건의했다."),
    ("동서", "유라시아 문명권", "동서 교류의 흔적을 만날 수 있다."),
    ("스피어", "수험생 알바", "알바몬(운영 법인 웍스피어)이 데이터를 분석했다."),
    ("에스엠", "공사 착공식", "에스엠디자인건설이 착공식을 열었다."),
    ("우리기술", "깃허브 생태계", "우리 기술을 보여주고 피드백을 받겠다고 말했다."),
    ("S-Oil", "Homeless Camp Tainted Soil", "The encampment is seen in St. Paul"),
    # 그룹 이름: 계열사 기사는 지주회사 기사가 아니다
    ("SK", "SK하이닉스 배당금", "SK하이닉스 순이익이 늘었다."),
    ("SK", "SK텔레콤 0.92% 내려", "SK텔레콤 주가는 내린 상태다."),
    ("한화", "한화오션 목표가 하향", "SK증권은 한화오션 목표주가를 내렸다."),
    ("GS", "뮤지컬 추천", "서울 GS아트센터에서 공연 중이다."),
    ("LS", "증권사 전망", "신중호 LS증권 리서치센터장은 주가 상승을 전망했다."),
    # 기사에 이름이 없음
    ("제주반도체", "아파트 경매 양극화", "제주(-0.28%)와 경남은 하락했다."),
])
def test_not_relevant(name, title, content):
    assert judge(name, title, content) is False


@pytest.mark.parametrize("name, title, content", [
    ("태성", "태성, 일본 첨단기판 시장 첫 수주", "글라스기판 핵심 장비를 공급한다."),
    ("태성", "PCB株 불꽃랠리", "네오티스와 태성, 펨트론 등이 상승권에 자리하고 있다."),
    ("삼성전자", "삼성전자 3분기 영업이익 발표", "메모리 업황이 회복됐다."),
    ("SK하이닉스", "SK하이닉스 배당금", "SK하이닉스 순이익이 늘었다."),
    ("SK", "최태원 SK 회장, 경영진에 주문", "실행과 성과를 강조했다."),
    ("SK", "지주사 상승랠리", "SK스퀘어·SK 강세에 저평가 해소 기대"),
    ("한화", "한화그룹 방산 수출", "한화는 이라크 사업 재개를 기대한다."),
    ("NAVER", "NAVER 주가, 장중 5.68% 상승", ""),
    ("NAVER", "네이버, AI 검색 개편", "네이버가 새 서비스를 공개했다."),
    ("신세계", "외국인 방문객 증가", "고속터미널, 신세계백화점 등이 있다."),
    ("삼성화재", "클래식카 박물관", "경기도 용인에 있는 삼성화재모빌리티뮤지엄입니다."),
    ("현대엘리베이터", "브랜드평판 1위", "1위 현대 엘리베이터, 2위 오티스"),
    ("현대차", "현대자동차 인도 공장", "현대자동차가 신공장을 준공했다."),
    ("S-Oil", "울산 화재", "샤힌프로젝트는 S-OIL이 짓는다."),
    ("동서", "동서식품 커피 신제품", ""),
    ("에스엠", "에스파 다음은 라이즈", "에스엠이 신인 그룹 데뷔를 준비한다."),
    ("광동제약", "주요 공시", "광동제약(009290) 공시"),
])
def test_relevant(name, title, content):
    assert judge(name, title, content) is True


def test_ticker_in_text_counts_as_a_mention():
    assert judge("고영", "반도체 장비주 강세", "관련주 (098460) 상승", ticker="098460") is True
    assert judge("고영", "반도체 장비주 강세", "관련주 상승", ticker="098460") is False
    assert judge("고영", "수치 1098460건", "", ticker="098460") is False


def test_etf_uses_the_existing_name_rule():
    assert judge("KODEX 200", "KODEX 200 9만원대", "", asset_type="etf") is True
    assert judge("KODEX 200", "KODEX 200TR 출시", "", asset_type="etf") is False
    assert judge("KODEX 200", "ETF 시장 전망", "", asset_type="etf") is False


def test_empty_text_is_not_relevant():
    assert is_relevant_news("태성", None, "stock", None, None, LISTED) is False


def test_name_forms_include_aliases_and_news_forms():
    assert {"현대차", "현대자동차"} <= set(name_forms("현대차"))
    assert "네이버" in name_forms("NAVER")


def test_context_names():
    assert needs_context("SK") and needs_context("태성") and needs_context("동서")
    assert not needs_context("삼성전자") and not needs_context("기아") and not needs_context("NAVER")


# ---------- 사람이 읽어 판정한 표본: 정상 기사를 숨기지 않는지 고정한다 ----------
# 표본 119건: 측정 지표 표본 50(이름 없음 25, 있음 25)과 표시 후보 표본 70(숨김 40, 남김 30). 판정은 "그 회사 기사이거나 그 회사를 언급함".


def _confusion():
    tp = fp = fn = tn = 0
    wrong = []
    for c in FIXTURE["cases"]:
        got = is_relevant_news(c["company"], c["ticker"], c["asset_type"], c["title"], c["content"], LISTED)
        hide, should_hide = not got, not c["relevant"]
        tp += hide and should_hide
        fp += hide and not should_hide
        fn += not hide and should_hide
        tn += not hide and not should_hide
        if hide != should_hide:
            wrong.append(c["tag"])
    return tp, fp, fn, tn, wrong


def test_labeled_sample_hides_almost_no_real_article():
    tp, fp, fn, tn, wrong = _confusion()
    assert tp + fp + fn + tn == 119
    assert fp / (tp + fp) <= 0.03, wrong  # 숨긴 기사 중 정상 기사 3% 이하
    assert tp / (tp + fn) >= 0.95, wrong  # 오염 기사 95% 이상을 숨긴다


def test_labeled_sample_has_only_the_known_misses():
    # M19 "KB·신한·하나·우리" 약칭(숨김), K4 현대제철 여자축구 일정(남김), M2 사진전 안내 속 카카오톡(남김)
    assert _confusion()[4] == ["K4", "M2", "M19"]


# ---------- 검색어 ----------


@pytest.mark.parametrize("name, query", [
    ("태성", '"태성" 주가'),
    ("동서", '"동서" 주가'),
    ("GS", '"GS" 지주사 -GS건설 -GS리테일 -GS칼텍스'),
    ("DL", '"DL" 주가'),
    ("NAVER", "네이버 실적"),
    ("SK", '"SK(주)"'),
    ("LG", '"LG" 지주사 -LG전자 -LG화학 -LG에너지솔루션 -LG유플러스'),
    ("한화", "한화 주가 실적"),   # 기존 이름 충돌 종목은 그대로
    ("삼성전자", "삼성전자"),
    ("카카오", "카카오"),
])
def test_news_search_query(name, query):
    assert news_search_query(name) == query


# ---------- 저장 시 표시(가짜 응답, 외부 요청 없음) ----------


class _FakeQuery:
    def __init__(self, rows, first):
        self.rows, self._first = rows, first

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._first

    def __iter__(self):
        return iter(self.rows)


class _FakeDb:
    def __init__(self, company):
        self.company = company

    def query(self, what):
        if what is Company:
            return _FakeQuery([], self.company)
        if what is Company.name:
            return _FakeQuery([(n,) for n in LISTED], None)
        return _FakeQuery([], None)  # 같은 url 중복 조회: 없음

    def close(self):
        pass

    def rollback(self):
        pass


def _item(title, description, n):
    return {"title": title, "description": description, "link": f"https://news.example/{n}", "originallink": f"https://news.example/{n}",
            "pubDate": "Fri, 30 Oct 2026 09:00:00 +0900"}


def test_new_news_is_marked_when_saved(monkeypatch):
    company = Company(id=1, ticker="323280", name="태성", asset_type="stock")
    monkeypatch.setattr(collectors, "SessionLocal", lambda: _FakeDb(company))
    asked = []

    def fake_fetch(query, display=10, sort="date"):
        asked.append(query)
        return [_item("이태성, 재혼 후 2세 바람", "배우 이태성의 근황", 1),
                _item("태성, 일본 첨단기판 시장 첫 수주", "글라스기판 장비 공급", 2)]

    monkeypatch.setattr(collectors, "fetch_naver_news", fake_fetch)
    result = collectors.fetch_and_save_news("태성", display=2, dry_run=True)
    assert asked == ['"태성" 주가']
    assert result.ok and [r.is_relevant for r in result.records] == [True, False]  # 관련 기사를 먼저 남긴다


def test_lg_cns_is_not_lg():
    """LG CNS는 상장 이름(LG씨엔에스)과 표기가 달라 긴 이름 제거로 지워지지 않는다(2026-11-02 실제 응답)."""
    assert is_relevant_news("LG", "003550", "stock", "LG CNS 인도네시아 데이터센터 완공", "AI 인프라 수주", ["LG", "LG씨엔에스"]) is False
    assert is_relevant_news("LG", "003550", "stock", "LG 지주사 주가 강세", "구광모 회장", ["LG", "LG씨엔에스"]) is True


# ---------- 제목 일치 (2026-11-02) ----------


@pytest.mark.parametrize("name, title, hit", [
    ("SK", "SK '안정속 쇄신'", True), ("SK", "SK그룹 인사", True), ("SK", "SK하이닉스 사장 과제", False),
    ("카카오", "카카오 주가", True), ("카카오", "카카오뱅크 170만 돌파", False),
    ("한화", "한화오션 수주", False), ("NAVER", "네이버·엔비디아 동맹", True),
    ("태성", "수호, 영화 출연", False), ("LG", None, False),
])
def test_title_mentions(name, title, hit):
    assert title_mentions(name, title) is hit


def test_news_tool_keeps_only_title_matches_within_the_window(monkeypatch):
    # 2026-11-03부터 제목에 종목 이름이 없는 기사는 근거에서 뺀다(app.news_relevance.evidence_news). 전에는 뒤로 보내기만 했다.
    from types import SimpleNamespace as NS

    from app.tools import news_tool as NT

    def row(i, title):
        return NS(title=title, source="s", published_at=None, url=f"u{i}", content="c")

    rows = [row(1, "시황 기사"), row(2, "시황 기사2"), row(3, "카카오 실적"), row(4, "시황 기사3"), row(5, "카카오 주가")]
    limits = []

    class Q:
        def filter(self, *a):
            return self

        def order_by(self, *a):
            return self

        def limit(self, n):
            limits.append(n)
            return self

        def all(self):
            return rows

    class Db:
        def query(self, *a):
            return Q()

        def close(self):
            pass

    company = NS(id=1, name="카카오", asset_type="stock")
    monkeypatch.setattr(NT, "SessionLocal", lambda: Db())
    monkeypatch.setattr(NT, "_add_indirect", lambda db, company, out, urls: None)
    monkeypatch.setattr(NT, "resolve_company", lambda db, name: NS(company=company, corrected_from=None))
    out = NT.news_tool("카카오", limit=3)
    assert [n["title"] for n in out["news"]] == ["카카오 실적", "카카오 주가"]
    assert out["excluded_unrelated"] == 3
    assert limits == [3 * NT.ETF_NEWS_OVERFETCH]

