"""LLM 답변의 표기 정리(2026-11-07 35차): 원시 숫자 표기 정리와 같은 문단·문장 반복 제거. 값과 뜻은 바꾸지 않는다.

33차 live 평가: "SK하이닉스의 최근 주가는 1414000.0원으로"(도구 결과의 float를 그대로 옮김), 공시·뉴스 두 절에 같은 두 문장을 반복.

숫자(normalize_numbers):
- 금액·수량(원, 주): 천 단위 구분을 넣고, 소수부가 0뿐이면 지운다. 0이 아닌 소수부는 그대로 둔다(1250.5원 -> 1,250.5원, 반올림하지 않음).
  이미 쉼표가 있거나 한글 단위(만·억)와 붙은 표기, 날짜·종목코드는 건드리지 않는다.
- 퍼센트: 소수 셋째 자리 이하가 0뿐이면 둘째 자리까지로 줄인다(-4.260000% -> -4.26%). 0이 아닌 자리는 그대로 둔다.
- 거래량·이동평균 등 지표의 정밀도를 하나로 맞추지 않는다(모든 숫자를 소수 둘째 자리로 만들지 않는다).

반복(drop_repeats): 정규화(공백·목록 기호·강조 기호 제거) 뒤 완전히 같은 문단, 그리고 문단 안에서 완전히 같은 문장(20자 이상)만 지운다.
제목 줄(#)과 짧은 문장은 지우지 않는다. 비슷하지만 다른 사실(숫자나 이름이 다른 문장)은 남는다.
예외(2026-11-08): 같은 문단에서 바로 앞 줄과 완전히 같은 줄(정규화 6자 이상)은 짧아도 생성 반복으로 보고 지운다.
"""

from __future__ import annotations

import re

_MONEY_RE = re.compile(r"(?<![\d.,])(?P<int>\d{4,})(?P<frac>\.\d+)?(?P<sp>\s*)(?P<unit>원|주(?!일|간|차|기|년|가|식))")
_PCT_RE = re.compile(r"(?<![\d.])(?P<num>[+\-−]?\d+\.\d{2})(?P<zeros>0+)(?=\s*%)")
_SENT_RE = re.compile(r"(?<=[.!?。])\s+")


def _group(int_part: str) -> str:
    return f"{int(int_part):,}"


def normalize_numbers(text: str) -> str:
    def money(m: re.Match) -> str:
        frac = m.group("frac") or ""
        if frac and set(frac[1:]) == {"0"}:
            frac = ""
        return f"{_group(m.group('int'))}{frac}{m.group('sp')}{m.group('unit')}"

    text = _MONEY_RE.sub(money, text or "")
    return _PCT_RE.sub(lambda m: m.group("num"), text)


def _norm(s: str) -> str:
    return re.sub(r"[\s*_`>#\-•·]+", "", s)


def drop_repeats(text: str, min_chars: int = 20) -> tuple[str, int]:
    """(정리한 글, 지운 문단·문장 수)."""
    removed = 0
    seen_blocks: set[str] = set()
    seen_sents: set[str] = set()
    out_blocks = []
    for block in re.split(r"\n\s*\n", text or ""):
        key = _norm(block)
        if not block.strip().startswith("#") and len(key) >= min_chars and key in seen_blocks:
            removed += 1
            continue
        seen_blocks.add(key)
        lines = []
        prev_line = None
        for line in block.split("\n"):
            if line.strip().startswith("#"):
                lines.append(line)
                prev_line = None
                continue
            # 2026-11-08: 바로 앞 줄과 완전히 같은 줄(6자 이상)은 생성 반복이다(live: "* 기업설명회(IR)개최(안내공시)" 8줄 연속).
            # 20자 기준의 문장 반복으로는 짧은 목록 줄이 남았다. 떨어진 문단의 짧은 같은 문장은 그대로 둔다
            key_line = _norm(line)
            if prev_line is not None and len(key_line) >= 6 and key_line == prev_line:
                removed += 1
                continue
            prev_line = key_line if key_line else prev_line
            kept = []
            for s in _SENT_RE.split(line):
                k = _norm(s)
                if len(k) >= min_chars and k in seen_sents:
                    removed += 1
                    continue
                seen_sents.add(k)
                kept.append(s)
            if kept or not line.strip():
                lines.append(" ".join(kept))
        cleaned = "\n".join(lines)
        if cleaned.strip() or not block.strip():
            out_blocks.append(cleaned)
    result = "\n\n".join(out_blocks)
    if removed:
        result = _drop_empty_headings(result)
    return re.sub(r"\n{3,}", "\n\n", result).strip(), removed


def _drop_empty_headings(text: str) -> str:
    """반복을 지워 본문이 없어진 제목 줄(다음 제목이나 끝까지 빈 줄뿐)을 지운다."""
    lines = text.split("\n")
    keep = []
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            level = len(line.strip()) - len(line.strip().lstrip("#"))
            rest, child = [], False
            for nxt in lines[i + 1:]:
                if nxt.strip().startswith("#"):
                    child = len(nxt.strip()) - len(nxt.strip().lstrip("#")) > level  # 하위 제목이 있으면 빈 절이 아니다
                    break
                rest.append(nxt)
            if not child and not any(r.strip() for r in rest):
                continue
        keep.append(line)
    return "\n".join(keep)


def tidy(text: str) -> tuple[str, int]:
    """숫자 표기 정리 + 반복 제거. (정리한 글, 지운 반복 수). 48차: 본문 없는 제목 줄은 반복이 없어도 지운다("## 관련 뉴스"만 남음)."""
    out, removed = drop_repeats(normalize_numbers(text))
    return re.sub(r"\n{3,}", "\n\n", _drop_empty_headings(out)).strip(), removed


# ---------- 근거 없는 일반론·같은 값 되풀이 (48차, 2026-11-13) ----------
# 48차 실측: "현대차의 주가는 일반적인 기업의 주가와 마찬가지로 수많은 요인에 의해 영향을 받습니다", "주가는 뉴스와 공시를 통해
# 변동합니다", "따라서 … 이러한 요인을 함께 고려해야 합니다", "최신 공시 내용은 disclosure_tool 결과에 있습니다", 그리고 원인 분석 절이
# 앞 절의 거래량만 되풀이("거래량은 21,638,160주로 … 하루 거래량은 21,638,160주입니다"). 도구 결과에서 온 사실이 아니라 어느 종목에나
# 붙일 수 있는 문장이다. 지우는 것은 아래 셋뿐이다(값과 구체적인 내용은 지우지 않는다):
# 1. 숫자가 없고 _GENERIC_RE의 상투 표현이 든 문장
# 2. 도구 이름(stock_tool 등)이 드러난 문장(내부 구현 노출)
# 3. "원인 분석" 절에서, 숫자가 모두 앞 절에 이미 나왔고 원인을 가리키는 낱말(뉴스·공시·계약·시장·때문 …)이 없는 문장
# 그리고 "다음과 같습니다" 같은 안내 문장만 남은 절은 안내 문장도 지운다. 면책 안내는 API의 disclaimer 필드로 따로 나간다.
_GENERIC_RE = re.compile(
    r"(?:다양한|여러\s*가지|여러|수많은|많은)\s*요인(?:에\s*의해|의\s*영향|에\s*따라|이|들이|을|를|으로)"
    r"|대표적인\s*요인은"
    r"|주가(?:는|가)?\s*(?:뉴스|공시|시장)[^.!?。]{0,20}(?:통해|따라|의해)\s*(?:변동|움직|결정)"
    r"|일반적인\s*(?:기업|회사|종목)의\s*주가와\s*마찬가지로"
    r"|이러한\s*(?:요인|점|사항)(?:들)?을\s*(?:함께\s*|종합적으로\s*|충분히\s*)?고려"
    r"|투자\s*(?:결정|판단)?(?:은|는|을|를|에|시|할\s*때)?\s*(?:신중|주의|유의)"
    r"|투자자(?:들)?(?:은|는)\s*(?:신중|주의|유의)"
    r"|(?:시장\s*상황|시장\s*동향|변동성)(?:을|를|에)?\s*(?:주의\s*깊게|면밀히|지속적으로|꾸준히)?\s*(?:주시|모니터링|살펴|지켜)"
    r"|전문가(?:와|의)?\s*(?:상담|조언)"
    r"|(?:주가|주식\s*시장)(?:은|는)\s*(?:예측하기\s*어렵|예측할\s*수\s*없|불확실)"
    r"|향후\s*(?:주가|동향|추이|흐름)(?:를|을)?\s*(?:지켜|주시|살펴)"
)
_TOOL_LEAK_RE = re.compile(r"\b(?:stock|news|disclosure|rag_search|market|fx|discovery|event_reaction)_tool\b")
_INTRO_RE = re.compile(r"(?:다음과|아래와)\s*같습니다[.。]?$")
_CAUSE_HEAD_RE = re.compile(r"^#{1,6}\s*원인")
_CAUSE_WORD_RE = re.compile(
    r"뉴스|기사|보도|공시|발표|계약|수주|실적|시장|코스피|코스닥|지수|업종|금리|환율|정책|수요|공급|때문|영향|덕분|배경|이유|원인|소식|이슈"
    r"|전망|매도|매수|외국인|기관"
)
_DIGITS_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_EMPTY_BULLETS_RE = re.compile(r"^\s*(?:[-*•]\s*)+$")
_BULLET_RE = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+)")


def _nums(s: str) -> set[str]:
    return {x.replace(",", "") for x in _DIGITS_RE.findall(s)}


def strip_generic(text: str) -> tuple[str, int]:
    """(정리한 글, 지운 문장 수). 남는 본문이 없으면 빈 문자열(호출측이 코드 요약으로 바꾼다)."""
    removed = 0
    seen_nums: set[str] = set()
    out_lines: list[str] = []
    state = {"cause": False, "start": 0, "intros": []}  # 지금 절: 원인 분석인지, 시작 줄, 안내 문장만 남은 줄

    def close_section():
        nonlocal removed
        body = [i for i in range(state["start"], len(out_lines))
                if out_lines[i].strip() and not out_lines[i].lstrip().startswith("#")]
        if body and set(body) <= set(state["intros"]):
            for i in body:
                out_lines[i] = ""
                removed += 1

    for line in (text or "").split("\n"):
        if line.lstrip().startswith("#"):
            close_section()
            state.update(cause=bool(_CAUSE_HEAD_RE.match(line.strip())), start=len(out_lines), intros=[])
            out_lines.append(line)
            continue
        if _EMPTY_BULLETS_RE.match(line):  # 48차: 글머리 기호만 있는 줄("-   -   -   -")은 생성 잔해다
            removed += 1
            continue
        m = _BULLET_RE.match(line)
        prefix, body = (m.group(1), line[m.end():]) if m else ("", line)
        kept = []
        for sent in _SENT_RE.split(body):
            if not sent.strip():
                continue
            nums = _nums(sent)
            drop = bool(_TOOL_LEAK_RE.search(sent)) or (not nums and bool(_GENERIC_RE.search(sent)))
            if not drop and state["cause"] and nums and nums <= seen_nums and not _CAUSE_WORD_RE.search(sent):
                drop = True
            if drop:
                removed += 1
                continue
            kept.append(sent)
        if not kept:
            if not body.strip():
                out_lines.append(line)  # 빈 줄은 문단 구분으로 남긴다
            continue
        joined = prefix + " ".join(kept)
        if all(_INTRO_RE.search(k.strip()) for k in kept):
            state["intros"].append(len(out_lines))
        out_lines.append(joined)
        seen_nums |= _nums(joined)
    close_section()
    result = "\n".join(out_lines)
    if removed:
        result = _drop_empty_headings(result)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    if not re.sub(r"^#.*$", "", result, flags=re.M).strip():
        return "", removed
    return result, removed
