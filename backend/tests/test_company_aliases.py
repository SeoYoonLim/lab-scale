import pytest

from app.company_aliases import COMPANY_ALIASES
from app.models import Company
from app.tools.company_resolver import _key, extract_from_text, resolve_company

REQUIRED = {"네이버": "NAVER", "포스코홀딩스": "POSCO홀딩스", "현대자동차": "현대차"}
ALL_TARGETS = sorted(set(COMPANY_ALIASES.values()))


class TestDictionaryIntegrity:
    def test_required_aliases_present(self):
        for alias, name in REQUIRED.items():
            assert COMPANY_ALIASES[alias] == name

    def test_keys_are_unique_after_normalization(self):
        # 정규화(공백 제거+소문자)하면 같아지는 키가 둘이면 뒤엣것이 앞엣것을 덮어쓴다
        keys = [_key(a) for a in COMPANY_ALIASES]
        assert len(keys) == len(set(keys))

    def test_no_empty_or_self_referencing_entries(self):
        for alias, name in COMPANY_ALIASES.items():
            assert alias.strip() and name.strip()
            assert _key(alias) != _key(name)


class TestResolveAlias:
    @pytest.mark.parametrize("alias, name", sorted(COMPANY_ALIASES.items()))
    def test_every_alias_resolves_to_its_registered_name(self, make_db, alias, name):
        res = resolve_company(make_db(*ALL_TARGETS), alias)
        assert res.company is not None and res.company.name == name
        assert res.corrected_from == alias

    @pytest.mark.parametrize("alias, name", REQUIRED.items())
    def test_required_three(self, make_db, alias, name):
        res = resolve_company(make_db("삼성전자", name, "카카오"), alias)
        assert res.company.name == name

    @pytest.mark.parametrize(
        "raw, name",
        [
            ("네이 버", "NAVER"),
            (" 네이버 ", "NAVER"),
            ('"네이버"', "NAVER"),
            ("포스코 홀딩스", "POSCO홀딩스"),
            ("현대 자동차", "현대차"),
            ("kai", "한국항공우주"),
            ("lg cns", "LG씨엔에스"),
            ("LG CNS", "LG씨엔에스"),
            ("lgcns", "LG씨엔에스"),
            ("sm 엔터", "에스엠"),
            ("Skt", "SK텔레콤"),
        ],
    )
    def test_alias_is_case_and_space_insensitive(self, make_db, raw, name):
        assert resolve_company(make_db(*ALL_TARGETS), raw).company.name == name

    def test_alias_beats_wrong_fuzzy_match(self, make_db):
        # 별칭 도입 전에는 퍼지 매칭이 다른 종목으로 잘못 연결했다: 삼성SDS -> 삼성SDI, KB금융지주 -> JB금융지주
        db = make_db("삼성SDI", "삼성에스디에스", "JB금융지주", "KB금융", "주성엔지니어링", "삼성E&A")
        assert resolve_company(db, "삼성SDS").company.name == "삼성에스디에스"
        assert resolve_company(db, "KB금융지주").company.name == "KB금융"
        assert resolve_company(db, "삼성엔지니어링").company.name == "삼성E&A"

    def test_alias_whose_target_is_not_in_db_falls_through_to_not_found(self, make_db):
        res = resolve_company(make_db("삼성전자", "카카오"), "네이버")
        assert res.company is None and "찾지 못했습니다" in res.message

    def test_unknown_name_is_still_not_found(self, make_db):
        assert resolve_company(make_db(*ALL_TARGETS), "롯데칠성").company is None

    def test_ambiguous_names_are_deliberately_not_aliased(self, make_db):
        # '포스코'는 POSCO홀딩스/포스코퓨처엠/포스코인터내셔널에 걸쳐서 별칭으로 정하지 않았다
        assert "포스코" not in COMPANY_ALIASES
        assert resolve_company(make_db("POSCO홀딩스", "포스코퓨처엠", "포스코인터내셔널"), "포스코").company is None


class TestExistingBehaviorUnchanged:
    def test_exact_registered_name_wins_over_alias(self, make_db):
        # DB에 '네이버'라는 이름의 종목이 따로 있다면 별칭이 아니라 그 종목이 나와야 한다(정확 일치가 먼저)
        res = resolve_company(make_db("네이버", "NAVER"), "네이버")
        assert res.company.name == "네이버" and res.corrected_from is None

    def test_exact_name_and_ticker_still_resolve_without_correction(self, make_db):
        db = make_db("NAVER", "삼성SDI", "삼성에스디에스")
        for raw, name in [("NAVER", "NAVER"), ("삼성SDI", "삼성SDI"), ("삼성에스디에스", "삼성에스디에스"), ("000002", "삼성SDI")]:
            res = resolve_company(db, raw)
            assert res.company.name == name and res.corrected_from is None

    def test_case_space_and_fuzzy_paths_still_work(self, make_db):
        db = make_db("삼성전자", "SK하이닉스", "삼성화재")
        res = resolve_company(db, "sk 하이닉스")  # 공백/대소문자 무시 일치
        assert res.company.name == "SK하이닉스" and res.corrected_from == "sk 하이닉스"
        res = resolve_company(make_db("한화에어로스페이스", "삼성화재"), "한화에어로스패이스")  # 오타는 퍼지로 보정
        assert res.company.name == "한화에어로스페이스"
        # 잘린 이름은 다른 종목일 수 있어 기존 설계대로 보정하지 않는다
        assert resolve_company(make_db("한화에어로스페이스", "삼성화재"), "한화에어로스페이").company is None

    def test_preferred_stock_still_not_resolved_to_common(self, make_db):
        assert resolve_company(make_db("삼성전자"), "삼성전자우").company is None


class TestExtractFromTextAlias:
    def test_alias_in_question_finds_company(self, make_db):
        assert [c.name for c in extract_from_text(make_db("NAVER", "카카오"), "네이버 최근 뉴스 알려줘")] == ["NAVER"]

    def test_alias_with_particle(self, make_db):
        assert [c.name for c in extract_from_text(make_db("NAVER"), "네이버가 오늘 왜 올랐어?")] == ["NAVER"]

    def test_order_of_appearance_with_alias_and_real_name(self, make_db):
        db = make_db("카카오", "NAVER")
        assert [c.name for c in extract_from_text(db, "카카오랑 네이버 주가 비교해줘")] == ["카카오", "NAVER"]
        assert [c.name for c in extract_from_text(db, "네이버랑 카카오 주가 비교해줘")] == ["NAVER", "카카오"]

    def test_longest_real_name_beats_shorter_alias(self, make_db):
        # '한전'은 한국전력의 별칭이지만 '한전KPS' 질문에서는 한전KPS만 나와야 한다
        db = make_db("한국전력", "한전KPS", "한전기술")
        assert [c.name for c in extract_from_text(db, "한전KPS 주가 알려줘")] == ["한전KPS"]
        assert [c.name for c in extract_from_text(db, "한전기술 주가 알려줘")] == ["한전기술"]
        assert [c.name for c in extract_from_text(db, "한전 주가 알려줘")] == ["한국전력"]

    def test_real_name_and_its_alias_in_same_text_are_counted_once(self, make_db):
        db = make_db("삼성바이오로직스")
        assert [c.name for c in extract_from_text(db, "삼성바이오로직스 삼성바이오 같은 회사")] == ["삼성바이오로직스"]

    def test_ascii_alias_needs_word_boundary(self, make_db):
        db = make_db("한국항공우주", "에스엠")
        assert extract_from_text(db, "KAIST 졸업생") == []
        assert [c.name for c in extract_from_text(db, "KAI 최근 주가")] == ["한국항공우주"]
        assert extract_from_text(db, "SMART 스마트팩토리") == []

    def test_no_alias_no_change(self, make_db):
        assert [c.name for c in extract_from_text(make_db("삼성전자", "NAVER"), "삼성전자 뉴스")] == ["삼성전자"]
        assert extract_from_text(make_db("삼성전자"), "오늘 날씨 어때?") == []


class TestFollowUpRepairWithAlias:
    def test_previous_question_with_alias_repairs_garbled_company(self, monkeypatch, make_db):
        import app.agent as agent

        class DB:
            def __init__(self):
                self.inner = make_db("NAVER", "카카오")

            def query(self, model):
                return self.inner.query(model)

            def close(self):
                pass

        monkeypatch.setattr(agent, "SessionLocal", DB)
        calls = [{"function": {"name": "news_tool", "arguments": {"company_name": "깨진이름"}}}]
        agent._repair_company_args(calls, "그럼 최근 뉴스는?", "네이버 최근 주가 어때?")
        assert calls[0]["function"]["arguments"]["company_name"] == "NAVER"


@pytest.mark.integration
class TestAgainstDevDB:
    def test_every_alias_target_is_a_registered_company_name(self, dev_db):
        from sqlalchemy import text

        registered = {r[0] for r in dev_db.execute(text("SELECT name FROM company"))}
        assert sorted(set(COMPANY_ALIASES.values()) - registered) == []

    def test_no_alias_key_shadows_a_real_company_name_or_ticker(self, dev_db):
        from sqlalchemy import text

        real = set()
        for name, ticker in dev_db.execute(text("SELECT name, ticker FROM company")):
            real |= {_key(name), _key(ticker)}
        assert sorted(a for a in COMPANY_ALIASES if _key(a) in real) == []

    @pytest.mark.parametrize("alias, name", REQUIRED.items())
    def test_tools_accept_the_alias(self, dev_db, alias, name):
        from app.tools.disclosure_tool import disclosure_tool
        from app.tools.market_tool import market_tool
        from app.tools.news_tool import news_tool
        from app.tools.stock_tool import stock_tool

        for r in (stock_tool(alias, 3), news_tool(alias, 3), disclosure_tool(alias, 3), market_tool(alias, 3)):
            assert r["found"] is True, r
            assert r["company_name"] == name
            assert r.get("corrected_from") == alias
