"""tool 실행 결과에서 근거 문서(news/disclosure) 목록(sources)을 만든다.

/api/research 응답을 만들 때와, 저장된 tool_call_log에서 리포트의 sources를 복원할 때 같은 로직을 쓴다.
"""

from collections.abc import Iterable


def extract_sources(fn_name: str, result: dict) -> list[dict]:
    """tool 결과에서 근거로 쓸 수 있는 문서(news/disclosure) 목록을 뽑는다. 주가 tool은 문서가 없어 제외."""
    if not isinstance(result, dict) or not result.get("found"):
        return []

    def source(type_, title, names, url):
        return {
            "tool": fn_name,
            "type": type_,
            "title": title,
            "company_names": names,
            "company_filter": None,
            "url": url,
        }

    if fn_name == "news_tool":
        return [source("news", n["title"], [n["company_name"]], n.get("url")) for n in result.get("news", [])]
    if fn_name == "disclosure_tool":
        return [
            source("disclosure", d["title"], [d["company_name"]], d.get("source_url"))
            for d in result.get("disclosures", [])
        ]
    if fn_name == "event_reaction_tool":
        # 사건 이후 주가 반응: 사건(뉴스/공시) 하나가 근거 문서 하나다. type은 기존 값(news|disclosure)만 쓴다.
        return [
            source(e["event_type"], e["title"], [result.get("company_name")], e.get("url"))
            for e in result.get("events", [])
            if e.get("event_type") in ("news", "disclosure")
        ]
    if fn_name == "rag_search_tool":
        out = []
        for r in result.get("results", []):
            s = source(r["type"], r["title"], r.get("company_names", []), r.get("url") or r.get("source_url"))
            s["company_filter"] = result.get("company_name")
            out.append(s)
        return out
    return []


def unique_tool_names(names: Iterable[str]) -> list[str]:
    """API의 used_tools: 실행된 tool 이름을 처음 호출된 순서로 한 번씩만 담는다.

    두 종목을 함께 묻는 질문은 종목마다 같은 tool을 부른다(2026-11-18 시연 리허설: stock_tool 등이 두 번씩 나와
    화면 칩이 겹치고 React key가 중복됐다). 호출 기록(tool_call_log)은 그대로 두고 응답 목록만 중복을 뺀다."""
    return list(dict.fromkeys(names))


def build_sources(tool_results: Iterable[tuple[str, dict]]) -> list[dict]:
    """(tool 이름, 결과) 목록에서 sources를 만들고, 같은 문서(type + url)는 한 번만 담는다.

    72차: 같은 문서가 다른 종목 결과에도 있으면(복수 종목 원인 답에서 두 종목에 연결된 기사) 한 번만 담되 company_names에 그 종목을
    더한다. 전에는 처음 나온 종목 이름만 남아 다른 종목 단락의 근거가 출처 목록에서 그 종목과 이어지지 않았다."""
    sources, seen = [], {}
    for fn_name, result in tool_results:
        for s in extract_sources(fn_name, result):
            key = (s["type"], s["url"] or s["title"])
            if key not in seen:
                s["company_names"] = list(s["company_names"] or [])  # 결과(rag)의 리스트를 그대로 고치지 않도록 복사
                seen[key] = s
                sources.append(s)
            else:
                names = seen[key]["company_names"]
                names.extend(n for n in s["company_names"] if n and n not in names)
    return sources


# ---------- 72차(2026-11-18): sources가 빈 이유(source_status) ----------
# sources=[]는 (1) 뉴스·공시를 조회하지 않는 답(시세·계산·용어), (2) 조회했지만 기간 안에 그 종목 자료가 없음, (3) 시세 기준 시점 뒤 자료만
# 있어 원인 근거에서 뺌(69차 정책), (4) 긴 기간이라 기사·공시를 원인으로 고르지 않음(72차), (5) 조회 오류를 모두 같은 모양으로 보였다.
# 프런트는 (5)만 오류로, 나머지는 정상 상태로 보여야 한다. 상태는 tool 기록만으로 정해(저장된 리포트도 tool_call_log로 같은 값을 복원한다).
EVIDENCE_TOOLS = ("news_tool", "disclosure_tool", "rag_search_tool", "event_reaction_tool")
SOURCE_STATUS_MESSAGES = {
    "available": None,
    "not_applicable": "이 답은 뉴스·공시를 조회하지 않는 답(시세·계산·안내)이라 출처가 없습니다.",
    "none_found": "분석 기간 안에서 이 종목을 직접 다룬 뉴스·공시를 찾지 못해 출처가 없습니다. 원인을 특정하지 않았습니다.",
    "excluded_after_cutoff": ("시세 기준일 {base} 장 마감({cut}) 뒤 자료 {n}건은 그 시세에 반영되지 않아 분석에 쓰지 않았고, "
                              "기준 시점 전의 유효한 근거가 없어 출처가 없습니다. 원인을 특정하지 않았습니다."),
    "long_period": "기간이 3개월보다 길어 기사·공시를 원인 근거로 고르지 않았습니다(건수만 답에 적었습니다). 출처 목록이 없습니다.",
    "error": "뉴스·공시를 조회하는 중 오류가 나서 출처를 가져오지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
}


def source_status(tool_results: Iterable[tuple[str, dict]], sources: list[dict]) -> dict:
    """sources의 상태. status: available / not_applicable / none_found / excluded_after_cutoff / long_period / error.

    excluded_after_cutoff_count: 기준 시점(시세 기준일 정규장 마감) 뒤라 뺀 자료 수. 코드 원인 경로는 DB에 저장된 그 종목 자료 전체를,
    LLM 경로(answer_cause.filter_records)는 도구가 읽어 온 자료 중 뺀 수를 센다. market_data_as_of·evidence_cutoff는 원인 경로에서만 채운다."""
    used, errors, long_period = False, False, False
    after = unknown = 0
    counted, cut_at, cut = set(), None, None
    for fn_name, result in tool_results:
        if fn_name not in EVIDENCE_TOOLS or not isinstance(result, dict):
            continue
        used = True
        if result.get("error"):
            errors = True
        if result.get("evidence_policy") == "long_period":
            long_period = True
        cc = result.get("cause_cutoff")
        if not isinstance(cc, dict):
            continue
        if cc.get("policy") == "long_period":
            long_period = True
        # 코드 원인 경로(policy 있음)는 같은 종목의 뉴스·공시 기록이 같은 DB 전체 건수를 들고 있어 종목·기준일마다 한 번만 센다.
        # LLM 경로(filter_records, policy 없음)는 기록마다 실제로 뺀 자료 수라 모두 더한다
        key = (result.get("company_name"), cc.get("base_date")) if cc.get("policy") else None
        if key is None or key not in counted:
            if key is not None:
                counted.add(key)
            after += int(cc.get("excluded_after") or 0)
            unknown += int(cc.get("excluded_unknown_time") or 0)
        if cc.get("base_date") and (cut_at is None or cc["base_date"] < cut_at[0]):
            cut_at = (cc["base_date"], cc.get("evidence_cutoff"))
            cut = cc.get("cutoff")
    base = cut_at[0] if cut_at else None
    if sources:
        status = "available"
    elif not used:
        status = "not_applicable"
    elif errors:
        status = "error"
    elif long_period:
        status = "long_period"
    elif after or unknown:
        status = "excluded_after_cutoff"
    else:
        status = "none_found"
    msg = SOURCE_STATUS_MESSAGES[status]
    if status == "excluded_after_cutoff":
        msg = msg.format(base=base or "-", cut=cut or "15:30", n=f"{after + unknown:,}")
    evidence_cutoff = cut_at[1] if cut_at else None
    if base and not evidence_cutoff:
        evidence_cutoff = f"{base}T{cut or '15:30'}:00+09:00"
    return {"status": status, "count": len(sources), "is_error": status == "error", "empty_reason": None if sources else status,
            "message": msg, "excluded_after_cutoff_count": after, "excluded_unknown_time_count": unknown,
            "market_data_as_of": base, "evidence_cutoff": evidence_cutoff}
