from fastapi import APIRouter
from pydantic import BaseModel

from app.agent import ask_question

router = APIRouter(prefix="/api", tags=["research"])


class ResearchRequest(BaseModel):
    question: str


class ResearchResponse(BaseModel):
    answer: str
    used_tools: list[str]


@router.post("/research", response_model=ResearchResponse)
def research(request: ResearchRequest) -> ResearchResponse:
    result = ask_question(request.question)
    return ResearchResponse(**result)
