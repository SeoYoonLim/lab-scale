"""백엔드 시작 시 채팅 모델과 임베딩 모델을 백그라운드에서 미리 올린다. 시작을 막지 않고, 실패해도 무시한다."""

import logging
import threading

import ollama

from app.embeddings import EMBEDDING_MODEL
from app.llm_settings import call_options, chat_model, embed_options, warmup_enabled

logger = logging.getLogger(__name__)
WARMUP_TIMEOUT_S = 120


def warmup_models() -> None:
    """모델마다 한 번씩 호출한다. 하나가 실패해도 나머지는 시도한다."""
    client = ollama.Client(timeout=WARMUP_TIMEOUT_S)
    for name, call in (
        ("chat", lambda: client.chat(model=chat_model(), messages=[{"role": "user", "content": "안녕"}],
                                     options={"num_predict": 1}, **call_options())),
        ("embed", lambda: client.embed(model=EMBEDDING_MODEL, input=["warmup"], **embed_options())),
    ):
        try:
            call()
        except Exception as e:  # noqa: BLE001 - 워밍업 실패는 서비스에 영향이 없다
            logger.info("모델 워밍업 실패(%s): %s", name, type(e).__name__)


def start_warmup() -> threading.Thread | None:
    if not warmup_enabled():
        return None
    thread = threading.Thread(target=warmup_models, name="ollama-warmup", daemon=True)
    thread.start()
    return thread
