"""리서치 답변을 요청 언어로(2026-11-05). ko-KR이 아닐 때만 app.agent.ask_question이 부른다.

답변은 언제나 기존 한국어 경로로 먼저 만든다(숫자 검증·종목 정합성·서술 검증·외국 문자 제거가 모두 그대로 적용된다).
그 결과를 언어에 맞게 바꾸는 순서:
1. 고정 안내(답변 실패·지연)는 언어별 고정 문구
2. 용어 사전 답(종목 없는 정의·비교·해석 질문)은 언어별 사전(app.glossary_i18n). 한국어 답이 사전 답과 글자까지 같을 때만
3. 전망 간결 답·후속 답(app.outlook_i18n)은 같은 계산 결과로 언어별 템플릿(2026-11-06). 기록으로 다시 만든 한국어 답이 저장된 답과
   글자까지 같고 새 숫자가 없을 때만 쓴다. 최근 N거래일 주가 흐름 답(app.price_flow_i18n)도 같은 조건의 언어별 템플릿이다
4. 그 밖(상세 전망·실적·기간·시세·LLM 답 등)은 보호 번역(app.i18n_translate): 숫자·날짜·종목명·기사/공시 제목은 코드가 원문 그대로 되돌린다.
   검증에 실패하면 한국어 원문 + 안내 문구다.
"""

import logging
from functools import lru_cache

from app import glossary, glossary_i18n, outlook_i18n, price_flow_i18n
from app.i18n import text
from app.i18n_translate import STATUS_LOCALIZED, protected_terms, translate

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _company_names() -> tuple[str, ...]:
    """DB의 등록 종목명(보호 문자열 후보). DB가 없으면 빈 값(그래도 tool 결과의 종목명은 보호된다)."""
    try:
        from app.db.session import SessionLocal
        from app.models import Company

        db = SessionLocal()
        try:
            return tuple(n for (n,) in db.query(Company.name).all() if n)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("종목명 목록을 읽지 못함(번역 보호는 tool 결과의 종목명만): %s", type(e).__name__)
        _company_names.cache_clear()
        return ()


def localize_answer(question: str, answer: str, records: list[dict], locale: str,
                    fixed: dict[str, str] | None = None) -> tuple[str, str]:
    """(그 언어의 답변, translation_status). fixed는 {고정 문구 키: 그 한국어 상수}(app.agent가 넘긴다)."""
    for key, korean in (fixed or {}).items():
        if answer == korean:
            return text(key, locale), STATUS_LOCALIZED
    if not records:
        korean = glossary.concept_answer(question)
        if korean is not None and korean == answer:
            localized = glossary_i18n.concept_answer(question, locale)
            if localized:
                return localized, STATUS_LOCALIZED
    templated = outlook_i18n.localize(answer, records, locale) or price_flow_i18n.localize(answer, records, locale)
    if templated is not None:
        return templated, STATUS_LOCALIZED
    names = [n for n in _company_names() if n in answer]
    result = translate(answer, locale, protected_terms(records, names))
    if result.failures:
        logger.info("답변 번역(%s) %s: %s", locale, result.status, result.failures)
    return result.text, result.status
