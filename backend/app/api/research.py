from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.agent import ask_question

router = APIRouter(prefix="/api", tags=["research"])


class ResearchRequest(BaseModel):
    question: str


class Source(BaseModel):
    tool: str
    type: Literal["news", "disclosure"]
    title: str
    company_names: list[str]
    company_filter: str | None = None
    url: str | None = None


class ResearchResponse(BaseModel):
    answer: str
    used_tools: list[str]
    sources: list[Source] = []


@router.post("/research", response_model=ResearchResponse)
def research(request: ResearchRequest) -> ResearchResponse:
    result = ask_question(request.question)
    return ResearchResponse(**result)
