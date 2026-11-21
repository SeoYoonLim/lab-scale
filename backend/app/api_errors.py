"""API 오류 응답의 안정적인 오류 코드(2026-11-21, 최종 시연 전 품질 수정).

배경: 오류 응답은 {"detail": "한국어 문장"}뿐이라, 영어·일본어·중국어 화면도 서버의 한국어 문장을 그대로 보여 줬다(보유 수량 부족 등).
프런트가 문장을 부분 문자열로 비교하지 않고 언어별 문구를 고를 수 있게, 기존 detail은 그대로 두고 "code"를 함께 준다.

- CodedHTTPException: HTTPException과 같고 code만 더한다. app.main의 전용 핸들러가 {"detail": ..., "code": ...}로 응답한다.
  code가 없는 다른 HTTPException은 FastAPI 기본 처리 그대로다(응답 모양이 바뀌지 않는다).
- code에는 내부 예외 이름·SQL·스택을 넣지 않는다. 아래 상수만 쓴다(프런트 번역 키와 1:1).
- 주문 요청의 검증 오류(422)는 detail(검증 오류 배열)을 그대로 두고, 어느 필드가 틀렸는지로 code만 붙인다(order_validation_code).
"""

from __future__ import annotations

from fastapi import HTTPException

# ---------- 오류 코드(프런트 errors.code.* 번역 키와 같다) ----------
INSUFFICIENT_HOLDINGS = "INSUFFICIENT_HOLDINGS"  # 매도 수량 > 보유 수량
INSUFFICIENT_CASH = "INSUFFICIENT_CASH"  # 매수 금액 > 현금 잔고
INVALID_ORDER_QUANTITY = "INVALID_ORDER_QUANTITY"  # 수량이 1 미만·정수 아님·상한 초과(422)
UNSUPPORTED_ORDER_TYPE = "UNSUPPORTED_ORDER_TYPE"  # side가 buy/sell이 아님(422)
INVALID_ORDER_REQUEST = "INVALID_ORDER_REQUEST"  # 그 밖의 주문 요청 형식 오류(422)
STOCK_NOT_FOUND = "STOCK_NOT_FOUND"  # 종목을 찾지 못함(주문·관심종목·체결 조회)
PRICE_UNAVAILABLE = "PRICE_UNAVAILABLE"  # 체결 가격을 구하지 못함(503)
NOT_IN_WATCHLIST = "NOT_IN_WATCHLIST"  # 관심종목에 없는 종목 삭제
ALREADY_IN_WATCHLIST = "ALREADY_IN_WATCHLIST"  # 이미 등록된 관심종목 추가(409)
RESET_CONFIRM_REQUIRED = "RESET_CONFIRM_REQUIRED"  # 모의투자 초기화 확인 본문 없음
AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"  # 토큰 없음·만료·폐기·위조(401)
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"  # 로그인 아이디·비밀번호 불일치(401)

ALL_CODES = frozenset({
    INSUFFICIENT_HOLDINGS, INSUFFICIENT_CASH, INVALID_ORDER_QUANTITY, UNSUPPORTED_ORDER_TYPE, INVALID_ORDER_REQUEST,
    STOCK_NOT_FOUND, PRICE_UNAVAILABLE, NOT_IN_WATCHLIST, ALREADY_IN_WATCHLIST, RESET_CONFIRM_REQUIRED, AUTHENTICATION_REQUIRED, INVALID_CREDENTIALS,
})


class CodedHTTPException(HTTPException):
    """HTTPException + 안정적인 오류 코드. detail은 기존 한국어 문장 그대로다(하위 호환)."""

    def __init__(self, status_code: int, detail, code: str, headers: dict | None = None):
        if code not in ALL_CODES:
            raise ValueError(f"알 수 없는 오류 코드: {code}")
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def error_body(detail, code: str | None) -> dict:
    """응답 본문. code가 없으면 기존과 같은 {"detail": ...}."""
    return {"detail": detail, **({"code": code} if code else {})}


def order_validation_code(errors: list[dict]) -> str:
    """POST /api/portfolio/orders의 422 검증 오류에서 code를 고른다(필드 이름만 본다, 입력값은 보지 않는다)."""
    fields = {str(loc) for e in errors for loc in (e.get("loc") or ())}
    if "quantity" in fields:
        return INVALID_ORDER_QUANTITY
    if "side" in fields:
        return UNSUPPORTED_ORDER_TYPE
    return INVALID_ORDER_REQUEST
