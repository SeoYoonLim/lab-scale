"""클라우드 패키지 동기화의 DB 통합 테스트(2026-11-23): 같은 패키지를 두 번 넣어도 중복이 없고, 이미 있는 날짜는 값이 달라도
덮어쓰지 않으며, 구간 첫 행의 등락률은 저장된 직전 종가로 계산한다. 외부 API·git은 쓰지 않는다(폴더 원천).
CI의 빈 DB(시드만)에서 돈다. 만든 행은 지운다: exchange_rate(pair_code 'TST/KRW')와 그 격리·경고 기록.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app import market_data_sync as S
from app import market_package as P
from scripts import cloud_market_data as CM

pytestmark = pytest.mark.integration
KST = ZoneInfo("Asia/Seoul")
KEY = bytes(range(32))
PAIR = "TST/KRW"


@pytest.fixture
def cleanup(dev_db):
    yield
    dev_db.rollback()
    dev_db.execute(text("DELETE FROM exchange_rate WHERE pair_code = :p"), {"p": PAIR})
    dev_db.execute(text("DELETE FROM data_ingestion_quarantine WHERE source_key LIKE :k"), {"k": f"exchange_rate:{PAIR}:%"})
    dev_db.commit()


def test_same_package_twice_no_duplicates_and_existing_not_overwritten(dev_db, cleanup, tmp_path):
    dev_db.execute(text("INSERT INTO exchange_rate (pair_code, price_date, close_price, change_pct) VALUES (:p, '2026-11-18', 1333.01, -0.20)"),
                   {"p": PAIR})
    dev_db.commit()
    rows = [{"date": "2026-11-18", "pair": PAIR, "close": 1333.50, "change_pct": None},  # 이미 있음(값 다름) -> 바꾸지 않음
            {"date": "2026-11-19", "pair": PAIR, "close": 1330.40, "change_pct": None},
            {"date": "2026-11-20", "pair": PAIR, "close": 1334.79, "change_pct": None}]
    manifest, files = P.build(date(2026, 11, 20), {"fx": {"status": "ok", "rows": rows}}, KEY,
                              generated_at=datetime(2026, 11, 23, 22, 0, tzinfo=KST), is_trading_day=True)
    out, data = tmp_path / "out", tmp_path / "data"
    data.mkdir()
    CM.publish(CM.write_package(out, manifest, files, "t1"), out, data, manifest)
    src = S.DirSource(data)
    path = src.list_manifests()[0]
    r1 = S.apply_manifest(dev_db, src, path, KEY, today=date(2026, 11, 23))["sections"]["fx"]
    r2 = S.apply_manifest(dev_db, src, path, KEY, today=date(2026, 11, 23))["sections"]["fx"]
    assert (r1["inserted"], r1["existing_different"]) == (2, 1)
    assert (r2["inserted"], r2["existing_same"], r2["existing_different"]) == (0, 2, 1)
    got = dev_db.execute(text("SELECT price_date, close_price, change_pct FROM exchange_rate WHERE pair_code = :p ORDER BY price_date"),
                         {"p": PAIR}).all()
    assert [(str(d), float(c), float(x)) for d, c, x in got] == [("2026-11-18", 1333.01, -0.20), ("2026-11-19", 1330.40, -0.20),
                                                                 ("2026-11-20", 1334.79, 0.33)]
