import pytest

from app.collectors import (
    NAME_COLLISION_ANCHOR,
    NAME_COLLISION_COMPANIES,
    NON_FINANCIAL_DOMAINS,
    _is_non_financial_item,
    is_non_financial_host,
    news_search_query,
)

EXPECTED_COLLISIONS = {
    "KCC", "현대모비스", "한국가스공사", "NC", "KT", "LG", "두산", "한화생명", "CJ", "SK",
    "대한항공", "하이브", "한화", "삼성생명",
}


class TestNewsSearchQuery:
    def test_collision_list_is_the_14_measured_companies(self):
        assert set(NAME_COLLISION_COMPANIES) == EXPECTED_COLLISIONS

    @pytest.mark.parametrize("name", sorted(EXPECTED_COLLISIONS))
    def test_collision_companies_are_anchored(self, name):
        assert news_search_query(name) == f"{name} {NAME_COLLISION_ANCHOR}"

    # 38->14 축소 때 제외한 종목(1~2건 우연 혼입)과 일반 종목은 회사명 단독 검색
    @pytest.mark.parametrize("name", ["삼성전자", "셀트리온", "신세계", "SK하이닉스", "SK네트웍스", "LG전자", "카카오"])
    def test_other_companies_use_plain_name(self, name):
        assert news_search_query(name) == name


class TestNonFinancialDomainFilter:
    @pytest.mark.parametrize("domain", NON_FINANCIAL_DOMAINS)
    def test_listed_domain_filtered(self, domain):
        assert is_non_financial_host(domain)

    @pytest.mark.parametrize("domain", NON_FINANCIAL_DOMAINS)
    def test_subdomain_filtered(self, domain):
        assert is_non_financial_host(f"m.{domain}")

    def test_case_insensitive(self):
        assert is_non_financial_host("Sports.Naver.COM")

    @pytest.mark.parametrize(
        "host",
        [
            "n.news.naver.com",
            "www.hankyung.com",
            "www.topstarnews.net",
            "notsports.naver.com",  # 접미사만 같은 다른 도메인은 걸리면 안 된다
            "",
            None,
        ],
    )
    def test_financial_or_unrelated_host_kept(self, host):
        assert not is_non_financial_host(host)

    def test_item_filtered_by_either_link(self):
        assert _is_non_financial_item(
            {"link": "https://n.news.naver.com/a/1", "originallink": "https://www.basketkorea.com/news/1"}
        )
        assert _is_non_financial_item(
            {"link": "https://m.sports.naver.com/basketball/1", "originallink": "https://www.hankyung.com/a"}
        )

    def test_item_kept_when_both_links_financial(self):
        assert not _is_non_financial_item(
            {"link": "https://n.news.naver.com/a/1", "originallink": "https://www.hankyung.com/a"}
        )

    def test_item_missing_links_kept(self):
        assert not _is_non_financial_item({})
