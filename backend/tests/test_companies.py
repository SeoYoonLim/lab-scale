"""app.companies 단위 테스트. _search_targets/_matches는 DB 없는 순수 로직이고,
search_companies(DB 조회 전체)는 dev DB로 검증한다(tests/test_companies_devdb.py)."""

from app.companies import _matches, _search_targets
from app.models import Company


def company(ticker, name):
    return Company(id=1, ticker=ticker, name=name)


class TestSearchTargets:
    def test_no_query_is_none(self):
        assert _search_targets(None) is None

    def test_blank_query_is_none(self):
        assert _search_targets("   ") is None

    def test_plain_query_is_itself_normalized(self):
        assert _search_targets("삼성 전자") == {"삼성전자"}

    def test_alias_query_also_includes_registered_name(self):
        assert _search_targets("네이버") == {"네이버", "naver"}

    def test_alias_lookup_is_case_and_space_insensitive(self):
        assert _search_targets(" 네 이 버 ") == {"네이버", "naver"}

    def test_non_alias_query_has_only_itself(self):
        assert _search_targets("SK") == {"sk"}


class TestMatches:
    def test_substring_match_on_name(self):
        assert _matches(company("005930", "삼성전자"), {"삼성"}) is True

    def test_substring_match_on_ticker(self):
        assert _matches(company("005930", "삼성전자"), {"5930"}) is True

    def test_no_match(self):
        assert _matches(company("005930", "삼성전자"), {"카카오"}) is False

    def test_alias_target_matches_registered_english_name(self):
        assert _matches(company("035420", "NAVER"), {"네이버", "naver"}) is True

    def test_case_insensitive(self):
        assert _matches(company("035420", "NAVER"), {"naver"}) is True
