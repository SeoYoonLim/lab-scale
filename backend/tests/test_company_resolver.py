import difflib

import pytest

from app.tools.company_resolver import FUZZY_CUTOFF, _key, extract_from_text, normalize_name, resolve_company


def names(companies):
    return [c.name for c in companies]


class TestNormalizeName:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("  삼성전자  ", "삼성전자"),
            ('"삼성전자"', "삼성전자"),
            ("삼성​전자", "삼성전자"),
            ("null", ""),
            ("None", ""),
            (None, ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_name(raw) == expected


class TestResolveCompany:
    def test_exact_name(self, make_db):
        res = resolve_company(make_db("삼성전자", "SK하이닉스"), "SK하이닉스")
        assert res.company.name == "SK하이닉스"
        assert res.corrected_from is None

    def test_exact_ticker(self, make_db):
        db = make_db("삼성전자", "SK하이닉스")
        res = resolve_company(db, "000002")
        assert res.company.name == "SK하이닉스"

    @pytest.mark.parametrize("raw", ["sk하이닉스", "SK 하이닉스", " Sk 하이닉스 "])
    def test_case_and_space_insensitive(self, make_db, raw):
        res = resolve_company(make_db("삼성전자", "SK하이닉스"), raw)
        assert res.company.name == "SK하이닉스"
        assert res.corrected_from is not None

    def test_empty_input(self, make_db):
        res = resolve_company(make_db("삼성전자"), "null")
        assert res.company is None
        assert "비어" in res.message

    def test_fuzzy_at_cutoff_is_corrected(self, make_db):
        # 5자 중 1자 치환 -> ratio 정확히 0.8 (get_close_matches는 >= cutoff를 포함)
        assert difflib.SequenceMatcher(None, _key("가나다라바"), _key("가나다라마")).ratio() == FUZZY_CUTOFF
        res = resolve_company(make_db("가나다라마"), "가나다라바")
        assert res.company.name == "가나다라마"
        assert res.corrected_from == "가나다라바"

    def test_fuzzy_below_cutoff_is_not_found(self, make_db):
        # 9자 중 2자 치환 -> ratio 0.778 < 0.8
        ratio = difflib.SequenceMatcher(None, _key("가나다라마바차카자"), _key("가나다라마바사아자")).ratio()
        assert ratio < FUZZY_CUTOFF
        res = resolve_company(make_db("가나다라마바사아자"), "가나다라마바차카자")
        assert res.company is None
        assert "찾지 못했습니다" in res.message

    def test_ambiguous_fuzzy_returns_candidates(self, make_db):
        res = resolve_company(make_db("가나다라마", "가나다라사"), "가나다라바")
        assert res.company is None
        assert set(res.candidates) == {"가나다라마", "가나다라사"}

    def test_preferred_stock_not_resolved_to_common(self, make_db):
        res = resolve_company(make_db("삼성전자", "SK하이닉스"), "삼성전자우")
        assert res.company is None

    def test_lg_preferred_not_resolved_to_lg_or_lg_electronics(self, make_db):
        res = resolve_company(make_db("LG", "LG전자", "삼성전자"), "LG전자우")
        assert res.company is None

    def test_unknown_listed_company_not_matched(self, make_db):
        # few-shot 예시에 쓰는 롯데칠성은 DB에 없어야 하고, 비슷한 종목으로 보정되면 안 된다
        res = resolve_company(make_db("롯데지주", "롯데쇼핑", "롯데케미칼"), "롯데칠성")
        assert res.company is None


class TestExtractFromText:
    def test_longest_match_wins(self, make_db):
        db = make_db("카카오", "카카오뱅크")
        assert names(extract_from_text(db, "카카오뱅크 요즘 어때?")) == ["카카오뱅크"]

    def test_overlapping_names_both_present(self, make_db):
        db = make_db("카카오", "카카오뱅크")
        assert names(extract_from_text(db, "카카오뱅크랑 카카오 비교해줘")) == ["카카오뱅크", "카카오"]

    def test_order_of_appearance(self, make_db):
        db = make_db("삼성전자", "SK하이닉스")
        assert names(extract_from_text(db, "SK하이닉스랑 삼성전자 중 뭐가 나아?")) == ["SK하이닉스", "삼성전자"]

    def test_duplicates_collapsed(self, make_db):
        db = make_db("삼성전자")
        assert names(extract_from_text(db, "삼성전자 삼성전자 삼성전자")) == ["삼성전자"]

    def test_ascii_name_needs_word_boundary(self, make_db):
        db = make_db("DL", "LG")
        assert extract_from_text(db, "DLNA 기술 설명해줘") == []
        assert names(extract_from_text(db, "DL 주가")) == ["DL"]

    def test_preferred_stock_excluded(self, make_db):
        db = make_db("삼성전자")
        assert extract_from_text(db, "삼성전자우 배당 알려줘") == []

    def test_lg_preferred_not_falling_back_to_lg(self, make_db):
        db = make_db("LG", "LG전자")
        assert extract_from_text(db, "LG전자우 주가") == []

    def test_word_starting_with_u_is_not_preferred(self, make_db):
        db = make_db("삼성전자")
        assert names(extract_from_text(db, "삼성전자우수 인재 채용")) == ["삼성전자"]

    def test_ticker_match(self, make_db):
        db = make_db("삼성전자")
        assert names(extract_from_text(db, "000001 주가")) == ["삼성전자"]


class TestResolveCompanyStrict:
    """allow_fuzzy=False(주문 경로): 정확/공백·대소문자/별칭만 인정하고, 유사 이름은 보정 없이 후보 안내만 한다."""

    @pytest.mark.parametrize("raw", ["삼성전자", "005930", "삼성 전자", "삼성전자 ", "SK하이닉스", "sk 하이닉스"])
    def test_exact_ticker_and_spacing_still_resolve(self, make_db, raw):
        db = make_db("SK하이닉스", "삼성전자")
        db._companies[1].ticker = "005930"
        res = resolve_company(db, raw, allow_fuzzy=False)
        assert res.company is not None and res.company.name in ("삼성전자", "SK하이닉스")

    def test_registered_alias_still_resolves(self, make_db):
        res = resolve_company(make_db("NAVER", "삼성전자"), "네이버", allow_fuzzy=False)
        assert res.company.name == "NAVER"

    @pytest.mark.parametrize(
        "listed, raw, suggested",
        [
            # 삼성SDI만 상장돼 있을 때 삼성SDS를 치면 기본 모드는 SDI로 보정하지만 주문에서는 거부하고 안내만 한다.
            (("삼성SDI", "삼성전자"), "삼성SDS", "삼성SDI"),
            (("삼성SDS", "삼성전자"), "삼성SDI", "삼성SDS"),
            (("삼성전자", "SK하이닉스"), "삼성전자우", "삼성전자"),  # 우선주는 보통주로 취급하지 않는다
            (("가나다라마", "삼성전자"), "가나다라바", "가나다라마"),  # ratio 0.8(퍼지 임계값 경계)
        ],
    )
    def test_similar_name_is_rejected_with_candidate(self, make_db, listed, raw, suggested):
        db = make_db(*listed)
        strict = resolve_company(db, raw, allow_fuzzy=False)
        assert strict.company is None
        assert strict.candidates and strict.candidates[0] == suggested
        assert strict.message.startswith("정확한 종목을 찾지 못했습니다. 혹시 ")
        assert suggested in strict.message

    @pytest.mark.parametrize("listed, raw", [(("삼성SDI",), "삼성SDS"), (("가나다라마",), "가나다라바")])
    def test_default_mode_still_corrects_the_same_input(self, make_db, listed, raw):
        res = resolve_company(make_db(*listed), raw)  # 기본값 allow_fuzzy=True: 기존 동작 그대로
        assert res.company is not None and res.corrected_from == raw

    @pytest.mark.parametrize("listed, raw", [(("KB금융", "삼성전자"), "JB금융"), (("JB금융", "삼성전자"), "KB금융")])
    def test_kb_jb_never_cross_in_either_mode(self, make_db, listed, raw):
        # 'KB금융'/'JB금융'은 유사도 0.75라 퍼지 임계값(0.8) 미만이다. 어느 모드에서도 서로로 해석되지 않는다.
        db = make_db(*listed)
        assert resolve_company(db, raw).company is None
        assert resolve_company(db, raw, allow_fuzzy=False).company is None

    def test_both_preferred_and_common_listed_resolve_exactly(self, make_db):
        db = make_db("삼성전자", "삼성전자우")
        assert resolve_company(db, "삼성전자", allow_fuzzy=False).company.name == "삼성전자"
        assert resolve_company(db, "삼성전자우", allow_fuzzy=False).company.name == "삼성전자우"
        assert resolve_company(db, "삼성전자 우", allow_fuzzy=False).company.name == "삼성전자우"  # 공백 차이

    def test_similar_pair_never_crosses(self, make_db):
        db = make_db("삼성SDI", "삼성SDS", "KB금융", "JB금융")
        for raw, expected in [("삼성SDI", "삼성SDI"), ("삼성sds", "삼성SDS"), ("KB금융", "KB금융"), ("jb 금융", "JB금융")]:
            assert resolve_company(db, raw, allow_fuzzy=False).company.name == expected

    def test_candidates_capped_at_three(self, make_db):
        db = make_db("현대차", "현대모비스", "현대건설", "현대제철", "현대위아")
        res = resolve_company(db, "현대", allow_fuzzy=False)
        assert res.company is None and len(res.candidates) == 3
        assert "중 하나인가요?" in res.message

    def test_unrelated_input_has_no_candidates(self, make_db):
        res = resolve_company(make_db("삼성전자", "SK하이닉스"), "없는회사xyz", allow_fuzzy=False)
        assert res.company is None and res.candidates is None
        assert res.message.startswith("정확한 종목을 찾지 못했습니다.")

    def test_empty_input_message_unchanged(self, make_db):
        res = resolve_company(make_db("삼성전자"), "null", allow_fuzzy=False)
        assert res.company is None and "비어" in res.message
