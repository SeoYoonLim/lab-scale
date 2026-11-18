"""승인 전 대기 마이그레이션(alembic/pending)을 **시험 DB에만** 적용한다(2026-11-14, 53차).

  python -m scripts.alembic_pending --database-url postgresql+psycopg://...:15432/ai_investment_session_design53 upgrade 0f6d0ec52ac3
  python -m scripts.alembic_pending --database-url <시험 DB> downgrade c7e2a9f4d158
  python -m scripts.alembic_pending --database-url <시험 DB> current

- 버전 위치 = alembic/versions + alembic/pending. 기본 alembic.ini(versions만)는 그대로라 일반 `alembic upgrade head`와
  /api/health 검사는 pending을 모른다.
- 개발 DB(.env의 DATABASE_URL, 이름 ai_investment)와 보존 중인 DB(PROTECTED_DATABASES)를 가리키면 거부한다.
  그 밖에도 허용 이름(ai_investment_session_*, captest_*)이 아니면 거부한다(54차).
  개발 DB 적용은 승인 뒤 파일을 alembic/versions로 옮겨 일반 절차로 한다(이 도구로 하지 않는다).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
PENDING = BACKEND / "alembic" / "pending"
VERSIONS = BACKEND / "alembic" / "versions"
DEV_DEFAULT_URL = "postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment"
PROTECTED_DATABASES = {"ai_investment", "ai_investment_postcheck47", "ai_investment_verify", "postgres", "template0", "template1"}
# 쓰기를 허용하는 시험 DB 이름(54차): 세션 리허설 DB와 CI·통합 테스트가 만드는 임시 DB만
ALLOWED_TEST_DATABASE = re.compile(r"(ai_investment_session_[a-z0-9_]+|captest_[a-z0-9_]+)")


class Refused(Exception):
    pass


def dev_url():
    """개발 DB 주소: 저장소 .env의 DATABASE_URL(없으면 app.db.session 기본값). 환경 변수는 보지 않는다(시험 DB로 덮어쓴 값일 수 있음)."""
    env = BACKEND.parent / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                return make_url(line.split("=", 1)[1].strip().strip('"').strip("'"))
    return make_url(DEV_DEFAULT_URL)


def check_target(url: str, allow_dev_db: bool = False):
    """쓰기 대상 DB 확인. 개발 DB·보존 DB면 Refused."""
    target = make_url(url)
    if not target.database:
        raise Refused("DB 이름이 없는 주소입니다")
    dev = dev_url()
    same_dev = (target.host, target.port or 5432, target.database) == (dev.host, dev.port or 5432, dev.database)
    if (same_dev or target.database == dev.database) and not allow_dev_db:
        raise Refused(f"개발 DB({target.database})에는 쓰지 않습니다(승인 전)")
    if target.database in PROTECTED_DATABASES - {dev.database}:
        raise Refused(f"보존 중인 DB({target.database})에는 쓰지 않습니다")
    if not (allow_dev_db and target.database == dev.database) and not ALLOWED_TEST_DATABASE.fullmatch(target.database):
        raise Refused(f"허용된 시험 DB 이름이 아닙니다: {target.database}(ai_investment_session_* 또는 captest_*)")
    return target


def alembic_config():
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("version_locations", os.pathsep.join([str(VERSIONS), str(PENDING)]))
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--database-url", required=True)
    ap.add_argument("command", choices=["upgrade", "downgrade", "current", "heads"])
    ap.add_argument("revision", nargs="?")
    args = ap.parse_args(argv)
    try:
        target = check_target(args.database_url)
        if args.command in ("upgrade", "downgrade") and not args.revision:
            raise Refused("upgrade/downgrade에는 리비전을 적으세요(head·-1 같은 상대 지정 대신 리비전 ID)")
    except Refused as e:
        print(f"거부: {e}", file=sys.stderr)
        return 2
    # alembic/env.py가 app.db.session.DB_URL(환경 변수 우선)에서 읽는다. 읽기가 아니라 넣기라 KeyError 위험은 없다
    os.environ.update({"DATABASE_URL": target.render_as_string(hide_password=False)})
    from alembic import command

    cfg = alembic_config()
    print(f"대상 DB: {target.render_as_string(hide_password=True)}")
    {"upgrade": lambda: command.upgrade(cfg, args.revision), "downgrade": lambda: command.downgrade(cfg, args.revision),
     "current": lambda: command.current(cfg, verbose=False), "heads": lambda: command.heads(cfg)}[args.command]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
