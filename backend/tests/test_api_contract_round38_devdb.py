"""38차(2026-11-09): 번역 상태·locale·시각·종목 매칭 계약 통합 테스트(개발 DB·CI 임시 DB 모두에서 돈다).

- 데이터는 37차 fake 픽스처가 만든 가짜 종목·ETF(이름에 캡테스트, ticker Q+16진 5자리)와 가입 fixture의 임시 사용자뿐이고 끝나면 지운다.
  수집 데이터가 없는 CI DB에서도 같은 결과라 needs_collected_data 마커를 달지 않는다(제외 상한 98 그대로).
- 외부 호출 없음: 실시간 시세는 conftest의 block_external_http가 막아 DB 종가 폴백, LLM은 _answer·번역 _chat을 가짜로 바꾼다.
"""

import re
import secrets

import pytest
from fastapi.testclient import TestClient

from app import agent, answer_i18n, i18n_translate
from app.main import app
from tests.test_api_contract_round37_devdb import DATETIME_RE, SYLLABLES, check_formats, fake, get  # noqa: F401  (fake는 픽스처)

pytestmark = pytest.mark.integration

client = TestClient(app, raise_server_exceptions=False)
UTC_RE = re.compile(r"\+00:00$")


def good_translation(messages):
    tokens = re.findall(r"⟦\d+⟧", messages[1]["content"])
    return " ".join(tokens) + " is trending upward."


def fake_answer(monkeypatch, korean):
    monkeypatch.setattr(agent, "_answer", lambda q, model, previous=None: ({"answer": korean, "used_tools": [], "sources": []}, []))


def suffix() -> str:
    return "".join(secrets.choice(SYLLABLES) for _ in range(3))


# ---------- 2·5·6·7·10: 번역 성공 POST = 기록 상세 ----------


def test_translated_post_matches_record_detail(signup, fake, monkeypatch):
    u = signup()
    co = fake()
    korean = f"{co.name}의 최근 흐름은 완만한 상승입니다."
    fake_answer(monkeypatch, korean)
    monkeypatch.setattr(answer_i18n, "_company_names", lambda: (co.name,))
    monkeypatch.setattr(i18n_translate, "_chat", good_translation)
    r = client.post("/api/research", headers=u["headers"], json={"question": f"{co.name} 어때?", "locale": "en-US"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["locale"] == "en-US" and body["translation_status"] == "translated" and body["report_id"]
    assert co.name in body["answer"] and "is trending upward" in body["answer"]

    r, rec = get(f"/api/research/{body['report_id']}?view=record", u)
    assert r.status_code == 200
    assert (rec["answer"], rec["locale"], rec["translation_status"]) == (body["answer"], body["locale"], body["translation_status"])
    assert rec["original_answer"] == korean
    # 7·10: created_at은 저장 시각(UTC 오프셋), local_date·local_time은 요청 시간대(기본 Asia/Seoul) 기준 표시값
    assert DATETIME_RE.match(rec["created_at"]) and UTC_RE.search(rec["created_at"]) and rec["timezone"] == "Asia/Seoul"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rec["local_date"]) and re.fullmatch(r"\d{2}:\d{2}", rec["local_time"])
    # 기본 상세(view 없음)는 예전 응답 그대로(locale·translation_status 없음)
    r, plain = get(f"/api/research/{body['report_id']}", u)
    assert "translation_status" not in plain and "locale" not in plain and plain["answer"] == body["answer"]


# ---------- 11·12: 미지원 locale은 한국어로 답하고 기록도 ko-KR ----------


def test_unsupported_locale_is_recorded_as_korean(signup, fake, monkeypatch):
    u = signup()
    co = fake()
    korean = f"{co.name}의 최근 흐름은 보합입니다."
    fake_answer(monkeypatch, korean)
    monkeypatch.setattr(i18n_translate, "_chat", lambda m: pytest.fail("지원하지 않는 언어는 번역하지 않는다"))
    r = client.post("/api/research", headers=u["headers"], json={"question": f"{co.name} 어때?", "locale": "fr-FR"})
    body = r.json()
    assert r.status_code == 200 and body["answer"] == korean and body["locale"] == "ko-KR" and body["translation_status"] is None
    r, rec = get(f"/api/research/{body['report_id']}?view=record", u)
    assert rec["locale"] == "ko-KR" and rec["translation_status"] is None and rec["original_answer"] is None


# ---------- 14·15·16·17: 관심종목 유사 매칭 vs 주문 엄격 매칭 ----------


def test_watchlist_corrects_name_but_order_requires_exact(signup, fake, dev_db):
    u = signup()
    co = fake(name=f"LG캡테스트{suffix()}")
    typed = "엘지" + co.name[2:]  # 한글 음차: 관심종목(기본 모드)은 LG…로 보정, 주문(엄격 모드)은 거부

    r = client.post("/api/watchlist", headers=u["headers"], json={"ticker": typed})
    assert r.status_code == 201, r.text
    item = r.json()
    # 15: 실제 등록된 종목명·코드를 돌려준다(입력과 다르면 프런트가 "LG…(으)로 등록됨"을 보여 줄 수 있다)
    assert (item["company_name"], item["ticker"], item["asset_type"]) == (co.name, co.ticker, "stock") and item["company_name"] != typed
    assert DATETIME_RE.match(item["added_at"]) and UTC_RE.search(item["added_at"])
    r, listed = get("/api/watchlist", u)
    assert [(i["ticker"], i["company_name"]) for i in listed["items"]] == [(co.ticker, co.name)]

    r = client.post("/api/portfolio/orders", headers=u["headers"], json={"ticker": typed, "side": "buy", "quantity": 1})  # 16
    assert r.status_code == 404 and isinstance(r.json()["detail"], str)
    r, trades = get("/api/portfolio/trades", u)
    assert trades["total"] == 0  # 거부된 주문은 체결·계좌에 흔적이 없다

    r = client.post("/api/portfolio/orders", headers=u["headers"], json={"ticker": co.ticker, "side": "buy", "quantity": 1})  # 17
    assert r.status_code == 200, r.text
    order = r.json()
    assert (order["ticker"], order["company_name"], order["side"], order["quantity"]) == (co.ticker, co.name, "buy", 1)
    check_formats(order)
    r, trades = get("/api/portfolio/trades", u)
    assert trades["total"] == 1 and trades["items"][0]["ticker"] == co.ticker and trades["items"][0]["asset_type"] == "stock"


# ---------- 18: ETF 주문·관심종목 ----------


def test_etf_order_and_watchlist_keep_asset_type(signup, fake):
    u = signup()
    etf = fake(asset_type="etf", name=f"KODEX 캡테스트{suffix()}")
    r = client.post("/api/portfolio/orders", headers=u["headers"],  # 엄격 모드도 ETF 브랜드 한글 표기는 인정(등록 표기와 같은 상품)
                    json={"ticker": "코덱스 " + etf.name.split(" ", 1)[1], "side": "buy", "quantity": 3})
    assert r.status_code == 200, r.text
    assert r.json()["ticker"] == etf.ticker and r.json()["company_name"] == etf.name
    r, port = get("/api/portfolio", u)
    h = next(x for x in port["holdings"] if x["ticker"] == etf.ticker)
    assert h["asset_type"] == "etf" and h["quantity"] == 3
    r, trades = get("/api/portfolio/trades", u)
    assert trades["items"][0]["asset_type"] == "etf"
    r = client.post("/api/watchlist", headers=u["headers"], json={"ticker": etf.ticker})
    assert r.status_code == 201 and r.json()["asset_type"] == "etf"
    # 이름 일부만 준 ETF는 주문하지 않는다(다른 ETF로 바뀌지 않게)
    r = client.post("/api/portfolio/orders", headers=u["headers"], json={"ticker": etf.name[:-1], "side": "buy", "quantity": 1})
    assert r.status_code == 404
