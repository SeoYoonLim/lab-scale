from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.disclaimer import DISCLAIMER
from app.i18n import MAX_LOCALE_LEN, disclaimer, is_default, resolve_locale

router = APIRouter(prefix="/api", tags=["disclaimer"])


class DisclaimerResponse(BaseModel):
    text: str


@router.get("/disclaimer", response_model=DisclaimerResponse)
def get_disclaimer(
    locale: str | None = Query(None, max_length=MAX_LOCALE_LEN, description="ko-KR(기본), en-US, ja-JP, zh-CN. 지원하지 않는 값이면 ko-KR"),
) -> DisclaimerResponse:
    """서비스 면책 문구(공개, 로그인 불필요). 화면 하단/약관 영역에 그대로 보여주면 된다. 공개 API라 계정 설정은 보지 않는다."""
    locale = resolve_locale(locale)
    return DisclaimerResponse(text=DISCLAIMER if is_default(locale) else disclaimer(locale))
