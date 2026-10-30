"""외부 호출 공용 timeout·재시도(2026-10-28 안정화).

- 일시적 오류(연결 실패, timeout, HTTP 5xx, 429)만 최대 RETRIES번 다시 시도한다. 4xx(429 제외)와 그 밖의 예외(응답 파싱 오류 등)는
  다시 시도해도 같으므로 바로 실패시킨다.
- 대기는 지수 백오프(BACKOFF_S x 2^n)이고, 다음 대기를 해도 합계가 MAX_TOTAL_S를 넘으면 더 기다리지 않고 실패시킨다.
- sleep·clock은 주입할 수 있어 테스트가 실제로 기다리지 않는다.

라이브러리 호출(FinanceDataReader 등)은 내부에서 requests를 timeout 없이 부르므로, with_timeout으로 별도 스레드에서 실행해
상한을 둔다(상한을 넘기면 TimeoutError. 그 스레드는 끝날 때까지 백그라운드에 남는다 - 배치 스크립트용).
"""

import concurrent.futures
import logging
import time
from typing import Callable, TypeVar

import requests

logger = logging.getLogger(__name__)
T = TypeVar("T")

# (연결, 읽기) 초. 수집 스크립트 기본값. 사용자 요청 경로는 호출측이 더 짧게 준다.
DEFAULT_TIMEOUT = (5, 20)
RETRIES = 2
BACKOFF_S = 0.5
MAX_TOTAL_S = 60.0
LIBRARY_TIMEOUT_S = 60.0


class TransientHTTPError(requests.HTTPError):
    """5xx·429 응답(다시 시도할 만한 HTTP 오류)."""


def is_transient(exc: BaseException) -> bool:
    """다시 시도할 만한 오류인지. 연결 오류·timeout·5xx·429는 True, 4xx·파싱 오류 등은 False."""
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status is not None and (status >= 500 or status == 429)
    try:  # Ollama 클라이언트(httpx)의 timeout·연결 오류
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
    except ImportError:  # pragma: no cover
        pass
    return False


def call_with_retry(fn: Callable[[], T], *, retries: int = RETRIES, backoff: float = BACKOFF_S,
                    max_total: float = MAX_TOTAL_S, transient: Callable[[BaseException], bool] = is_transient,
                    sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                    label: str = "") -> T:
    """fn()을 부르고 일시적 오류면 지수 백오프로 최대 retries번 다시 부른다. 마지막 예외를 그대로 올린다."""
    started = clock()
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - 분류 후 다시 올린다
            if not transient(e) or attempt >= retries:
                raise
            wait = backoff * (2 ** attempt)
            if clock() - started + wait > max_total:
                raise
            logger.warning("외부 호출 재시도 %s/%s %s(%s), %.1f초 후", attempt + 1, retries, label, type(e).__name__, wait)
            sleep(wait)
            attempt += 1


def get(url: str, *, params=None, headers=None, timeout=DEFAULT_TIMEOUT, session=None, label: str = "",
        **retry_kwargs) -> requests.Response:
    """requests GET + timeout + 일시적 오류 재시도. 4xx는 바로 HTTPError, 재시도 끝의 5xx는 TransientHTTPError."""
    http = session or requests

    def once():
        resp = http.get(url, params=params, headers=headers, timeout=timeout)
        status = getattr(resp, "status_code", 200)
        if status >= 500 or status == 429:
            raise TransientHTTPError(f"HTTP {status}", response=resp)
        resp.raise_for_status()
        return resp

    return call_with_retry(once, label=label or url.split("?")[0], **retry_kwargs)


def post(url: str, *, data=None, headers=None, timeout=DEFAULT_TIMEOUT, session=None, label: str = "",
         **retry_kwargs) -> requests.Response:
    """requests POST(form) + timeout + get()과 같은 재시도 규칙(5xx·429·연결 오류·timeout만). 4xx는 바로 HTTPError.
    label은 로그에 쓰이므로 URL만 넣는다(본문·비밀값은 로그에 남지 않는다)."""
    http = session or requests

    def once():
        resp = http.post(url, data=data, headers=headers, timeout=timeout)
        status = getattr(resp, "status_code", 200)
        if status >= 500 or status == 429:
            raise TransientHTTPError(f"HTTP {status}", response=resp)
        resp.raise_for_status()
        return resp

    return call_with_retry(once, label=label or url.split("?")[0], **retry_kwargs)


_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="ext-call")


def with_timeout(fn: Callable[[], T], seconds: float = LIBRARY_TIMEOUT_S) -> T:
    """timeout을 받지 않는 라이브러리 호출에 상한을 둔다. 넘기면 TimeoutError(일시적 오류로 분류돼 재시도 대상)."""
    future = _POOL.submit(fn)
    try:
        return future.result(timeout=seconds)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"{seconds:g}초 안에 응답이 없습니다") from None
