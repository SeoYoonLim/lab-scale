"""운영 설정(환경변수). 비밀값은 코드에 두지 않는다. 2026-10-28 안정화.

| 환경변수 | 기본값 | 뜻 |
| APP_ENV | development | production이면 약한 JWT_SECRET으로 기동하지 않는다 |
| CORS_ALLOW_ORIGINS | http://localhost:5173,http://127.0.0.1:5173 | 쉼표로 구분한 허용 origin. credentials는 쓰지 않는다(Bearer 헤더) |
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
# 알려진 예시·기본값에 흔한 낱말. 들어 있으면 약한 시크릿으로 본다.
_WEAK_WORDS_RE = re.compile(r"change|replace|example|sample|secret|password|default|dummy|test|교체|비밀", re.IGNORECASE)
MIN_DISTINCT_CHARS = 10


def app_env() -> str:
    return (os.getenv("APP_ENV") or "development").strip().lower()


def is_production() -> bool:
    return app_env() in ("production", "prod")


def cors_origins() -> list[str]:
    """허용 origin 목록. 비어 있으면 기본값(개발용 5173). '*'가 있으면 모든 origin(credentials는 어차피 끈다)."""
    raw = os.getenv("CORS_ALLOW_ORIGINS")
    if raw is None or not raw.strip():
        return list(DEFAULT_CORS_ORIGINS)
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    if "*" in origins:
        logger.warning("CORS_ALLOW_ORIGINS에 '*'가 있어 모든 origin을 허용합니다(credentials는 허용하지 않음).")
        return ["*"]
    return origins


def jwt_secret_problems(secret: str | None) -> list[str]:
    """JWT 시크릿이 약한 이유 목록(없으면 []). 길이 부족·비어 있음은 app.auth.get_jwt_secret이 모든 모드에서 거부한다.
    여기서는 길이는 충분하지만 예시 문구·반복 문자처럼 추측하기 쉬운 값을 본다."""
    if not secret:
        return ["비어 있음"]
    problems = []
    if _WEAK_WORDS_RE.search(secret):
        problems.append("예시·기본값에 흔한 낱말이 들어 있음")
    if len(set(secret)) < MIN_DISTINCT_CHARS:
        problems.append(f"서로 다른 문자가 {MIN_DISTINCT_CHARS}개 미만")
    return problems


def check_jwt_secret_for_startup() -> None:
    """기동 시 검사: 운영(APP_ENV=production)이면 약한 시크릿에 RuntimeError, 개발이면 경고 로그만(값은 출력하지 않는다)."""
    problems = jwt_secret_problems(os.getenv("JWT_SECRET"))
    if not problems:
        return
    msg = "JWT_SECRET이 약합니다: " + ", ".join(problems) + ". secrets.token_urlsafe(48)로 새로 만드세요."
    if is_production():
        raise RuntimeError(msg)
    logger.warning("%s (개발 모드라 계속 실행)", msg)
