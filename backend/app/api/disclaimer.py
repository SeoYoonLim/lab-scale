from fastapi import APIRouter
from pydantic import BaseModel

from app.disclaimer import DISCLAIMER

router = APIRouter(prefix="/api", tags=["disclaimer"])


class DisclaimerResponse(BaseModel):
    text: str


@router.get("/disclaimer", response_model=DisclaimerResponse)
def get_disclaimer() -> DisclaimerResponse:
    """서비스 면책 문구(공개, 로그인 불필요). 화면 하단/약관 영역에 그대로 보여주면 된다."""
    return DisclaimerResponse(text=DISCLAIMER)
