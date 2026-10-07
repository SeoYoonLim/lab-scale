import logging
from contextlib import asynccontextmanager

import ollama
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError

from app.api.auth import router as auth_router
from app.api.companies import router as companies_router
from app.api.disclaimer import router as disclaimer_router
from app.api.discovery import router as discovery_router
from app.api.portfolio import router as portfolio_router
from app.api.research import router as research_router
from app.api.stocks import router as stocks_router
from app.api.watchlist import router as watchlist_router
from app.auth import get_jwt_secret

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # JWT_SECRET이 없거나 짧으면 여기서 RuntimeError로 기동 자체를 실패시킨다(토큰 발급 시점까지 미루지 않음).
    get_jwt_secret()
    yield


app = FastAPI(title="AI Investment Research Agent", lifespan=lifespan)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # 기본 422 응답은 오류마다 입력값(`input`)을 그대로 되돌려준다. /api/auth에서는 그게 평문 비밀번호일 수 있어서 뺀다.
    if request.url.path.startswith("/api/auth"):
        errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})
    return await request_validation_exception_handler(request, exc)


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

app.include_router(auth_router)
app.include_router(disclaimer_router)
app.include_router(research_router)
app.include_router(stocks_router)
app.include_router(companies_router)
app.include_router(discovery_router)
app.include_router(watchlist_router)
app.include_router(portfolio_router)
