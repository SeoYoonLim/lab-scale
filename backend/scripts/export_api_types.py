"""OpenAPI 스키마에서 프런트엔드용 TypeScript 타입 파일을 만든다(2026-11-08, 37차). 손으로 쓴 타입이 실제 응답과 어긋나지 않게 한다.

    python -m scripts.export_api_types            # docs/frontend-api-types.ts를 다시 쓴다
    python -m scripts.export_api_types --check    # 파일이 최신이 아니면 종료 코드 1(tests/test_api_contract_round37.py도 확인)

규칙: 필수(required) 필드는 `name: T`, 나머지는 `name?: T`. null 허용(anyOf에 null)은 `T | null`. enum·Literal은 문자열 리터럴 합.
dict[str, X]는 Record<string, X>, 형식이 정해지지 않은 dict는 Record<string, unknown>. 정수·실수는 모두 number.
서버를 띄우거나 DB에 연결하지 않는다(app.openapi()만 부른다).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "docs" / "frontend-api-types.ts"
HEADER = """// 자동 생성 파일: backend/scripts/export_api_types.py가 FastAPI OpenAPI 스키마에서 만든다. 손으로 고치지 말 것.
// 다시 만들기: cd backend && python -m scripts.export_api_types
// 오류 응답은 422만 {detail: ValidationErrorItem[]}이고 나머지(400·401·404·409·413·429·500·503)는 ErrorResponse {detail: string}다.
// 날짜는 "YYYY-MM-DD" 문자열, 시각(*_at)은 오프셋이 붙은 ISO 8601 문자열(UTC "+00:00" 또는 KST "+09:00")이다.
"""


def _name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1].replace("-", "_").replace(".", "_")


def ts_type(s: dict) -> str:
    if not s:
        return "unknown"
    if "$ref" in s:
        return _name(s["$ref"])
    if "anyOf" in s or "oneOf" in s:
        parts = [ts_type(x) for x in s.get("anyOf") or s.get("oneOf")]
        return " | ".join(dict.fromkeys(parts))
    if "const" in s:
        return json.dumps(s["const"], ensure_ascii=False)
    if "enum" in s:
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in s["enum"])
    t = s.get("type")
    if t == "null":
        return "null"
    if t in ("integer", "number"):
        return "number"
    if t == "string":
        return "string"
    if t == "boolean":
        return "boolean"
    if t == "array":
        inner = ts_type(s.get("items", {}))
        return f"({inner})[]" if "|" in inner else f"{inner}[]"
    if t == "object" or "properties" in s or "additionalProperties" in s:
        if "properties" in s:
            return "{ " + "; ".join(_prop(k, v, k in s.get("required", [])) for k, v in s["properties"].items()) + " }"
        ap = s.get("additionalProperties")
        return f"Record<string, {ts_type(ap) if isinstance(ap, dict) and ap else 'unknown'}>"
    return "unknown"


def _prop(key: str, schema: dict, required: bool) -> str:
    k = key if key.isidentifier() else json.dumps(key)
    return f"{k}{'' if required else '?'}: {ts_type(schema)}"


def render(spec: dict) -> str:
    lines = [HEADER]
    for name, s in sorted(spec["components"]["schemas"].items()):
        desc = (s.get("description") or "").strip().splitlines()
        if desc:
            lines.append("/** " + desc[0][:160] + " */")
        if s.get("type") == "object" or "properties" in s:
            lines.append(f"export interface {_name(name)} {{")
            for k, v in (s.get("properties") or {}).items():
                lines.append(f"  {_prop(k, v, k in s.get('required', []))};")
            lines.append("}")
        else:
            lines.append(f"export type {_name(name)} = {ts_type(s)};")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build() -> str:
    from app.main import app

    return render(app.openapi())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OpenAPI -> TypeScript 타입")
    ap.add_argument("--check", action="store_true", help="파일이 최신인지 확인만")
    args = ap.parse_args(argv)
    text = build()
    current = OUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT.exists() else ""
    if args.check:
        if current != text:
            print(f"{OUT}가 최신이 아닙니다: python -m scripts.export_api_types")
            return 1
        print("최신")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"썼음: {OUT} ({text.count('export ')}개 타입)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
