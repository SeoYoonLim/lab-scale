"""67차 클라우드 분봉 보존(scripts/run_cloud_session_preservation.py). 원천·R2는 가짜로 대신한다(외부 요청 없음, DB·Ollama 없음)."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

from app import krx_calendar, session_bars as sb
from scripts import run_cloud_session_preservation as c

KST = krx_calendar.KST
NOW = datetime(2026, 11, 17, 20, 30, tzinfo=KST)  # 화요일, 그날 분봉 완료(20:10) 뒤
D = date(2026, 11, 16)
ASSETS = c.load_universe()
BACKEND = Path(__file__).resolve().parents[1]


# ---------- 가짜 원천·저장소 ----------

def row(day, hm, o, v=100, h=None, lo=None, cl=None):
    return {"localDateTime": f"{day}{hm}00", "openPrice": o, "highPrice": o if h is None else h, "lowPrice": o if lo is None else lo,
            "currentPrice": o if cl is None else cl, "accumulatedTradingVolume": v}


def day_rows(d: str, asset: str, base: int = 10000) -> list[dict]:
    """54차 분류 규칙을 통과하는 하루(정규장·종가 단일가·장후 종가매매, 주식은 애프터마켓까지). 시간 순서."""
    rows = [row(d, "0900", base, 1000, base + 50, base - 50, base + 10), row(d, "1100", base + 20, 900),
            row(d, "1519", base + 30, 800), row(d, "1530", base + 40, 5000), row(d, "1540", base + 40, 30), row(d, "1559", base + 40, 8)]
    if asset == "stock":
        rows += [row(d, "1600", base + 35, 300), row(d, "1959", base + 36, 33)]
    return rows


class Source:
    """api.stock.naver.com 분봉 흉내. days에 있는 날짜만 봉을 준다."""

    def __init__(self, days=None, empty=(), dup=(), fail=None, fail_times=None):
        self.days = {d.strftime("%Y%m%d") for d in (days or [])}
        self.empty, self.dup, self.fail = set(empty), set(dup), fail or {}
        self.fail_times = dict(fail_times or {})  # ticker -> 남은 실패 횟수
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url, params):
        t = url.rstrip("/").split("/")[-2]
        d = params["startDateTime"][:8]
        self.calls.append((t, d))
        if t in self.fail:
            raise self.fail[t]
        if self.fail_times.get(t, 0) > 0:
            self.fail_times[t] -= 1
            raise requests.ConnectionError("temporary")
        if d not in self.days or t in self.empty:
            return []
        rows = day_rows(d, ASSETS[t])
        if t in self.dup:
            rows.insert(2, dict(rows[1]))
        return rows


class MemStore:
    def __init__(self):
        self.obj: dict[str, bytes] = {}
        self.puts: list[str] = []

    def head(self, key):
        if key not in self.obj:
            return None
        return {"size": len(self.obj[key]), "sha256": hashlib.sha256(self.obj[key]).hexdigest()}

    def put(self, key, data, sha256, content_type):
        assert hashlib.sha256(data).hexdigest() == sha256
        self.obj[key] = bytes(data)
        self.puts.append(key)

    def get(self, key):
        return self.obj[key]

    def list(self, prefix):
        return sorted(k for k in self.obj if k.startswith(prefix))


def run(tmp_path, store, src, *args, now=NOW, env=None, name="w"):
    argv = ["--work-dir", str(tmp_path / name), "--store", "local", "--local-store-dir", str(tmp_path / "unused"), *args]
    rc = c.main(argv, now=now, store=store, get=src, env=env or {}, sleep=0, retry_waits=(0, 0), wait=lambda s: None,
                clock=lambda: now)
    rep = json.loads((tmp_path / name / "run_report.json").read_text(encoding="utf-8"))
    return rc, rep


def seal_of(store, d=D):
    k = f"session-minute/{d.isoformat()}/manifest.json"
    return json.loads(store.obj[k]) if k in store.obj else None


# ---------- 1·2·16. 거래일·휴장일·복구 범위 ----------

def test_targets_are_last_seven_trading_days_and_today_only_after_2010():
    st, days, info = c.plan_targets(NOW, 7, None)
    assert st == "trading"
    assert days == [date(2026, 11, d) for d in (9, 10, 11, 12, 13, 16, 17)]
    _, days, info = c.plan_targets(datetime(2026, 11, 17, 19, 0, tzinfo=KST), 7, None)
    assert days[-1] == D and days[0] == date(2026, 11, 6) and info["latest_complete"] == "2026-11-16"


@pytest.mark.parametrize("now,status", [(datetime(2026, 11, 21, 20, 20, tzinfo=KST), "weekend"),
                                        (datetime(2026, 12, 25, 20, 20, tzinfo=KST), "holiday")])
def test_weekend_and_holiday_skip_without_requests(tmp_path, now, status):
    store, src = MemStore(), Source()
    rc, rep = run(tmp_path, store, src, now=now)
    assert rc == 0 and rep["status"] == "skip_non_trading_day" and rep["plan"]["today_status"] == status
    assert src.calls == [] and rep["days"] == []
    assert [k for k in store.obj] == [f"session-minute/_runs/{rep['run_id']}.json"]  # SKIP도 기록은 남긴다


def test_recovery_range_is_limited_to_provider_window(tmp_path):
    store, src = MemStore(), Source(days=[D])
    for i, extra in enumerate((["--lookback", "8"], ["--date", "2026-11-06"])):  # 11-06 = 11-17 기준 8거래일 전
        rc = c.main(["--work-dir", str(tmp_path / f"w{i}"), *extra], now=NOW, store=store, get=src, env={})
        assert rc == 2
    assert store.obj == {} and src.calls == []


def test_expired_day_is_marked_unrecoverable_once_with_probe_only(tmp_path):
    store, src = MemStore(), Source(days=[D])  # 11-13은 원천이 더 이상 주지 않는다고 가정
    rc, rep = run(tmp_path, store, src, "--date", "2026-11-13", name="a")
    assert rc == 1 and rep["days"][0]["status"] == "unrecoverable_new"
    assert len(src.calls) == c.PROBE_TICKERS  # 나머지 347종목은 요청하지 않는다
    assert "session-minute/_status/2026-11-13.json" in store.obj
    rc, rep = run(tmp_path, store, src, "--date", "2026-11-13", name="b")
    assert rc == 0 and rep["days"][0]["status"] == "unrecoverable_known" and len(src.calls) == c.PROBE_TICKERS


def test_old_missing_day_does_not_block_latest(tmp_path):
    store, src = MemStore(), Source(days=[D, date(2026, 11, 17)])
    rc, rep = run(tmp_path, store, src, "--lookback", "3")  # 11-13(복구 불가), 11-16, 11-17
    st = {r["date"]: r["status"] for r in rep["days"]}
    assert st == {"2026-11-13": "unrecoverable_new", "2026-11-16": "completed", "2026-11-17": "completed"}
    assert seal_of(store, date(2026, 11, 17))["status"] == "complete"


# ---------- 3·7·8·12. 350종목 완료·세션 검증·SHA-256·봉인 순서 ----------

def test_full_day_350_tickers_sealed_last_with_sha256(tmp_path):
    store, src = MemStore(), Source(days=[D])
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat())
    day = rep["days"][0]
    assert rc == 0 and day["status"] == "completed", day
    assert day["local"]["raw_files"] == 350 and day["session_validation"]["ok"] is True
    seal = seal_of(store)
    assert seal["ticker_count"] == 350 and seal["assets"] == {"stock": 300, "etf": 50}
    assert seal["trade_date"] == "2026-11-16" and seal["sealed_at_kst"].endswith("+09:00") and seal["sealed_at_utc"].endswith("+00:00")
    assert seal["tickers"]["005930"]["rows"] == 8 and seal["tickers"]["069500"]["rows"] == 6
    pre = "session-minute/2026-11-16/"
    for rel, f in seal["files"].items():  # 업로드된 객체를 다시 해시해 manifest와 대조
        data = store.obj[pre + rel]
        assert len(data) == f["bytes"] and hashlib.sha256(data).hexdigest() == f["sha256"]
    assert seal["file_count"] == len(seal["files"]) and seal["total_bytes"] == sum(f["bytes"] for f in seal["files"].values())
    sha_lines = store.obj[pre + "MANIFEST.sha256"].decode().splitlines()
    assert len([ln for ln in sha_lines if "raw/" in ln]) == 350
    day_puts = [k for k in store.puts if k.startswith(pre)]
    assert day_puts[-1] == pre + "manifest.json" and day_puts.count(pre + "manifest.json") == 1  # 봉인은 마지막 한 번
    raw_idx = max(i for i, k in enumerate(day_puts) if "/raw/" in k)
    assert raw_idx < day_puts.index(pre + "MANIFEST.sha256")  # 원본 먼저, 색인 나중
    # 원본은 원천 응답 그대로(json.dumps)
    assert json.loads(store.obj[pre + "raw/005930.json"]) == day_rows("20261116", "stock")


def test_session_validation_matches_direct_54_classification(tmp_path):
    store, src = MemStore(), Source(days=[D], dup={"000660"})
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat())
    dest = tmp_path / "w" / "preserved" / D.isoformat()
    val = c.validate_sessions(dest, D, ASSETS, clock=lambda: NOW)
    direct_ok = 0
    for f in sorted((dest / "raw").glob("*.json")):
        by, probs, _ = sb.parse_bars(sb.load_rows(f.read_bytes()))
        r = sb.classify_day(D, by.get(D, []), ASSETS[f.stem], NOW, probs.get(D))
        direct_ok += r.status == "ok"
    assert val["counts"]["valid_days"] == direct_ok == 349
    assert [r["ticker"] for r in val["rejected"]] == ["000660"] and "duplicate_timestamp" in val["rejected"][0]["reasons"]


# ---------- 4·5·6. 누락·빈 응답·중복 ----------

def test_missing_ticker_is_not_success_and_is_retried(tmp_path):
    store, src = MemStore(), Source(days=[D], fail={"005930": requests.HTTPError("500 Server Error")})
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat())
    day = rep["days"][0]
    assert rc == 1 and day["status"] == "incomplete_tickers" and day["local"]["missing_tickers"] == ["005930"]
    assert [a["tag"] for a in day["attempts"]] == ["probe", "full", "retry1", "retry2"]
    assert src.calls.count(("005930", "20261116")) == 3
    assert seal_of(store) is None and "session-minute/2026-11-16/raw/000660.json" in store.obj  # 받은 것은 보존, 봉인은 없음


def test_empty_response_creates_no_file_and_blocks_seal(tmp_path):
    store, src = MemStore(), Source(days=[D], empty={"069500"})
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat())
    assert rc == 1 and rep["days"][0]["local"]["missing_tickers"] == ["069500"]
    assert "session-minute/2026-11-16/raw/069500.json" not in store.obj and seal_of(store) is None


def test_duplicate_timestamp_blocks_seal(tmp_path):
    store, src = MemStore(), Source(days=[D], dup={"005930"})
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat())
    assert rc == 1 and rep["days"][0]["status"] == "session_validation_failed" and seal_of(store) is None


def test_transient_ticker_failure_recovers_on_retry(tmp_path):
    store, src = MemStore(), Source(days=[D], fail_times={"005930": 1})
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat())
    assert rc == 0 and rep["days"][0]["status"] == "completed"
    assert [a["tag"] for a in rep["days"][0]["attempts"]] == ["probe", "full", "retry1"]


def test_source_block_stops_requests_without_bypass(tmp_path):
    store = MemStore()
    src = Source(days=[D, date(2026, 11, 17)], fail={t: requests.HTTPError("403 Client Error: Forbidden") for t in ASSETS})
    rc, rep = run(tmp_path, store, src, "--lookback", "2")
    st = [r["status"] for r in rep["days"]]
    assert rc == 1 and rep["source_blocked"] is True and st == ["source_blocked", "skipped_source_blocked"]
    assert len(src.calls) == c.PROBE_TICKERS  # 차단 응답 뒤 다시 요청하지 않는다


# ---------- 9·10·11. 멱등성·충돌·일부 업로드 실패 ----------

def test_rerun_skips_sealed_day_without_requests(tmp_path):
    store, src = MemStore(), Source(days=[D])
    run(tmp_path, store, src, "--date", D.isoformat(), name="a")
    n_calls, n_puts = len(src.calls), len(store.puts)
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat(), name="b")
    assert rc == 0 and rep["days"][0]["status"] == "already_complete"
    assert len(src.calls) == n_calls and store.puts[n_puts:] == [f"session-minute/_runs/{rep['run_id']}.json"]


def test_different_existing_raw_is_conflict_and_nothing_is_overwritten(tmp_path):
    store, src = MemStore(), Source(days=[D])
    key = "session-minute/2026-11-16/raw/005930.json"
    store.obj[key] = b"[]"  # 이전에 다른 내용으로 보존된 원본
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat())
    day = rep["days"][0]
    assert rc == 1 and day["status"] == "conflict" and day["upload"]["conflicts"][0]["file"] == "raw/005930.json"
    assert store.obj[key] == b"[]" and day["upload"]["uploaded"] == 0
    assert [k for k in store.obj if k.startswith("session-minute/2026-11-16/")] == [key]


class FailingPut(MemStore):
    def __init__(self, bad):
        super().__init__()
        self.bad = bad

    def put(self, key, data, sha256, content_type):
        if key.endswith(self.bad):
            raise ConnectionError("upload reset")
        super().put(key, data, sha256, content_type)


def test_partial_upload_failure_is_not_sealed_and_next_run_fills_gap(tmp_path):
    store, src = FailingPut("raw/000660.json"), Source(days=[D])
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat(), name="a")
    assert rc == 1 and rep["days"][0]["status"] == "upload_incomplete" and seal_of(store) is None
    assert rep["store_retries"] >= 3  # 지수형 backoff 재시도 뒤 실패
    store.bad = "__never__"
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat(), name="b")
    up = rep["days"][0]["upload"]
    assert rc == 0 and rep["days"][0]["status"] == "completed"
    assert up["skipped_same"] >= 349 and seal_of(store)["ticker_count"] == 350


def test_damaged_sealed_day_is_reported_not_repaired(tmp_path):
    store, src = MemStore(), Source(days=[D])
    run(tmp_path, store, src, "--date", D.isoformat(), name="a")
    del store.obj["session-minute/2026-11-16/raw/005930.json"]
    rc, rep = run(tmp_path, store, src, "--date", D.isoformat(), name="b")
    assert rc == 1 and rep["days"][0]["status"] == "remote_damaged"
    assert "session-minute/2026-11-16/raw/005930.json" not in store.obj


# ---------- 13. 저장소 연결 실패·재시도 ----------

class Flaky(MemStore):
    def __init__(self, n):
        super().__init__()
        self.left = n

    def head(self, key):
        if self.left > 0:
            self.left -= 1
            raise ConnectionError("r2 unreachable")
        return super().head(key)


def test_store_connection_errors_are_retried_with_backoff(tmp_path):
    waits = []
    store = c.RetryingStore(Flaky(2), sleep=waits.append)
    assert store.head("x") is None and store.retries == 2 and waits == [2.0, 4.0]


def test_store_auth_error_is_not_retried_and_fails(tmp_path):
    class Denied(MemStore):
        def head(self, key):
            raise c.StoreAuthError("R2 권한 오류 403 AccessDenied")

    rc, rep = run(tmp_path, Denied(), Source(days=[D]), "--date", D.isoformat())
    assert rc == 1 and rep["days"][-1]["status"] == "store_auth_error" and rep["store_retries"] == 0


# ---------- 14. 비밀값 ----------

def test_missing_r2_env_lists_names_only(tmp_path, capsys):
    rc = c.main(["--work-dir", str(tmp_path / "w")], now=NOW, env={"R2_BUCKET_NAME": "b"})
    out = capsys.readouterr()
    assert rc == 2 and "R2_ACCOUNT_ID" in out.err and "R2_SECRET_ACCESS_KEY" in out.err


def test_secrets_are_redacted_from_errors_and_report(tmp_path, capsys):
    secret = "s3cr3t-value-0123456789abcdef"
    env = {"R2_ACCOUNT_ID": "acct123456", "R2_ACCESS_KEY_ID": "AKIDEXAMPLE000", "R2_SECRET_ACCESS_KEY": secret, "R2_BUCKET_NAME": "b"}

    class Leaky(MemStore):
        def put(self, key, data, sha256, content_type):
            raise c.StoreAuthError(f"denied for key {secret} at https://acct123456.r2.cloudflarestorage.com")

    rc, rep = run(tmp_path, Leaky(), Source(days=[D]), "--date", D.isoformat(), env=env)
    text = (tmp_path / "w" / "run_report.json").read_text(encoding="utf-8") + capsys.readouterr().out
    assert rc == 1 and secret not in text and "acct123456" not in text and "AKIDEXAMPLE000" not in text
    assert rep["env"] == {k: "set" for k in c.REQUIRED_ENV}
    assert c.redact(f"x {secret} y", env) == "x *** y"


# ---------- 15. DB·Ollama 비의존 ----------

def test_runs_without_db_or_ollama_modules(tmp_path):
    """수집·보존·세션 검증·업로드·봉인 전체를 새 프로세스에서 돌리고, DB·Ollama 모듈이 한 번도 import되지 않았는지 본다."""
    code = "\n".join([
        "import sys, json",
        f"sys.path.insert(0, r'{BACKEND / 'tests'}')",
        "from scripts import run_cloud_session_preservation as c",
        "import test_cloud_session_preservation_67 as t",
        "store = t.MemStore()",
        f"rc = c.main(['--work-dir', r'{tmp_path / 'w'}', '--date', '2026-11-16'], now=t.NOW, store=store,"
        " get=t.Source(days=[t.D]), env={}, sleep=0, retry_waits=(0, 0), wait=lambda s: None, clock=lambda: t.NOW)",
        "bad = sorted(m for m in sys.modules if m.startswith(('app.db', 'app.models', 'ollama', 'psycopg', 'app.embeddings')))",
        "print('RESULT', rc, json.dumps(bad), t.seal_of(store)['ticker_count'])",
    ])
    env = {**os.environ, "DATABASE_URL": "postgresql+psycopg://x:y@127.0.0.1:1/none", "OLLAMA_HOST": "127.0.0.1:1"}
    p = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT")]
    assert line, p.stdout[-2000:] + p.stderr[-2000:]
    assert line[0] == "RESULT 0 [] 350"


def test_dry_run_checks_remote_state_without_requests(tmp_path):
    store, src = MemStore(), Source(days=[D])
    rc, rep = run(tmp_path, store, src, "--dry-run")
    assert rc == 0 and {r["status"] for r in rep["days"]} == {"would_collect"} and len(rep["days"]) == 7
    assert src.calls == [] and store.obj == {}


def test_universe_is_350_with_300_stocks_and_50_etfs():
    assert len(ASSETS) == 350 and sum(a == "etf" for a in ASSETS.values()) == 50


def test_probe_source_requests_three_tickers_only(tmp_path, capsys):
    src = Source(days=[date(2026, 11, 17)])
    assert c.main(["--work-dir", str(tmp_path / "w"), "--probe-source"], now=NOW, get=src, env={}) == 0
    assert len(src.calls) == 3 and {d for _, d in src.calls} == {"20261117"} and not (tmp_path / "w").exists()
    blocked = Source(fail={t: requests.HTTPError("403 Client Error: Forbidden") for t in ASSETS})
    assert c.main(["--work-dir", str(tmp_path / "w"), "--probe-source"], now=NOW, get=blocked, env={}) == 1
    assert '"blocked": true' in capsys.readouterr().out


def test_actions_summary_and_annotations_tolerate_dateless_entries(tmp_path, capsys):
    """R2 인증 오류 항목에는 date가 없다. GitHub 요약·주석이 그 항목에서 깨지지 않아야 한다."""
    class Denied(MemStore):
        def head(self, key):
            raise c.StoreAuthError("R2 권한 오류 403 AccessDenied")

    summary = tmp_path / "summary.md"
    env = {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_RUN_ID": "42"}
    rc, rep = run(tmp_path, Denied(), Source(days=[D]), "--date", D.isoformat(), env=env)
    out = capsys.readouterr().out
    assert rc == 1 and rep["run_id"].endswith("_42")
    assert "::error title=session-minute::failed" in out and "-=store_auth_error" in out
    assert "| - | store_auth_error |" in summary.read_text(encoding="utf-8")
    assert c.main(["--work-dir", str(tmp_path / "p"), "--probe-source"], now=NOW, get=Source(days=[date(2026, 11, 17)]), env=env) == 0
    assert "::notice title=source-probe::date=2026-11-17 ok=True" in capsys.readouterr().out
