"""39차(2026-11-09): 뉴스·공시 도구가 실제 DB 행(timestamptz)을 KST 문자열로 내보내는지(개발 DB·CI DB 모두, 가짜 종목만).

37차 fake 픽스처의 가짜 종목에 뉴스·공시를 직접 넣고 끝나면 지운다(뉴스는 company 삭제 시 SET NULL이라 직접 지운다).
수집 데이터가 필요 없어 needs_collected_data 마커를 달지 않는다.
"""

import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models import Disclosure, News
from app.tools.disclosure_tool import disclosure_tool
from app.tools.news_tool import news_tool
from tests.test_api_contract_round37_devdb import fake  # noqa: F401  (픽스처)

pytestmark = pytest.mark.integration


@pytest.fixture
def news_rows(dev_db):
    made = []
    yield made
    dev_db.rollback()
    for nid in made:
        dev_db.execute(text("DELETE FROM news WHERE id = :i"), {"i": nid})
    dev_db.commit()


def test_news_and_disclosure_tools_emit_kst_strings(fake, dev_db, news_rows):
    co = fake(n=0)
    early = datetime(2026, 10, 27, 23, 30, tzinfo=timezone.utc)  # KST 2026-10-28 08:30
    n = News(company_id=co.id, title=f"{co.name} 신제품 발표", content="본문", source="테스트", url=f"https://example.invalid/{secrets.token_hex(6)}",
             published_at=early, is_relevant=True)
    midnight_kst = datetime.fromisoformat("2026-10-28T00:00:00+09:00")  # 공시 저장 방식(DB에는 UTC 10-27 15:00)
    d = Disclosure(company_id=co.id, title="분기보고서", disclosure_type="분기보고서", source_url=f"https://example.invalid/d/{secrets.token_hex(6)}",
                   disclosed_at=midnight_kst)
    dev_db.add_all([n, d])
    dev_db.commit()
    news_rows.append(n.id)

    out = news_tool(co.name)
    assert out["found"] is True
    pub = out["news"][0]["published_at"]
    assert pub == "2026-10-28T08:30:00+09:00" and datetime.fromisoformat(pub) == early  # 같은 순간, 앞 10자가 한국 날짜

    out = disclosure_tool(co.name)
    assert out["found"] is True
    disc = out["disclosures"][0]["disclosed_at"]
    assert disc == "2026-10-28T00:00:00+09:00"  # 39차 이전: "2026-10-27T15:00:00+00:00"(하루 이른 날짜)


# ---------- 격리 재처리와 데이터 갱신의 동시 실행 ----------


def test_reprocess_apply_refuses_while_refresh_lock_is_held(dev_db, capsys):
    """reprocess --apply는 갱신 락을 잡는다: 갱신(또는 다른 재처리)이 실행 중이면 아무것도 넣지 않고 2, 끝나면 정상 재처리,
    다시 실행하면 건너뛴다(중복 적용 없음)."""
    from app import quarantine as Q, refresh_runs
    from app.db.session import SessionLocal
    from app.ingest_validation import Checked, Verdict
    from app.models import Company, DataIngestionQuarantine, StockPrice
    from scripts import quarantine as cli

    if dev_db.execute(text("SELECT to_regclass('data_ingestion_quarantine')")).scalar() is None:
        pytest.skip("격리 테이블 없음")
    ticker = ("Q" + secrets.token_hex(3)[:5]).upper()
    co = Company(ticker=ticker, name=f"captest_r39 {ticker}", asset_type="stock")
    dev_db.add(co)
    dev_db.commit()
    try:
        v = Verdict()
        v.reject("close_nonpositive", "합성")
        day = "2026-11-03"
        rec = {"price_date": day, "close_price": 100, "open_price": 100, "high_price": 100, "low_price": 100, "volume": 1}
        Q.record("stock_price", [Checked(rec, f"stock_price:{ticker}:{day}", v)], {"ticker": ticker})
        dev_db.expire_all()
        item = dev_db.query(DataIngestionQuarantine).filter(DataIngestionQuarantine.source_key == f"stock_price:{ticker}:{day}").one()
        item.resolution_status = "revalidated"  # 재검증 통과 상태로(값 100은 정상)
        dev_db.commit()

        try:
            held = refresh_runs.acquire_lock()
        except refresh_runs.LockBusy:
            pytest.skip("실제 데이터 갱신이 실행 중")
        try:
            assert cli.main(["reprocess", str(item.id), "--apply"], session_factory=SessionLocal) == 2
            assert "실행 중" in capsys.readouterr().out
            dev_db.expire_all()
            assert dev_db.get(DataIngestionQuarantine, item.id).resolution_status == "revalidated"
            assert dev_db.query(StockPrice).filter(StockPrice.company_id == co.id).count() == 0
            # --apply 없는 계획 보기는 락 없이 된다
            assert cli.main(["reprocess", str(item.id)], session_factory=SessionLocal) == 0
            assert "would_insert" in capsys.readouterr().out
        finally:
            held.release()

        assert cli.main(["reprocess", str(item.id), "--apply"], session_factory=SessionLocal) == 0
        assert "reprocessed" in capsys.readouterr().out
        assert cli.main(["reprocess", str(item.id), "--apply"], session_factory=SessionLocal) == 0  # 두 번째는 건너뜀
        assert "skipped:reprocessed" in capsys.readouterr().out
        dev_db.expire_all()
        assert dev_db.query(StockPrice).filter(StockPrice.company_id == co.id).count() == 1
        assert not refresh_runs.lock_is_held_elsewhere(dev_db)  # 재처리가 끝나면 락을 놓는다
    finally:
        dev_db.rollback()
        dev_db.execute(text("DELETE FROM data_ingestion_quarantine WHERE source_key LIKE :k"), {"k": f"%:{ticker}:%"})
        dev_db.execute(text("DELETE FROM company WHERE id = :i"), {"i": co.id})
        dev_db.commit()
