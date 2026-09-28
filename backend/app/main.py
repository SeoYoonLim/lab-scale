import logging

import ollama
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError

from app.api.research import router as research_router

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Investment Research Agent")


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message})


# 외부 의존성(Ollama, DB) 장애는 500 대신 재시도 가능한 5xx와 의미 있는 메시지로 돌려준다.
@app.exception_handler(ConnectionError)
async def ollama_unreachable(request: Request, exc: ConnectionError):
    logger.error("Ollama 연결 실패: %s", exc)
    return _error(503, "AI 모델 서버(Ollama)에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.")


@app.exception_handler(ollama.ResponseError)
async def ollama_error(request: Request, exc: ollama.ResponseError):
    logger.error("Ollama 응답 오류: %s", exc)
    return _error(502, "AI 모델 서버가 오류를 반환했습니다. 잠시 후 다시 시도해주세요.")


@app.exception_handler(OperationalError)
@app.exception_handler(DBAPIError)
async def db_unavailable(request: Request, exc: DBAPIError):
    logger.error("DB 오류: %s", exc)
    return _error(503, "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.")

# 로컬 프론트 개발 서버(Vite 5173, CRA/Next 3000) 허용. 실제 프론트 포트가 확정되면 조정한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(research_router)
