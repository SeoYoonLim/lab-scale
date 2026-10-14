"""ETF 1단계의 순수 로직(DB 없음): 브랜드 별칭/종목 해석, ETF 유니버스 생성, 수집 스크립트의 ETF 처리.
라우터는 test_etf_api.py, 실제 dev DB 동작은 test_etf_devdb.py."""

import json
import sys

import pandas as pd
import pytest

import scripts.build_etf_universe as build_etf
import scripts.collect_prices as collect_prices
import scripts.collect_universe as collect_universe
from app.company_aliases import COMPANY_ALIASES, ETF_BRANDS
from app.discovery import get_trending
from app.models import Company
from app.tools.company_resolver import _key, extract_from_text, resolve_company, rewrite_etf_brand

ETF_NAMES = [
    "KODEX 200", "KODEX 200TR", "KODEX 레버리지", "KODEX 인버스", "KODEX 미국나스닥100", "KODEX 미국S&P500",
    "TIGER 200", "TIGER 200 IT", "TIGER 미국나스닥100", "TIGER 미국S&P500", "ACE 미국S&P500", "SOL AI반도체TOP2플러스",
    "RISE 200", "PLUS 고배당주",
]
STOCK_NAMES = ["삼성전자", "카카오", "카카오뱅크", "에이스침대", "NAVER", "한화에어로스페이스"]


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, companies):
        self._companies = companies

    def query(self, model):
        assert model is Company
        return FakeQuery(self._companies)


@pytest.fixture
def db():
    """주식 + ETF가 섞인 가짜 DB(asset_type을 가진 Company)."""
    companies = [
        Company(id=i, name=n, ticker=f"{i:06d}", asset_type="etf" if n in ETF_NAMES else "stock")
        for i, n in enumerate(STOCK_NAMES + ETF_NAMES, start=1)
    ]
    return FakeDB(companies)


class TestRewriteBrand:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("코덱스200", "KODEX200"),
            ("코덱스 200", "KODEX 200"),
            ("코덱스레버리지", "KODEX레버리지"),
            ("타이거 나스닥", "TIGER 나스닥"),
            ("타이거미국나스닥100", "TIGER미국나스닥100"),
            ("에이스 미국S&P500", "ACE 미국S&P500"),
            ("쏠 AI반도체", "SOL AI반도체"),
            ("케이비스타 200", "RISE 200"),
            ("아리랑 고배당주", "PLUS 고배당주"),
            ("하나로 Fn K-반도체", "HANARO Fn K-반도체"),
            ("KODEX 200 최근 흐름", "KODEX 200 최근 흐름"),
            ("삼성전자 주가", "삼성전자 주가"),
        ],
    )
    def test_korean_brand_becomes_english(self, raw, expected):
        assert rewrite_etf_brand(raw) == expected

    def test_former_brands_become_current_names(self):
        # KBSTAR -> RISE, ARIRANG -> PLUS 로 이름이 바뀌었다. 영문 입력도 현재 이름으로 바꾼다.
        assert rewrite_etf_brand("KBSTAR 200") == "RISE 200"
        assert rewrite_etf_brand("kbstar 200") == "RISE 200"
        assert rewrite_etf_brand("ARIRANG 고배당주") == "PLUS 고배당주"
        assert rewrite_etf_brand("케이비스타 200") == "RISE 200"  # 한글 -> 영문(KBSTAR) -> 현재 이름(RISE)

    @pytest.mark.parametrize("word", ["에이스침대", "에이스침대 주가", "쏠쏠하다", "플러스알파", "아이에이스", "MYKODEX", "KODEXS"])
    def test_ordinary_words_are_not_brands(self, word):
        assert rewrite_etf_brand(word) == word

    def test_empty_and_none(self):
        assert rewrite_etf_brand("") == "" and rewrite_etf_brand(None) == ""

    def test_all_six_requested_brands_are_supported(self):
        assert {"코덱스": "KODEX", "타이거": "TIGER", "에이스": "ACE", "쏠": "SOL", "케이비스타": "KBSTAR",
                "아리랑": "ARIRANG"}.items() <= ETF_BRANDS.items()


class TestResolveEtf:
    @pytest.mark.parametrize(
        "raw, name",
        [
            ("KODEX 200", "KODEX 200"),          # 정확 일치
            ("kodex 200", "KODEX 200"),          # 대소문자
            ("KODEX200", "KODEX 200"),           # 공백 없음
            ("코덱스200", "KODEX 200"),
            ("코덱스 200", "KODEX 200"),
            ("코덱스 레버리지", "KODEX 레버리지"),
            ("코덱스인버스", "KODEX 인버스"),
            ("타이거 200", "TIGER 200"),
            ("타이거 미국나스닥100", "TIGER 미국나스닥100"),
            ("타이거 나스닥", "TIGER 미국나스닥100"),
            ("타이거나스닥", "TIGER 미국나스닥100"),
            ("TIGER 나스닥", "TIGER 미국나스닥100"),
            ("타이거 S&P500", "TIGER 미국S&P500"),
            ("코덱스 나스닥100", "KODEX 미국나스닥100"),
            ("에이스 미국S&P500", "ACE 미국S&P500"),
            ("쏠 AI반도체TOP2플러스", "SOL AI반도체TOP2플러스"),
            ("케이비스타 200", "RISE 200"),       # KBSTAR -> RISE
            ("KBSTAR 200", "RISE 200"),
            ("아리랑 고배당주", "PLUS 고배당주"),   # ARIRANG -> PLUS
            ("코덱스 200TR", "KODEX 200TR"),
        ],
    )
    def test_resolves_to_the_etf(self, db, raw, name):
        res = resolve_company(db, raw)
        assert res.company is not None and res.company.name == name, res.message

    def test_200_and_200tr_are_different_etfs(self, db):
        assert resolve_company(db, "KODEX 200").company.name == "KODEX 200"
        assert resolve_company(db, "KODEX 200TR").company.name == "KODEX 200TR"

    def test_ticker_resolves(self, db):
        etf = next(c for c in db.query(Company).all() if c.name == "KODEX 200")
        assert resolve_company(db, etf.ticker).company.name == "KODEX 200"

    @pytest.mark.parametrize("raw", ["코덱스", "타이거", "코덱스 없는상품", "에이스"])
    def test_brand_only_or_unknown_is_not_found(self, db, raw):
        assert resolve_company(db, raw).company is None


class TestStocksUnchangedByEtf:
    @pytest.mark.parametrize(
        "raw, name",
        [
            ("삼성전자", "삼성전자"),
            ("삼성 전자", "삼성전자"),
            ("삼전", "삼성전자"),
            ("카카오", "카카오"),
            ("카카오뱅크", "카카오뱅크"),
            ("카뱅", "카카오뱅크"),
            ("에이스침대", "에이스침대"),      # '에이스'로 시작하지만 ETF 브랜드가 아니다
            ("네이버", "NAVER"),
            ("한화에어로스패이스", "한화에어로스페이스"),  # 퍼지 보정이 ETF 이름으로 새지 않는다
        ],
    )
    def test_resolution_is_the_same_as_before(self, db, raw, name):
        assert resolve_company(db, raw).company.name == name

    def test_preferred_stock_is_still_not_common(self, db):
        assert resolve_company(db, "삼성전자우").company is None

    def test_stock_extraction_is_unchanged(self, db):
        assert [c.name for c in extract_from_text(db, "삼성전자 주가 알려줘")] == ["삼성전자"]
        assert [c.name for c in extract_from_text(db, "카카오랑 카카오뱅크 비교")] == ["카카오", "카카오뱅크"]
        assert [c.name for c in extract_from_text(db, "에이스침대 어때?")] == ["에이스침대"]

    def test_every_etf_alias_target_is_a_real_etf_name_form(self):
        etf_values = [v for v in COMPANY_ALIASES.values() if v.split(" ")[0] in {"KODEX", "TIGER"}]
        assert len(etf_values) == 9 and all(_key(a) != _key(v) for a, v in COMPANY_ALIASES.items())


class TestExtractEtf:
    def names(self, db, text):
        return [c.name for c in extract_from_text(db, text)]

    def test_exact_name(self, db):
        assert self.names(db, "KODEX 200 최근 흐름 알려줘") == ["KODEX 200"]

    def test_korean_brand_and_spaceless(self, db):
        assert self.names(db, "코덱스200 어때?") == ["KODEX 200"]
        assert self.names(db, "KODEX200 어때?") == ["KODEX 200"]
        assert self.names(db, "코덱스 레버리지 위험해?") == ["KODEX 레버리지"]

    def test_longer_etf_name_wins(self, db):
        assert self.names(db, "KODEX 200TR 어때?") == ["KODEX 200TR"]
        assert self.names(db, "TIGER 200 IT 어때?") == ["TIGER 200 IT"]
        assert self.names(db, "TIGER 200 어때?") == ["TIGER 200"]

    def test_alias_shorthand(self, db):
        assert self.names(db, "타이거 나스닥 어때?") == ["TIGER 미국나스닥100"]

    def test_etf_and_stock_in_one_question_keep_order(self, db):
        assert self.names(db, "삼성전자랑 코덱스200 비교해줘") == ["삼성전자", "KODEX 200"]
        assert self.names(db, "KODEX 200이랑 삼성전자 비교해줘") == ["KODEX 200", "삼성전자"]

    def test_no_false_positive_on_plain_text(self, db):
        assert self.names(db, "오늘 날씨 어때?") == []
        assert self.names(db, "쏠쏠한 수익 알려줘") == []


class TestGetTrendingAssetType:
    def test_unknown_asset_type_is_not_found_without_touching_db(self):
        # 검증이 DB 조회보다 먼저라 db를 쓰지 않는다(None을 넘겨도 예외가 없다)
        r = get_trending(None, "gainers", 5, asset_type="bond")
        assert r["found"] is False and "asset_type" in r["message"]

    def test_unknown_category_still_reported_first(self):
        assert get_trending(None, "bogus", 5)["found"] is False


class FakeListing:
    """fdr.StockListing("ETF/KR")를 흉내 낸 DataFrame."""

    @staticmethod
    def frame(rows):
        return pd.DataFrame(rows, columns=["Symbol", "Name", "MarCap"])


@pytest.fixture
def listing(monkeypatch):
    def install(rows):
        monkeypatch.setattr(build_etf.fdr, "StockListing", lambda market: FakeListing.frame(rows))

    return install


def _rows(n_filler=60, skip=()):
    """대표 5종 + 시가총액 순서가 뚜렷한 가짜 ETF n_filler개."""
    base = [
        ("069500", "KODEX 200", 900), ("360750", "TIGER 미국S&P500", 800), ("133690", "TIGER 미국나스닥100", 700),
        ("122630", "KODEX 레버리지", 100), ("114800", "KODEX 인버스", 10),
    ]
    rows = [r for r in base if r[1] not in skip]
    rows += [(f"A{i:05d}", f"ETF 가짜{i}", 600 - i) for i in range(n_filler)]
    return rows


class TestBuildEtfUniverse:
    def test_top_n_by_marcap_with_must_includes_and_exact_size(self, listing):
        listing(_rows())
        result, notes = build_etf.build_etf_universe(50)
        u = result["universe"]
        assert len(u) == 50 and result["excluded_not_in_dart"] == []
        names = [x["name"] for x in u]
        for must in ("KODEX 200", "TIGER 미국S&P500", "TIGER 미국나스닥100", "KODEX 레버리지", "KODEX 인버스"):
            assert must in names
        assert {x["name"] for x in u if x.get("must_include")} == {
            "KODEX 200", "TIGER 미국S&P500", "TIGER 미국나스닥100", "KODEX 레버리지", "KODEX 인버스"}
        # 상위권이 아닌 대표 ETF(레버리지 100, 인버스 10)를 넣느라 시가총액 하위가 빠진다
        assert "ETF 가짜0" in names and "ETF 가짜59" not in names
        assert any("뺀 ETF" in n for n in notes) and any("인버스" in n for n in notes)

    def test_entry_format_matches_the_stock_universe(self, listing):
        listing(_rows())
        x = build_etf.build_etf_universe(50)[0]["universe"][0]
        assert set(x) == {"rank", "ticker", "name", "market", "marcap", "asset_type", "must_include"}
        assert x["asset_type"] == "etf" and x["market"] is None and x["rank"] == 1
        assert isinstance(x["marcap"], int)

    def test_sorted_by_marcap_rank(self, listing):
        listing(_rows())
        ranks = [x["rank"] for x in build_etf.build_etf_universe(50)[0]["universe"]]
        assert ranks == sorted(ranks)

    def test_no_drop_when_must_includes_are_already_on_top(self, listing):
        listing([("069500", "KODEX 200", 900), ("360750", "TIGER 미국S&P500", 800), ("133690", "TIGER 미국나스닥100", 700),
                 ("122630", "KODEX 레버리지", 600), ("114800", "KODEX 인버스", 500)] + [(f"A{i:05d}", f"F{i}", 100 - i) for i in range(20)])
        result, notes = build_etf.build_etf_universe(10)
        assert len(result["universe"]) == 10 and notes == []

    def test_renamed_must_include_falls_back_to_same_ticker(self, listing):
        rows = [("069500", "KODEX 200", 900), ("360750", "TIGER 미국S&P500", 800), ("133690", "TIGER 미국나스닥100", 700),
                ("122630", "KODEX 레버리지(신)", 100), ("114800", "KODEX 인버스", 10)]
        listing(rows + [(f"A{i:05d}", f"F{i}", 50 - i) for i in range(10)])
        result, notes = build_etf.build_etf_universe(8)
        assert "KODEX 레버리지(신)" in [x["name"] for x in result["universe"]]
        assert any("같은 티커 122630" in n for n in notes)

    def test_missing_must_include_uses_closest_name_or_is_dropped(self, listing):
        rows = [r for r in _rows(10) if r[1] != "KODEX 인버스"] + [("999999", "KODEX 인버스2X", 5)]
        listing(rows)
        result, notes = build_etf.build_etf_universe(10)
        assert "KODEX 인버스2X" in [x["name"] for x in result["universe"]]
        assert any("가장 비슷한" in n for n in notes)
        listing([r for r in _rows(10) if r[1] != "KODEX 인버스"])
        result, notes = build_etf.build_etf_universe(10)
        assert len(result["universe"]) == 10 and "KODEX 인버스" not in [x["name"] for x in result["universe"]]

    def test_output_file_name_and_json(self, listing, monkeypatch, tmp_path, capsys):
        listing(_rows())
        monkeypatch.setattr(build_etf, "OUT_DIR", tmp_path)
        assert build_etf.main(["50"]) == tmp_path / "universe_etf50.json"
        data = json.loads((tmp_path / "universe_etf50.json").read_text(encoding="utf-8"))
        assert len(data["universe"]) == 50 and "대상 50개" in capsys.readouterr().out


def _write_universe(tmp_path, entries):
    (tmp_path / "u.json").write_text(json.dumps({"universe": entries}, ensure_ascii=False), encoding="utf-8")


ENTRIES = [
    {"rank": 1, "ticker": "005930", "name": "삼성전자", "market": "KOSPI", "marcap": 1},
    {"rank": 2, "ticker": "069500", "name": "KODEX 200", "market": None, "marcap": 1, "asset_type": "etf"},
]


class TestCollectScripts:
    def test_collect_universe_skips_disclosure_for_etf_only(self, monkeypatch, tmp_path):
        from app.collectors import CollectResult

        _write_universe(tmp_path, ENTRIES)
        monkeypatch.setattr(collect_universe, "UNIVERSE_DIR", tmp_path)
        news, disc = [], []
        monkeypatch.setattr(collect_universe, "fetch_and_save_news",
                            lambda name, **kw: news.append(name) or CollectResult(ok=True, saved=3, fetched=3))
        monkeypatch.setattr(collect_universe, "fetch_and_save_disclosures",
                            lambda name, **kw: disc.append(name) or CollectResult(ok=True, saved=1, fetched=1))
        monkeypatch.setattr(collect_universe.time, "sleep", lambda s: None)
        monkeypatch.setattr(sys, "argv", ["collect_universe", "--universe", "u.json", "--dry-run"])
        collect_universe.main()
        assert news == ["삼성전자", "KODEX 200"]  # 뉴스는 둘 다, 검색어는 이름 그대로
        assert disc == ["삼성전자"]                # 공시는 주식만

    def test_collect_universe_registers_with_asset_type(self, monkeypatch, tmp_path):
        from app.collectors import CollectResult

        _write_universe(tmp_path, ENTRIES)
        monkeypatch.setattr(collect_universe, "UNIVERSE_DIR", tmp_path)
        registered = []
        monkeypatch.setattr(collect_universe, "_register", lambda t, n, a="stock": registered.append((t, a)) or None)
        monkeypatch.setattr(collect_universe, "fetch_and_save_news", lambda name, **kw: CollectResult(ok=True))
        monkeypatch.setattr(collect_universe, "fetch_and_save_disclosures", lambda name, **kw: CollectResult(ok=True))
        monkeypatch.setattr(collect_universe.time, "sleep", lambda s: None)

        class NoStats:
            def execute(self, *a, **k):
                return []

            def close(self):
                pass

        monkeypatch.setattr(collect_universe, "SessionLocal", lambda: NoStats())
        monkeypatch.setattr(sys, "argv", ["collect_universe", "--universe", "u.json"])
        collect_universe.main()
        assert registered == [("005930", "stock"), ("069500", "etf")]

    def test_collect_prices_registers_etf_as_etf(self, monkeypatch, tmp_path):
        from app.collectors import CollectResult

        _write_universe(tmp_path, ENTRIES)
        monkeypatch.setattr(collect_prices, "UNIVERSE_DIR", tmp_path)
        registered = []
        monkeypatch.setattr(collect_prices, "_register", lambda t, n, a="stock": registered.append((t, a)) or None)
        monkeypatch.setattr(collect_prices, "fetch_and_save_stock", lambda *a, **k: CollectResult(ok=True, saved=1, fetched=1))
        monkeypatch.setattr(collect_prices.time, "sleep", lambda s: None)
        monkeypatch.setattr(sys, "argv", ["collect_prices", "--universe", "u.json"])
        collect_prices.main()
        assert registered == [("005930", "stock"), ("069500", "etf")]


class FakeSession:
    """get_or_create_company가 쓰는 query/add/flush만 흉내 낸 세션."""

    def __init__(self, existing=()):
        self.existing = list(existing)
        self.added = []

    def query(self, model):
        outer = self

        class Q:
            def filter(self, cond):
                return self

            def first(self):
                return outer.existing[0] if outer.existing else None

        return Q()

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


class TestGetOrCreateCompany:
    def test_new_company_defaults_to_stock(self):
        from app.collectors import get_or_create_company

        db = FakeSession()
        get_or_create_company(db, "005930", "삼성전자")
        assert db.added[0].asset_type == "stock"

    def test_new_etf_company(self):
        from app.collectors import get_or_create_company

        db = FakeSession()
        get_or_create_company(db, "0167A0", "SOL AI반도체TOP2플러스", asset_type="etf")  # 영문 섞인 티커도 허용
        assert db.added[0].asset_type == "etf" and db.added[0].ticker == "0167A0"

    def test_invalid_asset_type_is_rejected(self):
        from app.collectors import get_or_create_company

        db = FakeSession()
        with pytest.raises(ValueError, match="asset_type"):
            get_or_create_company(db, "069500", "KODEX 200", asset_type="bond")
        assert db.added == []

    def test_existing_company_keeps_its_asset_type(self):
        from app.collectors import get_or_create_company

        existing = Company(id=1, ticker="069500", name="KODEX 200", asset_type="etf")
        db = FakeSession([existing])
        assert get_or_create_company(db, "069500", "KODEX 200") is existing  # 기본값 'stock'으로 덮어쓰지 않는다
        assert existing.asset_type == "etf" and db.added == []


class TestSearchTargets:
    """GET /api/companies?q= 의 검색 키(app.companies._search_targets)."""

    def test_korean_brand_adds_the_english_key(self):
        from app.companies import _search_targets

        assert {"코덱스200", "kodex200"} <= _search_targets("코덱스200")
        assert "kodex레버리지" in _search_targets("코덱스 레버리지")

    def test_etf_alias_adds_the_registered_name(self):
        from app.companies import _search_targets

        assert _key("TIGER 미국나스닥100") in _search_targets("타이거 나스닥")

    def test_plain_stock_queries_are_unchanged(self):
        from app.companies import _search_targets

        assert _search_targets("삼성전자") == {"삼성전자"}
        assert _search_targets("네이버") == {"네이버", "naver"}
        assert _search_targets("") is None and _search_targets(None) is None
        assert _search_targets("에이스침대") == {"에이스침대"}
