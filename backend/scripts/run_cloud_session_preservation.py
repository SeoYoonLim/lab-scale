"""GitHub Actions에서 거래일 분봉 원본을 받아 Cloudflare R2에 날짜별로 보존한다(2026-11-17, 67차). DB·Ollama를 쓰지 않는다.

운영 문서: docs/SESSION_MINUTE_CLOUD_AUTOMATION_67.md (workflow: .github/workflows/session-minute-cloud.yml)

  python -m scripts.run_cloud_session_preservation --work-dir "$RUNNER_TEMP/smc"            # R2(환경 변수 4개 필요)
  python -m scripts.run_cloud_session_preservation --work-dir /tmp/smc --store local --local-store-dir /tmp/r2 # 로컬 흉내
  python -m scripts.run_cloud_session_preservation ... --dry-run   # 원천 요청·업로드 없이 R2 상태와 대상일만 확인
  python -m scripts.run_cloud_session_preservation ... --date 2026-11-16   # 그 하루만(원천 조회 가능 기간 안에서만)

기존 코드를 그대로 쓴다(바꾸지 않음):
- 수집: scripts.fetch_session_minute.fetch / check_target(52차, 원천 api.stock.naver.com 분봉, 20:10 KST 뒤에만)
- 보존·SHA-256: scripts.preserve_session_minute.preserve / verify(52차, raw/{ticker}.json + MANIFEST.sha256 + preserve_manifest.json)
- 세션 검증: scripts.load_session_minute.Loader(mode=validate, 54차). 종목 표만 DB 대신 유니버스 파일에서 읽는다(OfflineLoader)
- 거래일: app.krx_calendar(휴장일·미확인일), 원천 조회 가능 기간 = 최근 7거래일(app.session_ohlcv.fetch_minute 설명)

R2 객체(버킷 비공개):
  session-minute/<날짜>/raw/{ticker}.json, source_meta/<원본 id>/fetch_log.json   원본(바이트 그대로, 한 번 쓰면 다시 쓰지 않음)
  session-minute/<날짜>/MANIFEST.sha256, preserve_manifest.json, preserve_log.jsonl, session_validate.json   색인(봉인 전에만 갱신)
  session-minute/<날짜>/manifest.json   봉인. 모든 파일 업로드·원격 SHA-256 재검증·350종목·세션 검증이 끝난 뒤 마지막에 쓴다
  session-minute/_status/<날짜>.json    원천 조회 기간이 지나 복구할 수 없음 표시
  session-minute/_runs/<실행 id>.json   실행마다 결과 기록

멱등성: 봉인된 날짜는 건너뛴다. 원본 객체가 이미 있고 SHA-256이 같으면 건너뛰고, 다르면 그 날짜는 아무것도 올리지 않고 충돌로 남긴다
(자동 덮어쓰기 없음). 봉인 전 날짜는 다음 실행이 처음부터 다시 받아 빠진 파일만 채운다.

종료 코드: 0 = 대상일이 모두 봉인됨(또는 휴장일 SKIP, 이미 알려진 복구 불가), 1 = 실패·충돌·미완료·새 복구 불가, 2 = 설정·인자 오류.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from app import krx_calendar, session_ohlcv as so, session_schedule
from scripts import fetch_session_minute as fsm
from scripts import preserve_session_minute as psm

TOOL = "run_cloud_session_preservation"
VERSION = 1
KST = krx_calendar.KST
PREFIX = "session-minute"
UNIVERSE_FILE = Path(__file__).resolve().parents[1] / "app" / "data" / "session_minute_universe.csv"
EXPECTED_ASSETS = {"stock": 300, "etf": 50}
PROVIDER_WINDOW_TRADING_DAYS = 7  # 원천은 최근 7거래일만 준다(그보다 앞은 빈 목록)
DEFAULT_LOOKBACK = 7
PROBE_TICKERS = 3  # 첫 3종목이 모두 "그날 봉 없음"이면 원천 기간이 지난 것으로 보고 나머지를 요청하지 않는다
BLOCK_RE = re.compile(r"\b(401|403|429)\b")
BLOCK_LIMIT = 3  # 차단·제한 응답이 이만큼 나오면 그 실행의 원천 요청을 멈춘다(우회 재시도 없음)
DEFAULT_RETRY_WAITS = (30.0, 90.0)  # 실패 종목 재수집 간격(지수형). http_retry의 요청별 재시도와 별개
REQUIRED_ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME")
SECRET_ENV = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID")
SEAL = "manifest.json"
VALIDATE_FILE = "session_validate.json"
INDEX_FILES = {psm.MANIFEST_SHA, psm.MANIFEST_JSON, psm.RUN_LOG, VALIDATE_FILE}  # 봉인 전에는 갱신할 수 있는 색인
OK_DAY = {"already_complete", "completed", "unrecoverable_known"}


class ConfigError(Exception):
    """설정·인자 오류(종료 코드 2)."""


class StoreAuthError(Exception):
    """저장소 인증·권한 오류. 다시 시도하지 않는다."""


# ---------- 비밀값 가리기 ----------

def redact(msg: str, env: dict | None = None) -> str:
    env = os.environ if env is None else env
    out = str(msg)
    for k in SECRET_ENV:
        v = env.get(k)
        if v and len(v) >= 4:
            out = out.replace(v, "***")
    return out


# ---------- 저장소 ----------

class ObjectStore(Protocol):
    def head(self, key: str) -> dict | None: ...  # {"size": int, "sha256": str | None} 또는 None(없음)
    def put(self, key: str, data: bytes, sha256: str, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def list(self, prefix: str) -> list[str]: ...


class LocalStore:
    """디렉터리를 버킷처럼 쓴다(로컬 시험·테스트용). 메타데이터는 <키>.meta.json 대신 내용 해시로 대신한다."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _p(self, key: str) -> Path:
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError(f"잘못된 키: {key}")
        return self.root / key

    def head(self, key):
        p = self._p(key)
        if not p.is_file():
            return None
        return {"size": p.stat().st_size, "sha256": psm.sha256_file(p)[0]}

    def put(self, key, data, sha256, content_type):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name("." + p.name + ".partial")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key):
        return self._p(key).read_bytes()

    def list(self, prefix):
        base = self.root
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*")
                      if p.is_file() and p.relative_to(base).as_posix().startswith(prefix) and not p.name.endswith(".partial"))


class R2Store:
    """Cloudflare R2(S3 호환). 자격 증명은 환경 변수에서만 읽는다. 공개 URL·presigned URL을 만들지 않는다."""

    def __init__(self, env: dict):
        import boto3  # requirements-cloud.txt. 로컬 백엔드 환경에는 없어도 된다
        from botocore.config import Config

        self.bucket = env["R2_BUCKET_NAME"]
        self.c = boto3.client(
            "s3", endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com", region_name="auto",
            aws_access_key_id=env["R2_ACCESS_KEY_ID"], aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"],
            config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=10, read_timeout=60,
                          # R2는 boto3 1.36+의 기본 CRC 체크섬 헤더를 일부 요청에서 받지 않는다 → 필요할 때만
                          request_checksum_calculation="when_required", response_checksum_validation="when_required"))

    def _err(self, e):
        from botocore.exceptions import ClientError

        if isinstance(e, ClientError):
            st = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = e.response.get("Error", {}).get("Code", "")
            if st in (401, 403) or code in ("AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
                return StoreAuthError(f"R2 권한 오류 {st} {code}")
        return e

    def head(self, key):
        from botocore.exceptions import ClientError

        try:
            r = self.c.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404 or \
                    e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise self._err(e) from None
        return {"size": r["ContentLength"], "sha256": (r.get("Metadata") or {}).get("sha256")}

    def put(self, key, data, sha256, content_type):
        try:
            self.c.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, Metadata={"sha256": sha256})
        except Exception as e:  # noqa: BLE001
            raise self._err(e) from None

    def get(self, key):
        try:
            return self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as e:  # noqa: BLE001
            raise self._err(e) from None

    def list(self, prefix):
        out = []
        try:
            for page in self.c.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                out += [o["Key"] for o in page.get("Contents", [])]
        except Exception as e:  # noqa: BLE001
            raise self._err(e) from None
        return sorted(out)


class RetryingStore:
    """저장소 호출을 지수형 backoff로 다시 시도한다. 인증 오류는 다시 시도하지 않는다."""

    def __init__(self, inner, attempts: int = 4, base: float = 2.0, sleep: Callable[[float], None] = time.sleep):
        self.inner, self.attempts, self.base, self.sleep = inner, attempts, base, sleep
        self.retries = 0

    def _call(self, name, *a):
        for i in range(self.attempts):
            try:
                return getattr(self.inner, name)(*a)
            except StoreAuthError:
                raise
            except Exception:  # noqa: BLE001
                if i == self.attempts - 1:
                    raise
                self.retries += 1
                self.sleep(self.base * (2 ** i))

    def head(self, key):
        return self._call("head", key)

    def put(self, key, data, sha256, content_type):
        return self._call("put", key, data, sha256, content_type)

    def get(self, key):
        return self._call("get", key)

    def list(self, prefix):
        return self._call("list", prefix)


# ---------- 유니버스 ----------

def load_universe(path: Path = UNIVERSE_FILE) -> dict[str, str]:
    """ticker -> asset_type. 350종목(주식 300·ETF 50)이 아니면 ConfigError."""
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    rows = list(csv.DictReader(lines))
    out: dict[str, str] = {}
    for r in rows:
        t, a = r["ticker"].strip(), r["asset_type"].strip()
        if not psm.TICKER_RE.fullmatch(t) or a not in EXPECTED_ASSETS or t in out:
            raise ConfigError(f"유니버스 파일 행 오류: {r}")
        out[t] = a
    counts = {a: sum(1 for v in out.values() if v == a) for a in EXPECTED_ASSETS}
    if counts != EXPECTED_ASSETS:
        raise ConfigError(f"유니버스 종목 수가 다릅니다: {counts}, 기대 {EXPECTED_ASSETS}")
    return dict(sorted(out.items()))


# ---------- 대상일 ----------

def latest_complete_day(now: datetime, cal=None) -> date:
    """그날 분봉이 끝난 가장 최근 거래일(오늘이 거래일이고 20:10 뒤면 오늘)."""
    today = krx_calendar._kst(now).date()
    st = krx_calendar.is_trading_day(today, cal)
    if st is None:
        raise ConfigError(f"오늘({today})은 KRX 달력 범위 밖입니다. app/data/krx_calendar.json 갱신이 필요합니다")
    if st and so.day_complete(today, now):
        return today
    return krx_calendar.previous_trading_day(today, cal)


def recent_trading_days(end: date, n: int, cal=None) -> list[date]:
    """end를 포함한 최근 n개 거래일(오래된 순). 미확인일을 만나면 거기서 멈춘다(그보다 앞은 보지 않음)."""
    out = [end]
    while len(out) < n:
        p = krx_calendar.previous_trading_day(out[-1], cal)
        if krx_calendar.is_trading_day(p, cal) is not True:
            break
        out.append(p)
    return sorted(out)


# ---------- 세션 검증(54차 Loader 재사용) ----------

class _NoDB:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):  # Loader.run의 SET TRANSACTION READ ONLY만 받는다
        return None


def validate_sessions(dest: Path, d: date, assets: dict[str, str], clock=None) -> dict:
    """54차 load_session_minute.Loader(mode=validate)를 DB 없이 돌린다. 종목 표만 유니버스로 대신한다."""
    from scripts import load_session_minute as lsm

    class OfflineLoader(lsm.Loader):
        def _connect_checked(self):
            return _NoDB()

        def companies(self, conn):
            return {t: (0, a) for t, a in assets.items()}

    rep = OfflineLoader(None, dest, [d], None, "validate", "offline:universe", clock=clock).run()
    c = rep["counts"]
    ok = (c.get("valid_days", 0) == len(assets) and not rep["rejected"] and not c.get("missing_date")
          and c.get("files", 0) == len(assets))
    return {"ok": ok, "status": rep["status"], "counts": dict(c), "flags": rep["flags"], "rejected": rep["rejected"][:50],
            "rules_version": rep["rules_version"], "schedule_version": rep["schedule_version"], "report": rep}


# ---------- 원격 상태 ----------

def day_prefix(d: date) -> str:
    return f"{PREFIX}/{d.isoformat()}/"


def remote_state(store, d: date, deep: bool = False) -> dict:
    """complete | damaged | partial | absent | unrecoverable_known."""
    pre = day_prefix(d)
    if store.head(pre + SEAL):
        seal = json.loads(store.get(pre + SEAL).decode("utf-8"))
        bad = []
        for rel, f in seal.get("files", {}).items():
            h = store.head(pre + rel)
            if h is None or h["size"] != f["bytes"]:
                bad.append({"file": rel, "problem": "missing" if h is None else "size"})
            elif deep or not h.get("sha256"):
                if hashlib.sha256(store.get(pre + rel)).hexdigest() != f["sha256"]:
                    bad.append({"file": rel, "problem": "sha256"})
            elif h["sha256"] != f["sha256"]:
                bad.append({"file": rel, "problem": "sha256_meta"})
        return {"state": "damaged" if bad else "complete", "problems": bad[:50], "files": len(seal.get("files", {}))}
    if store.head(f"{PREFIX}/_status/{d.isoformat()}.json"):
        return {"state": "unrecoverable_known"}
    keys = store.list(pre)
    return {"state": "partial" if keys else "absent", "objects": len(keys)}


# ---------- 업로드 ----------

def _ctype(rel: str) -> str:
    return "application/json" if rel.endswith(".json") else ("application/x-ndjson" if rel.endswith(".jsonl") else "text/plain")


def upload_day(store, dest: Path, d: date, extra: dict[str, bytes]) -> dict:
    """보존 디렉터리 + 추가 파일을 올리고 원격에서 다시 읽어 SHA-256을 대조한다. 봉인(manifest.json)은 쓰지 않는다."""
    pre = day_prefix(d)
    files: dict[str, bytes] = {p.relative_to(dest).as_posix(): p.read_bytes() for p in sorted(dest.rglob("*")) if p.is_file()
                               and not p.name.endswith(psm.PARTIAL_SUFFIX)}
    files.update(extra)
    out = {"uploaded": 0, "skipped_same": 0, "conflicts": [], "verify_failed": [], "errors": [], "files": {}}
    plan = []
    for rel in sorted(files, key=lambda r: (r in INDEX_FILES, r)):  # 원본 먼저, 색인 나중
        data = files[rel]
        sha = hashlib.sha256(data).hexdigest()
        out["files"][rel] = {"bytes": len(data), "sha256": sha}
        if rel in INDEX_FILES:
            plan.append((rel, data, sha))
            continue
        h = store.head(pre + rel)
        if h is None:
            plan.append((rel, data, sha))
            continue
        remote_sha = h.get("sha256") or hashlib.sha256(store.get(pre + rel)).hexdigest()
        if remote_sha == sha and h["size"] == len(data):
            out["skipped_same"] += 1
        else:
            out["conflicts"].append({"file": rel, "local_sha256": sha, "remote_sha256": remote_sha, "remote_bytes": h["size"]})
    if out["conflicts"]:  # 충돌이 하나라도 있으면 그 날짜는 아무것도 올리지 않는다
        return out
    for rel, data, sha in plan:
        try:
            store.put(pre + rel, data, sha, _ctype(rel))
            got = store.get(pre + rel)
        except StoreAuthError:
            raise
        except Exception as e:  # noqa: BLE001 - 파일 하나의 실패로 기록을 잃지 않는다
            out["errors"].append({"file": rel, "error": str(e)[:200]})
            continue
        if hashlib.sha256(got).hexdigest() != sha or len(got) != len(data):
            out["verify_failed"].append(rel)
        else:
            out["uploaded"] += 1
    return out


# ---------- 하루 처리 ----------

@dataclass
class Ctx:
    store: object
    work: Path
    now: datetime
    assets: dict[str, str]
    get: Callable | None = None
    sleep: float = 0.35
    retry_waits: tuple = DEFAULT_RETRY_WAITS
    wait: Callable[[float], None] = time.sleep
    clock: Callable[[], datetime] | None = None
    code_version: str = "unknown"
    run_id: str = ""
    deep_verify: bool = False
    dry_run: bool = False
    blocked: bool = False
    stamp: str = ""
    env: dict = field(default_factory=dict)


def _blocked_errors(log: dict) -> list[dict]:
    return [f for f in log.get("failed", []) if BLOCK_RE.search(f.get("error", ""))]


def _seal_doc(ctx: Ctx, d: date, dest: Path, up: dict, val: dict, attempts: list[dict]) -> dict:
    pm = json.loads((dest / psm.MANIFEST_JSON).read_text(encoding="utf-8"))
    tickers = {}
    for rel, f in pm["files"].items():
        if f.get("kind") == "raw":
            tickers[f["ticker"]] = {"file": rel, "rows": f["target_bars"].get(d.isoformat()), "bytes": f["bytes"],
                                    "sha256": f["sha256"], "asset_type": ctx.assets.get(f["ticker"])}
    now = (ctx.clock or (lambda: datetime.now(KST)))()
    sched, why = session_schedule.resolve(d)
    return {
        "tool": TOOL, "version": VERSION, "status": "complete", "trade_date": d.isoformat(),
        "sealed_at_kst": now.astimezone(KST).isoformat(timespec="seconds"),
        "sealed_at_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "run_id": ctx.run_id, "code_version": ctx.code_version,
        "source": {"provider": "naver_minute", "url": so.MINUTE_URL, "note": "비공식 API, KRX 체결만(NXT 제외)"},
        "krx": {"open": krx_calendar.open_time(d).strftime("%H:%M"), "close": krx_calendar.close_time(d).strftime("%H:%M"),
                "schedule": sched.describe() if sched else why},
        "ticker_count": len(tickers), "assets": {a: sum(1 for t in tickers.values() if t["asset_type"] == a) for a in EXPECTED_ASSETS},
        "tickers": dict(sorted(tickers.items())),
        "files": up["files"], "file_count": len(up["files"]), "total_bytes": sum(f["bytes"] for f in up["files"].values()),
        "session_validation": {k: val[k] for k in ("ok", "status", "counts", "flags", "rules_version", "schedule_version")},
        "fetch_attempts": attempts,
    }


def process_day(ctx: Ctx, d: date) -> dict:
    t0 = time.monotonic()
    rec: dict = {"date": d.isoformat()}
    try:
        rec.update(_process_day(ctx, d, rec))
    except StoreAuthError as e:
        rec.update(status="store_auth_error", error=redact(e, ctx.env))
        raise
    except Exception as e:  # noqa: BLE001 - 하루의 오류로 다른 날짜를 막지 않는다
        rec.update(status="error", error=redact(f"{type(e).__name__}: {e}", ctx.env)[:300])
    rec["seconds"] = round(time.monotonic() - t0, 1)
    return rec


def _process_day(ctx: Ctx, d: date, rec: dict) -> dict:
    st = krx_calendar.is_trading_day(d)
    if st is None:
        return {"status": "calendar_unknown"}
    if st is False:
        return {"status": "not_trading_day"}
    rs = remote_state(ctx.store, d, deep=ctx.deep_verify)
    rec["remote_before"] = rs
    if rs["state"] == "complete":
        return {"status": "already_complete"}
    if rs["state"] == "damaged":
        return {"status": "remote_damaged"}  # 사용자 확인 대상. 자동 수리 없음
    if rs["state"] == "unrecoverable_known":
        return {"status": "unrecoverable_known"}
    if ctx.dry_run:
        return {"status": "would_collect"}
    if ctx.blocked:
        return {"status": "skipped_source_blocked"}
    try:
        fsm.check_target(d, ctx.now)
    except psm.Refused as e:
        return {"status": "not_ready", "reason": str(e)}

    tickers = list(ctx.assets)
    base = ctx.work / "staging"
    attempts: list[dict] = []
    stagings: list[Path] = []

    def run_fetch(tlist: list[str], tag: str) -> dict:
        sdir = base / f"{d.isoformat()}_{ctx.stamp}_{tag}"
        log = fsm.fetch(d, tlist, sdir, get=ctx.get, sleep=ctx.sleep, clock=ctx.clock)
        stagings.append(sdir)
        attempts.append({"tag": tag, "requested": len(tlist), "fetched": len(log["fetched_at"]), "failed": len(log["failed"]),
                         "no_bars": len(log["no_bars"]), "blocked": len(_blocked_errors(log)),
                         "errors": [redact(f["error"], ctx.env)[:120] for f in log["failed"][:5]]})
        return log

    probe = run_fetch(tickers[:PROBE_TICKERS], "probe")
    if not probe["fetched_at"] and not probe["failed"] and len(probe["no_bars"]) == len(tickers[:PROBE_TICKERS]):
        marker = {"status": "unrecoverable", "trade_date": d.isoformat(), "reason": "원천이 그날 봉을 주지 않음(조회 가능 기간 경과 추정)",
                  "detected_at": ctx.now.isoformat(timespec="seconds"), "run_id": ctx.run_id, "remote_before": rs}
        data = json.dumps(marker, ensure_ascii=False, indent=1).encode("utf-8")
        ctx.store.put(f"{PREFIX}/_status/{d.isoformat()}.json", data, hashlib.sha256(data).hexdigest(), "application/json")
        return {"status": "unrecoverable_new", "attempts": attempts}
    remaining = [f["ticker"] for f in probe["failed"]] + [f["ticker"] for f in probe["no_bars"]] + tickers[PROBE_TICKERS:]
    log = probe
    for i in range(len(ctx.retry_waits) + 1):
        if _blocked_errors(log) and sum(a["blocked"] for a in attempts) >= BLOCK_LIMIT:
            ctx.blocked = True
            break
        if not remaining:
            break
        if i > 0:
            ctx.wait(ctx.retry_waits[i - 1])
        log = run_fetch(remaining, "full" if i == 0 else f"retry{i}")
        remaining = [f["ticker"] for f in log["failed"]] + [f["ticker"] for f in log["no_bars"]]
    if sum(a["blocked"] for a in attempts) >= BLOCK_LIMIT:
        ctx.blocked = True

    dest_root = ctx.work / "preserved"
    pres = []
    for s in stagings:
        r = psm.preserve(s, dest_root, [d], expected=tickers, expected_source=str(UNIVERSE_FILE.name), allow_missing=[],
                         dry_run=False, now=ctx.now)
        pres.append({"staging": s.name, "status": r["status"], "copied": r.get("actions", {}).get("copy"),
                     "conflict": r.get("actions", {}).get("conflict")})
    dest = dest_root / d.isoformat()
    v = psm.verify(dest)
    complete_local = v["status"] == "ok" and not v.get("missing_tickers") and v["raw_files"] == len(tickers)
    val = validate_sessions(dest, d, ctx.assets, clock=ctx.clock)
    val_doc = {k: val[k] for k in ("ok", "status", "counts", "flags", "rejected", "rules_version", "schedule_version")}
    extra = {VALIDATE_FILE: json.dumps(val_doc, ensure_ascii=False, indent=1).encode("utf-8")}
    (ctx.work / "reports").mkdir(parents=True, exist_ok=True)
    (ctx.work / "reports" / f"session_validate_{d.isoformat()}.json").write_text(
        json.dumps(val["report"], ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    up = upload_day(ctx.store, dest, d, extra)
    out = {"attempts": attempts, "preserve": pres,
           "local": {"status": v["status"], "raw_files": v["raw_files"], "missing_tickers": v.get("missing_tickers", [])[:50],
                     "bytes": v["bytes"]},
           "session_validation": {k: val_doc[k] for k in ("ok", "status", "counts")},
           "upload": {k: up[k] for k in ("uploaded", "skipped_same", "conflicts", "verify_failed", "errors")}}
    if up["conflicts"]:
        return {**out, "status": "conflict"}
    if up["errors"] or up["verify_failed"]:
        return {**out, "status": "upload_incomplete"}
    if not complete_local:
        return {**out, "status": "incomplete_tickers" if not ctx.blocked else "source_blocked"}
    if not val["ok"]:
        return {**out, "status": "session_validation_failed"}
    seal = json.dumps(_seal_doc(ctx, d, dest, up, val, attempts), ensure_ascii=False, indent=1).encode("utf-8")
    sha = hashlib.sha256(seal).hexdigest()
    ctx.store.put(day_prefix(d) + SEAL, seal, sha, "application/json")
    if hashlib.sha256(ctx.store.get(day_prefix(d) + SEAL)).hexdigest() != sha:
        return {**out, "status": "upload_incomplete", "seal": "verify_failed"}
    return {**out, "status": "completed", "seal_sha256": sha}


# ---------- 실행 ----------

def code_version() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
                              cwd=Path(__file__).resolve().parent).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def env_report(env: dict) -> dict:
    return {k: ("set" if env.get(k) else "missing") for k in REQUIRED_ENV}


def plan_targets(now: datetime, lookback: int, only: date | None, cal=None) -> tuple[str, list[date], dict]:
    """(today_status, 대상일 목록, 정보). today_status: trading | holiday | weekend."""
    today = krx_calendar._kst(now).date()
    st = krx_calendar.day_status(today, cal)
    if st == krx_calendar.UNKNOWN:
        raise ConfigError(f"오늘({today})은 KRX 달력 범위 밖입니다(app/data/krx_calendar.json 갱신 필요)")
    end = latest_complete_day(now, cal)
    window = recent_trading_days(end, PROVIDER_WINDOW_TRADING_DAYS, cal)
    info = {"today": today.isoformat(), "today_status": st, "latest_complete": end.isoformat(),
            "provider_window": [window[0].isoformat(), window[-1].isoformat()]}
    if only is not None:
        if only not in window:
            raise ConfigError(f"{only}는 원천 조회 가능 기간(최근 {PROVIDER_WINDOW_TRADING_DAYS}거래일 {window[0]}~{window[-1]}) 밖입니다")
        return st, [only], info
    if not 1 <= lookback <= PROVIDER_WINDOW_TRADING_DAYS:
        raise ConfigError(f"--lookback은 1~{PROVIDER_WINDOW_TRADING_DAYS}이어야 합니다(원천 조회 가능 기간)")
    return st, window[-lookback:], info


def gh_annotate(level: str, title: str, msg: str, env: dict | None = None) -> None:
    """GitHub Actions 주석(::notice:: 등). 공개 API(check-runs annotations)로도 읽힌다. 비밀값은 넣지 않는다."""
    env = os.environ if env is None else env
    if env.get("GITHUB_ACTIONS") != "true":
        return
    esc = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title={title}::{esc}", flush=True)


def write_summary(rep: dict, env: dict | None = None) -> None:
    env = os.environ if env is None else env
    days = ", ".join(f"{r.get('date', '-')}={r['status']}" for r in rep["days"]) or "-"
    gh_annotate("notice" if rep["status"] in ("ok", "skip_non_trading_day") else "error", "session-minute",
                f"{rep['status']} · today {rep['plan'].get('today_status')} · {days}", env)
    p = env.get("GITHUB_STEP_SUMMARY")
    if not p:
        return
    lines = [f"## 분봉 클라우드 보존 {rep['status']}", "", f"- 실행 {rep['run_id']} · 기준 {rep['now_kst']} · 오늘 {rep['plan'].get('today_status')}",
             "", "| 거래일 | 결과 | 받음 | 업로드 | 같음(건너뜀) | 충돌 | 세션 검증 | 초 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rep["days"]:
        up = r.get("upload", {})
        lines.append(f"| {r.get('date', '-')} | {r['status']} | {(r.get('local') or {}).get('raw_files', '')} | {up.get('uploaded', '')} | "
                     f"{up.get('skipped_same', '')} | {len(up.get('conflicts', []) or [])} | "
                     f"{(r.get('session_validation') or {}).get('ok', '')} | {r.get('seconds', '')} |")
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def probe_source(now: datetime, get=None, n: int = PROBE_TICKERS) -> dict:
    """원천 접근 확인: 최근 완료 거래일 하루를 n종목만 요청한다(저장·업로드 없음). GitHub 러너 IP 차단 여부 확인용."""
    d = latest_complete_day(now)
    assets = load_universe()
    picks = [t for t in ("005930", "000660") if t in assets][: n - 1] + [t for t, a in assets.items() if a == "etf"][:1]
    out = {"date": d.isoformat(), "tickers": {}}
    for t in picks[:n]:
        t0 = time.monotonic()
        try:
            rows = so.fetch_minute(t, d, d, get=get)
            day_n = sum(1 for r in rows if isinstance(r, dict) and str(r.get("localDateTime", ""))[:8] == d.strftime("%Y%m%d"))
            out["tickers"][t] = {"ok": day_n > 0, "rows": len(rows), "day_bars": day_n}
        except Exception as e:  # noqa: BLE001
            m = BLOCK_RE.search(str(e))
            out["tickers"][t] = {"ok": False, "error": str(e)[:160], "blocked": bool(m)}
        out["tickers"][t]["seconds"] = round(time.monotonic() - t0, 2)
    out["ok"] = all(v["ok"] for v in out["tickers"].values())
    out["blocked"] = any(v.get("blocked") for v in out["tickers"].values())
    return out


def main(argv=None, now: datetime | None = None, store=None, get=None, env: dict | None = None,
         sleep: float | None = None, retry_waits: tuple | None = None, wait=None, clock=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work-dir", required=True, type=Path, help="작업 폴더(없거나 비어 있어야 함). 원본 사본·보고서를 둔다")
    ap.add_argument("--store", choices=("r2", "local"), default="r2")
    ap.add_argument("--local-store-dir", type=Path, help="--store local일 때 버킷 대신 쓸 폴더")
    ap.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK, help=f"확인할 최근 거래일 수(1~{PROVIDER_WINDOW_TRADING_DAYS})")
    ap.add_argument("--date", type=date.fromisoformat, help="이 거래일만(원천 조회 가능 기간 안)")
    ap.add_argument("--dry-run", action="store_true", help="원천 요청·업로드 없이 R2 상태와 대상일만 확인")
    ap.add_argument("--deep-verify", action="store_true", help="봉인된 날짜도 객체를 내려받아 SHA-256을 다시 계산")
    ap.add_argument("--time-budget-min", type=float, default=35.0, help="이 시간을 넘기면 남은 과거 날짜는 다음 실행으로 미룬다(최신일은 처리)")
    ap.add_argument("--sleep", type=float, default=0.35, help="원천 요청 간격(초)")
    ap.add_argument("--report", type=Path, help="실행 결과 JSON(기본 <work-dir>/run_report.json)")
    ap.add_argument("--probe-source", action="store_true", help="원천 접근만 확인(3종목, 저장소 없음, 비밀값 불필요)")
    args = ap.parse_args(argv)
    env = dict(os.environ) if env is None else env
    now = now or datetime.now(KST)
    if args.probe_source:
        try:
            res = probe_source(now, get=get)
        except ConfigError as e:
            print(f"설정 오류: {e}", file=sys.stderr)
            return 2
        print(json.dumps(res, ensure_ascii=False, indent=1))
        per = ", ".join(f"{t} day_bars={v.get('day_bars', '-')} ok={v['ok']}" + (" blocked" if v.get("blocked") else "")
                        for t, v in res["tickers"].items())
        gh_annotate("notice" if res["ok"] else "error", "source-probe", f"date={res['date']} ok={res['ok']} · {per}", env)
        return 0 if res["ok"] else 1
    clock = clock or (lambda: datetime.now(KST))
    run_id = f"{now.astimezone(KST).strftime('%Y%m%dT%H%M%S')}_{env.get('GITHUB_RUN_ID', 'local')}"
    rep: dict = {"tool": TOOL, "version": VERSION, "run_id": run_id, "now_kst": now.astimezone(KST).isoformat(timespec="seconds"),
                 "now_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"), "code_version": code_version(),
                 "python": platform.python_version(), "platform": platform.platform(), "store": args.store,
                 "env": env_report(env), "dry_run": args.dry_run, "plan": {}, "days": []}
    try:
        work = args.work_dir.resolve()
        if work.exists() and any(work.iterdir()):
            raise ConfigError(f"--work-dir가 비어 있지 않습니다: {work}")
        if sys.version_info < (3, 11):
            raise ConfigError("Python 3.11 이상이 필요합니다")
        assets = load_universe()
        st, targets, info = plan_targets(now, args.lookback, args.date)
        rep["plan"] = {**info, "targets": [d.isoformat() for d in targets], "universe": len(assets)}
        if store is None:
            if args.store == "r2":
                missing = [k for k, v in env_report(env).items() if v == "missing"]
                if missing:
                    raise ConfigError(f"R2 환경 변수가 없습니다: {', '.join(missing)}(GitHub Secrets 설정, 문서 7절)")
                store = R2Store(env)
            else:
                if not args.local_store_dir:
                    raise ConfigError("--store local에는 --local-store-dir가 필요합니다")
                store = LocalStore(args.local_store_dir)
        store = RetryingStore(store, sleep=wait or time.sleep) if not isinstance(store, RetryingStore) else store
    except (ConfigError, psm.Refused, OSError, ValueError) as e:
        print(f"설정 오류: {redact(e, env)}", file=sys.stderr)
        rep["status"] = "config_error"
        rep["error"] = redact(e, env)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        gh_annotate("error", "session-minute", f"config_error: {rep['error']}", env)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(store=store, work=work, now=now, assets=assets, get=get, sleep=args.sleep if sleep is None else sleep,
              retry_waits=DEFAULT_RETRY_WAITS if retry_waits is None else retry_waits, wait=wait or time.sleep, clock=clock,
              code_version=rep["code_version"], run_id=run_id, deep_verify=args.deep_verify, dry_run=args.dry_run,
              stamp=now.astimezone(KST).strftime("%Y%m%dT%H%M"), env=env)
    print(f"[{TOOL}] 실행 {run_id} · 오늘 {info['today']}({st}) · 대상 {rep['plan']['targets']} · 종목 {len(assets)} · 저장소 {args.store}")
    t0 = time.monotonic()
    if st != krx_calendar.TRADING and args.date is None:
        rep["status"] = "skip_non_trading_day"
        print(f"오늘은 KRX 거래일이 아닙니다({st}). 수집하지 않고 끝냅니다(SKIP)")
    else:
        latest = targets[-1]
        try:
            for d in targets:
                if d != latest and time.monotonic() - t0 > args.time_budget_min * 60:
                    rep["days"].append({"date": d.isoformat(), "status": "deferred_time_budget"})
                    continue
                r = process_day(ctx, d)
                rep["days"].append(r)
                print(f"  {d}: {r['status']} ({r.get('seconds')}초)")
        except StoreAuthError as e:
            rep["days"].append({"status": "store_auth_error", "error": redact(e, env)})
        bad = [r for r in rep["days"] if r["status"] not in OK_DAY | {"would_collect"}]
        rep["status"] = "ok" if not bad else "failed"
        rep["source_blocked"] = ctx.blocked
    rep["store_retries"] = getattr(store, "retries", 0)
    rep["seconds"] = round(time.monotonic() - t0, 1)
    text = json.dumps(rep, ensure_ascii=False, indent=1, default=str)
    (args.report or work / "run_report.json").write_text(text, encoding="utf-8")
    if not args.dry_run:
        try:
            data = text.encode("utf-8")
            store.put(f"{PREFIX}/_runs/{run_id}.json", data, hashlib.sha256(data).hexdigest(), "application/json")
        except Exception as e:  # noqa: BLE001 - 기록 업로드 실패는 결과에 남기고 실패로 끝낸다
            print(f"실행 기록 업로드 실패: {redact(e, env)[:200]}", file=sys.stderr)
            rep["status"] = "failed"
    write_summary(rep, env)
    print(f"결과: {rep['status']} · " + ", ".join(f"{r.get('date', '-')}={r['status']}" for r in rep["days"]))
    return 0 if rep["status"] in ("ok", "skip_non_trading_day") else 1


if __name__ == "__main__":
    sys.exit(main())
