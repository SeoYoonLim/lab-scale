"""사건(뉴스·공시) 이후 주가 반응 질문의 답변 구성과 검증(DB 없음). app/etf.py의 ETF 답변 처리와 같은 구조다.

원칙: 숫자와 표는 코드가 event_reaction_tool 결과로 쓴다(format_event_section). LLM은 짧은 해석만 쓰고, 그 문장은
check_event_answer가 결과와 대조해 맞지 않는 문장을 지운다. 지운 뒤 남는 문장이 없어도 코드가 쓴 섹션만으로 답이 된다.
사건 이후의 움직임은 시간적 선후관계일 뿐이므로 인과를 단정하는 문장은 지운다.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.etf import NUMBER_MASK, _norm, _sentences, _variants, _without
from app.event_reaction import HORIZONS, summarize

INDEX_NAMES = {"KS11": "코스피", "KQ11": "코스닥"}
SECTION_TITLE = "## 사건 이후 주가 반응"
INTERPRETATION_TITLE = "## 해석"
DISCLOSURE_TIME_NOTE = "※ 공시는 접수일 기준이며 시각 정보가 없어 장중 반응 여부는 알 수 없습니다."
NEWS_DATE_ONLY_NOTE = "※ 발행 시각이 없는 뉴스는 날짜 기준(공시와 같은 방식)으로 계산했습니다."
SMALL_SAMPLE = 3

# 최종 답변 단계에만 넣는 규칙(게이트를 통과한 질문에만). 예시 수치를 넣지 않는다 - 예시 속 숫자를 답변에 베끼는 일이 있었다
# (agent.FEW_SHOT_MESSAGES 주석 참고).
EVENT_ANSWER_RULES = (
    "이 질문은 뉴스·공시(사건) 이후의 주가 반응을 묻는다. 사건별 수치와 요약, 면책 문구는 코드가 이미 답변에 넣는다. "
    "너는 '## ' 제목이나 목록 없이 한두 문장의 짧은 해석만 한국어로 써라. 다음을 반드시 지켜라. "
    "(1) 주가 움직임을 사건과 인과로 연결하지 마라. '때문에', '영향으로', '덕분에', '탓에', '원인은', '~로 인해', '반영' 같은 "
    "표현을 쓰지 마라. 'OO 공시 이후 N거래일간 X% 움직였다'처럼 시간 순서로만 서술하라. "
    "(2) 조회 결과에 있는 수치와 날짜만 그대로 써라. 새로 계산하거나 바꾼 수치를 쓰지 마라. 기간은 1거래일, 5거래일, 20거래일로만 "
    "말하고 '한 달', '일주일' 같은 다른 기간을 쓰지 마라. "
    "(3) 값이 null이거나 available이 false인 항목은 추정하지 말고 아직 계산할 수 없다고만 말하라. "
    "(4) 과거의 반응을 앞으로의 주가로 일반화하거나 예측하지 마라. 매수나 매도를 권하지 마라. "
    "(5) 같은 기간에 다른 사건이 있으면(other_events_in_window) 그 사건만의 움직임으로 볼 수 없다고 말해도 된다. "
    "시장 지수 대비 초과수익률(excess_pct)이 있으면 시장 전체 흐름과 함께 봐야 한다고 말해도 된다. "
    "(6) 표에 이미 있는 수치와 사유를 되풀이하지 마라. 사건들 사이의 비교나 주의할 점만 한두 문장으로 써라. "
    "초과수익률은 %p, 수익률과 지수 수익률은 %로 구분해서 써라. 쓸 말이 없으면 아무것도 쓰지 말고 비워 두어라."
)

_REASON_TEXT = [
    (re.compile(r"(\d+)거래일 경과 전"), lambda m: f"아직 {m.group(1)}거래일이 지나지 않아 계산할 수 없음"),
    (re.compile(r"반응 시작일 전"), lambda m: "다음 거래일 주가가 아직 없어 계산할 수 없음"),
    (re.compile(r"기준 종가 없음"), lambda m: "사건 이전 주가 데이터가 없어 계산할 수 없음"),
]


def reason_text(reason: str | None) -> str:
    for pattern, fmt in _REASON_TEXT:
        m = pattern.search(reason or "")
        if m:
            return fmt(m)
    return "계산할 수 없음" + (f"({reason})" if reason else "")


def _won(x: float) -> str:
    return f"{int(x):,}원" if float(x).is_integer() else f"{x:,.2f}원"


def _event_when(e: dict) -> str:
    if e.get("event_at"):
        return e["event_at"][:16].replace("T", " ")
    return e.get("event_date") or ""


def _is_adjusted(e: dict) -> bool:
    return bool(e.get("note")) and "가격 조정" in e["note"]


DEFAULT_ITEMS = 5  # 유형별로 보여주는 항목 수(공시는 건, 뉴스는 같은 날짜 묶음)
MAX_GROUP_TITLES = 3


def _day(e: dict) -> str:
    return (e.get("event_at") or e.get("event_date") or "")[:10]


def _time_key(e: dict) -> tuple[str, str]:
    return (_day(e), e.get("event_at") or "")


def _reaction_key(e: dict):
    """같은 반응 구간(반응 시작일)의 사건은 기준일도 같아서 수치가 같다. 시작일이 없는(아직 다음 거래일이 없는) 사건은 발생일로 묶는다."""
    return e.get("reaction_start_date") or ("pending", _day(e))


def display_items(result: dict) -> list[dict]:
    """답변에 보여줄 항목들(최신순). 공시는 건별, 뉴스는 반응 시작일이 같은 것끼리 한 항목으로 묶는다.
    유형별로 result["limit"](기본 5)개 항목까지만 쓰고, 그 밖의 사건은 답변·요약·검증에서 모두 제외한다."""
    limit = result.get("limit") or DEFAULT_ITEMS
    events = [e for e in result.get("events") or [] if isinstance(e, dict)]
    disclosures = sorted((e for e in events if e["event_type"] == "disclosure"), key=_time_key, reverse=True)
    groups: dict = {}
    for e in sorted((e for e in events if e["event_type"] == "news"), key=_time_key, reverse=True):
        groups.setdefault(_reaction_key(e), []).append(e)
    items = [{"kind": "disclosure", "events": [e]} for e in disclosures[:limit]]
    items += [{"kind": "news", "events": g} for g in list(groups.values())[:limit]]
    items.sort(key=lambda it: max(_time_key(e) for e in it["events"]), reverse=True)
    return items


def shown_events(result: dict) -> list[dict]:
    """답변에 실제로 쓰이는 사건들(묶인 뉴스는 전부 포함)."""
    return [e for it in display_items(result) for e in it["events"]]


def display_summary(result: dict) -> dict:
    """요약에 쓰는 summary. 같은 반응 구간(반응 시작일)의 사건은 수치가 같아서 날짜 하나로 센다(같은 날 뉴스 5건이 표본 5건이 되지 않게).
    가격 조정 가능성 경고가 있는 사건(수익률을 표시하지 않는 사건)은 평균에서 뺀다. event_count는 사건 수, date_count는 날짜 수."""
    events = shown_events(result)
    reps: dict = {}
    for e in events:
        reps.setdefault(_reaction_key(e), e)
    out = summarize([e for e in reps.values() if not _is_adjusted(e)])
    out["event_count"] = len(events)
    out["date_count"] = len(reps)
    return out


def _horizon_text(n: int, h: dict, index_name: str | None) -> str:
    if not h.get("available"):
        return f"{n}거래일: {reason_text(h.get('reason'))}"
    text = f"{n}거래일 {h['pct']:+.2f}%"
    if h.get("excess_pct") is not None and index_name:
        text += f"({index_name} {h['index_pct']:+.2f}%, 초과 {h['excess_pct']:+.2f}%p)"
    return text


def summary_sentence(summary: dict) -> str:
    """코드가 계산한 값과 표본 수만 쓰는 요약 문장."""
    total, dates = summary.get("event_count", 0), summary.get("date_count", 0)
    scope = f"조회한 사건 {total}건(날짜 {dates}개)"
    parts, small = [], []
    for n in HORIZONS:
        k = summary.get(f"with_d{n}", 0)
        if not k:
            continue
        avg = summary.get(f"avg_d{n}_pct")
        part = f"{n}거래일 수익률은 {k}개 날짜에서 계산됐고 평균 {avg:+.2f}%(상승 {summary.get(f'positive_d{n}_count', 0)}개)"
        ex = summary.get(f"avg_excess_d{n}_pct")
        if ex is not None:
            part += f", 지수 대비 초과수익률 평균 {ex:+.2f}%p({summary.get(f'with_excess_d{n}', 0)}개 날짜)"
        parts.append(part)
        if k < SMALL_SAMPLE:
            small.append(f"{n}거래일")
    if not parts:
        return f"요약: {scope} 모두 아직 수익률을 계산할 수 없습니다."
    text = f"요약: {scope} 중 " + "; ".join(parts) + "입니다. 같은 날짜의 사건은 수치가 같아 날짜 하나로 셌습니다."
    if small:
        text += f" {', '.join(small)} 수익률은 표본(날짜)이 {SMALL_SAMPLE}개 미만이라 일반화하기 어렵습니다."
    return text


def format_event_section(result: dict) -> str:
    """'## 사건 이후 주가 반응' 섹션(코드가 조회 결과로 쓴다). 결과가 없으면(found=false) 사유 문장만 쓴다."""
    if not isinstance(result, dict) or not result.get("found"):
        msg = (result or {}).get("message") or "대상 사건이 없습니다."
        return f"{SECTION_TITLE}\n{msg}"
    index_name = INDEX_NAMES.get(result.get("market_index"))
    items = display_items(result)
    events = [e for it in items for e in it["events"]]
    summary = display_summary(result)
    head = (
        f"{result['company_name']}({result['ticker']}) 최근 {result.get('since_days')}일 안의 사건 {summary['event_count']}건"
        f"(날짜 {summary['date_count']}개)"
    )
    head += f", 주가 기준일 {result.get('price_as_of')}"
    head += f", 비교 지수 {index_name}." if index_name else "."
    lines = [SECTION_TITLE, head]
    if any(e["event_type"] == "disclosure" for e in events):
        lines.append(DISCLOSURE_TIME_NOTE)
    if any(e["event_type"] == "news" and not e.get("time_known") for e in events):
        lines.append(NEWS_DATE_ONLY_NOTE)
    excess_notes = {
        h.get("reason") for e in events for h in e["returns"].values()
        if h.get("available") and h.get("excess_pct") is None and h.get("reason")
    }
    for note in sorted(excess_notes):
        lines.append(f"※ {note}.")
    lines.append("")
    for it in items:
        evs = it["events"]
        e = evs[0]  # 묶인 뉴스는 반응 시작일이 같아 기준일·수익률·거래량이 모두 같다
        if it["kind"] == "disclosure":
            title_line = f"- {_event_when(e)} · 공시 · {e['title']}"
        else:
            days = sorted({_day(x) for x in evs})
            label = days[0] if len(days) == 1 else f"{days[0]}~{days[-1]}"
            title_line = f"- {label} · 뉴스 {len(evs)}건"
        if e.get("other_events_in_window"):
            title_line += f" (같은 기간 다른 사건 {e['other_events_in_window']}건)"
        lines.append(title_line)
        if it["kind"] == "news":
            for x in evs[:MAX_GROUP_TITLES]:
                lines.append(f"  · {x['title']}")
            if len(evs) > MAX_GROUP_TITLES:
                lines.append(f"  · 외 {len(evs) - MAX_GROUP_TITLES}건")
        if e.get("base_close") is not None:
            lines.append(f"  - 기준 종가 {_won(e['base_close'])}({e['base_date']}), 반응 시작일 {e.get('reaction_start_date') or '없음'}")
        if _is_adjusted(e):
            lines.append("  - 가격 조정(분할 등) 가능성: 구간 안에 일간 등락률이 30%를 넘는 날이 있어 수익률을 표시하지 않습니다.")
        else:
            lines.append("  - " + " / ".join(_horizon_text(n, e["returns"][f"d{n}"], index_name) for n in HORIZONS))
        if e.get("volume_ratio") is not None:
            lines.append(
                f"  - 거래량: 반응 시작일 거래량이 기준일까지 최근 {e['volume_sample_days']}거래일 평균의 {e['volume_ratio']:.2f}배"
            )
    lines.append("")
    lines.append(summary_sentence(summary))
    return "\n".join(lines).strip()


def event_result_for_llm(result: dict) -> dict:
    """LLM에 넘기는 결과 사본: 링크와 면책 문구를 빼고, 가격 조정 경고가 있는 사건은 수익률을 지운다. 기사 본문은 원래 없다
    (event_reaction_tool은 제목만 준다). summary는 요약 문장과 같은 값(display_summary)으로 바꾼다."""
    if not isinstance(result, dict) or not result.get("found"):
        return result
    events = []
    for e in shown_events(result):
        e = {k: v for k, v in e.items() if k != "url"}
        if _is_adjusted(e):
            e["returns"] = "가격 조정 가능성 때문에 수익률을 표시하지 않는다"
        events.append(e)
    return {
        **{k: v for k, v in result.items() if k not in ("events", "disclaimer", "summary")},
        "events": events,
        "summary": display_summary(result),
    }


# ---------- LLM 문장 검증 ----------

# 주가 움직임을 사건에 원인으로 연결하는 표현.
_CAUSAL_RE = re.compile(
    r"때문|영향으로|영향을\s*(?:받|주|줬|준|미치|미쳤|미친)|영향이\s*(?:있|컸|큰|크)|덕분|덕에|탓|원인|기인"
    r"|(?:으)?로\s*인(?:해|한|하여)|힘입|여파|견인|촉발|계기로|호재로|악재로|반영"
    # "주가는 뉴스에 반응했다"처럼 사건에 반응했다고 쓰는 것도 원인 서술이다("공시 이후 주가 반응은 …"은 해당 없음)
    r"|(?:에|에\s*대해|에\s*대한)\s*(?:주가\s*(?:가|는)?\s*)?(?:민감하게\s*|크게\s*|즉각\s*)?반응(?:했|하|한|해|을\s*보)"
    # "주가 움직임은 이 뉴스와 관련이 없다/있다"도 인과를 (부정 방향으로) 단정하는 서술이다
    r"|(?:관련|연관)(?:이|은|성이|성은)?\s*(?:없|있|크|적|보|많|높|깊)|무관"
    # "뉴스에 따라 주가가 변동했다", "기대가 주가 재평가 가능성을 높였다"처럼 사건이 주가를 움직였다는 서술
    r"|(?:에|에\s*의해)\s*따(?:라|른|르면)|따라서|(?:주가|가능성|기대|전망)\S*\s*(?:을|를)\s*(?:높였|낮췄|올렸|키웠|끌어올렸|떨어뜨렸|이끌었|부추겼|자극했)"
    # "공시되어, 하락했다", "발표되자 올랐다"처럼 사건을 연결어미로 등락에 바로 잇는 서술("~돼서 떨어졌다"로 읽힌다)
    r"|(?:공시|발표|보도|공개|게재)(?:되어|돼서|되자|하자|되면서|하면서|돼)\s*,?\s*.{0,25}?"
    r"(?:상승|하락|올랐|내렸|떨어|오른|내린|빠졌|반등|급락|급등)"
)
# 계산되지 않은 값을 "반응이 없었다/변화가 없었다"로 메운 문장(null을 0으로 추정)
_NO_REACTION_RE = re.compile(r"(?:반응|변화|움직임)(?:이|은|는|가)?\s*없|움직이지\s*않")
# 결과의 필드 이름(d1/d5/d20 등)을 그대로 쓴 문장
_FIELD_RE = re.compile(r"(?<![A-Za-z])d(?:1|5|20)(?![0-9])")
# 줄 앞 목록 표시. 번호 목록("1.")은 번호가 결과에 없는 수치라서 '- '로 바꿔 남긴다.
_LIST_MARK_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•]+)(?:\s+|$)")
_PUNCT_ONLY_RE = re.compile(r"[\s\"'“”‘’:·,.\-()\[\]…]+")
# 인과를 단정하지 않는다고 밝히는 문장은 남긴다("이 공시 때문이라고 단정할 수 없습니다").
_CAUSAL_HEDGE_RE = re.compile(
    r"단정(?:할\s*수\s*없|하기\s*어렵|하지\s*않|할\s*수는\s*없)|볼\s*수\s*(?:없|는\s*없)|알\s*수\s*없|분리(?:할|하기)|구분(?:할|하기)"
    r"|인과(?:관계)?(?:가|는|를)?\s*(?:아니|의미하지|뜻하지|보여주지)"
)
_FUTURE_RE = re.compile(
    r"(?:오를|내릴|떨어질|상승할|하락할|이어질|나타날|반등할|회복할|될)\s*(?:것|가능성|수\s*있)|예상|전망|기대(?:됩|할|해|된)"
    r"|향후|앞으로|다음에도|이번에도|패턴|경향|일반적으로|통상|매수|매도|추천"
)
# 조회 결과에 없는 기간 표현(결과는 1/5/20거래일뿐이다)
_PERIOD_WORD_RE = re.compile(r"한\s?달|일주일|한\s?주(?![가-힣식])|두\s?달|석\s?달|반년|분기|\d+\s?(?:개월|주(?:간|일)|달|년)")
_UNIT_RE = re.compile(r"([+-]?\d[\d,]*(?:\.\d+)?)\s?(%p|%|원|배|건|개|거래일|일)")
_BARE_NUM_RE = re.compile(r"(?<![\d.,])([+-]?\d[\d,]*\.\d+|\d{3,}(?:,\d{3})*)(?![\d.,%])")
_KOREAN_NUM_RE = re.compile(r"\d[\d,.]*\s?[천백십만억조](?:\s?\d*[\d,.]*\s?[천백십만억조])*\s?(?:원|주)")
_DATE_PATTERNS = [
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"(\d{4})\s?년\s?(\d{1,2})\s?월\s?(\d{1,2})\s?일"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"(?<!\d)(\d{1,2})\s?월\s?(\d{1,2})\s?일"), lambda m: (None, int(m[1]), int(m[2]))),
    (re.compile(r"(?<![\d.])(\d{1,2})/(\d{1,2})(?![\d/])"), lambda m: (None, int(m[1]), int(m[2]))),
]
_UP_RE = re.compile(r"상승|올랐|오른|올라|증가|플러스|반등")
_DOWN_RE = re.compile(r"하락|내렸|내린|떨어|감소|빠졌|마이너스|급락|약세")
_HORIZON_RE = re.compile(r"(?<!\d)(1|5|20)\s?(?:거래)?일")
_NOT_COMPUTED_RE = re.compile(r"계산할\s*수\s*없|계산되지\s*않|지나지\s*않|아직|없습니다|없어|없음")
_DENIES_DATA_RE = re.compile(r"조회하지 못|조회되지 않|조회 결과에 없|조회할 수 없")
# 영어 소문자 낱말(3자 이상)이 섞인 문장("market index와 비교했을 때"). 종목명·제목 속 영문은 대문자 약어라 걸리지 않는다.
_FOREIGN_RE = re.compile(r"[a-z]{3,}")
# 결과에 없는 번호를 매겨 사건을 다시 나열한 줄("뉴스 3: …", "두 번째 공시는 …"). 사건 목록은 코드가 이미 썼다.
_ENUM_RE = re.compile(
    r"(?:뉴스|공시|사건|기사|제목|타이틀)[가-힣A-Za-z\s]{0,6}?(?<![\d.])\d+\s?[:.)](?!\d)|\d+\s?번(?:째)?\s?(?:뉴스|공시|사건|기사)"
    r"|(?:첫|두|세|네|다섯)\s?번째\s?(?:뉴스|공시|사건|기사)"
)
# 뒤에 내용이 없는 안내 문장(사건 목록은 코드가 이미 썼다)
_FILLER_RE = re.compile(r"(?:다음과|아래와)\s*같습니다|정리하면\s*다음")
# 마무리 인사·내용 없는 맺음 문장("위와 같이 나타났습니다", "시간 순서를 따랐습니다")
_CLOSING_RE = re.compile(
    r"위와\s*같이|위(?:의)?\s*(?:내용|표|결과|사건)(?:과|와|을|를|은|는)?\s*(?:같|참고|확인)|시간\s*순서(?:를|로|대로)?\s*(?:따랐|따라|나타|보여)"
    r"|이상입니다|참고(?:하시|해\s*주)|확인하시기\s*바랍|도움이\s*되|나타났습니다\.?$|보여줍니다\.?$"
)
# 조회 결과의 사유 문구("20거래일 경과 전")를 그대로 옮긴 문장. 표에는 "아직 N거래일이 지나지 않아…"로 풀어 썼다.
_RAW_REASON_RE = re.compile(r"경과\s?전")
# 표를 보지 않아도 되는 새 정보: 사건 사이의 비교, 겹치는 사건, 시장 전체와의 관계, 표본·일반화 주의
_NEW_OBSERVATION_RE = re.compile(
    r"가장|대부분|모두|반면|한편|달리|보다|엇갈|일관|공통|차이|겹|다른\s*사건|함께\s*(?:봐야|보|고려)|시장\s*(?:전체|흐름|지수)|표본|일반화|단정"
    r"|\d+\s?(?:건|개)\s*중|사건들|여러\s*사건|알\s*수\s*없"
)
_RESIDUE_RE = re.compile(r"빈칸|빈 칸|이 섹션은|N/?A|\{|\}|\[[^\]]*(?:수치|값|날짜|종목)[^\]]*\]")


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", "").lstrip("+"))
    except InvalidOperation:
        return None


def _allowed(result: dict, events: list[dict] | None = None) -> dict:
    """결과에서 문장에 쓸 수 있는 수치(단위별, 반올림 표기 포함)와 부호(방향 대조용)."""
    events = shown_events(result) if events is None else events
    summary = display_summary(result)
    pct: set[Decimal] = set()
    ret: set[Decimal] = set()  # %로 쓰는 값(수익률, 지수 수익률)
    pp: set[Decimal] = set()  # %p로 쓰는 값(초과수익률)
    signs: dict[Decimal, set[int]] = {}

    def add_pct(v, unit_set):
        if v is None:
            return
        d = Decimal(str(v))
        for x in _variants(d):
            pct.add(x)
            unit_set.add(x)
            signs.setdefault(x, set()).add((d > 0) - (d < 0))

    won, times, counts = set(), set(), {Decimal(n) for n in HORIZONS}
    for e in events:
        if _is_adjusted(e):
            continue
        for h in e["returns"].values():
            add_pct(h.get("pct"), ret)
            add_pct(h.get("index_pct"), ret)
            add_pct(h.get("excess_pct"), pp)
        if e.get("base_close") is not None:
            won |= _variants(Decimal(str(e["base_close"])))
        if e.get("volume_ratio") is not None:
            times |= _variants(Decimal(str(e["volume_ratio"])))
        counts.add(Decimal(e.get("other_events_in_window") or 0))
        counts.add(Decimal(e.get("volume_sample_days") or 0))
    for k, v in summary.items():
        if k.startswith("avg_"):
            add_pct(v, pp if "excess" in k else ret)
        elif isinstance(v, int):
            counts.add(Decimal(v))
    counts.add(Decimal(len(events)))
    counts.add(Decimal(result.get("since_days") or 0))
    return {"%": pct, "원": won, "배": times, "건": counts, "개": counts, "일": counts, "signs": signs, "ret": ret, "pp": pp}


def _allowed_dates(result: dict) -> set[date]:
    out = set()
    for e in shown_events(result):
        for key in ("base_date", "reaction_start_date", "event_date"):
            if e.get(key):
                out.add(date.fromisoformat(e[key][:10]))
        if e.get("event_at"):
            out.add(date.fromisoformat(e["event_at"][:10]))
    if result.get("price_as_of"):
        out.add(date.fromisoformat(str(result["price_as_of"])[:10]))
    return out


def _dates_in(sentence: str) -> tuple[list[tuple], str]:
    """문장 속 날짜 표기들(연, 월, 일)과, 날짜를 지운 문장."""
    found = []
    rest = sentence
    for pattern, conv in _DATE_PATTERNS:
        for m in pattern.finditer(rest):
            found.append(conv(m))
        rest = pattern.sub(" ", rest)
    return found, rest


def _date_ok(ymd: tuple, allowed: set[date]) -> bool:
    y, mo, d = ymd
    return any((y is None or a.year == y) and a.month == mo and a.day == d for a in allowed)


def _events_by_date(sentence_dates: list[tuple], events: list[dict]) -> list[dict]:
    """문장에 나온 날짜가 사건 날짜(발행일/접수일)인 사건들."""
    out = []
    for e in events:
        day = date.fromisoformat((e.get("event_at") or e.get("event_date"))[:10])
        if any(_date_ok(ymd, {day}) for ymd in sentence_dates):
            out.append(e)
    return out


def _strip_titles(sentence: str, events: list[dict]) -> str:
    """LLM이 인용한 사건 제목(제목 속 숫자는 기사의 것)을 지운 문장."""
    for e in sorted(events, key=lambda x: len(x["title"]), reverse=True):
        t = e["title"].strip()
        if len(t) >= 4 and t in sentence:
            sentence = sentence.replace(t, " ")
    return sentence


def _other_company(sentence: str, subject: list[str], other_names) -> bool:
    """질문 종목이 아닌 다른 종목 이름이 문장에 있는지. 두 글자 이름은 일반 낱말과 겹쳐서("대상 사건") 보지 않는다."""
    hay = _without(sentence, subject)
    return any(len(o) >= 3 and o in hay for o in (_norm(n) for n in other_names if n not in subject))


def _direction_wrong(sentence: str, numbers: list[tuple[str, str]], allowed: dict, result: dict) -> bool:
    up, down = bool(_UP_RE.search(sentence)), bool(_DOWN_RE.search(sentence))
    if up == down:
        return False
    pct_nums = [_dec(n) for n, u in numbers if u in ("%", "%p")]
    pct_nums = [abs(x).normalize() for x in pct_nums if x is not None]
    if pct_nums:
        signs = set().union(*(allowed["signs"].get(x, set()) for x in pct_nums))
        if up and signs and signs <= {-1}:
            return True
        if down and signs and signs <= {1}:
            return True
        return False
    # 수치 없이 방향만 말한 문장: 말한 기간(없으면 전체)의 계산된 수익률이 모두 반대 부호면 틀린 서술이다
    horizons = {int(h) for h in _HORIZON_RE.findall(sentence)} or set(HORIZONS)
    values = [
        e["returns"][f"d{n}"]["pct"] for e in shown_events(result) if not _is_adjusted(e)
        for n in horizons if e["returns"][f"d{n}"]["available"]
    ]
    if not values:
        return False
    return (up and all(v < 0 for v in values)) or (down and all(v > 0 for v in values))


def _estimates_null(sentence: str, numbers, summary: dict) -> bool:
    """계산되지 않은 기간(표본 0건)에 수치나 방향을 붙인 문장인지."""
    if _NOT_COMPUTED_RE.search(sentence):
        return False
    for h in {int(x) for x in _HORIZON_RE.findall(sentence)}:
        if summary.get(f"with_d{h}", 0) == 0 and (
            any(u in ("%", "%p") for _, u in numbers) or _UP_RE.search(sentence) or _DOWN_RE.search(sentence)
        ):
            return True
    return False


def _has_data(sentence: str, numbers, sent_dates) -> bool:
    return bool(numbers or sent_dates or _HORIZON_RE.search(sentence) or _NOT_COMPUTED_RE.search(sentence))


def _no_new_observation(sentence: str) -> bool:
    """사건들 사이의 비교나 주의할 점(겹치는 사건, 시장 전체 흐름, 표본·일반화 주의 등)이 하나도 없는 문장인지."""
    return not _NEW_OBSERVATION_RE.search(sentence)


def check_event_answer(answer: str, result: dict, other_names=()) -> tuple[str, list[tuple[str, str]]]:
    """LLM 해석 문장을 event_reaction_tool 결과와 문장 단위로 대조해 맞지 않는 문장을 지운다. (정리된 답변, [(지운 문장, 사유)]).

    지우는 문장: (a) 주가 움직임을 사건에 원인으로 연결하는 표현(단정하지 않는다고 밝힌 문장은 남김), (b) 결과에 없는 수치
    (%·%p·원·배·건·N일, 소수·세 자리 이상 숫자, 한글 단위 수치), (c) 결과에 없는 날짜와 기간(한 달, 일주일, N개월 등),
    (d) 상승/하락 방향이 결과와 반대인 서술, 그리고 계산되지 않은 기간을 추정한 문장, 미래 예측·일반화·매매 권유,
    결과가 있는데 데이터가 없다고 한 문장, 질문 밖 종목에 수치를 붙인 문장, 템플릿 잔재. 헤더(#) 줄은 지운다
    (섹션 구성은 코드가 한다)."""
    events = shown_events(result) if isinstance(result, dict) else []
    found = isinstance(result, dict) and result.get("found")
    allowed = _allowed(result) if found else {"%": set(), "원": set(), "배": set(), "건": set(), "개": set(), "일": set(), "signs": {}, "ret": set(), "pp": set()}
    dates = _allowed_dates(result) if found else set()
    summary = display_summary(result) if found else {}
    subject = [result.get("company_name")] if isinstance(result, dict) and result.get("company_name") else []
    dropped: list[tuple[str, str]] = []
    out_lines = []
    for line in (answer or "").split("\n"):
        if line.lstrip().startswith("#"):
            continue
        if _ENUM_RE.search(_strip_titles(line, events)):
            # 번호를 매겨 사건을 다시 나열한 줄은 통째로 지운다("공시 1. 제목"이 문장 경계에서 나뉘어 제목만 남지 않게)
            dropped.append((line.strip(), "임의 번호로 사건 나열"))
            continue
        mark = _LIST_MARK_RE.match(line)
        prefix = "- " if mark else ""
        content = line[mark.end():] if mark else line
        if mark and not _PUNCT_ONLY_RE.sub("", _strip_titles(content, events)):
            # 목록 표시만 있거나 사건 제목만 다시 쓴 줄(사건 목록은 코드가 이미 썼다)
            if content.strip():
                dropped.append((line.strip(), "사건 제목만 다시 나열"))
            continue
        kept = []
        for sent in _sentences(content):
            if not sent.strip():
                continue
            # 사건 제목과 종목명("KODEX 200"의 200) 속 숫자는 수치로 보지 않는다
            body = _strip_titles(sent, events)
            for name in subject:
                body = body.replace(name, " ")
            sent_dates, no_dates = _dates_in(body)
            numbers = _UNIT_RE.findall(no_dates)
            reason = None
            if NUMBER_MASK in sent or _RESIDUE_RE.search(sent):
                reason = "템플릿 잔재"
            elif _FILLER_RE.search(body) or _CLOSING_RE.search(body):
                reason = "내용 없는 안내·마무리 문장"
            elif _RAW_REASON_RE.search(body):
                reason = "기간과 사유 문구를 섞어 씀"
            elif _FOREIGN_RE.search(body) or _FIELD_RE.search(body):
                reason = "다른 언어·필드 이름 섞임"
            elif _CAUSAL_RE.search(body) and not _CAUSAL_HEDGE_RE.search(body):
                reason = "인과 단정"
            elif _NO_REACTION_RE.search(body) and not _CAUSAL_HEDGE_RE.search(body):
                reason = "계산되지 않은 값을 추정"
            elif _FUTURE_RE.search(body):
                reason = "미래 예측·일반화·매매 권유"
            elif found and _DENIES_DATA_RE.search(body):
                reason = "조회한 결과를 없다고 함"
            elif _KOREAN_NUM_RE.search(no_dates):
                reason = "결과에 없는 한글 단위 수치"
            elif _PERIOD_WORD_RE.search(no_dates):
                reason = "결과에 없는 기간"
            elif any(not _date_ok(d, dates) for d in sent_dates):
                reason = "결과에 없는 날짜"
            elif _other_company(sent, subject, other_names) and (numbers or _BARE_NUM_RE.search(no_dates)):
                reason = "질문 밖 종목에 수치를 붙임"
            else:
                scoped = _events_by_date(sent_dates, events)
                scope = _allowed(result, scoped) if scoped else allowed
                bad = []
                confused = []
                for num, unit in numbers:
                    raw_unit = unit
                    unit = "%" if unit == "%p" else ("일" if unit == "거래일" else unit)
                    value = _dec(num)
                    if value is not None and raw_unit in ("%", "%p"):
                        v = abs(value).normalize()
                        # 같은 값이 %와 %p 양쪽에 있으면 구분할 수 없어 통과시킨다
                        if raw_unit == "%" and v in scope["pp"] and v not in scope["ret"]:
                            confused.append(f"{num}% (결과는 %p)")
                        if raw_unit == "%p" and v in scope["ret"] and v not in scope["pp"]:
                            confused.append(f"{num}%p (결과는 %)")
                    if value is None or abs(value).normalize() not in scope[unit] | (allowed[unit] if unit in ("건", "일") else set()):
                        bad.append(f"{num}{unit}")
                rest = _UNIT_RE.sub(" ", no_dates)
                every = scope["%"] | scope["원"] | scope["배"]
                for num in _BARE_NUM_RE.findall(rest):
                    value = _dec(num)
                    if value is None or abs(value).normalize() not in every:
                        bad.append(num)
                if bad:
                    reason = f"결과에 없는 수치 {bad}"
                elif confused:
                    reason = f"%와 %p를 혼동 {confused}"
                elif _estimates_null(body, numbers, summary):
                    reason = "계산되지 않은 값을 추정"
                elif found and _direction_wrong(body, numbers, scope, result):
                    reason = "방향이 결과와 반대"
                elif found and _no_new_observation(body):
                    # 해석은 사건들 사이의 비교나 주의할 점만 쓴다. 수치·날짜·사유만 담았으면 표의 되풀이이고, 그렇지 않아도
                    # 제목을 바꿔 쓴 문장이나 막연한 서술이라 쓰지 않는다.
                    reason = "표의 수치·사유를 되풀이" if _has_data(body, numbers, sent_dates) else "비교·주의가 없는 문장"
            if reason:
                dropped.append((sent.strip(), reason))
            else:
                kept.append(sent)
        joined = " ".join(s for s in kept if s)
        out_lines.append(prefix + joined if joined else "")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    return text, dropped


def compose_event_answer(llm_answer: str, result: dict, extra_notices=()) -> str:
    """최종 답변: 코드가 쓴 섹션 + (검증을 통과한 LLM 해석이 있으면) '## 해석' + 고정 문구들 + 이 기능 전용 면책 문구."""
    parts = [format_event_section(result)]
    if llm_answer and llm_answer.strip():
        parts.append(f"{INTERPRETATION_TITLE}\n{llm_answer.strip()}")
    parts.extend(n for n in extra_notices if n)
    disclaimer = (result or {}).get("disclaimer")
    if not disclaimer:
        from app.event_reaction import DISCLAIMER

        disclaimer = DISCLAIMER
    parts.append(f"※ {disclaimer}")
    return "\n\n".join(parts)


def trim_result(result: dict) -> dict:
    """tool 결과를 답변에 실제로 쓰는 사건만 남긴 사본으로 만든다(저장·근거(sources)·검증이 답변과 같은 사건을 보게 한다).
    조회는 묶기 전 사건을 넉넉히 하므로(event_reaction_tool.FETCH_FACTOR) 원래 조회 건수는 fetched_event_count에 남긴다."""
    if not isinstance(result, dict) or not result.get("found"):
        return result
    events = shown_events(result)
    return {**result, "events": events, "fetched_event_count": len(result.get("events") or [])}
