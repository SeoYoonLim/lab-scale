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
    """숫자 표기 정리 + 반복 제거. (정리한 글, 지운 반복 수)."""
    return drop_repeats(normalize_numbers(text))
