"""사건 이후 주가 반응 질문을 Ollama(llama3.1:8b)로 실제 호출해 최종 답변을 tool 결과와 대조한다(pytest -m slow).

질문 10개 x 3회. 리포트를 저장하지 않으므로(_answer 직접 호출) 사용자·리포트가 생기지 않는다. 오류 5개 항목은 답변 검증기
(app.event_answer.check_event_answer)와 별개로 센다:
 (1) 조회 결과에 없는 수치: 답변의 수치(사건 제목·날짜·시각 제외)가 tool 결과 JSON에 나온 수치(반올림 표기 포함)나 고정 문구의 수치가 아님
 (2) 인과 단정: '## 해석'의 문장에 원인 표현이 있고 단정하지 않는다는 말이 없음
 (3) null 추정: 계산되지 않은(표본 0건) 기간에 수치·방향을 붙인 해석 문장
 (4) 템플릿 잔재: 빈칸·자리표시·가린 수치·역할 접두어·중괄호
 (5) 고정 문구 누락: 답변 끝이 이 기능 전용 면책 문구가 아님
EVENT_SLOW_REPORT 환경변수에 경로를 주면 실행별 결과(답변, 지운 문장, 오류)를 JSON 줄로 덧붙인다.
"""

import json
import os
import re
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.agent import MODEL_NAME, _answer
from app.event_reaction import DISCLAIMER

pytestmark = [pytest.mark.slow, pytest.mark.integration]

QUESTIONS = [
    "삼성전자 최근 공시 이후 주가 반응은?",
    "삼성전자 실적 발표 후 주가 어땠어?",
    "SK하이닉스 공시 나온 뒤 주가 움직임 알려줘",
    "KT 뉴스 이후 주가 반응은?",
    "알테오젠 공시 이후 한 달 흐름",
    "KODEX 200 뉴스 이후 주가 반응",
    "금호타이어 기사 나온 뒤 주가",
    "삼성전자 자기주식취득 공시 이후 주가",
    # 최근 사건이라 5/20거래일 값이 null인 경우를 유도
    "삼성전자 오늘 나온 뉴스 이후 주가 반응은?",
    "KT 최근 뉴스 나온 뒤 20거래일 동안 주가는 어땠어?",
]
REPS = 3

_NUM = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?")
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:[+-]\d{2}:\d{2})?)?|\d{1,2}월\s?\d{1,2}일|\d{4}년")
_CAUSAL = re.compile(r"때문|영향으로|영향을\s*(?:받|주|줬|미)|덕분|덕에|탓|원인|(?:으)?로\s*인(?:해|한)|힘입|여파|반영|에\s*반응|호재|악재")
_HEDGE = re.compile(r"단정(?:할\s*수\s*없|하기\s*어렵)|볼\s*수\s*(?:는\s*)?없|알\s*수\s*없|인과")
_RESIDUE = re.compile(r"(?m)빈칸|빈 칸|이 섹션은|N/?A|\[수치 생략\]|\{|\}|^assistant\b")
_HORIZON = re.compile(r"(?<!\d)(1|5|20)\s?(?:거래)?일")
_MOVE = re.compile(r"%|상승|하락|올랐|내렸|떨어|오른|내린")
_NOT_YET = re.compile(r"아직|계산할\s*수\s*없|지나지\s*않|없습니다|없어")
# 코드가 쓰는 고정 문구 속 수치(30% 가격 조정, 3건 미만, 20거래일 평균, 조회 기간 120일)
_FIXED_NUMBERS = {Decimal(x) for x in ("1", "5", "20", "30", "3", "120")}


def _variants(x: Decimal) -> set[Decimal]:
    x = abs(x)
    return {x.normalize(), *(x.quantize(Decimal(q), rounding=ROUND_HALF_UP).normalize() for q in ("1", "0.1", "0.01"))}


def result_numbers(result: dict) -> set[Decimal]:
    out = set(_FIXED_NUMBERS)
    for m in _NUM.findall(json.dumps(result, ensure_ascii=False)):
        try:
            out |= _variants(Decimal(m.replace(",", "")))
        except Exception:
            pass
    return out


def interpretation(answer: str) -> str:
    if "## 해석" not in answer:
        return ""
    return answer.split("## 해석", 1)[1].rsplit("\n\n※ ", 1)[0]


def count_errors(answer: str, result: dict) -> dict:
    titles = sorted({e["title"] for e in result.get("events") or []}, key=len, reverse=True)
    body = answer.replace(DISCLAIMER, " ")
    for t in titles:
        body = body.replace(t, " ")
    body = _DATE_TIME.sub(" ", body)
    allowed = result_numbers(result)
    bad_numbers = []
    for m in _NUM.findall(body):
        v = Decimal(m.replace(",", ""))
        if abs(v).normalize() not in allowed:
            bad_numbers.append(m)

    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n", interpretation(answer)) if s.strip()]
    causal = [s for s in sentences if _CAUSAL.search(s) and not _HEDGE.search(s)]
    summary = result.get("summary") or {}
    null_est = []
    for s in sentences:
        for h in {int(x) for x in _HORIZON.findall(s)}:
            if summary.get(f"with_d{h}", 0) == 0 and _MOVE.search(s) and not _NOT_YET.search(s):
                null_est.append(s)
    residue = _RESIDUE.findall(answer)
    return {
        "numbers": bad_numbers,
        "causal": causal,
        "null_estimate": null_est,
        "residue": residue,
        "disclaimer_missing": not answer.rstrip().endswith("※ " + DISCLAIMER),
    }


@pytest.mark.parametrize("rep", range(REPS))
@pytest.mark.parametrize("question", QUESTIONS)
def test_event_question(question, rep):
    resp, records = _answer(question, MODEL_NAME)
    assert resp["used_tools"] == ["event_reaction_tool"], (question, resp["used_tools"])
    result = records[0]["result"]
    errors = count_errors(resp["answer"], result)
    path = os.environ.get("EVENT_SLOW_REPORT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "question": question, "rep": rep, "answer": resp["answer"], "errors": errors,
                "dropped": result.get("dropped_sentences", []), "found": result.get("found"),
                "summary": result.get("summary"), "sources": len(resp["sources"]),
            }, ensure_ascii=False) + "\n")
    assert all(not v for v in errors.values()), (question, errors, resp["answer"])
