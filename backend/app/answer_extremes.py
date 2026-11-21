"""LLM 답변의 기간 최고·최저 종가와 현재가 표현 검증(2026-11-21, 최종 시연 전 품질 수정).

배경: 브라우저 테스트에서 답변의 최고가와 최저가가 서로 바뀌어 나갔다. 기존 숫자 검증(app.answer_guard.guard_answer)은 "그 숫자가 도구 결과에
있는가"만 보므로, 최고값을 최저라고 쓰거나 날짜를 다른 날로 써도 두 값이 모두 도구 결과에 있어 통과했다.

원칙: 최고·최저·현재가는 코드가 계산한 값만 그 이름으로 나간다. 문장이 틀렸으면 그 문장만 도구 결과로 만든 결정론적 문장으로 바꾼다
(LLM을 다시 부르거나 숫자를 추측하지 않는다).
- 기간 종가 최고·최저: stock_tool 결과(found)의 summary("이 기간 종가 최고는 X원(날짜), 최저는 Y원(날짜)")에서 읽는다. summary가 없으면
  prices(구간의 일별 종가)로 계산한다. 이 값은 stock_tool이 정한 같은 정의(구간 안 거래일의 종가, 기준 종가 제외)다.
- 문장에 "최고(가)/고점"·"최저(가)/저점" 뒤에 원 단위 숫자가 오면 그 값·날짜를 대조한다. 52주·역대·사상·연중·장중·목표 같은 다른 개념은
  도구 결과에 없으므로 여기서 보지 않는다(설명되지 않는 숫자는 guard_answer가 따로 거른다).
- 현재가·실시간 가격: 도구 결과는 저장된 일별 종가뿐이라(실시간 시세 없음), "현재가/실시간/지금 주가 X원"은 X가 저장 종가면 그 종가의 날짜를
  밝힌 문장으로 바꾼다. "오늘 종가 X원"인데 X가 오늘이 아닌 날의 종가여도 같은 문장으로 바꾼다.
- 데이터가 부족하면(도구 결과에 최고·최저가 없으면) 그 문장을 지운다. 비교할 종목을 정할 수 없으면(종목 여럿, 문장에 종목 이름 없음) 손대지 않는다.
"""

from __future__ import annotations

import re
from datetime import date

from app.answer_guard import _SENTENCE_SPLIT_RE, extract_numbers

_SUMMARY_HL_RE = re.compile(r"종가 최고는 ([\d,]+)원\((\d{4}-\d{2}-\d{2})\), 최저는 ([\d,]+)원\((\d{4}-\d{2}-\d{2})\)")
_HIGH_WORD = r"(?:최고가|최고치|최고점|최고\s*종가|종가\s*최고|최고|고점|고가|가장\s*높(?:은|았던|게))"
_LOW_WORD = r"(?:최저가|최저치|최저점|최저\s*종가|종가\s*최저|최저|저점|저가|가장\s*낮(?:은|았던|게))"
_WORD_RE = re.compile(f"(?P<high>{_HIGH_WORD})|(?P<low>{_LOW_WORD})")
_OTHER_CONCEPT_RE = re.compile(r"52\s*주|역대|사상|연중|장중|목표|상장\s*이후|신고가|신저가|일중")
_REALTIME_RE = re.compile(r"현재가|실시간|현재\s*주가|현재\s*가격|지금\s*(?:주가|가격)|현\s*시세")
_TODAY_CLOSE_RE = re.compile(r"오늘(?:의)?\s*종가|금일\s*종가")
_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MD_RE = re.compile(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_BULLET_RE = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+)")


def _close_rows(result: dict) -> list[tuple[str, float]]:
    rows = []
    for p in result.get("prices") or []:
        if isinstance(p, dict) and p.get("price_date") and isinstance(p.get("close_price"), (int, float)):
            rows.append((str(p["price_date"])[:10], float(p["close_price"])))
    return rows


def period_extremes(records: list[dict]) -> dict[str, dict]:
    """종목 이름 -> {"high": (값, 날짜), "low": (값, 날짜), "closes": {날짜: 종가}}. stock_tool 결과(found)만 본다."""
    out: dict[str, dict] = {}
    for rec in records or []:
        r = rec.get("result")
        if rec.get("tool_name") != "stock_tool" or not isinstance(r, dict) or not r.get("found") or not r.get("company_name"):
            continue
        rows = _close_rows(r)
        closes = dict(rows)
        m = _SUMMARY_HL_RE.search(str(r.get("summary") or ""))
        if m:
            high = (float(m.group(1).replace(",", "")), m.group(2))
            low = (float(m.group(3).replace(",", "")), m.group(4))
        elif len(rows) >= 2:
            hi = max(rows, key=lambda x: x[1])
            lo = min(rows, key=lambda x: x[1])
            high, low = (hi[1], hi[0]), (lo[1], lo[0])
        else:
            high = low = None
        entry = out.setdefault(r["company_name"], {"high": None, "low": None, "closes": {}})
        entry["closes"].update(closes)
        if high is not None:
            # 같은 종목을 여러 기간으로 조회했으면 가장 긴 기간(행이 많은) 결과를 쓴다
            if entry["high"] is None or len(rows) > entry.get("_rows", 0):
                entry.update(high=high, low=low, _rows=len(rows))
    return out


def _close_enough(claimed, expected: float) -> bool:
    half = max(claimed.tolerance / 2, 0.5)
    return abs(claimed.value - expected) <= half + 1e-9


def _date_near(text: str, today: date | None) -> str | None:
    """숫자 바로 뒤(괄호 포함 20자 안)의 날짜를 ISO로. 연도가 없으면 today의 연도(없으면 None)."""
    m = _ISO_RE.search(text)
    if m:
        return m.group(0)
    m = _MD_RE.search(text)
    if m:
        year = int(m.group(1)) if m.group(1) else (today.year if today else None)
        if year is None:
            return None
        try:
            return date(year, int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None


def _claims(sentence: str) -> list[tuple[str, object, str | None]]:
    """문장의 (high|low, 숫자, 날짜) 주장들. 단어 뒤 같은 구절(다음 최고/최저 단어 전) 안의 첫 '원' 숫자만 본다."""
    out = []
    words = list(_WORD_RE.finditer(sentence))
    for i, w in enumerate(words):
        before = sentence[max(0, w.start() - 8):w.start()]
        if _OTHER_CONCEPT_RE.search(before) or _OTHER_CONCEPT_RE.match(sentence[w.end():w.end() + 4] or " "):
            continue
        end = words[i + 1].start() if i + 1 < len(words) else len(sentence)
        seg = sentence[w.end():end]
        nums = [n for n in extract_numbers(seg) if n.unit == "원"]
        if not nums:
            continue
        n = nums[0]
        pos = seg.find(n.raw)
        after = seg[pos + len(n.raw):pos + len(n.raw) + 24] if pos >= 0 else ""
        out.append(("high" if w.group("high") else "low", n, after))
    return out


def _subject(sentence: str, extremes: dict[str, dict]) -> str | None:
    named = [name for name in extremes if name in sentence]
    if len(named) == 1:
        return named[0]
    if not named and len(extremes) == 1:
        return next(iter(extremes))
    return None


def _fixed_sentence(name: str, e: dict) -> str:
    (hv, hd), (lv, ld) = e["high"], e["low"]
    return f"{name}의 이 기간 종가 최고는 {hv:,.0f}원({hd}), 최저는 {lv:,.0f}원({ld})입니다."


def _dated_close_sentence(name: str, day: str, value: float) -> str:
    return f"{name}의 {day} 종가는 {value:,.0f}원입니다(저장된 일별 종가이며 실시간 가격이 아닙니다)."


def _check_sentence(sentence: str, extremes: dict[str, dict], today: date | None) -> tuple[str | None, str | None]:
    """(바꿀 문장 또는 ""=지움, 이유) 또는 (None, None)=그대로."""
    name = _subject(sentence, extremes)
    if name is None:
        return None, None
    e = extremes[name]
    for kind, n, after in _claims(sentence):
        if e["high"] is None:
            return "", f"extreme_without_data:{kind}:{n.raw}"
        expected, other = (e["high"], e["low"]) if kind == "high" else (e["low"], e["high"])
        if not _close_enough(n, expected[0]):
            why = "swapped" if _close_enough(n, other[0]) else "value"
            return _fixed_sentence(name, e), f"extreme_{why}:{kind}:{n.raw}"
        claimed_day = _date_near(after, today)
        if claimed_day and claimed_day != expected[1] and not any(
                d == claimed_day and abs(v - expected[0]) < 0.5 for d, v in e["closes"].items()):
            return _fixed_sentence(name, e), f"extreme_date:{kind}:{claimed_day}"
    realtime = _REALTIME_RE.search(sentence)
    today_close = _TODAY_CLOSE_RE.search(sentence)
    if realtime or today_close:
        for n in extract_numbers(sentence):
            if n.unit != "원":
                continue
            days = [d for d, v in e["closes"].items() if abs(v - n.value) <= max(n.tolerance / 2, 0.5)]
            if not days:
                continue
            day = max(days)
            if realtime:
                return _dated_close_sentence(name, day, e["closes"][day]), f"realtime_as_close:{n.raw}"
            if today is not None and day != today.isoformat():
                return _dated_close_sentence(name, day, e["closes"][day]), f"today_close_date:{day}"
    return None, None


def fix_extremes(answer: str, records: list[dict], today: date | None = None) -> tuple[str, list[str]]:
    """(답변, 바꾼 이유 목록). 최고·최저·현재가 문장이 도구 결과와 다르면 그 문장만 결정론적 문장으로 바꾼다(같은 문장이 이미 있으면 지운다)."""
    if not answer:
        return answer, []
    extremes = period_extremes(records)
    if not extremes:
        return answer, []
    reasons: list[str] = []
    out_lines = []
    emitted: set[str] = set()
    for line in answer.split("\n"):
        if line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        m = _BULLET_RE.match(line)
        prefix, body = (m.group(1), line[m.end():]) if m else ("", line)
        kept = []
        changed = False
        for sent in _SENTENCE_SPLIT_RE.split(body):
            if not sent:
                continue
            new, why = _check_sentence(sent, extremes, today)
            if why is None:
                kept.append(sent)
                continue
            changed = True
            reasons.append(why)
            if new and new not in emitted and new not in answer:
                kept.append(new)
                emitted.add(new)
        if not changed:
            out_lines.append(line)
        elif kept:
            out_lines.append(prefix + " ".join(kept))
    if not reasons:
        return answer, []
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip(), reasons
