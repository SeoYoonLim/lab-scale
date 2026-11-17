"""GitHub Actions 아티팩트로 거래일 분봉 원본을 보존한다(68차-B, 2026-11-17). Cloudflare R2·추가 Secrets가 필요 없다. DB·Ollama를 쓰지 않는다.

운영 문서: docs/GITHUB_ACTIONS_MINUTE_AUTOMATION_68.md (workflow: .github/workflows/session-minute-actions.yml)

하위 명령:
  python -m scripts.run_actions_minute_preservation run --work-dir "$RUNNER_TEMP/sma" [--lookback 7] [--date D] [--dry-run]
  python -m scripts.run_actions_minute_preservation verify --work-dir "$RUNNER_TEMP/sma"

run:
 1. 최근 거래일(KRX 달력, 67차 plan_targets) 목록을 정한다.
 2. 날짜마다 이 저장소의 아티팩트 `session-minute-<날짜>`(만료 안 됨)를 GitHub REST API로 찾아 내려받고 검증한다
    (manifest.json 봉인·파일별 크기·SHA-256·350종목·52차 verify). 정상이면 실행 폴더(가짜 버킷)에 채운다 = 이미 보존됨.
    손상·불완전하면 채우지 않고(지우지도 않는다) 그 날짜를 다시 수집한다. `session-minute-status-<날짜>`는 복구 불가 표시다.
 3. 67차 실행기(scripts.run_cloud_session_preservation)를 --store local로 그 폴더에 돌린다. 수집·보존·세션 검증·봉인 규칙이 67차와 같다.
 4. 이번 실행에서 새로 봉인된 날짜만 업로드 칸(upload/slot1..7)에 옮긴다. 일부 종목만 받은 날짜는 올리지 않는다(최종 아티팩트 금지).
    업로드 전 저장소 전체 아티팩트 사용량 + 이번 업로드 압축 크기(실측)를 한도와 비교한다. 경고·중단 기준을 넘으면 올리지 않는다(기존 것은 지우지 않음).
 5. GITHUB_OUTPUT에 slotN_name/slotN_path를 쓴다. 업로드는 workflow의 actions/upload-artifact가 한다.
verify: 이번 실행이 올린 아티팩트를 API로 다시 내려받아 SHA-256을 대조하고, 결과를 합쳐 종료 코드를 정한다.

권한: GITHUB_TOKEN(workflow가 자동 발급)에 contents: read, actions: read. 추가 PAT·Secrets 없음. 아티팩트를 지우지 않는다(actions: write 불필요).
종료 코드: 0 = 대상일이 모두 보존됨(또는 휴장 SKIP), 1 = 실패·미완료, 2 = 설정 오류.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

from scripts import preserve_session_minute as psm
from scripts import run_cloud_session_preservation as cloud

TOOL = "run_actions_minute_preservation"
KST = cloud.KST
API = "https://api.github.com"
ART_PREFIX = "session-minute-"
STATUS_PREFIX = "session-minute-status-"
MAX_SLOTS = cloud.PROVIDER_WINDOW_TRADING_DAYS  # 한 실행에서 올릴 수 있는 날짜 수(최근 7거래일)
STORAGE_LIMIT_BYTES = 500 * 1000 * 1000  # GitHub Free 아티팩트 저장 한도(Packages와 공유)
WARN_RATIO, STOP_RATIO = 0.6, 0.8  # 사용량이 한도의 60%를 넘으면 경고, 80%를 넘을 것 같으면 업로드 중단
SECRET_ENV = ("GITHUB_TOKEN", *cloud.SECRET_ENV)


def redact(msg, env: dict | None = None) -> str:
    env = os.environ if env is None else env
    out = str(msg)
    for k in SECRET_ENV:
        v = env.get(k)
        if v and len(v) >= 4:
            out = out.replace(v, "***")
    return out


def art_name(d: date) -> str:
    return f"{ART_PREFIX}{d.isoformat()}"


# ---------- GitHub REST API ----------

class GitHubArtifacts:
    """이 저장소의 아티팩트 조회·내려받기(GITHUB_TOKEN, actions: read). 지우기·쓰기는 하지 않는다."""

    def __init__(self, repo: str, token: str, session=None, retries: int = 3, sleep=time.sleep):
        import requests

        self.repo, self.retries, self.sleep = repo, retries, sleep
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "lab-scale-session-minute"})

    def _get(self, url: str, **kw):
        last = None
        for i in range(self.retries):
            try:
                r = self.s.get(url, timeout=60, **kw)
            except Exception as e:  # noqa: BLE001 - 연결 오류는 다시 시도
                last = e
            else:
                if r.status_code in (401, 403, 404):
                    r.raise_for_status()  # 권한·없음은 다시 시도하지 않는다
                if r.status_code < 500 and r.status_code != 429:
                    r.raise_for_status()
                    return r
                last = RuntimeError(f"GitHub API {r.status_code}")
            self.sleep(2 * (2 ** i))
        raise last

    def list_named(self, name: str) -> list[dict]:
        r = self._get(f"{API}/repos/{self.repo}/actions/artifacts", params={"name": name, "per_page": 100})
        return r.json().get("artifacts", [])

    def list_run(self, run_id: str) -> list[dict]:
        r = self._get(f"{API}/repos/{self.repo}/actions/runs/{run_id}/artifacts", params={"per_page": 100})
        return r.json().get("artifacts", [])

    def list_all(self) -> list[dict]:
        out, page = [], 1
        while True:
            r = self._get(f"{API}/repos/{self.repo}/actions/artifacts", params={"per_page": 100, "page": page})
            arts = r.json().get("artifacts", [])
            out += arts
            if len(arts) < 100 or page >= 50:
                return out
            page += 1

    def usage_bytes(self) -> tuple[int, int]:
        """(만료되지 않은 아티팩트 전체 크기, 그중 session-minute 크기)."""
        live = [a for a in self.list_all() if not a.get("expired")]
        return (sum(a.get("size_in_bytes", 0) for a in live),
                sum(a.get("size_in_bytes", 0) for a in live if a.get("name", "").startswith(ART_PREFIX)))

    def download(self, artifact_id: int) -> bytes:
        return self._get(f"{API}/repos/{self.repo}/actions/artifacts/{artifact_id}/zip", allow_redirects=True).content


# ---------- 아티팩트 내용 검증 ----------

def verify_day_dir(dayd: Path, d: date, n_tickers: int) -> dict:
    """내려받아 푼 날짜 폴더 검증: 봉인(manifest.json, status complete, 같은 날짜) + 봉인에 적힌 모든 파일 크기·SHA-256 + 종목 수 +
    52차 verify(MANIFEST.sha256 전체 재해시·예상 종목 누락). 문제 목록이 비면 정상."""
    problems: list[str] = []
    seal_p = dayd / cloud.SEAL
    if not seal_p.is_file():
        return {"ok": False, "problems": ["manifest.json 없음(봉인 안 된 날짜)"]}
    try:
        seal = json.loads(seal_p.read_text(encoding="utf-8"))
    except ValueError:
        return {"ok": False, "problems": ["manifest.json 형식 오류"]}
    if seal.get("status") != "complete" or seal.get("trade_date") != d.isoformat():
        problems.append(f"봉인 상태·날짜 불일치({seal.get('status')}, {seal.get('trade_date')})")
    for rel, f in (seal.get("files") or {}).items():
        p = dayd / rel
        if not p.is_file():
            problems.append(f"파일 없음 {rel}")
        elif p.stat().st_size != f.get("bytes") or psm.sha256_file(p)[0] != f.get("sha256"):
            problems.append(f"크기·SHA-256 불일치 {rel}")
    if seal.get("ticker_count") != n_tickers:
        problems.append(f"종목 수 {seal.get('ticker_count')} ≠ {n_tickers}")
    try:
        v = psm.verify(dayd)
        if v["status"] == "failed":
            problems.append(f"52차 verify 실패(불일치 {len(v['mismatch'])}, 없음 {len(v['missing'])}, 누락 종목 {len(v.get('missing_tickers') or [])})")
    except psm.Refused as e:
        problems.append(f"52차 verify 거부: {e}")
    return {"ok": not problems, "problems": problems[:20], "files": len(seal.get("files") or {}),
            "sealed_at_kst": seal.get("sealed_at_kst"), "run_id": seal.get("run_id")}


def unzip_to(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"압축 안 경로가 폴더 밖을 가리킵니다: {info.filename}")
        z.extractall(dest)


def zipped_size(folder: Path, level: int = 6) -> int:
    """upload-artifact 기본 압축(zlib 6)과 같은 조건으로 실제 압축 크기를 잰다."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(folder).as_posix())
    return buf.tell()


# ---------- run ----------

def load_status_markers(api, targets: list[date], work: Path, env: dict) -> dict[str, Path]:
    """복구 불가 표시 아티팩트(session-minute-status-<실행 id>, 안에 <날짜>.json)에서 대상 날짜의 표시 파일을 모은다."""
    want = {d.isoformat() for d in targets}
    found: dict[str, Path] = {}
    try:
        arts = [a for a in api.list_all() if a.get("name", "").startswith(STATUS_PREFIX) and not a.get("expired")]
    except Exception:  # noqa: BLE001 - 표시를 못 읽으면 다시 확인(탐침 3종목)하면 된다
        return found
    for a in arts:
        tmp = work / "downloads" / f"status_{a['id']}"
        try:
            unzip_to(api.download(a["id"]), tmp)
        except Exception:  # noqa: BLE001
            continue
        for p in tmp.glob("*.json"):
            if p.stem in want and p.stem not in found:
                found[p.stem] = p
    return found


def seed_existing(api, targets: list[date], seed: Path, work: Path, n_tickers: int, env: dict) -> list[dict]:
    """대상일마다 기존 아티팩트를 찾아 검증하고, 정상인 것만 가짜 버킷(seed)에 채운다. 날짜별 기록을 돌려준다."""
    out = []
    status_files = load_status_markers(api, targets, work, env)
    for d in targets:
        rec: dict = {"date": d.isoformat(), "artifact": art_name(d)}
        try:
            arts = [a for a in api.list_named(art_name(d)) if not a.get("expired")]
        except Exception as e:  # noqa: BLE001
            rec.update(state="api_error", error=redact(e, env)[:200])
            out.append(rec)
            continue
        rec["found"] = [{"id": a["id"], "size": a.get("size_in_bytes"), "created_at": a.get("created_at"),
                         "expires_at": a.get("expires_at"), "run_id": (a.get("workflow_run") or {}).get("id"),
                         "digest": a.get("digest")} for a in arts]
        chosen = None
        for a in sorted(arts, key=lambda a: a.get("created_at") or ""):  # 가장 먼저 만든 정상본을 기준으로 삼는다
            tmp = work / "downloads" / f"{d.isoformat()}_{a['id']}"
            try:
                unzip_to(api.download(a["id"]), tmp)
            except Exception as e:  # noqa: BLE001
                rec.setdefault("damaged", []).append({"id": a["id"], "problem": redact(e, env)[:160]})
                continue
            check = verify_day_dir(tmp, d, n_tickers)
            if check["ok"]:
                chosen = (a, tmp, check)
                break
            rec.setdefault("damaged", []).append({"id": a["id"], "problems": check["problems"]})
        if chosen:
            a, tmp, check = chosen
            dest = seed / cloud.PREFIX / d.isoformat()
            shutil.copytree(tmp, dest)
            rec.update(state="preserved", artifact_id=a["id"], size=a.get("size_in_bytes"), expires_at=a.get("expires_at"),
                       verify=check)
        elif d.isoformat() in status_files:
            st_dir = seed / cloud.PREFIX / "_status"
            st_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(status_files[d.isoformat()], st_dir / f"{d.isoformat()}.json")
            rec.update(state="unrecoverable_known")
        else:
            rec["state"] = "damaged" if rec.get("damaged") else ("expired_or_missing" if not arts else "missing")
        out.append(rec)
    return out


def pick_uploads(seed: Path, before: set[str], run_id: str) -> tuple[list[dict], list[dict]]:
    """이번 실행에서 새로 봉인된 날짜(업로드 대상)와 새 복구 불가 표시."""
    days, status = [], []
    root = seed / cloud.PREFIX
    for dayd in sorted(p for p in root.glob("20*") if p.is_dir()):
        seal = dayd / cloud.SEAL
        if dayd.name in before or not seal.is_file():
            continue  # 기존 정상본이거나 봉인되지 않은 날짜(일부 종목만)는 올리지 않는다
        s = json.loads(seal.read_text(encoding="utf-8"))
        if s.get("status") == "complete" and s.get("run_id") == run_id:
            days.append({"date": dayd.name, "path": dayd})
    for p in sorted((root / "_status").glob("*.json")) if (root / "_status").is_dir() else []:
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("run_id") == run_id:
                status.append({"date": p.stem, "path": p})
        except ValueError:
            pass
    return days, status


def cmd_run(args, env: dict, api=None, now=None, get=None, sleep=None, retry_waits=None, wait=None, clock=None) -> int:
    work = args.work_dir.resolve()
    if work.exists() and any(work.iterdir()):
        print(f"설정 오류: --work-dir가 비어 있지 않습니다: {work}", file=sys.stderr)
        return 2
    work.mkdir(parents=True)
    now = now or datetime.now(KST)
    run_id = f"{now.astimezone(KST).strftime('%Y%m%dT%H%M%S')}_{env.get('GITHUB_RUN_ID', 'local')}"
    report: dict = {"tool": TOOL, "run_id": run_id, "now_kst": now.astimezone(KST).isoformat(timespec="seconds"),
                    "repo": env.get("GITHUB_REPOSITORY"), "retention_days": env.get("ARTIFACT_RETENTION_DAYS"), "days": []}
    try:
        assets = cloud.load_universe()
        st, targets, info = cloud.plan_targets(now, args.lookback, args.date)
        if api is None:
            if not env.get("GITHUB_TOKEN") or not env.get("GITHUB_REPOSITORY"):
                raise cloud.ConfigError("GITHUB_TOKEN·GITHUB_REPOSITORY가 없습니다(workflow 안에서 실행하세요)")
            api = GitHubArtifacts(env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"])
    except (cloud.ConfigError, ValueError) as e:
        print(f"설정 오류: {redact(e, env)}", file=sys.stderr)
        report.update(status="config_error", error=redact(e, env))
        _write_report(work, report)
        return 2
    report["plan"] = {**info, "targets": [d.isoformat() for d in targets]}
    if st != "trading" and args.date is None:
        report["status"] = "skip_non_trading_day"
        print(f"오늘은 KRX 거래일이 아닙니다({st}). 아티팩트를 확인·수집하지 않고 끝냅니다(SKIP)")
        _write_outputs(env, [], [], report)
        _write_report(work, report)
        return 0
    seed = work / "bucket"
    seed.mkdir()
    report["existing"] = seed_existing(api, targets, seed, work, len(assets), env)
    before = {r["date"] for r in report["existing"] if r.get("state") == "preserved"}
    print(f"[{TOOL}] 대상 {report['plan']['targets']} · 기존 정상 {sorted(before)}")
    cloud_argv = ["--work-dir", str(work / "collect"), "--store", "local", "--local-store-dir", str(seed),
                  "--lookback", str(args.lookback), "--report", str(work / "collect_report.json")]
    if args.date:
        cloud_argv += ["--date", args.date.isoformat()]
    if args.dry_run:
        cloud_argv.append("--dry-run")
    rc = cloud.main(cloud_argv, now=now, get=get, env={k: v for k, v in env.items() if k not in SECRET_ENV and k not in ("GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS")},
                    sleep=sleep, retry_waits=retry_waits, wait=wait, clock=clock)
    collect = json.loads((work / "collect_report.json").read_text(encoding="utf-8"))
    report["collect"] = {"rc": rc, "status": collect.get("status"), "days": collect.get("days"),
                         "source_blocked": collect.get("source_blocked")}
    days, status = ([], []) if args.dry_run else pick_uploads(seed, before, collect["run_id"])
    # 용량: 저장소 전체 사용량 + 이번에 올릴 압축 크기(실측)
    sizes = {x["date"]: zipped_size(x["path"]) for x in days}
    try:
        used, used_minute = api.usage_bytes()
    except Exception as e:  # noqa: BLE001
        used = used_minute = None
        report["usage_error"] = redact(e, env)[:200]
    planned = sum(sizes.values())
    storage = {"limit_bytes": STORAGE_LIMIT_BYTES, "used_bytes": used, "used_session_minute_bytes": used_minute,
               "planned_upload_bytes": planned, "planned_per_day": sizes,
               "projected_ratio": round((used + planned) / STORAGE_LIMIT_BYTES, 4) if used is not None else None}
    if used is None:
        storage["decision"] = "upload_without_usage_check"
    elif used + planned > STOP_RATIO * STORAGE_LIMIT_BYTES:
        storage["decision"] = "stopped_over_limit"
        days, status = [], []
    elif used + planned > WARN_RATIO * STORAGE_LIMIT_BYTES:
        storage["decision"] = "warning"
    else:
        storage["decision"] = "ok"
    report["storage"] = storage
    uploads = work / "upload"
    slots = []
    for i, x in enumerate(days[:MAX_SLOTS], 1):
        dest = uploads / f"slot{i}"
        shutil.copytree(x["path"], dest)
        slots.append({"slot": i, "name": f"{ART_PREFIX}{x['date']}", "path": str(dest), "date": x["date"],
                      "zip_bytes": sizes[x["date"]]})
    st_slots = []
    if status:
        dest = uploads / "status"
        dest.mkdir(parents=True)
        for x in status:
            shutil.copy2(x["path"], dest / f"{x['date']}.json")
            st_slots.append({"name": f"{STATUS_PREFIX}{env.get('GITHUB_RUN_ID', 'local')}", "path": str(dest), "date": x["date"]})
    report["uploads"] = slots
    report["status_uploads"] = st_slots
    report["status"] = "ok" if collect.get("status") == "ok" and storage["decision"] != "stopped_over_limit" else "failed"
    _write_outputs(env, slots, st_slots, report)
    _write_report(work, report)
    _summary(env, report)
    print(f"결과: {report['status']} · 새로 올릴 날짜 {[s['date'] for s in slots]} · 용량 {storage}")
    return 0  # 판정은 verify 단계가 업로드 확인과 함께 한다(완성된 날짜는 실패와 관계없이 올린다)


def _write_report(work: Path, report: dict) -> None:
    (work / "run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _write_outputs(env: dict, slots: list[dict], st_slots: list[dict], report: dict) -> None:
    p = env.get("GITHUB_OUTPUT")
    if not p:
        return
    with open(p, "a", encoding="utf-8") as fh:
        for i in range(1, MAX_SLOTS + 1):
            s = next((x for x in slots if x["slot"] == i), None)
            fh.write(f"slot{i}_name={s['name'] if s else ''}\nslot{i}_path={s['path'] if s else ''}\n")
        fh.write(f"status_dir={st_slots[0]['path'] if st_slots else ''}\n")
        fh.write(f"run_status={report.get('status')}\n")


def _summary(env: dict, report: dict) -> None:
    lines = [f"## 분봉 아티팩트 보존 {report.get('status')}", "",
             f"- 실행 {report['run_id']} · 대상 {', '.join((report.get('plan') or {}).get('targets', []))}",
             f"- 용량: {json.dumps(report.get('storage'), ensure_ascii=False)}", "",
             "| 거래일 | 기존 아티팩트 | 이번 수집 | 새 업로드 |", "| --- | --- | --- | --- |"]
    col = {r.get("date"): r.get("status") for r in (report.get("collect") or {}).get("days") or []}
    up = {s["date"] for s in report.get("uploads", [])}
    for r in report.get("existing", []):
        lines.append(f"| {r['date']} | {r.get('state')} | {col.get(r['date'], '-')} | {'예' if r['date'] in up else '-'} |")
    msg = "; ".join(f"{r['date']}={r.get('state')}/{col.get(r['date'], '-')}" for r in report.get("existing", []))
    if env.get("GITHUB_ACTIONS") == "true":
        lvl = "notice" if report.get("status") == "ok" else "error"
        print(f"::{lvl} title=session-minute-artifacts::{report.get('status')} · {msg}"[:900], flush=True)
    p = env.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


# ---------- verify ----------

def cmd_verify(args, env: dict, api=None) -> int:
    work = args.work_dir.resolve()
    report = json.loads((work / "run_report.json").read_text(encoding="utf-8"))
    if report.get("status") in ("skip_non_trading_day",):
        print("휴장일 SKIP: 확인할 업로드가 없습니다")
        return 0
    if report.get("status") == "config_error":
        return 2
    api = api or GitHubArtifacts(env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"])
    expected = {s["name"]: s for s in report.get("uploads", [])}
    checks = []
    if expected:
        arts = {a["name"]: a for a in api.list_run(env.get("GITHUB_RUN_ID", ""))}
        for name, s in expected.items():
            a = arts.get(name)
            if a is None:
                checks.append({"name": name, "ok": False, "problem": "이번 실행의 아티팩트 목록에 없음"})
                continue
            tmp = work / "verify" / name
            unzip_to(api.download(a["id"]), tmp)
            c = verify_day_dir(tmp, date.fromisoformat(s["date"]), cloud.EXPECTED_ASSETS["stock"] + cloud.EXPECTED_ASSETS["etf"])
            local = Path(s["path"])
            same = all(psm.sha256_file(p)[0] == psm.sha256_file(tmp / p.relative_to(local))[0]
                       for p in local.rglob("*") if p.is_file())
            checks.append({"name": name, "ok": c["ok"] and same, "artifact_id": a["id"], "size_in_bytes": a.get("size_in_bytes"),
                           "expires_at": a.get("expires_at"), "digest": a.get("digest"), "same_as_local": same,
                           "problems": c["problems"]})
    report["verify_uploads"] = checks
    bad = [c for c in checks if not c["ok"]]
    final = "ok" if report.get("status") == "ok" and not bad else "failed"
    report["final_status"] = final
    _write_report(work, report)
    if env.get("GITHUB_ACTIONS") == "true":
        lvl = "notice" if final == "ok" else "error"
        sizes = ", ".join(f"{c['name']}={c.get('size_in_bytes')}B ok={c['ok']}" for c in checks) or "새 업로드 없음"
        print(f"::{lvl} title=session-minute-verify::{final} · {sizes}"[:900], flush=True)
    p = env.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("\n### 업로드 재검증\n" + "\n".join(f"- {c['name']}: {'정상' if c['ok'] else '실패'} "
                                                     f"({c.get('size_in_bytes')}B, {c.get('problems') or c.get('problem') or ''})"
                                                     for c in checks) + "\n")
    print(json.dumps({"final_status": final, "verify_uploads": checks}, ensure_ascii=False, indent=1, default=str))
    return 0 if final == "ok" else 1


def main(argv=None, env: dict | None = None, **kw) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--work-dir", required=True, type=Path)
    r.add_argument("--lookback", type=int, default=cloud.DEFAULT_LOOKBACK)
    r.add_argument("--date", type=date.fromisoformat)
    r.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("--work-dir", required=True, type=Path)
    args = ap.parse_args(argv)
    env = dict(os.environ) if env is None else env
    if args.cmd == "run":
        return cmd_run(args, env, **kw)
    return cmd_verify(args, env, api=kw.get("api"))


if __name__ == "__main__":
    sys.exit(main())
