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
목록(2026-11-20): 목록 표시("4. ", "- ")는 문장으로 보지 않는다. 번호 목록 항목끼리 같은 문장은 다른 항목이라 남기고,
본문이 모두 지워진 목록 줄은 표시도 남기지 않는다.
"""

from __future__ import annotations

import re

_MONEY_RE = re.compile(r"(?<![\d.,])(?P<int>\d{4,})(?P<frac>\.\d+)?(?P<sp>\s*)(?P<unit>원|주(?!일|간|차|기|년|가|식))")
_PCT_RE = re.compile(r"(?<![\d.])(?P<num>[+\-−]?\d+\.\d{2})(?P<zeros>0+)(?=\s*%)")
_SENT_RE = re.compile(r"(?<=[.!?。])\s+")
_LIST_MARK_RE = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+)")


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
    seen_items: set[str] = set()  # 번호 목록 항목에서 본 문장
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
            # 2026-11-20: 목록 표시("4. ", "- ")를 떼고 본문만 문장으로 나눈다. 전에는 "4."가 한 문장으로 잘려 남고 본문이 반복으로
            # 지워져 "4."만 남았다(최종 검증: 접수번호가 다른 같은 제목 공시 3건 중 2건이 번호만 남음). 번호 목록 항목끼리 같은 문장은
            # 서로 다른 항목(같은 제목의 공시·기사)이라 남긴다. 앞 산문을 되풀이한 문장은 전처럼 지운다
            m = _LIST_MARK_RE.match(line)
            prefix, body = (m.group(1), line[m.end():]) if m else ("", line)
            numbered = bool(m) and prefix.strip()[0].isdigit()
            kept = []
            for s in _SENT_RE.split(body):
                k = _norm(s)
                if len(k) >= min_chars and k in seen_sents and not (numbered and k in seen_items):
                    removed += 1
                    continue
                seen_sents.add(k)
                if numbered:
                    seen_items.add(k)
                kept.append(s)
            if kept:
                lines.append(prefix + " ".join(kept))
            elif not line.strip():
                lines.append(line)
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
    # 2026-11-23: 실제 출처 없이 근거가 있는 것처럼 쓰는 안내("뉴스나 공시에서 이유를 찾을 수 있습니다", "관련 뉴스와 공시를 확인해 보세요")
    r"|(?:뉴스|공시|기사)(?:나|와|과|및|·|,)?\s*(?:뉴스|공시|기사)?\s*(?:에서|를\s*통해|을\s*통해|를\s*보면|을\s*보면)\s*(?:그\s*|주가\s*변동의\s*|변동\s*)?"
    r"(?:이유|원인|배경)(?:을|를)?\s*(?:찾|확인|알|파악)"
    r"|(?:관련\s*)?(?:뉴스|공시)(?:나|와|과|및)?\s*(?:뉴스|공시)?(?:를|을)\s*(?:확인|참고|살펴)(?:해\s*보|하)(?:시|세|면|는\s*것)"
)
# 2026-11-18: "news_tool에"처럼 한글 조사가 바로 붙으면 \b가 경계로 보지 않았고, "Disclosure_tool"·"RAG_search_tool"처럼 대소문자를 바꿔 쓰면
# 놓쳤다(최종 리허설 SK하이닉스 흐름 답). 영문·숫자·밑줄이 앞뒤에 없을 때를 경계로 보고 대소문자를 가리지 않는다
_TOOL_LEAK_RE = re.compile(r"(?<![A-Za-z0-9_])(?:stock|news|disclosure|rag_search|market|fx|discovery|event_reaction)_tool(?![A-Za-z0-9_])",
                           re.IGNORECASE)
# 2026-11-21: 도구 이름 말고도 내부 구현이 드러나는 문장(사용자가 이해할 필요가 없는 개발 정보). 브라우저 테스트에서 "parameter", "limit"이
# 답에 나왔다. 원인 하나는 도구 오류 결과("news_tool 호출에 필요한 인자가 없습니다: limit", 예외 원문)가 LLM에 그대로 들어간 것이다
# (app.agent._llm_safe_result가 이제 LLM에 넘기기 전에 일반 문장으로 바꾼다). 낱말 하나만으로 지우지 않는다: "limit"·"parameter"는
# 영어 금융 문장에도 나오므로 내부 문맥(=·:·호출·함수·인자·API와 함께)일 때만이다.
# 1) 오류·예외·SQL·스택 정보, 2) 내부 필드 이름(snake_case)·키=값 표기, 3) 인자·파라미터를 호출·함수·API와 함께 말한 문장, 4) DB 테이블·컬럼 이름
_INTERNAL_ERROR_RE = re.compile(
    r"Traceback\s*\(most recent call last\)|\b(?:psycopg2?|sqlalchemy|IntegrityError|OperationalError|ProgrammingError|DBAPIError"
    r"|KeyError|TypeError|ValueError|AttributeError|RuntimeError|IndexError|JSONDecodeError|NoneType|Exception)\b"
    r"|\bSQL\b|stack\s*trace|스택\s*트레이스|\bHTTP\s*[45]\d\d\b|status[_\s]?code"
)
_INTERNAL_FIELD_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:period_days|company_name|corrected_from|called_via|before_kst|close_price|change_pct|period_change_pct|tool_calls?"
    r"|price_date|trading_days|period_start_date|period_end_date|source_status|previous_report_id|rcept_no|corp_code|since_days)(?![A-Za-z0-9_])"
    r"|\bfound\s*[=:]\s*(?:false|true|False|True)\b|\b(?:limit|ticker|query|offset)\s*[=:]\s*\S"
    r"|(?<![A-Za-z0-9_])[a-z]+_[a-z_]+\s*\("
)
_INTERNAL_ARG_RE = re.compile(
    r"(?:호출|함수|도구|툴|API|tool|function)[^.!?。\n]{0,20}(?:인자|파라미터|parameter|매개\s*변수|argument)"
    r"|(?:인자|파라미터|parameter|매개\s*변수|argument)[^.!?。\n]{0,20}(?:호출|함수|도구|툴|API|tool|function|\blimit\b|=)"
    r"|\blimit\b[^.!?。\n]{0,15}(?:파라미터|parameter|인자|값|설정|argument)|(?:파라미터|parameter|인자|argument)[^.!?。\n]{0,15}\blimit\b",
    re.IGNORECASE,
)
_INTERNAL_TABLE_RE = re.compile(r"\b[a-z_]+\s*(?:테이블|컬럼|칼럼)|\b(?:table|column)\s+[a-z_]+_[a-z_]+\b")


def has_internal_leak(sentence: str) -> bool:
    """문장이 내부 오류·필드 이름·호출 인자·DB 테이블 이름을 드러내는가(위 설명). 일반 영어 금융 문장의 limit·parameter는 아니다."""
    s = sentence or ""
    return bool(_TOOL_LEAK_RE.search(s) or _INTERNAL_ERROR_RE.search(s) or _INTERNAL_FIELD_RE.search(s)
                or _INTERNAL_ARG_RE.search(s) or _INTERNAL_TABLE_RE.search(s))


NO_CAUSE_EVIDENCE_NOTE = "※ 조회된 뉴스·공시 중 이 주가 움직임의 원인을 뒷받침하는 자료가 없어 원인을 특정하지 않았습니다."
_MARKET_WORD_RE = re.compile(r"코스피|코스닥|시장|지수|업종|KOSPI|KOSDAQ", re.IGNORECASE)
# 원인 절 제목: 마크다운 제목(#) 또는 한 줄 전체가 굵은 글씨인 제목 중 "원인"이 든 것("## 원인 분석", "## 하락 원인", "**원인 분석**").
# strip_generic의 _CAUSE_HEAD_RE("원인"으로 시작하는 # 제목)보다 넓다. 이 판정은 출처가 없을 때만 쓴다.
_CAUSE_TITLE_RE = re.compile(r"^(?:#{1,6}\s*.*원인.*|\*\*[^*]*원인[^*]*\*\*\s*:?)$")
_BOLD_TITLE_RE = re.compile(r"^\*\*[^*]+\*\*\s*:?$")


# 2026-11-23: 근거 절 제목("## 뉴스·공시 근거", "## 관련 뉴스", "**공시**"). 출처가 없으면 절째 지운다(일반론으로 빈 근거 절을 채우지 않는다)
_EVIDENCE_TITLE_RE = re.compile(r"^(?:#{1,6}\s*.*(?:뉴스|공시|근거|기사|보도).*|\*\*[^*]*(?:뉴스|공시|근거|기사)[^*]*\*\*\s*:?)$")
NO_DIRECT_EVIDENCE_NOTE = ("※ 조회된 기사는 업종·시장 전반을 다룬 내용이라, 현재 확인된 자료만으로는 {name}의 개별적인 주가 변동 원인을 "
                           "특정하기 어렵습니다.")
REFERENCE_HEADING = "## 업종·시장 참고"
REFERENCE_INTRO = "아래 기사는 여러 기업이나 업종·시장 전반을 다룬 기사로, {name}의 직접 원인 근거가 아닙니다."
NO_EVIDENCE_NOTE = "※ 현재 확인된 관련 뉴스·공시가 없습니다."
# 근거 절이 자료가 없다고 밝힌 내용인가("관련 뉴스가 없습니다", "공시를 찾지 못했습니다")
_SAYS_NONE_RE = re.compile(r"없습니다|없어|없음|없었|찾지\s*못|확인되지\s*않|조회되지\s*않")
# 답이 이미 "원인을 특정할 수 없다"고 밝혔으면 안내를 덧붙이지 않는다
_ALREADY_NO_CAUSE_RE = re.compile(r"원인(?:을|은|이)?\s*(?:특정|판단|확인|단정|알)[^.。\n]{0,20}(?:없|어렵|않)")


def enforce_cause_section(text: str, has_sources: bool, has_market: bool, direct_evidence: bool | None = None,
                          references: list[str] | None = None, cause_question: bool = False,
                          subject: str | None = None) -> tuple[str, bool]:
    """(정리한 글, 바꿨는지). 2026-11-21: 뉴스·공시 근거(sources)가 없는데 LLM이 "## 원인 분석" 절을 쓰면 근거가 있는 것처럼 보인다.
    근거가 있으면 그대로다. 없으면 그 절을 지우고 근거가 없다는 안내 한 줄로 바꾼다. 시장 비교 결과(market_tool)가 있으면 그 절에서
    시장·지수를 숫자로 비교한 문장만 "## 시장과 비교" 절로 남긴다(원인 단정이 아니라 조회된 수치다).

    2026-11-23:
    - 출처가 없으면 "## 뉴스·공시 근거" 같은 근거 절도 지운다(빈 근거 절을 일반 문장으로 채운 경우). 그 절이 자료가 없다고 밝힌
      내용이면("관련 뉴스가 없습니다") 그대로 둔다. 다 지워져 본문이 없으면 자료가 없다는 안내 한 줄이다.
    - (새 인자) direct_evidence=False(원인 질문인데 출처가 업종·시장 기사뿐, 이 기업 직접 기사·공시 없음): 출처가 있어도 원인 절을
      "개별 원인을 특정하기 어렵다"는 안내로 바꾸고, 근거 절은 references(코드가 만든 업종·시장 참고 줄, 조회된 제목 그대로)로 바꾼다.
    - (새 인자) cause_question=True인데 원인 절이 없었으면 같은 안내를 끝에 한 줄 붙인다(이미 그렇게 밝혔으면 붙이지 않는다)."""
    if not text:
        return text, False
    no_direct = has_sources and direct_evidence is False
    if has_sources and not no_direct:
        return text, False
    name = subject or "이 종목"
    note = NO_DIRECT_EVIDENCE_NOTE.format(name=name) if no_direct else NO_CAUSE_EVIDENCE_NOTE
    ref_block = [REFERENCE_HEADING, REFERENCE_INTRO.format(name=name), *references, ""] if no_direct and references else []
    lines = text.split("\n")
    out: list[str] = []
    changed = noted = ref_done = False
    i = 0
    while i < len(lines):
        line = lines[i]
        title = line.strip()
        is_cause = bool(_CAUSE_TITLE_RE.match(title))
        is_evidence = not is_cause and bool(_EVIDENCE_TITLE_RE.match(title))
        if not (is_cause or is_evidence):
            out.append(line)
            i += 1
            continue
        j = i + 1
        body = []
        while j < len(lines) and not lines[j].lstrip().startswith("#") and not _BOLD_TITLE_RE.match(lines[j].strip()):
            body.append(lines[j])
            j += 1
        if is_evidence and not no_direct and _SAYS_NONE_RE.search("\n".join(body)):
            out.extend(lines[i:j])  # 자료가 없다고 밝힌 근거 절은 사실이다
            i = j
            continue
        changed = True
        if is_cause:
            kept = []
            if has_market:
                for b in body:
                    m = _BULLET_RE.match(b)
                    prefix, rest = (m.group(1), b[m.end():]) if m else ("", b)
                    sents = [x for x in _SENT_RE.split(rest) if x.strip() and _MARKET_WORD_RE.search(x) and _DIGITS_RE.search(x)]
                    if sents:
                        kept.append(prefix + " ".join(sents))
            if kept:
                out.extend(["## 시장과 비교", *kept, ""])
            if not noted:
                out.extend([note, ""])
                noted = True
        elif ref_block and not ref_done:
            out.extend(ref_block)
            ref_done = True
        i = j
    if cause_question and not noted and not _ALREADY_NO_CAUSE_RE.search("\n".join(out)):
        out.extend(["", note])
        changed = noted = True
    if ref_block and not ref_done and noted:
        out.extend(["", *ref_block])
        changed = True
    if not changed:
        return text, False
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    if not re.sub(r"^#.*$", "", result, flags=re.M).strip():
        return NO_EVIDENCE_NOTE, True
    return result, True


def strip_internal(text: str) -> tuple[str, int]:
    """(정리한 글, 지운 문장 수). 내부 정보를 드러낸 문장만 지운다(최종 답변 마지막 점검용, 번역된 답에도 쓴다).
    소제목·빈 줄은 그대로 두고, 지운 뒤 본문이 없으면 빈 문자열(호출측이 일반 안내로 바꾼다)."""
    removed = 0
    out = []
    for line in (text or "").split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            out.append(line)
            continue
        m = _BULLET_RE.match(line)
        prefix, body = (m.group(1), line[m.end():]) if m else ("", line)
        sents = [x for x in _SENT_RE.split(body) if x.strip()]
        kept = [x for x in sents if not has_internal_leak(x)]
        removed += len(sents) - len(kept)
        if kept:
            out.append(prefix + " ".join(kept))
    if not removed:
        return text, 0
    result = re.sub(r"\n{3,}", "\n\n", _drop_empty_headings("\n".join(out))).strip()
    if not re.sub(r"^#.*$", "", result, flags=re.M).strip():
        return "", removed
    return result, removed


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
            drop = has_internal_leak(sent) or (not nums and bool(_GENERIC_RE.search(sent)))
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
