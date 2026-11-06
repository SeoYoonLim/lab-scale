"""다국어(2026-11-05): 지원 언어, locale 정규화·결정, 언어별 고정 문구와 금액 표기.

- 지원: ko-KR(기본) · en-US · ja-JP · zh-CN. 지원하지 않는 값은 ko-KR로 대체한다(요청 파라미터). 설정 저장(PUT)은 422로 막는다.
- 요청의 locale이 없으면 계정 설정(users.preferred_locale), 그것도 없으면 ko-KR이다. Accept-Language 헤더는 보지 않는다
  (브라우저가 자동으로 보내서, 설정을 바꾸지 않은 기존 한국어 사용자의 응답이 달라지면 안 된다).
- ko-KR은 기존 경로를 그대로 쓴다. 이 모듈의 문구는 ko-KR이 아닐 때만 쓰이고, ko-KR 값은 기존 상수와 글자까지 같다.
- 금액·비율·날짜의 값은 바꾸지 않는다. 표기(단위 글자, 조·억 단위의 읽는 법)만 언어에 맞춘다.
"""

from decimal import Decimal, InvalidOperation

DEFAULT_LOCALE = "ko-KR"
SUPPORTED_LOCALES = ("ko-KR", "en-US", "ja-JP", "zh-CN")
LANGUAGE_NAMES = {"ko-KR": "Korean", "en-US": "English", "ja-JP": "Japanese", "zh-CN": "Simplified Chinese"}
NATIVE_NAMES = {"ko-KR": "한국어", "en-US": "English", "ja-JP": "日本語", "zh-CN": "简体中文"}
MAX_LOCALE_LEN = 20

# 언어만 온 값과 흔한 표기. 번체(zh-TW, zh-Hant)는 지원하지 않으므로 넣지 않는다(→ ko-KR).
_ALIASES = {
    "ko": "ko-KR", "ko-kr": "ko-KR", "ko_kr": "ko-KR",
    "en": "en-US", "en-us": "en-US", "en_us": "en-US",
    "ja": "ja-JP", "ja-jp": "ja-JP", "ja_jp": "ja-JP",
    "zh": "zh-CN", "zh-cn": "zh-CN", "zh_cn": "zh-CN", "zh-hans": "zh-CN", "zh-hans-cn": "zh-CN",
}


def normalize_locale(value: str | None) -> str | None:
    """지원하는 locale이면 표준 표기(ko-KR 등), 아니면 None. 대소문자·밑줄 차이는 허용한다."""
    if not isinstance(value, str):
        return None
    return _ALIASES.get(value.strip().casefold())


def resolve_locale(requested: str | None, preferred: str | None = None) -> str:
    """요청 locale(있으면 그것만 본다: 지원하지 않으면 ko-KR) > 계정 설정 > ko-KR."""
    if requested is not None and requested.strip():
        return normalize_locale(requested) or DEFAULT_LOCALE
    return normalize_locale(preferred) or DEFAULT_LOCALE


def is_default(locale: str | None) -> bool:
    return (locale or DEFAULT_LOCALE) == DEFAULT_LOCALE


# ---------- 고정 문구 ----------
# ko-KR 값은 기존 상수(app.disclaimer.DISCLAIMER, app.agent.FALLBACK_ANSWER 등)와 같아야 한다(tests/test_i18n.py가 확인한다).

TEXTS: dict[str, dict[str, str]] = {
    "disclaimer": {
        "ko-KR": ("이 서비스는 학습·시연용 모의투자와 AI 리서치입니다. 제공되는 분석과 의견은 투자 권유나 자문이 아니며, "
                  "투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다. AI 응답에는 오류가 있을 수 있습니다."),
        "en-US": ("This service provides simulated trading and AI research for learning and demonstration purposes. "
                  "The analysis and opinions provided are not investment advice or a solicitation, and you are solely responsible "
                  "for your investment decisions and any resulting gains or losses. AI responses may contain errors."),
        "ja-JP": ("本サービスは学習・デモ用の模擬投資とAIリサーチです。提供される分析や意見は投資の勧誘や助言ではなく、"
                  "投資判断とそれに伴う損益の責任はご自身にあります。AIの回答には誤りが含まれる場合があります。"),
        "zh-CN": ("本服务为用于学习和演示的模拟投资与 AI 研究。所提供的分析和意见不构成投资建议或劝诱，"
                  "投资决策及由此产生的盈亏由您本人承担。AI 回答可能存在错误。"),
    },
    "fallback_answer": {
        "ko-KR": "죄송합니다. 요청을 처리하는 중 문제가 발생했습니다. 종목명을 포함해 질문을 조금 더 구체적으로 다시 해주세요.",
        "en-US": "Sorry, a problem occurred while processing your request. Please ask again more specifically, including the company name.",
        "ja-JP": "申し訳ありません。リクエストの処理中に問題が発生しました。銘柄名を含めて、もう少し具体的に質問し直してください。",
        "zh-CN": "抱歉，处理请求时出现问题。请包含股票名称，更具体地重新提问。",
    },
    "timeout_answer": {
        "ko-KR": "응답 생성이 지연되어 답변을 만들지 못했습니다. 잠시 후 다시 시도해주세요.",
        "en-US": "The response took too long, so we couldn't create an answer. Please try again in a moment.",
        "ja-JP": "応答の生成が遅れたため、回答を作成できませんでした。しばらくしてからもう一度お試しください。",
        "zh-CN": "生成回答耗时过长，未能完成。请稍后再试。",
    },
    # 번역을 검증하지 못해 한국어 원문을 보여 줄 때 맨 위에 붙인다
    "translation_fallback": {
        "en-US": "※ We couldn't verify an accurate translation of this answer, so the original Korean answer is shown below.",
        "ja-JP": "※ この回答の翻訳を正確に検証できなかったため、韓国語の原文をそのまま表示します。",
        "zh-CN": "※ 未能确认此回答译文的准确性，因此以下显示韩语原文。",
    },
    # 번역문 끝에 붙인다: 이름·제목은 원문 그대로라는 안내
    "translation_note": {
        "en-US": "※ Translated from the Korean answer. Company names, ETF names, news and filing titles are kept in their original form.",
        "ja-JP": "※ 韓国語の回答を翻訳したものです。企業名・ETF名・ニュースや開示のタイトルは原文のまま表示しています。",
        "zh-CN": "※ 本回答译自韩语原文。公司名称、ETF 名称以及新闻和公告标题保留原文。",
    },
    "no_holdings": {
        "ko-KR": "보유 종목이 없습니다. 모의투자 주문 후 진단할 수 있어요.",
        "en-US": "You have no holdings. You can run a diagnosis after placing a simulated trade.",
        "ja-JP": "保有銘柄がありません。模擬投資で注文した後に診断できます。",
        "zh-CN": "您当前没有持仓。在模拟投资下单后即可进行诊断。",
    },
}


def text(key: str, locale: str) -> str:
    """고정 문구. 그 언어 문구가 없으면 한국어."""
    entry = TEXTS[key]
    return entry.get(locale) or entry[DEFAULT_LOCALE]


def disclaimer(locale: str | None) -> str:
    return text("disclaimer", locale or DEFAULT_LOCALE)


# ---------- 금액 표기 ----------

_UNIT = {"조": Decimal(10) ** 12, "억": Decimal(10) ** 8, "만": Decimal(10) ** 4}
_CURRENCY = {"en-US": ("KRW ", ""), "ja-JP": ("", "ウォン"), "zh-CN": ("", "韩元")}


def _fmt(value: Decimal) -> str:
    """천 단위 쉼표, 소수는 있는 자리까지(뒤 0은 지운다)."""
    q = value.normalize()
    if q == q.to_integral():
        return f"{int(q):,}"
    whole, frac = f"{q:f}".split(".")
    return f"{int(whole):,}.{frac}"


def money_text(parts: list[tuple[str, str]], locale: str, won: bool) -> str | None:
    """한국어 금액 [(숫자, 단위)]("1조 2,000억원" → [("1","조"), ("2,000","억")])를 그 언어 표기로. 값은 그대로다.

    - en-US: 조·억·만이 있으면 trillion/billion/million으로 다시 읽는다(1조 2,000억 → 1.2 trillion). 없으면 쉼표 숫자 그대로.
    - ja-JP: 같은 한자 단위를 쓴다(조→兆, 억→億, 만→万).  zh-CN: 조→万亿, 억→亿, 만→万.
    원이 붙어 있었으면 통화 표기를 붙인다(en: KRW, ja: ウォン, zh: 韩元). 해석할 수 없으면 None."""
    try:
        values = [(Decimal(n.replace(",", "")), u) for n, u in parts]
    except InvalidOperation:
        return None
    prefix, suffix = _CURRENCY[locale] if won else ("", "")
    if locale == "en-US":
        if not any(u for _, u in values):
            return f"{prefix}{_fmt(values[0][0])}{suffix}".strip() if len(values) == 1 else None
        total = sum(v * (_UNIT[u] if u else 1) for v, u in values)
        sign = "-" if total < 0 else ""
        a = abs(total)
        for scale, word in ((Decimal(10) ** 12, "trillion"), (Decimal(10) ** 9, "billion"), (Decimal(10) ** 6, "million")):
            if a >= scale:
                return f"{sign}{prefix}{_fmt(a / scale)} {word}".strip()
        return f"{sign}{prefix}{_fmt(a)}".strip()
    names = {"ja-JP": {"조": "兆", "억": "億", "만": "万"}, "zh-CN": {"조": "万亿", "억": "亿", "만": "万"}}[locale]
    body = "".join(f"{_fmt(v)}{names[u] if u else ''}" for v, u in values)
    return f"{prefix}{body}{suffix}"
