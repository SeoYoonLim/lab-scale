"""종목만 바꾼 후속 질문(2026-11-23).

브라우저 테스트: "삼성전자 최근 20거래일 수익률 분석해 줘." 다음에 "SK하이닉스는?"이라고 물으면 이 질문이 LLM 경로로 가서 모델이
news_tool만 불렀다. 20거래일 수익률은 계산되지 않았고("기록하지 못했습니다"), 직전 대화의 삼성전자 기사까지 답에 섞였다.

이제 후속 질문이 "종목 이름 + 조사·연결어"뿐이면(종목만 바꿔 같은 것을 묻는 말) 직전 질문의 종목 자리만 새 종목으로 바꾼 질문으로
처음부터 다시 답한다. 그 질문에는 직전 대화(previous)를 넘기지 않는다: 새 종목의 시세·수익률·기사·공시를 새로 조회·계산하고, 직전 종목의
수치·기사가 맥락으로 들어가지 않는다. 판정은 보수적이다.
- 새 질문에 종목이 정확히 하나이고, 종목 이름을 빼면 조사·연결어("는", "도", "그럼", "어때", "What about", "は", "呢")만 남을 때
- 직전 질문에서 종목이 정확히 하나일 때(그 자리를 바꾼다). 직전 질문도 이런 후속 질문이면 그 앞 리포트로 최대 MAX_HOPS번 거슬러 간다
- 직전 질문의 종목과 새 종목이 같으면 바꾸지 않는다(기존 흐름)
- 기간·의도 낱말("5거래일", "전망", "뉴스", "왜")이 있으면 새 질문이 우선이다(기존 흐름)
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

MAX_HOPS = 3
MAX_QUESTION_CHARS = 40

# 종목 이름을 뺀 나머지가 이 낱말들로만 되어 있으면 "종목만 바꾼 후속 질문"이다(공백·문장부호를 지운 뒤 소문자로 비교).
# 한국어: 조사·연결어·"어때"류. 영어·일본어·중국어는 intent_i18n.normalize 뒤의 글(종목명은 한국어 등록명으로 바뀐다)에 남는 낱말이다.
_FILLERS = (
    # 한국어 연결어(앞)
    "그럼", "그러면", "그렇다면", "그리고", "이번엔", "이번에는", "다음은", "다음으로", "혹시", "또", "그럼이번엔",
    # 한국어 조사(종목명 바로 뒤)
    "은요", "는요", "도요", "이요", "가요", "의경우는", "의경우", "경우는", "은", "는", "도", "이", "가", "요",
    # 한국어 서술(뒤)
    "어때요", "어때", "어떄", "어떤가요", "어떻습니까", "어떻게돼요", "어떻게돼", "어떻게됐어", "어떻게", "어떨까",
    "같은기준으로", "같은기준", "같은걸로", "똑같이", "마찬가지로", "동일하게", "같이",
    "해줘요", "해줘", "해주세요", "해봐", "해봐줘", "부탁해요", "부탁해", "알려줘요", "알려줘", "알려주세요", "봐줘", "보여줘",
    "분석해줘", "분석해주세요", "분석해", "확인해줘",
    # 영어(소문자, 공백 제거)
    "whatabout", "howabout", "andfor", "and", "then", "also", "too", "now", "same", "forit", "please", "ok", "okay",
    # 일본어
    "じゃあ", "では", "それでは", "それなら", "について", "はどうですか", "はどう", "はどうでしょう", "の場合は", "の場合",
    "どうですか", "どう", "も", "は",
    # 중국어
    "那么", "那", "的话", "怎么样", "如何", "呢", "也", "吧",
)
_FILLER_RE = re.compile("(?:" + "|".join(re.escape(f) for f in sorted(set(_FILLERS), key=len, reverse=True)) + ")*")
_DROP_RE = re.compile(r"[\s?？!！.。,，~…·'\"“”‘’()（）\[\]]+")
_HANGUL_SYL = re.compile(r"[가-힣]")


def _rest(text: str, spans: list[tuple[int, int]]) -> str:
    out, last = [], 0
    for st, en in spans:
        out.append(text[last:st])
        last = en
    out.append(text[last:])
    return _DROP_RE.sub("", "".join(out)).casefold()


def is_subject_only(text: str, spans: list[tuple[int, int]]) -> bool:
    """종목 언급(spans, text 기준 위치)을 빼면 조사·연결어만 남는가. 종목 언급이 없으면 False."""
    if not spans or len(text.strip()) > MAX_QUESTION_CHARS:
        return False
    rest = _rest(text, spans)
    return _FILLER_RE.fullmatch(rest) is not None


def _has_batchim(ch: str) -> bool | None:
    if not _HANGUL_SYL.fullmatch(ch or ""):
        return None
    return (ord(ch) - 0xAC00) % 28 != 0


_JOSA_PAIRS = {"은": ("은", "는"), "는": ("은", "는"), "이": ("이", "가"), "가": ("이", "가"), "을": ("을", "를"), "를": ("을", "를"),
               "과": ("과", "와"), "와": ("과", "와")}


def fix_josa(name: str, after: str) -> str:
    """바꾼 이름 바로 뒤 조사를 새 이름의 받침에 맞춘다('삼성전자는' -> 'LG에너지솔루션은'). 이름이 한글로 끝나지 않으면 그대로."""
    if not after or after[0] not in _JOSA_PAIRS or (len(after) > 1 and _HANGUL_SYL.fullmatch(after[1])):
        return after
    batchim = _has_batchim(name[-1:])
    if batchim is None:
        return after
    with_b, without_b = _JOSA_PAIRS[after[0]]
    return (with_b if batchim else without_b) + after[1:]


def _mentions(db, text: str):
    from app.tools.company_resolver import find_mentions, mention_text

    base = mention_text(text)
    if len(base.casefold()) != len(base):  # 대소문자 접기로 길이가 바뀌면 위치가 맞지 않는다(드묾) -> 바꾸지 않는다
        return base, None
    return base, find_mentions(db, text)


def _base_question(db, previous: dict, user_id: int | None, normalize, new_company) -> tuple[str, str] | None:
    """(직전 분석 질문을 새 종목으로 바꾼 글, 직전 종목 이름). 바꿀 수 없으면 None."""
    from app.reports import get_report

    prev = previous
    for _ in range(MAX_HOPS + 1):
        question = normalize(prev.get("question") or "")
        base, hits = _mentions(db, question)
        if hits is None:
            return None
        companies = {c.id: c for _, _, c in hits}
        if len(companies) == 1:
            if is_subject_only(base, [(st, en) for st, en, _ in hits]):
                # 직전 질문도 "종목만 바꾼 후속 질문"이다 -> 그 앞 리포트(같은 사용자)의 질문으로 거슬러 간다
                if not prev.get("previous_report_id") or user_id is None:
                    return None
                older = get_report(prev["previous_report_id"], user_id=user_id)
                if older is None:
                    return None
                prev = {**older, "question": older.get("question") or ""}
                continue
            old = next(iter(companies.values()))
            if old.id == new_company.id:
                return None
            new = new_company.name
            out, last = [], 0
            for st, en, _ in hits:
                seg = base[last:st]
                out += [fix_josa(new, seg) if last else seg, new]
                last = en
            out.append(fix_josa(new, base[last:]))
            return "".join(out), old.name
        return None
    return None


def rewrite(question: str, previous: dict | None, user_id: int | None = None, normalize=None) -> dict | None:
    """종목만 바꾼 후속 질문이면 {"question": 바꾼 질문, "from": 직전 종목, "to": 새 종목, "base": 직전 분석 질문}. 아니면 None.

    question은 이미 정규화된 글(app.intent_i18n.normalize 결과)이다. normalize는 직전 리포트 질문에 같은 정규화를 하는 함수(없으면 그대로).
    DB를 읽지 못하면 None(기존 흐름)."""
    if not previous or not question:
        return None
    norm = normalize or (lambda s: s)
    try:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            base, hits = _mentions(db, question)
            if not hits or len({c.id for _, _, c in hits}) != 1:
                return None
            if not is_subject_only(base, [(st, en) for st, en, _ in hits]):
                return None
            new_company = hits[0][2]
            found = _base_question(db, previous, user_id, norm, new_company)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 - 판정 실패는 기존 흐름으로
        logger.warning("종목 교체 후속 질문 판정 실패: %s", type(e).__name__)
        return None
    if found is None:
        return None
    rewritten, old_name = found
    return {"question": rewritten, "from": old_name, "to": new_company.name}
