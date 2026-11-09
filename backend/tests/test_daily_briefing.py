"""일일 투자 브리핑 조립·요약 검증(app/daily_briefing, app/daily_briefing_db의 LLM 검증). DB·Ollama 없이 합성 값으로 돈다.

DB 흐름(사용자 분리, 저장·이력, 중복 생성 방지, 스냅샷 보존, 401·404)은 tests/test_daily_briefing_devdb.py.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app import daily_briefing as d
from app import daily_briefing_db as s
from app.briefing import KST, Bar, BriefingInputs, DisclosureRow, Instrument, NewsRow, build_briefing
from evals.metrics import forbidden_hits

END = date(2026, 10, 30)  # 금요일
NOW = datetime(2026, 10, 30, 20, 0, tzinfo=KST)  # 장 마감 뒤 → 기대 거래일 = END
LOCALES = ("ko-KR", "en-US", "ja-JP", "zh-CN")
HANGUL = re.compile(r"[가-힣]")
KANA = re.compile(r"[぀-ヿ]")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _days(n, end=END):
    out, x = [], end
    while len(out) < n:
        if x.weekday() < 5:
            out.append(x)
        x -= timedelta(days=1)
    return list(reversed(out))


def _bars(start: float, step: float, n: int = 22, end=END, last_volume: int = 1_000_000):
    days = _days(n, end)
    return [Bar(x, start + i * step, 1_000_000 if i < n - 1 else last_volume) for i, x in enumerate(days)]


def _series(start: float, step: float, n: int = 8, end=END):
    return [(x, start + i * step) for i, x in enumerate(_days(n, end))]


HYNIX = Instrument("000660", "SK하이닉스", "stock", 10, 150_000.0)
SAMSUNG = Instrument("005930", "삼성전자", "stock", 20, 60_000.0)
KODEX = Instrument("069500", "KODEX 200", "etf", 5, 30_000.0)
NAVER = Instrument("035420", "NAVER", "stock")


def _inputs(holdings=(HYNIX, SAMSUNG), watch=(NAVER,), end=END, news=True, disclosures=True, kosdaq=True, cash=2_000_000.0):
    inp = BriefingInputs(has_account=True, cash_balance=cash, holdings=list(holdings), watchlist=list(watch))
    for inst in [*holdings, *watch]:
        inp.bars[inst.ticker] = _bars(100_000 if inst.ticker != "000660" else 160_000, 500 if inst.ticker != "035420" else -800, end=end,
                                      last_volume=3_000_000 if inst.ticker == "035420" else 1_000_000)
    at = datetime.combine(end, datetime.min.time(), KST).replace(hour=9)
    if news:
        inp.news["000660"] = [NewsRow("SK하이닉스, HBM 공급 확대 본격화", "연합", "https://n/1", at),
                              NewsRow("SK하이닉스 HBM 공급 확대 본격화했다", "뉴스1", "https://n/2", at - timedelta(hours=1)),
                              NewsRow("SK하이닉스 HBM 공급 확대 본격화", "중복", "https://n/2b", at - timedelta(hours=2)),
                              NewsRow("SK하이닉스 3분기 실적 발표", "매경", "https://n/3", at - timedelta(hours=3))]
    if disclosures:
        inp.disclosures["005930"] = [DisclosureRow("연결재무제표기준영업(잠정)실적(공정공시)", "공정공시", "https://d/1", at)]
        inp.disclosures["035420"] = [DisclosureRow("주요사항보고서(자기주식취득결정)", "주요사항", "https://d/2", at)]
    inp.kospi = _series(2500, 5, end=end)
    inp.kosdaq = _series(800, -2, end=end) if kosdaq else []
    inp.usd_krw = _series(1380, 1, end=end)
    return inp


def _facts(inp=None, now=NOW, status=None, **kw):
    inp = inp or _inputs()
    brief = build_briefing(inp, 10, now)
    held = {h.ticker for h in inp.holdings}
    return d.Facts(now=now, timezone="Asia/Seoul", brief=brief, bars=inp.bars,
                   index_series={"kospi": inp.kospi, "kosdaq": inp.kosdaq, "usd_krw": inp.usd_krw},
                   price_status=status, watch_dedup=sum(1 for w in inp.watchlist if w.ticker in held),
                   markets={"000660": "KOSPI", "005930": "KOSPI"}, known_names=["SK하이닉스", "삼성전자", "NAVER", "카카오", "현대차"], **kw)


# ---------- 영역·빈 데이터 ----------


def test_five_sections_and_freshness_first():
    c = d.render(_facts(), "ko-KR")
    assert list(c["sections"]) == list(d.SECTIONS)
    assert c["market_data_status"] == "latest" and c["data_as_of"] == "2026-10-30"
    assert c["freshness"]["headline"] == "시세는 최신 거래일(2026-10-30) 종가 기준입니다." and c["freshness"]["warning"] is False
    assert c["section_status"] == {"market": "ok", "holdings": "ok", "watchlist": "ok", "risk": "ok", "checklist": "ok"}
    json.dumps(c)  # 저장 가능한(JSON) 값만


def test_no_holdings():
    c = d.render(_facts(_inputs(holdings=())), "ko-KR")
    h, r = c["sections"]["holdings"], c["sections"]["risk"]
    assert h["status"] == "empty" and h["items"] == [] and "보유 중인 종목이 없습니다" in h["message"]
    assert r["status"] == "empty" and r["texts"] == ["보유 종목이 없어 포트폴리오 위험을 점검하지 않았습니다."]
    assert c["sections"]["watchlist"]["items"]  # 관심 종목은 그대로


def test_no_watchlist():
    w = d.render(_facts(_inputs(watch=())), "ko-KR")["sections"]["watchlist"]
    assert w["status"] == "empty" and w["message"] == "관심 종목이 없습니다. 관심 종목을 추가하면 업데이트를 볼 수 있습니다."


def test_holding_and_watchlist_are_deduplicated():
    f = _facts(_inputs(watch=(NAVER, SAMSUNG)))
    c = d.render(f, "ko-KR")
    assert [i["ticker"] for i in c["sections"]["watchlist"]["items"]] == ["035420"]
    assert c["sections"]["watchlist"]["dedup_text"] == "보유 종목과 겹치는 관심 종목 1개는 보유 종목 영역에만 표시했습니다."
    assert sorted((t["ticker"], t["scope"]) for t in d.tickers(f)) == [("000660", "holding"), ("005930", "holding"), ("035420", "watchlist")]
    only = d.render(_facts(_inputs(watch=(SAMSUNG,))), "ko-KR")["sections"]["watchlist"]
    assert only["status"] == "empty" and only["message"].startswith("관심 종목이 모두 보유 종목이라")


def test_no_news_and_no_disclosures():
    c = d.render(_facts(_inputs(news=False, disclosures=False)), "ko-KR")
    for it in c["sections"]["holdings"]["items"]:
        assert it["news"] == [] and it["news_empty_text"] == "최근 7일 직접 관련 뉴스가 없습니다."
        assert it["disclosures"] == [] and it["disclosure_empty_text"] == "최근 7일 공시가 없습니다."
    assert c["sections"]["checklist"]["status"] in ("ok", "empty")
    etf = d.render(_facts(_inputs(holdings=(KODEX,), news=False)), "ko-KR")["sections"]["holdings"]["items"][0]
    assert etf["disclosure_empty_text"] == "ETF는 공시를 수집하지 않습니다."


def test_similar_headlines_are_grouped():
    it = d.render(_facts(), "ko-KR")["sections"]["holdings"]["items"]
    hynix = next(i for i in it if i["ticker"] == "000660")
    assert [n["similar_count"] for n in hynix["news"]] == [2, 1]
    assert hynix["news"][0]["title"] == "SK하이닉스, HBM 공급 확대 본격화"  # 문장부호만 다른 제목은 기존 select_news가 지우고, 비슷한 제목은 묶는다


def test_watchlist_notable_first_and_no_update_text():
    quiet = Instrument("051910", "LG화학", "stock")
    inp = _inputs(watch=(quiet, NAVER))
    inp.bars["051910"] = _bars(100_000, 0)  # 변화 없음
    w = d.render(_facts(inp), "ko-KR")["sections"]["watchlist"]
    assert [i["ticker"] for i in w["items"]] == ["035420", "051910"]
    assert w["items"][0]["notable"] and w["items"][0]["changes"]
    assert w["items"][1]["status_text"] == "특별한 업데이트가 없습니다." and w["notable_count"] == 1


def test_since_research_counts():
    at = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
    f = _facts(research={"035420": {"at": at, "news": 4, "disclosures": 1}})
    w = d.render(f, "ko-KR")["sections"]["watchlist"]["items"][0]
    assert w["since_research"]["text"] == "마지막 리서치(2026-10-28 10:00) 이후 저장된 뉴스 4건, 공시 1건이 있습니다."


# ---------- 신선도 ----------


def test_delayed_prices_warn_first_and_in_checklist():
    now = datetime(2026, 11, 3, 20, 0, tzinfo=KST)  # 기대 거래일 11-03, 시세는 10-30(2영업일 지연)
    c = d.render(_facts(now=now, status={"status": "미수집", "detail": "장 마감 뒤 시세 수집 기록 없음"}), "ko-KR")
    assert c["market_data_status"] == "delayed" and c["freshness"]["warning"] is True
    assert c["freshness"]["headline"] == ("시세가 2영업일 지연됐습니다. 마지막 종가는 2026-10-30, 기대 거래일은 2026-11-03입니다(미수집). "
                                          "아래 숫자는 2026-10-30 종가 기준이며 오늘의 시장 상황이 아닙니다.")
    assert c["sections"]["checklist"]["items"][0]["kind"] == "data_delay"
    assert "시세가 2영업일 지연돼 위험 점검도 2026-10-30 종가 기준입니다." in c["sections"]["risk"]["cautions"]
    assert c["freshness"]["sources"]["kospi"]["lag_business_days"] == 2


def test_stale_and_no_data():
    stale = d.render(_facts(now=datetime(2026, 11, 12, 20, 0, tzinfo=KST)), "ko-KR")
    assert stale["market_data_status"] == "stale" and "5영업일을 넘었습니다" in stale["freshness"]["headline"]
    inp = BriefingInputs(has_account=True, cash_balance=1_000_000.0, holdings=[HYNIX])
    none = d.render(_facts(inp), "ko-KR")
    assert none["market_data_status"] == "no_data" and none["data_as_of"] is None
    assert none["sections"]["holdings"]["status"] == "no_data"
    assert none["sections"]["holdings"]["items"][0]["price_text"] == "시세 데이터가 없어 등락률을 계산하지 못했습니다."
    assert none["sections"]["risk"]["status"] == "no_data"
    assert none["sections"]["checklist"]["items"][0]["kind"] == "data_missing"
    assert none["summary"]["text"].startswith("시세 데이터가 없어 기준일을 정하지 못했습니다.")


def test_partial_market_data():
    c = d.render(_facts(_inputs(kosdaq=False)), "ko-KR")
    m = c["sections"]["market"]
    assert m["status"] == "partial"
    assert next(i for i in m["indices"] if i["code"] == "kosdaq")["text"] == "코스닥: 데이터 없음"
    assert "코스닥 지수 데이터가 없습니다." in c["freshness"]["notices"]


def test_older_price_for_one_item_is_noted():
    inp = _inputs()
    inp.bars["005930"] = _bars(100_000, 500, end=END - timedelta(days=1))
    c = d.render(_facts(inp), "ko-KR")
    sam = next(i for i in c["sections"]["holdings"]["items"] if i["ticker"] == "005930")
    assert "이 종목의 시세는 2026-10-29 기준으로 브리핑 기준일(2026-10-30)보다 오래됐습니다." in sam["risk_factors"]


# ---------- 위험·확인할 사항 ----------


def test_risk_reuses_diagnosis_rules():
    r = d.render(_facts(_inputs(cash=0.0)), "ko-KR")["sections"]["risk"]
    codes = {f["code"] for f in r["flags"]}
    assert "CASH_LOW" in codes and "FEW_HOLDINGS" in codes
    assert r["sector"]["status"] == "unavailable" and r["etf_exposure"]["status"] == "no_etf"
    assert r["metrics"]["holding_count"] == 2


def test_etf_exposure_not_claimed_without_constituents():
    ex = {"decomposed_etfs": [], "undecomposed_etfs": [{"ticker": "069500", "name": "KODEX 200", "reason_code": "no_holdings_data"}],
          "flags": [], "companies": []}
    r = d.render(_facts(_inputs(holdings=(KODEX,)), exposure=ex), "ko-KR")["sections"]["risk"]["etf_exposure"]
    assert r["status"] == "not_computed" and r["flags"] == [] and r["top_companies"] == []
    assert "KODEX 200는 실질 노출을 계산하지 않았습니다" in r["text"]


def test_checklist_at_most_three_and_earnings_first():
    now = datetime(2026, 11, 3, 20, 0, tzinfo=KST)
    c = d.render(_facts(_inputs(cash=0.0), now=now), "ko-KR")
    items = c["sections"]["checklist"]["items"]
    assert len(items) == d.MAX_CHECKS
    assert [i["kind"] for i in items][:2] == ["data_delay", "earnings_disclosure"]
    assert items[1]["text"] == "삼성전자(005930)의 실적 관련 공시: 연결재무제표기준영업(잠정)실적(공정공시) (2026-10-30)"


def test_no_advice_phrases_in_any_text():
    for L in ("ko-KR",):
        c = d.render(_facts(_inputs(cash=0.0)), L)
        assert forbidden_hits(json.dumps(c, ensure_ascii=False)) == []


# ---------- 언어 ----------


def _texts(c: dict) -> list[str]:
    """템플릿 문장(제목·종목명·뉴스/공시 제목을 뺀 코드 문장)."""
    s = c["sections"]
    out = [c["title"], c["freshness"]["headline"], *c["freshness"]["notices"], c["summary"]["text"], c["not_advice"],
           *(s[k]["title"] for k in d.SECTIONS), s["market"]["news_label"]]
    out += [i["text"] for i in s["market"]["indices"]]
    for it in s["holdings"]["items"]:
        out += [it["price_text"], it["position_text"], it["news_label"]]
    for it in s["watchlist"]["items"]:
        out += [it["price_text"]] + ([it["status_text"]] if it["status_text"] else [])
    out += s["risk"]["texts"] + [f["text"] for f in s["risk"]["flags"]] + [s["risk"]["sector"]["text"], s["risk"]["etf_exposure"]["text"]]
    out += [i["text"] for i in s["checklist"]["items"] if i["kind"] == "data_delay"]
    return out


NAMES = ("SK하이닉스", "삼성전자", "NAVER", "KODEX 200")


@pytest.mark.parametrize("L", LOCALES[1:])
def test_locales_keep_numbers_and_use_their_script(L):
    now = datetime(2026, 11, 3, 20, 0, tzinfo=KST)
    f = _facts(_inputs(cash=0.0), now=now, status={"status": "미수집", "detail": "x"})
    ko, out = d.render(f, "ko-KR"), d.render(f, L)
    # 숫자 필드는 언어와 관계없이 같다
    for a, b in zip(ko["sections"]["holdings"]["items"], out["sections"]["holdings"]["items"]):
        for k in ("ticker", "name", "latest_date", "latest_close", "day_change_pct", "week_change_pct", "return_20d_pct", "eval_amount",
                  "weight_pct", "profit_loss_pct", "news", "disclosures"):
            assert a[k] == b[k], k
    assert ko["sections"]["risk"]["metrics"] == out["sections"]["risk"]["metrics"]
    assert [i["close"] for i in ko["sections"]["market"]["indices"]] == [i["close"] for i in out["sections"]["market"]["indices"]]
    assert ko["section_status"] == out["section_status"] and ko["data_as_of"] == out["data_as_of"]
    ko_nums = {n.replace(",", "") for t in _texts(ko) for n in NUM.findall(t)}
    for t in _texts(out):
        rest = t
        for n in NAMES:
            rest = rest.replace(n, "")
        assert not HANGUL.search(rest), (L, t)
        assert {n.replace(",", "") for n in NUM.findall(t)} <= ko_nums | {"5", "20"}, (L, t)
        if L == "en-US":
            assert not KANA.search(rest), t
    assert out["locale"] == L and out["disclaimer"] != ko["disclaimer"]


def test_sources_and_dates_match_the_items():
    f = _facts()
    src = d._sources(f)
    titles = {n["title"] for scope in ("holdings", "watchlist") for it in f.brief[scope] for n in it["news"] + it["disclosures"]}
    assert src and all(x["title"] in titles for x in src)
    assert {x["kind"] for x in src} == {"news", "disclosure"}
    c = d.render(f, "ko-KR")
    for it in c["sections"]["holdings"]["items"]:
        assert it["latest_date"] == "2026-10-30" and it["latest_date"] in it["price_text"]


# ---------- LLM 요약(검증·대체) ----------


def test_llm_connection_failure_falls_back_to_code(monkeypatch):
    def down(messages):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(s, "_chat", down)
    f = _facts()
    out = s.build(f, "ko-KR", use_llm=True)
    assert out["summary_source"] == "code" and out["llm_failure"].startswith("LLM 호출 실패")
    assert out["content"]["summary"]["source"] == "code"
    assert out["content"]["summary"]["text"] == d.code_summary(f, "ko-KR", len(f.brief["highlights"]),
                                                               len(out["content"]["sections"]["checklist"]["items"]))


def test_llm_summary_used_only_when_numbers_check(monkeypatch):
    f = _facts()
    calls = []

    def fake(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"summary": "SK하이닉스는 12.34% 올랐고 목표 없음."}, ensure_ascii=False)  # 없는 숫자
        return json.dumps({"summary": "2026-10-30 종가 기준으로 보유 2종목과 관심 1종목을 정리했습니다."}, ensure_ascii=False)

    monkeypatch.setattr(s, "_chat", fake)
    out = s.build(f, "ko-KR", use_llm=True)
    assert len(calls) == 2 and "12.34" in calls[1][2]["content"] and "입력에 없는 숫자" in calls[1][3]["content"]
    assert out["summary_source"] == "llm" and out["content"]["summary"]["text"].startswith("2026-10-30 종가 기준")
    assert out["original"] is out["content"]


@pytest.mark.parametrize("text, why", [
    ("SK하이닉스 주가가 99.99% 올랐습니다.", "입력에 없는 숫자"),
    ("SK하이닉스는 매수하세요.", "금지 표현"),
    ("SK하이닉스 목표주가가 높습니다.", "금지 표현"),
    ("카카오와 SK하이닉스를 정리했습니다.", "입력에 없는 종목"),
    ("현대차와 SK하이닉스를 정리했습니다.", "입력에 없는 종목"),
    ("Summary in English only.", "한국어 아님"),
])
def test_validate_summary_rejects(text, why):
    f = _facts()
    payload = d.llm_payload(f)
    names = [t["name"] for t in d.tickers(f)]
    got, reason = s.validate_summary(json.dumps({"summary": text}, ensure_ascii=False), payload, "ko-KR", names, f.known_names)
    assert got is None and why in reason


@pytest.mark.parametrize("L, text, ok", [
    ("en-US", "Based on the 2026-10-30 close, SK하이닉스 and 삼성전자 were reviewed.", True),
    ("en-US", "SK하이닉스는 2026-10-30 기준입니다.", False),
    ("ja-JP", "2026-10-30の終値基準で、SK하이닉스を確認しました。", True),
    ("ja-JP", "2026-10-30 close based summary.", False),
    ("zh-CN", "以2026-10-30收盘价为准，已检查SK하이닉스。", True),
    ("zh-CN", "2026-10-30の終値です。", False),
])
def test_validate_summary_script_per_locale(L, text, ok):
    f = _facts()
    got, _ = s.validate_summary(json.dumps({"summary": text}, ensure_ascii=False), d.llm_payload(f), L, [t["name"] for t in d.tickers(f)])
    assert (got is not None) == ok


def test_non_korean_locale_keeps_korean_original(monkeypatch):
    monkeypatch.setattr(s, "_chat", lambda m: json.dumps({"summary": "Based on the 2026-10-30 close, two holdings were reviewed."}))
    out = s.build(_facts(), "en-US", use_llm=True)
    assert out["content"]["locale"] == "en-US" and out["original"]["locale"] == "ko-KR"
    assert out["content"]["summary"]["source"] == "llm" and out["original"]["summary"]["source"] == "code"
    assert out["content"]["sections"]["holdings"]["items"][0]["latest_close"] == out["original"]["sections"]["holdings"]["items"][0]["latest_close"]


def test_use_llm_false_never_calls(monkeypatch):
    monkeypatch.setattr(s, "_chat", lambda m: pytest.fail("LLM을 부르면 안 된다"))
    assert s.build(_facts(), "ja-JP", use_llm=False)["summary_source"] == "code"


def test_direct_news_requires_the_company_in_the_title():
    inp = _inputs()
    at = datetime.combine(END, datetime.min.time(), KST).replace(hour=10)
    inp.news["005930"] = [NewsRow("[주식마감] 한진칼 상한가", "x", "https://n/9", at), NewsRow("삼전, 신제품 공개", "y", "https://n/8", at)]
    f = _facts(inp)
    sam = next(i for i in d.render(f, "ko-KR")["sections"]["holdings"]["items"] if i["ticker"] == "005930")
    assert [n["title"] for n in sam["news"]] == ["삼전, 신제품 공개"]  # 통칭(삼전)은 직접 관련, 시황 기사는 아니다
    assert "[주식마감] 한진칼 상한가" not in {x["title"] for x in d._sources(f)}


def test_delayed_summary_must_state_the_base_date():
    f = _facts(now=datetime(2026, 11, 3, 20, 0, tzinfo=KST))
    payload = d.llm_payload(f)
    names = [t["name"] for t in d.tickers(f)]
    bad, reason = s.validate_summary(json.dumps({"summary": "보유 2종목을 정리했습니다."}, ensure_ascii=False), payload, "ko-KR", names)
    assert bad is None and "기준일" in reason
    ok, _ = s.validate_summary(json.dumps({"summary": "2026-10-30 종가 기준으로 보유 2종목을 정리했습니다."}, ensure_ascii=False),
                               payload, "ko-KR", names)
    assert ok is not None
    late, reason = s.validate_summary(json.dumps({"summary": "보유 2종목을 정리했습니다. 기준은 2026-10-30 종가입니다."}, ensure_ascii=False),
                                      payload, "ko-KR", names)
    assert late is None and "첫 문장" in reason


def test_non_korean_summary_may_quote_original_titles():
    f = _facts()
    payload = d.llm_payload(f, "en-US")
    assert payload["holdings"][0]["price"].startswith("Close on")  # 입력도 그 언어로 그린 값
    names = [t["name"] for t in d.tickers(f)]
    text = "As of the 2026-10-30 close, 삼성전자 filed 연결재무제표기준영업(잠정)실적(공정공시)."
    assert s.validate_summary(json.dumps({"summary": text}, ensure_ascii=False), payload, "en-US", names)[0] is None
    keep = [x["title"] for x in d._sources(f)]
    assert s.validate_summary(json.dumps({"summary": text}, ensure_ascii=False), payload, "en-US", names, (), keep)[0] == text
