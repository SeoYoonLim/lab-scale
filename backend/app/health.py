"""GET /api/health 상태 검사(2026-10-28 안정화). 검사마다 짧은 상한(CHECK_TIMEOUT_S)으로 병렬 실행하고, 하나가 실패하거나
멈춰도 예외 없이 결과에 사유를 남긴다. 응답에는 비밀값·내부 주소·버전(마이그레이션 리비전, 모델 태그 등)을 넣지 않는다.

검사: db(SELECT 1), migrations(DB의 alembic 리비전 == 코드의 head), ollama(서버 연결, 기본 모델 설치·메모리 로딩 여부),
data(마지막 시세 날짜와 stale, app.data_status.is_stale - GET /api/status와 같은 규칙).
"""

import concurrent.futures
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

CHECK_TIMEOUT_S = 2.0
OLLAMA_MODEL = "llama3.1:8b"  # app.agent.MODEL_NAME과 같은 값(health가 agent를 import하지 않게 따로 둔다)
_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _ollama_host() -> str:
    # 기본값은 ollama 클라이언트와 같은 127.0.0.1(Windows에서 localhost는 IPv6를 먼저 시도해 약 2초 늦게 연결된다)
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    return host if host.startswith("http") else "http://" + host


def check_db() -> dict:
    from sqlalchemy import text

    from app.db.session import engine

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"ok": True}


def check_migrations() -> dict:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from app.db.session import engine

    heads = set(ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_heads())
    with engine.connect() as conn:
        current = {r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))}
    ok = current == heads
    return {"ok": ok, **({} if ok else {"reason": "DB 스키마가 코드의 최신 마이그레이션과 다릅니다"})}


def check_ollama(get=None) -> dict:
    import requests

    get = get or requests.get
    host = _ollama_host()
    tags = get(host + "/api/tags", timeout=(1, CHECK_TIMEOUT_S)).json().get("models", [])
    names = {m.get("name") for m in tags}
    installed = OLLAMA_MODEL in names or f"{OLLAMA_MODEL}:latest" in names
    loaded_names = {m.get("name") for m in get(host + "/api/ps", timeout=(1, CHECK_TIMEOUT_S)).json().get("models", [])}
    loaded = OLLAMA_MODEL in loaded_names or f"{OLLAMA_MODEL}:latest" in loaded_names
    out = {"ok": installed, "model_installed": installed, "model_loaded": loaded}
    if not installed:
        out["reason"] = "기본 모델이 설치되어 있지 않습니다"
    return out


def check_data(today=None) -> dict:
    from sqlalchemy import func

    from app.data_status import KST, is_stale
    from app.db.session import SessionLocal
    from app.models import StockPrice

    today = today or datetime.now(KST).date()
    db = SessionLocal()
    try:
        latest = db.query(func.max(StockPrice.price_date)).scalar()
    finally:
        db.close()
    stale = is_stale(latest, today)
    out = {"ok": not stale, "latest_date": latest.isoformat() if latest else None, "stale": stale}
    if stale:
        out["reason"] = "시세 데이터가 오래됐습니다" if latest else "시세 데이터가 없습니다"
    return out


CHECKS: dict[str, Callable[[], dict]] = {
    "db": check_db,
    "migrations": check_migrations,
    "ollama": check_ollama,
    "data": check_data,
}


def _reason(exc: BaseException) -> str:
    """예외는 종류만(메시지에는 주소·SQL이 들어갈 수 있어 내보내지 않는다)."""
    if isinstance(exc, concurrent.futures.TimeoutError):
        return "응답 시간 초과"
    return f"연결 또는 조회 실패({type(exc).__name__})"


def run_checks(checks: dict[str, Callable[[], dict]] | None = None, timeout: float = CHECK_TIMEOUT_S) -> dict:
    """모든 검사를 병렬로 돌려 {"status": "ok"|"degraded", "checks": {이름: {"ok", ...}}}. 예외를 올리지 않는다.
    상한을 넘긴 검사의 스레드는 끝날 때까지 남지만 응답은 기다리지 않는다(요청마다 새 풀이라 다음 요청을 막지 않는다)."""
    checks = checks or CHECKS
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(checks), thread_name_prefix="health")
    try:
        futures = {name: pool.submit(fn) for name, fn in checks.items()}
        concurrent.futures.wait(futures.values(), timeout=timeout)  # 전체 상한(검사들은 병렬)
        results = {}
        for name, fut in futures.items():
            if not fut.done():
                results[name] = {"ok": False, "reason": "응답 시간 초과"}
                continue
            try:
                r = fut.result()
                results[name] = r if isinstance(r, dict) and "ok" in r else {"ok": False, "reason": "알 수 없는 결과"}
            except Exception as e:  # noqa: BLE001 - 사유만 남긴다
                results[name] = {"ok": False, "reason": _reason(e)}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {"status": "ok" if all(r["ok"] for r in results.values()) else "degraded", "checks": results}
