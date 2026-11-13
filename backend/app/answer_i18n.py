"""리서치 답변을 요청 언어로(2026-11-05). ko-KR이 아닐 때만 app.agent.ask_question이 부른다.

답변은 언제나 기존 한국어 경로로 먼저 만든다(숫자 검증·종목 정합성·서술 검증·외국 문자 제거가 모두 그대로 적용된다).
그 결과를 언어에 맞게 바꾸는 순서:
1. 고정 안내(답변 실패·지연)는 언어별 고정 문구
2. 용어 사전 답(종목 없는 정의·비교·해석 질문)은 언어별 사전(app.glossary_i18n). 한국어 답이 사전 답과 글자까지 같을 때만
3. 전망 간결 답·후속 답(app.outlook_i18n)은 같은 계산 결과로 언어별 템플릿(2026-11-06). 기록으로 다시 만든 한국어 답이 저장된 답과
   글자까지 같고 새 숫자가 없을 때만 쓴다. 최근 N거래일 주가 흐름 답(app.price_flow_i18n)도 같은 조건의 언어별 템플릿이다
4. 그 밖(상세 전망·실적·기간·시세·LLM 답 등)은 보호 번역(app.i18n_translate): 숫자·날짜·종목명·기사/공시 제목은 코드가 원문 그대로 되돌린다.
   검증에 실패하면(2026-11-07 35차) 한국어 원문이 아니라 그 언어의 안내 + 도구 결과 데이터 요약이다(status fallback_summary).
   단순 시세 답(app.quote_i18n)은 3번처럼 언어별 템플릿이다. ETF 구성종목·특정 종목 비중 답(app.etf_holdings_i18n, 48차)도 같다.
"""

import logging
from functools import lru_cache

from app import etf_holdings_i18n, glossary, glossary_i18n, outlook_i18n, price_flow_i18n, quote_i18n
from app.i18n import text
from app.i18n_translate import STATUS_FALLBACK, STATUS_LOCALIZED, protected_terms, translate

# 2026-11-07(35차): 번역 검증 실패 시 한국어 원문 대신 그 언어의 안내 + 데이터 요약(app.quote_i18n.fallback_summary)
STATUS_FALLBACK_SUMMARY = "fallback_summary"

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


# 49차: 전망 기간 안내(app.outlook.horizon_note / unsupported_horizon_answer)의 언어별 문장. 숫자는 같은 값이다.
_HORIZON_NOTE = {
    "en-US": "The requested period (about {n} trading days) is not a supported outlook period (the next 5, 20 or 60 trading days), "
             "so this analysis uses the nearest one: the next {used} trading days.",
    "ja-JP": "ご指定の期間（約{n}営業日）は対応している見通し期間（今後5・20・60営業日）ではないため、最も近い今後{used}営業日を基準に分析しました。",
    "zh-CN": "您指定的期间（约{n}个交易日）不是支持的展望期间（未来5、20、60个交易日），因此按最接近的未来{used}个交易日进行分析。",
}
_HORIZON_UNSUPPORTED = {
    "en-US": "Outlooks are only provided for the next 5, 20 and 60 trading days (1 week, 1 month and 3 months). An outlook for the "
             "requested period is not provided (the indicators and the historical backtest are based on these three periods). "
             "Please ask again with one of these periods.",
    "ja-JP": "見通しは今後5・20・60営業日（1週間・1か月・3か月）についてのみ提供しています。ご指定の期間の見通しは提供していません"
             "（指標の計算と過去検証がこの3つの期間を基準としているためです）。期間を変えて再度ご質問ください。",
    "zh-CN": "展望仅提供未来5、20、60个交易日（1周、1个月、3个月）的分析。不提供您指定期间的展望（指标计算和历史回测均以这三个期间为基准）。"
             "请更换期间后重新提问。",
}


def _localize_horizon(question, answer, records, locale, fixed, code_data, horizon) -> tuple[str, str] | None:
    """전망 기간 안내가 붙은 답. 지원 밖 안내는 고정 문장, 기간을 바꾼 안내는 그 문단만 떼어 옮기고 나머지는 원래 경로로 옮긴다."""
    if locale not in _HORIZON_NOTE or not horizon:
        return None
    if horizon.get("kind") == "unsupported":
        from app import outlook

        h = outlook.Horizon(horizon["text"], None, None, False)
        if answer == outlook.unsupported_horizon_answer(h):
            return f"{_HORIZON_UNSUPPORTED[locale]}\n\n{text('disclaimer', locale)}", STATUS_LOCALIZED
        return None
    note = horizon.get("note")
    if horizon.get("kind") == "note" and note and answer.startswith(note + "\n\n"):
        from app.outlook import PERIODS

        rest, status = localize_answer(question, answer[len(note) + 2:], records, locale, fixed, code_data)
        local_note = _HORIZON_NOTE[locale].format(n=horizon["requested_days"], used=PERIODS[horizon["label"]].days)
        return f"{local_note}\n\n{rest}", status
    return None


_AMBIGUOUS = {
    "en-US": "The name '{token}' matches several listed companies ({names}, etc.), so we can't tell which one you mean. "
             "Please ask again with the exact company name or ticker.",
    "ja-JP": "「{token}」という名前は複数の銘柄（{names}など）に当てはまるため、どの銘柄か特定できません。正確な銘柄名か銘柄コードで再度ご質問ください。",
    "zh-CN": "“{token}”对应多只股票（{names}等），无法确定是哪一只。请用准确的股票名称或股票代码重新提问。",
}


def localize_answer(question: str, answer: str, records: list[dict], locale: str,
                    fixed: dict[str, str] | None = None, code_data: dict | None = None,
                    horizon: dict | None = None, ambiguous: dict | None = None) -> tuple[str, str]:
    """(그 언어의 답변, translation_status). fixed는 {고정 문구 키: 그 한국어 상수}(app.agent가 넘긴다).
    code_data(48차)는 코드 템플릿 답의 데이터(app.agent 내부 meta["etf_holdings"])다. 있으면 그 데이터로 언어별 템플릿을 먼저 본다.
    horizon(49차)은 전망 기간 안내(내부 meta["forecast_horizon"])다."""
    for key, korean in (fixed or {}).items():
        if answer == korean:
            return text(key, locale), STATUS_LOCALIZED
    if ambiguous and locale in _AMBIGUOUS:
        from app.agent import ambiguous_group_text

        if answer == ambiguous_group_text(ambiguous["token"], ambiguous["candidates"]):
            sep = ", " if locale == "en-US" else "、"
            return _AMBIGUOUS[locale].format(token=ambiguous["token"], names=sep.join(ambiguous["candidates"])), STATUS_LOCALIZED
    with_horizon = _localize_horizon(question, answer, records, locale, fixed, code_data, horizon)
    if with_horizon is not None:
        return with_horizon
    holdings = etf_holdings_i18n.localize(answer, code_data, locale)
    if holdings is not None:
        return holdings, STATUS_LOCALIZED
    if not records:
        korean = glossary.concept_answer(question)
        if korean is not None and korean == answer:
            localized = glossary_i18n.concept_answer(question, locale)
            if localized:
                return localized, STATUS_LOCALIZED
    templated = (outlook_i18n.localize(answer, records, locale) or price_flow_i18n.localize(answer, records, locale)
                 or quote_i18n.localize(answer, records, locale))
    if templated is not None:
        return templated, STATUS_LOCALIZED
    names = [n for n in _company_names() if n in answer]
    result = translate(answer, locale, protected_terms(records, names))
    if result.failures:
        logger.info("답변 번역(%s) %s: %s", locale, result.status, result.failures)
    if result.status == STATUS_FALLBACK:
        # 한국어 원문을 내보내지 않는다. 원문은 리포트 original_content에 그대로 저장된다(GET /api/research/{id}?view=record)
        return quote_i18n.fallback_summary(records, locale), STATUS_FALLBACK_SUMMARY
    return result.text, result.status
