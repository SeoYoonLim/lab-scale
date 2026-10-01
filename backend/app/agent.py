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
from app.period_parser import parse_period
from app.reports import save_report as save_report_row
from app.sources import build_sources
from app.tools.company_resolver import extract_from_text, normalize_name, resolve_company
from app.routing import needs_fx_tool, needs_market_tool
from app.tools.disclosure_tool import disclosure_tool
from app.tools.fx_tool import fx_tool
from app.tools.market_tool import market_tool
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
    "최근에 올라온 공시 목록(예: '최근 공시 뭐 있어?')이 필요한 질문에는 disclosure_tool을 호출하라. "
    "news_tool/disclosure_tool은 '최신순으로 N건 그대로' 가져오는 조회용이다. 반면 공시나 뉴스에 담긴 내용을 "
    "특정 주제로 찾아야 하는 질문, 즉 '공시 내용', '자세히', '구체적으로', '근거'처럼 목록이 아니라 내용을 묻는 "
    "질문에는 disclosure_tool이 아니라 rag_search_tool을 호출하라(예: '삼성전자 반도체 업황 관련 근거 찾아줘', "
    "'~ 관련 공시 내용 자세히 알려줘'). "
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
# 내용은 해당 스크립트 상단 주석 참고).
# 예시에는 실제 종목과 복사할 만한 헤드라인/수치를 넣지 않는다. 실제 종목(SK하이닉스)과 그럴듯한
# 헤드라인("HBM 수주 확대 소식에 매수세 유입")을 넣었을 때, 모델이 실제 데이터가 빈약하면 예시를 실제 뉴스처럼
# 베껴 답변에 넣었다(SK네트웍스 질문에 DB에 없는 "SK네트웍스, HBM 수주 확대 소식에 매수세 유입"을 지어내거나,
# KCC 질문에 "SK하이닉스는 최근 뉴스…"라고 답한 사례).
# 종목명만 가상("가상전자")으로 바꾸고 가상 헤드라인을 남기면 오염이 그대로였다: 모델이 헤드라인을 회사명만
# 바꿔 베꼈고("KCC, 신규 공장 증설 계획 발표"), 예시의 가상 수치·종목명이 답변에 그대로 출력되기도 했다
# (오염 5/135, 종목별 25회 반복 측정). 원인은 이름이 아니라 "예시에 복사 가능한 헤드라인이 있다"는 점이라서,
# 예시의 뉴스 결과를 헤드라인 없는 "수집된 뉴스 없음" 사례로 바꿨다 (오염 0/135, p=0.03).
# 시스템 프롬프트에 "지어내지 마라" 지침 문장을 추가해서 오염을 막는 것도 시도했으나, 이는 multi_tool
# (주가+뉴스 동시 호출) 성공률을 87%→62%까지 떨어뜨려(30회 측정) 제외했다. 오염 방지는 few-shot의
# 뉴스 결과를 found=false로 만드는 것만으로 충분하다.
# 종목명은 실제 상장사이면서 이 프로젝트 DB(company 테이블, 300종목 백필)에는 없는 롯데칠성을 쓴다.
# 애초 후보였던 SK하이닉스는 그사이 300종목 백필에 포함돼 DB에 실제 주가·뉴스가 존재하게 되어(예시의
# found=false가 실제 조회 결과와 모순되므로) 제외했다. 롯데칠성은 company 테이블에 아예 없어서
# resolve_company()가 stock_tool/news_tool 모두에 대해 동일하게 "종목을 찾지 못했습니다" found=false를
# 돌려준다(실제 호출로 확인). 그래서 예시의 두 tool 결과도 그 실제 메시지를 그대로 쓴다 — 가짜 수치나
# 가짜 헤드라인이 전혀 없다. 문구도 실제 테스트 케이스(benchmark_models.py의 CASE_*, 삼성전자 기준)와
# 다르게 구성한다.
FEW_SHOT_MESSAGES = [
    # 예시 1: 여러 정보가 필요한 질문 -> stock_tool + news_tool을 한 응답에서 동시에 호출
    {
        "role": "user",
        "content": "롯데칠성 요즘 흐름이 심상치 않던데, 주가 움직임이랑 관련 소식 같이 정리해줄 수 있어?",
    },
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "stock_tool",
                    "arguments": {"ticker": "롯데칠성", "period_days": 1},
                }
            },
            {
                "function": {
                    "name": "news_tool",
                    "arguments": {"company_name": "롯데칠성", "limit": 5},
                }
            },
        ],
    },
    {
        "role": "tool",
        "content": json.dumps(
            {
                "ticker": "롯데칠성",
                "period_days": 1,
                "found": False,
                "message": "'롯데칠성' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요.",
            },
            ensure_ascii=False,
        ),
    },
    {
        "role": "tool",
        "content": json.dumps(
            {
                "company_name": "롯데칠성",
                "limit": 5,
                "found": False,
                "message": "'롯데칠성' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요.",
            },
            ensure_ascii=False,
        ),
    },
    {
        "role": "assistant",
        "content": (
            "죄송합니다. '롯데칠성'은 현재 등록된 종목 목록에서 찾지 못해 주가와 뉴스 모두 "
            "확인할 수 없었습니다. 정확한 종목명이나 종목코드를 알려주시면 다시 확인해드리겠습니다."
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
                "DB에 저장된 실제 데이터 기준으로, 특정 종목의 '최근 공시 목록'(DART 전자공시)을 최신순으로 N건 그대로 "
                "조회한다. 최근에 어떤 공시가 올라왔는지 목록을 볼 때만 호출한다 (예: 최근 공시 뭐 있어?, 오늘 올라온 공시, "
                "최신 공시 목록). 공시에 담긴 구체적인 내용·조건·금액을 특정 주제로 찾아야 하는 질문에는 호출하지 말고 "
                "rag_search_tool을 호출한다. 단순 주가 수치나 일반 뉴스 질문에도 호출하지 않는다. 아직 공시가 수집되지 "
                "않은 종목이거나 등록되지 않은 종목인 경우 found=false와 함께 그 사유를 담은 message를 반환한다."
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
                "공시와 뉴스의 '내용'을 의미(semantic) 기준으로 검색해, 질문과 관련도가 높은 문서 top-k를 찾는다. "
                "특정 주제(전환사채 발행, 임상시험, 사업 양수도 등)에 대해 공시·뉴스에 담긴 구체적인 내용, 조건, 금액 "
                "같은 근거를 찾을 때 호출한다 (예: '전환사채 발행 공시에 나온 조건 알려줘', '임상시험 결과 공시 내용을 "
                "자세히 알려줘', '~ 관련 근거 찾아줘'). 단순히 최신순 목록이 필요한 질문에는 호출하지 않는다. 임베딩이 아직 "
                "채워지지 않았거나 일치하는 문서가 없으면 found=false를 반환한다."
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

# market_tool은 TOOLS(기본 tool 목록)에 넣지 않는다. 5번째 tool을 LLM에 상시 노출하면 stock_tool+news_tool 동시
# 호출(multi)이 깨져서(60%->36%, n=96) 코드에서 질문 문구로 판단해(app/routing.py) 시장 비교 질문일 때만 그 요청의
# tool 목록과 시스템 프롬프트에 추가한다. 그 외 질문은 4-tool 프롬프트가 이전과 완전히 같다.
MARKET_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "market_tool",
        "description": (
            "DB에 저장된 실제 데이터 기준으로, 특정 종목의 최근 N거래일 등락률을 그 종목이 상장된 시장"
            "(코스피/코스닥) 전체 지수의 같은 기간 등락률과 비교한다. 종목 주가 변동이 종목 개별 요인인지 "
            "시장 전체 흐름 때문인지 가릴 때, 또는 시장 지수 대비 성과를 물을 때 호출한다. 종목 등락률, 시장 "
            "등락률, 그 차이(%p)를 돌려준다. 단순 주가·등락률·거래량 수치만 필요한 질문에는 호출하지 않는다"
            "(그때는 stock_tool). 업종(섹터) 지수와의 비교는 지원하지 않는다. 등록되지 않은 종목이거나 "
            "주가·지수 데이터가 없으면 found=false와 함께 그 사유를 담은 message를 반환한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": (
                        "종목명 또는 종목코드(예: 삼성전자, 005930)."
                        " company 테이블의 name 또는 ticker 컬럼으로 조회한다."
                    ),
                },
                "period_days": {
                    "type": "integer",
                    "description": "비교할 기간(거래일 수, 달력 일수가 아님). 기본값 5. 일주일이면 5, 한 달이면 20.",
                },
            },
            "required": ["ticker"],
        },
    },
}

_MARKET_HINT_ANCHOR = "최근 이슈나 '왜 올랐는지/내렸는지' 같이 뉴스가 필요한 질문에는 news_tool을 호출하라. "
MARKET_HINT = (
    "종목의 움직임이 그 종목만의 요인 때문인지 시장 전체 흐름 때문인지 구분해야 하거나 코스피·코스닥 지수와 "
    "비교하는 질문에는 market_tool도 함께 호출하라. "
)

# fx_tool도 market_tool과 같은 이유로 기본 TOOLS에 넣지 않는다(상시 노출 시 multi 호출이 깨지는 문제,
# app/routing.py의 needs_fx_tool이 환율 질문일 때만 추가한다).
FX_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "fx_tool",
        "description": (
            "DB에 저장된 실제 데이터 기준으로, 최근 N거래일 원/달러(USD/KRW) 환율의 현재가·전일대비 등락률· "
            "기간 추세를 조회한다. 환율 수준이나 최근 추이를 묻거나, 환율 변동이 종목(특히 수출입 비중이 큰 "
            "종목)에 미치는 영향을 분석해야 하는 질문에서 호출한다(예: '요즘 환율 어때?', '환율이 많이 올랐는데 "
            "삼성전자 주가에 영향 있어?'). 특정 수치 조회 없이 환율 관련 개념만 설명하면 되는 질문(예: '환율이 "
            "오르면 왜 수출주가 유리해?')에는 호출하지 않는다. 종목이 아니라서 ticker 인자가 없다. 환율 데이터가 "
            "아직 수집되지 않았으면 found=false와 함께 그 사유를 담은 message를 반환한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period_days": {
                    "type": "integer",
                    "description": "조회할 기간(거래일 수, 달력 일수가 아님). 기본값 5. 일주일이면 5, 한 달이면 20.",
                },
            },
            "required": [],
        },
    },
}

FX_HINT = (
    "환율 수준이나 최근 추이, 환율 변동이 종목에 미치는 영향을 분석해야 하는 질문에는 fx_tool도 함께 호출하라. "
)

AVAILABLE_FUNCTIONS = {
    "stock_tool": stock_tool,
    "news_tool": news_tool,
    "disclosure_tool": disclosure_tool,
    "rag_search_tool": rag_search_tool,
    "market_tool": market_tool,
    "fx_tool": fx_tool,
}


def build_request(question: str) -> tuple[str, list[dict]]:
    """질문에 맞는 (시스템 프롬프트, tool 목록)을 만든다. 어느 게이트도 안 걸리면 기본값 그대로(같은 객체)다.

    market_tool/fx_tool 둘 다 기본 TOOLS에는 없고, 각자의 게이트(needs_market_tool/needs_fx_tool)가 True인
    요청에만 해당 tool과 프롬프트 힌트를 추가한다. 한 질문에서 둘 다 걸리면(예: "환율 때문에 코스피 전체가
    빠졌어?") 둘 다 추가된다."""
    hints, extra_tools = [], []
    if needs_market_tool(question):
        hints.append(MARKET_HINT)
        extra_tools.append(MARKET_TOOL_SPEC)
    if needs_fx_tool(question):
        hints.append(FX_HINT)
        extra_tools.append(FX_TOOL_SPEC)

    if not hints:
        return SYSTEM_PROMPT, TOOLS
    prompt = SYSTEM_PROMPT.replace(_MARKET_HINT_ANCHOR, _MARKET_HINT_ANCHOR + "".join(hints), 1)
    return prompt, [*TOOLS, *extra_tools]


FALLBACK_ANSWER = "죄송합니다. 요청을 처리하는 중 문제가 발생했습니다. 종목명을 포함해 질문을 조금 더 구체적으로 다시 해주세요."

# FR-11(답변 구조화): tool 결과를 종합하는 최종 답변 생성 단계에만 추가하는 안내. SYSTEM_PROMPT/TOOLS는 건드리지
# 않으므로 1차 호출(tool 선택)에는 전혀 영향이 없다 - scripts/benchmark_routing.py가 측정하는 경로
# (build_request + _first_call)는 이 상수를 아예 거치지 않는다.
ANSWER_STRUCTURE_INSTRUCTION = (
    "이제 위 조회 결과를 바탕으로 최종 답변을 작성하라. 질문이 여러 tool 결과를 종합해야 하는 질문이면, "
    "답변을 줄글이나 글머리 기호(-, *) 목록이 아니라 '## 섹션 제목' 형식의 마크다운 소제목으로 된 섹션들로 "
    "나눠서 작성하라(각 섹션 제목 줄은 반드시 '## '로 시작한다. 예: '## 주가 동향\\n삼성전자는 ...'). "
    "쓸 수 있는 섹션은 아래 다섯 개뿐이고, 실제로 호출한 tool의 결과가 뒷받침하는 섹션만 쓴다 - 호출하지 "
    "않은 tool에 해당하는 섹션은 아예 쓰지 말고 데이터 없이 지어내지 마라: "
    "## 주가 동향(stock_tool 결과가 있을 때), "
    "## 원인 분석(뉴스·공시·시장 비교 등 변동 원인과 관련된 결과가 있을 때), "
    "## 뉴스·공시 근거(news_tool/disclosure_tool/rag_search_tool 결과가 있을 때), "
    "## 시장 상황(market_tool 결과가 있을 때), "
    "## 위험요인(조회된 데이터에서 실제로 유의할 만한 신호가 보일 때만, 없으면 생략). "
    "반대로 질문이 단순 수치 하나만 묻는 등 짧은 조회성 질문이면 섹션 제목 없이 자연스러운 한두 문장으로만 "
    "답하라 - 이 경우에는 섹션을 억지로 만들지 마라."
)

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
    "market_tool": "ticker",
    "news_tool": "company_name",
    "disclosure_tool": "company_name",
    "rag_search_tool": "company_name",
}


def _repair_company_args(calls: list[dict], question: str, previous_question: str | None = None) -> dict[int, str]:
    """모델이 만든 종목 인자가 DB 종목으로 해석되지 않으면, 질문 원문에서 회사명을 찾아 대신 넣는다.

    모델이 질문 속 회사명을 JSON 인자로 옮기다 엉뚱한 문자열로 깨뜨리는 경우를 위한 fallback이다.
    같은 tool로 이미 정상 해석된 회사는 후보에서 빼고, 남은 후보와 실패한 호출이 일대일로 맞을 때만
    (질문 등장 순서대로) 채운다. 임의로 고르지 않으므로 애매하면 그대로 두어 not-found 응답이 나간다.
    후속 질문("그럼 최근 뉴스는?")처럼 이번 질문에 회사명이 하나도 없을 때만 직전 질문(previous_question)에서 찾는다.
    또 그런 후속 질문에서 직전 질문에 회사가 정확히 하나면, 모델이 채운 회사가 그 종목이 아닐 때 직전 종목으로 바꾼다:
    모델은 회사명이 없는 질문에 "삼성전자" 같은 유효한 이름을 기본값처럼 채우는데(직전 종목이 카카오/현대차/네이버일 때
    후속 뉴스 조회의 60~80%가 다른 회사로 갔다), 해석이 되는 이름이라 위 보정이 손대지 못하기 때문이다.
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
        resolved = []
        failed = []
        for i, fn_name, key, args in targets:
            raw = args.get(key)
            # 종목 필터 없이 검색하려는 rag 호출(인자 없음/null)은 손대지 않는다
            if fn_name == "rag_search_tool" and not normalize_name(raw):
                continue
            res = resolve_company(db, raw)
            if res.company is not None:
                resolved_names.setdefault(fn_name, set()).add(res.company.name)
                resolved.append((i, key, args, raw, res.company))
            else:
                failed.append((i, fn_name, key, args, raw))
        if not failed and not previous_question:
            return {}

        candidates = extract_from_text(db, question)
        follow_up_subject = None
        if not candidates and previous_question:
            candidates = extract_from_text(db, previous_question)
            if len(candidates) == 1:
                follow_up_subject = candidates[0]
        repaired: dict[int, str] = {}
        for fn_name in {f[1] for f in failed}:
            fails = [f for f in failed if f[1] == fn_name]
            remaining = [c for c in candidates if c.name not in resolved_names.get(fn_name, set())]
            if len(remaining) != len(fails):
                continue
            for (i, _, key, args, raw), company in zip(fails, remaining):
                args[key] = company.name
                repaired[i] = "" if raw is None else str(raw)
        if follow_up_subject is not None:
            for i, key, args, raw, company in resolved:
                if company.id != follow_up_subject.id:
                    args[key] = follow_up_subject.name
                    repaired[i] = "" if raw is None else str(raw)
        return repaired
    finally:
        db.close()


# tool별로 기간(period_days)을 받는 인자 이름. news_tool/disclosure_tool/rag_search_tool은 기간 인자 자체가
# 없어서(README "FR-01 인수조건별 검증" 참고) 여기 없고, 손대지 않는다.
_PERIOD_ARG = {"stock_tool": "period_days", "market_tool": "period_days", "fx_tool": "period_days"}


def _apply_period_override(calls: list[dict], question: str) -> dict[int, object]:
    """질문에서 app.period_parser.parse_period가 기간 표현을 인식했으면, 기간 인자를 받는 tool 호출의
    period_days를 그 값으로 덮어써서 LLM이 추론한(종종 틀리는) 값보다 우선하게 한다.

    인식하지 못하면(parse_period가 None) 아무것도 하지 않는다 - 이전처럼 LLM이 추론한 값을 그대로 쓴다.
    calls를 직접 수정하고, {호출 인덱스: 덮어쓰기 전 원래 값(없었으면 None)}을 돌려준다."""
    parsed = parse_period(question)
    if parsed is None:
        return {}

    overridden: dict[int, object] = {}
    for i, call in enumerate(calls):
        key = _PERIOD_ARG.get(call["function"]["name"])
        if key is None:
            continue
        args = _as_args(call["function"]["arguments"])
        call["function"]["arguments"] = args
        overridden[i] = args.get(key)
        args[key] = parsed.period_days
    return overridden


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


def _first_call(model: str, messages: list[dict], tools: list[dict] | None = None):
    """1차 호출. tool 호출 JSON이 본문 텍스트로 새어 나오면 복구하고, 복구 불가면 한 번 재시도한다.

    tools를 생략하면 기본 TOOLS를 쓴다.
    Returns: (assistant message, tool_calls 또는 None). 끝내 실패하면 (None, None).
    """
    for _ in range(2):
        msg = ollama.chat(model=model, messages=messages, tools=TOOLS if tools is None else tools)["message"]
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


# 후속 질문에 넘기는 직전 답변의 최대 길이. 저장된 답변은 보통 수백 자(dev DB 최대 314자)라서 대부분 그대로 들어가고,
# 뉴스 목록처럼 긴 답변만 잘린다. 대화 맥락은 "무슨 종목/주제였는지"를 잇는 용도라 앞부분이면 충분하다.
MAX_PREVIOUS_ANSWER_CHARS = 800


def _previous_turn(previous: dict | None) -> list[dict]:
    """직전 보고서의 질문/답변을 대화 기록(user, assistant 메시지)으로 만든다. 없으면 빈 리스트.

    직전 답변을 만들 때 쓴 tool 호출/결과는 넣지 않는다(질문과 최종 답변 텍스트만)."""
    if not previous:
        return []
    answer = (previous.get("answer") or "").strip()
    if len(answer) > MAX_PREVIOUS_ANSWER_CHARS:
        answer = answer[: MAX_PREVIOUS_ANSWER_CHARS - 1] + "…"
    return [
        {"role": "user", "content": previous["question"]},
        {"role": "assistant", "content": answer},
    ]


def _answer(question: str, model: str, previous: dict | None = None) -> tuple[dict, list[dict]]:
    """답변을 만든다. (응답 dict, tool 실행 기록 목록)을 돌려준다.

    previous={"question", "answer", ...}가 있으면 후속 질문으로 보고 그 질문/답변을 few-shot 뒤, 이번 질문 앞에
    대화 기록으로 넣는다. 없으면 메시지 구성이 이전과 완전히 같다.
    tool 실행 기록: {"tool_name", "arguments", "result", "called_at", "elapsed_ms"} (실행 순서대로)."""
    system_prompt, tools = build_request(question)
    messages = [
        {"role": "system", "content": system_prompt},
        *FEW_SHOT_MESSAGES,
        *_previous_turn(previous),
        {"role": "user", "content": question},
    ]

    # 1차 호출: 모델이 tool을 쓸지 말지 스스로 판단
    msg, tool_calls = _first_call(model, messages, tools)

    if msg is None:
        return {"answer": FALLBACK_ANSWER, "used_tools": [], "sources": []}, []

    if not tool_calls:
        # 모델이 tool 없이 바로 답한 경우
        return {"answer": extract_answer_text(msg["content"]), "used_tools": [], "sources": []}, []

    messages.append(msg)
    records = []

    # 2. 모델이 만든 종목 인자가 깨졌으면 질문 원문에서 회사명을 찾아 보정한 뒤, tool을 실제로 실행하고
    #    결과를 다시 넘겨줌
    repaired = _repair_company_args(tool_calls, question, previous["question"] if previous else None)
    period_overrides = _apply_period_override(tool_calls, question)
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
        if idx in period_overrides and isinstance(result, dict):
            result.setdefault("period_days_corrected_from", period_overrides[idx])
            result.setdefault("period_days_corrected_via", "period_parser")

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

    # 3. tool 결과를 반영한 최종 답변 생성. 구조화 안내(FR-11)는 이 호출 직전에만 추가한다.
    messages.append({"role": "system", "content": ANSWER_STRUCTURE_INSTRUCTION})
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


def ask_question(
    question: str, model: str = MODEL_NAME, save_report: bool = True, previous: dict | None = None
) -> dict:
    """질문을 받아 필요한 tool을 호출하고 최종 답변을 생성한다.

    previous는 후속 질문일 때 이어받을 직전 보고서({"report_id", "question", "answer"}, get_report()의 결과)다.
    바로 직전 1개만 잇고 그보다 앞선 체인은 따라가지 않는다. 새 보고서의 previous_report_id로 저장된다.

    model은 기본값(MODEL_NAME) 외에 다른 모델로도 같은 로직을 검증할 수 있도록
    (scripts/benchmark_models.py) 파라미터로 열어둔 것이다.

    save_report=True이면 질문/답변을 research_report에, tool 실행 이력을 tool_call_log에 저장한다.
    저장이 실패해도 답변은 정상 반환하고 report_id만 None이 된다. 벤치마크/스모크 스크립트는
    DB를 오염시키지 않도록 save_report=False로 호출한다.

    Returns:
        {"answer": str, "used_tools": list[str], "sources": list[dict], "report_id": int | None,
         "previous_report_id": int | None}
        sources는 tool 결과에서 뽑은 근거 문서(news/disclosure) 목록이다.
    """
    previous_id = previous["report_id"] if previous else None
    response, records = _answer(question, model, previous)
    response["report_id"] = (
        save_report_row(question, response["answer"], records, previous_report_id=previous_id) if save_report else None
    )
    response["previous_report_id"] = previous_id
    return response
