"""HTTP 보안 통제(2026-10-30 보안 점검): 보안 헤더, 개인 데이터 응답 no-store, 요청 본문 크기 제한, 공개 GET 거친 레이트 리밋,
접근 로그의 쿼리 문자열 제거, 운영에서 API 문서 끄기. 미들웨어 연결은 app/main.py가 한다.

환경변수(정수·불리언, 요청마다 읽는다)
- MAX_REQUEST_BODY_BYTES(기본 1,048,576 = 1MB): 넘는 본문은 413. Content-Length로 먼저 거르고, 길이를 밝히지 않은(chunked) 본문은 받으면서 센다.
- RATE_LIMIT_PUBLIC_GET_PER_MIN(기본 600): 모든 GET /api/* 요청을 IP당 분당 센다(공개 시세·검색·헬스 포함). Authorization 헤더를 붙여도
  공개 라우트는 헤더를 보지 않으므로 헤더 유무와 상관없이 센다(가짜 헤더로 우회하지 못하게). RATE_LIMIT_ENABLED=0이면 끈다.
- SECURITY_HSTS(기본 꺼짐): 1이면 Strict-Transport-Security를 붙인다. HTTPS로만 서비스할 때만 켠다(HTTP에서 켜면 의미가 없고 로컬 개발을 방해한다).
- APP_ENV=production이면 /docs, /redoc, /openapi.json을 끈다(개발 기본 동작은 그대로).
"""

import logging
import os
from urllib.parse import urlsplit

from app import rate_limit
from app.config import is_production

DEFAULT_MAX_BODY = 1_048_576
DEFAULT_PUBLIC_GET_PER_MIN = 600
BODY_TOO_LARGE = "요청 본문이 너무 큽니다."
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
HSTS_VALUE = "max-age=31536000; includeSubDomains"

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": PERMISSIONS_POLICY,
}


def max_body_bytes() -> int:
    try:
        return max(1024, int(os.getenv("MAX_REQUEST_BODY_BYTES") or DEFAULT_MAX_BODY))
    except ValueError:
        return DEFAULT_MAX_BODY


def hsts_enabled() -> bool:
    return (os.getenv("SECURITY_HSTS") or "").strip().lower() in ("1", "true", "on", "yes")


def docs_enabled() -> bool:
    return not is_production()


def needs_no_store(path: str, has_authorization: bool) -> bool:
    """인증 관련 응답과 로그인한 사용자의 응답(개인 데이터)은 브라우저·프록시 캐시에 남지 않게 한다."""
    return path.startswith("/api/auth") or has_authorization


def apply_headers(headers, path: str, has_authorization: bool) -> None:
    """응답 헤더에 보안 헤더를 더한다. 이미 있는 값(예: 소셜 로그인 리디렉션의 Cache-Control)은 덮지 않는다."""
    for name, value in BASE_HEADERS.items():
        if name not in headers:
            headers[name] = value
    if needs_no_store(path, has_authorization) and "Cache-Control" not in headers:
        headers["Cache-Control"] = "no-store"
    if hsts_enabled():
        headers["Strict-Transport-Security"] = HSTS_VALUE


def public_get_limited(method: str, path: str, ip: str) -> float | None:
    """GET /api/* 거친 IP 한도. 넘으면 대기 초, 아니면 None."""
    if method != "GET" or not path.startswith("/api/") or not rate_limit.enabled():
        return None
    ok, wait = rate_limit.public_get.hit(ip, rate_limit.setting("RATE_LIMIT_PUBLIC_GET_PER_MIN"))
    return None if ok else wait


class BodyTooLarge(Exception):
    """본문이 MAX_REQUEST_BODY_BYTES를 넘었다(app/main.py가 413으로 바꾼다)."""


class BodySizeLimitMiddleware:
    """ASGI 미들웨어: Content-Length가 한도를 넘으면 바로 413. 길이를 밝히지 않은(chunked) 본문은 한도까지만 먼저 읽어 두고(최대 1MB),
    넘으면 413, 아니면 읽어 둔 본문을 그대로 앱에 넘긴다. Content-Length보다 실제 본문이 길면 받으면서 세어 BodyTooLarge."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = max_body_bytes()
        declared = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    return await _send_413(send)
        if declared is not None and declared > limit:
            return await _send_413(send)
        if declared is None and scope.get("method") in ("POST", "PUT", "PATCH", "DELETE"):
            buffered, size = [], 0
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    buffered.append(message)
                    break
                size += len(message.get("body", b""))
                if size > limit:
                    return await _send_413(send)
                buffered.append(message)
                if not message.get("more_body", False):
                    break

            async def replay():
                return buffered.pop(0) if buffered else await receive()

            return await self.app(scope, replay, send)
        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise BodyTooLarge()
            return message

        return await self.app(scope, limited_receive, send)


async def _send_413(send):
    body = ('{"detail":"' + BODY_TOO_LARGE + '"}').encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    headers += [(k.lower().encode(), v.encode()) for k, v in BASE_HEADERS.items()]
    await send({"type": "http.response.start", "status": 413, "headers": headers})
    await send({"type": "http.response.body", "body": body})


# ---------- 접근 로그 ----------


def strip_query(path: str) -> str:
    """'/a/b?code=..&state=..' -> '/a/b'. 쿼리에는 소셜 로그인 공급자 code·state, 검색어 같은 값이 있을 수 있다."""
    return urlsplit(path).path if "?" in path else path


class QueryRedactFilter(logging.Filter):
    """uvicorn.access 로그 레코드의 요청 경로에서 쿼리 문자열을 지운다(args: client_addr, method, full_path, http_version, status_code)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = args[:2] + (strip_query(args[2]),) + args[3:]
        return True


def install_access_log_filter() -> None:
    """uvicorn은 로깅 설정을 앱을 불러오기 전에 끝내므로 앱 import 시점에 필터를 붙이면 유지된다. 두 번 붙이지 않는다."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, QueryRedactFilter) for f in logger.filters):
        logger.addFilter(QueryRedactFilter())
