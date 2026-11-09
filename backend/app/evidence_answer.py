"""설명형·원인형 답변을 근거 우선 구조로 만든다(순수 함수, DB·Ollama 없음). 조회와 LLM 호출은 app.agent._answer_evidence가 한다.

배경(2026-11-01 모델 비교의 직접 판독): 설명형 답변 23개 중 13개에 근거와 어긋나거나 근거 없이 단정한 문장이 있었고, 모델을 바꿔도 줄지 않았다.
숫자·날짜·제목·출처를 모델이 옮겨 쓰고 원인을 지어내는 구조가 문제다. 그래서
- 시세 문장, 기사 목록(번호·제목·날짜·출처)은 코드가 쓴다.
- 모델은 번호가 붙은 근거만 보고 근거 번호를 단 문장을 쓴다(호출 1회, 도구 없음).
- check_sentences가 근거 번호가 없거나, 인용한 근거에 없는 숫자가 있거나, 인용한 근거와 낱말이 겹치지 않는 문장을 지운다.
- 근거가 0건이면 모델을 부르지 않는다.
"""

import os
import re

from app.answer_guard import _SENTENCE_SPLIT_RE, extract_numbers, is_supported, support_values
from app.display_dates import kst_day

MAX_ITEMS = 5
MAX_ITEMS_EACH = 3
MAX_SENTENCES = 4
MIN_OVERLAP = 0.5
# 근거 번호를 빠뜨린 문장은 낱말이 이만큼 한 근거에 있을 때만 그 근거 번호를 코드가 붙인다
AUTO_CITE_OVERLAP = 0.75
AUTO_CITE_MIN_WORDS = 4
SNIPPET_CHARS = 220

SWITCH_DEFAULT = False  # EXPLAIN_EVIDENCE_FIRST가 없을 때. 2026-11-02 사전 조건 재검증 미달로 끔(evals/reports/evidence_recollect_20261102.md)


def enabled() -> bool:
    """EXPLAIN_EVIDENCE_FIRST: 0/false/off/no면 끈다(기존 도구 호출 흐름), 1/true/on/yes면 켠다. 없거나 그 밖의 값이면 SWITCH_DEFAULT."""
    value = (os.getenv("EXPLAIN_EVIDENCE_FIRST") or "").strip().lower()
    if value in {"0", "false", "off", "no"}:
        return False
    if value in {"1", "true", "on", "yes"}:
        return True
    return SWITCH_DEFAULT


NARRATIVE_RE = re.compile(r"이슈|분위기|배경|리스크|위험|우려|요약|정리|설명|소식|영향|실적|사업|어떤 회사|뉴스|공시|왜|이유|원인|주목")
_DOCUMENT_RE = re.compile(r"사업|위험|리스크|우려|실적|공시\s*내용|의미|배경|영향")
_DISCLOSURE_RE = re.compile(r"공시")
_NOT_NARRATIVE_RE = re.compile(r"구성종목|비중|브리핑|보유종목|관심종목|목표가|얼마야|현재가|종가|거래량")

_UP_RE = re.compile(r"올랐|오른|오르|상승|강세|급등")
_DOWN_RE = re.compile(r"떨어|내렸|내린|하락|약세|급락|빠졌|빠진")
_CAUSAL_RE = re.compile(r"때문|덕분|영향으로|원인은|이유는|탓|으로\s*인해|로\s*인해|이에\s*따라|면서\s*(?:떨어|올랐|하락|상승|급락|급등|내렸)")
_ATTRIBUTION_RE = re.compile(r"기사|보도|따르면|전했|밝혔|알려졌")
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
# "...했다. [3]"처럼 문장 부호 뒤에 쓴 근거 번호를 문장 안으로 옮긴다(그대로 두면 문장과 번호가 갈라진다)
_TRAILING_CITATION_RE = re.compile(r"([.!?])((?:\s*\[\d{1,2}\])+)")
# 근거를 바꿔 말하는 범위를 넘는 추측. 인용 표현이 있어도 지운다
_SPECULATION_RE = re.compile(r"것으로\s*(?:보입니다|보인다|추정|예상됩니다|판단|해석)|것\s*같(?:다|습니다)|수\s*있을\s*것|듯합니다|듯하다|추측")
_WORD_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_JOSA_RE = re.compile(r"(?:으로부터|에서는|에게서|으로서|으로써|이라고|라고|했다고|됐다고|에서|으로|에게|까지|부터|보다|처럼|이며|이고|하며|하고|했습니다|됐습니다|합니다|입니다|습니다|했다|됐다|된다|한다|이다|은|는|이|가|을|를|의|에|도|와|과|로)$")
# 인용 표현은 근거와 겹치는지 셀 때 뺀다
_BOILERPLATE = frozenset({"기사", "기사에", "따르면", "보도", "보도됐", "보도되었", "전했", "밝혔", "알려졌", "관련", "내용", "최근", "또한", "다만", "한편", "대해", "대한", "것으로", "있습니다", "있다",
                          "주가", "회사", "뉴스", "공시", "근거"})

NO_MATERIAL_TEXT = "관련 뉴스·공시를 찾지 못해 원인을 확인할 자료가 없습니다."
NO_SUMMARY_TEXT = "제공된 자료에서 질문에 답할 내용을 확인할 수 없습니다. 위 기사 제목을 참고해 주세요."
CAUSE_NOTE = "※ 위 내용은 기사에 보도된 내용을 옮긴 것이며, 주가 변동의 원인으로 확인된 것은 아닙니다."

SYSTEM_PROMPT = (
    "너는 아래 [근거]만 읽고 한국어로 요약하는 사람이다. 규칙을 반드시 지켜라.\n"
    "1. [근거]에 있는 내용만 바꿔 말한다. 근거에 없는 사실, 원인, 사업 설명, 전망을 쓰지 않는다.\n"
    "2. 문장마다 끝에 근거 번호를 붙인다. 예: 3분기 영업이익이 늘었다고 보도됐습니다 [1].\n"
    "3. 원인을 말할 때는 '기사에 따르면 ~로 보도됐습니다'처럼 쓴다. 단정하지 않는다.\n"
    "4. 숫자, 날짜, 기사 제목, 매체 이름을 쓰지 않는다. 목록·소제목·마크다운을 쓰지 않는다.\n"
    "5. '~습니다'체로 두세 문장만 쓴다. 질문에 답할 내용이 근거에 없으면 '제공된 자료에서 확인할 수 없습니다.'라고만 쓴다.\n"
    "6. 매수·매도를 권하지 않는다."
)


def is_narrative_question(question: str, cause: bool = False) -> bool:
    q = question or ""
    return (cause or bool(NARRATIVE_RE.search(q))) and not _NOT_NARRATIVE_RE.search(q)


def wanted_tools(question: str, cause: bool) -> list[str]:
    """코드가 고르는 조회 도구(LLM의 도구 선택 호출을 쓰지 않는다)."""
    tools = ["stock_tool"]
    if cause:
        tools.append("market_tool")
    tools.append("news_tool")
    if _DISCLOSURE_RE.search(question or ""):
        tools.append("disclosure_tool")
    if _DOCUMENT_RE.search(question or ""):
        tools.append("rag_search_tool")
    return tools


def _item(company: str | None, title: str, content: str, when, source: str | None, kind: str) -> dict:
    return {"company": company, "title": title or "", "content": (content or "")[:SNIPPET_CHARS], "date": kst_day(when), "source": source or "", "kind": kind}


def collect_items(records: list[dict], names: list[str]) -> list[dict]:
    """도구 결과에서 근거 목록을 만든다. 같은 기사는 한 번만, 제목에 종목 이름이 있는 것을 먼저 쓴다. 종목마다 고르게 담는다."""
    picked: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for rec in records:
        r = rec.get("result")
        if not isinstance(r, dict) or not r.get("found"):
            continue
        name = r.get("company_name")
        for it in r.get("news") or []:
            item = _item(name, it.get("title"), it.get("content"), it.get("published_at"), it.get("source"), "news")
            picked.setdefault(name, []).append(item)
        for it in r.get("disclosures") or []:
            item = _item(name, it.get("title"), it.get("content"), it.get("rcept_dt") or it.get("disclosed_at"), "DART", "disclosure")
            picked.setdefault(name, []).append(item)
        for it in r.get("results") or []:
            who = (it.get("company_names") or [name])[0]
            item = _item(who, it.get("title"), it.get("content_snippet"), it.get("published_at") or it.get("disclosed_at"), "", it.get("type") or "document")
            picked.setdefault(who, []).append(item)
    each = MAX_ITEMS if len(names) < 2 else MAX_ITEMS_EACH
    out: list[dict] = []
    for name in names or list(picked):
        ranked = sorted(picked.get(name, []), key=lambda it: (name not in it["title"], not it["content"]))
        kept = 0
        for it in ranked:
            if it["title"] in seen or kept >= each:
                continue
            seen.add(it["title"])
            out.append(it)
            kept += 1
    return out[:MAX_ITEMS * max(1, min(len(names), 2))]


def evidence_block(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        body = f" - {it['content']}" if it["content"] else ""
        lines.append(f"[{i}] ({it['company']}) {it['title']}{body}")
    return "\n".join(lines)


def user_prompt(question: str, items: list[dict]) -> str:
    return f"[근거]\n{evidence_block(items)}\n\n[질문]\n{question}"


def item_lines(items: list[dict]) -> list[str]:
    lines = []
    for i, it in enumerate(items, 1):
        tail = ", ".join(x for x in (it["date"], it["source"]) if x)
        lines.append(f"[{i}] {it['title']}" + (f" ({tail})" if tail else ""))
    return lines


def _words(text: str) -> list[str]:
    out = []
    for w in _WORD_RE.findall(text):
        w = _JOSA_RE.sub("", w)
        if len(w) >= 2 and w not in _BOILERPLATE and not w.isdigit():
            out.append(w)
    return out


def _source_text(item: dict) -> str:
    return f"{item['title']} {item['content']}"


def _auto_citation(sentence: str, items: list[dict], names: list[str]) -> int | None:
    """번호 없는 문장의 낱말 대부분이 한 근거에 있으면 그 근거 번호. 없으면 None."""
    words = [w for w in _words(sentence) if w not in names]
    if len(words) < AUTO_CITE_MIN_WORDS:
        return None
    rates = [sum(w in _source_text(it) for w in words) / len(words) for it in items]
    best = max(rates, default=0)
    return rates.index(best) + 1 if best >= AUTO_CITE_OVERLAP else None


def _sentence_problem(sentence: str, items: list[dict], names: list[str]) -> str | None:
    cited = [int(n) for n in _CITATION_RE.findall(sentence)]
    if not cited or any(not 1 <= n <= len(items) for n in cited):
        return "no_citation"
    docs = [items[n - 1] for n in cited]
    plain = _CITATION_RE.sub(" ", sentence)
    support = support_values(docs)
    for n in extract_numbers(plain):
        if not is_supported(n, support):
            return f"number:{n.raw}"
    if _SPECULATION_RE.search(plain):
        return "speculation"
    if _CAUSAL_RE.search(plain) and not _ATTRIBUTION_RE.search(plain):
        return "causal_without_attribution"
    source = " ".join(_source_text(d) for d in docs)
    words = [w for w in _words(plain) if w not in names]
    if words:
        hit = sum(w in source for w in words)
        if hit / len(words) < MIN_OVERLAP:
            return f"overlap:{hit}/{len(words)}"
    return None


def check_sentences(text: str, items: list[dict], names: list[str]) -> tuple[list[str], list[str]]:
    """(남긴 문장, 지운 이유). "확인할 수 없습니다" 문장은 근거 번호 없이도 남기지 않고 호출측의 고정 문구로 대신한다."""
    kept, dropped = [], []
    cleaned = re.sub(r"\]\s*[.,]\s*,?\s*\[", "] [", (text or "").replace("**", ""))
    moved = _TRAILING_CITATION_RE.sub(lambda m: f"{m.group(2)}{m.group(1)}", cleaned)
    for raw in _SENTENCE_SPLIT_RE.split(moved):
        sentence = re.sub(r"^[\s\-*•]*(?:\d{1,2}[.)]\s*)?", "", raw).strip()
        if not _WORD_RE.search(_CITATION_RE.sub(" ", sentence)):
            continue
        if not _CITATION_RE.search(sentence) and (source := _auto_citation(sentence, items, names)):
            sentence = re.sub(r"([.!?]?)$", rf" [{source}]\1", sentence, count=1)
        problem = _sentence_problem(sentence, items, names)
        if problem:
            dropped.append(problem)
        elif len(kept) < MAX_SENTENCES:
            kept.append(sentence)
    return kept, dropped


def premise_note(question: str, change_pct: float | None) -> str | None:
    """질문의 전제와 실제 등락이 다르면 코드가 밝힌다("왜 올랐어" 인데 하락)."""
    if change_pct is None:
        return None
    q = question or ""
    if _UP_RE.search(q) and not _DOWN_RE.search(q) and change_pct < 0:
        return "질문과 달리 이 기간 주가는 하락했습니다."
    if _DOWN_RE.search(q) and not _UP_RE.search(q) and change_pct > 0:
        return "질문과 달리 이 기간 주가는 상승했습니다."
    return None


def compose(price_lines: list[str], items: list[dict], sentences: list[str], cause: bool) -> str:
    sections = []
    if price_lines:
        sections.append("## 주가 동향\n" + "\n".join(price_lines))
    if not items:
        sections.append(NO_MATERIAL_TEXT)
        return "\n\n".join(sections)
    sections.append("## 관련 뉴스·공시\n" + "\n".join(item_lines(items)))
    body = " ".join(sentences) if sentences else NO_SUMMARY_TEXT
    sections.append("## 근거 요약\n" + body + (f"\n\n{CAUSE_NOTE}" if cause and sentences else ""))
    return "\n\n".join(sections)
