"""영어·일본어·중국어 질문의 기간·방향·의도를 한국어 표준 표현으로 옮기는 질문 정규화(49차, 2026-11-13).

배경: 질문의 의도 판정(app.routing, app.period_parser, app.outlook, app.price_flow)은 한국어 표현만 본다. 48차까지 영어·일본어·중국어 질문은
대부분 LLM 경로로 가서 기간을 모델이 추측했다("最近5个交易日" -> period_days 1, "next month" -> 기본 1주일 등).
locale은 답 언어일 뿐이고 질문은 어느 언어로도 온다.

방식: 낱말을 찾아 바꾸는 사전이 아니라, 기간 표현은 (방향, 수, 단위)를 읽어 한국어 표준 표현으로 다시 쓴다.
- 과거 창: "over the last 5 trading days" / "直近5営業日" / "最近5个交易日" -> "최근 5거래일"
- 미래 창: "over the next 5 trading days" / "今後5営業日" / "未来5个交易日" -> "향후 5거래일" (전망 경로가 기간을 그대로 받는다)
- 상대 시점: "last month" / "先月" / "上个月" -> "지난달", "next week" / "来週" / "下周" -> "다음 주", "this year" -> "올해"
- 이동평균·수익률 창: "5-day and 20-day moving averages" / "5日・20日移動平均" / "5日和20日均线" -> "5일·20일 이동평균"
- 방향·의도(전망, 오를까/떨어질까, 사도 될까, 분석, 비교, 주가, 거래량, 뉴스, 공시, 추이)는 질문 끝에 한국어 표현으로 덧붙인다.
- 종목명은 등록명으로 바꾼다(app.company_names_i18n의 외국어 표기 -> "현대차"). LLM 도구 인자도 등록명을 쓰게 되어 퍼지 매칭 오류가 줄어든다.
같은 질문 안에서 "최근 5거래일"과 "향후 5거래일"은 방향이 달라 다른 경로로 간다(과거 조회 vs 전망).

정규화는 질문에 한글이 종목명(등록명) 밖에 없을 때만 한다. 한국어 질문은 한 글자도 바꾸지 않는다(기존 한국어 품질 유지).
정규화한 질문은 의도 판정과 LLM 입력에만 쓴다. 리포트에는 원래 질문이 저장된다(app.agent.ask_question).
지원하지 않는 표현(예: "in the second half of next year")은 바꾸지 않는다: 그 경우 기존 판정(대개 LLM 경로)이 그대로 동작하고,
전망 기간이 지원 범위 밖이면 app.outlook.horizon_in이 도구 없이 안내한다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.company_names_i18n import foreign_aliases

_HANGUL_RE = re.compile(r"[가-힣]")
_KANA_RE = re.compile(r"[぀-ヿ]")
_HAN_RE = re.compile(r"[一-鿿]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")

# ---------- 수 ----------
_EN_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "sixty": 60}
_CJK_DIGIT = {"一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cjk_number(s: str) -> int | None:
    """'5', '十', '二十', '二十五', '一', '两' -> 정수."""
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        tens = _CJK_DIGIT.get(a, 1) if a else 1
        ones = _CJK_DIGIT.get(b, 0) if b else 0
        return tens * 10 + ones
    return _CJK_DIGIT.get(s)


def _en_number(s: str) -> int | None:
    s = s.lower()
    return int(s) if s.isdigit() else _EN_NUM.get(s)


_EN_N = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|sixty)"
_CJK_N = r"(\d+|[一二两兩三四五六七八九十]+)"

# ---------- 단위 ----------
_EN_UNIT = r"(trading\s+days?|business\s+days?|sessions?|days?|weeks?|months?|quarters?|years?)"
_JA_UNIT = r"(営業日|取引日|日間|日|週間|週|か月|ヶ月|カ月|ヵ月|ケ月|か月間|ヶ月間|年間|年)"
_ZH_UNIT = r"(个交易日|個交易日|交易日|个工作日|個工作日|天|日|个星期|個星期|星期|周|週|个月|個月|月|年)"


def _unit_ko(unit: str) -> str:
    u = unit.lower()
    if "trading" in u or "business" in u or "session" in u or u in ("営業日", "取引日") or "交易日" in u or "工作日" in u:
        return "거래일"
    if u.startswith("day") or u in ("日", "日間", "天"):
        return "일"
    if u.startswith("week") or u in ("週間", "週", "周", "星期") or "星期" in u:
        return "주"
    if u.startswith("month") or u.startswith("quarter") or "月" in u:
        return "개월"
    return "년"


def _qty(n: int, unit: str) -> str:
    u = _unit_ko(unit)
    if unit.lower().startswith("quarter"):
        n *= 3
    return f"{n}{u}"


@dataclass
class Normalized:
    text: str
    changed: bool
    steps: list[str] = field(default_factory=list)  # 적용한 규칙(내부 기록·테스트용)


# ---------- 규칙 ----------
# (이름, 정규식, 바꿀 문자열을 만드는 함수). 기간·창은 제자리에서 바꾸고(한국어 판정이 숫자와 단위를 붙여 읽는다), 의도는 끝에 덧붙인다.
_I = re.IGNORECASE


def _r(pattern: str, flags: int = 0) -> re.Pattern:
    return re.compile(pattern, flags)


_PERIOD_RULES: list[tuple[str, re.Pattern, object]] = [
    # 영어: 미래 창
    ("en_future_window", _r(r"\b(?:over|in|during|for|within)?\s*(?:the\s+)?(?:next|coming|following|upcoming)\s+" + _EN_N + r"\s+" + _EN_UNIT + r"\b", _I),
     lambda m: f" 향후 {_qty(_en_number(m.group(1)), m.group(2))} "),
    ("en_in_n", _r(r"\bin\s+" + _EN_N + r"\s+" + _EN_UNIT + r"\b(?!\s+ago)", _I), lambda m: f" {_qty(_en_number(m.group(1)), m.group(2))} 뒤 "),
    # 영어: 과거 창
    ("en_past_window", _r(r"\b(?:over|in|during|for|within)?\s*(?:the\s+)?(?:last|past|previous|recent|latest|prior)\s+" + _EN_N + r"\s+" + _EN_UNIT + r"\b", _I),
     lambda m: f" 최근 {_qty(_en_number(m.group(1)), m.group(2))} "),
    ("en_past_unit", _r(r"\b(?:over|in|during|for)\s+the\s+(?:last|past)\s+(week|month|quarter|year)\b", _I), lambda m: f" 최근 {_qty(1, m.group(1))} "),
    ("en_next_unit", _r(r"\b(?:over\s+|in\s+|during\s+)?(?:the\s+)?(?:next|coming)\s+(week|month|quarter)\b", _I),
     lambda m: {"week": " 다음 주 ", "month": " 다음 달 ", "quarter": " 향후 3개월 "}[m.group(1).lower()]),
    ("en_next_year", _r(r"\bnext\s+year\b|\bin\s+the\s+long\s+(?:run|term)\b", _I), lambda m: " 내년 "),
    ("en_last_unit", _r(r"\blast\s+(week|month|year)\b", _I), lambda m: {"week": " 지난주 ", "month": " 지난달 ", "year": " 작년 "}[m.group(1).lower()]),
    ("en_this", _r(r"\b(?:this\s+year|year[-\s]to[-\s]date|ytd)\b", _I), lambda m: " 올해 "),
    ("en_this_month", _r(r"\bthis\s+month\b", _I), lambda m: " 이번 달 "),
    ("en_this_week", _r(r"\bthis\s+week\b", _I), lambda m: " 이번 주 "),
    ("en_today", _r(r"\btoday\b", _I), lambda m: " 오늘 "),
    ("en_yesterday", _r(r"\byesterday\b", _I), lambda m: " 어제 "),
    # 일본어
    ("ja_future_window", _r(r"(?:今後|これから|向こう|この先)\s*" + _CJK_N + r"\s*" + _JA_UNIT), lambda m: f" 향후 {_qty(_cjk_number(m.group(1)), m.group(2))} "),
    ("ja_after", _r(_CJK_N + r"\s*" + _JA_UNIT + r"\s*(?:後|先)"), lambda m: f" {_qty(_cjk_number(m.group(1)), m.group(2))} 뒤 "),
    ("ja_past_window", _r(r"(?:直近|過去|最近|この|ここ)\s*" + _CJK_N + r"\s*" + _JA_UNIT), lambda m: f" 최근 {_qty(_cjk_number(m.group(1)), m.group(2))} "),
    ("ja_relative", _r(r"来週|来月|来年|先週|先月|昨年|去年|今年|今月|今週|今日|昨日|本日"),
     lambda m: {"来週": " 다음 주 ", "来月": " 다음 달 ", "来年": " 내년 ", "先週": " 지난주 ", "先月": " 지난달 ", "昨年": " 작년 ",
                "去年": " 작년 ", "今年": " 올해 ", "今月": " 이번 달 ", "今週": " 이번 주 ", "今日": " 오늘 ", "本日": " 오늘 ",
                "昨日": " 어제 "}[m.group(0)]),
    # 중국어
    ("zh_future_window", _r(r"(?:未来|今后|今後|接下来|接下來|往后|往後)\s*" + _CJK_N + r"\s*" + _ZH_UNIT), lambda m: f" 향후 {_qty(_cjk_number(m.group(1)), m.group(2))} "),
    ("zh_after", _r(_CJK_N + r"\s*" + _ZH_UNIT + r"\s*(?:后|後|之后|之後)"), lambda m: f" {_qty(_cjk_number(m.group(1)), m.group(2))} 뒤 "),
    ("zh_past_window", _r(r"(?:最近|过去|過去|近|前)\s*" + _CJK_N + r"\s*" + _ZH_UNIT), lambda m: f" 최근 {_qty(_cjk_number(m.group(1)), m.group(2))} "),
    ("zh_relative", _r(r"下周|下週|下星期|下个月|下個月|下月|明年|上周|上週|上星期|上个月|上個月|去年|今年|本月|这个月|這個月|本周|这周|今天|昨天"),
     lambda m: {"下周": " 다음 주 ", "下週": " 다음 주 ", "下星期": " 다음 주 ", "下个月": " 다음 달 ", "下個月": " 다음 달 ", "下月": " 다음 달 ",
                "明年": " 내년 ", "上周": " 지난주 ", "上週": " 지난주 ", "上星期": " 지난주 ", "上个月": " 지난달 ", "上個月": " 지난달 ",
                "去年": " 작년 ", "今年": " 올해 ", "本月": " 이번 달 ", "这个月": " 이번 달 ", "這個月": " 이번 달 ", "本周": " 이번 주 ",
                "这周": " 이번 주 ", "今天": " 오늘 ", "昨天": " 어제 "}[m.group(0)]),
]

# 이동평균·수익률 창: 숫자 목록을 한국어 창 표기로 제자리에서 바꾼다("5-day and 20-day" -> "5일·20일").
_EN_DAY_LIST_RE = re.compile(r"\b(\d+)\s*-?\s*days?\b((?:\s*(?:,|and|&|/)\s*(\d+)\s*-?\s*days?\b)*)", _I)
_CJK_DAY_LIST_RE = re.compile(r"(\d+)\s*日((?:\s*(?:・|、|,|，|と|和|与|與|及)\s*\d+\s*日)*)")
_MA_WORDS = [(re.compile(r"\b(?:simple\s+)?moving\s+averages?\b|\bSMA\b", _I), " 이동평균 "),
             (re.compile(r"移動平均線?|移动平均线?|移動平均線|均线|均線"), " 이동평균 ")]
_RET_WORDS = [(re.compile(r"\breturns?\b|\bperformance\s+over\b", _I), " 수익률 "),
              (re.compile(r"騰落率|リターン|収益率|收益率|涨跌幅|漲跌幅|回报率|回報率"), " 수익률 ")]
_MA_OR_RET_RE = re.compile(r"moving\s+average|\bSMA\b|\breturns?\b|移動平均|移动平均|均线|均線|騰落率|リターン|収益率|收益率|涨跌幅|漲跌幅|回报|回報", _I)

# 의도(끝에 덧붙인다). 같은 의도는 한 번만.
_INTENT_RULES: list[tuple[str, re.Pattern]] = [
    ("오를까", re.compile(r"\bwill\b[^?.]{0,60}\b(?:rise|go\s+up|climb|gain|increase|rebound|rally|recover)\b|\bgo(?:ing)?\s+up\b"
                       r"|上がり(?:ます|そう)|上がる(?:か|でしょう|の)|上昇(?:します|する)(?:か|でしょう)|値上がり"
                       r"|会涨|會漲|能涨|能漲|会上涨|會上漲|上涨吗|上漲嗎|涨吗|漲嗎", _I)),
    ("떨어질까", re.compile(r"\bwill\b[^?.]{0,60}\b(?:fall|drop|go\s+down|decline|slide|plunge|sink)\b|\bgo(?:ing)?\s+down\b"
                        r"|下がり(?:ます|そう)|下がる(?:か|でしょう|の)|下落(?:します|する)(?:か|でしょう)|値下がり"
                        r"|会跌|會跌|会下跌|會下跌|下跌吗|下跌嗎|跌吗|跌嗎", _I)),
    ("전망", re.compile(r"\b(?:outlook|forecast|prospects?|predict(?:ion)?|projection)\b|見通し|予想|予測|展望|先行き"
                      r"|前景|预测|預測|走势预测|走勢預測|预期|預期", _I)),
    ("사도 될까", re.compile(r"\bshould\s+i\s+(?:buy|invest)\b|\b(?:good|right)\s+time\s+to\s+buy\b|\bworth\s+buying\b"
                         r"|買うべき|買っても|買い時|该不该买|該不該買|值得买|值得買|可以买吗|可以買嗎|能买吗|能買嗎", _I)),
    ("팔아야 할까", re.compile(r"\bshould\s+i\s+sell\b|売るべき|売り時|该不该卖|該不該賣|要不要卖|要不要賣", _I)),
    ("분석", re.compile(r"\banaly[sz]e\b|\banalysis\b|分析", _I)),
    ("비교", re.compile(r"\bcompare\b|\bcompared\s+(?:to|with)\b|\bversus\b|\bvs\.?\b|比較|比べ|比较|对比|對比", _I)),
    ("주가", re.compile(r"\b(?:stock|share)\s+prices?\b|\bprice\s+of\b|株価|股价|股價", _I)),
    ("거래량", re.compile(r"\b(?:trading\s+)?volume\b|出来高|成交量", _I)),
    ("뉴스", re.compile(r"\bnews\b|ニュース|新闻|新聞", _I)),
    ("공시", re.compile(r"\b(?:filings?|disclosures?)\b|開示|公告|公示", _I)),
    ("추이", re.compile(r"\bhow\s+(?:did|has|have)\b[^?.]{0,60}\b(?:move|moved|perform|performed|trade|traded)\b|\btrend\b"
                      r"|推移|値動き|動き|走势|走勢", _I)),
    ("최고가 최저가", re.compile(r"\bhigh(?:est)?\s+and\s+low(?:est)?\b|\bhighs?\s+and\s+lows?\b|高値|安値|最高价|最低價|最低价|最高價", _I)),
]


def _strip_names(text: str, names: list[str]) -> str:
    out = text
    for n in sorted(names, key=len, reverse=True):
        if n and len(n) >= 2:
            out = out.replace(n, " ")
    return out


def is_foreign_question(question: str, names: list[str] | None = None) -> bool:
    """종목명(등록명·외국어 표기)을 빼고 한글이 없고, 영어 낱말이나 가나·한자가 있으면 True."""
    rest = _strip_names(unicodedata.normalize("NFKC", question or ""), list(names or []) + list(foreign_aliases()))
    if _HANGUL_RE.search(rest):
        return False
    return bool(_KANA_RE.search(rest) or _HAN_RE.search(rest) or _LATIN_WORD_RE.search(rest))


def _replace_names(text: str) -> tuple[str, list[str]]:
    """외국어 종목 표기를 등록명으로(긴 표기부터, 영문은 단어 경계)."""
    steps = []
    for alias, name in sorted(foreign_aliases().items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.match(r"[A-Za-z0-9]", alias):
            pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])(?:'s|’s)?", re.IGNORECASE)
        else:
            pat = re.compile(re.escape(alias))
        new = pat.sub(f" {name} ", text)
        if new != text:
            steps.append(f"name:{alias}->{name}")
            text = new
    return text, steps


def _windows(text: str) -> tuple[str, list[str]]:
    """이동평균·수익률 질문의 일수 목록을 한국어 창 표기로."""
    if not _MA_OR_RET_RE.search(text):
        return text, []
    steps = []

    def en(m: re.Match) -> str:
        nums = [m.group(1)] + re.findall(r"(\d+)\s*-?\s*days?", m.group(2) or "", _I)
        steps.append("window:" + "/".join(nums))
        return " " + "·".join(f"{n}일" for n in nums) + " "

    def cjk(m: re.Match) -> str:
        nums = [m.group(1)] + re.findall(r"(\d+)\s*日", m.group(2) or "")
        steps.append("window:" + "/".join(nums))
        return " " + "·".join(f"{n}일" for n in nums) + " "

    text = _EN_DAY_LIST_RE.sub(en, text)
    text = _CJK_DAY_LIST_RE.sub(cjk, text)
    for rx, ko in _MA_WORDS + _RET_WORDS:
        text, n = rx.subn(ko, text)
        if n:
            steps.append("window_word:" + ko.strip())
    return text, steps


def normalize(question: str, names: list[str] | None = None) -> Normalized:
    """정규화한 질문. 한국어 질문(종목명 밖에 한글이 있음)이나 바꿀 것이 없는 질문은 그대로(changed=False)."""
    q = question or ""
    if not is_foreign_question(q, names):
        return Normalized(q, False)
    text = unicodedata.normalize("NFKC", q)
    text, steps = _replace_names(text)
    text, win_steps = _windows(text)
    steps += win_steps
    for name, rx, repl in _PERIOD_RULES:
        new = rx.sub(lambda m: repl(m) if repl(m) is not None else m.group(0), text)
        if new != text:
            steps.append(f"period:{name}")
            text = new
    intents = [ko for ko, rx in _INTENT_RULES if rx.search(q)]
    if intents:
        steps += [f"intent:{k}" for k in intents]
        text = text.rstrip() + " (" + " ".join(intents) + ")"
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    changed = text != q.strip()
    return Normalized(text if changed else q, changed, steps)
