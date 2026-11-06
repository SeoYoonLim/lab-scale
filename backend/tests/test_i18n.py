"""다국어(2026-11-05)의 DB 없는 부분: locale 결정, 고정 문구, 금액 표기, 보호 번역(가짜 LLM), 외국 문자 제거의 locale별 동작,
답변 다국어 처리 순서, 리서치 API의 locale 전달, 진단·용어 사전·브리핑·실질 노출 문장의 숫자 보존."""

import re

import pytest
from fastapi.testclient import TestClient

import app.agent as agent
import app.api.research as research_api
import app.diagnosis as d
import app.i18n_translate as tr
from app import answer_i18n, briefing_i18n, diagnosis_i18n, exposure_i18n, glossary, glossary_i18n, i18n
from app.answer_guard import strip_foreign_script
from app.disclaimer import DISCLAIMER
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
LOCALES = ("en-US", "ja-JP", "zh-CN")
NUM_RE = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?")
HANGUL_RE = re.compile(r"[가-힣]")


def nums(text: str) -> list[str]:
    return sorted(n.lstrip("+").rstrip(",") for n in NUM_RE.findall(text))


class TestLocale:
    @pytest.mark.parametrize("value, expected", [
        ("ko-KR", "ko-KR"), ("en-US", "en-US"), ("ja-JP", "ja-JP"), ("zh-CN", "zh-CN"),
        ("EN-us", "en-US"), ("en_US", "en-US"), ("en", "en-US"), ("ja", "ja-JP"), ("zh", "zh-CN"), ("zh-Hans", "zh-CN"),
        ("zh-TW", None), ("zh-Hant", None), ("fr-FR", None), ("", None), (None, None), ("x" * 30, None),
    ])
    def test_normalize(self, value, expected):
        assert i18n.normalize_locale(value) == expected

    def test_resolve_order(self):
        assert i18n.resolve_locale(None) == "ko-KR"  # 기본
        assert i18n.resolve_locale(None, None) == "ko-KR"
        assert i18n.resolve_locale(None, "ja-JP") == "ja-JP"  # 계정 설정
        assert i18n.resolve_locale("en-US", "ja-JP") == "en-US"  # 요청이 먼저
        assert i18n.resolve_locale("fr-FR", "ja-JP") == "ko-KR"  # 지원하지 않는 요청 값은 ko-KR(설정으로 넘어가지 않는다)
        assert i18n.resolve_locale("   ", "zh-CN") == "zh-CN"  # 빈 값은 요청이 없는 것
        assert i18n.resolve_locale(None, "fr-FR") == "ko-KR"

    def test_korean_texts_equal_existing_constants(self):
        assert i18n.disclaimer("ko-KR") == DISCLAIMER
        assert i18n.text("fallback_answer", "ko-KR") == agent.FALLBACK_ANSWER
        assert i18n.text("timeout_answer", "ko-KR") == agent.LLM_TIMEOUT_ANSWER
        assert i18n.text("no_holdings", "ko-KR") == d.NO_HOLDINGS_MESSAGE
        for key, entry in i18n.TEXTS.items():
            for loc in LOCALES:
                assert entry.get(loc), (key, loc)
                assert not HANGUL_RE.search(entry[loc]), (key, loc)

    @pytest.mark.parametrize("parts, locale, won, expected", [
        ([("1,414,000", "")], "en-US", True, "KRW 1,414,000"),
        ([("1", "조"), ("2,000", "억")], "en-US", True, "KRW 1.2 trillion"),
        ([("5,000", "억")], "en-US", True, "KRW 500 billion"),
        ([("3", "만")], "en-US", False, "30,000"),
        ([("-1,234", "억")], "en-US", True, "-KRW 123.4 billion"),
        ([("5,000", "억")], "ja-JP", True, "5,000億ウォン"),
        ([("1", "조"), ("2,000", "억")], "ja-JP", True, "1兆2,000億ウォン"),
        ([("1.2", "조")], "zh-CN", True, "1.2万亿韩元"),
        ([("3", "만")], "zh-CN", False, "3万"),
    ])
    def test_money_text_keeps_value(self, parts, locale, won, expected):
        assert i18n.money_text(parts, locale, won) == expected


def fake_llm(transform):
    """자리표시자를 지키면서 한글 낱말을 바꿔 주는 가짜 번역 LLM."""
    calls = []

    def chat(messages):
        calls.append(messages)
        return transform(messages[-1]["content"] if messages[-1]["role"] == "user" and len(messages) == 2 else messages[1]["content"])

    chat.calls = calls
    return chat


EN = lambda s: HANGUL_RE.sub("", re.sub(r"[가-힣]+", "word", s))  # noqa: E731
JA = lambda s: re.sub(r"[가-힣]+", "です", s)  # noqa: E731
ZH = lambda s: re.sub(r"[가-힣]+", "上涨", s)  # noqa: E731
SOURCE = ("SK하이닉스(000660) · 향후 5거래일 · 방향: 상승 쪽(신뢰도 낮음)\n\n"
          "SK하이닉스의 2026-11-03 종가는 1,414,000원으로, 20거래일간 -10.34%입니다. 영업이익 1조 2,000억원.\n"
          "- 기사: [HBM 수요 3배 확대](https://example.com/a?x=1) · 답변 생성 2026-11-05 10:00 KST")
TERMS = ["SK하이닉스", "HBM 수요 3배 확대"]


class TestProtectedTranslation:
    def test_values_names_titles_links_restored_exactly(self, monkeypatch):
        monkeypatch.setattr(tr, "_chat", fake_llm(JA))
        r = tr.translate(SOURCE, "ja-JP", TERMS)
        assert r.status == tr.STATUS_TRANSLATED and r.failures == []
        for keep in ("SK하이닉스", "000660", "2026-11-03", "-10.34%", "HBM 수요 3배 확대", "https://example.com/a?x=1",
                     "2026-11-05 10:00", "1,414,000ウォン", "1兆2,000億ウォン"):
            assert keep in r.text, keep
        assert r.text.endswith(i18n.text("translation_note", "ja-JP"))

    def test_numbers_multiset_is_unchanged(self, monkeypatch):
        """ja-JP는 금액 단위를 같은 한자로 쓰므로 숫자 문자열이 원문과 하나도 다르지 않아야 한다."""
        monkeypatch.setattr(tr, "_chat", fake_llm(JA))
        body = tr.translate(SOURCE, "ja-JP", TERMS).text.removesuffix("\n\n" + i18n.text("translation_note", "ja-JP"))
        assert nums(body) == nums(SOURCE)

    def test_english_money_value_is_preserved(self, monkeypatch):
        monkeypatch.setattr(tr, "_chat", fake_llm(EN))
        r = tr.translate(SOURCE, "en-US", TERMS)
        assert r.status == tr.STATUS_TRANSLATED
        assert "KRW 1,414,000" in r.text and "KRW 1.2 trillion" in r.text
        assert not HANGUL_RE.search(r.text.replace("SK하이닉스", "").replace("HBM 수요 3배 확대", ""))

    @pytest.mark.parametrize("bad, problem", [
        (lambda s: JA(s).replace("⟦0⟧", "", 1), "placeholders"),  # 자리표시자를 빠뜨림
        (lambda s: JA(s) + " 7", "digits outside placeholders"),  # 숫자를 지어냄
        (lambda s: JA(s).replace("です", "입니다", 1), "hangul left"),  # 덜 번역함
        (lambda s: EN(s), "no kana"),  # 요청 언어가 아님
    ])
    def test_violations_retry_then_fall_back_to_korean(self, monkeypatch, bad, problem):
        llm = fake_llm(bad)
        monkeypatch.setattr(tr, "_chat", llm)
        r = tr.translate(SOURCE, "ja-JP", TERMS)
        assert r.status == tr.STATUS_FALLBACK
        assert len(llm.calls) == tr.MAX_ATTEMPTS and any(problem in f for f in r.failures)
        assert r.text == f"{i18n.text('translation_fallback', 'ja-JP')}\n\n{SOURCE}"  # 한국어 원문 그대로

    def test_llm_error_falls_back(self, monkeypatch):
        monkeypatch.setattr(tr, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("down")))
        r = tr.translate(SOURCE, "zh-CN", TERMS)
        assert r.status == tr.STATUS_FALLBACK and r.text.endswith(SOURCE)

    def test_check_rules(self):
        src = "⟦0⟧의 종가는 ⟦1⟧입니다.\n# 제목"
        assert tr.check(src, "⟦0⟧ closed at ⟦1⟧.\n# Title", "en-US") == []
        assert "heading count" in tr.check(src, "⟦0⟧ closed at ⟦1⟧. Title", "en-US")
        assert "cjk in english" in tr.check(src, "⟦0⟧ closed at ⟦1⟧ 市場.\n# Title", "en-US")
        assert "not simplified chinese" in tr.check(src, "⟦0⟧のです⟦1⟧。\n# 标题", "zh-CN")
        assert tr.check(src, "⟦0⟧收盘价为⟦1⟧。\n# 标题", "zh-CN") == []
        assert "placeholders 3/2" in tr.check(src, "⟦0⟧⟦0⟧ ⟦1⟧ x\n# t", "en-US")

    def test_nothing_to_translate_skips_llm(self, monkeypatch):
        monkeypatch.setattr(tr, "_chat", lambda m: pytest.fail("LLM을 부르면 안 됨"))
        assert tr.translate("KODEX 200 (069500) 1.2%", "en-US").text == "KODEX 200 (069500) 1.2%"
        assert tr.translate("SK하이닉스 000660", "en-US", ["SK하이닉스"]).text.startswith("SK하이닉스 000660")

    def test_protected_terms_from_records(self):
        records = [{"result": {"company_name": "삼성전자", "news": [{"title": "삼성전자, 3분기 영업이익"}],
                               "disclosures": [{"title": "주요사항보고서"}]}}]
        terms = tr.protected_terms(records, ["NAVER"])
        assert set(terms) == {"삼성전자", "삼성전자, 3분기 영업이익", "주요사항보고서", "NAVER"}
        assert terms[0] == "삼성전자, 3분기 영업이익"  # 긴 것부터


class TestForeignScriptByLocale:
    REC = [{"result": {"company_name": "SK하이닉스", "news": [{"title": "韓 반도체"}]}}]

    def test_korean_default_is_unchanged(self):
        assert strip_foreign_script("SK하이닉스는 상승 쪽입니다. 市場 점유율", self.REC, "SK하이닉스 전망") == ("SK하이닉스는 상승 쪽입니다.", 1)
        assert strip_foreign_script("韓 반도체 기사입니다.", self.REC, "SK하이닉스 전망") == ("韓 반도체 기사입니다.", 0)

    def test_japanese_answer_is_kept(self):
        text = "SKハイニックス(SK하이닉스)の株価は上昇寄りです。"
        assert strip_foreign_script(text, self.REC, "SK하이닉스 전망", "ja-JP") == (text, 0)

    def test_chinese_keeps_han_removes_kana(self):
        assert strip_foreign_script("SK하이닉스近期股价偏上涨。", self.REC, "SK하이닉스 전망", "zh-CN") == ("SK하이닉스近期股价偏上涨。", 0)
        assert strip_foreign_script("股价上涨。これは", self.REC, "q", "zh-CN") == ("", 1)

    def test_english_keeps_hangul_names_removes_unknown_cjk(self):
        assert strip_foreign_script("SK하이닉스 shows an upward bias.", self.REC, "What about it?", "en-US") == (
            "SK하이닉스 shows an upward bias.", 0)
        assert strip_foreign_script("Up. 市場 share rose.", self.REC, "q", "en-US") == ("Up.", 1)
        assert strip_foreign_script("韓 chip news.", self.REC, "q", "en-US") == ("韓 chip news.", 0)  # 근거에 있는 표기


FIXED = {"fallback_answer": agent.FALLBACK_ANSWER, "timeout_answer": agent.LLM_TIMEOUT_ANSWER}


class TestAnswerLocalization:
    @pytest.fixture(autouse=True)
    def no_db_names(self, monkeypatch):
        monkeypatch.setattr(answer_i18n, "_company_names", lambda: ("SK하이닉스",))

    def test_fixed_notices(self, monkeypatch):
        monkeypatch.setattr(tr, "_chat", lambda m: pytest.fail("LLM을 부르면 안 됨"))
        for loc in LOCALES:
            assert answer_i18n.localize_answer("q", agent.LLM_TIMEOUT_ANSWER, [], loc, FIXED) == (
                i18n.text("timeout_answer", loc), "localized")

    @pytest.mark.parametrize("question", ["PER이 뭐야?", "PER과 PBR 차이가 뭐야?", "PBR이 1보다 낮으면 저평가야?", "블록딜이 뭐야?"])
    def test_glossary_uses_native_definitions(self, monkeypatch, question):
        monkeypatch.setattr(tr, "_chat", lambda m: pytest.fail("LLM을 부르면 안 됨"))
        korean = glossary.concept_answer(question)
        for loc in LOCALES:
            text, status = answer_i18n.localize_answer(question, korean, [], loc, FIXED)
            assert status == "localized" and text == glossary_i18n.concept_answer(question, loc)

    def test_other_answers_are_translated(self, monkeypatch):
        monkeypatch.setattr(tr, "_chat", fake_llm(ZH))
        text, status = answer_i18n.localize_answer("SK하이닉스 전망", "SK하이닉스는 5거래일 상승 쪽입니다.", [], "zh-CN", FIXED)
        assert status == "translated" and text.startswith("SK하이닉스") and "5" in text

    def test_ask_question_korean_path_is_untouched(self, monkeypatch):
        saved = []
        monkeypatch.setattr(agent, "_answer", lambda q, m, p: ({"answer": "한국어 답", "used_tools": [], "sources": []}, []))
        monkeypatch.setattr(agent, "save_report_row", lambda *a, **k: saved.append(k) or 9)
        monkeypatch.setattr(answer_i18n, "localize_answer", lambda *a, **k: pytest.fail("ko-KR은 다국어 처리를 하지 않음"))
        for kwargs in ({}, {"locale": None}, {"locale": "ko-KR"}):
            assert agent.ask_question("q", user_id=1, **kwargs)["answer"] == "한국어 답"
        assert saved == [{"previous_report_id": None, "user_id": 1}] * 3

    def test_ask_question_other_locale_saves_original(self, monkeypatch):
        saved = []
        monkeypatch.setattr(agent, "_answer", lambda q, m, p: ({"answer": "한국어 답", "used_tools": [], "sources": []}, []))
        monkeypatch.setattr(agent, "save_report_row", lambda *a, **k: saved.append((a, k)) or 9)
        monkeypatch.setattr(answer_i18n, "localize_answer", lambda q, a, r, loc, fixed: (f"[{loc}] answer", "translated"))
        out = agent.ask_question("q", user_id=1, locale="en-US")
        assert out["answer"] == "[en-US] answer" and out["report_id"] == 9
        (args, kwargs), = saved
        assert args[1] == "[en-US] answer"
        assert kwargs == {"previous_report_id": None, "user_id": 1, "locale": "en-US", "original_content": "한국어 답",
                          "translation_status": "translated"}


class TestResearchRouteLocale:
    @pytest.fixture
    def calls(self, monkeypatch):
        seen = []

        def fake(question, **kw):
            seen.append(kw)
            return {"answer": "a", "used_tools": [], "sources": [], "report_id": 1, "previous_report_id": None}

        monkeypatch.setattr(research_api, "ask_question", fake)
        return seen

    def _login(self, preferred=None):
        from app.api.deps import get_current_user
        from app.auth import CurrentUser

        user = CurrentUser(id=5, username="u", preferred_locale=preferred or "ko-KR")
        app.dependency_overrides[get_current_user] = lambda: user

    def teardown_method(self):
        from app.api.deps import get_current_user

        app.dependency_overrides.pop(get_current_user, None)

    @pytest.mark.parametrize("body_locale, preferred, expected", [
        (None, None, None), ("ko-KR", None, None), ("fr-FR", "en-US", None),
        ("en-US", None, "en-US"), ("ja", None, "ja-JP"), (None, "zh-CN", "zh-CN"), ("ko-KR", "ja-JP", None),
    ])
    def test_locale_passed_only_when_not_korean(self, calls, body_locale, preferred, expected):
        self._login(preferred)
        body = {"question": "PER이 뭐야?", **({"locale": body_locale} if body_locale else {})}
        r = client.post("/api/research", json=body)
        assert r.status_code == 200
        assert calls[-1] == ({"user_id": 5} if expected is None else {"user_id": 5, "locale": expected})
        assert r.json()["disclaimer"] == (DISCLAIMER if expected is None else i18n.disclaimer(expected))
        assert set(r.json()) == {"answer", "used_tools", "sources", "report_id", "previous_report_id", "disclaimer"}

    def test_followup_of_translated_record_uses_korean_original(self, calls, monkeypatch):
        self._login("en-US")
        monkeypatch.setattr(research_api, "get_report", lambda report_id, user_id: {
            "report_id": 3, "question": "q", "answer": "English answer", "original_answer": "한국어 원문", "company_name": None})
        client.post("/api/research", json={"question": "왜?", "previous_report_id": 3})
        assert calls[-1]["previous"]["answer"] == "한국어 원문" and calls[-1]["locale"] == "en-US"

    def test_locale_too_long_is_422(self, calls):
        self._login()
        assert client.post("/api/research", json={"question": "q", "locale": "x" * 21}).status_code == 422

    def test_disclaimer_endpoint(self):
        assert client.get("/api/disclaimer").json() == {"text": DISCLAIMER}
        assert client.get("/api/disclaimer", params={"locale": "fr-FR"}).json() == {"text": DISCLAIMER}
        for loc in LOCALES:
            assert client.get("/api/disclaimer", params={"locale": loc}).json() == {"text": i18n.disclaimer(loc)}


H = d.HoldingInput
CASES = [
    (1_000_000, [H("005930", "삼성전자", "KOSPI", 100, 100_000.0, 100_000.0, True, -2.5)]),
    (3_000_000, [H("005930", "삼성전자", "KOSPI", 20, 100_000.0, 100_000.0, True, 1.2),
                 H("000660", "SK하이닉스", "KOSPI", 10, 200_000.0, 210_000.0, False, 4.0),
                 H("247540", "에코프로비엠", "KOSDAQ", 20, 100_000.0, 80_000.0, True, None)]),
    (9_000_000, [H("122630", "KODEX 레버리지", "ETF", 10, 20_000.0, 18_000.0, True, -3.0, "etf"),
                 H("035720", "카카오", "KOSPI", 20, 45_000.0, None, False, None)]),
    (500_000, [H("252670", "KODEX 200선물인버스2X", "ETF", 100, 3_000.0, 2_500.0, True, 6.0, "etf"),
               H("035420", "NAVER", "KOSPI", 5, 200_000.0, 150_000.0, True, -8.0),
               H("051910", "LG화학", "KOSPI", 3, 300_000.0, 320_000.0, True, 2.0)]),
]
TEXT_FIELDS = ("summary", "strengths", "risks", "suggestions", "notes")


def _text(res: dict) -> str:
    return " ".join([res["summary"], *res["strengths"], *res["risks"], *res["suggestions"], *res["notes"],
                     *(f["message"] for f in res["flags"])])


class TestDiagnosisI18n:
    @pytest.fixture(autouse=True)
    def no_llm(self, monkeypatch):
        monkeypatch.setattr(d, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("no llm")))  # 규칙 기반

    @pytest.mark.parametrize("case", range(len(CASES)))
    def test_same_values_and_same_sentence_counts(self, case):
        ko = d.build_diagnosis(*CASES[case])
        for loc in LOCALES:
            res = d.build_diagnosis(*CASES[case], locale=loc)
            for key in ("source", "metrics", "holdings"):
                assert res[key] == ko[key], (loc, key)
            assert [(f["code"], f["value"], f["threshold"], f.get("ticker")) for f in res["flags"]] == [
                (f["code"], f["value"], f["threshold"], f.get("ticker")) for f in ko["flags"]]
            for key in ("strengths", "risks", "suggestions", "notes"):
                assert len(res[key]) == len(ko[key]), (loc, key)
            assert nums(_text(res)) == nums(_text(ko)), loc  # 문장 속 숫자가 하나도 다르지 않다
            assert res["disclaimer"] == i18n.disclaimer(loc)
            names = {h.company_name for h in CASES[case][1]}
            leftover = _text(res)
            for n in sorted(names, key=len, reverse=True):
                leftover = leftover.replace(n, "")
            assert not HANGUL_RE.search(leftover), (loc, leftover[:200])  # 한글은 공식 종목명뿐

    def test_korean_is_unchanged(self):
        for cash, holdings in CASES:
            a, b = d.build_diagnosis(cash, holdings), d.build_diagnosis(cash, holdings, locale="ko-KR")
            a.pop("generated_at"), b.pop("generated_at")
            assert a == b

    def test_llm_summary_translated_or_rule_based_fallback(self, monkeypatch):
        import json

        monkeypatch.setattr(d, "_chat", lambda m: json.dumps({"summary": "현금 비중을 확인했습니다.", "suggestions": ["종목별 뉴스를 점검"]},
                                                           ensure_ascii=False))
        monkeypatch.setattr(tr, "_chat", fake_llm(EN))
        res = d.build_diagnosis(*CASES[0], locale="en-US")
        assert res["source"] == "llm" and not HANGUL_RE.search(res["summary"]) and res["notes"] == []
        monkeypatch.setattr(tr, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("down")))
        res = d.build_diagnosis(*CASES[0], locale="en-US")
        assert res["source"] == "rule_based" and res["model"] is None
        assert res["notes"][-1] == diagnosis_i18n.T["en-US"]["n_translate_fail"]


class TestGlossaryI18n:
    def test_every_term_in_every_language(self):
        assert set(glossary_i18n.DEFINITIONS) == set(glossary.GLOSSARY)
        for term, by_loc in glossary_i18n.DEFINITIONS.items():
            assert set(by_loc) == set(LOCALES), term

    def test_no_new_numbers_in_definitions(self):
        """번역 정의에 한국어 정의에 없는 숫자가 없다(새 수치를 지어내지 않았다)."""
        for term, by_loc in glossary_i18n.DEFINITIONS.items():
            korean = set(nums(glossary.GLOSSARY[term][1]))
            for loc, (_, definition) in by_loc.items():
                extra = set(nums(definition)) - korean - {"1980", "4", "1", "100"}
                assert not extra, (term, loc, extra)

    @pytest.mark.parametrize("question", ["PER이 뭐야?", "공매도가 뭐야?", "PER과 PBR 차이", "삼성전자 PER은?", "오늘 PER 높은 종목 추천해줘",
                                          "블록딜이 뭐야?", "ETF가 뭐야?"])
    def test_same_routing_as_korean(self, question):
        for loc in LOCALES:
            assert (glossary.concept_answer(question) is None) == (glossary_i18n.concept_answer(question, loc) is None)


class TestBriefingAndExposureI18n:
    FLAGS = [
        {"type": "concentration", "scope": "holding", "ticker": "005930", "name": "삼성전자", "text": "k", "value": 45.5,
         "threshold": 30.0, "unit": "%", "date": "2026-11-04", "direction": None},
        {"type": "day_move", "scope": "holding", "ticker": "005930", "name": "삼성전자", "text": "k", "value": -3.48,
         "threshold": 3.0, "unit": "%", "date": "2026-11-04", "direction": "down"},
        {"type": "volume_spike", "scope": "watchlist", "ticker": "000660", "name": "SK하이닉스", "text": "k", "value": 2.31,
         "threshold": 2.0, "unit": "배", "date": "2026-11-04", "direction": None},
        {"type": "new_disclosure", "scope": "watchlist", "ticker": "000660", "name": "SK하이닉스", "text": "k", "value": 2.0,
         "threshold": 1.0, "unit": "건", "date": "2026-11-03", "direction": None},
    ]

    def test_briefing_flags_keep_values(self):
        from app.briefing import EMPTY_MESSAGE, NO_ACCOUNT_MESSAGE

        brief = {"stale_notice": "마지막 데이터는 2026-11-03 기준입니다.", "message": EMPTY_MESSAGE,
                 "market": {"kospi": {"name": "코스피", "close": 1}, "usd_krw": None},
                 "portfolio": {"message": NO_ACCOUNT_MESSAGE}, "holdings": [{"name": "삼성전자", "flags": self.FLAGS[:2], "notice": None}],
                 "watchlist": [{"name": "KODEX 레버리지", "flags": self.FLAGS[2:], "notice": "k"}], "highlights": self.FLAGS,
                 "disclaimer": DISCLAIMER}
        out = briefing_i18n.localize(brief, "en-US")
        texts = [f["text"] for f in out["highlights"]]
        assert texts[0] == "삼성전자 makes up 45.50% of total assets (threshold 30% or more)."
        assert texts[1] == "삼성전자's closing price on 2026-11-04 fell -3.48% from the previous trading day."
        assert "2.31x" in texts[2] and "2 filing(s)" in texts[3]
        assert [f["unit"] for f in out["highlights"]] == ["%", "%", "배", "건"]  # unit은 응답 스키마의 코드값이라 그대로
        assert out["stale_notice"] == "The latest data is as of 2026-11-03." and out["market"]["kospi"]["name"] == "KOSPI"
        assert "KRW 10,000,000" in out["portfolio"]["message"] and out["watchlist"][0]["notice"].startswith("This is a leveraged ETF")
        assert out["disclaimer"] == i18n.disclaimer("en-US") and brief["highlights"][0]["text"] == "k"  # 원본은 그대로
        from app.api.briefing import Flag

        for loc in LOCALES:  # 응답 모델 검증을 통과한다(코드값 필드를 바꾸지 않았다)
            for f in briefing_i18n.localize(brief, loc)["highlights"]:
                Flag(**f)

    def test_exposure_messages(self):
        result = {
            "message": None, "companies": [{"name": "삼성전자", "code": "005930", "effective_weight_pct": 40.0, "direct_weight_pct": 20.0}],
            "overlaps": [], "flags": [
                {"kind": "concentration", "name": "삼성전자", "code": "005930", "value_pct": 40.0, "threshold_pct": 30.0, "message": "k"},
                {"kind": "etf_inflates_weight", "name": "삼성전자", "code": "005930", "value_pct": 20.0, "threshold_pct": 10.0, "message": "k"}],
            "data_warnings": [
                {"kind": "older_price", "message": "KODEX 200(069500)의 최신 종가는 2026-10-30 기준입니다(다른 종목은 2026-11-03)."},
                {"kind": "etf_weight_under_100", "message": "KODEX 200의 구성종목 비중 합계가 97.50%입니다. 나머지 2.50%(25,000원)는 '기타(현금·파생 등)'로 뒀습니다."},
                {"kind": "no_price", "message": "알 수 없는 형식"}],
            "decomposed_etfs": [{"stale_notice": "KODEX 200의 구성종목 기준일은 2026-10-30입니다."}],
            "undecomposed_etfs": [{"reason_code": "leveraged_inverse", "reason": "k"}], "disclaimer": "k",
        }
        out = exposure_i18n.localize(result, "ja-JP")
        assert out["flags"][0]["message"] == "삼성전자の実質比率が保有評価額の40.00%です(基準30%以上)。"
        assert "40.00%" in out["flags"][1]["message"] and "20.00%" in out["flags"][1]["message"]
        assert out["data_warnings"][0]["message"] == "KODEX 200(069500)の最新終値は2026-10-30時点です(他の銘柄は2026-11-03)。"
        assert "97.50%" in out["data_warnings"][1]["message"] and "25,000ウォン" in out["data_warnings"][1]["message"]
        assert out["data_warnings"][2]["message"] == "알 수 없는 형식"  # 패턴이 다르면 값을 추측하지 않고 그대로 둔다
        assert out["decomposed_etfs"][0]["stale_notice"] == "KODEX 200の構成銘柄の基準日は2026-10-30です。"
        assert out["undecomposed_etfs"][0]["reason"].startswith("レバレッジ")
        assert out["disclaimer"].endswith(i18n.disclaimer("ja-JP"))
