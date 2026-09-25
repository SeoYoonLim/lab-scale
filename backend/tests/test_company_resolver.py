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
