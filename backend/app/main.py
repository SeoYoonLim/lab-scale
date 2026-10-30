import logging
import time
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
from app.api.backtest import router as backtest_router
from app.api.briefing import router as briefing_router
from app.api.companies import router as companies_router
from app.api.disclaimer import router as disclaimer_router
from app.api.discovery import router as discovery_router
from app.api.etf import router as etf_router
from app.api.exposure import router as exposure_router
from app.api.performance import router as performance_router
from app.api.portfolio import router as portfolio_router
from app.api.research import router as research_router
from app.api.status import router as status_router
from app.api.stocks import router as stocks_router
from app.api.watchlist import router as watchlist_router
from app.auth import get_jwt_secret
from app.config import check_jwt_secret_for_startup, cors_expose_headers, cors_origins

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # JWT_SECRET이 없거나 짧으면 여기서 RuntimeError로 기동 자체를 실패시킨다(토큰 발급 시점까지 미루지 않음).
    get_jwt_secret()
    # 길이는 충분해도 예시 문구·반복 문자면: 운영(APP_ENV=production)은 기동 거부, 개발은 경고 로그(app/config.py)
    check_jwt_secret_for_startup()
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
    # 예외 원문에는 SQL 파라미터(사용자 입력)가 섞일 수 있어 종류와 SQL 문장 앞부분만 남긴다
    logger.error("DB 오류: %s (%s)", type(exc).__name__, " ".join(str(getattr(exc, "statement", "") or "").split())[:200])
    return _error(503, "데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.")

SERVER_ERROR_DETAIL = "서버 오류가 발생했습니다."

# ---------- 요청 로그 ----------
# 메서드, 경로(쿼리스트링 제외), 상태 코드, 처리 시간(ms), user id(인증된 요청만)만 남긴다. 헤더·토큰·본문은 남기지 않는다.
access_logger = logging.getLogger("app.access")
SLOW_REQUEST_MS = 3000  # 이 이상이면 WARNING


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        ms = (time.perf_counter() - started) * 1000
        user_id = getattr(request.state, "user_id", None)
        level = logging.WARNING if ms >= SLOW_REQUEST_MS else logging.INFO
        access_logger.log(level, "%s %s %s %.0fms user=%s", request.method, request.url.path, status, ms,
                          user_id if user_id is not None else "-")


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # 처리되지 않은 예외: 응답에는 고정 문구만(스택트레이스·내부 경로·SQL 없음), 상세는 서버 로그에만 남긴다.
    logger.exception("처리되지 않은 오류: %s %s", request.method, request.url.path)
    return _error(500, SERVER_ERROR_DETAIL)


# 허용 origin은 CORS_ALLOW_ORIGINS(쉼표 구분, 기본 http://localhost:5173, http://127.0.0.1:5173, app/config.py).
# 인증은 Authorization 헤더(Bearer)라 쿠키·credentials를 쓰지 않는다(allow_credentials=False). '*'와 credentials를 함께 쓰지 않는다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=cors_expose_headers(),  # 브라우저가 X-Total-Count를 읽으려면 필요(CORS_EXPOSE_HEADERS)
)

app.include_router(auth_router)
app.include_router(disclaimer_router)
app.include_router(status_router)
app.include_router(research_router)
app.include_router(stocks_router)
app.include_router(companies_router)
app.include_router(discovery_router)
app.include_router(watchlist_router)
app.include_router(portfolio_router)
app.include_router(briefing_router)
app.include_router(performance_router)
app.include_router(exposure_router)
app.include_router(backtest_router)
app.include_router(etf_router)
