from datetime import datetime, timezone

from app.embeddings import MAX_TEXT_LEN, _disclosure_text, _news_text
from app.models import Company, Disclosure, News


def disclosure(title="증권발행실적보고서", disclosed_at=None, content=None, company="삼성전자"):
    return Disclosure(title=title, disclosed_at=disclosed_at, content=content, company=Company(name=company))


def test_disclosure_text_includes_company_title_and_kst_date():
    # DART 접수일은 KST 자정으로 저장되고 DB에서 UTC 전날 15:00으로 읽힌다 -> KST 날짜로 돌려놔야 한다
    row = disclosure(disclosed_at=datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc))
    assert _disclosure_text(row) == "삼성전자 증권발행실적보고서 (2026-07-24)"


def test_same_title_different_company_or_date_gives_different_text():
    d1 = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)
    d2 = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)
    texts = {
        _disclosure_text(disclosure(company="삼성전자", disclosed_at=d1)),
        _disclosure_text(disclosure(company="카카오", disclosed_at=d1)),
        _disclosure_text(disclosure(company="삼성전자", disclosed_at=d2)),
    }
    assert len(texts) == 3


def test_disclosure_without_date():
    assert _disclosure_text(disclosure()) == "삼성전자 증권발행실적보고서"


def test_disclosure_content_is_not_embedded():
    # 원문을 붙이면 중복이 안 줄고 검색 적중이 떨어졌다(_disclosure_text docstring 참고)
    assert _disclosure_text(disclosure(content="가" * 1000)) == _disclosure_text(disclosure(content=None))


def test_disclosure_text_truncated_to_max_len():
    assert len(_disclosure_text(disclosure(title="가" * 1000))) == MAX_TEXT_LEN


def test_news_text_unchanged():
    assert _news_text(News(title="제목", content="본문")) == "제목\n본문"
    assert _news_text(News(title="제목", content=None)) == "제목"
