"""분봉 원본(raw/{ticker}.json)을 거래일별 보존 디렉터리로 복사하고 SHA-256으로 검증한다(2026-11-14, 52차).

DB·네트워크를 쓰지 않는다. 원본은 읽기만 한다(이동·삭제·수정 없음). 보존본에는 새 파일만 만든다(덮어쓰지 않음).
운영 절차: docs/SESSION_MINUTE_DATA_PRESERVATION_52.md

  # 보존(+검증). 보존 위치 = <dest-root>/<날짜> 또는 <dest-root>/<시작>_<끝>
  python -m scripts.preserve_session_minute --source <원본 폴더> --dest-root backups/session_minute \\
      --date 2026-11-16 --expected-from backups/session_minute/2026-11-13
  python -m scripts.preserve_session_minute ... --dry-run                 # 복사 없이 계획만
  python -m scripts.preserve_session_minute --verify-only --dest backups/session_minute/2026-11-16
  python -m scripts.preserve_session_minute --verify-only --dest <51차 보존본> --manifest <커밋한 .sha256>

원본 폴더 형식(50·51차와 같음): <원본>/raw/{ticker}.json = 네이버 분봉 응답 목록(종목 하나, 날짜 여러 개 가능),
<원본> 바로 아래 파일(fetch_log.json·session_audit_summary.json·README.md 등)은 출처 기록으로 source_meta/<원본 id>/에 같이 복사한다.

보존 디렉터리 형식:
  raw/{ticker}.json          원본과 바이트까지 같은 사본(수정 시각 보존)
  source_meta/<원본 id>/*    원본 바로 아래 파일의 사본. 원본 id = <원본 폴더 이름>__<경로 SHA-256 앞 8자리>
                             (실패 종목만 다른 스테이징에서 다시 받아 보태도 출처 기록끼리 충돌하지 않는다)
  MANIFEST.sha256            `sha256sum -c` 입력(LF). 위 두 종류 파일 전부
  preserve_manifest.json     파일별 크기·SHA-256·원본/사본 수정 시각·날짜별 봉 수, 대상 날짜, 예상 종목
  preserve_log.jsonl         실행마다 한 줄(모드·결과)
  <dest-root>/PRESERVATION_LOG.tsv  모든 날짜의 실행 기록(한 줄씩 추가)

안전 규칙:
- 이미 있는 파일과 SHA-256이 같으면 "이미 보존됨"으로 둔다. 다르면 충돌이다. 충돌이 하나라도 있으면 아무것도 복사하지 않고 실패한다.
- 복사는 임시 파일(.{이름}.partial)에 쓰고 SHA-256이 원본과 같을 때만 최종 이름으로 바꾼다(기존 파일이 있으면 바꾸지 않음).
- 이 도구가 만들지 않은 디렉터리(예: 51차 보존본)에는 쓰지 않는다. 원본과 보존 위치가 겹치면 거부한다.
- 예상 종목 파일이 없거나 그날 봉이 없으면 빈 파일을 만들지 않고 실패로 기록한다.

종료 코드: 0 = ok·warning, 1 = failed(누락·충돌·비정상·검증 실패), 2 = 거부(잘못된 인자·보호 규칙).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from app import krx_calendar

TOOL = "preserve_session_minute"
VERSION = 1
RAW, META = "raw", "source_meta"
MANIFEST_SHA, MANIFEST_JSON, RUN_LOG, ROOT_LOG = "MANIFEST.sha256", "preserve_manifest.json", "preserve_log.jsonl", "PRESERVATION_LOG.tsv"
TOOL_FILES = {MANIFEST_SHA, MANIFEST_JSON, RUN_LOG}
PARTIAL_SUFFIX = ".partial"
CHUNK = 1 << 20
TICKER_RE = re.compile(r"^[0-9A-Z]{6}$")
RECORD_KEYS = ("localDateTime", "openPrice", "highPrice", "lowPrice", "currentPrice", "accumulatedTradingVolume")
DAY_COMPLETE_AT = (20, 10)  # app.session_ohlcv.DAY_COMPLETE_AT과 같다(애프터마켓 ~20:00 뒤). 그 전 수집이면 그날 분봉은 미완성
KST = krx_calendar.KST
ROOT_LOG_FIELDS = ("run_at", "mode", "label", "target_dates", "status", "expected", "found", "copied", "already_present",
                   "conflicts", "missing", "abnormal", "bytes", "source")


class Refused(Exception):
    """보호 규칙·인자 오류. 아무것도 쓰지 않고 종료 코드 2."""


class CopyError(Exception):
    pass


# ---------- 파일 검사 ----------

def sha256_file(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _iso_minute(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def inspect_raw(data: bytes, targets: list[date]) -> dict:
    """분봉 파일 내용 검사(읽기만). 날짜별 봉 수, 대상 날짜 봉 수, 형식 오류."""
    out: dict = {"error": None, "bars": 0, "dates": {}, "target_bars": {d.isoformat(): 0 for d in targets}, "duplicate_bars": 0}
    try:
        rows = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        out["error"] = f"json_error: {str(e)[:80]}"
        return out
    if not isinstance(rows, list):
        out["error"] = "not_a_list"
        return out
    seen: set[str] = set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or any(k not in r for k in RECORD_KEYS):
            out["error"] = f"bad_record #{i}: 필드 누락"
            return out
        ts = str(r["localDateTime"])
        if not re.fullmatch(r"\d{14}", ts) or any(not isinstance(r[k], (int, float)) or isinstance(r[k], bool) for k in RECORD_KEYS[1:]):
            out["error"] = f"bad_record #{i}: 시각·숫자 형식"
            return out
        if ts in seen:
            out["duplicate_bars"] += 1
        seen.add(ts)
        d = _iso_minute(ts)
        out["dates"][d] = out["dates"].get(d, 0) + 1
    out["bars"] = len(rows)
    out["dates"] = dict(sorted(out["dates"].items()))
    for d in out["target_bars"]:
        out["target_bars"][d] = out["dates"].get(d, 0)
    if not rows:
        out["error"] = "empty"
    elif missing := [d for d, n in out["target_bars"].items() if n == 0]:
        out["error"] = f"no_target_bars: {','.join(missing)}"
    return out


# ---------- 예상 종목 ----------

def tickers_in(folder: Path) -> list[str]:
    """폴더의 raw/*.json(없으면 폴더 바로 아래 *.json) 이름. 읽기만 한다."""
    base = folder / RAW if (folder / RAW).is_dir() else folder
    if not base.is_dir():
        raise Refused(f"종목 목록 폴더가 없습니다: {folder}")
    return sorted(p.stem for p in base.iterdir() if p.is_file() and p.suffix == ".json" and not p.name.startswith("."))


def read_ticker_file(path: Path) -> list[str]:
    if not path.is_file():
        raise Refused(f"종목 목록 파일이 없습니다: {path}")
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        t = line.split("#", 1)[0].strip()
        if t:
            out.append(t)
    return out


def case_duplicates(names: list[str]) -> list[list[str]]:
    """대소문자만 다르거나 같은 이름이 두 번 이상(윈도우 파일 시스템에서는 같은 파일이 된다)."""
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(n.upper(), []).append(n)
    return [sorted(v) for v in groups.values() if len(v) > 1]


# ---------- 수집 시각(그날 분봉이 끝난 뒤 받았는가) ----------

def _parse_ts(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=KST)


def collection_times(source: Path) -> tuple[dict[str, datetime], datetime | None, str]:
    """(종목별 수집 시각, 전체 수집 시각, 근거). fetch_log.json(51·52차) > session_audit_summary.json(50차) > 없음."""
    fl = source / "fetch_log.json"
    if fl.is_file():
        try:
            d = json.loads(fl.read_text(encoding="utf-8"))
            per = {t: v for t, s in (d.get("fetched_at") or {}).items() if (v := _parse_ts(s))}
            return per, _parse_ts(d.get("started_at")), "fetch_log.json"
        except (ValueError, AttributeError):
            pass
    sm = source / "session_audit_summary.json"
    if sm.is_file():
        try:
            return {}, _parse_ts(json.loads(sm.read_text(encoding="utf-8")).get("generated_at")), "session_audit_summary.json"
        except (ValueError, AttributeError):
            pass
    return {}, None, "file_mtime"


def complete_after(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, *DAY_COMPLETE_AT, tzinfo=KST)


# ---------- 계획 ----------

@dataclass
class Item:
    rel: str  # 보존 디렉터리 기준 경로(raw/000080.json, source_meta/<원본 id>/fetch_log.json)
    src: Path
    kind: str  # raw | meta
    ticker: str | None = None
    sha256: str = ""
    bytes: int = 0
    src_mtime: float = 0.0
    inspect: dict | None = None
    action: str = ""  # copy | already_present | conflict | skip_abnormal
    dest_sha256: str | None = None
    error: str | None = None


@dataclass
class Plan:
    source: Path
    dest: Path
    targets: list[date]
    items: list[Item] = field(default_factory=list)
    expected: list[str] | None = None
    expected_source: str = ""
    allow_missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # 실패 사유(사람이 읽는 문장)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    allowed_missing: list[str] = field(default_factory=list)
    from_previous: list[str] = field(default_factory=list)  # 이번 원본에는 없지만 앞선 실행이 이미 보존한 예상 종목
    unexpected: list[str] = field(default_factory=list)
    duplicates: list[list[str]] = field(default_factory=list)
    abnormal: list[dict] = field(default_factory=list)
    per_date_files: dict = field(default_factory=dict)
    extra_dates: dict = field(default_factory=dict)
    collected_basis: str = ""


def source_id(source: Path) -> str:
    """출처 기록 하위 폴더 이름. 같은 원본 폴더면 늘 같다(재실행이 같은 결과)."""
    r = source.resolve()
    return f"{r.name}__{hashlib.sha256(str(r).lower().encode('utf-8')).hexdigest()[:8]}"


def label_for(targets: list[date]) -> str:
    return targets[0].isoformat() if len(targets) == 1 else f"{targets[0].isoformat()}_{targets[-1].isoformat()}"


def _inside(a: Path, b: Path) -> bool:
    """a가 b와 같거나 b 안에 있는가."""
    try:
        a.relative_to(b)
        return True
    except ValueError:
        return False


def check_dest_root(root: Path) -> None:
    """dest-root에는 이 도구가 만든 날짜 디렉터리와 PRESERVATION_LOG.tsv만 있어야 한다(51차 보존본·DB 덤프 폴더 보호)."""
    if not root.exists():
        return
    if not root.is_dir():
        raise Refused(f"--dest-root가 디렉터리가 아닙니다: {root}")
    for p in root.iterdir():
        if p.name == ROOT_LOG:
            continue
        if p.is_dir() and ((p / MANIFEST_JSON).is_file() or not any(p.iterdir())):
            continue
        raise Refused(f"--dest-root에 이 도구가 만들지 않은 항목이 있습니다: {p.name}. 보존 전용 폴더(예: backups/session_minute)를 쓰세요")


def check_dest(dest: Path, targets: list[date]) -> dict | None:
    """기존 보존 디렉터리면 그 manifest. 이 도구가 만들지 않은 비어 있지 않은 디렉터리는 거부."""
    if not dest.exists():
        return None
    mf = dest / MANIFEST_JSON
    if not mf.is_file():
        if any(dest.iterdir()):
            raise Refused(f"이 도구가 만든 보존 디렉터리가 아닙니다(쓰지 않음): {dest}")
        return None
    m = json.loads(mf.read_text(encoding="utf-8"))
    if m.get("target_dates") != [d.isoformat() for d in targets]:
        raise Refused(f"보존 디렉터리의 대상 날짜 {m.get('target_dates')}가 이번 날짜와 다릅니다: {dest}")
    return m


def build_plan(source: Path, dest_root: Path, targets: list[date], expected: list[str] | None, expected_source: str,
               allow_missing: list[str]) -> Plan:
    source, dest_root = source.resolve(), dest_root.resolve()
    if not (source / RAW).is_dir():
        raise Refused(f"원본 폴더에 raw/가 없습니다: {source}")
    dest = dest_root / label_for(targets)
    if _inside(dest, source) or _inside(source, dest_root):
        raise Refused(f"원본({source})과 보존 위치({dest_root})가 겹칩니다")
    check_dest_root(dest_root)
    previous = check_dest(dest, targets)
    plan = Plan(source=source, dest=dest, targets=targets, expected=expected, expected_source=expected_source,
                allow_missing=sorted(set(allow_missing)))

    for d in targets:
        st = krx_calendar.is_trading_day(d)
        if st is False:
            raise Refused(f"{d}는 KRX 거래일이 아닙니다")
        if st is None:
            plan.warnings.append(f"{d}: KRX 달력 범위 밖이라 거래일인지 확인하지 못했습니다")

    # 원본 raw/ 목록
    names: list[str] = []
    for p in sorted((source / RAW).iterdir()):
        if p.is_dir():
            plan.abnormal.append({"file": f"{RAW}/{p.name}", "error": "raw/ 안의 하위 폴더(복사하지 않음)"})
        elif p.name.endswith(PARTIAL_SUFFIX) or p.name.startswith("."):
            plan.abnormal.append({"file": f"{RAW}/{p.name}", "error": "임시·숨김 파일(복사하지 않음)"})
        elif p.suffix != ".json":
            plan.abnormal.append({"file": f"{RAW}/{p.name}", "error": "json이 아닌 파일(복사하지 않음)"})
        elif not TICKER_RE.fullmatch(p.stem):
            plan.abnormal.append({"file": f"{RAW}/{p.name}", "error": "종목코드 형식이 아닌 파일 이름(복사하지 않음)"})
        else:
            names.append(p.stem)
            plan.items.append(Item(rel=f"{RAW}/{p.name}", src=p, kind="raw", ticker=p.stem))
    for p in sorted(source.iterdir()):
        if p.is_file() and not p.name.endswith(PARTIAL_SUFFIX):
            plan.items.append(Item(rel=f"{META}/{source_id(source)}/{p.name}", src=p, kind="meta"))

    plan.duplicates = case_duplicates(names)
    if expected is not None:
        if dup := case_duplicates(expected):
            plan.duplicates += [["expected"] + g for g in dup]
        exp = {t.upper() for t in expected}
        found = {t.upper() for t in names}
        allowed = {t.upper() for t in plan.allow_missing}
        # 앞선 실행이 이미 보존한 종목은 누락이 아니다(실패 종목만 다시 받아 보태는 재실행)
        kept = {Path(r).stem.upper() for r in (previous or {}).get("files", {})
                if r.startswith(f"{RAW}/") and (plan.dest / r).is_file()}
        plan.from_previous = sorted((exp & kept) - found)
        miss = sorted(exp - found - kept)
        plan.missing = [t for t in miss if t not in allowed]
        plan.allowed_missing = [t for t in miss if t in allowed]
        plan.unexpected = sorted(found - exp)
    else:
        plan.warnings.append("예상 종목 기준이 없어 누락을 판정하지 않았습니다(--no-expected)")

    # 파일 검사·해시·수집 시각
    per_ticker_at, overall_at, basis = collection_times(source)
    plan.collected_basis = basis
    if basis == "file_mtime":
        plan.warnings.append("수집 기록(fetch_log.json 등)이 없어 파일 수정 시각으로 그날 분봉 완료 여부를 판정했습니다")
    target_iso = {d.isoformat() for d in targets}
    for it in plan.items:
        data = it.src.read_bytes()
        it.sha256, it.bytes, it.src_mtime = hashlib.sha256(data).hexdigest(), len(data), it.src.stat().st_mtime
        if it.kind != "raw":
            continue
        it.inspect = inspect_raw(data, targets)
        err = it.inspect["error"]
        at = per_ticker_at.get(it.ticker) or overall_at or datetime.fromtimestamp(it.src_mtime, tz=KST)
        late = [d.isoformat() for d in targets if at < complete_after(d)]
        if not err and late:
            err = f"incomplete_day: {','.join(late)} 분봉 완료(20:10 KST) 전 수집({at.isoformat(timespec='minutes')})"
        if err:
            it.error = err
            plan.abnormal.append({"file": it.rel, "error": err})
        if it.inspect["duplicate_bars"]:
            plan.warnings.append(f"{it.rel}: 같은 시각 봉 {it.inspect['duplicate_bars']}개(원본 그대로 복사)")
        for d, n in it.inspect["dates"].items():
            if d in target_iso:
                plan.per_date_files[d] = plan.per_date_files.get(d, 0) + (1 if n else 0)
            else:
                plan.extra_dates[d] = plan.extra_dates.get(d, 0) + 1
    for d in sorted(target_iso):
        plan.per_date_files.setdefault(d, 0)
    if plan.extra_dates:
        plan.warnings.append(f"원본 파일에 대상 밖 날짜도 들어 있습니다(바이트 그대로 보존): {sorted(plan.extra_dates)}")

    # 보존 디렉터리와 대조
    for it in plan.items:
        if it.error:
            it.action = "skip_abnormal"
            continue
        dst = plan.dest / it.rel
        if dst.exists():
            it.dest_sha256 = sha256_file(dst)[0]
            it.action = "already_present" if it.dest_sha256 == it.sha256 else "conflict"
        else:
            it.action = "copy"
    if previous:  # 이전 실행이 기록한 파일이 사라졌는가(보존본 훼손)
        for rel in previous.get("files", {}):
            if not (plan.dest / rel).exists():
                plan.errors.append(f"이전에 보존한 파일이 없어졌습니다: {rel}")

    conflicts = [it.rel for it in plan.items if it.action == "conflict"]
    if conflicts:
        plan.errors.append(f"기존 보존본과 SHA-256이 다른 파일 {len(conflicts)}개(덮어쓰지 않음, 이번 실행은 복사하지 않음): {conflicts[:10]}")
    if plan.missing:
        plan.errors.append(f"예상 종목 중 원본 파일이 없는 종목 {len(plan.missing)}개: {plan.missing[:20]}")
    if plan.allowed_missing:
        plan.warnings.append(f"허용한 누락 종목: {plan.allowed_missing}")
    if plan.unexpected:
        plan.warnings.append(f"예상 목록에 없는 종목 {len(plan.unexpected)}개(복사함): {plan.unexpected[:20]}")
    if plan.duplicates:
        plan.errors.append(f"중복 종목: {plan.duplicates}")
    if plan.abnormal:
        plan.errors.append(f"비정상 파일 {len(plan.abnormal)}개(복사하지 않음): {[a['file'] for a in plan.abnormal][:10]}")
    return plan


# ---------- 복사 ----------

def _write_chunk(fo, chunk: bytes) -> None:  # 테스트가 복사 도중 실패를 흉내 내려고 바꾼다
    fo.write(chunk)


def _finalize(tmp: Path, dst: Path) -> None:
    """임시 파일을 최종 이름으로. 최종 파일이 이미 있으면 바꾸지 않는다(윈도우 rename·POSIX link 모두 기존 파일이면 실패)."""
    if os.name == "nt":
        os.rename(tmp, dst)
    else:
        os.link(tmp, dst)
        tmp.unlink()


def copy_verified(src: Path, dst: Path, expected_sha: str, expected_size: int) -> None:
    """src -> dst. 임시 파일 SHA-256이 원본(계획 때 계산)과 같을 때만 확정한다. 실패하면 임시 파일을 지우고 dst는 만들지 않는다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name("." + dst.name + PARTIAL_SUFFIX)
    if tmp.exists():
        tmp.unlink()  # 이 도구가 남긴 이전 임시 파일(최종 파일이 아님)
    try:
        with open(src, "rb") as fi, open(tmp, "xb") as fo:
            for chunk in iter(lambda: fi.read(CHUNK), b""):
                _write_chunk(fo, chunk)
            fo.flush()
            os.fsync(fo.fileno())
        shutil.copystat(src, tmp)
        got, size = sha256_file(tmp)
        if got != expected_sha or size != expected_size:
            raise CopyError(f"사본 SHA-256/크기가 원본과 다릅니다({size} vs {expected_size}바이트)")
        if dst.exists():
            raise CopyError("최종 파일이 이미 있습니다(덮어쓰지 않음)")
        _finalize(tmp, dst)
        if sha256_file(dst)[0] != expected_sha:  # 확정 뒤 다시 읽어 확인
            raise CopyError("확정한 파일의 SHA-256이 원본과 다릅니다")
    finally:
        if tmp.exists():
            tmp.unlink()


# ---------- 기록 ----------

def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name("." + path.name + PARTIAL_SUFFIX)
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_manifests(plan: Plan, previous: dict | None, run: dict) -> dict:
    files = dict((previous or {}).get("files", {}))
    for it in plan.items:
        dst = plan.dest / it.rel
        if it.action in ("copy", "already_present") and dst.exists() and it.rel not in files:
            files[it.rel] = {"sha256": it.sha256, "bytes": it.bytes, "kind": it.kind, "ticker": it.ticker,
                             "source_rel": it.src.relative_to(plan.source).as_posix(),
                             "source_mtime": datetime.fromtimestamp(it.src_mtime, tz=KST).isoformat(),
                             "copy_mtime": datetime.fromtimestamp(dst.stat().st_mtime, tz=KST).isoformat(),
                             "dates": (it.inspect or {}).get("dates"), "target_bars": (it.inspect or {}).get("target_bars")}
    files = dict(sorted(files.items()))
    sha_text = "".join(f"{f['sha256']}  {rel}\n" for rel, f in files.items())
    shp = plan.dest / MANIFEST_SHA
    if not shp.exists() or shp.read_text(encoding="utf-8") != sha_text:
        _atomic_write(shp, sha_text)
    manifest = {
        "tool": TOOL, "version": VERSION, "label": plan.dest.name, "target_dates": [d.isoformat() for d in plan.targets],
        "created_at": (previous or {}).get("created_at") or run["run_at"],
        "expected": {"source": plan.expected_source, "count": None if plan.expected is None else len(plan.expected),
                     "tickers": plan.expected, "allow_missing": plan.allow_missing},
        "last_run": run, "files": files,
    }
    _atomic_write(plan.dest / MANIFEST_JSON, json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return manifest


def append_logs(dest_root: Path, dest: Path | None, run: dict) -> None:
    if dest is not None and dest.exists():
        with open(dest / RUN_LOG, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(run, ensure_ascii=False) + "\n")
    dest_root.mkdir(parents=True, exist_ok=True)
    log = dest_root / ROOT_LOG
    new = not log.exists()
    with open(log, "a", encoding="utf-8", newline="\n") as fh:
        if new:
            fh.write("\t".join(ROOT_LOG_FIELDS) + "\n")
        fh.write("\t".join(str(run.get(k, "")) for k in ROOT_LOG_FIELDS) + "\n")


# ---------- 실행 ----------

def summarize(plan: Plan, copied: int = 0, copy_errors: list | None = None) -> dict:
    raws = [it for it in plan.items if it.kind == "raw"]
    by = lambda a: sum(1 for it in plan.items if it.action == a)  # noqa: E731
    status = "failed" if plan.errors or copy_errors else ("warning" if plan.warnings else "ok")
    return {
        "status": status, "label": plan.dest.name, "dest": str(plan.dest), "source": str(plan.source),
        "target_dates": [d.isoformat() for d in plan.targets],
        "expected": None if plan.expected is None else len(plan.expected), "found": len(raws),
        "missing": plan.missing, "allowed_missing": plan.allowed_missing, "from_previous": plan.from_previous,
        "unexpected": plan.unexpected,
        "duplicates": plan.duplicates, "abnormal": plan.abnormal, "per_date_files": plan.per_date_files,
        "extra_dates": plan.extra_dates, "collected_basis": plan.collected_basis,
        "raw_bytes": sum(it.bytes for it in raws), "meta_files": len(plan.items) - len(raws),
        "actions": {a: by(a) for a in ("copy", "already_present", "conflict", "skip_abnormal")},
        "copied": copied, "copy_errors": copy_errors or [], "errors": plan.errors, "warnings": plan.warnings,
    }


def preserve(source: Path, dest_root: Path, targets: list[date], expected: list[str] | None, expected_source: str,
             allow_missing: list[str], dry_run: bool, now: datetime | None = None) -> dict:
    plan = build_plan(source, dest_root, targets, expected, expected_source, allow_missing)
    if dry_run:
        out = summarize(plan)
        out["mode"] = "dry-run"
        return out
    dest_root = dest_root.resolve()
    previous = check_dest(plan.dest, targets)
    run_at = (now or datetime.now(KST)).isoformat(timespec="seconds")
    copy_errors: list[dict] = []
    copied = 0
    conflict = any(it.action == "conflict" for it in plan.items)
    if not conflict and previous is None and any(it.action == "copy" for it in plan.items):
        # 첫 복사 전에 표시 manifest를 쓴다: 도중에 멈춰도 다음 실행이 "이 도구가 만든 디렉터리"로 알아보고 이어 간다
        plan.dest.mkdir(parents=True, exist_ok=True)
        previous = write_manifests(Plan(source=plan.source, dest=plan.dest, targets=targets, expected=plan.expected,
                                        expected_source=plan.expected_source, allow_missing=plan.allow_missing),
                                   None, {"run_at": run_at, "status": "in_progress"})
    if not conflict:  # 충돌이 있으면 아무것도 복사하지 않는다
        for it in plan.items:
            if it.action != "copy":
                continue
            try:
                copy_verified(it.src, plan.dest / it.rel, it.sha256, it.bytes)
                copied += 1
            except (OSError, CopyError) as e:
                it.action = "copy_failed"
                copy_errors.append({"file": it.rel, "error": str(e)[:200]})
    out = summarize(plan, copied, copy_errors)
    run = {"run_at": run_at, "mode": "preserve", "label": plan.dest.name, "target_dates": ",".join(out["target_dates"]),
           "status": out["status"], "expected": out["expected"], "found": out["found"], "copied": copied,
           "already_present": out["actions"]["already_present"], "conflicts": out["actions"]["conflict"],
           "missing": len(plan.missing), "abnormal": len(plan.abnormal), "bytes": out["raw_bytes"], "source": str(plan.source)}
    if plan.dest.exists() and not conflict:  # 충돌 실행은 manifest를 건드리지 않는다(기록만 남김)
        write_manifests(plan, previous, run)
    if (plan.dest / MANIFEST_SHA).is_file():
        out["verify"] = verify(plan.dest)  # 확정한 결과를 처음부터 다시 검증
        if out["verify"]["status"] == "failed":
            out["status"] = run["status"] = "failed"
    append_logs(dest_root, plan.dest, run)
    out["mode"] = "preserve"
    return out


def read_sha_manifest(path: Path) -> dict[str, str]:
    out = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if not m:
            raise Refused(f"{path}:{n}: sha256sum 형식이 아닙니다")
        out[m.group(2)] = m.group(1)
    return out


def verify(dest: Path, manifest: Path | None = None, source: Path | None = None) -> dict:
    """읽기만 한다. 목록(MANIFEST.sha256 또는 --manifest)의 모든 파일을 다시 해시해 대조한다."""
    dest = dest.resolve()
    if not dest.is_dir():
        raise Refused(f"보존 디렉터리가 없습니다: {dest}")
    mpath = manifest or dest / MANIFEST_SHA
    if not mpath.is_file():
        raise Refused(f"체크섬 목록이 없습니다: {mpath}")
    listed = read_sha_manifest(mpath)
    ok, mismatch, missing = 0, [], []
    for rel, sha in listed.items():
        p = dest / rel
        if not p.is_file():
            missing.append(rel)
        elif sha256_file(p)[0] != sha:
            mismatch.append(rel)
        else:
            ok += 1
    allowed = TOOL_FILES | {"README.md"}
    present = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    unlisted = sorted(r for r in present - set(listed) if r not in allowed and not r.endswith(PARTIAL_SUFFIX))
    partials = sorted(r for r in present if r.endswith(PARTIAL_SUFFIX))
    out = {"mode": "verify-only", "dest": str(dest), "manifest": str(mpath), "listed": len(listed), "ok": ok,
           "mismatch": mismatch, "missing": missing, "unlisted": unlisted, "partials": partials,
           "raw_files": sum(1 for r in listed if r.startswith(f"{RAW}/")),
           "bytes": sum((dest / r).stat().st_size for r in listed if (dest / r).is_file())}
    pm = dest / MANIFEST_JSON
    if pm.is_file():
        m = json.loads(pm.read_text(encoding="utf-8"))
        out["target_dates"] = m.get("target_dates")
        out["last_run_status"] = (m.get("last_run") or {}).get("status")
        size_bad = [r for r, f in m.get("files", {}).items() if (dest / r).is_file() and (dest / r).stat().st_size != f.get("bytes")]
        not_in_sha = sorted(set(m.get("files", {})) - set(listed))
        out["size_mismatch"], out["manifest_json_not_in_sha"] = size_bad, not_in_sha
        exp = (m.get("expected") or {}).get("tickers")
        if exp is not None:
            have = {Path(r).stem.upper() for r in listed if r.startswith(f"{RAW}/") and (dest / r).is_file()}
            allowed_missing = {t.upper() for t in (m.get("expected") or {}).get("allow_missing") or []}
            out["expected"] = len(exp)
            out["missing_tickers"] = sorted({t.upper() for t in exp} - have - allowed_missing)
    if source is not None:
        src_diff, src_missing = [], []
        for rel, sha in listed.items():
            if rel.startswith(f"{META}/"):
                if not rel.startswith(f"{META}/{source_id(source)}/"):
                    continue  # 다른 원본(재실행 스테이징)의 출처 기록
                sp = source / rel.split("/", 2)[2]
            else:
                sp = source / rel
            if not sp.is_file():
                src_missing.append(rel)
            elif sha256_file(sp)[0] != sha:
                src_diff.append(rel)
        out["source"], out["source_missing"], out["source_differs"] = str(source), src_missing, src_diff
    bad = mismatch or missing or out.get("size_mismatch") or out.get("manifest_json_not_in_sha") or out.get("missing_tickers") \
        or out.get("source_differs")
    out["status"] = "failed" if bad else ("warning" if unlisted or partials or out.get("source_missing") else "ok")
    return out


def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"YYYY-MM-DD 형식이 아닙니다: {s}")


def targets_from(args) -> list[date]:
    if args.date and (args.start or args.end):
        raise Refused("--date와 --start/--end는 함께 쓸 수 없습니다")
    if args.date:
        return [args.date]
    if not (args.start and args.end):
        raise Refused("--date 또는 --start와 --end가 필요합니다")
    if args.end < args.start:
        raise Refused("--end가 --start보다 앞입니다")
    days = krx_calendar.trading_days(args.start, args.end)
    if not days:
        raise Refused(f"{args.start}~{args.end}에 KRX 거래일이 없습니다")
    if len(days) > 31:
        raise Refused("한 번에 31거래일까지만 처리합니다")
    return days


def print_result(out: dict) -> None:
    if out["mode"] == "verify-only":
        print(f"[검증] {out['dest']}  목록 {out['manifest']}")
        print(f"  파일 {out['listed']}개 중 일치 {out['ok']}, 불일치 {len(out['mismatch'])}, 없음 {len(out['missing'])}, "
              f"목록 밖 {len(out['unlisted'])}, 임시 파일 {len(out['partials'])}, 분봉 파일 {out['raw_files']}개, 목록 파일 합계 {out['bytes']:,}바이트")
        for k in ("mismatch", "missing", "unlisted", "partials", "size_mismatch", "manifest_json_not_in_sha", "missing_tickers",
                  "source_missing", "source_differs"):
            if out.get(k):
                print(f"  {k}: {out[k][:20]}")
        print(f"결과: {out['status']}")
        return
    a = out["actions"]
    print(f"[{'계획(dry-run)' if out['mode'] == 'dry-run' else '보존'}] {out['label']}  원본 {out['source']} -> {out['dest']}")
    print(f"  대상 날짜 {out['target_dates']}, 날짜별 파일 {out['per_date_files']}")
    print(f"  예상 종목 {out['expected']}, 발견 {out['found']}, 이미 보존됨(이번 원본 밖) {len(out['from_previous'])}, "
          f"누락 {len(out['missing'])}, 예상 밖 {len(out['unexpected'])}, "
          f"중복 {len(out['duplicates'])}, 비정상 {len(out['abnormal'])}, 분봉 {out['raw_bytes']:,}바이트, 출처 기록 파일 {out['meta_files']}개")
    print(f"  복사 {'예정 ' if out['mode'] == 'dry-run' else ''}{a['copy']}, 이미 보존됨 {a['already_present']}, 충돌 {a['conflict']}, "
          f"비정상 제외 {a['skip_abnormal']}" + (f", 실제 복사 {out['copied']}, 복사 실패 {len(out['copy_errors'])}" if out["mode"] != "dry-run" else ""))
    for e in out["errors"]:
        print(f"  오류: {e}")
    for a_ in out["abnormal"][:20]:
        print(f"  비정상: {a_['file']}: {a_['error']}")
    for e in out["copy_errors"]:
        print(f"  복사 실패: {e}")
    for w in out["warnings"]:
        print(f"  경고: {w}")
    if out.get("verify"):
        v = out["verify"]
        print(f"  복사 뒤 재검증: {v['status']} (일치 {v['ok']}/{v['listed']})")
    print(f"결과: {out['status']}")


def main(argv=None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, help="원본 폴더(raw/{ticker}.json이 있는 곳). 읽기만 한다")
    ap.add_argument("--dest-root", type=Path, help="보존 전용 상위 폴더. 그 아래 날짜 폴더를 만든다")
    ap.add_argument("--date", type=_date, help="대상 거래일 YYYY-MM-DD")
    ap.add_argument("--start", type=_date)
    ap.add_argument("--end", type=_date)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--expected-from", type=Path, help="예상 종목 = 이 폴더의 raw/*.json 이름(이전 보존본 등, 읽기만)")
    g.add_argument("--expected-file", type=Path, help="예상 종목 목록 파일(한 줄에 하나, #은 주석)")
    g.add_argument("--no-expected", action="store_true", help="예상 종목 대조를 하지 않는다(결과는 warning 이상)")
    ap.add_argument("--allow-missing", default="", help="누락을 허용할 종목(쉼표). 거래정지 등 확인한 경우만")
    ap.add_argument("--dry-run", action="store_true", help="복사 없이 계획만 출력")
    ap.add_argument("--verify-only", action="store_true", help="보존본을 체크섬 목록으로 다시 검증(읽기만)")
    ap.add_argument("--dest", type=Path, help="--verify-only: 검증할 보존 디렉터리")
    ap.add_argument("--manifest", type=Path, help="--verify-only: 체크섬 목록(기본 <dest>/MANIFEST.sha256)")
    ap.add_argument("--json-out", type=Path, help="결과 JSON을 쓸 파일(원본·보존 디렉터리 밖)")
    args = ap.parse_args(argv)
    try:
        if args.verify_only:
            if not args.dest:
                raise Refused("--verify-only에는 --dest가 필요합니다")
            out = verify(args.dest, args.manifest, args.source.resolve() if args.source else None)
            guard = [args.dest.resolve()]
        else:
            if not (args.source and args.dest_root):
                raise Refused("--source와 --dest-root가 필요합니다")
            if not (args.expected_from or args.expected_file or args.no_expected):
                raise Refused("--expected-from, --expected-file, --no-expected 중 하나가 필요합니다")
            targets = targets_from(args)
            if args.expected_from:
                expected, exp_src = tickers_in(args.expected_from), f"dir:{args.expected_from}"
            elif args.expected_file:
                expected, exp_src = read_ticker_file(args.expected_file), f"file:{args.expected_file}"
            else:
                expected, exp_src = None, "none"
            allow = [t.strip() for t in args.allow_missing.split(",") if t.strip()]
            out = preserve(args.source, args.dest_root, targets, expected, exp_src, allow, args.dry_run, now)
            guard = [args.source.resolve(), args.dest_root.resolve()]
        if args.json_out:
            jp = args.json_out.resolve()
            if any(_inside(jp, g_) for g_ in guard):
                raise Refused("--json-out은 원본·보존 디렉터리 밖이어야 합니다")
            jp.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except Refused as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    print_result(out)
    return 1 if out["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
