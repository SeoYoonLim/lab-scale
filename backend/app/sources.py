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
    if fn_name == "rag_search_tool":
        out = []
        for r in result.get("results", []):
            s = source(r["type"], r["title"], r.get("company_names", []), r.get("url") or r.get("source_url"))
            s["company_filter"] = result.get("company_name")
            out.append(s)
        return out
    return []


def build_sources(tool_results: Iterable[tuple[str, dict]]) -> list[dict]:
    """(tool 이름, 결과) 목록에서 sources를 만들고, 같은 문서(type + url)는 한 번만 담는다."""
    sources, seen = [], set()
    for fn_name, result in tool_results:
        for s in extract_sources(fn_name, result):
            key = (s["type"], s["url"] or s["title"])
            if key not in seen:
                seen.add(key)
                sources.append(s)
    return sources
