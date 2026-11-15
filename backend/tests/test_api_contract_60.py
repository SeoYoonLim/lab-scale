"""60차(2026-11-15): 프런트엔드 연동 계약(docs/FRONTEND_API_HANDOFF_60.md 부록 A)이 실제 OpenAPI와 같은지. DB·네트워크 없음.

공개 GET 엔드포인트마다 응답 필드(점 표기, 배열은 [], 타입 뒤 ?=null 허용, !=필수), 쿼리 범위(기본값·최소·최대·허용값), 선언된 오류 응답을
OpenAPI에서 다시 계산해 문서 부록의 JSON과 비교한다. 응답 필드를 바꾸면 이 테스트가 실패한다: 문서를 먼저 고치고 프런트엔드와 합의한다.
부록 JSON 다시 만들기: python -m tests.test_api_contract_60 (backend/에서).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.api.deps import get_current_user
from app.main import _api_routes, _uses_dependency, app

HANDOFF = Path(__file__).resolve().parents[2] / "docs" / "FRONTEND_API_HANDOFF_60.md"

PUBLIC_GETS = [
    "/api/health",
    "/api/status",
    "/api/market/overview",
    "/api/companies",
    "/api/stocks/{ticker}/price-history",
    "/api/stocks/{ticker}/news",
    "/api/stocks/{ticker}/disclosures",
    "/api/stocks/{ticker}/event-reactions",
    "/api/stocks/{ticker}/realtime-price",
    "/api/discovery/trending",
    "/api/etf/{ticker}/holdings",
    "/api/disclaimer",
]


def _resolve(spec, s):
    comp = spec["components"]["schemas"]
    while "$ref" in s:
        s = comp[s["$ref"].split("/")[-1]]
    return s


def _nullable(spec, s) -> bool:
    s = _resolve(spec, s)
    opts = s.get("anyOf") or s.get("oneOf") or []
    return s.get("type") == "null" or any(_resolve(spec, o).get("type") == "null" for o in opts)


def _non_null(spec, s):
    s = _resolve(spec, s)
    opts = [o for o in (s.get("anyOf") or s.get("oneOf") or []) if _resolve(spec, o).get("type") != "null"]
    return _resolve(spec, opts[0]) if len(opts) == 1 else s


def _type(spec, s) -> str:
    s = _non_null(spec, s)
    if "enum" in s:
        return "enum(" + "|".join(map(str, s["enum"])) + ")"
    if "const" in s:
        return f"const({s['const']})"
    t = s.get("type")
    if t == "array":
        return "array"
    if t == "object" and not s.get("properties"):
        return "object(any)"
    return t or ("anyOf" if s.get("anyOf") else "any")


def fields(spec, s, prefix="", out=None, depth=0):
    """{필드 경로: "타입[?]"(?는 null 허용, !는 필수)}."""
    out = {} if out is None else out
    s = _non_null(spec, s)
    if s.get("type") == "array" and "items" in s:
        return fields(spec, s["items"], prefix + "[]", out, depth)
    req = set(s.get("required", []))
    for k, sub in (s.get("properties") or {}).items():
        path = f"{prefix}.{k}" if prefix else k
        out[path] = _type(spec, sub) + ("?" if _nullable(spec, sub) else "") + ("!" if k in req else "")
        if depth < 4:
            inner = _non_null(spec, sub)
            if inner.get("properties") or (inner.get("type") == "array" and _non_null(spec, inner.get("items", {})).get("properties")):
                fields(spec, sub, path, out, depth + 1)
    return out


def query_params(spec, path) -> dict:
    op = spec["paths"][path]["get"]
    out = {}
    for p in op.get("parameters", []):
        if p["in"] != "query":
            continue
        s = _non_null(spec, p["schema"])
        d = {k: s[k] for k in ("minimum", "maximum", "minLength", "maxLength") if k in s}
        if "enum" in s:
            d["enum"] = s["enum"]
        if "default" in p["schema"]:
            d["default"] = p["schema"]["default"]
        out[p["name"]] = d
    return out


def contract(spec) -> dict:
    out = {}
    for path in PUBLIC_GETS:
        op = spec["paths"][path]["get"]
        sch = op["responses"]["200"]["content"]["application/json"]["schema"]
        out[path] = {"query": query_params(spec, path), "fields": fields(spec, sch),
                     "errors": sorted(k for k in op["responses"] if k != "200")}
    return out


def _doc_contract() -> dict:
    text = HANDOFF.read_text(encoding="utf-8")
    m = re.search(r"<!-- contract60:begin -->\s*```json\n(.*?)\n```\s*<!-- contract60:end -->", text, re.S)
    assert m, "부록 A JSON 블록이 없다"
    return json.loads(m.group(1))


def test_handoff_contract_matches_openapi():
    doc, real = _doc_contract(), contract(app.openapi())
    assert sorted(doc) == sorted(real)
    for path in real:
        assert doc[path] == real[path], path


def test_public_gets_need_no_auth_and_exist():
    found = {r.path: r for r in _api_routes(app.routes) if "GET" in r.methods}
    for path in PUBLIC_GETS:
        assert path in found, path
        assert not _uses_dependency(found[path].dependant, get_current_user), path


def test_price_history_range_and_key_limits():
    c = contract(app.openapi())
    assert c["/api/stocks/{ticker}/price-history"]["query"]["days"] == {"minimum": 1, "maximum": 365, "default": 90}
    assert c["/api/companies"]["query"]["limit"]["maximum"] == 300
    assert c["/api/discovery/trending"]["query"]["limit"]["maximum"] == 50
    f = c["/api/market/overview"]["fields"]
    assert f["indices[].date"].startswith("string") and f["freshness.intraday_excluded"].startswith("boolean")


if __name__ == "__main__":
    print(json.dumps(contract(app.openapi()), ensure_ascii=False, indent=1, sort_keys=True))
