"""4개 언어(ko·en·ja·zh) 실답 검사(48차, 2026-11-13). live 전용(Ollama 필요), CI에서는 돌지 않는다.

질문마다 app.agent.ask_question(save_report=False, locale=...)을 부르고 코드로 다섯 가지를 검사한다(LLM 심사 없음):
- wrong_company: 도구가 기대 밖 종목으로 해석했거나, 기대 종목을 못 찾았거나, 답(한국어 원문)에 질문·도구 결과에 없는 등록 종목명이 나옴
- bad_source_id: 응답 sources의 id/url이 도구 결과 근거(뉴스·공시 id·url·접수번호)에 없음
- number_changed: 번역 답(translated)의 숫자 다중집합이 한국어 원문과 다름
- base_date_missing: 시세 도구 결과가 있는데 답에 그 마지막 시세 날짜가 없음(ISO·점·한국어 날짜 표기 인정, app.answer_basis.states_date)
- holdings_missing: ETF 구성종목 질문(id가 e/E로 시작)인데 답에 구성종목 줄("1. 이름 (코드) 12.34%")이나 특정 종목 비중 문장이 없음
번역 실패로 데이터 요약이 된 답(translation_status=fallback_summary)의 수도 따로 센다.

실행(backend/에서). 개발 DB는 읽기 전용 세션으로 연결하는 것을 권한다(쓰기 시도는 DB가 거부한다):
  DATABASE_URL="<기존 주소>?options=-c%20default_transaction_read_only%3Don" OLLAMA_HOST=http://127.0.0.1:11434 \\
  python -m evals.run_multilingual evals/multilingual_cases.txt --split holdout --out evals/results/multilingual_x.jsonl
입력 줄: split|id|locale|기대 종목(쉼표, 없으면 -)|질문
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import date

from app import agent, answer_basis, answer_i18n
from app.db.session import SessionLocal
from app.models import Company

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_HOLDING_LINE = re.compile(r"^\s*\d+\.\s.+\s-?\d+\.\d{2}%\s*$", re.M)
_HOLDING_WEIGHT = re.compile(r"비중은\s*-?\d+\.\d{2}%|makes up -?\d+\.\d{2}%|比率は-?\d+\.\d{2}%|权重为-?\d+\.\d{2}%"
                             r"|종목은 없습니다|is not among|含まれていません|中，没有")
_PRICE_TOOLS = ("stock_tool",)


def has_holdings(answer: str) -> bool:
    return bool(_HOLDING_LINE.search(answer or "") or _HOLDING_WEIGHT.search(answer or ""))


def numbers(s: str) -> Counter:
    return Counter(x.replace(",", "") for x in _NUM.findall(s or ""))


def _names() -> list[str]:
    db = SessionLocal()
    try:
        return sorted({c.name for c in db.query(Company).all()}, key=len, reverse=True)
    finally:
        db.close()


def mentioned(text: str, names: list[str]) -> list[str]:
    """답에 나온 등록 종목명(겹치면 긴 이름, 영문 이름은 단어 경계)."""
    hay, found = text or "", set()
    taken = [False] * len(hay)
    for n in names:
        if len(n) < 2:
            continue
        for m in re.finditer(re.escape(n), hay):
            if any(taken[m.start():m.end()]):
                continue
            if re.match(r"[A-Za-z0-9]", n[0]) and m.start() > 0 and re.match(r"[A-Za-z0-9]", hay[m.start() - 1]):
                continue
            if re.match(r"[A-Za-z0-9]", n[-1]) and m.end() < len(hay) and re.match(r"[A-Za-z0-9]", hay[m.end()]):
                continue
            for i in range(m.start(), m.end()):
                taken[i] = True
            found.add(n)
    return sorted(found)


def _texts(v, out: set, depth=0):
    """도구 결과 속 이름·제목·본문(답이 그 안의 종목명을 전하는 것은 허용)."""
    if depth > 6:
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, str) and k in ("company_name", "name", "etf_name", "holding_name", "title", "summary", "content", "body"):
                out.add(x)
            else:
                _texts(x, out, depth + 1)
    elif isinstance(v, list):
        for x in v:
            _texts(x, out, depth + 1)


def _evidence_keys(records: list[dict]) -> set[str]:
    keys = set()

    def walk(v, depth=0):
        if depth > 6:
            return
        if isinstance(v, dict):
            for k in ("id", "url", "source_url", "link", "rcept_no", "news_id", "disclosure_id"):
                if v.get(k) not in (None, ""):
                    keys.add(str(v[k]))
            for x in v.values():
                walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)
    for r in records:
        walk(r.get("result"))
    return keys


def _price_dates(records: list[dict]) -> set[str]:
    out = set()
    for r in records:
        res = r.get("result")
        if r.get("tool_name") in _PRICE_TOOLS and isinstance(res, dict) and res.get("found"):
            prices = [p.get("price_date") for p in res.get("prices") or [] if isinstance(p, dict) and p.get("price_date")]
            d = max(prices) if prices else res.get("period_end_date")
            if isinstance(d, str) and _ISO.match(d):
                out.add(d[:10])
    return out


def check_one(cid: str, locale: str, expected: list[str], question: str, resp: dict, records: list[dict], korean: str,
              names: list[str]) -> dict:
    checks: dict = {}
    allowed = set(expected)
    for rec in records:
        _texts(rec.get("result"), allowed)
    _texts((resp.get("meta") or {}).get("etf_holdings"), allowed)  # 구성종목 템플릿의 종목명(도구 기록이 아니라 내부 meta에 있다)
    allowed_text = " ".join(allowed) + " " + question
    extra = [n for n in mentioned(korean, names) if n not in expected and n not in allowed_text]
    resolved = sorted({(rec.get("result") or {}).get("company_name") for rec in records if isinstance(rec.get("result"), dict)} - {None})
    wrong_resolved = [n for n in resolved if expected and n not in expected]
    if extra or wrong_resolved or (expected and not resolved and not any(e in korean for e in expected)):
        checks["wrong_company"] = {"extra_in_answer": extra, "resolved": resolved}
    ev = _evidence_keys(records)

    def src_ok(src):
        vals = {str(src.get(k)) for k in ("id", "url", "link") if src.get(k)}
        return bool(vals & ev) or any(len(e) >= 8 and e in v for v in vals for e in ev)
    bad = [s for s in resp.get("sources") or [] if not src_ok(s)]
    if bad:
        checks["bad_source_id"] = bad[:3]
    if locale != "ko-KR" and (resp.get("meta") or {}).get("translation_status") == "translated":
        a, b = numbers(korean), numbers(resp["answer"])
        if a != b:
            checks["number_changed"] = {"missing": dict(a - b), "added": dict(b - a)}
    dates = _price_dates(records)
    if dates and not any(answer_basis.states_date(resp["answer"], date.fromisoformat(d)) for d in dates):
        checks["base_date_missing"] = sorted(dates)
    if cid.lower().startswith("e") and not has_holdings(resp["answer"]):
        checks["holdings_missing"] = True
    return checks


def run(path: str, split: str, out_path: str) -> Counter:
    names = _names()
    captured: dict = {}
    orig_localize, orig_answer = answer_i18n.localize_answer, agent._answer

    def localize(question, answer, records, locale, fixed=None, code_data=None):
        captured["ko"] = answer
        return orig_localize(question, answer, records, locale, fixed, code_data=code_data)

    def spy(question, model, previous=None):
        resp, recs = orig_answer(question, model, previous)
        captured["records"] = recs
        return resp, recs

    agent.answer_i18n.localize_answer, agent._answer = localize, spy
    rows, counts = [], Counter()
    try:
        with open(out_path, "w", encoding="utf-8") as out:
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                sp, cid, loc, exp, q = line.split("|", 4)
                if split != "all" and sp != split:
                    continue
                captured.clear()
                t0 = time.time()
                try:
                    resp = agent.ask_question(q, save_report=False, locale=None if loc == "ko-KR" else loc)
                except Exception as e:  # noqa: BLE001 - 한 문항의 실패가 전체를 멈추지 않게
                    resp = {"answer": f"EXC {type(e).__name__}: {e}", "used_tools": [], "sources": [], "meta": {}}
                expected = [] if exp == "-" else exp.split(",")
                korean = captured.get("ko", resp["answer"])
                checks = check_one(cid, loc, expected, q, resp, captured.get("records") or [], korean, names)
                row = {"split": sp, "id": cid, "locale": loc, "question": q, "expected": expected, "sec": round(time.time() - t0, 1),
                       "used_tools": resp.get("used_tools"), "translation_status": (resp.get("meta") or {}).get("translation_status"),
                       "checks": checks, "answer": resp["answer"], "korean": korean if loc != "ko-KR" else None}
                rows.append(row)
                counts.update(checks.keys())
                out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                print(f"{cid} [{loc}] {','.join(checks) or 'ok':38} ts={row['translation_status']} tools={row['used_tools']} | {q}", flush=True)
    finally:
        agent.answer_i18n.localize_answer, agent._answer = orig_localize, orig_answer
    counts["fallback_summary"] = sum(1 for r in rows if r["translation_status"] == "fallback_summary")
    counts["n"] = len(rows)
    print("SUMMARY", dict(counts))
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="4개 언어 실답 검사(live)")
    ap.add_argument("cases")
    ap.add_argument("--split", default="all", choices=["dev", "holdout", "all"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    run(a.cases, a.split, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
