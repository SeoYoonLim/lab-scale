"""클라우드 수집·원격 패키지·집 PC 동기화·종류별 지연 판정(2026-11-23). DB·외부 네트워크 없이 돈다."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app import data_freshness as DF
from app import market_data_sync as S
from app import market_package as P
from scripts import cloud_market_data as CM

KST = ZoneInfo("Asia/Seoul")
KEY = bytes(range(32))
NOW = datetime(2026, 11, 23, 21, 50, tzinfo=KST)


def _sections():
    return {
        "stocks": {"status": "ok", "rows": [{"date": "2026-11-20", "ticker": "005930", "name": "삼성전자", "asset_type": "stock",
                                              "open": 188100.0, "high": 189000.0, "low": 180000.0, "close": 183100.0,
                                              "volume": 17713847, "change_pct": -7.01}], "targets": 1, "succeeded": 1, "failed": 0},
        "indices": {"status": "partial", "rows": [{"date": "2026-11-20", "code": "KS11", "close": 4869.96, "change_pct": -5.21}],
                    "targets": 2, "succeeded": 1, "failed": 1, "failures": [{"code": "KQ11", "error": "x"}]},
        "news": {"status": "skipped", "rows": [], "note": "NAVER 없음"},
    }


def _package(tmp_path, key=KEY, sections=None):
    manifest, files = P.build(date(2026, 11, 23), sections or _sections(), key, generated_at=NOW, is_trading_day=True,
                              run={"id": "1"})
    d = CM.write_package(tmp_path / "out", manifest, files, "t1")
    return manifest, files, d


# ---------- 1·2·3. 패키지: 만들기·체크섬 검증·잘못된 체크섬 거부 ----------


def test_package_roundtrip_and_manifest_fields(tmp_path):
    manifest, files, d = _package(tmp_path)
    for k in ("schema_version", "package_version", "data_date", "is_trading_day", "generated_at_kst", "encryption", "sections", "complete"):
        assert k in manifest
    assert manifest["data_date"] == "2026-11-23" and manifest["is_trading_day"] is True and manifest["complete"] is False  # indices partial
    st = manifest["sections"]["stocks"]
    assert st["rows"] == 1 and st["succeeded"] == 1 and st["failed"] == 0 and len(st["sha256_plain"]) == 64 and len(st["sha256_file"]) == 64
    assert manifest["sections"]["news"] == {"status": "skipped", "note": "NAVER 없음", "failures": [], "rows": 0, "date_range": None}
    assert CM.verify_dir(d, KEY) == {"stocks": 1, "indices": 1}
    rows = P.read_section(manifest, "stocks", files[st["file"]], KEY)
    assert rows[0]["close"] == 183100.0 and rows[0]["date"] == "2026-11-20"


def test_tampered_file_wrong_key_and_schema_are_rejected(tmp_path):
    manifest, files, _ = _package(tmp_path)
    f = manifest["sections"]["stocks"]["file"]
    bad = bytearray(files[f])
    bad[-1] ^= 1
    with pytest.raises(P.PackageError, match="체크섬"):
        P.read_section(manifest, "stocks", bytes(bad), KEY)
    with pytest.raises(P.PackageError, match="키"):
        P.read_section(manifest, "stocks", files[f], bytes(32))
    m2 = {**manifest, "schema_version": 99}
    with pytest.raises(P.PackageError, match="스키마"):
        P.read_section(m2, "stocks", files[f], KEY)
    m3 = json.loads(json.dumps(manifest))
    m3["sections"]["stocks"]["rows"] = 5
    with pytest.raises(P.PackageError, match="행 수"):
        P.read_section(m3, "stocks", files[f], KEY)
    # 체크섬까지 맞춘 위조(인증 태그 불일치)도 거부
    m4 = json.loads(json.dumps(manifest))
    m4["sections"]["stocks"]["sha256_file"] = P.sha256(bytes(bad))
    with pytest.raises(P.PackageError, match="인증"):
        P.read_section(m4, "stocks", bytes(bad), KEY)


def test_encryption_hides_content_and_key_never_in_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "naver-secret-value-123")
    monkeypatch.setenv("DART_API_KEY", "dart-key-value-456")
    manifest, files, d = _package(tmp_path)
    blob = b"".join(files.values())
    assert "삼성전자".encode() not in blob and b"183100" not in blob
    text = (d / "manifest.json").read_text(encoding="utf-8")
    import base64
    for secret in (base64.b64encode(KEY).decode(), "naver-secret-value-123", "dart-key-value-456"):
        assert secret not in text and secret.encode() not in blob
    assert manifest["encryption"]["key_id"] == P.key_id(KEY) and len(manifest["encryption"]["key_id"]) == 8


def test_dart_key_is_masked_in_failure_messages():
    from app import collectors as C

    msg = C._safe_error(RuntimeError("GET https://opendart.fss.or.kr/api/list.json?crtfc_key=abcdef123&corp_code=1 failed"))
    assert "abcdef123" not in msg


def test_key_loading(tmp_path):
    k = P.new_key_text()
    assert P.parse_key(k) and P.load_key({"MARKET_DATA_KEY": k}) == P.parse_key(k)
    f = tmp_path / "k"
    f.write_text(k, encoding="ascii")
    assert P.load_key({"MARKET_DATA_KEY_FILE": str(f)}) == P.parse_key(k)
    assert P.load_key({"MARKET_DATA_KEY_FILE": str(tmp_path / "none")}) is None
    with pytest.raises(P.PackageError):
        P.parse_key("c2hvcnQ=")


# ---------- 1. 클라우드 수집(PC·DB 없이) ----------


def test_cloud_plan_skips_weekend_and_runs_on_trading_day():
    sun = CM.plan(datetime(2026, 11, 22, 18, 35, tzinfo=KST))
    assert sun["useful"] is False and sun["data_date"] == date(2026, 11, 20)
    mon_evening = CM.plan(datetime(2026, 11, 23, 18, 35, tzinfo=KST))
    assert mon_evening["useful"] and mon_evening["is_trading_day"] and mon_evening["data_date"] == date(2026, 11, 23)
    mon_morning = CM.plan(datetime(2026, 11, 24, 9, 40, tzinfo=KST))
    assert mon_morning["useful"] and mon_morning["data_date"] == date(2026, 11, 23)


def test_cloud_fx_drops_unfinished_bar(monkeypatch):
    from app import collectors as C

    idx = pd.to_datetime(["2026-11-19", "2026-11-20", "2026-11-23"])
    monkeypatch.setattr(C, "_read_fdr", lambda *a, **k: pd.DataFrame({"Close": [1330.4, 1334.79, 1346.38]}, index=idx))
    s = CM.collect_fx(NOW)
    assert s["status"] == "ok" and [r["date"] for r in s["rows"]] == ["2026-11-19", "2026-11-20"]
    assert "2026-11-23" in s["note"]


def test_cloud_news_skipped_without_keys_and_uses_fetch_when_given(monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    uni = [{"ticker": "005930", "name": "삼성전자", "asset_type": "stock"}]
    assert CM.collect_news(uni, NOW)["status"] == "skipped"
    item = {"title": "삼성전자 주가 하락", "originallink": "https://example.com/a", "link": "https://n.news.naver.com/a",
            "description": "요약", "pubDate": "Mon, 23 Nov 2026 10:00:00 +0900"}
    s = CM.collect_news(uni, NOW, fetch=lambda q, display, sort, start: [item], sleep=lambda x: None)
    assert s["status"] == "ok" and s["rows"][0]["ticker"] == "005930" and s["rows"][0]["date"] == "2026-11-23"
    assert set(s["rows"][0]) == {"date", "ticker", *CM.NEWS_FIELDS}  # 본문 전문 필드 없음


def test_cloud_universe_file_has_350_companies():
    u = CM.load_universe()
    assert len(u) == 350 and sum(x["asset_type"] == "stock" for x in u) == 300 and sum(x["asset_type"] == "etf" for x in u) == 50
    assert all(set(x) == {"ticker", "name", "asset_type"} for x in u)  # 시세·비밀값 없음


# ---------- 4·5·11. 동기화: 반복 시 중복 없음, 다시 켜도 최신 반영, 구역 실패는 그 구역만 ----------


class FakeDB:
    def rollback(self):
        pass

    def close(self):
        pass

    def add(self, x):
        pass

    def commit(self):
        pass


def _publish(tmp_path, sections=None, tag="t1"):
    manifest, files = P.build(date(2026, 11, 23), sections or _sections(), KEY, generated_at=NOW, is_trading_day=True)
    out = tmp_path / "out"
    d = CM.write_package(out, manifest, files, tag)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    CM.publish(d, out, data, manifest)
    return data


def test_sync_applies_new_packages_once_and_skips_on_restart(tmp_path, monkeypatch):
    data = _publish(tmp_path)
    calls = []
    monkeypatch.setattr(S, "apply_section", lambda db, name, rows, dry, today: calls.append((name, len(rows))) or S.SectionResult(rows=len(rows), inserted=len(rows)))
    monkeypatch.setattr(S, "_record", lambda *a: None)
    state = tmp_path / "state.json"
    r1 = S.sync(source=S.DirSource(data), key=KEY, state_path=state, session_factory=FakeDB, use_lock=False)
    assert r1["status"] == "ok" and sorted(calls) == [("indices", 1), ("stocks", 1)]
    # 프로세스 재시작(상태 파일만 남음) 뒤: 같은 패키지는 다시 넣지 않는다
    r2 = S.sync(source=S.DirSource(data), key=KEY, state_path=state, session_factory=FakeDB, use_lock=False)
    assert r2["status"] == "up_to_date" and len(calls) == 2
    # 새 패키지가 올라오면 그것만 적용
    _publish(tmp_path, {"fx": {"status": "ok", "rows": [{"date": "2026-11-20", "pair": "USD/KRW", "close": 1334.79, "change_pct": 0.33}]}}, tag="t2")
    r3 = S.sync(source=S.DirSource(data), key=KEY, state_path=state, session_factory=FakeDB, use_lock=False)
    assert r3["status"] == "ok" and len(r3["applied"]) == 1 and calls[-1] == ("fx", 1)


def test_sync_bad_checksum_fails_only_that_section(tmp_path, monkeypatch):
    data = _publish(tmp_path)
    man_path = next(data.glob("packages/*/*/manifest.json"))
    m = json.loads(man_path.read_text(encoding="utf-8"))
    (man_path.parent / m["sections"]["stocks"]["file"]).write_bytes(b"LSMD1" + bytes(80))
    applied = []
    monkeypatch.setattr(S, "apply_section", lambda db, name, rows, dry, today: applied.append(name) or S.SectionResult(rows=len(rows), inserted=len(rows)))
    monkeypatch.setattr(S, "_record", lambda *a: None)
    state = tmp_path / "state.json"
    r = S.sync(source=S.DirSource(data), key=KEY, state_path=state, session_factory=FakeDB, use_lock=False)
    secs = r["applied"][0]["sections"]
    assert r["status"] == "partial" and secs["stocks"]["status"] == "failed" and "체크섬" in secs["stocks"]["error"]
    assert applied == ["indices"]  # 다른 구역은 들어간다
    assert S.load_state(state).get("applied", {}) == {}  # 실패가 있는 패키지는 다음 동기화에서 다시 시도


def test_sync_disabled_under_pytest_and_without_key(monkeypatch, tmp_path):
    assert S.enabled({"MARKET_DATA_KEY": P.new_key_text()}) is False  # pytest 중에는 서버 훅이 돌지 않는다
    monkeypatch.setattr(P, "load_key", lambda env=None: None)
    r = S.sync(source=S.DirSource(tmp_path), key=None, state_path=tmp_path / "s.json", session_factory=FakeDB)
    assert r["status"] == "disabled" and not (tmp_path / "s.json").exists()


def test_briefing_hook_calls_sync_before_generate(monkeypatch):
    from fastapi.testclient import TestClient

    from app import daily_briefing_db as store
    from app.api import daily_briefing as api
    from app.api.deps import get_current_user
    from app.main import app

    order = []
    monkeypatch.setattr(api.market_data_sync, "sync_before_briefing", lambda wait_s=0: order.append("sync"))

    def fake_generate(*a, **k):
        order.append("generate")
        raise store.TooManyVersions()

    monkeypatch.setattr(store, "generate", fake_generate)
    monkeypatch.setattr(api, "SessionLocal", FakeDB)
    monkeypatch.setattr(api.rate_limit, "check", lambda *a, **k: None)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, owner_key="user:1", username="u", preferred_locale="ko-KR",
                                                                         preferred_timezone=None)
    try:
        r = TestClient(app).post("/api/briefings/daily/generate", json={"regenerate": True, "use_llm": False})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 409 and order == ["sync", "generate"]


# ---------- 7~10. 종류별 지연 판정 ----------


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def test_intraday_previous_close_is_latest_for_all_krx_kinds():
    now = at(2026, 11, 24, 11, 0)  # 화요일 장중
    assert DF.lag_days(DF.INDEX, "2026-11-23", now) == 0
    assert DF.lag_days(DF.ETF, "2026-11-23", now) == 0
    assert DF.lag_days(DF.STOCK, "2026-11-23", now) == 0  # 11-23 공식 종가는 11-24 10:00 뒤 기대
    assert DF.lag_days(DF.INDEX, "2026-11-20", now) == 1


def test_pre_market_morning_of_next_day_is_not_delayed():
    now = at(2026, 11, 24, 8, 30)
    assert DF.lag_days(DF.INDEX, "2026-11-23", now) == 0
    assert DF.lag_days(DF.STOCK, "2026-11-20", now) == 0  # 11-23 주식 종가는 아직 확정 시각 전 -> 11-20이 정상
    assert DF.status(DF.STOCK, "2026-11-20", now)["pending_date"] == "2026-11-23"


def test_delay_only_after_grace_period():
    d = "2026-11-20"  # 11-23(월) 데이터가 아직 없음
    assert DF.lag_days(DF.ETF, d, at(2026, 11, 23, 16, 0)) == 0 and DF.status(DF.ETF, d, at(2026, 11, 23, 16, 0))["pending_date"] == "2026-11-23"
    assert DF.lag_days(DF.ETF, d, at(2026, 11, 23, 20, 29)) == 0
    assert DF.lag_days(DF.ETF, d, at(2026, 11, 23, 20, 31)) == 1
    assert DF.lag_days(DF.INDEX, d, at(2026, 11, 23, 23, 59)) == 0
    assert DF.lag_days(DF.INDEX, d, at(2026, 11, 24, 8, 1)) == 1
    assert DF.lag_days(DF.STOCK, "2026-11-19", at(2026, 11, 23, 9, 59)) == 0  # 11-20 종가는 11-23 10:00에 기대
    assert DF.lag_days(DF.STOCK, "2026-11-19", at(2026, 11, 23, 10, 1)) == 1


def test_weekend_and_holiday_have_no_extra_delay():
    sat = at(2026, 11, 21, 12, 0)
    assert DF.lag_days(DF.INDEX, "2026-11-20", sat) == 0 and DF.lag_days(DF.ETF, "2026-11-20", sat) == 0
    assert DF.lag_days(DF.FX, "2026-11-20", sat) == 0
    sun = at(2026, 11, 22, 20, 0)
    assert DF.lag_days(DF.STOCK, "2026-11-19", sun) == 0  # 금요일 종가는 월요일 개장 뒤 확정
    assert DF.lag_days(DF.INDEX, "2026-11-20", sun) == 0
    # 연말 휴장(2026-12-31): 12-30 데이터가 1월 1일에도 최신
    assert DF.lag_days(DF.INDEX, "2026-12-30", at(2027, 1, 1, 12, 0)) == 0


def test_fx_schedule_is_independent_of_krx():
    # 환율 11-23 일봉은 런던 0시(09:00 KST) + 2시간 뒤에 기대
    assert DF.expected_date(DF.FX, at(2026, 11, 24, 10, 59)) == date(2026, 11, 20)
    assert DF.expected_date(DF.FX, at(2026, 11, 24, 11, 1)) == date(2026, 11, 23)
    # KRX 휴장일(연말)에도 환율은 평일이면 기대한다
    assert DF.expected_date(DF.FX, at(2027, 1, 1, 12, 0)) == date(2026, 12, 31)
    assert DF.expected_date(DF.INDEX, at(2027, 1, 1, 12, 0)) == date(2026, 12, 30)
    # 서머타임(런던 UTC+1)에는 한 시간 빠르다
    assert DF.due_at(DF.FX, date(2026, 7, 1)) == datetime(2026, 7, 2, 10, 0, tzinfo=KST)


def test_briefing_sources_use_their_own_schedules():
    from app import daily_briefing as dbf

    def series(last: date, n=8):
        out, d = [], last
        while len(out) < n:
            if d.weekday() < 5:
                out.append((d, 1000.0 + len(out)))
            d -= timedelta(days=1)
        return out

    f = dbf.Facts(now=at(2026, 11, 23, 21, 50), timezone="Asia/Seoul",
                  brief={"data_date": "2026-11-23", "holdings": [], "watchlist": [], "highlights": []},
                  index_series={"kospi": series(date(2026, 11, 23)), "kosdaq": series(date(2026, 11, 20)), "usd_krw": series(date(2026, 11, 20))})
    fr = dbf.freshness(f)
    assert fr["status"] == "latest" and fr["lag_business_days"] == 0
    # 코스닥 11-20(11-23 지수는 11-24 08:00까지 정상 대기), 환율 11-20(11-23 일봉은 11-24 11:00 기대): 지연 아님
    assert fr["sources"]["kosdaq"]["lag_business_days"] == 0 and fr["sources"]["kosdaq"]["pending_date"] == "2026-11-23"
    # 환율 11-23 일봉은 런던 자정(11-24 09:00 KST) 전이라 아직 끝나지도 않았다: 대기 안내도 없다
    assert fr["sources"]["usd_krw"]["lag_business_days"] == 0 and fr["sources"]["usd_krw"]["pending_date"] is None
    head, notices = dbf._fresh_notices(fr, "ko-KR")
    assert head == "시세는 최신 거래일(2026-11-23) 종가 기준입니다."
    assert any("지연 아님" in n and "코스닥" in n for n in notices) and not any("영업일 지연" in n for n in notices)
    m = dbf._market(f, "ko-KR")["indices"]
    assert [i["date"] for i in m] == ["2026-11-23", "2026-11-20", "2026-11-20"] and all(i["source"] and i["collected"] for i in m)


def test_news_failure_does_not_mark_prices_delayed():
    from app import daily_briefing as dbf

    s = [(date(2026, 11, 23) - timedelta(days=i), 100.0 + i) for i in range(8)]
    f = dbf.Facts(now=at(2026, 11, 24, 9, 30), timezone="Asia/Seoul", brief={"data_date": "2026-11-23", "holdings": [], "watchlist": []},
                  index_series={"kospi": s, "kosdaq": s, "usd_krw": []}, news_collected_at=None, market_news=[],
                  last_refresh={"ok": False, "failed_steps": ["news"], "finished_at": "2026-11-23T21:00:00+09:00"})
    fr = dbf.freshness(f)
    assert fr["status"] == "latest" and fr["lag_business_days"] == 0
    assert fr["sources"]["usd_krw"]["date"] is None and fr["sources"]["kospi"]["lag_business_days"] == 0


def test_stock_holding_waits_for_official_close_without_delay_warning():
    from app import daily_briefing as dbf

    s = [(date(2026, 11, 20) - timedelta(days=i), 100.0 + i) for i in range(8)]
    brief = {"data_date": "2026-11-23", "holdings": [{"ticker": "005930", "asset_type": "stock", "latest_date": "2026-11-20"},
                                                     {"ticker": "069500", "asset_type": "etf", "latest_date": "2026-11-23"}], "watchlist": []}
    f = dbf.Facts(now=at(2026, 11, 23, 21, 50), timezone="Asia/Seoul", brief=brief, index_series={"kospi": s, "kosdaq": s, "usd_krw": s})
    fr = dbf.freshness(f)
    assert fr["status"] == "latest" and fr["basis_kind"] == "stock" and fr["pending_date"] == "2026-11-23"
    head, notices = dbf._fresh_notices(fr, "ko-KR")
    assert any(n.startswith("시세 2026-11-23 데이터는 정상 제공 시각(11-24 10:00)") for n in notices)
    # 다음 날 10:00이 지나도 주식 11-23 종가가 없으면 그때부터 지연
    late = dbf.freshness(dbf.Facts(now=at(2026, 11, 24, 10, 30), timezone="Asia/Seoul", brief=brief,
                                   index_series={"kospi": s, "kosdaq": s, "usd_krw": s}))
    assert late["status"] == "delayed" and late["lag_business_days"] == 1 and late["base_date"] == "2026-11-20"
