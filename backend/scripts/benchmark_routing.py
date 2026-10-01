"""tool 라우팅 벤치마크: 질문 유형별로 1차 호출에서 어떤 tool이 선택되는지 측정한다.

답변 생성까지 가지 않고 1차 호출(tool 선택)만 보므로 한 번에 1~3초로 끝나 반복 횟수를 늘릴 수 있다.
프롬프트/TOOLS 변형(VARIANTS)을 라운드마다 번갈아 실행(interleave)해서 모델 상태 변화가 특정 변형에만
유리하게 작용하지 않게 한다.

케이스 그룹과 성공 기준:
- rag:        공시 "내용"을 주제로 찾는 질문 -> rag_search_tool 호출 (disclosure_tool만 부르면 실패)
- disc_list:  단순 최신 공시 목록 질문   -> disclosure_tool 호출, rag_search_tool은 부르지 않음
- multi:      주가+뉴스 -> stock_tool과 news_tool을 함께 호출
- stock/news: 각각 그 tool을 호출 (다른 tool을 더 불러도 해당 tool이 있으면 성공)
- no_tool:    개념 질문 -> tool 호출 없음 (llama3.1:8b의 알려진 약점, 회귀 여부만 본다)
- *_ho:       rag_ho/disc_ho/no_tool_ho/market_ho는 프롬프트를 고치는 동안 보지 않은 hold-out. 개발용 질문에만 맞춰
              튜닝됐는지(문구 겹침으로 인한 착시) 확인한다.
- market/market_ho: 시장 지수 비교 질문 -> market_tool 호출. market_tool은 기본 tool 목록에 없고 app/routing.py의 키워드
              게이트가 True일 때만 추가된다. gate_off/gate_always 변형으로 게이트 유무를 비교한다.
- fx/fx_ho: 환율(원/달러) 조회 질문 -> fx_tool 호출(FR-07). market_tool과 같은 이유로 기본 tool 목록에 없고
              app/routing.py의 needs_fx_tool이 True일 때만 추가된다.

측정 방식이 실제 요청 경로(agent.build_request)를 그대로 쓰므로, 요청 구성이 바뀌는 수정을 하면 반드시 이 스크립트로
가드레일 그룹(rag, rag_ho, disc_list, disc_ho, stock, news, multi, no_tool, no_tool_ho)의 회귀를 확인한다.
같은 입력도 실행마다 결과가 달라서(multi는 n=32에서 ±9%p 수준) 같은 입력끼리(gate_off vs current 등) 노이즈를 먼저 본다.

사용:  python scripts/benchmark_routing.py [--reps 8] [--variants current,gate_off] [--groups multi,market] [--out routing.json]
       (--out 경로는 저장소 밖이나 .gitignore 대상 위치를 쓸 것)
"""

import argparse
import copy
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.agent as agent  # noqa: E402

# 변형은 항상 import 시점의 원본에서 만든다. run()이 agent 모듈 전역을 변형으로 덮어쓰기 때문에,
# 현재 전역을 원본으로 착각하면 앞 변형의 수정이 다음 변형에 새어 들어간다.
ORIG_TOOLS = copy.deepcopy(agent.TOOLS)
ORIG_SYSTEM_PROMPT = agent.SYSTEM_PROMPT
ORIG_FEW_SHOT = copy.deepcopy(agent.FEW_SHOT_MESSAGES)

CASES = {
    "rag": [
        "삼성전자 자사주 매입 관련 공시 내용 자세히 알려줘",
        "SK하이닉스 유상증자 관련 공시 내용을 자세히 찾아줘",
        "현대차 배당 정책 관련 공시 내용 자세히 알려줘",
        "카카오 신규 투자 관련 공시에서 구체적으로 뭐라고 했는지 알려줘",
        "LG에너지솔루션 공급계약 공시 내용 자세히 설명해줘",
        "네이버 자기주식 처분 관련 공시 내용이 뭔지 자세히 알려줘",
        "HD현대중공업 수주 관련 공시에 나온 계약 조건 자세히 알려줘",
        "셀트리온 합병 관련 공시 내용 근거 찾아줘",
    ],
    # 프롬프트를 고치는 동안 보지 않은 hold-out. 개발용 질문(rag/disc_list)에 맞춰 튜닝됐는지 확인한다.
    "rag_ho": [
        "포스코홀딩스 신규 공장 투자 관련 공시에 무슨 내용이 있는지 구체적으로 알려줘",
        "한화에어로스페이스 해외 수주 공시에서 계약 상대와 금액이 어떻게 나와 있는지 알려줘",
        "KB금융 주주환원 관련 공시 내용 자세히 알려줘",
        "삼성바이오로직스 대규모 계약 관련 공시가 뭐라고 설명하는지 찾아줘",
        "두산에너빌리티 원전 수주 공시 내용을 자세하게 알려줘",
        "기아 사채 발행 관련 공시에서 조건이 어떻게 되는지 알려줘",
        "SK이노베이션 자회사 관련 결정 공시의 세부 내용 알려줘",
        "크래프톤 지분 취득 관련 공시 내용이 뭐야? 자세히 알려줘",
    ],
    "disc_ho": [
        "한화에어로스페이스 최근 공시 뭐 올라왔어?",
        "KB금융 공시 목록 보여줘",
        "삼성바이오로직스 최신 공시 3건만 알려줘",
        "기아 이번 주에 나온 공시 있어?",
        "포스코홀딩스 최근에 올라온 공시들 알려줘",
        "두산에너빌리티 공시 최신순으로 보여줘",
    ],
    "disc_list": [
        "삼성전자 최근 공시 알려줘.",
        "SK하이닉스 최근 공시 5건 보여줘",
        "현대차 최근에 올라온 공시 뭐 있어?",
        "카카오 오늘 공시 있어?",
        "LG에너지솔루션 최신 공시 목록 알려줘",
        "네이버 요즘 공시 나온 거 있어?",
    ],
    "multi": [
        "삼성전자 오늘 왜 올랐어? 최근 주가랑 관련 뉴스 같이 확인해줘.",
        "현대차 요즘 왜 이렇게 빠져? 주가 흐름이랑 뉴스 같이 봐줘",
        "카카오 오늘 주가 어때? 관련 소식도 같이 알려줘",
        "LG화학 주가 급등한 이유가 뭐야? 주가랑 뉴스 둘 다 확인해줘",
    ],
    "stock": [
        "삼성전자 최근 3일 등락률이랑 거래량 알려줘.",
        "SK하이닉스 오늘 거래량 얼마야?",
    ],
    "news": [
        "삼성전자 관련 최근 뉴스 좀 알려줘.",
        "카카오 관련 최근 소식 알려줘",
    ],
    "no_tool": [
        "주식 투자를 처음 시작할 때 알아야 할 기본 용어를 알려줘.",
        "PER이 뭐야? 쉽게 설명해줘.",
        "ETF랑 펀드 차이가 뭐야?",
    ],
    # 시장 지수 비교(market_tool). market_ho는 tool 설명/프롬프트 문구를 다듬는 동안 보지 않은 다른 표현.
    "market": [
        "삼성전자가 최근 일주일 떨어진 게 시장 전체 때문이야, 아니면 삼성전자만의 문제야?",
        "SK하이닉스 최근 5일 등락률을 코스피랑 비교해줘",
        "카카오 요즘 주가 하락이 시장 전체 흐름 때문인지 궁금해",
        "현대차가 코스피보다 더 올랐어?",
        "LG에너지솔루션 이번 주 주가, 시장 대비 어땠어?",
        "알테오젠 최근 10일 성과를 코스닥 지수와 비교해서 알려줘",
    ],
    "market_ho": [
        "셀트리온 주가 빠진 게 장 전체가 약해서야, 이 종목 이슈야?",
        "에코프로 최근 일주일 코스닥보다 잘 버텼어?",
        "기아가 지수 대비 초과 수익을 냈는지 알려줘",
        "한화에어로스페이스 코스피 흐름이랑 비교하면 어때?",
        "HLB 이번 달 시장 평균보다 부진한 게 개별 종목 요인 때문인지 알고 싶어",
    ],
    # no_tool 프롬프트를 고치는 동안 예시로 쓰지 않은 개념 질문 (개발용 질문에 맞춰 튜닝됐는지 확인)
    "no_tool_ho": [
        "배당수익률이 뭐고 어떻게 계산해?",
        "공매도가 뭔지 초보자도 알기 쉽게 설명해줘.",
        "분산투자를 왜 하는지 알려줘.",
        "시가총액이랑 주가 차이가 뭐야?",
        "손절매는 언제 하는 게 좋은지 일반적인 원칙만 알려줘.",
    ],
    # 환율(원/달러) 조회(fx_tool, FR-07). fx_ho는 키워드를 정할 때 보지 않은 hold-out.
    "fx": [
        "요즘 환율 어때?",
        "원달러 환율 최근 추이 알려줘",
        "환율이 최근 많이 올랐는데 삼성전자 주가에 영향 있어?",
        "달러 환율 오늘 얼마야?",
        "최근 일주일 환율 등락 알려줘",
        "환율이 최근 급등했는데 어느 정도 올랐어?",
    ],
    "fx_ho": [
        "원/달러 환율 지금 수준이 어느 정도야?",
        "최근 한 달 환율 흐름 알려줘",
        "환율 변동이 수출 기업 주가에 어떤 영향을 주는지 알려줘",
        "오늘 원/달러가 얼마나 됐어?",
        "환율 때문에 수입 원자재 비용 부담이 커졌는지 확인해줘",
    ],
}


def judge(group: str, tools: set[str]) -> bool:
    group = {
        "rag_ho": "rag", "disc_ho": "disc_list", "no_tool_ho": "no_tool", "market_ho": "market", "fx_ho": "fx",
    }.get(group, group)
    if group == "market":
        return "market_tool" in tools
    if group == "fx":
        return "fx_tool" in tools
    if group == "rag":
        return "rag_search_tool" in tools
    if group == "disc_list":
        return "disclosure_tool" in tools and "rag_search_tool" not in tools
    if group == "multi":
        return {"stock_tool", "news_tool"} <= tools
    if group == "stock":
        return "stock_tool" in tools
    if group == "news":
        return "news_tool" in tools
    return not tools


def select_tools(question: str) -> set[str]:
    system_prompt, tools = agent.build_request(question)
    messages = [
        {"role": "system", "content": system_prompt},
        *agent.FEW_SHOT_MESSAGES,
        {"role": "user", "content": question},
    ]
    _, calls = agent._first_call(agent.MODEL_NAME, messages, tools)
    return {c["function"]["name"] for c in calls or []}


def _tool(tools, name):
    return next(t["function"] for t in tools if t["function"]["name"] == name)


# 변형: (TOOLS, SYSTEM_PROMPT, FEW_SHOT_MESSAGES)를 돌려주는 함수. 항상 원본의 deepcopy 위에서 고친다.
def variant_current():
    return ORIG_TOOLS, ORIG_SYSTEM_PROMPT, ORIG_FEW_SHOT


# 반영 완료로 제거한 변형: `desc`, `sp`, `sp_desc` (공시 목록 vs 내용 검색 라우팅 수정: SYSTEM_PROMPT 라우팅 문장 +
# disclosure_tool/rag_search_tool 설명 재작성). 이 수정은 커밋 99b5081에서 app/agent.py에 들어갔고 지금은 "current"가 곧 그
# 결과다. 수정 전 프롬프트가 있어야 재현되므로 다시 돌리려면 99b5081의 부모 커밋 agent.py에서 시작해야 한다.
# 아래 fs/fs_pair/nt_* 변형은 기각된 실험이지만 README의 수치를 재현하는 데 그대로 쓸 수 있어 남겨 둔다.


def _not_found(company: str, **base) -> str:
    # company 테이블에 없는 종목에 대해 실제 tool이 돌려주는 응답과 같은 형태
    return json.dumps(
        {**base, "found": False, "message": f"'{company}' 종목을 찾지 못했습니다. company 테이블에 등록된 종목명 또는 종목코드를 지정해주세요."},
        ensure_ascii=False,
    )


RAG_EXAMPLE = [
    {"role": "user", "content": "종근당 임상시험 결과 관련 공시에는 구체적으로 어떤 내용이 담겨 있는지 자세히 알려줘"},
    {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "rag_search_tool", "arguments": {
        "query": "종근당 임상시험 결과 공시 내용", "company_name": "종근당", "limit": 5}}}]},
    {"role": "tool", "content": _not_found("종근당", query="종근당 임상시험 결과 공시 내용", company_name="종근당", limit=5)},
    {"role": "assistant", "content": "죄송합니다. '종근당'은 현재 등록된 종목 목록에서 찾지 못해 공시 내용을 검색할 수 없었습니다. 정확한 종목명이나 종목코드를 알려주시면 다시 확인해드리겠습니다."},
]
LIST_EXAMPLE = [
    {"role": "user", "content": "한샘 최근 올라온 공시 목록 보여줘"},
    {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "disclosure_tool", "arguments": {
        "company_name": "한샘", "limit": 5}}}]},
    {"role": "tool", "content": _not_found("한샘", company_name="한샘", limit=5)},
    {"role": "assistant", "content": "죄송합니다. '한샘'은 현재 등록된 종목 목록에서 찾지 못해 공시를 확인할 수 없었습니다. 정확한 종목명이나 종목코드를 알려주시면 다시 확인해드리겠습니다."},
]


def _with_examples(*examples):
    """개념 질문 예시(마지막)는 그대로 맨 뒤에 두고, 그 앞에 예시를 끼워 넣는다."""
    fs = copy.deepcopy(ORIG_FEW_SHOT)
    i = next(k for k, m in enumerate(fs) if m["role"] == "user" and "공모주" in m["content"])
    return fs[:i] + [m for ex in examples for m in copy.deepcopy(ex)] + fs[i:]


def variant_fs():
    return copy.deepcopy(ORIG_TOOLS), ORIG_SYSTEM_PROMPT, _with_examples(RAG_EXAMPLE)


def variant_fs_pair():
    return copy.deepcopy(ORIG_TOOLS), ORIG_SYSTEM_PROMPT, _with_examples(RAG_EXAMPLE, LIST_EXAMPLE)


NT_SENTENCE_OLD = "일반적인 용어 설명이나 개념 질문에는 절대 tool을 호출하지 말고 바로 답변하라. "
# 예시 문구는 no_tool/no_tool_ho 질문과 겹치지 않게 고른다.
NT_SENTENCE_NEW = (
    "질문이 개념 설명이나 일반 지식이라 종목별 데이터(주가·뉴스·공시) 조회가 필요 없다면 "
    "(예: '해외 주식 세금이 어떻게 돼?', '환율이 오르면 왜 수출주가 유리해?') tool을 호출하지 말고 바로 답변하라. "
    "특정 종목이 언급되지 않은 질문에는 rag_search_tool도 호출하지 마라. "
)
NT_DESC_SUFFIX = " 특정 종목·공시·뉴스와 무관한 일반 지식이나 용어·개념 설명 질문에는 호출하지 않는다."

NT_FEW_SHOT = [
    {"role": "user", "content": "물가가 오르면 채권 가격은 왜 내려가는지 설명해줘."},
    {"role": "assistant", "content": (
        "금리가 오르면 새로 발행되는 채권의 이자가 더 높아져서, 기존 채권의 매력이 떨어지고 가격이 내려갑니다. "
        "물가 상승은 보통 금리 인상 기대로 이어지기 때문에 채권 가격에 부담이 됩니다. "
        "특정 종목 데이터가 필요 없는 일반 지식이라 tool 호출 없이 바로 답변했습니다.")},
]


def variant_nt_sp():
    assert NT_SENTENCE_OLD in ORIG_SYSTEM_PROMPT
    return copy.deepcopy(ORIG_TOOLS), ORIG_SYSTEM_PROMPT.replace(NT_SENTENCE_OLD, NT_SENTENCE_NEW), ORIG_FEW_SHOT


def variant_nt_desc():
    tools = copy.deepcopy(ORIG_TOOLS)
    d = _tool(tools, "rag_search_tool")
    d["description"] += NT_DESC_SUFFIX
    return tools, ORIG_SYSTEM_PROMPT, ORIG_FEW_SHOT


NT_RAG_DESC_PREFIX = (
    "종목명이 명시된 질문에서 그 종목의 공시·뉴스 '내용'을 찾을 때만 호출한다. "
    "종목이 언급되지 않은 일반 지식·용어·개념 질문(예: 세금, 환율, 투자 원칙)에는 절대 호출하지 않는다. "
)
NT_STOCK_DESC_PREFIX = "특정 종목의 주가 수치가 필요할 때만 호출한다. 개념·용어 설명 질문에는 호출하지 않는다. "


def variant_nt_desc2():
    tools = copy.deepcopy(ORIG_TOOLS)
    _tool(tools, "rag_search_tool")["description"] = NT_RAG_DESC_PREFIX + _tool(tools, "rag_search_tool")["description"]
    _tool(tools, "stock_tool")["description"] = NT_STOCK_DESC_PREFIX + _tool(tools, "stock_tool")["description"]
    return tools, ORIG_SYSTEM_PROMPT, ORIG_FEW_SHOT


def variant_nt_desc2_sp():
    tools, _, fs = variant_nt_desc2()
    return tools, variant_nt_sp()[1], fs


def variant_nt_fs():
    # 기존 예시들 뒤, 실제 질문 바로 앞에 개념 질문 예시를 하나 더 둔다.
    return copy.deepcopy(ORIG_TOOLS), ORIG_SYSTEM_PROMPT, ORIG_FEW_SHOT + copy.deepcopy(NT_FEW_SHOT)


def variant_nt_sp_fs():
    tools, sp, fs = variant_nt_sp()
    return tools, sp, fs + copy.deepcopy(NT_FEW_SHOT)


# market_tool 게이트(agent.needs_market_tool) 비교. 게이트 없이 5번째 tool을 항상 보여주면 multi가 깨진다
# (60% -> 36%, n=96). 그 실험용 변형(mt_tool 등)은 커밋 377d8dc의 이 파일에 남아 있다.
GATES = {
    "gate_off": lambda q: False,   # 시장 비교 질문에도 market_tool을 안 보여줌 (도입 전과 같은 4-tool)
    "gate_always": lambda q: True,  # 모든 질문에 market_tool + 프롬프트 문장을 보여줌
}
ORIG_GATE = agent.needs_market_tool


VARIANTS = {"current": variant_current, "gate_off": variant_current, "gate_always": variant_current,
            "fs": variant_fs, "fs_pair": variant_fs_pair,
            "nt_sp": variant_nt_sp, "nt_desc": variant_nt_desc, "nt_fs": variant_nt_fs, "nt_sp_fs": variant_nt_sp_fs,
            "nt_desc2": variant_nt_desc2, "nt_desc2_sp": variant_nt_desc2_sp}


def run(variant_names: list[str], reps: int, groups: list[str]):
    orig = (agent.TOOLS, agent.SYSTEM_PROMPT, agent.FEW_SHOT_MESSAGES)
    assert orig[0] == ORIG_TOOLS  # 시작 시점의 agent 전역이 원본과 같아야 한다
    results = defaultdict(lambda: defaultdict(list))  # variant -> group -> [(question, ok, tools)]
    try:
        for rep in range(reps):
            for group in groups:
                for q in CASES[group]:
                    for name in variant_names:
                        agent.TOOLS, agent.SYSTEM_PROMPT, agent.FEW_SHOT_MESSAGES = copy.deepcopy(VARIANTS[name]())
                        agent.needs_market_tool = GATES.get(name, ORIG_GATE)
                        try:
                            tools = select_tools(q)
                        except Exception as e:  # noqa: BLE001
                            tools = {f"ERROR:{type(e).__name__}"}
                        results[name][group].append((q, judge(group, tools), sorted(tools)))
            print(f"round {rep + 1}/{reps} done", flush=True)
    finally:
        agent.TOOLS, agent.SYSTEM_PROMPT, agent.FEW_SHOT_MESSAGES = orig
        agent.needs_market_tool = ORIG_GATE
    return results


def summarize(results, variant_names, groups):
    print(f"\n{'group':<11}" + "".join(f"{n:>22}" for n in variant_names))
    summary = {}
    for group in groups:
        row = f"{group:<11}"
        for n in variant_names:
            rs = results[n][group]
            ok = sum(1 for _, o, _ in rs if o)
            summary.setdefault(n, {})[group] = [ok, len(rs)]
            row += f"{ok:>10}/{len(rs):<3} ({100 * ok / len(rs):3.0f}%)  "
        print(row)

    # 게이트가 걸린 tool이 자기 그룹 밖의 질문에서 불필요하게 호출된 비율(추가 호출은 위 성공 판정에는 안 잡힌다)
    for label, tool_name, own_prefix in [("mt 오호출", "market_tool", "market"), ("fx 오호출", "fx_tool", "fx")]:
        row = f"{label:<11}"
        for n in variant_names:
            rs = [r for g in groups if not g.startswith(own_prefix) for r in results[n][g]]
            hit = sum(1 for _, _, tools in rs if tool_name in tools)
            summary.setdefault(n, {})[f"{tool_name}_false_calls"] = [hit, len(rs)]
            row += f"{hit:>10}/{len(rs):<3} ({100 * hit / max(len(rs), 1):3.0f}%)  "
        print(row)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=4, help="질문당 반복 횟수")
    ap.add_argument("--variants", default="current,gate_off", help=f"쉼표 구분. 선택: {','.join(VARIANTS)}")
    ap.add_argument("--groups", default=",".join(CASES), help="측정할 그룹(쉼표 구분). 기본은 전부")
    ap.add_argument("--out", default=None, help="원시 결과 JSON 저장 경로")
    a = ap.parse_args()
    names = a.variants.split(",")
    groups = a.groups.split(",")

    started = time.time()
    results = run(names, a.reps, groups)
    summary = summarize(results, names, groups)
    print(f"\n소요 {time.time() - started:.0f}s")
    if a.out:
        raw = {n: {g: [{"q": q, "ok": o, "tools": t} for q, o, t in rs] for g, rs in gs.items()} for n, gs in results.items()}
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "raw": raw}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
