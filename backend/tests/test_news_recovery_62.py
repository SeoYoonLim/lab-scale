"""62차(2026-11-15): 뉴스·공시 누락 구간 복구(app/news_recovery.py, scripts/recover_news_disclosures.py).

네이버·DART 요청은 모두 가짜 함수다(외부 요청 없음). integration은 격리 DB에서 가짜 종목(Q + 16진 5자리)만 만들고 지운다.
"""
import json
import secrets
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app import collectors as C, news_recovery as R
from scripts import recover_news_disclosures as T

KST = ZoneInfo("Asia/Seoul")
START, END = date(2026, 11, 4), date(2026, 11, 14)
SINCE, UNTIL = R.kst_window(START, END)


def article(i: int, at: datetime, link: str | None = None, title: str = "기사") -> dict:
    return {"title": f"{title} {i}", "description": "본문", "link": link or f"https://www.mk.co.kr/news/r62/{i}",
            "originallink": link or f"https://www.mk.co.kr/news/r62/{i}", "pubDate": format_datetime(at)}


class FakeNaver:
    """최신순 기사 목록을 start·display로 잘라 준다. calls에 (start, display, sort)를 남긴다."""

    def __init__(self, items, fail_at_start=None, error="RuntimeError: boom"):
        self.items, self.calls, self.fail_at_start, self.error = items, [], fail_at_start, error

    def __call__(self, query, display=10, sort="date", start=1):
        self.calls.append((start, display, sort))
        if self.fail_at_start == start:
            raise RuntimeError(self.error)
        return self.items[start - 1:start - 1 + display]


def hourly(n: int, newest: datetime) -> list[dict]:
    return [article(i, newest - timedelta(hours=i)) for i in range(n)]


# ---------- 페이지 넘김(순수) ----------

def test_more_than_ten_articles_are_paged_until_the_start_date():
    newest = datetime(2026, 11, 14, 20, 0, tzinfo=KST)
    items = hourly(300, newest)  # 1시간 간격 300건: 11-14 20시 ~ 11-02 09시
    f = FakeNaver(items)
    scan = R.paginate(f, "q", SINCE, UNTIL)
    in_window = [it for it in items if R.item_time(it) >= SINCE]
    assert scan.stop == R.REACHED_SINCE and not scan.truncated and scan.pages == 3
    assert len(scan.items) == len(in_window) == 261 and scan.out_of_range == 300 - 261
    assert [c[0] for c in f.calls] == [1, 101, 201] and all(c[1:] == (100, "date") for c in f.calls)


def test_window_end_filters_newer_articles():
    items = hourly(50, datetime(2026, 11, 16, 10, 0, tzinfo=KST))  # 11-16 10시부터 거꾸로
    scan = R.paginate(FakeNaver(items), "q", SINCE, UNTIL)
    assert all(SINCE <= R.item_time(it) < UNTIL for it in scan.items)
    assert scan.stop == R.END_OF_RESULTS and len(scan.items) == 50 - 35  # 11-16 10시~11-15 00시(35건)는 기간 밖


def test_end_of_results_and_empty_page():
    assert R.paginate(FakeNaver(hourly(30, datetime(2026, 11, 10, tzinfo=KST))), "q", SINCE, UNTIL).stop == R.END_OF_RESULTS
    empty = R.paginate(FakeNaver([]), "q", SINCE, UNTIL)
    assert empty.stop == R.END_OF_RESULTS and empty.pages == 1 and empty.items == []


def test_search_window_limit_marks_truncated():
    items = [article(i, datetime(2026, 11, 14, 20, tzinfo=KST) - timedelta(minutes=i)) for i in range(1200)]  # 모두 기간 안
    f = FakeNaver(items)
    scan = R.paginate(f, "q", SINCE, UNTIL)
    assert scan.stop == R.MAX_WINDOW and scan.truncated and scan.pages == R.MAX_PAGES == 10
    assert f.calls[-1][0] == 901 and max(c[0] for c in f.calls) <= R.MAX_START and len(scan.items) == 1000


def test_max_pages_cap_marks_truncated():
    items = [article(i, datetime(2026, 11, 14, 20, tzinfo=KST) - timedelta(minutes=i)) for i in range(500)]
    scan = R.paginate(FakeNaver(items), "q", SINCE, UNTIL, max_pages=2)
    assert scan.stop == R.MAX_PAGES_HIT and scan.truncated and scan.pages == 2 and len(scan.items) == 200


def test_duplicate_urls_across_pages_are_dropped():
    newest = datetime(2026, 11, 14, 20, tzinfo=KST)
    items = hourly(150, newest)
    items[120] = dict(items[5])  # 2쪽에 1쪽 기사가 다시 나옴(결과가 밀린 경우)
    scan = R.paginate(FakeNaver(items), "q", SINCE, UNTIL)
    assert scan.duplicate_in_scan == 1 and len({R.item_url(x) for x in scan.items}) == len(scan.items)


def test_api_error_keeps_partial_result_and_reports_error():
    f = FakeNaver(hourly(300, datetime(2026, 11, 14, 20, tzinfo=KST)), fail_at_start=101)
    scan = R.paginate(f, "q", SINCE, UNTIL)
    assert scan.stop == R.ERROR and "boom" in scan.error and scan.pages == 1 and len(scan.items) == 100


def test_delay_between_pages():
    slept = []
    R.paginate(FakeNaver(hourly(300, datetime(2026, 11, 14, 20, tzinfo=KST))), "q", SINCE, UNTIL, delay=0.5, sleep=slept.append)
    assert slept == [0.5, 0.5]  # 3쪽: 쪽 사이 2번


def test_kst_window_and_disclosure_days():
    assert R.kst_window(START, END) == (datetime(2026, 11, 4, tzinfo=KST), datetime(2026, 11, 15, tzinfo=KST))
    with pytest.raises(ValueError):
        R.kst_window(END, START)
    assert R.disclosure_days(START, date(2026, 11, 16)) == 13 and R.disclosure_days(date(2026, 6, 1), date(2026, 11, 16)) == 90
    assert R.disclosure_days(date(2026, 11, 16), date(2026, 11, 16)) == 1


# ---------- 원천 요청 함수(가짜 HTTP) ----------

def test_naver_fetch_passes_start(monkeypatch):
    got = {}
    monkeypatch.setenv("NAVER_CLIENT_ID", "x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "y")
    monkeypatch.setattr(C.http_retry, "get", lambda url, params=None, **kw: (got.update(params), SimpleNamespace(json=lambda: {"items": []}))[1])
    C.fetch_naver_news("삼성전자", display=100, sort="date", start=301)
    assert got == {"query": "삼성전자", "display": 100, "start": 301, "sort": "date"}
    C.fetch_naver_news("삼성전자")
    assert got["start"] == 1  # 기본값은 예전과 같다


def _dart_pages(pages):
    calls = []

    def get(url, params=None, **kw):
        calls.append(params["page_no"])
        return SimpleNamespace(json=lambda: pages[params["page_no"] - 1])
    return get, calls


def test_dart_pagination_until_total_page(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "k")
    pages = [{"status": "000", "total_page": 3, "list": [{"rcept_no": f"{p}{i}"} for i in range(2)]} for p in range(1, 4)]
    get, calls = _dart_pages(pages)
    monkeypatch.setattr(C.http_retry, "get", get)
    before = C.dart_requests
    items = C.fetch_dart_disclosures("00000000", days=13)
    assert calls == [1, 2, 3] and len(items) == 6 and C.dart_requests - before == 3


def test_dart_no_data_and_limit_error(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "k")
    get, _ = _dart_pages([{"status": "013", "message": "조회된 데이타가 없습니다."}])
    monkeypatch.setattr(C.http_retry, "get", get)
    assert C.fetch_dart_disclosures("00000000") == []
    get, _ = _dart_pages([{"status": "020", "message": "요청 제한을 초과하였습니다."}])
    monkeypatch.setattr(C.http_retry, "get", get)
    with pytest.raises(RuntimeError, match="020"):
        C.fetch_dart_disclosures("00000000")


# ---------- 실행 전 안전 장치(순수) ----------

DEV = "postgresql+psycopg://u:p@127.0.0.1:15432/ai_investment"


def test_guard():
    T.guard("plan", "postgresql+psycopg://u:p@127.0.0.1:1/anything", False, None)  # plan은 읽기만 한다
    with pytest.raises(T.Refused, match="allow-network"):
        T.guard("dry-run", DEV, False, None)
    with pytest.raises(T.Refused, match="ai_investment만"):
        T.guard("dry-run", "postgresql+psycopg://u:p@127.0.0.1:15432/ai_investment_news61_it", True, None)
    with pytest.raises(T.Refused, match="confirm-database"):
        T.guard("apply", DEV, True, None)
    with pytest.raises(T.Refused):
        T.guard("apply", DEV, True, "ai_investment_verify")
    T.guard("apply", DEV, True, "ai_investment")


def test_targets_are_the_refresh_universes():
    t = T.load_targets()
    assert len(t) == 350 and sum(x["asset_type"] == "etf" for x in t) == 50 and len({x["ticker"] for x in t}) == 350
    assert [x["ticker"] for x in T.load_targets(tickers=["005930", "069500"])] == ["005930", "069500"]


def test_dry_run_and_apply_refuse_without_flags_before_any_db_or_network(monkeypatch, capsys):
    monkeypatch.setattr(C.http_retry, "get", lambda *a, **k: pytest.fail("외부 요청"))
    assert T.main(["--mode", "dry-run"]) == 2 and "allow-network" in capsys.readouterr().out
    assert T.main(["--mode", "apply", "--allow-network"]) == 2


def test_step_status():
    assert T._step_status(3, 0, None) == "success" and T._step_status(2, 1, None) == "partial"
    assert T._step_status(0, 3, None) == "failed" and T._step_status(3, 0, "naver_budget") == "partial"


# ---------- integration: 저장·재실행·부분 실패·실행 기록(격리 DB, 가짜 API) ----------

@pytest.fixture
def cos(dev_db):
    """가짜 주식 1·ETF 1. 끝나면 그 종목의 뉴스·공시·격리 기록, 이 테스트가 만든 recovery 실행 기록, company를 지운다."""
    from app.models import Company

    made = []
    run_max = dev_db.execute(text("SELECT coalesce(max(id), 0) FROM data_refresh_run")).scalar_one()
    log_max = dev_db.execute(text("SELECT coalesce(max(id), 0) FROM data_refresh_log")).scalar_one()

    def make(kind="stock"):
        ticker = ("Q" + secrets.token_hex(3)[:5]).upper()
        name = f"captest62 {ticker}" if kind == "stock" else f"KODEX captest62 {ticker}"
        c = Company(ticker=ticker, name=name, asset_type=kind)
        dev_db.add(c)
        dev_db.commit()
        made.append(c)
        return c

    yield make
    dev_db.rollback()
    dev_db.execute(text("DELETE FROM data_refresh_log WHERE id > :m AND step IN ('news_recovery', 'disclosure_recovery')"), {"m": log_max})
    dev_db.execute(text("DELETE FROM data_refresh_run WHERE id > :m AND trigger = 'recovery'"), {"m": run_max})
    for c in made:
        for t in ("news", "disclosure"):
            dev_db.execute(text(f"DELETE FROM {t} WHERE company_id = :i"), {"i": c.id})
        dev_db.execute(text("DELETE FROM data_ingestion_quarantine WHERE source_key LIKE :k OR payload->>'ticker' = :t"),
                       {"k": f"%:{c.ticker}:%", "t": c.ticker})
        dev_db.execute(text("DELETE FROM company WHERE id = :i"), {"i": c.id})
    dev_db.commit()


def _news_count(db, company_id):
    return db.execute(text("SELECT count(*) FROM news WHERE company_id = :i"), {"i": company_id}).scalar_one()


def _fake_for(stock, etf, newest=datetime(2026, 11, 14, 20, tzinfo=KST)):
    """종목별 가짜 응답: 주식 150건(기간 안 134), ETF 120건 중 짝수 번째만 ETF 이름이 제목에 있음."""
    by_query = {
        R.news_query(stock): [article(i, newest - timedelta(hours=2 * i), f"https://www.mk.co.kr/news/r62/{stock.ticker}/{i}",
                                      f"{stock.name} 소식") for i in range(150)],
        R.news_query(etf): [article(i, newest - timedelta(hours=2 * i), f"https://www.mk.co.kr/news/r62/{etf.ticker}/{i}",
                                    f"{etf.name} 수익률" if i % 2 == 0 else "ETF 시장 전반") for i in range(120)],
    }

    def fetch(query, display=10, sort="date", start=1):
        return by_query[query][start - 1:start - 1 + display]
    return fetch


def _targets(*cs):
    return [{"ticker": c.ticker, "name": c.name, "asset_type": c.asset_type} for c in cs]


@pytest.mark.integration
def test_dry_run_counts_without_writing_then_apply_then_rerun_is_zero(dev_db, cos, tmp_path):
    from app import data_status as DS

    stock, etf = cos("stock"), cos("etf")
    fetch = _fake_for(stock, etf)
    status_before = DS.build_status(dev_db)["last_refresh"]
    news_attempt_before = DS.last_attempt(dev_db, "news")
    kw = dict(start=START, end=END, today=date(2026, 11, 15), fetch=fetch, disclosures=False, sleep=lambda s: None, log=lambda m: None)
    dry = T.run(_targets(stock, etf), mode="dry-run", **kw)
    assert dry["news"]["new"] > 0 and _news_count(dev_db, stock.id) == 0 and _news_count(dev_db, etf.id) == 0
    rows = {r["ticker"]: r["news"] for r in dry["rows"]}
    assert rows[stock.ticker]["in_window"] == 131 and rows[stock.ticker]["stop"] == R.REACHED_SINCE  # 2시간 간격: 11-14 20시~11-04 02시
    assert rows[etf.ticker]["etf_name_missing"] > 0  # ETF 이름이 없는 기사는 저장하지 않는다

    state = tmp_path / "state.json"
    first = T.run(_targets(stock, etf), mode="apply", state_path=str(state), **kw)
    dev_db.expire_all()
    saved = _news_count(dev_db, stock.id) + _news_count(dev_db, etf.id)
    assert first["news"]["new"] == dry["news"]["new"] == saved and first["failed"] == []
    assert json.loads(state.read_text(encoding="utf-8"))["news"] == [stock.ticker, etf.ticker]

    again = T.run(_targets(stock, etf), mode="apply", state_path=str(tmp_path / "state2.json"), **kw)  # 새 상태 파일 = 처음부터
    assert again["news"]["new"] == 0 and again["news"]["existing"] == saved
    assert _news_count(dev_db, stock.id) + _news_count(dev_db, etf.id) == saved  # 중복 저장 없음, 기존 행 그대로

    logs = dev_db.execute(text("SELECT step, status, rows_added, run_id FROM data_refresh_log WHERE run_id = :r"),
                          {"r": first["run_id"]}).all()
    assert [(l.step, l.status, l.rows_added) for l in logs] == [("news_recovery", "success", saved)]
    run_row = dev_db.execute(text("SELECT trigger, status, steps, note FROM data_refresh_run WHERE id = :r"), {"r": first["run_id"]}).one()
    assert run_row.trigger == "recovery" and run_row.status == "success" and f"news new {saved}" in run_row.note
    # 기존 의미 그대로: GET /api/status의 last_refresh, 목록의 last_collection(news 단계)은 복구 실행을 세지 않는다
    assert DS.build_status(dev_db)["last_refresh"] == status_before and DS.last_attempt(dev_db, "news") == news_attempt_before


@pytest.mark.integration
def test_partial_failure_then_rerun_with_state_finishes_only_the_rest(dev_db, cos, tmp_path):
    a, b = cos("stock"), cos("stock")
    fa, fb = _fake_for(a, cos("etf")), _fake_for(b, cos("etf"))

    def flaky(query, display=10, sort="date", start=1):
        if query == R.news_query(a):
            raise RuntimeError("ConnectionError: reset")
        return fb(query, display=display, sort=sort, start=start)

    state = tmp_path / "state.json"
    kw = dict(start=START, end=END, today=date(2026, 11, 15), disclosures=False, sleep=lambda s: None, log=lambda m: None,
              state_path=str(state))
    first = T.run(_targets(a, b), mode="apply", fetch=flaky, **kw)
    assert [f["ticker"] for f in first["failed"]] == [a.ticker] and first["news"]["companies_ok"] == 1
    assert json.loads(state.read_text(encoding="utf-8"))["news"] == [b.ticker]
    run_st = dev_db.execute(text("SELECT status FROM data_refresh_run WHERE id = :r"), {"r": first["run_id"]}).scalar_one()
    assert run_st == "partial"

    def fixed(query, display=10, sort="date", start=1):
        return (fa if query == R.news_query(a) else fb)(query, display=display, sort=sort, start=start)

    second = T.run(_targets(a, b), mode="apply", fetch=fixed, **kw)
    assert [r["ticker"] for r in second["rows"] if r.get("news")] == [a.ticker]  # b는 상태 파일로 건너뜀
    assert second["failed"] == [] and _news_count(dev_db, a.id) == second["news"]["new"] > 0


@pytest.mark.integration
def test_blocked_and_budget_stop_before_other_companies(dev_db, cos):
    a, b = cos("stock"), cos("stock")
    kw = dict(start=START, end=END, today=date(2026, 11, 15), disclosures=False, sleep=lambda s: None, log=lambda m: None)

    def blocked(query, display=10, sort="date", start=1):
        raise RuntimeError("HTTPError: 429 Client Error: Too Many Requests")

    out = T.run(_targets(a, b), mode="dry-run", fetch=blocked, **kw)
    assert out["stopped"] == "naver_blocked" and out["not_processed"] == [b.ticker]
    budget = T.run(_targets(a, b), mode="dry-run", fetch=_fake_for(a, b), naver_budget=11, **kw)  # a가 2쪽을 쓰면 다음 종목 최대 10쪽은 넘친다
    assert budget["stopped"] == "naver_budget" and budget["processed"] == 1 and budget["requests"]["naver_pages"] == 2


@pytest.mark.integration
def test_disclosure_recovery_dedups_by_receipt_and_records_step(dev_db, cos, monkeypatch):
    co = cos("stock")
    serial = f"{secrets.randbelow(10**6):06d}"
    items = [{"rcept_no": f"2026110{d}" + serial[:5] + str(d), "rcept_dt": f"2026110{d}", "report_nm": f"기업설명회(IR)개최 {d}",
              "corp_name": co.name, "flr_nm": co.name} for d in (5, 6, 9)]
    seen_days = []
    monkeypatch.setattr(C, "get_dart_corp_code", lambda t: "00000000")
    monkeypatch.setattr(C, "fetch_dart_disclosures", lambda corp, days=90: (seen_days.append(days), items)[1])
    kw = dict(start=START, end=END, today=date(2026, 11, 16), news=False, sleep=lambda s: None, log=lambda m: None)
    first = T.run(_targets(co), mode="apply", **kw)
    again = T.run(_targets(co), mode="apply", **kw)
    assert seen_days == [13, 13]  # 11-04 ~ 11-16(오늘)
    assert first["disclosures"]["new"] == 3 and again["disclosures"]["new"] == 0 and again["disclosures"]["existing"] == 3
    n = dev_db.execute(text("SELECT count(*) FROM disclosure WHERE company_id = :i"), {"i": co.id}).scalar_one()
    assert n == 3
    steps = dev_db.execute(text("SELECT step, rows_added FROM data_refresh_log WHERE run_id = :r"), {"r": first["run_id"]}).all()
    assert [(s.step, s.rows_added) for s in steps] == [("disclosure_recovery", 3)]


@pytest.mark.integration
def test_plan_mode_reads_only_and_makes_no_requests(dev_db, monkeypatch, capsys):
    monkeypatch.setattr(C.http_retry, "get", lambda *a, **k: pytest.fail("plan이 외부 요청을 했다"))
    before = dev_db.execute(text("SELECT (SELECT count(*) FROM news), (SELECT count(*) FROM disclosure), "
                                 "(SELECT count(*) FROM data_refresh_run)")).one()
    assert T.main(["--tickers", "005930,069500", "--end", "2026-11-14"]) == 0
    out = capsys.readouterr().out
    assert '"requests_max": 20' in out and '"stocks": 1' in out and "tables_never_touched" in out
    after = dev_db.execute(text("SELECT (SELECT count(*) FROM news), (SELECT count(*) FROM disclosure), "
                                "(SELECT count(*) FROM data_refresh_run)")).one()
    assert tuple(before) == tuple(after)
