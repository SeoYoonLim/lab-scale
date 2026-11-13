"""저장 시세(stock_price) 각 열의 데이터 기준(2026-11-13, 50차 조사 결과의 코드 표현).

docs/MARKET_DATA_QUALITY_AUDIT_50.md 2절이 근거다. 요약:
- 종가(close_price): 공식 종가(KRX 정규장 15:30) x 원천 수정주가 비율(app/official_close.py, 47차 교정).
- 시가(open_price): KRX 정규장 시가(11-05~11-12 주식 판정 가능 1,765행 모두 분봉 정규장 시가와 같음).
- 고가·저가(high/low_price): KRX 하루 범위. 2026-09-14 이후 주식은 장 마감 뒤 KRX 애프터마켓(16:00~20:00) 체결을 포함한다
  (같은 1,765행 중 고가 167행·저가 183행이 애프터마켓 체결, scripts/audit_session_ohlcv.py).
- 거래량(volume): KRX 하루 전체(정규장 + 장후 종가매매 + 애프터마켓 + 분봉에 없는 체결). NXT 체결은 포함하지 않는다.
  원천이 수정주가 비율의 역수로 조정한다(분배락·기업 이벤트 이전 행은 실제 거래량 / 가격 비율).
- ETF·ETN은 애프터마켓 대상이 아니다.
"""
from __future__ import annotations

from datetime import date

from app.official_close import AFTER_MARKET_START

# 답에 붙는 문장이라 숫자(시각)를 넣지 않는다: 숫자 검증기는 도구 결과에 없는 숫자를 의심한다
OHLC_AFTER_MARKET_NOTE = "고가·저가는 장 마감 뒤 KRX 애프터마켓 체결을 포함한 하루 범위이고, 시가와 종가는 정규장 값입니다."
VOLUME_BASIS_NOTE = "거래량은 정규장과 장 마감 뒤 KRX 애프터마켓 체결을 합친 KRX 하루 거래량입니다(NXT 체결 제외)."


def _as_date(d) -> date | None:
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d)[:10])
    except ValueError:
        return None


def after_market_included(asset_type: str | None, day) -> bool:
    """그날 저장 행의 고가·저가·거래량에 애프터마켓 체결이 들어 있을 수 있는가(주식, 2026-09-14 이후)."""
    d = _as_date(day)
    return asset_type == "stock" and d is not None and d >= AFTER_MARKET_START
