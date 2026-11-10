"""외부 호출 공용 timeout·재시도(app/http_retry). 네트워크·실제 대기 없이(sleep·clock 주입) 돈다."""

import time

import pytest
import requests

from app import http_retry as H


class Resp:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class Http:
    def __init__(self, *items):
        self.items, self.calls = list(items), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(timeout)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def sleeps():
    return []


def get(http, sleeps, **kw):
    return H.get("https://x.example/a?key=1", session=http, sleep=sleeps.append, **kw)


def test_success_first_try_passes_the_timeout(sleeps):
    http = Http(Resp(200))
    assert get(http, sleeps, timeout=(1, 2)).status_code == 200
    assert http.calls == [(1, 2)] and sleeps == []


@pytest.mark.parametrize("err", [requests.ConnectionError(), requests.Timeout(), Resp(503), Resp(429)])
def test_transient_errors_are_retried_with_exponential_backoff(err, sleeps):
    http = Http(err, err, Resp(200))
    assert get(http, sleeps).status_code == 200
    assert len(http.calls) == 3 and sleeps == [0.5, 1.0]


def test_gives_up_after_two_retries(sleeps):
    http = Http(*[Resp(502)] * 5)
    with pytest.raises(H.TransientHTTPError):
        get(http, sleeps)
    assert len(http.calls) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_4xx_is_not_retried(status, sleeps):
    http = Http(Resp(status), Resp(200))
    with pytest.raises(requests.HTTPError):
        get(http, sleeps)
    assert len(http.calls) == 1 and sleeps == []


def test_non_transient_exception_is_not_retried(sleeps):
    calls = []

    def boom():
        calls.append(1)
        raise ValueError("파싱 오류")

    with pytest.raises(ValueError):
        H.call_with_retry(boom, sleep=sleeps.append)
    assert calls == [1]


def test_total_time_cap_stops_retrying(sleeps):
    now = [0.0]
    http = Http(requests.Timeout(), requests.Timeout(), Resp(200))

    def clock():
        now[0] += 10.0  # 호출마다 10초 걸린 것처럼
        return now[0]

    with pytest.raises(requests.Timeout):
        get(http, sleeps, max_total=10, clock=clock)
    assert len(http.calls) == 1  # 첫 실패까지 10초 + 대기 0.5초가 상한(10초)을 넘으므로 바로 실패


def test_is_transient():
    assert H.is_transient(requests.ConnectionError()) and H.is_transient(TimeoutError())
    assert H.is_transient(requests.HTTPError(response=Resp(500)))
    assert not H.is_transient(requests.HTTPError(response=Resp(404)))
    assert not H.is_transient(requests.HTTPError())  # 응답 없는 HTTPError는 알 수 없어 재시도하지 않는다
    assert not H.is_transient(KeyError("x"))
    import httpx

    assert H.is_transient(httpx.ReadTimeout("t")) and H.is_transient(httpx.ConnectError("c"))


def test_with_timeout_raises_timeout_error():
    assert H.with_timeout(lambda: 7, 1) == 7
    t0 = time.perf_counter()
    with pytest.raises(TimeoutError):
        H.with_timeout(lambda: time.sleep(0.5), 0.05)
    assert time.perf_counter() - t0 < 0.4


def test_with_timeout_error_is_retried(sleeps):
    calls = []

    def slow_then_fast():
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError()
        return "ok"

    assert H.call_with_retry(slow_then_fast, sleep=sleeps.append) == "ok" and len(calls) == 2


def test_realtime_price_user_path_has_a_short_connect_read_timeout(monkeypatch):
    from app import realtime_price as R

    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["timeout"] = timeout
        raise requests.Timeout()

    monkeypatch.setattr(R.requests, "get", fake_get)
    assert R._fetch_from_naver("005930") is None  # 실패하면 None -> 호출측이 DB 종가로 폴백(기존 동작)
    assert seen["timeout"] == (1.5, 2.5) and sum(seen["timeout"]) <= 4


def test_dart_document_4xx_is_not_retried(monkeypatch):
    from app import dart_documents as D

    monkeypatch.setenv("DART_API_KEY", "dummy")
    http = Http(Resp(404), Resp(200))
    out = D.fetch_document_text("1", "", http)
    assert out.status == "transient" and len(http.calls) == 1 and http.calls[0] == (5, 60)
