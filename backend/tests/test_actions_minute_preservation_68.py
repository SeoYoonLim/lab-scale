"""68차-B GitHub Actions 아티팩트 보존(scripts/run_actions_minute_preservation.py). GitHub API·원천은 가짜(외부 요청·DB·Ollama 없음)."""
import io
import json
import zipfile
from argparse import Namespace
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

from scripts import run_actions_minute_preservation as am
from tests.test_cloud_session_preservation_67 import ASSETS, KST, NOW, Source

D = date(2026, 11, 16)
TOKEN = "ghs_dummyExampleTokenValue0123456789"


def zip_dir(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(folder).as_posix())
    return buf.getvalue()


class FakeAPI:
    """GitHub 아티팩트 API 흉내. 업로드는 upload()(workflow의 upload-artifact 역할). 지우기는 없다."""

    def __init__(self, used_bytes=0):
        self.arts: list[dict] = []
        self.blobs: dict[int, bytes] = {}
        self.extra_used = used_bytes
        self.downloads: list[int] = []

    def upload(self, name, data: bytes, run_id="r1", expired=False, created="2026-11-17T12:00:00Z"):
        aid = len(self.arts) + 1
        self.arts.append({"id": aid, "name": name, "size_in_bytes": len(data), "expired": expired, "created_at": created,
                          "expires_at": "2026-12-17T12:00:00Z", "workflow_run": {"id": run_id}, "digest": f"sha256:{aid}"})
        self.blobs[aid] = data
        return aid

    def list_named(self, name):
        return [a for a in self.arts if a["name"] == name]

    def list_all(self):
        return list(self.arts)

    def list_run(self, run_id):
        return [a for a in self.arts if a["workflow_run"]["id"] == run_id]

    def usage_bytes(self):
        live = [a for a in self.arts if not a["expired"]]
        return self.extra_used + sum(a["size_in_bytes"] for a in live), sum(
            a["size_in_bytes"] for a in live if a["name"].startswith(am.ART_PREFIX))

    def download(self, aid):
        self.downloads.append(aid)
        return self.blobs[aid]


def run(tmp_path, api, src, *extra, name="w", now=NOW, run_id="r1", raw_enabled=True):
    """68차 업로드 로직 시험은 69차 잠금을 연 상태(raw_enabled=True)로 돈다. 잠금 기본 동작은 69차 시험이 본다."""
    out = tmp_path / f"{name}_out.txt"
    env = {"GITHUB_RUN_ID": run_id, "GITHUB_OUTPUT": str(out), "GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": TOKEN}
    if raw_enabled:
        env[am.RAW_ENABLE_ENV] = "true"
    rc = am.main(["run", "--work-dir", str(tmp_path / name), *extra], env=env, api=api, now=now, get=src, sleep=0,
                 retry_waits=(0, 0), wait=lambda s: None, clock=lambda: now)
    rep = json.loads((tmp_path / name / "run_report.json").read_text(encoding="utf-8"))
    outputs = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines()) if out.exists() else {}
    return rc, rep, outputs, env


def do_uploads(api, outputs, run_id="r1"):
    """workflow의 upload-artifact 단계: 빈 칸이 아닌 slot만 올린다."""
    for i in range(1, am.MAX_SLOTS + 1):
        if outputs.get(f"slot{i}_name"):
            api.upload(outputs[f"slot{i}_name"], zip_dir(Path(outputs[f"slot{i}_path"])), run_id=run_id)
    if outputs.get("status_dir"):
        api.upload(f"{am.STATUS_PREFIX}{run_id}", zip_dir(Path(outputs["status_dir"])), run_id=run_id)


def verify(tmp_path, api, env, name="w"):
    return am.main(["verify", "--work-dir", str(tmp_path / name)], env=env, api=api)


# ---------- 업로드·재검증·SHA-256 ----------

def test_new_day_is_sealed_uploaded_and_reverified(tmp_path):
    api, src = FakeAPI(), Source(days=[D])
    rc, rep, out, env = run(tmp_path, api, src, "--date", D.isoformat())
    assert rc == 0 and rep["status"] == "ok"
    assert out["slot1_name"] == "session-minute-2026-11-16" and out["slot2_name"] == "" and out["status_dir"] == ""
    slot = Path(out["slot1_path"])
    seal = json.loads((slot / "manifest.json").read_text(encoding="utf-8"))
    assert seal["ticker_count"] == 350 and len(list((slot / "raw").glob("*.json"))) == 350
    assert rep["storage"]["decision"] == "ok" and rep["storage"]["planned_per_day"]["2026-11-16"] > 0
    do_uploads(api, out)
    assert verify(tmp_path, api, env) == 0
    final = json.loads((tmp_path / "w" / "run_report.json").read_text(encoding="utf-8"))
    assert final["final_status"] == "ok" and final["verify_uploads"][0]["same_as_local"] is True


def test_verify_detects_a_changed_upload(tmp_path):
    api, src = FakeAPI(), Source(days=[D])
    rc, rep, out, env = run(tmp_path, api, src, "--date", D.isoformat())
    raw = Path(out["slot1_path"]) / "raw" / "005930.json"
    good = raw.read_bytes()
    raw.write_bytes(b"[]")  # 업로드 도중 바뀐 것처럼
    do_uploads(api, out)
    raw.write_bytes(good)
    assert verify(tmp_path, api, env) == 1


# ---------- 이전 아티팩트 조회·내려받기·중복 방지 ----------

def test_existing_valid_artifact_is_downloaded_verified_and_not_reuploaded(tmp_path):
    api, src = FakeAPI(), Source(days=[D])
    _, _, out, _ = run(tmp_path, api, src, "--date", D.isoformat(), name="a")
    do_uploads(api, out)
    n_calls, n_arts = len(src.calls), len(api.arts)
    rc, rep, out2, env = run(tmp_path, api, src, "--date", D.isoformat(), name="b", run_id="r2")
    ex = rep["existing"][0]
    assert ex["state"] == "preserved" and ex["verify"]["ok"] and api.downloads
    assert out2["slot1_name"] == "" and len(src.calls) == n_calls  # 원천 요청 0, 다시 올리지 않음
    assert rep["collect"]["days"][0]["status"] == "already_complete"
    do_uploads(api, out2, "r2")
    assert len(api.arts) == n_arts and verify(tmp_path, api, env, name="b") == 0


def test_damaged_artifact_is_recollected_and_old_one_is_kept(tmp_path):
    api, src = FakeAPI(), Source(days=[D])
    _, _, out, _ = run(tmp_path, api, src, "--date", D.isoformat(), name="a")
    slot = Path(out["slot1_path"])
    (slot / "raw" / "000660.json").write_bytes(b"[]")  # 손상본
    api.upload("session-minute-2026-11-16", zip_dir(slot))
    rc, rep, out2, _ = run(tmp_path, api, src, "--date", D.isoformat(), name="b", run_id="r2")
    ex = rep["existing"][0]
    assert ex["state"] == "damaged" and any("raw/000660.json" in p for p in ex["damaged"][0]["problems"])
    assert out2["slot1_name"] == "session-minute-2026-11-16"  # 다시 받아 새 아티팩트로 올린다
    assert len(api.arts) == 1  # 기존(손상) 아티팩트는 지우지 않는다(지우기 API 없음)


def test_expired_artifact_counts_as_missing(tmp_path):
    api, src = FakeAPI(), Source(days=[D])
    _, _, out, _ = run(tmp_path, api, src, "--date", D.isoformat(), name="a")
    api.upload("session-minute-2026-11-16", zip_dir(Path(out["slot1_path"])), expired=True)
    rc, rep, out2, _ = run(tmp_path, api, src, "--date", D.isoformat(), name="b", run_id="r2")
    assert rep["existing"][0]["state"] == "expired_or_missing" and out2["slot1_name"] == "session-minute-2026-11-16"


def test_unzip_rejects_paths_outside_the_folder(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.json", "[]")
    with pytest.raises(ValueError):
        am.unzip_to(buf.getvalue(), tmp_path / "x")


# ---------- 일부 종목 실패·누락일 복구·복구 불가 ----------

def test_partial_day_is_not_uploaded_as_final_artifact(tmp_path):
    api = FakeAPI()
    src = Source(days=[D], fail={"005930": requests.HTTPError("500 Server Error")})
    rc, rep, out, env = run(tmp_path, api, src, "--date", D.isoformat())
    assert out["slot1_name"] == "" and rep["status"] == "failed"
    assert rep["collect"]["days"][0]["status"] == "incomplete_tickers"
    assert verify(tmp_path, api, env) == 1


def test_missing_days_in_window_are_recovered_and_expired_one_marked(tmp_path):
    api, src = FakeAPI(), Source(days=[D, date(2026, 11, 17)])  # 11-13은 원천에도 없다고 가정
    rc, rep, out, env = run(tmp_path, api, src, "--lookback", "3")
    assert [rep["existing"][i]["state"] for i in range(3)] == ["expired_or_missing"] * 3
    st = {d["date"]: d["status"] for d in rep["collect"]["days"]}
    assert st == {"2026-11-13": "unrecoverable_new", "2026-11-16": "completed", "2026-11-17": "completed"}
    assert {out["slot1_name"], out["slot2_name"]} == {"session-minute-2026-11-16", "session-minute-2026-11-17"}
    assert out["status_dir"] and (Path(out["status_dir"]) / "2026-11-13.json").is_file()
    assert rep["status"] == "failed"  # 복구 불가가 처음 확인된 실행은 실패로 알린다
    do_uploads(api, out)
    calls = len(src.calls)
    rc, rep2, out2, env2 = run(tmp_path, api, src, "--lookback", "3", name="b", run_id="r2")
    assert [r["state"] for r in rep2["existing"]] == ["unrecoverable_known", "preserved", "preserved"]
    assert len(src.calls) == calls and rep2["status"] == "ok" and out2["slot1_name"] == ""
    assert verify(tmp_path, api, env2, name="b") == 0


# ---------- 용량·휴장·비밀값 ----------

def test_storage_over_stop_threshold_uploads_nothing_and_deletes_nothing(tmp_path):
    api, src = FakeAPI(used_bytes=int(am.STORAGE_LIMIT_BYTES * am.STOP_RATIO)), Source(days=[D])
    rc, rep, out, env = run(tmp_path, api, src, "--date", D.isoformat())
    assert rep["storage"]["decision"] == "stopped_over_limit" and out["slot1_name"] == "" and rep["status"] == "failed"
    assert verify(tmp_path, api, env) == 1


def test_storage_warning_still_uploads(tmp_path):
    api, src = FakeAPI(used_bytes=int(am.STORAGE_LIMIT_BYTES * (am.WARN_RATIO + 0.05))), Source(days=[D])
    rc, rep, out, _ = run(tmp_path, api, src, "--date", D.isoformat())
    assert rep["storage"]["decision"] == "warning" and out["slot1_name"]


def test_holiday_skips_without_api_or_source(tmp_path):
    class NoAPI(FakeAPI):
        def list_named(self, name):
            pytest.fail("휴장일에는 아티팩트를 조회하지 않는다")

    src = Source()
    rc, rep, out, env = run(tmp_path, NoAPI(), src, now=datetime(2026, 12, 25, 20, 20, tzinfo=KST))
    assert rc == 0 and rep["status"] == "skip_non_trading_day" and src.calls == [] and out["slot1_name"] == ""
    assert verify(tmp_path, NoAPI(), env) == 0


def test_token_is_redacted_and_missing_token_is_config_error(tmp_path, capsys):
    assert am.redact(f"x {TOKEN} y", {"GITHUB_TOKEN": TOKEN}) == "x *** y"
    rc = am.main(["run", "--work-dir", str(tmp_path / "w")], env={}, now=NOW)
    assert rc == 2 and "GITHUB_TOKEN" in capsys.readouterr().err


def test_compressed_size_is_measured_like_upload_artifact(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.json").write_text(json.dumps([{"x": i} for i in range(2000)]), encoding="utf-8")
    raw = (tmp_path / "d" / "a.json").stat().st_size
    assert 0 < am.zipped_size(tmp_path / "d") < raw / 3


def test_universe_still_350():
    assert len(ASSETS) == 350


# ---------- 원천 값 이상으로 54차 검증이 거부한 날(실측: 2026-11-10 284430 거래량 -8) ----------

def test_rejected_day_is_kept_as_unsealed_artifact_once_and_not_refetched(tmp_path):
    api, src = FakeAPI(), Source(days=[D], dup={"005930"})  # 그날 한 종목이 54차 규칙에 걸린다
    rc, rep, out, env = run(tmp_path, api, src, "--date", D.isoformat())
    assert rep["collect"]["days"][0]["status"] == "session_validation_failed" and rep["status"] == "failed"
    assert out["slot1_name"] == "session-minute-unsealed-2026-11-16"  # 최종본 이름(session-minute-<날짜>)이 아니다
    slot = Path(out["slot1_path"])
    assert not (slot / "manifest.json").exists() and len(list((slot / "raw").glob("*.json"))) == 350
    assert json.loads((slot / "session_validate.json").read_text(encoding="utf-8"))["ok"] is False
    do_uploads(api, out)
    assert verify(tmp_path, api, env) == 1  # 처음 확인한 실행은 실패로 알린다(업로드 재검증 자체는 통과)
    final = json.loads((tmp_path / "w" / "run_report.json").read_text(encoding="utf-8"))
    assert final["verify_uploads"][0]["ok"] is True
    calls = len(src.calls)
    rc, rep2, out2, env2 = run(tmp_path, api, src, "--date", D.isoformat(), name="b", run_id="r2")
    assert rep2["existing"][0]["state"] == "rejected_known" and len(src.calls) == calls and out2["slot1_name"] == ""
    assert rep2["status"] == "ok" and verify(tmp_path, api, env2, name="b") == 0


# ---------- 69차: 원본 수집·업로드 잠금 ----------

@pytest.mark.parametrize("flag", [None, "false", "True", "1", ""])
def test_raw_lock_forces_dry_run_no_source_no_upload_slots(tmp_path, flag):
    api, src = FakeAPI(), Source(days=[D])
    out = tmp_path / "o.txt"
    env = {"GITHUB_RUN_ID": "r1", "GITHUB_OUTPUT": str(out), "GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": TOKEN}
    if flag is not None:
        env[am.RAW_ENABLE_ENV] = flag
    rc = am.main(["run", "--work-dir", str(tmp_path / "w"), "--date", D.isoformat()], env=env, api=api, now=NOW, get=src,
                 sleep=0, retry_waits=(0, 0), wait=lambda s: None, clock=lambda: NOW)
    rep = json.loads((tmp_path / "w" / "run_report.json").read_text(encoding="utf-8"))
    outputs = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    assert rc == 0
    assert src.calls == []                      # 원천 요청 0
    assert rep["forced_dry_run"] is True and rep["raw_locked"] is True and rep["dry_run"] is True
    assert rep["uploads"] == [] and rep["status_uploads"] == []
    assert all(outputs[f"slot{i}_name"] == "" for i in range(1, am.MAX_SLOTS + 1)) and outputs["status_dir"] == ""
    assert api.arts == []


def test_raw_lock_still_lists_existing_artifacts_read_only(tmp_path):
    api, src = FakeAPI(), Source(days=[D])
    rc, rep, out, env = run(tmp_path, api, src, "--date", D.isoformat(), name="a")
    do_uploads(api, out)
    n = len(api.arts)
    src2 = Source(days=[D])
    rc2, rep2, out2, _ = run(tmp_path, api, src2, "--date", D.isoformat(), name="b", run_id="r2", raw_enabled=False)
    assert rc2 == 0 and src2.calls == [] and len(api.arts) == n
    assert [r["state"] for r in rep2["existing"]] == ["preserved"]
    assert rep2["forced_dry_run"] is True and rep2["uploads"] == []


def test_workflow_has_no_automatic_triggers_and_upload_is_locked():
    """PyYAML 없이 줄 단위로 본다: on: 블록의 최상위 키는 workflow_dispatch 하나, 원본 업로드 단계는 모두 잠금 조건."""
    path = Path(__file__).resolve().parents[2] / ".github/workflows/session-minute-actions.yml"
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index("on:")
    end = next(i for i in range(start + 1, len(lines)) if lines[i] and not lines[i].startswith(" "))
    on_block = lines[start + 1:end]
    triggers = [ln.strip().rstrip(":") for ln in on_block if ln.startswith("  ") and not ln.startswith("   ") and ln.strip()]
    assert triggers == ["workflow_dispatch"]  # push·schedule·pull_request 없음
    i = next(k for k, ln in enumerate(on_block) if ln.strip() == "dry_run:")
    assert "default: true" in [ln.strip() for ln in on_block[i:i + 4]]
    assert '  RAW_UPLOAD_ENABLED: "false"' in lines
    uses = [k for k, ln in enumerate(lines) if "uses: actions/upload-artifact" in ln]
    gated = [k for k in uses if "steps.run.outputs.slot" in lines[k - 1] or "steps.run.outputs.status_dir" in lines[k - 1]]
    assert len(gated) == am.MAX_SLOTS + 1
    assert all(lines[k - 1].strip().startswith("- if: env.RAW_UPLOAD_ENABLED == 'true' && ") for k in gated)
    rest = [k for k in uses if k not in gated]  # 잠금 없는 업로드 = 원본이 없는 실행 기록 하나
    assert len(rest) == 1 and any("run_report.json" in ln for ln in lines[rest[0]:rest[0] + 5])
    text = "\n".join(lines)
    assert "SESSION_MINUTE_RAW_ENABLED: ${{ env.RAW_UPLOAD_ENABLED }}" in text
    assert 'if [[ "${SESSION_MINUTE_RAW_ENABLED:-false}" != "true" ]]; then' in text
