"""37차(2026-11-08): 프런트엔드 연동을 가정한 API 계약 통합 테스트(개발 DB·CI 임시 DB 모두에서 돈다).

- 데이터는 이 파일이 만든 가짜 종목·ETF(captest_r37 이름, ticker Q+16진 5자리)와 그 시세·ETF 구성, 가입 fixture의 임시 사용자뿐이고
  끝나면 지운다. 기존 종목·수집 데이터·다른 사용자 데이터는 읽거나 바꾸지 않는다(수집 데이터가 없는 CI DB에서도 같은 결과).
- 외부 API는 부르지 않는다: 실시간 시세(네이버)는 conftest의 block_external_http가 막아 DB 종가 폴백을 쓰고, LLM이 필요한 경로는 쓰지 않는다
  (번역 실패 폴백은 번역 호출을 실패로 바꿔 재현한다).
"""

import re
import secrets
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import agent, i18n_translate, krx_calendar
from app.briefing import KST
from app.data_status import last_business_day
from app.main import SERVER_ERROR_DETAIL, app
from app.models import Company, EtfHolding, StockPrice
from app.performance_compare import BENCHMARK_STATUSES, STATUSES

pytestmark = pytest.mark.integration

client = TestClient(app, raise_server_exceptions=False)
HANGUL = re.compile(r"[가-힣]")
SYLLABLES = "가나다라마바사아자차카타파하"
TODAY = datetime.now(KST).date()


def rand_name(prefix: str) -> str:
    return prefix + "".join(secrets.choice(SYLLABLES) for _ in range(3))


@pytest.fixture
def fake(dev_db):
    """가짜 종목·ETF를 만든다. prices: 마지막 거래일(end)까지 n거래일 종가. 끝나면 company를 지운다(시세·구성·체결·보유·관심 CASCADE)."""
    made = []

    def _company(asset_type="stock", n=30, end=None, name=None, close=10_000.0):
        ticker = ("Q" + secrets.token_hex(3)[:5]).upper()
        name = name or rand_name("캡테스트전자" if asset_type == "stock" else "KODEX 캡테스트")
        c = Company(ticker=ticker, name=name, asset_type=asset_type, market="KOSPI")
        dev_db.add(c)
        dev_db.commit()
        made.append(c.id)
        if n:
            end = end or last_business_day(TODAY)
            days = krx_calendar.trading_days(end - timedelta(days=n * 2 + 10), end)[-n:]
            for i, d in enumerate(days):
                px = close + i * 10
                dev_db.add(StockPrice(company_id=c.id, price_date=d, open_price=px, high_price=px, low_price=px, close_price=px,
                                      volume=1000 + i, change_pct=0.1))
            dev_db.commit()
        return c

    yield _company
    dev_db.rollback()
    for cid in made:
        dev_db.execute(text("DELETE FROM company WHERE id = :i"), {"i": cid})
    dev_db.commit()


def get(url, user):
    r = client.get(url, headers=user["headers"])
    return r, (r.json() if r.headers.get("content-type", "").startswith("application/json") else None)


# ---------- 값 형식 검사(금액·수익률·날짜·시각) ----------

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")
AMOUNT_KEYS = re.compile(r"(_amount|_value|_balance|^cash_balance|^total_asset|^price|_price|^close|_close|^avg_price|^current_price)$")


def check_formats(obj, path=""):  # 22
    """금액·%·날짜·시각 필드의 형식. 금액은 숫자(원), %는 숫자(퍼센트 값, 예: -3.48), 날짜는 YYYY-MM-DD, *_at은 오프셋 있는 ISO 8601."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if v is None:
                continue
            if AMOUNT_KEYS.search(k) and not isinstance(v, (dict, list, str)):
                assert isinstance(v, (int, float)) and not isinstance(v, bool), (p, v)
            if k.endswith("_pct") and not isinstance(v, (dict, list)):
                assert isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e6, (p, v)
                assert not (isinstance(v, float) and v == 0 and str(v).startswith("-")), (p, "음수 0")
            if (k.endswith("_date") or k in ("date", "as_of", "price_as_of", "local_date", "data_as_of", "holdings_as_of")) and isinstance(v, str):
                assert DATE_RE.match(v), (p, v)
            if k.endswith("_at") and isinstance(v, str):
                assert DATETIME_RE.match(v), (p, v)
            check_formats(v, p)
    elif isinstance(obj, list):
        for i, x in enumerate(obj[:20]):
            check_formats(x, f"{path}[{i}]")


# ---------- 1·2·11·14: 새 사용자의 빈 상태 ----------


def test_new_user_empty_states_are_200_or_documented_404(signup):
    u = signup()
    r, port = get("/api/portfolio", u)
    assert r.status_code == 200 and port["holdings"] == [] and port["cash_balance"] == 10_000_000.0
    r, trades = get("/api/portfolio/trades", u)
    assert r.status_code == 200 and trades["items"] == [] and trades["total"] == 0 and trades["has_next"] is False
    r, exp = get("/api/portfolio/exposure?view=look_through", u)
    assert r.status_code == 200 and exp["status"] == "empty" and exp["companies"] == [] and exp["etfs"] == []
    r, perf = get("/api/portfolio/performance?view=benchmark", u)
    assert r.status_code == 200 and perf["status"] in ("no_account", "no_trades") and perf["summary"]["period_return_pct"] is None
    r, body = get("/api/briefings/daily/current", u)  # 14: 브리핑 없음은 404 + 안내
    assert r.status_code == 404 and "generate" in body["detail"]
    for url in ("/api/briefings/daily/history", "/api/portfolio/diagnosis/history", "/api/research", "/api/history/search"):
        r, body = get(url, u)
        assert r.status_code == 200 and body["items"] == [] and body["total"] == 0 and body["has_next"] is False, url
    r, body = get("/api/watchlist", u)
    assert r.status_code == 200 and body == {"items": []}
    for b in (port, trades, exp, perf):
        check_formats(b)


# ---------- 4·5·21: 없는 종목·데이터 없는 종목·엄격 매칭 ----------


def test_unknown_stock_stock_without_prices_and_strict_orders(signup, fake):
    u = signup()
    r, body = get("/api/stocks/QZZZZZ/price-history", u)
    assert r.status_code == 404 and isinstance(body["detail"], str)
    empty = fake(n=0)
    r, body = get(f"/api/stocks/{empty.ticker}/price-history", u)
    assert r.status_code == 200 and body["items"] == [] and body["count"] == 0 and body["price_as_of"] is None
    r = client.post("/api/portfolio/orders", headers=u["headers"], json={"ticker": empty.ticker, "side": "buy", "quantity": 1})
    assert r.status_code in (400, 404, 503) and isinstance(r.json()["detail"], str)  # 가격이 없으면 체결하지 않는다
    co = fake()
    near = co.name[:-1]  # 마지막 글자를 뺀 이름: 주문은 비슷한 이름으로 체결하지 않는다(allow_fuzzy=False)
    r = client.post("/api/portfolio/orders", headers=u["headers"], json={"ticker": near, "side": "buy", "quantity": 1})
    assert r.status_code == 404 and "정확" in r.json()["detail"]
    r = client.post("/api/portfolio/orders", headers=u["headers"], json={"ticker": co.name, "side": "buy", "quantity": 1})
    assert r.status_code == 200 and r.json()["ticker"] == co.ticker
    check_formats(r.json())


# ---------- 6·9·10·11·12·13·16·18·22: 포트폴리오·노출·성과 ----------


def test_portfolio_exposure_and_performance_states(signup, fake, dev_db):
    u = signup()
    fresh = fake()
    old = fake(end=krx_calendar.trading_days(TODAY - timedelta(days=40), TODAY)[0])  # 마지막 시세가 한 달 넘게 전
    etf = fake(asset_type="etf")
    for c in (fresh, old, etf):
        r = client.post("/api/portfolio/orders", headers=u["headers"], json={"ticker": c.ticker, "side": "buy", "quantity": 2})
        assert r.status_code == 200, r.text
    r, port = get("/api/portfolio", u)
    assert r.status_code == 200 and {h["ticker"] for h in port["holdings"]} == {fresh.ticker, old.ticker, etf.ticker}
    check_formats(port)

    r, exp = get("/api/portfolio/exposure?view=look_through&limit=5", u)
    assert r.status_code == 200 and exp["status"] == "ok"
    e = next(x for x in exp["etfs"] if x["ticker"] == etf.ticker)
    assert e["status"] == "not_decomposed" and exp["summary"]["undecomposed_etf_amount"] > 0  # 9: 구성 데이터 없음
    # 6: 일부 종목만 시세가 오래됐으면 전체 stale이 아니라 data_warnings의 older_price(평가 기준일 = 가장 최근 종가일)
    assert any(w["kind"] == "older_price" and w["ticker"] == old.ticker for w in exp["data_warnings"])
    check_formats(exp)
    only_old = signup()
    assert client.post("/api/portfolio/orders", headers=only_old["headers"],
                       json={"ticker": old.ticker, "side": "buy", "quantity": 1}).status_code == 200
    r, stale = get("/api/portfolio/exposure?view=look_through", only_old)  # 보유 전체의 시세가 오래됨 -> stale + 안내 문장
    assert stale["stale"] is True and stale["price_lag_business_days"] >= 2 and isinstance(stale["stale_notice"], str)
    # 10: 구성종목 기준일이 오래됨
    as_of = krx_calendar.trading_days(TODAY - timedelta(days=60), TODAY)[0]
    dev_db.add_all([EtfHolding(company_id=etf.id, as_of_date=as_of, holding_name=fresh.name, holding_code=fresh.ticker, weight_pct=60,
                               source="captest", collected_at=datetime.now(KST)),
                    EtfHolding(company_id=etf.id, as_of_date=as_of, holding_name="기타", holding_code=None, weight_pct=40,
                               source="captest", collected_at=datetime.now(KST))])
    dev_db.commit()
    r, exp = get("/api/portfolio/exposure?view=look_through", u)
    e = next(x for x in exp["etfs"] if x["ticker"] == etf.ticker)
    assert exp["etf_holdings_stale"] is True and e["holdings_stale"] is True and e["holdings_as_of"] == as_of.isoformat()
    # 18: 영어 응답은 같은 구조, 문장만 영어
    r, en = get("/api/portfolio/exposure?view=look_through&locale=en-US", u)
    assert en["locale"] == "en-US" and set(en) == set(exp) and not HANGUL.search(en["message"] or "")
    r, fr = get("/api/portfolio/exposure?view=look_through&locale=fr-FR", u)
    assert r.status_code == 200 and fr["locale"] == "ko-KR"  # 지원하지 않는 언어는 한국어로(응답 locale로 확인)

    r, perf = get("/api/portfolio/performance?view=benchmark&period=1m", u)
    assert r.status_code == 200 and perf["status"] in STATUSES  # 12: 오늘 체결은 아직 종가가 없어 pending_only 등
    assert perf["comparison"] is None or perf["comparison"].get("status") in BENCHMARK_STATUSES
    check_formats(perf)
    r, perf_en = get("/api/portfolio/performance?view=benchmark&period=1m&locale=en-US", u)
    assert perf_en["locale"] == "en-US" and set(perf_en) == set(perf) and not HANGUL.search(perf_en["message"] or "")
    # 13: 보유와 체결 기록이 맞지 않으면 history_unavailable(계산하지 않는다)
    dev_db.execute(text("UPDATE holding SET quantity = quantity + 5 WHERE device_id = :d AND company_id = :c"),
                   {"d": f"user:{u['id']}", "c": fresh.id})
    dev_db.commit()
    r, perf = get("/api/portfolio/performance?view=benchmark", u)
    assert r.status_code == 200 and perf["status"] == "history_unavailable" and perf["history_verified"] is False
    assert perf["summary"]["period_return_pct"] is None


# ---------- 7·8: 휴장일 다음 날·달력 범위 밖(단순 시세 답) ----------


def _fixed_now(monkeypatch, now):
    monkeypatch.setattr(agent, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: now)}))


def test_quote_answer_after_holiday_and_beyond_calendar(signup, fake, monkeypatch):
    u = signup()
    co = fake(end=date(2026, 10, 8))  # 한글날(10-09 금) 전날 종가
    _fixed_now(monkeypatch, datetime(2026, 10, 12, 10, 0, tzinfo=KST))  # 7: 휴장일 다음 월요일 장중 -> 지연 아님
    r = client.post("/api/research", headers=u["headers"], json={"question": f"{co.name} 현재가 알려줘"})
    assert r.status_code == 200 and "2026-10-08" in r.json()["answer"] and "지연" not in r.json()["answer"]
    _fixed_now(monkeypatch, datetime(2027, 1, 5, 18, 0, tzinfo=KST))  # 8: 달력 범위 밖 -> calendar_unknown 문장
    r = client.post("/api/research", headers=u["headers"], json={"question": f"{co.name} 현재가 알려줘"})
    answer = r.json()["answer"]
    assert r.status_code == 200 and "확인할 수 없는 날" in answer and "최신 시세가 아닙니다" in answer


# ---------- 17: 번역 실패 폴백(fallback_summary) ----------


def test_translation_failure_returns_localized_summary_and_record_keeps_original(signup, fake, monkeypatch):
    u = signup()
    co = fake()
    korean = f"{co.name}의 최근 흐름은 완만한 상승입니다. 추가 근거는 공시를 확인하세요."
    monkeypatch.setattr(agent, "_answer", lambda q, model, previous=None: ({"answer": korean, "used_tools": [], "sources": []}, []))
    monkeypatch.setattr(i18n_translate, "_chat", lambda m: (_ for _ in ()).throw(ConnectionError("simulated")))
    r = client.post("/api/research", headers=u["headers"], json={"question": f"{co.name} 어때?", "locale": "ja-JP"})
    body = r.json()
    assert r.status_code == 200 and set(body) == {"answer", "used_tools", "sources", "report_id", "previous_report_id", "disclaimer"}
    assert korean not in body["answer"] and body["answer"].startswith("※")
    r, rec = get(f"/api/research/{body['report_id']}?view=record", u)
    assert rec["translation_status"] == "fallback_summary" and rec["locale"] == "ja-JP" and rec["original_answer"] == korean


# ---------- 14·15·사용자 분리: 일일 브리핑 ----------


def test_daily_briefing_lifecycle_isolation_and_failure(signup, monkeypatch):
    a, b = signup(), signup()
    r = client.post("/api/briefings/daily/generate", headers=a["headers"], json={"use_llm": False})
    assert r.status_code == 201
    first = r.json()
    assert first["created"] is True and first["version"] == 1 and first["summary_source"] == "code"
    assert first["market_data_status"] in ("latest", "delayed", "stale", "no_data")
    assert set(first["section_status"].values()) <= {"ok", "partial", "no_data", "empty"}  # 16: 일부 데이터 없음은 섹션별로
    check_formats(first)
    again = client.post("/api/briefings/daily/generate", headers=a["headers"], json={"use_llm": False})
    assert again.status_code == 200 and again.json()["id"] == first["id"] and again.json()["created"] is False
    r, cur = get("/api/briefings/daily/current", a)
    assert cur["id"] == first["id"]
    r, hist = get("/api/briefings/daily/history?size=1", a)
    assert hist["total"] == 1 and hist["items"][0]["id"] == first["id"] and hist["page"] == 1 and hist["has_next"] is False
    for url in (f"/api/briefings/daily/{first['id']}",):  # 다른 사용자: 없는 것과 같은 404
        assert client.get(url, headers=b["headers"]).status_code == 404
    r, other = get("/api/briefings/daily/history", b)
    assert other["total"] == 0
    # 15: 생성 중 예기치 않은 오류 -> 500 고정 문구(내부 정보 없음)
    from app import daily_briefing_db

    monkeypatch.setattr(daily_briefing_db, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("C:\\secret\\path.py")))
    r = client.post("/api/briefings/daily/generate", headers=b["headers"], json={"use_llm": False})
    assert r.status_code == 500 and r.json() == {"detail": SERVER_ERROR_DETAIL}


# ---------- 19: 페이지네이션 경계 ----------


def test_pagination_boundaries(signup, fake, monkeypatch):
    u = signup()
    co = fake()
    monkeypatch.setattr(agent, "_answer", lambda q, model, previous=None: ({"answer": "코드 답", "used_tools": [], "sources": []}, []))
    for i in range(3):
        assert client.post("/api/research", headers=u["headers"], json={"question": f"{co.name} 질문 {i}"}).status_code == 200
    r, p1 = get("/api/research?limit=2&offset=0", u)
    assert p1["total"] == 3 and len(p1["items"]) == 2 and p1["has_next"] is True and p1["total_pages"] == 2
    r, p2 = get("/api/research?limit=2&offset=2", u)
    assert len(p2["items"]) == 1 and p2["has_next"] is False
    r, far = get("/api/research?limit=2&offset=100", u)
    assert r.status_code == 200 and far["items"] == [] and far["has_next"] is False and far["total"] == 3
    r, s1 = get("/api/history/search?type=research&size=2&page=2", u)
    assert s1["page"] == 2 and len(s1["items"]) == 1 and s1["has_next"] is False and s1["total_pages"] == 2
    assert get("/api/research?limit=100", u)[0].status_code == 200 and get("/api/research?limit=101", u)[0].status_code == 422
    r = client.get("/api/companies?limit=1")
    assert r.status_code == 200 and int(r.headers["X-Total-Count"]) >= 1 and len(r.json()) == 1
