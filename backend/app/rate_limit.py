"""메모리 기반 레이트 리밋(2026-10-28 안정화). 단일 프로세스·단일 인스턴스 가정.

막는 것: 한 IP의 로그인·가입 반복(무차별 대입), 같은 아이디+IP의 연속 로그인 실패(일시 잠금), 한 사용자의 주문 폭주,
한 사용자의 AI 질문 폭주와 동시 질문(Ollama를 오래 점유한다), 한 사용자의 로그아웃·비밀번호 변경 반복과
비밀번호 변경 시 현재 비밀번호 연속 실패(일시 잠금, 2026-10-29).
못 막는 것: 서버를 재시작하면 카운터가 초기화된다. 워커·인스턴스를 여러 개 띄우면 각자 따로 센다. IP는 request.client.host
(직접 연결한 주소)라 프록시 뒤에서는 모든 요청이 같은 IP로 보이고, X-Forwarded-For는 위조될 수 있어 믿지 않는다.
여러 IP에서 한 아이디를 노리는 분산 시도는 아이디+IP 잠금으로 막히지 않는다(정상 사용자를 잠그지 않기 위한 선택).

설정(환경변수, 정수, 기본값은 아래 상수): RATE_LIMIT_ENABLED(1/0), RATE_LIMIT_LOGIN_PER_MIN, RATE_LIMIT_SIGNUP_PER_HOUR,
LOGIN_LOCK_FAILURES, LOGIN_LOCK_SECONDS, RATE_LIMIT_ORDERS_PER_MIN, RATE_LIMIT_RESEARCH_PER_MIN, RATE_LIMIT_RESEARCH_CONCURRENT,
RATE_LIMIT_LOGOUT_PER_MIN, RATE_LIMIT_PASSWORD_CHANGE_PER_HOUR, PASSWORD_CHANGE_LOCK_FAILURES, PASSWORD_CHANGE_LOCK_SECONDS,
RATE_LIMIT_BACKTEST_PER_MIN, RATE_LIMIT_RECOVERY_PER_MIN, RATE_LIMIT_RECOVERY_EMAIL_PER_HOUR, RATE_LIMIT_TOKEN_PER_MIN,
RATE_LIMIT_VERIFICATION_MAIL_PER_HOUR(계정 복구, 2026-10-30).
설정은 요청 때마다 읽으므로(값이 바뀌면 그 키의 한도도 바뀐다) 테스트에서 monkeypatch.setenv로 바꿀 수 있다.
"""

import math
import os
import threading
import time
from collections import OrderedDict, deque
from typing import Callable

from fastapi import HTTPException

DEFAULTS = {
    "RATE_LIMIT_LOGIN_PER_MIN": 10,  # IP당 로그인 시도
    "RATE_LIMIT_SIGNUP_PER_HOUR": 10,  # IP당 가입
    "LOGIN_LOCK_FAILURES": 5,  # 같은 아이디+IP 연속 실패 이만큼이면
    "LOGIN_LOCK_SECONDS": 300,  # 이만큼 잠금(실패 카운트도 이 시간 안의 것만 센다)
    "RATE_LIMIT_ORDERS_PER_MIN": 30,  # 사용자당 모의투자 주문
    "RATE_LIMIT_RESEARCH_PER_MIN": 6,  # 사용자당 AI 질문
    "RATE_LIMIT_RESEARCH_CONCURRENT": 1,  # 사용자당 동시에 처리 중인 AI 질문
    "RATE_LIMIT_LOGOUT_PER_MIN": 10,  # 사용자당 로그아웃
    "RATE_LIMIT_PASSWORD_CHANGE_PER_HOUR": 10,  # 사용자당 비밀번호 변경 시도(argon2 검증·해시를 매번 한다)
    "PASSWORD_CHANGE_LOCK_FAILURES": 5,  # 같은 사용자의 현재 비밀번호 연속 오류 이만큼이면
    "PASSWORD_CHANGE_LOCK_SECONDS": 300,  # 이만큼 비밀번호 변경 잠금
    "RATE_LIMIT_BACKTEST_PER_MIN": 10,  # 사용자당 백테스트(요청마다 최대 5종목·5년을 계산한다)
    # 계정 복구(2026-10-30). 아이디 찾기·비밀번호 찾기는 엔드포인트별로 IP당 분당 + 이메일당 시간당(메일 폭탄 방지)
    "RATE_LIMIT_RECOVERY_PER_MIN": 5,
    "RATE_LIMIT_RECOVERY_EMAIL_PER_HOUR": 3,
    "RATE_LIMIT_TOKEN_PER_MIN": 10,  # 이메일 인증·비밀번호 재설정(토큰 제출) 엔드포인트별 IP당 분당(토큰 무차별 대입 방지)
    "RATE_LIMIT_VERIFICATION_MAIL_PER_HOUR": 5,  # 사용자당 인증 메일(이메일 등록·변경 + 재발송 합산)
}
MAX_KEYS = 10_000  # 리미터 하나가 기억하는 키 수 상한(넘으면 가장 오래 안 쓴 키부터 버린다)
CONCURRENT_RETRY_AFTER_S = 5  # 동시 질문으로 거절할 때 안내하는 대기 시간
DETAIL = "요청이 너무 많습니다. {n}초 후 다시 시도해주세요."


def enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")


def setting(name: str) -> int:
    try:
        return max(1, int(os.getenv(name, DEFAULTS[name])))
    except ValueError:
        return DEFAULTS[name]


def too_many(retry_after: float) -> HTTPException:
    n = max(1, math.ceil(retry_after))
    return HTTPException(status_code=429, detail=DETAIL.format(n=n), headers={"Retry-After": str(n)})


class SlidingWindow:
    """키별 최근 window초 안의 요청 시각을 세는 슬라이딩 윈도. hit()이 (허용 여부, 남은 대기 초)를 돌려준다."""

    def __init__(self, window_s: float, clock: Callable[[], float] = time.monotonic, max_keys: int = MAX_KEYS):
        self.window_s, self.clock, self.max_keys = window_s, clock, max_keys
        self._hits: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int) -> tuple[bool, float]:
        now = self.clock()
        with self._lock:
            q = self._hits.pop(key, None) or deque()
            while q and q[0] <= now - self.window_s:
                q.popleft()
            if len(q) >= limit:
                self._hits[key] = q
                return False, q[0] + self.window_s - now
            q.append(now)
            self._hits[key] = q  # 맨 뒤 = 가장 최근에 쓴 키
            self._prune(now)
            return True, 0.0

    def _prune(self, now: float) -> None:
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)
        # 앞쪽(오래 안 쓴) 키 중 이미 다 만료된 것은 버린다(한 번에 조금씩)
        for _ in range(min(16, len(self._hits))):
            key, q = next(iter(self._hits.items()))
            if q and q[-1] > now - self.window_s:
                break
            self._hits.popitem(last=False)

    def size(self) -> int:
        return len(self._hits)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


class FailureLock:
    """키별 연속 실패를 세어 threshold번이면 lock_s초 잠근다. 성공(reset)하면 초기화된다."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = MAX_KEYS):
        self.clock, self.max_keys = clock, max_keys
        self._state: OrderedDict[str, tuple[int, float, float]] = OrderedDict()  # key -> (실패 수, 첫 실패, 잠금 해제 시각)
        self._lock = threading.Lock()

    def locked_for(self, key: str) -> float:
        now = self.clock()
        with self._lock:
            st = self._state.get(key)
            if st is None:
                return 0.0
            if st[2] > now:
                return st[2] - now
            if st[2] and st[2] <= now:  # 잠금이 끝났으면 처음부터 다시 센다
                self._state.pop(key, None)
            return 0.0

    def failure(self, key: str, threshold: int, lock_s: float) -> None:
        now = self.clock()
        with self._lock:
            count, first, until = self._state.pop(key, (0, now, 0.0))
            if now - first > lock_s:  # 오래전 실패는 세지 않는다
                count, first = 0, now
            count += 1
            until = now + lock_s if count >= threshold else 0.0
            self._state[key] = (count, first, until)
            while len(self._state) > self.max_keys:
                self._state.popitem(last=False)

    def reset(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def reset_prefix(self, prefix: str) -> None:
        """prefix로 시작하는 키를 모두 지운다(비밀번호 재설정 뒤 그 아이디의 모든 IP 잠금 해제)."""
        with self._lock:
            for key in [k for k in self._state if k.startswith(prefix)]:
                self._state.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._state.clear()


class Concurrency:
    """키별 동시에 진행 중인 작업 수. acquire가 False면 한도 초과."""

    def __init__(self):
        self._active: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str, limit: int) -> bool:
        with self._lock:
            n = self._active.get(key, 0)
            if n >= limit:
                return False
            self._active[key] = n + 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            n = self._active.get(key, 0) - 1
            if n > 0:
                self._active[key] = n
            else:
                self._active.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._active.clear()


# ---------- 앱이 쓰는 리미터(프로세스당 하나) ----------

login_ip = SlidingWindow(60)
signup_ip = SlidingWindow(3600)
login_failures = FailureLock()
orders = SlidingWindow(60)
research = SlidingWindow(60)
research_active = Concurrency()
logout = SlidingWindow(60)
password_change = SlidingWindow(3600)
password_change_failures = FailureLock()
backtest = SlidingWindow(60)
recovery_ip = SlidingWindow(60)  # 키: "{엔드포인트}|{ip}"
recovery_email = SlidingWindow(3600)  # 키: "{엔드포인트}|{정규화한 이메일}"
token_ip = SlidingWindow(60)  # 키: "{엔드포인트}|{ip}"
verification_mail = SlidingWindow(3600)  # 키: 사용자(owner_key)
ALL = (login_ip, signup_ip, login_failures, orders, research, research_active, logout, password_change, password_change_failures,
       backtest, recovery_ip, recovery_email, token_ip, verification_mail)


def reset_all() -> None:
    for r in ALL:
        r.clear()


def check(limiter: SlidingWindow, key: str, limit_name: str) -> None:
    """한도를 넘으면 429를 올린다. 비활성(RATE_LIMIT_ENABLED=0)이면 아무것도 하지 않는다."""
    if not enabled():
        return
    ok, wait = limiter.hit(key, setting(limit_name))
    if not ok:
        raise too_many(wait)


def _check_lock(lock: FailureLock, key: str) -> None:
    if not enabled():
        return
    wait = lock.locked_for(key)
    if wait > 0:
        raise too_many(wait)


def _record(lock: FailureLock, key: str, success: bool, prefix: str) -> None:
    if not enabled():
        return
    if success:
        lock.reset(key)
    else:
        lock.failure(key, setting(f"{prefix}_LOCK_FAILURES"), setting(f"{prefix}_LOCK_SECONDS"))


def check_login_lock(key: str) -> None:
    _check_lock(login_failures, key)


def record_login(key: str, success: bool) -> None:
    _record(login_failures, key, success, "LOGIN")


def clear_locks_after_password_reset(username: str, owner_key: str) -> None:
    """비밀번호 재설정에 성공하면 그 아이디의 로그인 잠금(모든 IP)과 비밀번호 변경 잠금을 푼다(비활성 상태에서도 지운다)."""
    login_failures.reset_prefix(f"{username.casefold()}|")
    password_change_failures.reset(owner_key)


def check_password_change_lock(key: str) -> None:
    _check_lock(password_change_failures, key)


def record_password_change(key: str, success: bool) -> None:
    """현재 비밀번호가 맞았는지만 센다(새 비밀번호 규칙 위반 422는 라우트에 들어오기 전에 끝나 여기까지 오지 않는다)."""
    _record(password_change_failures, key, success, "PASSWORD_CHANGE")


class research_slot:
    """with research_slot(user_key): 사용자당 동시 AI 질문 수를 제한한다(초과면 429)."""

    def __init__(self, key: str):
        self.key, self.held = key, False

    def __enter__(self):
        if enabled():
            if not research_active.acquire(self.key, setting("RATE_LIMIT_RESEARCH_CONCURRENT")):
                raise too_many(CONCURRENT_RETRY_AFTER_S)
            self.held = True
        return self

    def __exit__(self, *exc):
        if self.held:
            research_active.release(self.key)
        return False
