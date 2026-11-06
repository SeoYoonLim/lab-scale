"""다국어(2026-11-05)를 실제 dev DB와 실제 토큰으로 검증한다. 사용자는 실제 /api/auth/signup으로 만들고(pt_ 접두어) 끝나면 지운다
(signup 픽스처: 리포트·진단은 users FK CASCADE, 관심종목·모의투자는 소유자 키로 정리). 수집 데이터(시세·뉴스)는 쓰지 않는다:
가격은 고정하고, LLM(진단 설명·번역)은 가짜로 바꾸거나 실패로 둔다.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.agent as agent
import app.diagnosis as d
import app.i18n_translate as tr
import app.portfolio as portfolio_module
from app import glossary, glossary_i18n, i18n
from app.db.session import SessionLocal
from app.disclaimer import DISCLAIMER
from app.main import app

pytestmark = pytest.mark.integration
client = TestClient(app, raise_server_exceptions=False)
HANGUL_WORD = re.compile(r"[가-힣]+")


def _prefs(user):
    r = client.get("/api/auth/preferences", headers=user["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _put(user, body):
    return client.put("/api/auth/preferences", headers=user["headers"], json=body)


def _row(sql, **params):
    db = SessionLocal()
    try:
        return db.execute(text(sql), params).mappings().one()
    finally:
        db.close()


class TestPreferences:
    def test_default_is_korean_and_seoul(self, signup):
        user = signup()
        assert _prefs(user) == {"preferred_locale": "ko-KR", "preferred_timezone": None, "effective_timezone": "Asia/Seoul",
                                "supported_locales": ["ko-KR", "en-US", "ja-JP", "zh-CN"], "default_locale": "ko-KR"}

    def test_update_persists_across_relogin(self, signup):
        user = signup()
        r = _put(user, {"preferred_locale": "en_us", "preferred_timezone": "America/New_York"})
        assert r.status_code == 200 and r.json()["preferred_locale"] == "en-US"  # 표준 표기로 저장
        login = client.post("/api/auth/login", json={"username": user["username"], "password": user["password"]})
        assert login.status_code == 200
        fresh = {"headers": {"Authorization": f"Bearer {login.json()['access_token']}"}}
        assert _prefs(fresh)["preferred_locale"] == "en-US" and _prefs(fresh)["effective_timezone"] == "America/New_York"
        # 보내지 않은 필드는 그대로, null은 기본 시간대로
        assert _put(fresh, {"preferred_locale": "ja-JP"}).json()["preferred_timezone"] == "America/New_York"
        out = _put(fresh, {"preferred_timezone": None}).json()
        assert out["preferred_locale"] == "ja-JP" and out["preferred_timezone"] is None and out["effective_timezone"] == "Asia/Seoul"

    @pytest.mark.parametrize("body", [{"preferred_locale": "fr-FR"}, {"preferred_locale": "zh-TW"}, {"preferred_timezone": "Mars/Base"},
                                      {"preferred_locale": "x" * 21}])
    def test_invalid_values_are_422_and_not_saved(self, signup, body):
        user = signup()
        r = _put(user, body)
        assert r.status_code == 422
        assert "input" not in json.dumps(r.json())  # /api/auth 검증 오류는 입력값을 되돌려주지 않는다
        assert _prefs(user)["preferred_locale"] == "ko-KR"

    def test_requires_login_and_only_changes_own_settings(self, signup):
        me, other = signup(), signup()
        assert client.get("/api/auth/preferences").status_code == 401
        assert client.put("/api/auth/preferences", json={"preferred_locale": "en-US"}).status_code == 401
        assert _put(me, {"preferred_locale": "zh-CN"}).status_code == 200
        assert _prefs(other)["preferred_locale"] == "ko-KR"
        assert _row("SELECT preferred_locale FROM users WHERE id = :id", id=other["id"])["preferred_locale"] == "ko-KR"

    def test_account_timezone_is_used_for_history_dates(self, signup):
        user = signup()
        _put(user, {"preferred_timezone": "UTC"})
        assert client.get("/api/history/search", headers=user["headers"]).json()["timezone"] == "UTC"
        assert client.get("/api/history/search", headers=user["headers"], params={"timezone": "Asia/Tokyo"}).json()["timezone"] == "Asia/Tokyo"


class TestResearchLocale:
    def test_glossary_answer_in_each_language_and_saved_with_locale(self, signup):
        user = signup()
        question = "PER이 뭐야?"
        korean = glossary.concept_answer(question)
        ko = client.post("/api/research", headers=user["headers"], json={"question": question}).json()
        assert ko["answer"] == korean and ko["disclaimer"] == DISCLAIMER
        for loc in ("en-US", "ja-JP", "zh-CN"):
            body = client.post("/api/research", headers=user["headers"], json={"question": question, "locale": loc}).json()
            assert body["answer"] == glossary_i18n.concept_answer(question, loc) and body["disclaimer"] == i18n.disclaimer(loc)
            row = _row("SELECT locale, content, original_content, translation_status, summary FROM research_report WHERE id = :id",
                       id=body["report_id"])
            assert dict(row) == {"locale": loc, "content": body["answer"], "original_content": korean, "translation_status": "localized",
                                 "summary": row["summary"]}
            detail = client.get(f"/api/research/{body['report_id']}", headers=user["headers"], params={"view": "record"}).json()
            assert (detail["locale"], detail["translation_status"], detail["original_answer"]) == (loc, "localized", korean)
        ko_row = _row("SELECT locale, original_content, translation_status FROM research_report WHERE id = :id", id=ko["report_id"])
        assert dict(ko_row) == {"locale": "ko-KR", "original_content": None, "translation_status": None}
        items = client.get("/api/history/search", headers=user["headers"], params={"type": "research"}).json()["items"]
        assert sorted(i["locale"] for i in items) == ["en-US", "ja-JP", "ko-KR", "zh-CN"]

    def test_account_locale_applies_and_unsupported_falls_back_to_korean(self, signup):
        user = signup()
        _put(user, {"preferred_locale": "ja-JP"})
        body = client.post("/api/research", headers=user["headers"], json={"question": "공매도가 뭐야?"}).json()
        assert body["answer"] == glossary_i18n.concept_answer("공매도가 뭐야?", "ja-JP")
        body = client.post("/api/research", headers=user["headers"], json={"question": "공매도가 뭐야?", "locale": "fr-FR"}).json()
        assert body["answer"] == glossary.concept_answer("공매도가 뭐야?") and body["disclaimer"] == DISCLAIMER

    def test_changing_language_never_rewrites_past_records(self, signup):
        user = signup()
        first = client.post("/api/research", headers=user["headers"], json={"question": "ETF가 뭐야?"}).json()
        before = dict(_row("SELECT question, summary, content, locale, created_at FROM research_report WHERE id = :id", id=first["report_id"]))
        _put(user, {"preferred_locale": "zh-CN"})
        client.post("/api/research", headers=user["headers"], json={"question": "PER이 뭐야?"})
        after = dict(_row("SELECT question, summary, content, locale, created_at FROM research_report WHERE id = :id", id=first["report_id"]))
        assert after == before and after["locale"] == "ko-KR"
        detail = client.get(f"/api/research/{first['report_id']}", headers=user["headers"], params={"view": "record"}).json()
        assert detail["answer"] == first["answer"] and detail["locale"] == "ko-KR"

    def test_translated_code_answer_keeps_numbers_and_followup_uses_korean(self, signup, monkeypatch):
        """전망 같은 코드 답(여기서는 고정)을 가짜 번역 LLM으로 옮긴다. 숫자·종목명·제목은 원문 그대로, 후속 질문 맥락은 한국어 원문."""
        user = signup()
        korean = ("SK하이닉스(000660) · 향후 5거래일 · 방향: 상승 쪽(신뢰도 낮음)\n\n"
                  "SK하이닉스의 2026-11-03 종가는 1,414,000원으로, 20거래일간 -10.34%입니다.\n- 기사: HBM 수요 3배 확대")
        records = [{"tool_name": "news_tool", "arguments": {}, "called_at": None, "elapsed_ms": 0,
                    "result": {"company_name": "SK하이닉스", "found": True,
                               "news": [{"title": "HBM 수요 3배 확대", "company_name": "SK하이닉스", "url": "https://e.x/1"}]}}]
        seen = []

        def fake_answer(question, model, previous=None):
            seen.append(previous)
            return {"answer": korean, "used_tools": ["news_tool"], "sources": []}, records

        monkeypatch.setattr(agent, "_answer", fake_answer)
        monkeypatch.setattr(tr, "_chat", lambda m: HANGUL_WORD.sub("word", m[1]["content"]))
        body = client.post("/api/research", headers=user["headers"], json={"question": "SK하이닉스 전망", "locale": "en-US"}).json()
        for keep in ("SK하이닉스", "000660", "2026-11-03", "KRW 1,414,000", "-10.34%", "HBM 수요 3배 확대"):
            assert keep in body["answer"], keep
        row = _row("SELECT content, original_content, translation_status FROM research_report WHERE id = :id", id=body["report_id"])
        assert row["original_content"] == korean and row["translation_status"] == "translated" and row["content"] == body["answer"]
        # 같은 기록의 후속 질문: 맥락은 번역 전 한국어 답변
        client.post("/api/research", headers=user["headers"], json={"question": "왜?", "previous_report_id": body["report_id"], "locale": "en-US"})
        assert seen[-1]["answer"] == korean
        # 번역 LLM이 실패하면 한국어 원문 + 안내
        monkeypatch.setattr(tr, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("down")))
        body = client.post("/api/research", headers=user["headers"], json={"question": "SK하이닉스 전망", "locale": "ja-JP"}).json()
        assert body["answer"] == f"{i18n.text('translation_fallback', 'ja-JP')}\n\n{korean}"
        assert _row("SELECT translation_status FROM research_report WHERE id = :id", id=body["report_id"])["translation_status"] == "fallback_original"

    def test_other_users_translated_record_is_404(self, signup):
        me, other = signup(), signup()
        body = client.post("/api/research", headers=other["headers"], json={"question": "PER이 뭐야?", "locale": "en-US"}).json()
        assert client.get(f"/api/research/{body['report_id']}", headers=me["headers"], params={"view": "record"}).status_code == 404
        r = client.post("/api/research", headers=me["headers"], json={"question": "왜?", "previous_report_id": body["report_id"], "locale": "en-US"})
        assert r.status_code == 404


PRICE = 1000.0


@pytest.fixture
def fixed_price(monkeypatch):
    def price(db, company):
        return {"current_price": PRICE, "change_amount": 0.0, "change_pct": 0.0, "as_of": "t", "is_realtime": True, "source": "naver"}

    monkeypatch.setattr(portfolio_module, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "get_realtime_price_data", price)
    monkeypatch.setattr(d, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("no llm")))  # 규칙 기반 문장


class TestDiagnosisLocale:
    def test_numbers_identical_across_languages_and_locale_saved(self, signup, fixed_price):
        user = signup()
        for ticker, qty in (("005930", 30), ("000660", 5)):
            r = client.post("/api/portfolio/orders", headers=user["headers"], json={"ticker": ticker, "side": "buy", "quantity": qty})
            assert r.status_code == 200, r.text
        bodies = {}
        for loc in ("ko-KR", "en-US", "ja-JP", "zh-CN"):
            r = client.post("/api/portfolio/diagnosis", headers=user["headers"], params={} if loc == "ko-KR" else {"locale": loc})
            assert r.status_code == 200, r.text
            bodies[loc] = r.json()
        ko = bodies["ko-KR"]
        for loc, body in bodies.items():
            assert set(body) == set(ko)  # 응답 키는 언어와 무관
            assert body["metrics"] == ko["metrics"] and body["holdings"] == ko["holdings"]
            assert [(f["code"], f["value"], f["threshold"]) for f in body["flags"]] == [(f["code"], f["value"], f["threshold"]) for f in ko["flags"]]
            saved = _row("SELECT result FROM portfolio_diagnosis WHERE id = :id", id=body["diagnosis_id"])["result"]
            assert saved.get("locale") == (None if loc == "ko-KR" else loc)  # ko-KR 저장본은 예전 형태 그대로
            assert body["disclaimer"] == i18n.disclaimer(loc)
        for loc in ("en-US", "ja-JP", "zh-CN"):  # 규칙 기반 요약에는 종목명이 없어 한글이 하나도 없어야 한다
            assert not HANGUL_WORD.search(bodies[loc]["summary"]), bodies[loc]["summary"]
        hist = client.get("/api/portfolio/diagnosis/history", headers=user["headers"], params={"view": "record"}).json()["items"]
        titles = {i["locale"]: i["title"] for i in hist}
        assert titles == {"ko-KR": "삼성전자 외 1종목 진단", "en-US": "Diagnosis: 삼성전자 and 1 more",
                          "ja-JP": "삼성전자ほか1銘柄の診断", "zh-CN": "삼성전자等2只股票诊断"}
        # 저장본은 그 언어 그대로 다시 열린다(언어 설정을 바꿔도 같다)
        _put(user, {"preferred_locale": "en-US"})
        again = client.get(f"/api/portfolio/diagnosis/{ko['diagnosis_id']}", headers=user["headers"]).json()
        assert again["summary"] == ko["summary"] and again["disclaimer"] == DISCLAIMER
        compare = client.get(f"/api/portfolio/diagnosis/{bodies['zh-CN']['diagnosis_id']}/compare", headers=user["headers"],
                             params={"view": "record"}).json()
        assert compare["basis"]["note"].startswith("Each diagnosis")  # 계정 설정(en-US)

    def test_no_holdings_message_localized(self, signup):
        user = signup()
        assert client.post("/api/portfolio/diagnosis", headers=user["headers"]).json() == {"detail": d.NO_HOLDINGS_MESSAGE}
        r = client.post("/api/portfolio/diagnosis", headers=user["headers"], params={"locale": "en-US"})
        assert r.status_code == 400 and r.json() == {"detail": i18n.text("no_holdings", "en-US")}


class TestBriefingExposureLocale:
    def test_messages_localized_and_korean_unchanged(self, signup):
        from app.briefing import EMPTY_MESSAGE, NO_ACCOUNT_MESSAGE
        from app.exposure import MSG_NO_HOLDINGS

        user = signup()
        ko = client.get("/api/briefing", headers=user["headers"]).json()
        assert ko["message"] == EMPTY_MESSAGE and ko["portfolio"]["message"] == NO_ACCOUNT_MESSAGE and ko["disclaimer"] == DISCLAIMER
        en = client.get("/api/briefing", headers=user["headers"], params={"locale": "en-US"}).json()
        assert en["message"].startswith("Add stocks") and "KRW 10,000,000" in en["portfolio"]["message"]
        assert (en["data_date"], en["stale"], en["market"].keys()) == (ko["data_date"], ko["stale"], ko["market"].keys())
        assert client.get("/api/portfolio/exposure", headers=user["headers"]).json()["message"] == MSG_NO_HOLDINGS
        zh = client.get("/api/portfolio/exposure", headers=user["headers"], params={"locale": "zh-CN"}).json()
        assert zh["message"].startswith("当前没有持仓") and zh["disclaimer"].endswith(i18n.disclaimer("zh-CN"))
