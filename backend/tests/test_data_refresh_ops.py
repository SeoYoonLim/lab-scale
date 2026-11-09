"""데이터 자동 갱신 운영 기능(2026-11-07): 단계 재시도·부분 성공·오류 분류·종료 코드·단계 선택·CLI, 모니터링 판정·보고서·알림.
DB·외부 API를 쓰지 않는다(수집 함수·락·기록은 mock). 실제 DB의 락·기록·품질 검사는 tests/test_data_refresh_ops_devdb.py.
"""

import io
import json
from datetime import date, datetime

import pytest
import requests

from app import data_monitor as DM
from scripts import refresh_data as rd
from scripts.refresh_data import KST, Step


def _steps(behaviors: dict, keys=rd.STEP_ORDER):
    calls = {k: 0 for k in keys}

    def make(key):
        def run():
            calls[key] += 1
            b = behaviors.get(key)
            if isinstance(b, list):  # 호출마다 다른 결과
                b = b[min(calls[key], len(b)) - 1]
            if isinstance(b, BaseException):
                raise b
            return b
        return run

    return [Step(k, k, make(k)) for k in keys], calls


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=resp)


# ---------- 오류 분류 ----------

@pytest.mark.parametrize("exc,code,retry", [
    (requests.ConnectionError("down"), "network", True),
    (requests.Timeout("slow"), "timeout", True),
    (TimeoutError("lib"), "timeout", True),
    (rd.StepTimeout("x"), "timeout", True),
    (_http_error(500), "server_error", True),
    (_http_error(503), "server_error", True),
    (_http_error(429), "rate_limited", False),
    (_http_error(401), "auth", False),
    (_http_error(403), "auth", False),
    (_http_error(400), "http_error", False),
    (ValueError("bad json"), "bad_data", False),
    (KeyError("close"), "bad_data", False),
    (SystemExit(3), "rate_limited", False),  # collect_universe: 네이버 401·403·429로 멈춤
    (SystemExit(1), "script_exit", False),
    (rd.FailureRatioTooHigh("200/300"), "failure_ratio", False),
    (RuntimeError("universe_top300.json: TimeoutError; universe_etf50.json: ConnectionError"), "network", True),
    (RuntimeError("universe_top300.json: KeyError"), "collector_error", False),
    (OSError("disk"), "unknown", False),
])
def test_classify_error(exc, code, retry):
    assert rd.classify_error(exc) == (code, retry)


# ---------- 재시도 ----------

def test_transient_failure_is_retried_then_succeeds():
    steps, calls = _steps({"index": [requests.ConnectionError("x"), None]})
    sleeps = []
    res = {r.step: r for r in rd.run_steps(steps, ["index"], retries=1, backoff=5, sleep=sleeps.append)}
    assert res["index"].status == "success" and res["index"].retry_count == 1 and calls["index"] == 2
    assert sleeps == [5]


def test_retries_are_capped_with_exponential_backoff():
    steps, calls = _steps({"fx": requests.Timeout("x")})
    sleeps = []
    res = {r.step: r for r in rd.run_steps(steps, ["fx"], retries=2, backoff=10, sleep=sleeps.append)}
    assert res["fx"].status == "failed" and res["fx"].error_code == "timeout" and res["fx"].retry_count == 2
    assert calls["fx"] == 3 and sleeps == [10, 20]  # 무한 반복하지 않는다


@pytest.mark.parametrize("exc", [_http_error(401), _http_error(429), ValueError("format"), SystemExit(3)])
def test_auth_rate_limit_and_bad_data_are_not_retried(exc):
    steps, calls = _steps({"news": exc})
    sleeps = []
    res = {r.step: r for r in rd.run_steps(steps, ["news"], retries=3, sleep=sleeps.append)}
    assert res["news"].status == "failed" and calls["news"] == 1 and sleeps == [] and res["news"].retry_count == 0


def test_server_error_5xx_is_retried():
    steps, calls = _steps({"prices": [_http_error(502), None]})
    res = {r.step: r for r in rd.run_steps(steps, ["prices"], retries=1, sleep=lambda s: None)}
    assert res["prices"].status == "success" and calls["prices"] == 2


def test_default_run_steps_does_not_retry():
    """기존 호출(retries 생략)은 예전처럼 다시 시도하지 않는다."""
    steps, calls = _steps({"index": requests.ConnectionError("x")})
    assert rd.run_steps(steps, ["index"])[1].status == "failed" and calls["index"] == 1


# ---------- 부분 성공·종료 코드 ----------

def test_partial_success_is_not_success():
    steps, _ = _steps({"news": rd.Partial("일부 실패 3/300건"), "fx": RuntimeError("x")})
    res = {r.step: r for r in rd.run_steps(steps, list(rd.STEP_ORDER))}
    assert res["news"].status == "partial" and res["fx"].status == "failed" and res["prices"].status == "success"
    assert "partial" in rd.format_table(list(res.values()))


def test_plain_warning_string_stays_success():
    steps, _ = _steps({"prices": "참고"})
    assert rd.run_steps(steps, ["prices"])[0].status == "success"


@pytest.mark.parametrize("statuses,quality_error,code", [
    (["success", "skipped"], False, 0),
    (["success", "partial"], False, 3),
    (["success", "success"], True, 3),
    (["failed", "partial"], False, 1),
    (["failed", "success"], True, 1),
])
def test_final_exit_code(statuses, quality_error, code):
    t = datetime(2026, 11, 6, 17, tzinfo=KST)
    res = [rd.StepResult(f"s{i}", s, t, t) for i, s in enumerate(statuses)]
    assert rd.final_exit_code(res, quality_error) == code


def test_run_status():
    from app.refresh_runs import run_status

    assert run_status(["success", "skipped"]) == "success"
    assert run_status(["success", "partial"]) == "partial"
    assert run_status(["success", "failed"]) == "partial"
    assert run_status(["failed", "failed", "skipped"]) == "failed"
    assert run_status(["skipped"]) == "success"


def test_etf_holdings_outcome():
    from app.etf_holdings_collect import EtfResult

    ok = [EtfResult("1", "KODEX a", ok=True) for _ in range(9)]
    skipped = [EtfResult("2", "RISE b", skipped="미지원")]
    assert rd.etf_holdings_outcome(ok + skipped) is None
    part = rd.etf_holdings_outcome(ok + [EtfResult("3", "TIGER c", error="HTTP 500")])
    assert isinstance(part, rd.Partial) and "1/10" in part
    with pytest.raises(RuntimeError):
        rd.etf_holdings_outcome([EtfResult("3", "TIGER c", error="x")] * 3 + ok[:2])


# ---------- 단계 선택 ----------

def test_select_run_steps():
    assert rd.select_run_steps(None, None) == list(rd.STEP_ORDER)
    assert rd.select_run_steps(None, None, with_optional=["etf_holdings"]) == list(rd.STEP_ORDER) + ["etf_holdings"]
    assert rd.select_run_steps(None, None, ["stock_price", "dart"]) == ["prices", "news"]
    assert rd.select_run_steps(None, None, ["etf_price", "stock_price"]) == ["prices"]
    assert rd.select_run_steps(["etf_holdings"], None) == ["etf_holdings"]
    assert rd.select_run_steps(None, ["embed"], ["news", "embeddings"]) == ["news"]
    with pytest.raises(ValueError, match="지원하지 않는"):
        rd.select_run_steps(None, None, ["kospi200"])
    with pytest.raises(ValueError, match="알 수 없는"):
        rd.select_run_steps(None, None, ["bitcoin"])
    assert rd.select_steps(None, None) == list(rd.STEP_ORDER)  # 기존 함수는 그대로


def test_status_steps_are_unchanged_for_api_status():
    from app.data_status import REFRESH_STEPS

    assert REFRESH_STEPS == rd.STEP_ORDER and "etf_holdings" not in REFRESH_STEPS


# ---------- CLI(락·DB·기록은 mock) ----------

class _FakeLock:
    released = False

    def release(self):
        _FakeLock.released = True


@pytest.fixture
def fake_ops(monkeypatch):
    """main()의 DB 부분을 가짜로: 락, 실행 기록, 단계 기록, 보고서."""
    from app import refresh_runs

    calls = {"finish": None, "logs": [], "start": None}
    monkeypatch.setattr(refresh_runs, "acquire_lock", lambda url=None: _FakeLock())
    monkeypatch.setattr(refresh_runs, "recover_aborted", lambda db: 0)
    monkeypatch.setattr(refresh_runs, "start_run", lambda db, steps, trigger="manual": calls.__setitem__("start", (steps, trigger)) or 77)
    monkeypatch.setattr(refresh_runs, "finish_run", lambda db, rid, status, code, note=None: calls.__setitem__("finish", (rid, status, code, note)))
    monkeypatch.setattr(refresh_runs, "set_step_quality", lambda *a: None)
    monkeypatch.setattr(refresh_runs, "recent_runs", lambda db, n=10: [])
    monkeypatch.setattr(rd, "write_log", lambda res, run_id=None: calls["logs"].append((res.step, res.status, run_id)))
    monkeypatch.setattr(rd, "resolve_days", lambda days, today: (10, "test"))

    class _DB:
        def close(self):
            pass

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _DB())
    report = {"sources": [], "summary": {"overall": "healthy", "not_usable": [], "quality_errors": []}}
    monkeypatch.setattr(DM, "build_report", lambda db, **k: report)
    monkeypatch.setattr(DM, "write_reports", lambda r, d: ("a.json", "a.md"))
    _FakeLock.released = False
    return calls, report


def _patch_steps(monkeypatch, behaviors):
    steps, calls = _steps(behaviors, rd.ALL_STEPS)
    monkeypatch.setattr(rd, "build_steps", lambda *a, **k: steps)
    return calls


def test_cli_all_success_records_run_and_releases_lock(fake_ops, monkeypatch):
    calls, _ = fake_ops
    ran = _patch_steps(monkeypatch, {})
    assert rd.main(["--all", "--step-retries", "0"]) == 0
    assert calls["start"] == (list(rd.STEP_ORDER), "manual")
    assert calls["finish"][:3] == (77, "success", 0) and _FakeLock.released
    assert [s for s, _, rid in calls["logs"]] == list(rd.STEP_ORDER) and all(rid == 77 for *_, rid in calls["logs"])
    assert ran["etf_holdings"] == 0  # 선택 단계는 고르지 않으면 실행·기록하지 않는다


def test_cli_one_collector_fails_others_continue(fake_ops, monkeypatch):
    calls, _ = fake_ops
    ran = _patch_steps(monkeypatch, {"fx": ValueError("format")})
    assert rd.main(["--step-retries", "0"]) == 1
    assert all(ran[k] == 1 for k in rd.STEP_ORDER)
    assert calls["finish"][1:3] == ("partial", 1)


def test_cli_partial_exit_code_three(fake_ops, monkeypatch):
    calls, _ = fake_ops
    _patch_steps(monkeypatch, {"news": rd.Partial("일부 실패 2/300건")})
    assert rd.main(["--step-retries", "0"]) == 3
    assert calls["finish"][1:3] == ("partial", 3)


def test_cli_quality_error_exit_code_three(fake_ops, monkeypatch):
    calls, report = fake_ops
    report["sources"] = [{"source": "stock_price", "step": "prices", "status": "healthy", "quality_status": "error",
                          "warning_count": 0, "quality_checks": [], "reasons": []}]
    _patch_steps(monkeypatch, {})
    assert rd.main(["--source", "stock_price", "--step-retries", "0"]) == 3
    assert calls["finish"][3] == "품질 error"


def test_cli_source_selection_and_optional_step(fake_ops, monkeypatch):
    calls, _ = fake_ops
    ran = _patch_steps(monkeypatch, {})
    assert rd.main(["--source", "etf_holdings", "--step-retries", "0"]) == 0
    assert ran["etf_holdings"] == 1 and ran["prices"] == 0
    assert ("etf_holdings", "success", 77) in calls["logs"] and ("prices", "skipped", 77) in calls["logs"]


def test_cli_lock_busy_exit_four(monkeypatch):
    from app import refresh_runs

    def busy(url=None):
        raise refresh_runs.LockBusy("x")

    monkeypatch.setattr(refresh_runs, "acquire_lock", busy)
    monkeypatch.setattr(rd, "resolve_days", lambda days, today: (10, "test"))
    ran = _patch_steps(monkeypatch, {})
    assert rd.main([]) == 4 and sum(ran.values()) == 0


def test_cli_db_down_exit_five(monkeypatch):
    from app import refresh_runs

    def down(url=None):
        raise ConnectionRefusedError("db")

    monkeypatch.setattr(refresh_runs, "acquire_lock", down)
    monkeypatch.setattr(rd, "resolve_days", lambda days, today: (10, "test"))
    assert rd.main([]) == 5


def test_cli_unsupported_source_is_usage_error():
    with pytest.raises(SystemExit) as e:
        rd.main(["--source", "kospi200"])
    assert e.value.code == 2


def test_cli_retry_failed_runs_only_failed_steps(fake_ops, monkeypatch):
    from app import refresh_runs

    calls, _ = fake_ops
    monkeypatch.setattr(refresh_runs, "failed_steps", lambda db, steps: ["fx", "news"])
    ran = _patch_steps(monkeypatch, {})
    assert rd.main(["--retry-failed", "--step-retries", "0"]) == 0
    assert ran["fx"] == 1 and ran["news"] == 1 and ran["prices"] == 0
    assert calls["start"] == (["fx", "news"], "retry")


def test_cli_retry_failed_nothing_to_do(fake_ops, monkeypatch):
    from app import refresh_runs

    calls, _ = fake_ops
    monkeypatch.setattr(refresh_runs, "failed_steps", lambda db, steps: [])
    ran = _patch_steps(monkeypatch, {})
    assert rd.main(["--retry-failed"]) == 0 and sum(ran.values()) == 0 and calls["start"] is None


def test_cli_status_exit_codes(monkeypatch, capsys):
    from app import refresh_runs

    class _DB:
        def close(self):
            pass

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _DB())
    monkeypatch.setattr(refresh_runs, "lock_is_held_elsewhere", lambda db: False)
    monkeypatch.setattr(refresh_runs, "recent_runs", lambda db, n=10: [])
    rep = {"generated_at": "x", "sources": [], "summary": {"overall": "stale", "not_usable": ["fx"], "quality_errors": []}, "notes": []}
    monkeypatch.setattr(DM, "build_report", lambda db, **k: rep)
    assert rd.main(["--status", "--no-report", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["summary"]["overall"] == "stale"
    rep["summary"] = {"overall": "healthy", "not_usable": [], "quality_errors": []}
    assert rd.main(["--status", "--no-report"]) == 0


def test_cli_status_db_down_exit_five(monkeypatch):
    def boom():
        raise ConnectionRefusedError("x")

    monkeypatch.setattr("app.db.session.SessionLocal", boom)
    assert rd.main(["--status", "--no-report"]) == 5


def test_dry_run_lists_optional_step():
    out = io.StringIO()
    import contextlib

    with contextlib.redirect_stdout(out):
        rd.print_plan(["prices", "etf_holdings"], 10, 30)
    assert "etf_holdings" in out.getvalue()


# ---------- 모니터링 판정 ----------

FRI_EVENING = datetime(2026, 11, 6, 18, 0, tzinfo=KST)   # 금요일 장 마감 뒤
FRI_MORNING = datetime(2026, 11, 6, 10, 0, tzinfo=KST)   # 금요일 장중
SAT = datetime(2026, 11, 7, 12, 0, tzinfo=KST)
MON_EARLY = datetime(2026, 11, 9, 8, 0, tzinfo=KST)
PRICE = DM.SOURCE_BY_KEY["stock_price"]
OK_HIST = {"last_attempt": {"status": "success", "finished_at": datetime(2026, 11, 6, 8, tzinfo=KST)},
           "last_success": {"status": "success", "finished_at": datetime(2026, 11, 6, 8, tzinfo=KST)}}
Q_OK = {"status": "ok", "checks": []}


def ev(src=PRICE, as_of=date(2026, 11, 6), hist=OK_HIST, q=Q_OK, now=FRI_EVENING, running=None, count=100):
    return DM.evaluate_source(src, DM.SourceFacts(as_of, count), hist, q, now, running)


def test_expected_close_date():
    assert DM.expected_close_date(FRI_EVENING) == date(2026, 11, 6)
    assert DM.expected_close_date(FRI_MORNING) == date(2026, 11, 5)  # 장중
    assert DM.expected_close_date(SAT) == date(2026, 11, 6)  # 주말
    assert DM.expected_close_date(MON_EARLY) == date(2026, 11, 6)


def test_healthy_and_separate_collected_vs_as_of():
    r = ev()
    assert r["status"] == "healthy" and r["usable_for_analysis"] and r["lag_business_days"] == 0
    assert r["data_as_of"] == "2026-11-06" and r["last_success_at"].startswith("2026-11-06T08:00")


def test_weekend_and_monday_morning_are_not_stale():
    assert ev(now=SAT)["status"] == "healthy"
    assert ev(now=MON_EARLY)["status"] == "healthy"


def test_one_business_day_behind_is_still_healthy_with_note():
    r = ev(as_of=date(2026, 11, 5))
    assert r["status"] == "healthy" and r["lag_business_days"] == 1 and any("1영업일" in x for x in r["reasons"])


def test_price_delay_is_stale_and_not_usable():
    r = ev(as_of=date(2026, 11, 3))
    assert r["status"] == "stale" and r["lag_business_days"] == 3 and not r["usable_for_analysis"]


def test_news_publish_delay():
    news = DM.SOURCE_BY_KEY["news"]
    assert ev(news, as_of=date(2026, 11, 7), now=SAT)["status"] == "healthy"  # 주말 기사
    assert ev(news, as_of=date(2026, 11, 2), now=FRI_EVENING)["status"] == "stale"


def test_disclosure_receipt_date():
    disc = DM.SOURCE_BY_KEY["disclosure"]
    r = ev(disc, as_of=date(2026, 11, 6), now=SAT)
    assert r["expected_as_of"] == "2026-11-06" and r["status"] == "healthy"


def test_etf_holdings_delay():
    etf = DM.SOURCE_BY_KEY["etf_holdings"]
    assert ev(etf, as_of=date(2026, 11, 5))["status"] == "healthy"
    r = ev(etf, as_of=date(2026, 10, 29))
    assert r["status"] == "stale" and r["lag_business_days"] == 6


def test_exchange_rate_missing():
    fx = DM.SOURCE_BY_KEY["exchange_rate"]
    r = ev(fx, as_of=None, hist={}, count=0)
    assert r["status"] == "never_run" and not r["usable_for_analysis"]
    r = ev(fx, as_of=None, hist=OK_HIST, count=0)
    assert r["status"] == "stale" and "데이터 없음" in r["reasons"]


def test_last_failure_does_not_hide_good_data():
    """최근 실행이 실패해도 마지막 성공·데이터 기준일은 그대로 보이고, 데이터가 최신이면 분석에 쓸 수 있다."""
    hist = {**OK_HIST, "last_attempt": {"status": "failed", "error_code": "network", "started_at": FRI_EVENING},
            "last_failure": {"status": "failed", "error_code": "network", "started_at": FRI_EVENING}}
    r = ev(hist=hist)
    assert r["status"] == "failed" and r["usable_for_analysis"]
    assert r["last_success_at"].startswith("2026-11-06T08") and r["last_failure_reason"] == "network"


def test_partial_running_disabled_unsupported():
    assert ev(hist={**OK_HIST, "last_attempt": {"status": "partial"}})["status"] == "partial"
    assert ev(running={"prices"})["status"] == "running"
    assert ev(DM.SOURCE_BY_KEY["earnings"])["status"] == "disabled"
    r = ev(DM.SOURCE_BY_KEY["kospi200"], as_of=None)
    assert r["status"] == "unsupported" and not r["usable_for_analysis"]


def test_quality_error_makes_data_unusable_even_if_fresh():
    q = {"status": "error", "checks": [{"code": "close_null_or_nonpositive", "severity": "error", "label": "x", "count": 3}]}
    r = ev(q=q)
    assert r["status"] == "healthy" and not r["usable_for_analysis"] and r["quality_status"] == "error"


def test_quality_status_rules():
    assert DM.quality_status([{"severity": "warning", "count": 0}, {"severity": "error", "count": 0}]) == "ok"
    assert DM.quality_status([{"severity": "warning", "count": 2}]) == "warning"
    assert DM.quality_status([{"severity": "warning", "count": 2}, {"severity": "error", "count": 1}]) == "error"


def test_quality_check_sql_failure_counts_as_error():
    class _Bad:
        def execute(self, *a, **k):
            raise RuntimeError("sql")

        def rollback(self):
            pass

    out = DM.check_quality(_Bad(), FRI_EVENING, ["stock_price"])
    assert out["stock_price"]["status"] == "error" and all(c["count"] is None for c in out["stock_price"]["checks"])


def test_every_quality_check_has_known_source_and_severity():
    for src, code, sev, label, sql in DM.QUALITY_CHECKS:
        assert src in DM.SOURCE_BY_KEY and sev in ("error", "warning") and sql.lstrip().upper().startswith("SELECT")


def test_quality_checks_are_read_only():
    for *_, sql in DM.QUALITY_CHECKS:
        up = sql.upper()
        assert not any(w in up for w in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE "))


def test_unsupported_sources_are_listed_not_scheduled():
    unsupported = {s.key for s in DM.SOURCES if not s.supported}
    assert {"kospi200", "interest_rate", "other_issuer_etf_holdings", "overseas_stock"} <= unsupported
    assert all(s.step is None and not s.enabled for s in DM.SOURCES if not s.supported)


# ---------- 보고서·알림 ----------

def _report(rows):
    return {"generated_at": "2026-11-06T18:00:00+09:00", "sources": rows, "summary": DM.summarize(rows), "recent_runs": [],
            "notes": ["n"]}


def test_report_json_and_markdown(tmp_path):
    rows = [ev(), ev(as_of=date(2026, 11, 3)), ev(DM.SOURCE_BY_KEY["kospi200"], as_of=None)]
    rep = _report(rows)
    assert rep["summary"]["overall"] == "stale" and rep["summary"]["not_usable"] == ["stock_price"]
    jp, mp = DM.write_reports(rep, tmp_path)
    loaded = json.loads(open(jp, encoding="utf-8").read())
    for k in ("source", "status", "last_run_at", "last_success_at", "data_as_of", "expected_as_of", "lag_business_days",
              "duration_ms", "records_inserted", "records_updated", "records_skipped", "error_code", "retry_count",
              "quality_status", "warning_count", "usable_for_analysis"):
        assert k in loaded["sources"][0]
    md = open(mp, encoding="utf-8").read()
    assert "| 소스 | 상태 |" in md and "stale" in md and "unsupported" in md
    assert DM.status_exit_code(rep) == 3
    assert DM.status_exit_code(_report([ev()])) == 0


def test_report_contains_no_secrets(tmp_path, monkeypatch):
    rep = _report([ev()])
    text = json.dumps(rep, default=str)
    assert "crtfc_key" not in text and "password" not in text.lower() and "token" not in text.lower()


def test_alerts_and_notifier_isolation():
    rows = [ev(hist={**OK_HIST, "last_attempt": {"status": "failed", "error_code": "auth"}}), ev(as_of=date(2026, 11, 3)),
            ev(q={"status": "error", "checks": [{"code": "future_date", "severity": "error", "label": "x", "count": 1}]}),
            ev(DM.SOURCE_BY_KEY["kospi200"], as_of=None)]
    events = DM.alerts(_report(rows))
    assert [e["level"] for e in events] == ["error", "warning", "error"]
    assert "future_date" in events[2]["message"]

    class Boom:
        def notify(self, e):
            raise RuntimeError("slack down")

    got = []

    class Collect:
        def notify(self, e):
            got.append(e)

    DM.dispatch(_report(rows), [Boom(), Collect()])  # 한 알림이 실패해도 다음 알림은 받는다
    assert len(got) == 3


def test_log_notifier_writes_stream():
    buf = io.StringIO()
    DM.LogNotifier(buf).notify({"level": "error", "source": "fx", "message": "failed"})
    assert "[ALERT:error] fx: failed" in buf.getvalue()


def test_step_quality_takes_worst_source():
    rows = [ev(), ev(DM.SOURCE_BY_KEY["etf_price"], q={"status": "warning", "checks": [
        {"code": "x", "severity": "warning", "label": "x", "count": 4}]})]
    assert DM.step_quality(_report(rows)) == {"prices": ("warning", 4)}
