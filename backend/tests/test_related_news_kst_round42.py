"""2026-11-11(42차): 간접 뉴스(업종·기초지수) 시각을 KST 문자열로.

실데이터 검증(검증 DB, KODEX 200 보유 계정의 일일 브리핑)에서 holdings.items[].indirect_news.items[].published_at과
sources[].date만 '2026-11-09T08:12:00+00:00'(UTC)로 나왔다. 직접 뉴스는 39차부터 KST다. 같은 함수 결과를 news_tool이 LLM 근거로도
넘기므로, KST 00~09시 기사는 앞 10자(날짜)가 하루 전으로 보였다.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from app import related_news


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, n):
        return self.rows[:n]


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *cols):
        return _Q(self.rows)


def test_indirect_news_published_at_is_kst():
    etf = SimpleNamespace(name="KODEX 200", ticker="069500", asset_type="etf")
    assert related_news.index_rule(etf) is not None  # 기초지수 규칙을 타는 ETF
    label = related_news.index_rule(etf)[0]
    # 2026-11-09 08:12 UTC = 17:12 KST, 2026-11-08 18:30 UTC = 2026-11-09 03:30 KST(UTC 날짜로는 하루 전)
    rows = [(f"{label} 지수 사상 최고치", "https://example.com/a", "www.example.com", datetime(2026, 11, 9, 8, 12, tzinfo=timezone.utc)),
            (f"{label} 반등 마감", "https://example.com/b", "www.example.com", datetime(2026, 11, 8, 18, 30, tzinfo=timezone.utc))]
    section, topic, items = related_news.indirect_news(_DB(rows), etf, since=None)
    assert section == related_news.INDEX_LABEL and topic == label
    assert [n["published_at"] for n in items] == ["2026-11-09T17:12:00+09:00", "2026-11-09T03:30:00+09:00"]
    assert all(n["published_at"][:10] == "2026-11-09" for n in items)  # 앞 10자가 한국 날짜


def test_indirect_news_missing_time_stays_none():
    etf = SimpleNamespace(name="KODEX 200", ticker="069500", asset_type="etf")
    label = related_news.index_rule(etf)[0]
    _, _, items = related_news.indirect_news(_DB([(f"{label} 약세", "https://example.com/c", None, None)]), etf, since=None)
    assert items[0]["published_at"] is None
