"""
Ollama tool calling 기반 리서치 에이전트.

scripts/test_tool_calling.py에서 검증했던 ask() 로직을 재사용 가능한 형태로 옮긴 것.
FastAPI 라우트(app/api/research.py)와 CLI 스모크 테스트 스크립트가 이 모듈을 공유한다.
"""

import inspect
import json
import re
import time
from datetime import datetime, timezone

import ollama

from app.db.session import SessionLocal
from app.reports import save_report as save_report_row
from app.sources import build_sources
from app.tools.company_resolver import extract_from_text, normalize_name, resolve_company
from app.tools.disclosure_tool import disclosure_tool
from app.tools.news_tool import news_tool
from app.tools.rag_search_tool import rag_search_tool
from app.tools.stock_tool import stock_tool
from app.utils import extract_answer_text

# 벤치마크(scripts/benchmark_models.py, few-shot 오염 제거 후 재실행) 결과 llama3.1:8b로 확정:
# 총점은 qwen2.5:7b-instruct가 근소하게 앞섰지만(10/15 vs 9/15), qwen은 multi_tool
# 실패 시 tool 없이 가짜 등락률·가짜 뉴스 제목을 지어내는 hallucination을 보인 반면
# llama는 no_tool 실패(불필요한 tool 호출) 시에도 답변 내용 자체는 사실 왜곡이 없어
# 금융 정보 서비스 특성상 더 안전한 실패 모드로 판단.
MODEL_NAME = "llama3.1:8b"

SYSTEM_PROMPT = (
    "너는 한국 주식 투자자를 돕는 리서치 어시스턴트다. "
    "반드시 한국어로만 답변하고 다른 언어를 절대 섞지 마라. "
    "주가·등락률·거래량 등 수치 조회가 필요한 질문에는 stock_tool을 호출하고, "
    "최근 이슈나 '왜 올랐는지/내렸는지' 같이 뉴스가 필요한 질문에는 news_tool을 호출하라. "
    "공시나 공식 발표(자사주 매입, 사업보고서 등)가 필요한 질문에는 disclosure_tool을 호출하라. "
    "news_tool/disclosure_tool은 '최신순으로 N건 그대로' 가져오는 조회용이다. 반면 특정 "
    "주제나 맥락에 대해 의미적으로 관련된 근거를 찾아야 하는 질문(예: '삼성전자 반도체 업황 "
    "관련 근거 찾아줘', '자사주 매입 관련 공시 내용 자세히 알려줘'처럼 최신순이 아니라 특정 "
    "주제·키워드에 대한 관련도 기준 검색이 필요한 경우)에는 rag_search_tool을 호출하라. "
    "여러 종류의 정보가 필요한 질문이면 해당하는 tool들을 한 번의 응답에서 함께(동시에) 호출하라. "
    "일반적인 용어 설명이나 개념 질문에는 절대 tool을 호출하지 말고 바로 답변하라. "
    "답변은 절대 JSON 형식으로 하지 말고, 자연스러운 문장으로 답하라. "
    "아래 대화 예시의 판단 기준(멀티 tool 동시 호출 / 개념 질문은 tool 미호출)을 그대로 따르라."
)

# 실제 Ollama tool-calling 메시지 포맷(assistant.tool_calls / role=tool)을 그대로 흉내낸
# few-shot 예시. system prompt만으로는 "여러 tool을 한 번에 호출"하거나 "개념 질문엔
# tool을 아예 안 쓴다"는 판단이 불안정해서, 대화 기록 형태의 예시를 실제 요청 메시지에
# 섞어 넣어 모델이 패턴을 그대로 모방하도록 유도한다.
#
# 주의: 여기 쓰는 질문 문구/종목명은 실제 서비스 질문이나 벤치마크 테스트 질문과
# 겹치면 안 된다. 겹치면 모델이 "이미 답변한 질문"으로 인식해 tool을 호출하지 않고
# 이 few-shot 안의 가짜 데이터를 그대로 베껴서 답하는 현상이 있었다
# (scripts/benchmark_models.py 1차 실행에서 qwen2.5:7b-instruct가 5/5 재현, 자세한
# 내용은 해당 스크립트 상단 주석 참고). 그래서 예시는 삼성전자가 아닌 SK하이닉스로,
# 문구도 실제 테스트 케이스와 다르게 구성한다.
FEW_SHOT_MESSAGES = [
    # 예시 1: 여러 정보가 필요한 질문 -> stock_tool + news_tool을 한 응답에서 동시에 호출
    {
        "role": "user",
        "content": "SK하이닉스 요즘 흐름이 심상치 않던데, 주가 움직임이랑 관련 소식 같이 정리해줄 수 있어?",
    },
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "stock_tool",
                    "arguments": {"ticker": "SK하이닉스", "period_days": 1},
                }
            },
            {
                "function": {
                    "name": "news_tool",
                    "arguments": {"company_name": "SK하이닉스", "limit": 5},
                }
            },
        ],
    },
    {
        "role": "tool",
        "content": json.dumps(
            {"ticker": "SK하이닉스", "found": True, "change_pct": -2.3, "volume": 9876543},
            ensure_ascii=False,
        ),
    },
    {
        "role": "tool",
        "content": json.dumps(
            {
                "company_name": "SK하이닉스",
                "found": True,
                "news": [{"title": "SK하이닉스, HBM 수주 확대 소식에 매수세 유입", "source": "example.com"}],
            },
            ensure_ascii=False,
        ),
    },
    {
        "role": "assistant",
        "content": (
            "SK하이닉스는 오늘 2.3% 하락했고 거래량은 9,876,543주였습니다. 다만 HBM 수주 확대 "
            "소식이 전해지며 매수세가 유입되고 있다는 뉴스도 함께 확인됩니다."
        ),
    },
    # 예시 2: DB 조회가 필요 없는 일반 개념 질문 -> tool 호출 없이 바로 답변
    {
        "role": "user",
        "content": "공모주 청약이 뭔지 쉽게 설명해줘.",
    },
    {
        "role": "assistant",
        "content": (
            "공모주 청약은 기업이 상장하기 전에 일반 투자자에게 정해진 가격으로 주식을 미리 "
            "배정받을 수 있게 하는 절차입니다. 특정 종목의 실시간 데이터 조회가 필요 없는 "
            "일반 지식이라 tool 호출 없이 바로 답변했습니다."
        ),
    },
]

# Ollama에게 알려줄 tool 스펙 (OpenAI function calling과 동일한 형식)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "stock_tool",
            "description": (
                "DB에 저장된 실제 데이터 기준으로, 특정 종목의 최근 N일간 주가"
                "(등락률, 거래량, 종가 등)를 조회한다. 아직 주가가 수집되지 않은"
                "종목이거나 데이터가 없는 경우 found=false와 함께 그 사유를 담은"
                "message를 반환한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": (
                            "종목코드가 아니라 종목명(예: 삼성전자, SK하이닉스)."
                            " company 테이블의 name 컬럼으로 조회한다."
                        ),
                    },
                    "period_days": {
                        "type": "integer",
                        "description": (
                            "조회할 기간(일). 기본값 1. 2 이상이면 응답의 prices"
                            "리스트에 날짜별(등락률/거래량/종가) 데이터가 여러 건 담긴다."
                        ),
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "news_tool",
            "description": (
                "DB에 저장된 실제 데이터 기준으로, 특정 종목과 관련된 최신 뉴스를"
                "조회한다. 뉴스가 필요한 질문에만 호출한다 (예: 최근 이슈, 왜 올랐는지"
                "/내렸는지, 관련 소식 등). 단순 주가·등락률·거래량 수치만 필요한"
                "질문에는 호출하지 않는다. 아직 뉴스가 수집되지 않은 종목이거나"
                "등록되지 않은 종목인 경우 found=false와 함께 그 사유를 담은"
                "message를 반환한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {
                        "type": "string",
                        "description": (
                            "종목명 또는 종목코드(예: 삼성전자, 005930)."
                            " company 테이블의 name 또는 ticker 컬럼으로 조회한다."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "조회할 최근 뉴스 건수. 기본값 5.",
                    },
                },
                "required": ["company_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "disclosure_tool",
            "description": (
                "DB에 저장된 실제 데이터 기준으로, 특정 종목의 최근 공시(DART 전자공시)를"
                "조회한다. 공시·공시내용·공식 발표 관련 질문에만 호출한다 (예: 최근 공시,"
                "자사주 매입 공시, 사업보고서 등). 단순 주가 수치나 일반 뉴스 질문에는"
                "호출하지 않는다. 아직 공시가 수집되지 않은 종목이거나 등록되지 않은"
                "종목인 경우 found=false와 함께 그 사유를 담은 message를 반환한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {
                        "type": "string",
                        "description": (
                            "종목명 또는 종목코드(예: 삼성전자, 005930)."
                            " company 테이블의 name 또는 ticker 컬럼으로 조회한다."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "조회할 최근 공시 건수. 기본값 5.",
                    },
                },
                "required": ["company_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search_tool",
            "description": (
                "news/disclosure 전체 내용을 의미(semantic) 기준으로 검색해, 질문과 관련도가"
                "높은 문서 top-k를 찾는다. news_tool/disclosure_tool처럼 '최신순 N건'을"
                "가져오는 게 아니라, 특정 주제·맥락에 대해 근거가 될 만한 내용을 찾을 때"
                "호출한다 (예: '~관련 근거 찾아줘', '~내용 자세히 알려줘'처럼 특정 이슈에"
                "대한 관련도 기준 검색이 필요한 질문). 임베딩이 아직 채워지지 않았거나"
                "일치하는 문서가 없으면 found=false를 반환한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색하려는 주제/맥락을 자연어 문장으로 표현한 질의.",
                    },
                    "company_name": {
                        "type": "string",
                        "description": (
                            "종목명 또는 종목코드로 검색 범위를 좁힐 때만 지정한다"
                            " (예: 삼성전자, 005930). 특정 종목에 한정하지 않으면 생략한다."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "반환할 최대 문서 건수. 기본값 5.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

AVAILABLE_FUNCTIONS = {
    "stock_tool": stock_tool,
    "news_tool": news_tool,
    "disclosure_tool": disclosure_tool,
    "rag_search_tool": rag_search_tool,
}


FALLBACK_ANSWER = "죄송합니다. 요청을 처리하는 중 문제가 발생했습니다. 종목명을 포함해 질문을 조금 더 구체적으로 다시 해주세요."

_TOOL_NAME_RE = re.compile(r'"name"\s*:\s*"(?:%s)"' % "|".join(AVAILABLE_FUNCTIONS))


def _mentions_tool_call(content: str) -> bool:
    """모델이 tool_calls 대신 tool 호출 JSON을 본문 텍스트로 내보낸 경우인지 판별한다."""
    return bool(content) and bool(_TOOL_NAME_RE.search(content))


def _recover_text_tool_calls(content: str) -> list[dict]:
    """본문에 텍스트로 나온 tool 호출 JSON({"name":..., "parameters":{...}})을 tool_calls 형태로 복구한다.
    JSON이 깨져 있으면(모델이 잘못된 이스케이프를 내보내는 경우) 빈 리스트를 반환한다."""
    decoder = json.JSONDecoder()
    calls, idx = [], 0
    while idx < len(content):
        if content[idx] in " \t\r\n,;":
            idx += 1
            continue
        try:
            obj, idx = decoder.raw_decode(content, idx)
        except json.JSONDecodeError:
            return []
        if not isinstance(obj, dict) or obj.get("name") not in AVAILABLE_FUNCTIONS:
            return []
        params = obj.get("parameters", obj.get("arguments", {}))
        if not isinstance(params, dict):
            return []
        calls.append({"function": {"name": obj["name"], "arguments": params}})
    return calls


def _as_args(fn_args) -> dict:
    """tool 인자를 dict로 맞춘다(JSON 문자열이거나 깨져 있으면 빈 dict)."""
    if isinstance(fn_args, str):
        try:
            fn_args = json.loads(fn_args)
        except json.JSONDecodeError:
            return {}
    return fn_args if isinstance(fn_args, dict) else {}


# tool별로 종목명/티커를 받는 인자 이름
_COMPANY_ARG = {
    "stock_tool": "ticker",
    "news_tool": "company_name",
    "disclosure_tool": "company_name",
    "rag_search_tool": "company_name",
}


def _repair_company_args(calls: list[dict], question: str) -> dict[int, str]:
    """모델이 만든 종목 인자가 DB 종목으로 해석되지 않으면, 질문 원문에서 회사명을 찾아 대신 넣는다.

    모델이 질문 속 회사명을 JSON 인자로 옮기다 엉뚱한 문자열로 깨뜨리는 경우를 위한 fallback이다.
    같은 tool로 이미 정상 해석된 회사는 후보에서 빼고, 남은 후보와 실패한 호출이 일대일로 맞을 때만
    (질문 등장 순서대로) 채운다. 임의로 고르지 않으므로 애매하면 그대로 두어 not-found 응답이 나간다.
    calls를 직접 수정하고, {호출 인덱스: 모델이 만든 원래 인자}를 돌려준다."""
    targets = []
    for i, call in enumerate(calls):
        fn_name = call["function"]["name"]
        key = _COMPANY_ARG.get(fn_name)
        if key is None:
            continue
        args = _as_args(call["function"]["arguments"])
        call["function"]["arguments"] = args
        targets.append((i, fn_name, key, args))
    if not targets:
        return {}

    db = SessionLocal()
    try:
        resolved_names: dict[str, set[str]] = {}
        failed = []
        for i, fn_name, key, args in targets:
            raw = args.get(key)
            # 종목 필터 없이 검색하려는 rag 호출(인자 없음/null)은 손대지 않는다
            if fn_name == "rag_search_tool" and not normalize_name(raw):
                continue
            res = resolve_company(db, raw)
            if res.company is not None:
                resolved_names.setdefault(fn_name, set()).add(res.company.name)
            else:
                failed.append((i, fn_name, key, args, raw))
        if not failed:
            return {}

        candidates = extract_from_text(db, question)
        repaired: dict[int, str] = {}
        for fn_name in {f[1] for f in failed}:
            fails = [f for f in failed if f[1] == fn_name]
            remaining = [c for c in candidates if c.name not in resolved_names.get(fn_name, set())]
            if len(remaining) != len(fails):
                continue
            for (i, _, key, args, raw), company in zip(fails, remaining):
                args[key] = company.name
                repaired[i] = "" if raw is None else str(raw)
        return repaired
    finally:
        db.close()


def _run_tool(fn_name: str, fn_args) -> dict:
    """tool을 안전하게 실행한다. 모르는 tool/인자, 필수 인자 누락, 실행 중 예외도 예외 대신 결과로 돌려준다."""
    fn = AVAILABLE_FUNCTIONS.get(fn_name)
    if fn is None:
        return {"found": False, "error": f"unknown tool: {fn_name}"}

    fn_args = _as_args(fn_args)

    params = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in fn_args.items() if k in params}
    missing = [
        n for n, p in params.items() if p.default is inspect.Parameter.empty and kwargs.get(n) in (None, "")
    ]
    if missing:
        return {"found": False, "message": f"{fn_name} 호출에 필요한 인자가 없습니다: {', '.join(missing)}"}

    try:
        return fn(**kwargs)
    except Exception as e:
        return {"found": False, "error": f"{fn_name} 실행 중 오류: {e}"}


def _first_call(model: str, messages: list[dict]):
    """1차 호출. tool 호출 JSON이 본문 텍스트로 새어 나오면 복구하고, 복구 불가면 한 번 재시도한다.

    Returns: (assistant message, tool_calls 또는 None). 끝내 실패하면 (None, None).
    """
    for _ in range(2):
        msg = ollama.chat(model=model, messages=messages, tools=TOOLS)["message"]
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            return msg, tool_calls

        content = msg.get("content") or ""
        if not _mentions_tool_call(content):
            return msg, None

        recovered = _recover_text_tool_calls(content)
        if recovered:
            return {"role": "assistant", "content": "", "tool_calls": recovered}, recovered
    return None, None


def _answer(question: str, model: str) -> tuple[dict, list[dict]]:
    """답변을 만든다. (응답 dict, tool 실행 기록 목록)을 돌려준다.

    tool 실행 기록: {"tool_name", "arguments", "result", "called_at", "elapsed_ms"} (실행 순서대로)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *FEW_SHOT_MESSAGES,
        {"role": "user", "content": question},
    ]

    # 1차 호출: 모델이 tool을 쓸지 말지 스스로 판단
    msg, tool_calls = _first_call(model, messages)

    if msg is None:
        return {"answer": FALLBACK_ANSWER, "used_tools": [], "sources": []}, []

    if not tool_calls:
        # 모델이 tool 없이 바로 답한 경우
        return {"answer": extract_answer_text(msg["content"]), "used_tools": [], "sources": []}, []

    messages.append(msg)
    records = []

    # 2. 모델이 만든 종목 인자가 깨졌으면 질문 원문에서 회사명을 찾아 보정한 뒤, tool을 실제로 실행하고
    #    결과를 다시 넘겨줌
    repaired = _repair_company_args(tool_calls, question)
    for idx, call in enumerate(tool_calls):
        fn_name = call["function"]["name"]
        fn_args = _as_args(call["function"]["arguments"])

        called_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        result = _run_tool(fn_name, fn_args)
        elapsed_ms = round((time.perf_counter() - started) * 1000)

        if idx in repaired and isinstance(result, dict):
            result.setdefault("corrected_from", repaired[idx])
            result.setdefault("corrected_via", "question_text")

        records.append(
            {
                "tool_name": fn_name,
                "arguments": fn_args,
                "result": result,
                "called_at": called_at,
                "elapsed_ms": elapsed_ms,
            }
        )
        messages.append(
            {
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    # 3. tool 결과를 반영한 최종 답변 생성
    final = ollama.chat(model=model, messages=messages)
    answer = extract_answer_text(final["message"]["content"])
    if _mentions_tool_call(answer):
        answer = FALLBACK_ANSWER
    response = {
        "answer": answer,
        "used_tools": [r["tool_name"] for r in records],
        "sources": build_sources((r["tool_name"], r["result"]) for r in records),
    }
    return response, records


def ask_question(question: str, model: str = MODEL_NAME, save_report: bool = True) -> dict:
    """질문을 받아 필요한 tool을 호출하고 최종 답변을 생성한다.

    model은 기본값(MODEL_NAME) 외에 다른 모델로도 같은 로직을 검증할 수 있도록
    (scripts/benchmark_models.py) 파라미터로 열어둔 것이다.

    save_report=True이면 질문/답변을 research_report에, tool 실행 이력을 tool_call_log에 저장한다.
    저장이 실패해도 답변은 정상 반환하고 report_id만 None이 된다. 벤치마크/스모크 스크립트는
    DB를 오염시키지 않도록 save_report=False로 호출한다.

    Returns:
        {"answer": str, "used_tools": list[str], "sources": list[dict], "report_id": int | None}
        sources는 tool 결과에서 뽑은 근거 문서(news/disclosure) 목록이다.
    """
    response, records = _answer(question, model)
    response["report_id"] = save_report_row(question, response["answer"], records) if save_report else None
    return response
