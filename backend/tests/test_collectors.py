import pytest

from app.collectors import (
    NAME_COLLISION_ANCHOR,
    NAME_COLLISION_COMPANIES,
    NEWS_QUERY_NAMES,
    NON_FINANCIAL_DOMAINS,
    _is_non_financial_item,
    _prefer_relevant,
    fetch_naver_news,
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

    @pytest.mark.parametrize("name", sorted(EXPECTED_COLLISIONS - set(NEWS_QUERY_NAMES)))
    def test_collision_companies_are_anchored(self, name):
        assert news_search_query(name) == f"{name} {NAME_COLLISION_ANCHOR}"

    def test_tuned_queries_only_cover_the_four_measured_names(self):
        assert set(NEWS_QUERY_NAMES) == {"NAVER", "SK", "GS", "LG"}
        assert {n: news_search_query(n) for n in NEWS_QUERY_NAMES} == NEWS_QUERY_NAMES

    # 계열사·같은 이름 일부인 종목은 조정의 영향을 받지 않는다
    @pytest.mark.parametrize("name", ["SK이노베이션", "SK스퀘어", "GS건설", "GS리테일", "LG에너지솔루션", "LG화학", "NHN"])
    def test_tuned_queries_do_not_affect_affiliates(self, name):
        assert news_search_query(name) == name

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


class _Co:
    name, ticker, asset_type = "SK", "034730", "stock"


class TestPreferRelevant:
    def _item(self, title):
        return {"title": title, "description": "주가"}

    def test_relevant_articles_survive_the_cut_in_original_order(self):
        items = [self._item("SK하이닉스 급등"), self._item("SK 지주 실적"), self._item("SK이노베이션 하락"), self._item("SK 주가 상승")]
        kept = _prefer_relevant(items, 2, _Co, ["SK", "SK하이닉스", "SK이노베이션"])
        assert [i["title"] for i in kept] == ["SK 지주 실적", "SK 주가 상승"]

    def test_fills_with_the_rest_up_to_display(self):
        items = [self._item("SK하이닉스 급등"), self._item("SK 지주 실적")]
        kept = _prefer_relevant(items, 5, _Co, ["SK", "SK하이닉스"])
        assert [i["title"] for i in kept] == ["SK 지주 실적", "SK하이닉스 급등"]


def test_fetch_naver_news_counts_requests(monkeypatch):
    import app.collectors as C

    class Resp:
        status_code = 200

        def json(self):
            return {"items": [{"title": "x"}]}

        def raise_for_status(self):
            pass

    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "x")
    monkeypatch.setattr(C.requests, "get", lambda *a, **k: Resp())
    before = C.naver_requests
    assert fetch_naver_news("SK") == [{"title": "x"}]
    assert C.naver_requests == before + 1
