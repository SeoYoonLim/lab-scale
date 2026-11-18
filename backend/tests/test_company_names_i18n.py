"""48차 문제 4: 일본어·중국어(그리고 영어) 종목명으로 국내 종목을 정확하게 식별한다(app/company_names_i18n.py).

수정 전 실측(2026-11-13, dev DB 읽기 전용, llama3.1:8b): 9문항 중 7건이 잘못된 종목 또는 종목 없음.
- "SK海力士最近股价怎么样？" -> SK(지주사)로 연결(질문 원문의 "SK"만 잡힘)
- "LG新能源最近的新闻" -> LG(지주사)
- "現代自動車…", "サムスン電子…", "现代汽车…", "起亚…", "Samsung Electronics…" -> 종목을 못 찾아 도구 없이 LLM이 답함
"""

import json
from pathlib import Path

import pytest

from app.company_aliases import COMPANY_ALIASES
from app.company_names_i18n import FOREIGN_NAMES, foreign_aliases
from app.tools.company_resolver import _key, extract_from_text, resolve_company

SEED = json.loads((Path(__file__).resolve().parents[1] / "scripts" / "ci_seed_companies.json").read_text(encoding="utf-8"))
SEED_ROWS = SEED if isinstance(SEED, list) else SEED.get("companies") or next(iter(SEED.values()))
SEED_NAMES = sorted({r["name"] for r in SEED_ROWS})
ALIASES = foreign_aliases()


class TestDictionary:
    def test_every_target_is_a_registered_company(self):
        assert sorted(n for n in FOREIGN_NAMES if n not in SEED_NAMES) == []

    def test_keys_are_unique_and_never_another_company_name(self):
        # 정규화가 같은 표기("Kakao Bank"·"KakaoBank")는 질문 추출용으로 둘 다 둔다. 대신 같은 종목이어야 한다
        by_key = {}
        for a, n in ALIASES.items():
            by_key.setdefault(_key(a), set()).add(n)
        assert {k: v for k, v in by_key.items() if len(v) > 1} == {}
        registered = {_key(n): n for n in SEED_NAMES}
        clash = {a: registered[_key(a)] for a, n in ALIASES.items() if _key(a) in registered and registered[_key(a)] != n}
        assert clash == {}

    def test_no_clash_with_korean_aliases(self):
        korean = {_key(a): n for a, n in COMPANY_ALIASES.items()}
        assert {a: (n, korean[_key(a)]) for a, n in ALIASES.items() if _key(a) in korean and korean[_key(a)] != n} == {}

    @pytest.mark.parametrize("ambiguous", ["Samsung", "三星", "サムスン", "Hyundai", "現代", "现代", "LG", "SK", "Lotte",
                                           "カカオ", "新世界", "Orion", "ハイブ", "キア"])
    def test_ambiguous_or_common_words_are_not_aliases(self, ambiguous):
        assert _key(ambiguous) not in {_key(a) for a in ALIASES}

    def test_duplicate_alias_for_two_companies_is_an_error(self, monkeypatch):
        import app.company_names_i18n as m

        monkeypatch.setattr(m, "FOREIGN_NAMES", {"삼성전자": ("X Corp",), "SK하이닉스": ("X Corp",)})
        with pytest.raises(ValueError):
            m.foreign_aliases()


class TestResolve:
    @pytest.mark.parametrize("alias, name", sorted(ALIASES.items()))
    def test_every_alias_resolves(self, make_db, alias, name):
        res = resolve_company(make_db(*SEED_NAMES), alias)
        assert res.company is not None and res.company.name == name

    @pytest.mark.parametrize("raw, name", [("samsung electronics", "삼성전자"), ("SK HYNIX", "SK하이닉스"), ("skhynix", "SK하이닉스"),
                                           (" 起亚 ", "기아"), ("LG新能源", "LG에너지솔루션")])
    def test_case_and_space_insensitive(self, make_db, raw, name):
        assert resolve_company(make_db(*SEED_NAMES), raw).company.name == name

    def test_strict_mode_accepts_registered_aliases(self, make_db):
        # 주문 등 엄격 모드도 등록된 별칭은 인정한다(퍼지가 아니다)
        assert resolve_company(make_db(*SEED_NAMES), "サムスン電子", allow_fuzzy=False).company.name == "삼성전자"


class TestExtractFromQuestion:
    @pytest.mark.parametrize("q, want", [
        ("SK海力士最近股价怎么样？", ["SK하이닉스"]),  # 수정 전: SK
        ("LG新能源最近的新闻", ["LG에너지솔루션"]),  # 수정 전: LG
        ("SKハイニックスの株価は？", ["SK하이닉스"]),
        ("現代自動車の最近の株価推移を教えて", ["현대차"]),
        ("サムスン電子の最近のニュースは？", ["삼성전자"]),
        ("现代汽车的股价是多少？", ["현대차"]),
        ("起亚最近怎么样？", ["기아"]),
        ("三星电子和SK海力士哪个涨得多？", ["삼성전자", "SK하이닉스"]),
        ("How has Samsung Electronics stock performed recently?", ["삼성전자"]),
        ("What is Samsung Biologics' stock price?", ["삼성바이오로직스"]),
        ("Compare Samsung Electronics and Samsung Electro-Mechanics", ["삼성전자", "삼성전기"]),
        ("Kia vs Hyundai Motor", ["기아", "현대차"]),
        ("KEPCO E&C stock price", ["한전기술"]),
        ("KODEX 200里三星电子占多少权重？", ["KODEX 200", "삼성전자"]),
    ])
    def test_foreign_names_in_questions(self, make_db, q, want):
        db = make_db(*SEED_NAMES)
        assert [c.name for c in extract_from_text(db, q)] == want

    @pytest.mark.parametrize("q", ["三星的股价", "サムスンの株価", "Samsung stock price", "現代の株価", "LGの株価は？"])
    def test_ambiguous_group_names_are_not_guessed(self, make_db, q):
        names = [c.name for c in extract_from_text(make_db(*SEED_NAMES), q)]
        assert not set(names) & {"삼성전자", "삼성전기", "삼성SDI", "현대차", "현대모비스", "LG전자", "LG화학"}

    @pytest.mark.parametrize("q", ["サムスン電子優先株の株価", "三星电子优先股的股价"])
    def test_preferred_shares_are_not_the_common_stock(self, make_db, q):
        assert "삼성전자" not in [c.name for c in extract_from_text(make_db(*SEED_NAMES), q)]

    def test_english_alias_needs_word_boundaries(self, make_db):
        assert "기아" not in [c.name for c in extract_from_text(make_db(*SEED_NAMES), "Kiaora market news")]

    def test_korean_questions_unchanged(self, make_db):
        db = make_db(*SEED_NAMES)
        assert [c.name for c in extract_from_text(db, "삼성전자와 SK하이닉스 비교")] == ["삼성전자", "SK하이닉스"]
        assert [c.name for c in extract_from_text(db, "SK 주가 알려줘")] == ["SK"]


class TestToolArgumentRepair:
    """48차 holdout(N3)에서 찾은 오매칭: "Compare Kia and Hyundai Motor"에 모델이 stock_tool 인자로 "현대모터스"를 넘겼고, 퍼지 매칭이
    현대모비스로 보정했다(질문 원문에서는 현대차를 정확히 찾았다). 보정을 거친 인자가 질문에 없는 종목이면 질문 종목으로 바꾼다."""

    @pytest.fixture
    def agent_db(self, monkeypatch, make_db):
        import app.agent as agent

        db = make_db("기아", "현대차", "현대모비스", "삼성전자", "삼성SDI")
        db.close = lambda: None
        monkeypatch.setattr(agent, "SessionLocal", lambda: db)
        return agent

    @staticmethod
    def calls(*names):
        return [{"function": {"name": "stock_tool", "arguments": {"ticker": n, "period_days": 20}}} for n in names]

    def test_fuzzy_argument_outside_the_question_is_replaced(self, agent_db):
        calls = self.calls("기아", "현대모빗스")  # 퍼지 매칭이면 현대모비스로 간다
        repaired = agent_db._repair_company_args(calls, "Compare Kia and Hyundai Motor over the last 20 trading days")
        assert [c["function"]["arguments"]["ticker"] for c in calls] == ["기아", "현대차"] and repaired == {1: "현대모빗스"}

    def test_exact_name_outside_the_question_is_left_alone(self, agent_db):
        calls = self.calls("기아", "현대모비스")  # 정확한 등록명은 모델이 의도한 종목으로 본다(보정하지 않는다)
        assert agent_db._repair_company_args(calls, "기아와 현대차 비교") == {}
        assert calls[1]["function"]["arguments"]["ticker"] == "현대모비스"

    def test_fuzzy_argument_inside_the_question_is_kept(self, agent_db):
        calls = self.calls("삼성 SDI")
        assert agent_db._repair_company_args(calls, "삼성SDI 주가") == {}

    def test_no_companies_in_question_keeps_old_behavior(self, agent_db):
        calls = self.calls("현대모빗스")
        assert agent_db._repair_company_args(calls, "그 종목 주가 알려줘") == {}

    def test_transliteration_alias(self, make_db):
        assert resolve_company(make_db("현대차", "현대모비스"), "현대모터스").company.name == "현대차"
