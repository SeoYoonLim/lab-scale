"""40차(2026-11-09): 수집 중복 방지·정정 공시 보존·수집 CLI 락·주문 asset_type 통합 테스트(개발 DB·CI DB, 가짜 종목만).

외부 API(네이버 뉴스·DART)는 부르지 않는다: collectors.fetch_naver_news·fetch_dart_disclosures·get_dart_corp_code를 가짜로 바꾼다.
만든 뉴스(company 삭제 시 SET NULL)·격리 기록은 직접, 공시·시세·체결은 company 삭제(CASCADE)로 지운다.
"""

import secrets
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import collectors
from app.briefing import KST
from app.main import app
from app.models import Disclosure, News
from tests.test_api_contract_round37_devdb import fake, get  # noqa: F401  (fake는 픽스처)

pytestmark = pytest.mark.integration
client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def cleanup(dev_db):
    """테스트가 만든 뉴스(종목 id)와 격리 기록(키 일부)을 지운다."""
    state = {"companies": [], "keys": []}
    yield state
    dev_db.rollback()
    for cid in state["companies"]:
        dev_db.execute(text("DELETE FROM news WHERE company_id = :c"), {"c": cid})
    for k in state["keys"]:
        dev_db.execute(text("DELETE FROM data_ingestion_quarantine WHERE source_key LIKE :k"), {"k": f"%{k}%"})
    dev_db.commit()


def _naver_item(url, title, published):
    return {"title": title, "link": url, "originallink": url, "description": "본문", "pubDate": published.strftime("%a, %d %b %Y %H:%M:%S %z")}


def test_news_collection_never_duplicates_and_keeps_per_company_rows(fake, dev_db, cleanup, monkeypatch):
    a, b = fake(n=0), fake(n=0)
    cleanup["companies"] += [a.id, b.id]
    cleanup["keys"] += [a.ticker, b.ticker]
    pub = datetime.now(KST) - timedelta(hours=3)
    shared = f"https://www.hankyung.com/article/{secrets.token_hex(6)}"
    other = f"https://www.hankyung.com/article/{secrets.token_hex(6)}"
    items = {a.name: [_naver_item(shared, f"{a.name} 실적 발표", pub), _naver_item(shared, f"{a.name} 실적 발표", pub),  # 같은 응답 안 중복
                      _naver_item(other, f"{a.name} 신제품", pub)],
             b.name: [_naver_item(shared, f"{b.name}·{a.name} 공동 개발", pub)]}  # 같은 기사가 다른 종목에도
    monkeypatch.setattr(collectors, "fetch_naver_news", lambda query, display=10, sort="date": next(v for k, v in items.items() if k in query))

    def rows(cid):
        dev_db.expire_all()
        return sorted(u for (u,) in dev_db.query(News.url).filter(News.company_id == cid))

    r = collectors.fetch_and_save_news(a.name)
    assert r.ok and r.saved == 2 and r.quarantined == 1  # 같은 url 두 번째는 격리(duplicate_same)
    assert rows(a.id) == sorted([shared, other])
    r = collectors.fetch_and_save_news(a.name)  # 다시 수집해도 새로 넣지 않는다
    assert r.ok and r.saved == 0 and rows(a.id) == sorted([shared, other])
    r = collectors.fetch_and_save_news(b.name)  # 같은 url이라도 다른 종목에는 저장(종목별 키 company_id + url)
    assert r.ok and r.saved == 1 and rows(b.id) == [shared]


def test_disclosure_correction_is_kept_as_separate_row(fake, dev_db, cleanup, monkeypatch):
    co = fake(n=0)
    cleanup["keys"].append(co.ticker)
    day = (datetime.now(KST) - timedelta(days=3)).strftime("%Y%m%d")
    base = int(secrets.token_hex(3), 16) % 900000 + 100000
    orig, corr = f"{day}{base:06d}", f"{day}{base + 1:06d}"
    cleanup["keys"] += [orig, corr]
    listed = [
        {"rcept_no": orig, "rcept_dt": day, "report_nm": "단일판매ㆍ공급계약체결", "corp_name": co.name, "flr_nm": co.name},
        {"rcept_no": corr, "rcept_dt": day, "report_nm": "[기재정정]단일판매ㆍ공급계약체결", "corp_name": co.name, "flr_nm": co.name},
        {"rcept_no": orig, "rcept_dt": day, "report_nm": "단일판매ㆍ공급계약체결", "corp_name": co.name, "flr_nm": co.name},  # 같은 접수번호
    ]
    monkeypatch.setattr(collectors, "get_dart_corp_code", lambda ticker: "00000000")
    monkeypatch.setattr(collectors, "fetch_dart_disclosures", lambda corp_code, days=90: [dict(x) for x in listed])

    def saved():
        dev_db.expire_all()
        return sorted((d.title, d.source_url[-14:]) for d in dev_db.query(Disclosure).filter(Disclosure.company_id == co.id))

    r = collectors.fetch_and_save_disclosures(co.name)
    assert r.ok and r.saved == 2
    # 정정 공시는 접수번호가 달라 원본을 덮어쓰거나 합치지 않고 별도 행이다. 같은 접수번호는 한 번만
    assert saved() == sorted([("단일판매ㆍ공급계약체결", orig), ("[기재정정]단일판매ㆍ공급계약체결", corr)])
    r = collectors.fetch_and_save_disclosures(co.name)
    assert r.ok and r.saved == 0 and len(saved()) == 2


def test_collect_universe_cli_takes_refresh_lock(dev_db, monkeypatch, capsys):
    """직접 실행한 수집(scripts.collect_universe)은 갱신 락을 잡는다: 다른 실행 중이면 수집하지 않고 2, --dry-run은 락 없이 실행."""
    import sys

    from app import refresh_runs
    from scripts import collect_universe as cu

    calls = []
    monkeypatch.setattr(cu, "main", lambda: calls.append(refresh_runs.lock_is_held_elsewhere(dev_db)))
    try:
        held = refresh_runs.acquire_lock()
    except refresh_runs.LockBusy:
        pytest.skip("실제 데이터 갱신이 실행 중")
    try:
        monkeypatch.setattr(sys, "argv", ["collect_universe", "--limit", "1"])
        with pytest.raises(SystemExit) as e:
            cu.run_cli()
        assert e.value.code == cu.LOCK_BUSY_EXIT_CODE and calls == []  # main(외부 수집)을 부르지 않았다
        assert "실행 중" in capsys.readouterr().out
        monkeypatch.setattr(sys, "argv", ["collect_universe", "--dry-run"])
        cu.run_cli()
        assert len(calls) == 1  # dry-run은 락 없이 실행
    finally:
        held.release()
    monkeypatch.setattr(sys, "argv", ["collect_universe", "--limit", "1"])
    cu.run_cli()
    assert calls[-1] is True  # main이 도는 동안 락이 잡혀 있다(다른 세션에서 보임)
    assert refresh_runs.lock_is_held_elsewhere(dev_db) is False  # 끝나면 놓는다


def test_order_response_asset_type_matches_master(signup, fake):
    u = signup()
    stock, etf = fake(), fake(asset_type="etf")
    for co, expected in ((stock, "stock"), (etf, "etf")):
        r = client.post("/api/portfolio/orders", headers=u["headers"], json={"ticker": co.ticker, "side": "buy", "quantity": 1})
        assert r.status_code == 200, r.text
        assert r.json()["asset_type"] == expected and r.json()["ticker"] == co.ticker
    _, trades = get("/api/portfolio/trades", u)
    assert {(t["ticker"], t["asset_type"]) for t in trades["items"]} == {(stock.ticker, "stock"), (etf.ticker, "etf")}
    _, port = get("/api/portfolio", u)
    assert {(h["ticker"], h["asset_type"]) for h in port["holdings"]} == {(stock.ticker, "stock"), (etf.ticker, "etf")}
