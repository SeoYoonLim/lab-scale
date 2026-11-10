import logging
import time
import traceback
from contextlib import asynccontextmanager

import ollama
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError

from app import rate_limit, security
from app.api.auth import router as auth_router
from app.api.backtest import router as backtest_router
from app.api.briefing import router as briefing_router
from app.api.daily_briefing import router as daily_briefing_router
from app.api.companies import router as companies_router
from app.api.deps import client_ip
from app.api.disclaimer import router as disclaimer_router
from app.api.discovery import router as discovery_router
from app.api.etf import router as etf_router
from app.api.history import router as history_router
from app.api.market import router as market_router
from app.api.oauth import router as oauth_router
from app.api.exposure import router as exposure_router
from app.api.performance import router as performance_router
from app.api.portfolio import router as portfolio_router
from app.api.research import router as research_router
from app.api.status import router as status_router
from app.api.stocks import router as stocks_router
from app.api.watchlist import router as watchlist_router
from app.auth import get_jwt_secret
from app.config import check_jwt_secret_for_startup, cors_expose_headers, cors_origins
from app.warmup import start_warmup

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # JWT_SECRET이 없거나 짧으면 여기서 RuntimeError로 기동 자체를 실패시킨다(토큰 발급 시점까지 미루지 않음).
    get_jwt_secret()
    # 길이는 충분해도 예시 문구·반복 문자면: 운영(APP_ENV=production)은 기동 거부, 개발은 경고 로그(app/config.py)
    check_jwt_secret_for_startup()
    # 채팅·임베딩 모델을 미리 올린다(백그라운드, 실패 무시, 테스트·CI에서는 돌지 않음)
    start_warmup()
    yield


# 운영(APP_ENV=production)에서는 API 문서(/docs, /redoc, /openapi.json)를 끈다. 개발 기본 동작은 그대로(app/security.py).
_DOCS = security.docs_enabled()
app = FastAPI(title="AI Investment Research Agent", lifespan=lifespan,
              docs_url="/docs" if _DOCS else None, redoc_url="/redoc" if _DOCS else None,
              openapi_url="/openapi.json" if _DOCS else None)
# uvicorn 접근 로그에서 쿼리 문자열(소셜 로그인 콜백의 code·state 등)을 지운다
security.install_access_log_filter()


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


@app.exception_handler(security.BodyTooLarge)
async def body_too_large(request: Request, exc: security.BodyTooLarge):
    return _error(413, security.BODY_TOO_LARGE)


@app.middleware("http")
async def security_controls(request: Request, call_next):
    # 공개 GET 거친 레이트 리밋(IP당) → 보안 헤더·no-store(app/security.py)
    wait = security.public_get_limited(request.method, request.url.path, client_ip(request))
    if wait is not None:
        exc = rate_limit.too_many(wait)
        response = JSONResponse(status_code=429, content={"detail": exc.detail}, headers=exc.headers)
    else:
        response = await call_next(request)
    security.apply_headers(response.headers, request.url.path, "authorization" in request.headers)
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # 처리되지 않은 예외: 응답에는 고정 문구만(스택트레이스·내부 경로·SQL 없음), 상세는 서버 로그에만 남긴다.
    # 예외 메시지에는 SQL 파라미터(이메일 등 사용자 입력)가 섞일 수 있어 종류와 스택(코드 위치)만 남긴다
    logger.error("처리되지 않은 오류: %s %s %s | %s", request.method, request.url.path, type(exc).__name__,
                 "".join(traceback.format_tb(exc.__traceback__)))
    return _error(500, SERVER_ERROR_DETAIL)


# 요청 본문 크기 제한(413). CORS보다 안쪽에 둬서 413 응답에도 CORS 헤더가 붙게 한다(브라우저가 읽을 수 있게).
app.add_middleware(security.BodySizeLimitMiddleware)

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
app.include_router(oauth_router)
app.include_router(disclaimer_router)
app.include_router(status_router)
app.include_router(research_router)
app.include_router(stocks_router)
app.include_router(companies_router)
app.include_router(discovery_router)
app.include_router(watchlist_router)
app.include_router(portfolio_router)
app.include_router(briefing_router)
app.include_router(daily_briefing_router)
app.include_router(performance_router)
app.include_router(exposure_router)
app.include_router(backtest_router)
app.include_router(history_router)
app.include_router(etf_router)
app.include_router(market_router)


# ---------- OpenAPI 오류 응답 선언(2026-11-08, 37차) ----------
# 동작은 바꾸지 않는다. 라우트 선언에 200·422만 있어 OpenAPI로 타입을 만드는 프런트가 401·404·429를 몰랐다.
# 실제 코드에서 사실을 읽어 붙인다: 인증 의존성(get_current_user)이 있으면 401, 경로 파라미터가 있으면 404(남의 자원도 404),
# GET /api/*는 IP당 공개 GET 한도(app.security.public_get_limited)로 429, 엔드포인트와 그 안에서 부르는 같은 모듈 도우미 함수
# (한 단계)의 소스에 status_code=4xx가 있으면 그 코드, rate_limit.check가 있으면 429, 본문이 있으면 413, 모두 503(DB·Ollama 장애).
# 422 외의 오류 본문은 모두 {"detail": "문장"}이다. tests/test_api_contract_round37.py가 실제 응답 코드와 대조한다.
ERROR_RESPONSE_SCHEMA = {"title": "ErrorResponse", "type": "object", "required": ["detail"],
                         "properties": {"detail": {"type": "string", "title": "Detail"}}}
_ERROR_DESCRIPTIONS = {
    "400": "요청은 형식상 맞지만 처리할 수 없음(예: 잔고·보유 수량 부족, 잘못된 토큰·링크). detail 문장을 그대로 보여 준다",
    "401": "인증 필요: 토큰 없음·형식 오류·만료·로그아웃·비밀번호 변경으로 폐기됨",
    "403": "권한 없음",
    "404": "없는 자원(다른 사용자의 자원도 404)",
    "409": "현재 상태와 충돌(예: 이미 있는 관심종목, 하루 브리핑 버전 한도)",
    "413": "요청 본문이 너무 큼",
    "429": "요청이 너무 많음(Retry-After 헤더: 다시 시도할 수 있는 초)",
    "503": "DB 또는 AI 모델 서버(Ollama) 일시 장애",
}
NOT_FOUND_WITHOUT_PATH_PARAM = {("GET", "/api/briefings/daily/current")}


def _uses_dependency(dependant, target) -> bool:
    return any(d.call is target or _uses_dependency(d, target) for d in dependant.dependencies)


def _endpoint_source(endpoint) -> str:
    """엔드포인트 함수 소스 + 그 안에서 부르는 같은 모듈의 도우미 함수(한 단계) 소스. 소스를 못 읽으면 빈 문자열."""
    import inspect
    import re
    import sys as _sys

    try:
        src = inspect.getsource(endpoint)
    except (OSError, TypeError):
        return ""
    module = _sys.modules.get(endpoint.__module__)
    extra = []
    for name in set(re.findall(r"\b(_[A-Za-z0-9_]+)\(", src)):
        fn = getattr(module, name, None)
        if callable(fn):
            try:
                extra.append(inspect.getsource(fn))
            except (OSError, TypeError):
                pass
    return "\n".join([src, *extra])


def _declared_error_codes(endpoint) -> set[str]:
    """소스에서 실제로 내는 4xx(status_code=4xx)와 레이트 리밋(rate_limit.check → 429)을 찾는다."""
    import re

    src = _endpoint_source(endpoint)
    codes = {c for c in re.findall(r"status_code=(4\d\d)", src) if c in _ERROR_DESCRIPTIONS}
    if "rate_limit.check(" in src:
        codes.add("429")
    return codes


def _api_routes(routes):
    """앱의 라우트를 펼친다. FastAPI 0.141은 include_router한 라우터를 _IncludedRouter(original_router)로 감싼다."""
    for r in routes:
        inner = getattr(r, "original_router", None) or getattr(r, "router", None)
        if getattr(r, "dependant", None) is not None:
            yield r
        elif inner is not None and hasattr(inner, "routes"):
            yield from _api_routes(inner.routes)


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    from app.api.deps import get_current_user

    schema = FastAPI.openapi(app)
    schema.setdefault("components", {}).setdefault("schemas", {})["ErrorResponse"] = ERROR_RESPONSE_SCHEMA
    content = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}
    for route in _api_routes(app.routes):
        dependant, methods = getattr(route, "dependant", None), getattr(route, "methods", None)
        ops = schema["paths"].get(getattr(route, "path", ""), {})
        if dependant is None or not methods:
            continue
        auth = _uses_dependency(dependant, get_current_user)
        raised = _declared_error_codes(route.endpoint)
        for method in methods:
            op = ops.get(method.lower())
            if op is None:
                continue
            codes = {"503"} | raised
            if auth:
                codes.add("401")
            if "{" in route.path or (method, route.path) in NOT_FOUND_WITHOUT_PATH_PARAM:
                codes.add("404")
            if method == "GET" and route.path.startswith("/api/"):
                codes.add("429")
            if "requestBody" in op:
                codes.add("413")
            responses = op.setdefault("responses", {})
            for code in sorted(codes):
                responses.setdefault(code, {"description": _ERROR_DESCRIPTIONS[code], "content": content})
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
