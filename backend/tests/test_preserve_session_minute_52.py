"""52차: 분봉 원본 일일 보존(scripts/preserve_session_minute.py)과 원본 확보(scripts/fetch_session_minute.py).
임시 폴더만 쓴다. 네트워크·DB 없이."""
import hashlib
import json
import os
import re
from datetime import date, datetime

import pytest

from app import krx_calendar
from scripts import fetch_session_minute as fsm
from scripts import preserve_session_minute as p

KST = krx_calendar.KST
D = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 21, 0, tzinfo=KST)
TICKERS = ["000080", "005930", "0009K0"]


def bars(day: str = "20261116", base: float = 1000.0) -> list[dict]:
    return [{"localDateTime": f"{day}{hm}00", "openPrice": base, "highPrice": base + 5, "lowPrice": base - 5,
             "currentPrice": base + i, "accumulatedTradingVolume": 100 + i}
            for i, hm in enumerate(("0900", "1200", "1530", "1545", "1600", "1959"))]


def make_source(root, tickers=TICKERS, days=("20261116",), fetched="2026-11-16T20:30:00+09:00", log=True):
    src = root / "staging"
    (src / "raw").mkdir(parents=True)
    for i, t in enumerate(tickers):
        rows = [b for d in days for b in bars(d, 1000.0 + i)]
        (src / "raw" / f"{t}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    if log:
        (src / "fetch_log.json").write_text(json.dumps({"started_at": fetched, "fetched_at": {t: fetched for t in tickers}}),
                                            encoding="utf-8")
    return src


def state(path) -> dict:
    """폴더 안 모든 파일의 (SHA-256, 크기, 수정 시각 ns)."""
    if not path.exists():
        return {}
    out = {}
    for f in sorted(path.rglob("*")):
        if f.is_file():
            out[f.relative_to(path).as_posix()] = (hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_size, f.stat().st_mtime_ns)
    return out


def run(*args, now=NOW) -> int:
    return p.main([str(a) for a in args], now=now)


def preserve_args(src, dest_root, *extra, expected_file=None):
    exp = ["--expected-file", expected_file] if expected_file else ["--expected-from", src]
    return ["--source", src, "--dest-root", dest_root, "--date", "2026-11-16", *exp, *extra]


@pytest.fixture
def env(tmp_path):
    src = make_source(tmp_path)
    return tmp_path, src, tmp_path / "session_minute"


def write_expected(tmp_path, tickers):
    f = tmp_path / "expected.txt"
    f.write_text("# 예상 종목\n" + "\n".join(tickers) + "\n", encoding="utf-8")
    return f


# ---------- 1~3. 정상 복사, SHA-256·개수·용량 ----------

def test_normal_copy_matches_source_sha_count_bytes_and_mtime(env, capsys):
    tmp, src, root = env
    assert run(*preserve_args(src, root)) == 0
    dest = root / "2026-11-16"
    for t in TICKERS:
        a, b = src / "raw" / f"{t}.json", dest / "raw" / f"{t}.json"
        assert a.read_bytes() == b.read_bytes()
        assert hashlib.sha256(a.read_bytes()).hexdigest() == hashlib.sha256(b.read_bytes()).hexdigest()
        assert a.stat().st_mtime_ns // 10**6 == b.stat().st_mtime_ns // 10**6  # 수정 시각 보존(ms)
    src_raw = sorted((src / "raw").iterdir())
    dst_raw = sorted((dest / "raw").iterdir())
    assert len(src_raw) == len(dst_raw) == 3
    assert sum(f.stat().st_size for f in src_raw) == sum(f.stat().st_size for f in dst_raw)
    assert (dest / "source_meta" / p.source_id(src) / "fetch_log.json").read_bytes() == (src / "fetch_log.json").read_bytes()
    assert p.source_id(src).startswith("staging__") and len(p.source_id(src)) == len("staging__") + 8
    # MANIFEST.sha256: sha256sum 형식, LF, 데이터 파일 전부
    text = (dest / p.MANIFEST_SHA).read_bytes().decode()
    assert "\r" not in text
    lines = text.splitlines()
    assert len(lines) == 4 and all(re.fullmatch(r"[0-9a-f]{64}  (raw|source_meta)/\S+", ln) for ln in lines)
    m = json.loads((dest / p.MANIFEST_JSON).read_text(encoding="utf-8"))
    f = m["files"]["raw/005930.json"]
    assert {"sha256", "bytes", "source_rel", "source_mtime", "copy_mtime", "dates", "target_bars"} <= set(f)
    assert m["target_dates"] == ["2026-11-16"] and m["last_run"]["status"] == "ok"
    log = (root / p.ROOT_LOG).read_text(encoding="utf-8").splitlines()
    assert log[0].split("\t") == list(p.ROOT_LOG_FIELDS) and log[1].split("\t")[4] == "ok"
    out = capsys.readouterr().out
    assert "결과: ok" in out and "복사 뒤 재검증: ok" in out


# ---------- 4. 같은 날짜 두 번 ----------

def test_rerun_same_date_creates_nothing_and_overwrites_nothing(env, capsys):
    tmp, src, root = env
    assert run(*preserve_args(src, root)) == 0
    dest = root / "2026-11-16"
    data_before = {k: v for k, v in state(dest).items() if k.startswith(("raw/", "source_meta/"))}
    sha_before = (dest / p.MANIFEST_SHA).read_bytes()
    capsys.readouterr()
    assert run(*preserve_args(src, root)) == 0
    data_after = {k: v for k, v in state(dest).items() if k.startswith(("raw/", "source_meta/"))}
    assert data_after == data_before  # 내용·수정 시각 그대로, 파일 수 그대로
    assert (dest / p.MANIFEST_SHA).read_bytes() == sha_before
    assert not [k for k in state(dest) if k.endswith(p.PARTIAL_SUFFIX)]
    out = capsys.readouterr().out
    assert "복사 0, 이미 보존됨 4, 충돌 0" in out
    assert len((dest / p.RUN_LOG).read_text(encoding="utf-8").splitlines()) == 2


# ---------- 5. 충돌 ----------

def test_conflict_with_different_hash_never_overwrites_and_copies_nothing(env, capsys):
    tmp, src, root = env
    assert run(*preserve_args(src, root)) == 0
    dest = root / "2026-11-16"
    before = state(dest)
    (src / "raw" / "005930.json").write_text(json.dumps(bars(base=9999.0)), encoding="utf-8")  # 다른 원본
    (src / "raw" / "000100.json").write_text(json.dumps(bars()), encoding="utf-8")  # 새 종목도 같이
    capsys.readouterr()
    assert run(*preserve_args(src, root)) == 1
    after = state(dest)
    assert {k: v for k, v in after.items() if k != p.RUN_LOG} == {k: v for k, v in before.items() if k != p.RUN_LOG}
    assert not (dest / "raw" / "000100.json").exists()  # 충돌이 있으면 다른 파일도 복사하지 않는다
    out = capsys.readouterr().out
    assert "SHA-256이 다른 파일 1개" in out and "결과: failed" in out


# ---------- 6. 원본 파일 누락(복사 도중 사라짐·보존본 파일 삭제) ----------

def test_source_file_vanishing_during_copy_is_error_and_not_finalized(env, monkeypatch, capsys):
    tmp, src, root = env
    real = p.copy_verified

    def vanish(s, d, sha, size):
        if s.name == "005930.json":
            s.unlink()
        return real(s, d, sha, size)

    monkeypatch.setattr(p, "copy_verified", vanish)
    assert run(*preserve_args(src, root, expected_file=write_expected(tmp, TICKERS))) == 1
    dest = root / "2026-11-16"
    assert not (dest / "raw" / "005930.json").exists()
    assert (dest / "raw" / "000080.json").exists()
    assert "005930.json" not in (dest / p.MANIFEST_SHA).read_text(encoding="utf-8")
    assert "복사 실패" in capsys.readouterr().out


def test_verify_only_reports_deleted_preserved_file(env, capsys):
    tmp, src, root = env
    assert run(*preserve_args(src, root)) == 0
    dest = root / "2026-11-16"
    (dest / "raw" / "000080.json").unlink()
    capsys.readouterr()
    assert run("--verify-only", "--dest", dest) == 1
    out = capsys.readouterr().out
    assert "없음 1" in out and "missing_tickers" in out


# ---------- 7. 예상 종목 누락 ----------

def test_missing_expected_ticker_fails_without_placeholder(env, capsys):
    tmp, src, root = env
    exp = write_expected(tmp, TICKERS + ["000660"])
    assert run(*preserve_args(src, root, expected_file=exp)) == 1
    dest = root / "2026-11-16"
    assert not (dest / "raw" / "000660.json").exists()
    assert len(list((dest / "raw").iterdir())) == 3  # 있는 것은 보존한다
    out = capsys.readouterr().out
    assert "예상 종목 4, 발견 3, 이미 보존됨(이번 원본 밖) 0, 누락 1" in out and "000660" in out and "결과: failed" in out
    assert run("--verify-only", "--dest", dest) == 1  # 검증에서도 누락으로 보인다


def test_allow_missing_turns_confirmed_missing_into_warning(env, capsys):
    tmp, src, root = env
    exp = write_expected(tmp, TICKERS + ["000660"])
    assert run(*preserve_args(src, root, "--allow-missing", "000660", expected_file=exp)) == 0
    assert "결과: warning" in capsys.readouterr().out
    assert run("--verify-only", "--dest", root / "2026-11-16") == 0


def test_unexpected_ticker_is_copied_with_warning(env, capsys):
    tmp, src, root = env
    assert run(*preserve_args(src, root, expected_file=write_expected(tmp, TICKERS[:2]))) == 0
    out = capsys.readouterr().out
    assert "예상 밖 1" in out and "결과: warning" in out


def test_duplicate_tickers_in_expected_list_fail(env, capsys):
    tmp, src, root = env
    assert run(*preserve_args(src, root, expected_file=write_expected(tmp, TICKERS + ["000080"]))) == 1
    assert "중복 종목" in capsys.readouterr().out


def test_case_duplicates():
    assert p.case_duplicates(["0009K0", "0009k0", "000080"]) == [["0009K0", "0009k0"]]
    assert p.case_duplicates(["000080", "000080"]) == [["000080", "000080"]]
    assert p.case_duplicates(["000080"]) == []


# ---------- 8. 복사 도중 실패 ----------

def test_failure_mid_copy_leaves_no_final_or_partial_file_and_rerun_recovers(env, monkeypatch, capsys):
    tmp, src, root = env
    monkeypatch.setattr(p, "CHUNK", 64)
    state_ = {"n": 0}

    def boom(fo, chunk):
        if fo.name.endswith(".005930.json.partial"):
            state_["n"] += 1
            if state_["n"] == 2:
                raise OSError("disk full (테스트)")
        fo.write(chunk)

    monkeypatch.setattr(p, "_write_chunk", boom)
    assert run(*preserve_args(src, root)) == 1
    dest = root / "2026-11-16"
    assert not (dest / "raw" / "005930.json").exists()
    assert not [k for k in state(dest) if k.endswith(p.PARTIAL_SUFFIX)]
    assert "005930.json" not in (dest / p.MANIFEST_SHA).read_text(encoding="utf-8")
    monkeypatch.setattr(p, "_write_chunk", lambda fo, chunk: fo.write(chunk))
    capsys.readouterr()
    assert run(*preserve_args(src, root)) == 0  # 다시 실행하면 빠진 파일만 복사
    out = capsys.readouterr().out
    assert "복사 1, 이미 보존됨 3" in out
    assert (dest / "raw" / "005930.json").read_bytes() == (src / "raw" / "005930.json").read_bytes()


def test_corrupted_copy_is_not_finalized(env, monkeypatch, capsys):
    tmp, src, root = env
    # "1000.0"은 000080 파일에만 있다(base 1000). 그 파일만 사본이 원본과 달라진다
    monkeypatch.setattr(p, "_write_chunk", lambda fo, chunk: fo.write(chunk.replace(b"1000.0", b"1009.0")))
    assert run(*preserve_args(src, root)) == 1
    raw = root / "2026-11-16" / "raw"
    assert not (raw / "000080.json").exists()
    assert not [f for f in raw.iterdir() if f.name.endswith(p.PARTIAL_SUFFIX)]
    assert (raw / "005930.json").read_bytes() == (src / "raw" / "005930.json").read_bytes()
    assert "사본 SHA-256/크기가 원본과 다릅니다" in capsys.readouterr().out


def test_interrupted_first_run_is_resumed_not_refused(env, monkeypatch):
    tmp, src, root = env
    real, calls = p.copy_verified, {"n": 0}

    def crash_after_one(*a):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt  # 프로세스 중단 흉내
        return real(*a)

    monkeypatch.setattr(p, "copy_verified", crash_after_one)
    with pytest.raises(KeyboardInterrupt):
        run(*preserve_args(src, root))
    dest = root / "2026-11-16"
    assert (dest / p.MANIFEST_JSON).is_file()  # 첫 복사 전에 쓴 표시
    monkeypatch.setattr(p, "copy_verified", real)
    assert run(*preserve_args(src, root)) == 0
    assert run("--verify-only", "--dest", dest) == 0


# ---------- 9. dry-run ----------

def test_dry_run_changes_nothing(env, capsys):
    tmp, src, root = env
    before = state(tmp)
    assert run(*preserve_args(src, root, "--dry-run")) == 0
    assert state(tmp) == before and not root.exists()
    assert "복사 예정 4" in capsys.readouterr().out


def test_dry_run_on_existing_preservation_changes_nothing(env):
    tmp, src, root = env
    assert run(*preserve_args(src, root)) == 0
    before = state(tmp)
    assert run(*preserve_args(src, root, "--dry-run")) == 0
    assert state(tmp) == before


# ---------- 10. verify-only ----------

def test_verify_only_is_read_only_and_detects_tamper(env, capsys):
    tmp, src, root = env
    assert run(*preserve_args(src, root)) == 0
    dest = root / "2026-11-16"
    before = state(tmp)
    assert run("--verify-only", "--dest", dest, "--source", src) == 0
    assert state(tmp) == before
    with open(dest / "raw" / "000080.json", "ab") as fh:
        fh.write(b" ")
    tampered = state(tmp)
    capsys.readouterr()
    assert run("--verify-only", "--dest", dest) == 1
    assert state(tmp) == tampered
    assert "불일치 1" in capsys.readouterr().out


def test_verify_only_with_external_manifest_like_round51(tmp_path, capsys):
    d = tmp_path / "session_audit_x"
    (d / "raw").mkdir(parents=True)
    (d / "raw" / "000080.json").write_text(json.dumps(bars()), encoding="utf-8")
    (d / "README.md").write_text("보존본", encoding="utf-8")
    sha = hashlib.sha256((d / "raw" / "000080.json").read_bytes()).hexdigest()
    m = tmp_path / "x.sha256"
    m.write_bytes(f"{sha}  raw/000080.json\n".encode())
    before = state(tmp_path)
    assert run("--verify-only", "--dest", d, "--manifest", m) == 0
    assert state(tmp_path) == before
    assert "결과: ok" in capsys.readouterr().out


# ---------- 보호 규칙 ----------

def test_refuses_existing_directory_not_created_by_tool(env, capsys):
    tmp, src, root = env
    foreign = root / "2026-11-16"
    (foreign / "raw").mkdir(parents=True)
    (foreign / "README.md").write_text("51차 보존본처럼 생긴 폴더", encoding="utf-8")
    before = state(tmp)
    assert run(*preserve_args(src, root)) == 2
    assert state(tmp) == before


def test_refuses_dest_root_with_foreign_items(env):
    tmp, src, root = env
    root.mkdir()
    (root / "ai_investment_backup.dump").write_bytes(b"x")
    before = state(tmp)
    assert run(*preserve_args(src, root)) == 2
    assert state(tmp) == before


def test_refuses_overlapping_source_and_dest(env):
    tmp, src, root = env
    before = state(tmp)
    assert run(*preserve_args(src, src / "inner")) == 2
    assert run(*preserve_args(src, tmp)) == 2  # 원본이 dest-root 안
    assert state(tmp) == before


def test_refuses_non_trading_day_and_bad_arguments(env):
    tmp, src, root = env
    assert run("--source", src, "--dest-root", root, "--date", "2026-11-15", "--expected-from", src) == 2  # 일요일
    assert run("--source", src, "--dest-root", root, "--date", "2026-11-16") == 2  # 예상 종목 기준 없음
    assert run("--verify-only") == 2
    assert not root.exists()


def test_json_out_must_be_outside_preserved_dirs(env):
    tmp, src, root = env
    assert run(*preserve_args(src, root)) == 0
    dest = root / "2026-11-16"
    before = state(dest)
    assert run("--verify-only", "--dest", dest, "--json-out", dest / "r.json") == 2
    assert state(dest) == before
    assert run("--verify-only", "--dest", dest, "--json-out", tmp / "r.json") == 0
    assert json.loads((tmp / "r.json").read_text(encoding="utf-8"))["status"] == "ok"


# ---------- 내용 검사 ----------

def test_file_without_target_date_bars_is_abnormal_and_not_copied(tmp_path, capsys):
    src = make_source(tmp_path)
    (src / "raw" / "005930.json").write_text(json.dumps(bars("20261113")), encoding="utf-8")
    root = tmp_path / "session_minute"
    assert run(*preserve_args(src, root)) == 1
    assert not (root / "2026-11-16" / "raw" / "005930.json").exists()
    out = capsys.readouterr().out
    assert "no_target_bars: 2026-11-16" in out and "날짜별 파일 {'2026-11-16': 2}" in out


@pytest.mark.parametrize("content", [b"{not json", b"{}", b"[]", json.dumps([{"localDateTime": "x"}]).encode()])
def test_malformed_files_are_abnormal(tmp_path, content):
    src = make_source(tmp_path)
    (src / "raw" / "005930.json").write_bytes(content)
    root = tmp_path / "session_minute"
    assert run(*preserve_args(src, root)) == 1
    assert not (root / "2026-11-16" / "raw" / "005930.json").exists()


def test_collected_before_day_complete_is_abnormal(tmp_path, capsys):
    src = make_source(tmp_path, fetched="2026-11-16T19:00:00+09:00")
    assert run(*preserve_args(src, tmp_path / "session_minute")) == 1
    assert "incomplete_day" in capsys.readouterr().out


def test_extra_dates_are_preserved_byte_for_byte_with_warning(tmp_path, capsys):
    src = make_source(tmp_path, days=("20261113", "20261116"))
    root = tmp_path / "session_minute"
    assert run(*preserve_args(src, root)) == 0
    assert (root / "2026-11-16" / "raw" / "000080.json").read_bytes() == (src / "raw" / "000080.json").read_bytes()
    assert "대상 밖 날짜" in capsys.readouterr().out


def test_range_label_and_per_date_counts(tmp_path, capsys):
    src = make_source(tmp_path, days=("20261113", "20261116"), fetched="2026-11-16T21:00:00+09:00")
    root = tmp_path / "session_minute"
    assert run("--source", src, "--dest-root", root, "--start", "2026-11-13", "--end", "2026-11-16", "--expected-from", src) == 0
    assert (root / "2026-11-13_2026-11-16" / p.MANIFEST_SHA).is_file()
    assert "'2026-11-13': 3, '2026-11-16': 3" in capsys.readouterr().out


def test_without_fetch_log_uses_file_mtime_with_warning(tmp_path, capsys):
    src = make_source(tmp_path, log=False)
    for f in (src / "raw").iterdir():
        os.utime(f, (NOW.timestamp(), NOW.timestamp()))
    assert run(*preserve_args(src, tmp_path / "session_minute")) == 0
    assert "파일 수정 시각" in capsys.readouterr().out


# ---------- 원본 확보(fetch_session_minute) ----------

def fake_get(day="20261116", empty=(), fail=()):
    calls = []

    def get(url, params):
        t = url.split("/item/")[1].split("/")[0]
        calls.append((t, params))
        if t in fail:
            raise RuntimeError("원천 오류(테스트)")
        return [] if t in empty else bars(day)
    get.calls = calls
    return get


def test_fetch_refuses_before_day_complete_and_non_trading_day(tmp_path):
    tl = write_expected(tmp_path, TICKERS)
    out = tmp_path / "staging"
    get = fake_get()
    early = datetime(2026, 11, 16, 19, 59, tzinfo=KST)
    assert fsm.main(["--date", "2026-11-16", "--tickers-file", str(tl), "--out", str(out)], now=early, get=get, sleep=0) == 2
    assert fsm.main(["--date", "2026-11-15", "--tickers-file", str(tl), "--out", str(out)], now=NOW, get=get, sleep=0) == 2
    assert not out.exists() and get.calls == []


def test_fetch_refuses_non_empty_out(tmp_path):
    tl = write_expected(tmp_path, TICKERS)
    out = tmp_path / "staging"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    assert fsm.main(["--date", "2026-11-16", "--tickers-file", str(tl), "--out", str(out)], now=NOW, get=fake_get(), sleep=0) == 2
    assert [f.name for f in out.iterdir()] == ["keep.txt"]


def test_fetch_dry_run_makes_no_request_and_no_file(tmp_path):
    tl = write_expected(tmp_path, TICKERS)
    get = fake_get()
    out = tmp_path / "staging"
    assert fsm.main(["--date", "2026-11-16", "--tickers-file", str(tl), "--out", str(out), "--dry-run"],
                    now=NOW, get=get, sleep=0) == 0
    assert get.calls == [] and not out.exists()


def test_fetch_then_preserve_end_to_end(tmp_path, capsys):
    tl = write_expected(tmp_path, TICKERS + ["000660", "000100"])
    out = tmp_path / "staging"
    get = fake_get(empty={"000660"}, fail={"000100"})
    assert fsm.main(["--date", "2026-11-16", "--tickers-file", str(tl), "--out", str(out)], now=NOW, get=get, sleep=0,
                    clock=lambda: NOW) == 1
    assert sorted(f.stem for f in (out / "raw").iterdir()) == sorted(TICKERS)  # 봉 없음·실패 종목은 파일 없음
    assert get.calls[0][1] == {"startDateTime": "202611160000", "endDateTime": "202611162359"}
    log = json.loads((out / "fetch_log.json").read_text(encoding="utf-8"))
    assert [x["ticker"] for x in log["no_bars"]] == ["000660"] and [x["ticker"] for x in log["failed"]] == ["000100"]
    assert log["complete"] is True and set(log["fetched_at"]) == set(TICKERS)
    assert not [f for f in out.rglob("*") if f.name.endswith(".partial")]
    root = tmp_path / "session_minute"
    capsys.readouterr()
    assert run("--source", out, "--dest-root", root, "--date", "2026-11-16", "--expected-file", tl) == 1
    assert "누락 2" in capsys.readouterr().out
    # 거래정지 등으로 확인한 경우만 허용 -> warning, 같은 날짜 재실행이라 이미 보존된 3개는 그대로
    assert run("--source", out, "--dest-root", root, "--date", "2026-11-16", "--expected-file", tl,
               "--allow-missing", "000660,000100") == 0
    assert "복사 0, 이미 보존됨 4" in capsys.readouterr().out


def test_fetch_collected_before_complete_is_rejected_by_preserve(tmp_path, capsys):
    tl = write_expected(tmp_path, TICKERS)
    out = tmp_path / "staging"
    # now 검사를 우회해 20:10 전 수집 기록을 만든 경우에도 보존 단계가 막는다
    assert fsm.main(["--date", "2026-11-16", "--tickers-file", str(tl), "--out", str(out)], now=NOW, get=fake_get(), sleep=0,
                    clock=lambda: datetime(2026, 11, 16, 19, 0, tzinfo=KST)) == 0
    capsys.readouterr()
    assert run("--source", out, "--dest-root", tmp_path / "sm", "--date", "2026-11-16", "--expected-file", tl) == 1
    assert "incomplete_day" in capsys.readouterr().out


def test_retry_with_only_failed_tickers_completes_same_date(tmp_path, capsys):
    """원천 오류로 빠진 종목만 새 스테이징에 다시 받아 같은 날짜 보존본에 보태는 재실행(운영 문서 7절)."""
    full = write_expected(tmp_path, TICKERS)
    first = make_source(tmp_path, tickers=TICKERS[:2])
    root = tmp_path / "session_minute"
    assert run("--source", first, "--dest-root", root, "--date", "2026-11-16", "--expected-file", full) == 1  # 1개 누락
    second_root = tmp_path / "retry"
    second_root.mkdir()
    second = make_source(second_root, tickers=TICKERS[2:])
    capsys.readouterr()
    assert run("--source", second, "--dest-root", root, "--date", "2026-11-16", "--expected-file", full) == 0
    out = capsys.readouterr().out
    assert "발견 1, 이미 보존됨(이번 원본 밖) 2, 누락 0" in out and "결과: ok" in out
    dest = root / "2026-11-16"
    assert sorted(f.stem for f in (dest / "raw").iterdir()) == sorted(TICKERS)
    assert run("--verify-only", "--dest", dest) == 0
