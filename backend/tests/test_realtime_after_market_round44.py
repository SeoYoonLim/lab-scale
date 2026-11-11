"""2026-11-11(44차): 애프터마켓 가격을 "실시간 시세"로 내보내지 않는다.

애프터마켓(NXT 15:40~, KRX 16:00~20:00) 중에도 네이버 폴링 응답은 marketStatus="OPEN"이다. 2026-11-11 16:34 실제 응답:
marketSessionType="afterMarket", overMarketPriceInfo={"tradingSessionType": "AFTER_MARKET", "overMarketStatus": "OPEN", ...},
closePrice=205,500(애프터마켓 체결가), 전일 대비 +6,600(정규장 기준가 198,900 대비). 같은 화면의 차트·홈(저장 종가 206,000원,
+4.41%)과 기사(+3.6%)가 서로 달랐다. 모듈 설계("장 마감 후에는 실시간이 아니다 -> DB 폴백")를 애프터마켓에도 지킨다.
"""
import app.realtime_price as rp
from app.realtime_price import _fetch_from_naver
from tests.test_realtime_price import TICKER, FakeResponse, naver_payload


def _get(payload):
    return lambda *a, **k: FakeResponse(payload)


def test_after_market_session_is_not_realtime(monkeypatch):
    monkeypatch.setattr(rp.requests, "get", _get(naver_payload(marketSessionType="afterMarket")))
    assert _fetch_from_naver(TICKER) is None


def test_open_over_market_info_is_not_realtime(monkeypatch):
    over = {"tradingSessionType": "AFTER_MARKET", "overMarketStatus": "OPEN", "overPrice": "205,500"}
    monkeypatch.setattr(rp.requests, "get", _get(naver_payload(overMarketPriceInfo=over)))
    assert _fetch_from_naver(TICKER) is None


def test_closed_over_market_info_does_not_block_regular_session(monkeypatch):
    # 정규장 중 애프터마켓 정보가 닫힌 상태로 오거나(null 포함) 세션 필드가 없으면 기존대로 실시간이다
    for over in (None, {"tradingSessionType": "AFTER_MARKET", "overMarketStatus": "CLOSE"}):
        monkeypatch.setattr(rp.requests, "get", _get(naver_payload(overMarketPriceInfo=over)))
        data = _fetch_from_naver(TICKER)
        assert data is not None and data["is_realtime"] is True and data["current_price"] == 268000.0
