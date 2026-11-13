"""검증을 거친 한국어 답변을 다른 언어로 옮기는 보호 번역(2026-11-05).

LLM은 숫자·날짜·종목명·기사/공시 제목을 볼 수 없다: 코드가 먼저 그 값들을 자리표시자(⟦0⟧, ⟦1⟧ …)로 바꾸고, LLM은 나머지 문장만
번역한 뒤, 코드가 자리표시자를 원래 값으로 되돌린다. 그래서 번역이 숫자·날짜·이름을 바꾸거나 새로 만들 수 없다.

번역문은 아래를 모두 만족해야 쓴다(하나라도 어기면 한 번 다시 시키고, 그래도 어기면 한국어 원문 + 안내 문구):
1. 자리표시자가 원문과 같은 집합이고 각각 정확히 한 번씩 나온다.
2. 자리표시자 밖에 숫자가 없다(LLM이 숫자를 쓰지 않았다).
3. 자리표시자 밖에 한글이 없다(덜 번역되지 않았다). 한글 종목명은 자리표시자 안에 있어 원문 그대로 남는다.
4. 그 언어의 글자다: en-US는 가나·한자가 없고, ja-JP는 가나가 있고, zh-CN은 한자가 있고 가나가 없다.
5. 제목 줄(#) 수가 원문과 같다(답변 구조가 유지됐다).

금액은 값은 그대로 두고 표기만 언어에 맞춘다(app.i18n.money_text: "1조 2,000억원" → "KRW 1.2 trillion" / "1兆2,000億ウォン").
"""

import logging
import re
from dataclasses import dataclass, field

import ollama

from app.i18n import LANGUAGE_NAMES, money_text, text
from app.llm_settings import call_options, chat_model

logger = logging.getLogger(__name__)

TRANSLATE_TIMEOUT_S = 120
CHUNK_CHARS = 1200
MAX_ATTEMPTS = 2

STATUS_TRANSLATED = "translated"
STATUS_LOCALIZED = "localized"  # 코드가 그 언어로 바로 쓴 답(번역 없음)
STATUS_FALLBACK = "fallback_original"

_TOKEN_RE = re.compile(r"⟦(\d+)⟧")
_HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")
_KANA_RE = re.compile(r"[぀-ヿ]")
_HAN_RE = re.compile(r"[一-鿿]")
_DIGIT_RE = re.compile(r"[0-9０-９]")

_URL_RE = re.compile(r"https?://[^\s)\]>]+")
_NUM = r"[+-]?\d[\d,]*(?:\.\d+)?"
# 금액: "1조 2,000억원", "5,000억", "1,414,000원으로", "3만 주". 단위 뒤에는 조사(은·는·이·가·으로 …)나 한글이 아닌 글자만 올 수 있다
# ("5조정", "1억명"은 금액이 아니다). "원" 뒤의 원유·원자재·원인 같은 낱말도 금액이 아니다.
_AFTER_UNIT = r"(?=[은는이가을를의에와과도로으만씩]|[^가-힣]|$)"
_MONEY_RE = re.compile(
    rf"(?<![\w.])({_NUM})\s*(조|억|만)(?:\s*({_NUM})\s*(억|만))?(?:\s*({_NUM})\s*(만))?\s*(원(?![유자인래료칙화형])|{_AFTER_UNIT})"
    rf"|(?<![\w.])({_NUM})\s*(원)(?![유자인래료칙화형]){_AFTER_UNIT}"
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?|\d{4}\.\d{1,2}\.\d{1,2}\.?|\d{4}년\s*\d{1,2}월\s*\d{1,2}일")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_NUMBER_RE = re.compile(rf"{_NUM}(?:%p|%)?")

# 언어별 표준 금융 용어(번역 프롬프트에 그 청크에 나온 것만 넣는다). 프론트 번역 리소스의 용어 사전과 같은 값이다.
FINANCE_TERMS: dict[str, dict[str, str]] = {
    "상승 쪽": {"en-US": "upward bias", "ja-JP": "上昇寄り", "zh-CN": "偏上涨"},
    "하락 쪽": {"en-US": "downward bias", "ja-JP": "下落寄り", "zh-CN": "偏下跌"},
    "중립": {"en-US": "neutral", "ja-JP": "中立", "zh-CN": "中性"},
    "판단 보류": {"en-US": "judgment withheld", "ja-JP": "判断保留", "zh-CN": "暂不判断"},
    "판단 유보": {"en-US": "judgment withheld", "ja-JP": "判断保留", "zh-CN": "暂不判断"},
    "신뢰도": {"en-US": "confidence", "ja-JP": "信頼度", "zh-CN": "可信度"},
    "묶음": {"en-US": "signal group", "ja-JP": "シグナル群", "zh-CN": "信号组"},
    "거래일": {"en-US": "trading days", "ja-JP": "取引日", "zh-CN": "个交易日"},
    "영업일": {"en-US": "business days", "ja-JP": "営業日", "zh-CN": "个工作日"},
    "종가": {"en-US": "closing price", "ja-JP": "終値", "zh-CN": "收盘价"},
    "등락률": {"en-US": "rate of change", "ja-JP": "騰落率", "zh-CN": "涨跌幅"},
    "수익률": {"en-US": "return", "ja-JP": "収益率", "zh-CN": "收益率"},
    "거래량": {"en-US": "trading volume", "ja-JP": "出来高", "zh-CN": "成交量"},
    "거래대금": {"en-US": "trading value", "ja-JP": "売買代金", "zh-CN": "成交额"},
    "시가총액": {"en-US": "market capitalization", "ja-JP": "時価総額", "zh-CN": "市值"},
    "이동평균": {"en-US": "moving average", "ja-JP": "移動平均", "zh-CN": "移动平均线"},
    "국면": {"en-US": "market phase", "ja-JP": "局面", "zh-CN": "市场阶段"},
    "추세": {"en-US": "trend", "ja-JP": "トレンド", "zh-CN": "趋势"},
    "과거 검증": {"en-US": "historical backtest", "ja-JP": "過去検証", "zh-CN": "历史回测"},
    "표본": {"en-US": "sample size", "ja-JP": "サンプル数", "zh-CN": "样本数"},
    "통계적으로 유의": {"en-US": "statistically significant", "ja-JP": "統計的に有意", "zh-CN": "具有统计显著性"},
    "공시": {"en-US": "regulatory filing", "ja-JP": "開示", "zh-CN": "公告"},
    "잠정실적": {"en-US": "preliminary earnings", "ja-JP": "暫定業績", "zh-CN": "初步业绩"},
    "영업이익": {"en-US": "operating profit", "ja-JP": "営業利益", "zh-CN": "营业利润"},
    "매출": {"en-US": "revenue", "ja-JP": "売上高", "zh-CN": "营业收入"},
    "순이익": {"en-US": "net income", "ja-JP": "純利益", "zh-CN": "净利润"},
    "분기": {"en-US": "quarter", "ja-JP": "四半期", "zh-CN": "季度"},
    "코스피": {"en-US": "KOSPI", "ja-JP": "KOSPI", "zh-CN": "KOSPI"},
    "코스닥": {"en-US": "KOSDAQ", "ja-JP": "KOSDAQ", "zh-CN": "KOSDAQ"},
    "환율": {"en-US": "exchange rate", "ja-JP": "為替レート", "zh-CN": "汇率"},
    "변동성": {"en-US": "volatility", "ja-JP": "ボラティリティ", "zh-CN": "波动率"},
    "기준일": {"en-US": "base date", "ja-JP": "基準日", "zh-CN": "基准日"},
    "구성종목": {"en-US": "constituents", "ja-JP": "構成銘柄", "zh-CN": "成分股"},
    "비중": {"en-US": "weight", "ja-JP": "比率", "zh-CN": "权重"},
    "투자 권유": {"en-US": "investment recommendation", "ja-JP": "投資勧誘", "zh-CN": "投资劝诱"},
    "모의투자": {"en-US": "simulated trading", "ja-JP": "模擬投資", "zh-CN": "模拟投资"},
    "레버리지": {"en-US": "leveraged", "ja-JP": "レバレッジ", "zh-CN": "杠杆"},
    "인버스": {"en-US": "inverse", "ja-JP": "インバース", "zh-CN": "反向"},
    "골든크로스": {"en-US": "golden cross", "ja-JP": "ゴールデンクロス", "zh-CN": "金叉"},
    "데드크로스": {"en-US": "dead cross", "ja-JP": "デッドクロス", "zh-CN": "死叉"},
}

# 48차: 번역 프롬프트에만 넣는 표현 힌트(프론트 번역 리소스의 용어 사전 FINANCE_TERMS와는 따로다).
# "하루"를 "1日"로 옮겨 원문에 없는 숫자가 생겨 검증에 떨어졌다(-> 데이터 요약). 숫자 없는 표현을 준다.
PROMPT_HINTS: dict[str, dict[str, str]] = {
    "하루 등락률": {"en-US": "daily change", "ja-JP": "当日の騰落率", "zh-CN": "当日涨跌幅"},
    "하루": {"en-US": "that day", "ja-JP": "当日", "zh-CN": "当日"},
}

# 48차: 답변 구조의 정해진 섹션 제목(app.agent.ANSWER_STRUCTURE_INSTRUCTION과 LLM이 자주 쓰는 변형)은 코드가 옮긴다.
# 모델이 제목 줄을 옮기지 않고 남겨 "한글 남음"으로 답 전체가 데이터 요약이 되는 일이 가장 흔했다(36문항 중 7건).
HEADINGS: dict[str, dict[str, str]] = {
    "주가 동향": {"en-US": "Price Trend", "ja-JP": "株価動向", "zh-CN": "股价走势"},
    "원인 분석": {"en-US": "Cause Analysis", "ja-JP": "原因分析", "zh-CN": "原因分析"},
    "뉴스·공시 근거": {"en-US": "News and Filings", "ja-JP": "ニュース・開示の根拠", "zh-CN": "新闻与公告依据"},
    "뉴스 · 공시 근거": {"en-US": "News and Filings", "ja-JP": "ニュース・開示の根拠", "zh-CN": "新闻与公告依据"},
    "관련 뉴스": {"en-US": "Related News", "ja-JP": "関連ニュース", "zh-CN": "相关新闻"},
    "관련 공시": {"en-US": "Related Filings", "ja-JP": "関連開示", "zh-CN": "相关公告"},
    "시장 상황": {"en-US": "Market Conditions", "ja-JP": "市場の状況", "zh-CN": "市场状况"},
    "위험요인": {"en-US": "Risk Factors", "ja-JP": "リスク要因", "zh-CN": "风险因素"},
    "위험 요인": {"en-US": "Risk Factors", "ja-JP": "リスク要因", "zh-CN": "风险因素"},
}
_HEADING_RE = re.compile(r"^(#{1,6}[ \t]*)(" + "|".join(re.escape(h) for h in sorted(HEADINGS, key=len, reverse=True)) + r")[ \t]*$", re.M)
# 48차: 모델이 자리표시자 괄호를 바꿔 쓴 것("⟦2⟩", "[3]", "【4】", "⟦ 5 ⟧"). 번호가 원문 자리표시자일 때만 같은 자리표시자로 되돌린다.
# 값은 코드가 넣으므로 숫자가 바뀌지 않는다. 괄호 없이 숫자만 쓴 것("⟦3⟧" -> "3")은 고치지 않는다(번호인지 값인지 알 수 없다).
_LOOSE_TOKEN_RE = re.compile(r"[⟦\[【〚［〔]\s*(\d{1,3})\s*[⟧⟩\]】〛］〕]")

_STYLE = {
    "en-US": "Write clear, natural English.",
    "ja-JP": "自然な日本語の「です・ます」調で書いてください。",
    "zh-CN": "只使用简体中文汉字（不要使用繁体字或日文假名）。",
}


@dataclass
class Masked:
    text: str
    values: list[str] = field(default_factory=list)

    def token(self, value: str) -> str:
        self.values.append(value)
        return f"⟦{len(self.values) - 1}⟧"


@dataclass
class Result:
    text: str
    status: str
    failures: list[str] = field(default_factory=list)


# ---------- 가리기 ----------


def _money(m: re.Match, locale: str) -> str | None:
    g = m.groups()
    if g[7] is not None:  # "1,414,000원"
        return money_text([(g[7], "")], locale, won=True)
    parts = [(g[i], g[i + 1]) for i in (0, 2, 4) if g[i] is not None]
    return money_text(parts, locale, won=bool(g[6]))


def protected_terms(records: list[dict], names: list[str] | None = None) -> list[str]:
    """번역하지 않을 문자열: tool 결과의 종목명·기사/공시/사건 제목 + 주어진 종목명(예: DB의 등록명 중 답변에 나온 것)."""
    out = set(names or [])

    def walk(v, depth=0):
        if depth > 6:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                if k in ("company_name", "title", "name", "etf_name", "report_nm") and isinstance(x, str):
                    out.add(x)
                else:
                    walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)

    for r in records or []:
        walk(r.get("result"))
    return sorted((t for t in out if t and len(t.strip()) >= 2), key=len, reverse=True)


def _sub_outside(regex: re.Pattern, repl, text_: str) -> str:
    """이미 만든 자리표시자(⟦n⟧) 밖에서만 바꾼다(자리표시자 안의 숫자를 다시 가리지 않게)."""
    parts = re.split(r"(⟦\d+⟧)", text_)
    return "".join(part if i % 2 else regex.sub(repl, part) for i, part in enumerate(parts))


# 보호 문자열 경계(43차): 앞 글자가 한글이 아니고, 뒤 글자가 한글이 아니거나 조사·서술격 조사의 첫 글자일 때만 보호한다.
_JOSA_FIRST = "은는이가을를의에와과도로으만보처까부나라랑께입"
_TERM_BEFORE = r"(?<![가-힣])"
_TERM_AFTER = rf"(?=[^가-힣]|[{_JOSA_FIRST}]|$)"


def mask(source: str, locale: str, terms: list[str]) -> Masked:
    """URL → 보호 문자열(긴 것부터) → 금액 → 날짜 → 시각 → 숫자 순서로 자리표시자로 바꾼다."""
    m = Masked(text=source)
    m.text = _HEADING_RE.sub(lambda x: x.group(1) + m.token(HEADINGS[x.group(2)][locale]), m.text)
    m.text = _sub_outside(_URL_RE, lambda x: m.token(x.group(0)), m.text)
    present = [t for t in terms if t in m.text]
    if present:
        # 43차(2026-11-11): 단어 안의 부분 문자열은 가리지 않는다. 앞에 한글이 붙지 않고, 뒤가 한글이 아니거나 조사로 시작할 때만.
        # 전에는 "반도체 테스트"의 '테스'(등록 종목명)까지 가려 "⟦n⟧트"가 되었고, 번역 뒤 되돌리면 한국어 조각 '테스'가 남았다
        # (중국어 답 "半导体테스的…"가 translated로 통과).
        alt = re.compile(_TERM_BEFORE + "(?:" + "|".join(re.escape(t) for t in present) + ")" + _TERM_AFTER)
        m.text = _sub_outside(alt, lambda x: m.token(x.group(0)), m.text)

    def money(x):
        localized = _money(x, locale)
        return m.token(localized) if localized is not None else x.group(0)

    m.text = _sub_outside(_MONEY_RE, money, m.text)
    for regex in (_DATE_RE, _TIME_RE, _NUMBER_RE):
        m.text = _sub_outside(regex, lambda x: m.token(x.group(0)), m.text)
    return m


def unmask(translated: str, values: list[str]) -> str:
    return _TOKEN_RE.sub(lambda x: values[int(x.group(1))], translated)


def repair_tokens(source_masked: str, translated: str) -> str:
    """번역문에서 괄호만 바뀐 자리표시자를 원래 모양으로(원문에 있는 번호만). 다른 글자는 바꾸지 않는다."""
    valid = set(_TOKEN_RE.findall(source_masked))

    def fix(m: re.Match) -> str:
        return f"⟦{m.group(1)}⟧" if m.group(1) in valid else m.group(0)

    return _LOOSE_TOKEN_RE.sub(fix, translated)


# ---------- 검증 ----------


def check(source_masked: str, translated: str, locale: str) -> list[str]:
    """번역문(자리표시자 상태)의 위반 목록. 비어 있으면 통과."""
    problems = []
    want = sorted(int(i) for i in _TOKEN_RE.findall(source_masked))
    got = sorted(int(i) for i in _TOKEN_RE.findall(translated))
    if want != got:
        problems.append(f"placeholders {len(got)}/{len(want)}")
    rest = _TOKEN_RE.sub("", translated)
    if _DIGIT_RE.search(rest):
        problems.append("digits outside placeholders")
    if _HANGUL_RE.search(rest):
        problems.append("hangul left")
    if locale == "en-US" and (_KANA_RE.search(rest) or _HAN_RE.search(rest)):
        problems.append("cjk in english")
    if locale == "ja-JP" and not _KANA_RE.search(rest):
        problems.append("no kana")
    if locale == "zh-CN" and (_KANA_RE.search(rest) or not _HAN_RE.search(rest)):
        problems.append("not simplified chinese")
    heads = lambda s: sum(1 for line in s.splitlines() if line.lstrip().startswith("#"))  # noqa: E731
    if heads(source_masked) != heads(translated):
        problems.append("heading count")
    return problems


# ---------- LLM ----------


def _chat(messages: list[dict]) -> str:
    """번역 LLM 호출(테스트가 바꿔 끼운다). 온도 0."""
    resp = ollama.Client(timeout=TRANSLATE_TIMEOUT_S).chat(
        model=chat_model(), messages=messages, options={"temperature": 0, "num_predict": 2048}, **call_options()
    )
    return resp["message"]["content"]


def _prompt(masked: str, locale: str) -> list[dict]:
    lang = LANGUAGE_NAMES[locale]
    terms = [f"- {ko} → {t[locale]}" for ko, t in {**FINANCE_TERMS, **PROMPT_HINTS}.items() if ko in masked]
    system = (
        f"You are a professional financial translator. Translate the user's Korean text into {lang}.\n"
        "Rules:\n"
        "1. Tokens like ⟦0⟧, ⟦12⟧ are protected values (numbers, dates, money, company names, news titles, links). "
        "Copy every token exactly once, exactly as written. Never translate, change, split, merge, or drop a token.\n"
        "2. Never write digits yourself. Every number is already inside a token.\n"
        "3. Keep the same line breaks and markdown: headings (#), list markers (-, 1.), bold (**), tables (|).\n"
        "4. Translate only. Do not add or remove information, explanations, opinions, or notes. Output only the translation.\n"
        "5. Korean particles attached to a token (e.g. ⟦3⟧는, ⟦3⟧의) belong to the sentence, not the token.\n"
        f"6. {_STYLE[locale]}"
    )
    if terms:
        system += "\nUse these standard financial terms:\n" + "\n".join(terms)
    return [{"role": "system", "content": system}, {"role": "user", "content": masked}]


def _translate_chunk(masked: str, locale: str) -> tuple[str | None, list[str]]:
    """(번역문 또는 None, 시도별 위반). 자리표시자 상태로 주고받는다."""
    messages = _prompt(masked, locale)
    failures = []
    for _ in range(MAX_ATTEMPTS):
        try:
            out = _chat(messages).strip()
        except Exception as e:  # noqa: BLE001 - LLM 장애는 한국어 원문으로 대신한다
            failures.append(f"llm {type(e).__name__}")
            return None, failures
        out = repair_tokens(masked, out)
        problems = check(masked, out, locale)
        if not problems:
            return out, failures
        failures.append(", ".join(problems))
        messages = messages + [
            {"role": "assistant", "content": out},
            {"role": "user", "content": "That output broke the rules (" + ", ".join(problems)
             + "). Translate the original text again. Copy every ⟦n⟧ token exactly once, write no digits, keep line breaks."},
        ]
    return None, failures


def _translate_lines(masked: str, locale: str) -> tuple[str | None, list[str]]:
    """자리표시자 상태의 문단을 줄 단위로 옮긴다. 옮길 한글이 없는 줄(빈 줄·자리표시자만·코드가 옮긴 제목)은 그대로 둔다."""
    lines, failures = [], []
    for line in masked.split("\n"):
        if not _HANGUL_RE.search(_TOKEN_RE.sub("", line)):
            lines.append(line)
            continue
        out, f = _translate_chunk(line, locale)
        failures += f
        if out is None:
            return None, failures
        lines.append(out)
    return "\n".join(lines), failures


def _chunks(source: str) -> list[str]:
    """빈 줄로 나눈 문단을 CHUNK_CHARS 안쪽으로 묶는다(문단 사이 빈 줄은 유지)."""
    out, cur = [], []
    for block in source.split("\n\n"):
        if cur and len("\n\n".join(cur + [block])) > CHUNK_CHARS:
            out.append("\n\n".join(cur))
            cur = []
        cur.append(block)
    if cur:
        out.append("\n\n".join(cur))
    return out


def translate(source: str, locale: str, terms: list[str] | None = None) -> Result:
    """한국어 source를 locale로. 실패하면 한국어 원문 + 안내(STATUS_FALLBACK). 번역에 성공하면 끝에 원문 안내를 붙인다."""
    if not source or not _HANGUL_RE.search(source):
        return Result(source, STATUS_TRANSLATED)
    pieces, failures = [], []
    for chunk in _chunks(source):
        if not _HANGUL_RE.search(chunk):
            pieces.append(chunk)
            continue
        m = mask(chunk, locale, terms or [])
        if not _HANGUL_RE.search(_TOKEN_RE.sub("", m.text)):  # 가리고 나면 번역할 글이 없다(종목명·숫자뿐)
            pieces.append(unmask(m.text, m.values))
            continue
        out, chunk_failures = _translate_chunk(m.text, locale)
        failures += chunk_failures
        if out is None and "\n" in m.text.strip() and not any(f.startswith("llm ") for f in chunk_failures):
            # 48차: 문단 전체가 두 번 실패하면 곧바로 데이터 요약으로 가지 않고 줄마다 다시 옮긴다(입력이 짧을수록 자리표시자를 덜 잃는다).
            # 각 줄도 같은 검증(자리표시자·숫자·한글·문자)을 통과해야 하고, 한 줄이라도 실패하면 전과 같이 폴백이다.
            out, line_failures = _translate_lines(m.text, locale)
            failures += [f"line: {f}" for f in line_failures]
        if out is None:
            logger.info("번역 검증 실패(%s): %s", locale, chunk_failures)
            return Result(f"{text('translation_fallback', locale)}\n\n{source}", STATUS_FALLBACK, failures)
        pieces.append(unmask(out, m.values))
    return Result("\n\n".join(pieces) + f"\n\n{text('translation_note', locale)}", STATUS_TRANSLATED, failures)
