"""48차 회귀: 47차 개발 DB 교정 때 scripts/audit_close_basis.py가 os.environ["DATABASE_URL"]을 직접 읽어 KeyError로 멈췄다.
실행 스크립트는 리허설 대상일 때만 DATABASE_URL을 내보냈고, 개발 DB 대상(앱이 backend/.env에서 주소를 읽는 경우)은 리허설에서 지나가지 않았다.
여기서는 (1) 환경변수 없이도 감사 스크립트가 앱과 같은 주소로 DB 이름만 바꾸는지, (2) scripts/·app/·evals/에 같은 패턴이 다시 생기지 않는지 확인한다.
DB·네트워크에는 접속하지 않는다."""
import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]


def test_db_url_for_swaps_only_database_name():
    from scripts.audit_close_basis import db_url_for

    base = "postgresql+psycopg://dummy_user:dummy%40pw@db.example:15432/ai_investment?sslmode=disable"  # 견본 값
    got = make_url(db_url_for("ai_investment_verify", base))
    want = make_url(base)
    assert got.database == "ai_investment_verify"
    assert (got.drivername, got.username, got.password, got.host, got.port, dict(got.query)) == \
        (want.drivername, want.username, want.password, want.host, want.port, dict(want.query))


def test_db_url_for_default_is_app_db_url():
    from app.db.session import DB_URL
    from scripts import audit_close_basis as acb

    assert make_url(acb.db_url_for(make_url(DB_URL).database)) == make_url(DB_URL)


def test_audit_script_resolves_url_without_database_url_env():
    """47차 실패 재현: DATABASE_URL을 지운 환경에서 감사 모듈의 주소 계산이 KeyError 없이 앱 주소를 쓴다."""
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    code = ("from app.db.session import DB_URL; from scripts.audit_close_basis import db_url_for; "
            "from sqlalchemy.engine import make_url; "
            "u = make_url(db_url_for('ai_investment_x')); b = make_url(DB_URL); "
            "assert u.database == 'ai_investment_x' and (u.host, u.port, u.username) == (b.host, b.port, b.username); print('ok')")
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().endswith("ok")


def test_no_direct_database_url_environ_lookup_in_backend_code():
    """주소는 app.db.session.DB_URL 한 곳에서 정한다. os.environ["DATABASE_URL"]은 .env만 있는 실행에서 KeyError가 난다."""
    pat = re.compile(r"""os\.environ\[\s*["']DATABASE_URL["']\s*\]""")
    hits = [f"{p.relative_to(BACKEND)}:{i}" for d in ("app", "scripts", "evals") for p in (BACKEND / d).rglob("*.py")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pat.search(line)]
    assert hits == []
