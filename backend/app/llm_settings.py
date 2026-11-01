"""Ollama 호출 설정(환경변수). DB·Ollama를 import하지 않는 순수 모듈이다.

- OLLAMA_CHAT_MODEL: 채팅 모델. 없으면 llama3.1:8b. 롤백은 이 값을 지우면 된다.
- OLLAMA_KEEP_ALIVE: 호출마다 보내는 keep_alive. 없으면 30분. 빈 값이면 보내지 않는다(Ollama 서버 기본값, 보통 5분).
- OLLAMA_THINK: 생각 모드가 있는 모델에만 쓴다. true/false. 없으면 보내지 않는다(llama3.1에는 필요 없다).
- OLLAMA_WARMUP: 0/false/off면 시작 시 미리 적재하지 않는다.
"""

import os
import sys

DEFAULT_CHAT_MODEL = "llama3.1:8b"
DEFAULT_KEEP_ALIVE = "30m"
_TRUE = {"1", "true", "on", "yes"}
_FALSE = {"0", "false", "off", "no"}


def chat_model() -> str:
    return (os.getenv("OLLAMA_CHAT_MODEL") or "").strip() or DEFAULT_CHAT_MODEL


def keep_alive() -> str | None:
    value = os.getenv("OLLAMA_KEEP_ALIVE")
    if value is None:
        return DEFAULT_KEEP_ALIVE
    return value.strip() or None


def think_option() -> bool | None:
    value = (os.getenv("OLLAMA_THINK") or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def call_options() -> dict:
    """chat 호출에 덧붙일 인자. 값이 없는 것은 보내지 않는다."""
    out = {}
    if (alive := keep_alive()) is not None:
        out["keep_alive"] = alive
    if (think := think_option()) is not None:
        out["think"] = think
    return out


def embed_options() -> dict:
    alive = keep_alive()
    return {"keep_alive": alive} if alive is not None else {}


def warmup_enabled() -> bool:
    """테스트·CI에서는 돌지 않는다(Ollama 없이 통과해야 한다)."""
    if (os.getenv("OLLAMA_WARMUP") or "").strip().lower() in _FALSE:
        return False
    return not (os.getenv("CI") or os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules)
