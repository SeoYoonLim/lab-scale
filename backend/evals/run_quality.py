"""AI 답변 품질 평가 실행기(2026-11-07). 8개 영역(A 종목 식별 ~ H 장애·폴백)을 결정론적으로 채점하고 기준선과 비교한다.

    cd backend
    python -m evals.run_quality                                   # 결정론 평가(DB·Ollama 불필요) + 보고서 + 기준선 비교
    python -m evals.run_quality --legacy-summary <run_eval summary>  # 기존 offline 평가의 실패 11건이 등록된 알려진 실패와 같은지도 본다
    python -m evals.run_quality --mode live                       # (로컬 전용) 실제 에이전트·Ollama 답을 같은 판정기로 채점
    python -m evals.run_quality --update-baseline                 # 기준선 다시 쓰기(이유를 커밋 메시지·README에 남길 것)

문항(evals/quality_cases.jsonl)의 kind:
- product: 합성 입력으로 실제 제품 코드 경로(종목 해석, 라우팅 판별, 주가 흐름 답 4개 언어, ETF look-through, 벤치마크 비교,
  LLM 장애 폴백, 숫자 검증 계층, 데이터 기준일 점검, 고정 안내 문구)를 실행하고, 결과를 독립 계산한 정답과 비교한다.
- judge: 미리 쓴 답변(정상·오류 주입)을 판정기에 넣어 판정기가 기대한 실패 코드를 정확히 내는지 본다(평가기 자체의 신뢰성).
- live: 실제 ask_question(Ollama) 답. CI에서는 돌지 않는다.

종료 코드: 0 통과 / 1 게이트 실패(새 회귀, 판정기 검증 실패, 기준선 문항 삭제, 데이터셋 버전 불일치, 알려지지 않은 offline 실패).
하드 실패는 기준선에 '알려진 실패'로 분류돼 있어도 보고서에 항상 따로 센다. 방법과 한계: docs/AI_QUALITY_EVALUATION.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from pathlib import Path

from evals import quality_fixtures as F
from evals import quality_judges as J

EVALS_DIR = Path(__file__).resolve().parent
CASES = EVALS_DIR / "quality_cases.jsonl"
DATASET_META = EVALS_DIR / "quality_dataset.json"
BASELINE = EVALS_DIR / "quality_baseline.json"
RESULTS_DIR = EVALS_DIR / "results"
SEED = EVALS_DIR.parent / "scripts" / "ci_seed_companies.json"
LOCALES = ("ko-KR", "en-US", "ja-JP", "zh-CN")
KINDS = ("product", "judge", "live")
SEVERITIES = ("hard", "soft")
FAILURE_CLASSES = ("product_bug", "expectation_error", "intended_reject", "allowed_fallback", "data_missing", "unsupported_policy")


def load_cases(path: Path = CASES) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def dataset_sha256(path: Path = CASES) -> str:
    """줄 끝(CRLF/LF) 차이를 무시한 내용 해시. 문항을 고치면 바뀐다 -> quality_dataset.json의 version과 sha256을 같이 올려야 한다."""
    with open(path, "rb") as f:
        data = f.read().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def load_meta(path: Path = DATASET_META) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- 제품 경로(합성 입력) ----------


@dataclass
class _Co:
    id: int
    name: str
    ticker: str
    market: str | None = None
    asset_type: str = "stock"
    sector: str | None = None


class _SeedDB:
    """company_resolver가 쓰는 db.query(Company).all()만 흉내 낸다. 종목은 CI 시드(공개 종목 목록) 그대로라 CI DB와 같다."""

    def __init__(self, companies: list[_Co]):
        self._companies = companies

    def query(self, _model):
        return self

    def all(self):
        return list(self._companies)


_SEED_CACHE: list[_Co] | None = None


def seed_db() -> _SeedDB:
    global _SEED_CACHE
    if _SEED_CACHE is None:
        with open(SEED, encoding="utf-8") as f:
            rows = json.load(f)
        _SEED_CACHE = [_Co(i + 1, r["name"], r["ticker"], r.get("market"), r.get("asset_type") or "stock", r.get("sector"))
                       for i, r in enumerate(rows)]
    return _SeedDB(_SEED_CACHE)


def run_resolver(case: dict) -> tuple[list[dict], dict]:
    """기본 모드(allow_fuzzy=True)와 엄격 모드(주문, allow_fuzzy=False)의 정책별 기대.
    expect.company: 정답 종목(없으면 null = 어떤 종목도 돌려주면 안 됨). expect.strict: match(정답과 같아야) | reject(종목을 돌려주면 안 됨).
    expect.default: match(기본) | reject(모호·미지원 입력이라 거절·후보 안내가 맞음)."""
    from app.tools.company_resolver import resolve_company

    e = case["expect"]
    want, must_not = e.get("company"), set(e.get("must_not_companies") or [])
    db = seed_db()
    out, detail = [], {}
    for mode, fuzzy, policy in (("default", True, e.get("default", "match")), ("strict", False, e.get("strict", "match"))):
        res = resolve_company(db, case["question"], allow_fuzzy=fuzzy)
        got = res.company.name if res.company else None
        detail[mode] = {"got": got, "candidates": list(res.candidates or [])[:5]}
        if got is not None and (got in must_not or (want is not None and got != want) or (want is None)):
            out.append(J.failure("wrong_company", f"{mode}: '{case['question']}' -> {got} (기대 {want})"))
        elif policy == "match" and want is not None and got is None:
            out.append(J.failure("entity_miss", f"{mode}: '{case['question']}' 못 찾음(후보 {detail[mode]['candidates']})"))
        elif policy == "reject" and got is not None:
            code = "strict_fuzzy_accept" if mode == "strict" else "entity_not_rejected"
            out.append(J.failure(code, f"{mode}: '{case['question']}' -> {got}, 거절·후보 안내여야 함"))
    return out, detail


_GATE_MODULES = ("routing", "price_flow", "outlook", "glossary", "code_answers", "market_ranking", "market_overview")


def _gate_func(name: str):
    import importlib

    mod, _, fn = name.rpartition(".")
    if mod not in _GATE_MODULES:
        raise ValueError(f"허용하지 않는 판별 함수: {name}")
    return getattr(importlib.import_module(f"app.{mod}"), fn)


def run_routing(case: dict) -> tuple[list[dict], dict]:
    """expect.gates: {"모듈.함수": 기대값}. 기대 True인데 False면 missing_route, 기대 False인데 True면 wrong_route(다른·불필요한 기능)."""
    out, detail = [], {}
    for name, want in case["expect"]["gates"].items():
        got = _gate_func(name)(case["question"])
        if isinstance(want, bool):
            got_b = bool(got)
            detail[name] = got_b
            if got_b != want:
                out.append(J.failure("missing_route" if want else "wrong_route", f"{name} = {got_b}, 기대 {want}"))
        else:
            detail[name] = got
            if got != want:
                out.append(J.failure("wrong_route", f"{name} = {got!r}, 기대 {want!r}"))
    return out, detail


def _kst(s: str) -> datetime:
    from app.briefing import KST

    return datetime.fromisoformat(s).replace(tzinfo=KST)


def _visible_rows(rows: list[tuple], now: datetime) -> list[tuple]:
    """평가기 쪽 장중 처리(제품과 따로): 평일 15:30 전이면 오늘 날짜 행은 종가가 아니므로 정답 계산에서 뺀다."""
    today = now.date()
    if now.weekday() < 5 and now.strftime("%H:%M") < "15:30":
        return [r for r in rows if r[0] != today]
    return rows


def _price_flow_product(case: dict, rows: list[tuple], now: datetime) -> dict[str, str]:
    """제품 경로 그대로: 장중 행 제외(briefing.drop_open_session) -> outlook 지표·신호·데이터 점검·판정 -> price_flow 계산·답(ko),
    price_flow_i18n 템플릿(en/ja/zh). outlook.analyze의 DB 조회(코스피 거래일 달력·수집 상태)만 fixture의 missing_days로 대신한다."""
    from app import outlook as o, price_flow as pf, price_flow_i18n as pfi
    from app.briefing import drop_open_session

    rows = drop_open_session(rows, now, lambda r: r[0])
    period = o.PERIODS[o.DEFAULT_PERIOD]
    ind = o.compute_indicators(rows, period, None)
    signals = o.classify_signals(ind, period)
    missing = [date.fromisoformat(d) for d in case["fixture"].get("missing_days", [])]
    data = o.data_check(rows[-1][0], now, missing, None)
    data["base_is_trading_day"] = True
    res = {"indicators": ind, "signals": signals, "label": o.judge(signals, data), "data": data}
    flow = pf.compute(rows, pf.windows(case["question"]))
    co = _Co(0, case["fixture"].get("company", "가나다전자"), case["fixture"].get("ticker", "123450"), "KOSPI")
    out = {}
    for loc in case.get("locales") or ["ko-KR"]:
        out[loc] = pf.answer(co, flow, res, now) if loc == "ko-KR" else (pfi.answer(co, flow, res, now, loc) or "")
    return out


def judge_answer(answer: str, case: dict, truth=None, base_date: date | None = None, lag: int = 0, locale: str = "ko-KR",
                 evidence: list[dict] | None = None, sources: list[dict] | None = None) -> tuple[list[dict], list]:
    """case.checks(기본: 전부)에 있는 판정기를 답 하나에 적용한다."""
    e = case.get("expect", {})
    checks = set(case.get("checks") or ("numbers", "freshness", "safety", "language", "required", "companies", "evidence"))
    names = e.get("allowed_names") or ([case["fixture"]["company"]] if case.get("fixture", {}).get("company") else [])
    out: list[dict] = []
    claims = J.extract_claims(answer)
    if "numbers" in checks and truth is not None:
        out += J.check_claims(claims, truth)
        if e.get("supports") is not None or e.get("strict_numbers"):
            sup = list(truth.values.values()) + list(truth.closes_by_date.values()) + list(e.get("supports") or [])
            bad = J.unexplained_values(answer, [s for s in sup if s is not None], claims)
            if bad:
                out.append(J.failure("unexplained_number", f"근거로 설명되지 않는 숫자 {bad[:5]}"))
    if "freshness" in checks and (base_date is not None or lag):
        out += J.check_freshness(answer, base_date, lag, e.get("require_base_date", True))
    if "safety" in checks:
        out += J.check_safety(answer, e.get("require_disclaimer", False))
    if "language" in checks:
        out += J.check_language(answer, locale, names)
    if "required" in checks and e.get("required"):
        out += J.check_required(claims, answer, e["required"])
    if "companies" in checks and (e.get("companies") or e.get("must_not_companies")):
        out += J.check_companies(answer, e.get("companies") or [], e.get("must_not_companies") or [])
    if "evidence" in checks and evidence is not None:
        out += J.check_evidence(answer, evidence, e.get("companies") or [], e.get("evidence_required", True))
        if sources is not None:
            out += J.check_structured_sources(sources, evidence, e.get("companies") or [])
    return out, claims


def run_price_flow(case: dict) -> tuple[list[dict], dict]:
    now = _kst(case["fixture"]["now"])
    rows_all = F.price_rows(case["fixture"]["prices"])
    rows = _visible_rows(rows_all, now)
    truth = F.price_truth(rows, window=case["fixture"].get("window", 20))
    base = rows[-1][0]
    lag = J.trading_days_behind(base, J.expected_trading_day(now.date(), now.strftime("%H:%M")))
    answers = _price_flow_product(case, rows_all, now)
    out, by_loc = [], {}
    for loc, ans in answers.items():
        fs, claims = judge_answer(ans, case, truth, base, lag, loc)
        out += [{**f, "detail": f"[{loc}] {f['detail']}"} for f in fs]
        by_loc[loc] = claims
    if len(by_loc) > 1:
        out += J.cross_locale_failures(by_loc)
    return out, {"base_date": base.isoformat(), "lag": lag, "answers": {k: v[:4000] for k, v in answers.items()},
                 "claims": {k: len(v) for k, v in by_loc.items()}}


def run_look_through(case: dict) -> tuple[list[dict], dict]:
    from app import look_through as lt
    from app.briefing import KST
    from app.exposure import ConstituentIn, EtfIn, HoldingIn

    fx = case["fixture"]
    price_date = date.fromisoformat(fx["price_date"])
    hs = [HoldingIn(h["ticker"], h["name"], h["asset_type"], h["quantity"], h.get("price"), price_date if h.get("price") else None)
          for h in fx["holdings"]]
    etfs = {t: EtfIn(t, e.get("name", t), date.fromisoformat(e["as_of"]) if e.get("as_of") else None,
                     [ConstituentIn(c["name"], c["ticker"], c["weight_pct"], c["ticker"], c["name"], c.get("asset_type", "stock"))
                      for c in e["constituents"]]) for t, e in fx["etfs"].items()}
    now = datetime.fromisoformat(fx["now"]).replace(tzinfo=KST)
    res = lt.compute(hs, etfs, fx["cash"], now, date.fromisoformat(fx["expected_date"]))
    truth = F.look_through_truth(fx["holdings"], fx["etfs"], fx["cash"])
    out = []
    if abs(res["summary"]["total_asset"] - truth["total_asset"]) > 0.5:
        out.append(J.failure("result_mismatch", f"총자산 {res['summary']['total_asset']}, 정답 {truth['total_asset']}"))
    got = {c["ticker"] or c["name"]: c for c in res["companies"]}
    for t, tv in truth["companies"].items():
        c = got.get(t)
        if c is None:
            out.append(J.failure("result_mismatch", f"{t} 노출 누락"))
            continue
        for k in ("direct_weight_pct", "via_etf_weight_pct", "total_weight_pct"):
            if abs((c[k] or 0) - tv[k]) > 0.011:  # 제품은 소수 둘째 자리 반올림
                out.append(J.failure("result_mismatch", f"{t} {k} {c[k]}, 정답 {tv[k]:.4f}"))
    for t in got:
        if t not in truth["companies"]:
            out.append(J.failure("result_mismatch", f"정답에 없는 기업 노출 {t}"))
    e = case["expect"]
    kinds = {f["kind"] for f in res["flags"]}
    for k in e.get("flags", []):
        if k not in kinds:
            out.append(J.failure("result_mismatch", f"플래그 {k} 없음(있는 것 {sorted(kinds)})"))
    for k in e.get("no_flags", []):
        if k in kinds:
            out.append(J.failure("result_mismatch", f"플래그 {k}가 있으면 안 됨"))
    if "holdings_stale" in e and bool(res["etf_holdings_stale"]) != e["holdings_stale"]:
        out.append(J.failure("stale_presented_as_latest" if e["holdings_stale"] else "false_stale_notice",
                             f"etf_holdings_stale={res['etf_holdings_stale']}, 기대 {e['holdings_stale']}"))
    for t in e.get("not_decomposed", []):
        row = next((r for r in res["etfs"] if r["ticker"] == t), None)
        if row is None or row["status"] != lt.STATUS_NOT_DECOMPOSED:
            out.append(J.failure("result_mismatch", f"{t}는 분해하지 않아야 함(status {row and row['status']})"))
    return out, {"summary": res["summary"], "flags": sorted(kinds), "stale": res["etf_holdings_stale"]}


def run_benchmark(case: dict) -> tuple[list[dict], dict]:
    from app import performance_compare as pc

    fx = case["fixture"]
    points = [{"date": d, "total_asset": v, "estimated": False} for d, v in fx["portfolio"]]
    for i, p in enumerate(points):
        p["daily_return_pct"] = None if i == 0 else (p["total_asset"] / points[i - 1]["total_asset"] - 1) * 100
    idx = [(date.fromisoformat(d), v) for d, v in fx["index"]]
    warnings: list = []
    res = pc._compare(points, points[0]["total_asset"], idx, fx.get("code", "KS11"), date.fromisoformat(fx["expected_date"]),
                      pc.ST_OK, warnings)
    truth = F.benchmark_truth(points, fx["index"])
    out = []
    want_status = case["expect"]["status"]
    if res["status"] != want_status:
        out.append(J.failure("fallback_status_mismatch", f"status {res['status']}, 기대 {want_status}"))
    if "portfolio_return_pct" in truth and res.get("portfolio_return_pct") is not None:
        for k in ("portfolio_return_pct", "benchmark_return_pct", "excess_return_pp"):
            if abs(res[k] - truth[k]) > 0.011:
                out.append(J.failure("result_mismatch", f"{k} {res[k]}, 정답 {truth[k]:.4f}"))
    elif want_status in (pc.B_NO_DATA, pc.B_INSUFFICIENT) and res.get("excess_return_pp") is not None:
        out.append(J.failure("failed_shown_as_success", "비교할 수 없는데 초과 수익률을 냄"))
    return out, {"status": res["status"], **{k: res.get(k) for k in ("portfolio_return_pct", "benchmark_return_pct", "excess_return_pp")},
                 "warnings": [w["kind"] for w in warnings]}


def _records(case: dict) -> list[dict]:
    return case["fixture"].get("records", [])


def _listed_titles(answer: str) -> list[str]:
    """code_summary의 근거 절('- 제목 (날짜)') 제목들."""
    import re

    titles, in_sec = [], False
    for ln in answer.splitlines():
        if ln.startswith("## "):
            in_sec = "근거" in ln
            continue
        if in_sec and ln.startswith("- "):
            titles.append(re.sub(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*$", "", ln[2:]).strip())
    return titles


def _check_listed_titles(answer: str, records: list[dict]) -> list[dict]:
    ev = [it.get("title") for r in records if isinstance(r.get("result"), dict) and r["result"].get("found")
          for k in ("news", "disclosures", "results") for it in r["result"].get(k) or []]
    return [J.failure("fabricated_source", f"근거 밖 제목 {t[:40]}") for t in _listed_titles(answer) if t not in ev]


def run_fallback(case: dict) -> tuple[list[dict], dict]:
    """LLM 장애(timeout·연결 오류·길이 제한)나 도구 호출 JSON을 글로 쓴 경우의 제품 대체 경로(app.agent._llm_fallback / code_summary)."""
    from app import agent
    from app.answer_guard import code_summary

    recs = _records(case)
    e = case["expect"]
    agent._reset_llm_state()
    if case["fixture"].get("llm_content") is not None:  # 모델이 답 대신 도구 호출 JSON을 씀
        detected = agent._mentions_tool_call(case["fixture"]["llm_content"])
        answer = (code_summary(recs) or agent.FALLBACK_ANSWER) if detected else case["fixture"]["llm_content"]
        meta = None
        out = [] if detected else [J.failure("failed_shown_as_success", "도구 호출 JSON을 답으로 내보냄")]
    else:
        response, _ = agent._llm_fallback(agent.LLMUnavailable(case["fixture"]["reason"]), recs)
        answer, meta = response["answer"], agent._meta().get("fallback")
        out = []
    supports = [v for r in recs for v in J.numbers_in(r.get("result"))]
    allowed = [agent.LLM_TIMEOUT_ANSWER, agent.FALLBACK_ANSWER] if not any(
        isinstance(r.get("result"), dict) and r["result"].get("found") for r in recs) else None
    out += J.check_fallback(answer, meta, e.get("fallback"), supports, allowed)
    if allowed is not None and answer not in allowed:
        out.append(J.failure("failed_shown_as_success", f"쓸 도구 결과가 없는데 안내 문구가 아님: {answer[:60]}"))
    out += _check_listed_titles(answer, recs)
    for pat in e.get("must_not_contain", []):
        import re

        if re.search(pat, answer):
            out.append(J.failure("failed_shown_as_success", f"실패한 도구를 정상 결과처럼 씀: /{pat}/"))
    return out, {"answer": answer, "meta_fallback": meta}


def run_guard(case: dict) -> tuple[list[dict], dict]:
    """LLM이 숫자·URL·외국 문자를 잘못 쓴 답을 숫자 검증 계층(app.agent._guard: answer_guard + URL·외국 문자·맥락 검사)에 넣고,
    최종 답에 근거 없는 숫자·근거 밖 링크·다른 언어가 남지 않는지 본다."""
    from app import agent

    recs = _records(case)
    agent._reset_llm_state()
    final = agent._guard(case["fixture"]["llm_answer"], recs, case["question"])
    supports = [v for r in recs for v in J.numbers_in(r.get("result"))] + [v.value for v in J.find_values(case["question"])]
    out = []
    bad = J.unexplained_values(final, supports)
    if bad:
        out.append(J.failure("number_mismatch", f"검증 후에도 근거 없는 숫자 {bad[:5]}"))
    for pat in case["expect"].get("must_not_contain", []):
        import re

        if re.search(pat, final):
            out.append(J.failure("fabricated_source" if "http" in pat else "wrong_language", f"검증 후에도 남음: /{pat}/"))
    out += J.check_language(final, "ko-KR", case["expect"].get("allowed_names"))
    log = agent._meta().get("answer_guard") or {}
    return out, {"final": final[:2000], "replaced": log.get("replaced"), "violations": log.get("violations")}


def run_data_check(case: dict) -> tuple[list[dict], dict]:
    """outlook.data_check(기준일, 지금, 누락일)의 지연 영업일·경고·판단 유보가 평가기가 따로 센 지연과 맞는지."""
    from app import outlook as o

    fx = case["fixture"]
    now = _kst(fx["now"])
    base = date.fromisoformat(fx["base_date"])
    missing = [date.fromisoformat(d) for d in fx.get("missing_days", [])]
    res = o.data_check(base, now, missing, None)
    lag = J.trading_days_behind(base, J.expected_trading_day(now.date(), now.strftime("%H:%M")))
    out = []
    if res["lag_business_days"] != lag:
        out.append(J.failure("result_mismatch", f"lag {res['lag_business_days']}, 평가기 {lag}"))
    if (lag >= 1 or missing) and not res["warnings"]:
        out.append(J.failure("stale_presented_as_latest", f"{lag}영업일 지연·누락 {len(missing)}일인데 경고 없음"))
    if lag >= 1 and res["freshness"] == "최신 시세":
        out.append(J.failure("stale_presented_as_latest", "지연인데 '최신 시세'"))
    if lag == 0 and not missing and res["warnings"]:
        out.append(J.failure("false_stale_notice", f"최신인데 경고 {res['warnings'][:1]}"))
    want_hold = lag > 5 or bool(missing)
    if res["hold"] != want_hold:
        out.append(J.failure("result_mismatch", f"hold {res['hold']}, 기대 {want_hold}"))
    if case["expect"].get("freshness") and res["freshness"] != case["expect"]["freshness"]:
        out.append(J.failure("result_mismatch", f"freshness '{res['freshness']}', 기대 '{case['expect']['freshness']}'"))
    return out, {"lag": lag, "product": {k: res[k] for k in ("lag_business_days", "freshness", "hold", "expected_date")}}


def _product_text(source: str) -> str:
    from app import glossary, i18n, outlook, price_flow
    from app.disclaimer import DISCLAIMER

    name, _, arg = source.partition(":")
    return {
        "outlook.ORDER_GUIDE_ANSWER": lambda: outlook.ORDER_GUIDE_ANSWER,
        "outlook.NO_PROBABILITY": lambda: outlook.NO_PROBABILITY,
        "price_flow.NOT_A_FORECAST": lambda: price_flow.NOT_A_FORECAST,
        "disclaimer.DISCLAIMER": lambda: DISCLAIMER,
        "i18n.disclaimer": lambda: i18n.disclaimer(arg),
        "glossary.concept_answer": lambda: glossary.concept_answer(arg) or "",
    }[name]()


def run_text(case: dict) -> tuple[list[dict], dict]:
    text = _product_text(case["fixture"]["source"])
    fs, _ = judge_answer(text, case, locale=case.get("locale", "ko-KR"))
    return fs, {"text": text[:1500]}


def run_request_safety(case: dict) -> tuple[list[dict], dict]:
    """(35차) 확정 요청 분류(app.request_safety.classify)와 안내 문장: 기대 종류, 금지 표현 없음, 면책, 안 되는 이유를 밝힘."""
    from app import request_safety

    fx = case["fixture"]
    kind = request_safety.classify(case["question"], fx.get("has_company", False))
    out = []
    if kind != case["expect"]["kind"]:
        out.append(J.failure("wrong_route" if kind else "missing_route", f"kind {kind}, 기대 {case['expect']['kind']}"))
    if kind:
        text = request_safety.answer(kind, fx.get("company"))
        fs, _ = judge_answer(text, case, locale="ko-KR")
        out += fs
        return out, {"kind": kind, "text": text[:1500]}
    return out, {"kind": kind}


def run_quote_freshness(case: dict) -> tuple[list[dict], dict]:
    """(35차) 저장 종가 답의 기준일·지연 문장(app.quote_freshness.line): 평가기가 따로 센 지연과 같은지, 언어별로."""
    from app import quote_freshness

    fx = case["fixture"]
    now, base = _kst(fx["now"]), date.fromisoformat(fx["base_date"])
    lag = J.trading_days_behind(base, J.expected_trading_day(now.date(), now.strftime("%H:%M")))
    out, texts = [], {}
    for loc in case.get("locales") or ["ko-KR"]:
        t = quote_freshness.line(base, now, loc)
        texts[loc] = t
        out += [{**f, "detail": f"[{loc}] {f['detail']}"} for f in J.check_freshness(t, base, lag) + J.check_language(t, loc)]
    if quote_freshness.status(base, now)["lag"] != lag:
        out.append(J.failure("result_mismatch", f"제품 지연 {quote_freshness.status(base, now)['lag']}, 평가기 {lag}"))
    return out, {"lag": lag, "texts": texts}


def run_tidy(case: dict) -> tuple[list[dict], dict]:
    """(35차) LLM 답 표기 정리(app.answer_tidy): 값은 그대로, 원시 소수 표기·반복이 남지 않음, 다른 문장은 지우지 않음."""
    from app import answer_tidy

    src = case["fixture"]["text"]
    out_text, removed = answer_tidy.tidy(src)
    out = J.check_language(out_text, "ko-KR", case["expect"].get("allowed_names"))
    before = sorted({v.value for v in J.find_values(src)})
    after = sorted({v.value for v in J.find_values(out_text)})
    if before != after:
        out.append(J.failure("number_mismatch", f"정리 전후 값이 다름 {before} -> {after}"))
    for keep in case["expect"].get("must_keep", []):
        if keep not in out_text:
            out.append(J.failure("missing_required_info", f"지우면 안 되는 문장이 사라짐: {keep[:40]}"))
    if "removed" in case["expect"] and removed != case["expect"]["removed"]:
        out.append(J.failure("result_mismatch", f"지운 반복 {removed}, 기대 {case['expect']['removed']}"))
    return out, {"text": out_text, "removed": removed}


def run_translation_fallback(case: dict) -> tuple[list[dict], dict]:
    """(35차) 번역 검증 실패 시 언어별 폴백(app.quote_i18n.fallback_summary): 그 언어 문자, 도구 결과 숫자·날짜 그대로, 한국어 문장 없음."""
    from app import quote_i18n

    recs = _records(case)
    now = _kst(case["fixture"]["now"])
    out, texts, by_loc = [], {}, {}
    supports = [v for r in recs for v in J.numbers_in(r.get("result"))]
    for loc in case.get("locales") or ["en-US", "ja-JP", "zh-CN"]:
        t = quote_i18n.fallback_summary(recs, loc, now)
        texts[loc] = t
        out += [{**f, "detail": f"[{loc}] {f['detail']}"} for f in J.check_language(t, loc, case["expect"].get("allowed_names"))]
        for v in case["expect"].get("must_contain", []):
            if v not in t:
                out.append(J.failure("missing_required_info", f"[{loc}] 값 {v} 없음"))
        bad = [x for x in J.unexplained_values(t, supports) if not re.match(r"^\d{1,2}$", x)]
        if bad:
            out.append(J.failure("fallback_ungrounded", f"[{loc}] 근거 없는 숫자 {bad[:5]}"))
        by_loc[loc] = sorted(v.value for v in J.find_values(t) if v.unit)
    if len({tuple(v) for v in by_loc.values()}) > 1:
        out.append(J.failure("cross_locale_mismatch", f"언어별 값 다름 {by_loc}"))
    return out, {"texts": {k: v[:1500] for k, v in texts.items()}}


PRODUCT_TARGETS = {"resolver": run_resolver, "routing": run_routing, "price_flow": run_price_flow, "look_through": run_look_through,
                   "benchmark": run_benchmark, "fallback": run_fallback, "guard": run_guard, "data_check": run_data_check,
                   "text": run_text, "request_safety": run_request_safety, "quote_freshness": run_quote_freshness, "tidy": run_tidy,
                   "translation_fallback": run_translation_fallback}


# ---------- 판정기 검증(judge) ----------


def judge_truth(fx: dict):
    """judge 문항의 정답: prices(합성 시세) 또는 values(직접 준 지표 값)."""
    if fx.get("prices"):
        rows = F.price_rows(fx["prices"])
        return F.price_truth(rows, window=fx.get("window", 20)), rows[-1][0]
    if fx.get("values"):
        vals = {(k.split(":")[0], int(k.split(":")[1]) if ":" in k else None): v for k, v in fx["values"].items()}
        return J.Truth(vals), date.fromisoformat(fx["base_date"]) if fx.get("base_date") else None
    return None, date.fromisoformat(fx["base_date"]) if fx.get("base_date") else None


def run_judge(case: dict) -> tuple[list[dict], dict]:
    """판정기가 낸 실패 코드 집합이 expect.codes와 정확히 같아야 통과. 다르면 평가기 결함(judge_miss/judge_false_alarm)."""
    fx = case.get("fixture", {})
    truth, base = judge_truth(fx)
    lag = fx.get("lag", 0)
    if fx.get("fallback") is not None or "fallback" in (case.get("checks") or []):
        got = J.check_fallback(case["answer"], fx.get("meta_fallback"), fx.get("fallback"), fx.get("supports", []),
                               fx.get("allowed_texts"))
    elif "cross_locale" in (case.get("checks") or []):
        got = J.cross_locale_failures({loc: J.extract_claims(a) for loc, a in case["answers"].items()})
    else:
        got, _ = judge_answer(case["answer"], case, truth, base, lag, case.get("locale", "ko-KR"),
                              evidence=fx.get("evidence") if "evidence" in fx else None, sources=fx.get("sources"))
    got_codes = sorted({f["code"] for f in got})
    want = sorted(set(case["expect"].get("codes", [])))
    detail = {"got": got_codes, "want": want, "failures": got}
    if got_codes == want:
        return [], detail
    out = []
    for c in sorted(set(want) - set(got_codes)):
        out.append({"code": "judge_miss", "severity": "hard", "area": case["area"], "detail": f"판정기가 {c}를 못 잡음"})
    for c in sorted(set(got_codes) - set(want)):
        out.append({"code": "judge_false_alarm", "severity": "hard", "area": case["area"], "detail": f"판정기가 {c}를 잘못 냄"})
    return out, detail


# ---------- live(로컬 전용) ----------


def ollama_info(model: str) -> tuple[bool, dict]:
    """모델 이름·digest(버전). OLLAMA_HOST만 읽는다(주소를 바꾸지 않는다)."""
    import os
    import urllib.request

    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            tags = json.load(r)
    except Exception as e:  # noqa: BLE001
        return False, {"host": host, "error": f"{type(e).__name__}: {e}"}
    m = next((t for t in tags.get("models", []) if t.get("name") == model or t.get("model") == model), None)
    if m is None:
        return False, {"host": host, "error": f"모델 {model} 없음"}
    return True, {"host": host, "model": model, "digest": m.get("digest"), "parameter_size": (m.get("details") or {}).get("parameter_size"),
                  "quantization": (m.get("details") or {}).get("quantization_level")}


def _db_truth(db, company_name: str, base: date | None) -> tuple[J.Truth | None, date | None]:
    """live 정답: stock_price를 평가기가 직접 읽어(읽기 전용) 기준일 이하 260행으로 계산한다. 제품의 계산 결과를 쓰지 않는다."""
    from app.models import Company, StockPrice

    co = db.query(Company).filter(Company.name == company_name).first()
    if co is None:
        return None, None
    q = db.query(StockPrice.price_date, StockPrice.close_price, StockPrice.volume).filter(StockPrice.company_id == co.id)
    if base is not None:
        q = q.filter(StockPrice.price_date <= base)
    rows = list(reversed(q.order_by(StockPrice.price_date.desc()).limit(260).all()))
    if len(rows) < 2:
        return None, None
    rows = [(r[0], float(r[1]), r[2]) for r in rows]
    return F.price_truth(rows), rows[-1][0]


def _market_base(db, kind: str) -> date | None:
    """시장 전체 질문의 기준일 정답(평가기가 DB를 직접 읽음): stock = 주식 시세 최신일, index = 두 지수 중 늦은 쪽의 최신일."""
    from sqlalchemy import text

    if kind == "index":
        return db.execute(text("SELECT min(d) FROM (SELECT max(price_date) d FROM market_index GROUP BY index_code) t")).scalar()
    return db.execute(text("SELECT max(p.price_date) FROM stock_price p JOIN company c ON c.id = p.company_id "
                           "WHERE c.asset_type = 'stock'")).scalar()


def _db_evidence(db, company_names: list[str], records: list[dict]) -> list[dict]:
    """live 근거: 그 답이 실제로 받은 도구 결과의 뉴스·공시(제목·날짜·종목). 같은 제목이 DB에 실제로 있는지도 확인해 없으면 뺀다."""
    from app.models import Disclosure, News

    ev = []
    for r in records:
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        if not res.get("found"):
            continue
        # indirect_news(35차 추가): news_tool이 따로 붙이는 업종·기초지수 기사. LLM이 실제로 받은 근거라 인용해도 지어낸 것이 아니다
        # (live lv_15에서 오탐). 그 종목 기사가 아니므로 종목은 비운다(종목 불일치로 보지 않음).
        for k, model in (("news", News), ("disclosures", Disclosure), ("results", News), ("indirect_news", News)):
            for it in res.get(k) or []:
                t = it.get("title") or it.get("report_nm")
                if not t:
                    continue
                exists = db.query(model.id).filter(model.title == t).first() is not None if hasattr(model, "title") else True
                if exists:
                    d = str(it.get("published_at") or it.get("rcept_dt") or it.get("disclosed_at") or "")[:10] or None
                    company = None if k == "indirect_news" else (res.get("company_name") or it.get("company_name"))
                    ev.append({"id": it.get("id"), "title": t, "date": d, "company": company,
                               "text": " ".join(str(it.get(k) or "") for k in ("content", "summary", "description"))})
    return ev


def run_live(cases: list[dict], model: str) -> tuple[list[dict], dict]:
    """실제 app.agent.ask_question(save_report=False: 리포트·도구 기록을 저장하지 않음, user_id=None: 계정 데이터 없음)."""
    import threading

    from app import agent
    from app.briefing import KST
    from app.db.session import SessionLocal

    ok, info = ollama_info(model)
    if not ok:
        return [], {"ran": False, "skipped_reason": info.get("error"), **info}
    captured = threading.local()
    orig = agent._answer

    def wrapped(*a, **k):
        resp, recs = orig(*a, **k)
        captured.records = recs
        return resp, recs

    agent._answer = wrapped
    results = []
    db = SessionLocal()
    try:
        for case in cases:
            for loc in case.get("locales") or ["ko-KR"]:
                captured.records = []
                t0 = time.perf_counter()
                try:
                    resp = agent.ask_question(case["question"], model=model, save_report=False, locale=loc)
                    err = None
                except Exception as e:  # noqa: BLE001
                    resp, err = {"answer": "", "meta": {}}, f"{type(e).__name__}: {e}"
                elapsed = round(time.perf_counter() - t0, 2)
                recs = captured.records or []
                answer = resp.get("answer") or ""
                companies = sorted({r["result"].get("company_name") for r in recs if isinstance(r.get("result"), dict)
                                    and r["result"].get("found") and r["result"].get("company_name")})
                e = case["expect"]
                fails = []
                if err:
                    fails.append(J.failure("fallback_status_mismatch", err))
                wrong = [c for c in companies if c in (e.get("must_not_companies") or [])
                         or (e.get("companies") is not None and c not in e["companies"] and e.get("strict_companies", True))]
                if wrong:
                    fails.append(J.failure("wrong_company", f"조회 종목 {wrong}"))
                truth, base = (None, None)
                if e.get("companies") and len(e["companies"]) == 1:
                    stated = J.stated_base_dates(answer)
                    truth, base = _db_truth(db, e["companies"][0], stated[0] if stated else None)
                elif e.get("market_base"):  # 시장 전체 질문: 정답 기준일은 DB의 최신 거래일(주식 시세 또는 지수)
                    base = _market_base(db, e["market_base"])
                now = datetime.now(KST)
                lag = J.trading_days_behind(base, J.expected_trading_day(now.date(), now.strftime("%H:%M"))) if base else 0
                ev = _db_evidence(db, e.get("companies") or [], recs) if "evidence" in (case.get("checks") or ()) else None
                fs, _ = judge_answer(answer, case, truth, base, lag, loc, evidence=ev)
                fails += fs
                meta = resp.get("meta") or {}
                if meta.get("fallback") and "fallback" not in e.get("allow", []):
                    fails.append(J.failure("fallback_status_mismatch", f"LLM 폴백 발생: {meta.get('fallback')}"))
                results.append({"id": f"{case['id']}@{loc}", "case_id": case["id"], "kind": "live", "area": case["area"],
                                "category": case["category"], "locale": loc, "failures": fails,
                                "detail": {"answer": answer[:3000], "companies": companies, "elapsed_s": elapsed,
                                           "fallback": meta.get("fallback"), "translation_status": meta.get("translation_status"),
                                           "base_date": base.isoformat() if base else None, "llm_calls": None}})
    finally:
        agent._answer = orig
        db.close()
    return results, {"ran": True, **info}


# ---------- 실행·집계 ----------


def run_case(case: dict) -> dict:
    t0 = time.perf_counter()
    try:
        if case["kind"] == "judge":
            fails, detail = run_judge(case)
        else:
            fails, detail = PRODUCT_TARGETS[case["target"]](case)
    except Exception as e:  # noqa: BLE001 - 실행 오류도 실패로 남긴다(통과로 보이면 안 된다)
        fails, detail = [{"code": "runner_error", "severity": "hard", "area": case["area"], "detail": f"{type(e).__name__}: {e}"}], {}
    return {"id": case["id"], "kind": case["kind"], "area": case["area"], "category": case["category"],
            "target": case.get("target"), "severity": case.get("severity", "hard"), "failures": fails, "detail": detail,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}


def passed(r: dict) -> bool:
    return not r["failures"]


def summarize(results: list[dict]) -> dict:
    """영역별·종류별 통과율, 하드 실패 수, 실패 코드별 건수, 판정기 검증(오류 주입 탐지율·정상 답 오탐률)."""
    by_area = {}
    for a in J.AREAS:
        rs = [r for r in results if r["area"] == a]
        if not rs:
            continue
        prod = [r for r in rs if r["kind"] != "judge"]
        jud = [r for r in rs if r["kind"] == "judge"]
        by_area[a] = {"label": J.AREA_LABEL[a], "cases": len(rs), "passed": sum(map(passed, rs)),
                      "pass_rate": round(sum(map(passed, rs)) / len(rs), 4),
                      "product_cases": len(prod), "product_passed": sum(map(passed, prod)),
                      "judge_cases": len(jud), "judge_passed": sum(map(passed, jud)),
                      "hard_failures": sum(1 for r in prod for f in r["failures"] if f["severity"] == "hard")}
    prod = [r for r in results if r["kind"] != "judge"]
    jud = [r for r in results if r["kind"] == "judge"]
    hard = [{"id": r["id"], **f} for r in prod for f in r["failures"] if f["severity"] == "hard"]
    codes = Counter(f["code"] for r in prod for f in r["failures"])
    bad_answers = [r for r in jud if r["detail"].get("want")]
    good_answers = [r for r in jud if not r["detail"].get("want")]
    rates = [v["pass_rate"] for v in by_area.values()]
    return {
        "total": len(results), "passed": sum(map(passed, results)), "failed": sum(not passed(r) for r in results),
        "pass_rate": round(sum(map(passed, results)) / len(results), 4) if results else None,
        "product": {"cases": len(prod), "passed": sum(map(passed, prod)),
                    "pass_rate": round(sum(map(passed, prod)) / len(prod), 4) if prod else None},
        "judge_validation": {"cases": len(jud), "passed": sum(map(passed, jud)),
                             "injected_error_detection_rate": round(sum(map(passed, bad_answers)) / len(bad_answers), 4) if bad_answers else None,
                             "clean_answer_false_alarm_rate": round(sum(not passed(r) for r in good_answers) / len(good_answers), 4) if good_answers else None,
                             "injected": len(bad_answers), "clean": len(good_answers)},
        "areas": by_area,
        "hard_failure_count": len(hard),
        "hard_failures": hard,
        "failure_codes": dict(codes.most_common()),
        # 영역 통과율의 단순 평균. 하드 실패를 덮지 않도록 hard_failure_count와 항상 같이 본다(이 값만으로 결론 내지 않는다).
        "composite_area_mean": round(sum(rates) / len(rates), 4) if rates else None,
    }


def compare_baseline(results: list[dict], baseline: dict | None, case_ids: set[str]) -> dict:
    """기준선 대비: regressions(기준선 통과 -> 실패), new_failures(기준선에 없는 문항의 실패), improvements(실패 -> 통과),
    removed(기준선에 있었는데 데이터셋에서 사라진 문항), known_failures(기준선에도 실패로 분류된 문항, 분류 포함)."""
    base = (baseline or {}).get("cases", {})
    now = {r["id"]: r for r in results if r["kind"] != "live"}
    reg, new_f, imp, known, changed = [], [], [], [], []
    for cid, r in now.items():
        b = base.get(cid)
        codes = sorted({f["code"] for f in r["failures"]})
        if b is None:
            if not passed(r):
                new_f.append({"id": cid, "codes": codes})
            continue
        if b["status"] == "pass" and not passed(r):
            reg.append({"id": cid, "codes": codes})
        elif b["status"] == "fail" and passed(r):
            imp.append({"id": cid, "was": b.get("codes")})
        elif b["status"] == "fail":
            known.append({"id": cid, "codes": codes, "classification": b.get("classification"), "reason": b.get("reason")})
            if codes != sorted(b.get("codes") or []):
                changed.append({"id": cid, "was": b.get("codes"), "now": codes})
    removed = sorted(set(base) - case_ids)
    return {"baseline_date": (baseline or {}).get("date"), "baseline_commit": (baseline or {}).get("git_commit"),
            "regressions": reg, "new_failures": new_f, "improvements": imp, "removed": removed, "known_failures": known,
            "known_failure_codes_changed": changed}


def check_legacy(summary_path: str | None, baseline: dict | None) -> dict | None:
    """기존 run_eval offline summary의 실패(문항, check)가 기준선에 분류해 둔 알려진 실패와 같은지. 새 실패는 게이트 실패."""
    if not summary_path:
        return None
    with open(summary_path, encoding="utf-8") as f:
        s = json.load(f)
    known = {(k["id"], k["check"]): k for k in (baseline or {}).get("legacy_offline", {}).get("known_failures", [])}
    got = {(f["id"], f["check"].split(":")[0]): f for f in s.get("failures", [])}
    wrong = {n: s["metrics"].get(n, {}).get("all") for n in ("entity_default_wrong_rate", "entity_strict_wrong_rate")}
    return {"summary": Path(summary_path).name, "n_items": s.get("n_items"), "failures": len(got),
            "new": [{"id": i, "check": c, "actual": str(got[(i, c)].get("actual"))[:80]} for i, c in sorted(set(got) - set(known))],
            "resolved": [{"id": i, "check": c} for i, c in sorted(set(known) - set(got))],
            "classified": [{**known[k], "actual": str(got[k].get("actual"))[:80]} for k in sorted(set(got) & set(known))],
            "wrong_rates": wrong,
            # 엄격 모드(주문)에서 다른 종목으로 잘못 연결하지 않은 비율. 실패 11건은 전부 거절·후보 안내라 안전한 쪽이다
            "strict_no_wrong_rate": None if wrong["entity_strict_wrong_rate"] is None else round(1 - wrong["entity_strict_wrong_rate"], 4),
            "strict_accuracy": s["metrics"].get("entity_strict_accuracy", {}).get("all")}


def gate(report: dict) -> list[str]:
    problems = []
    if not report["dataset"]["version_ok"]:
        problems.append(f"데이터셋 내용이 quality_dataset.json의 sha256과 다릅니다(문항을 바꿨으면 version과 sha256을 올리세요): "
                        f"{report['dataset']['sha256'][:12]} != {report['dataset']['expected_sha256'][:12]}")
    jv = [r for r in report["results"] if r["kind"] == "judge" and not passed(r)]
    if jv:
        problems.append(f"판정기 검증 실패 {len(jv)}건(평가기가 주입한 오류를 못 잡거나 정상 답을 실패로 봄): {[r['id'] for r in jv][:10]}")
    b = report["baseline"]
    if b["regressions"]:
        problems.append(f"회귀 {len(b['regressions'])}건(기준선 통과 -> 실패): {[r['id'] for r in b['regressions']][:10]}")
    prod_new = [r for r in b["new_failures"] if not r["id"].startswith("jv_")]
    if prod_new:
        problems.append(f"기준선에 없는 문항의 실패 {len(prod_new)}건(분류해 기준선에 올리거나 고치세요): {[r['id'] for r in prod_new][:10]}")
    if b["removed"]:
        problems.append(f"기준선 문항이 데이터셋에서 사라짐: {b['removed'][:10]}")
    lg = report.get("legacy_offline")
    if lg:
        if lg["new"]:
            problems.append(f"기존 offline 평가에 알려지지 않은 실패 {len(lg['new'])}건: {[(x['id'], x['check']) for x in lg['new']][:10]}")
        for n, v in lg["wrong_rates"].items():
            if v not in (0, 0.0):
                problems.append(f"{n} = {v} (0이어야 함)")
    return problems


def _git(*args) -> str | None:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=EVALS_DIR, timeout=10).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def markdown(report: dict) -> str:
    s, b = report["summary"], report["baseline"]
    L = [f"## AI 답변 품질 평가 ({report['mode']})", "",
         f"- 실행 {report['date']} · 커밋 `{(report['git_commit'] or '?')[:10]}`{' (작업 트리 변경 있음)' if report.get('git_dirty') else ''}"
         f" · 데이터셋 `{report['dataset']['version']}` ({s['total']}문항) · 소요 {report['elapsed_s']}초",
         f"- 모델: {report['model'].get('name')}" + (f" (digest {str(report['model'].get('digest'))[:19]})" if report["model"].get("digest") else "")
         + (" · LLM 답은 이 실행에서 채점하지 않음(결정론 평가)" if report["mode"] != "live" else ""),
         f"- 게이트: **{'통과' if not report['gate_problems'] else '실패'}**"]
    L += [f"  - ❌ {p}" for p in report["gate_problems"]]
    jv = s["judge_validation"]
    L += ["", f"전체 {s['passed']}/{s['total']} 통과 ({s['pass_rate']:.1%}) · 제품 경로 {s['product']['passed']}/{s['product']['cases']} · "
          f"판정기 검증 {jv['passed']}/{jv['cases']} (오류 주입 탐지율 {_p(jv['injected_error_detection_rate'])}, 정상 답 오탐률 {_p(jv['clean_answer_false_alarm_rate'])})",
          f"**하드 실패 {s['hard_failure_count']}건** (제품 경로, 알려진 실패 포함) · 영역 통과율 평균 {_p(s['composite_area_mean'])} (하드 실패와 함께 볼 것)",
          "", "| 영역 | 문항 | 통과 | 통과율 | 제품 경로 | 판정기 검증 | 하드 실패 |", "|---|---|---|---|---|---|---|"]
    for a, v in s["areas"].items():
        L.append(f"| {v['label']} | {v['cases']} | {v['passed']} | {v['pass_rate']:.1%} | {v['product_passed']}/{v['product_cases']} | "
                 f"{v['judge_passed']}/{v['judge_cases']} | {v['hard_failures']} |")
    if s["failure_codes"]:
        L += ["", "실패 코드별 건수(제품 경로): " + ", ".join(f"`{k}` {v}" for k, v in s["failure_codes"].items())]
    L += ["", f"### 기준선 대비 (기준선 {b['baseline_date'] or '-'} `{(b['baseline_commit'] or '-')[:10]}`)",
          f"- 회귀 {len(b['regressions'])} · 새 문항 실패 {len(b['new_failures'])} · 개선 {len(b['improvements'])} · 삭제된 문항 {len(b['removed'])}"]
    for r in b["regressions"]:
        L.append(f"  - 회귀 `{r['id']}`: {', '.join(r['codes'])}")
    for r in b["new_failures"]:
        L.append(f"  - 새 실패 `{r['id']}`: {', '.join(r['codes'])}")
    for r in b["improvements"]:
        L.append(f"  - 개선 `{r['id']}` (이전 {', '.join(r['was'] or [])})")
    if b["known_failures"]:
        L += ["", "### 아직 해결하지 못한 실패(기준선에 분류됨)"]
        for k in b["known_failures"]:
            L.append(f"- `{k['id']}` [{k['classification']}] {', '.join(k['codes'])} - {k['reason']}")
    fails = [r for r in report["results"] if not passed(r)]
    if fails:
        L += ["", "### 실패 문항"]
        for r in fails[:40]:
            for f in r["failures"][:3]:
                L.append(f"- `{r['id']}` ({J.AREA_LABEL.get(r['area'], r['area'])}, {f['severity']}) `{f['code']}`: {f['detail'][:160]}")
    lg = report.get("legacy_offline")
    if lg:
        L += ["", f"### 기존 offline 평가(run_eval) 실패 {lg['failures']}건 분류",
              f"- 오매칭률 {lg['wrong_rates']} · 엄격 모드 정확도 {lg['strict_accuracy']} · 엄격 모드 오매칭 없음 {lg['strict_no_wrong_rate']}"
              f" · 알려지지 않은 실패 {len(lg['new'])} · 해소 {len(lg['resolved'])}"]
        for k in lg["classified"]:
            L.append(f"- `{k['id']}` {k['check']} [{k['classification']}] {k['reason']}")
        for k in lg["new"]:
            L.append(f"- ❌ 새 실패 `{k['id']}` {k['check']}: {k['actual']}")
    L += ["", "평가하지 않는 것: " + "; ".join(report["not_evaluated"])]
    return "\n".join(L)


def _p(v) -> str:
    return "-" if v is None else f"{v:.1%}"


NOT_EVALUATED = [
    "CI에서는 실제 LLM(Ollama) 생성 답을 채점하지 않는다(--mode live로 로컬에서만, 비결정적)",
    "LLM-as-a-Judge는 쓰지 않는다(결정론 판정만)",
    "계정 간 데이터 분리는 이 평가가 아니라 통합 테스트(tests/*_devdb.py의 사용자 분리 테스트)가 확인한다",
    "일일 브리핑·포트폴리오 진단 문장은 이 평가의 제품 경로에 없다(각 기능 테스트가 확인)",
    "공휴일 달력이 없어 영업일은 평일 기준이다(제품과 같은 한계)",
]


def build_report(results: list[dict], mode: str, model: dict, elapsed: float, legacy_path: str | None, baseline: dict | None,
                 cases: list[dict], live_info: dict | None = None) -> dict:
    meta = load_meta()
    sha = dataset_sha256()
    report = {
        "date": datetime.now().isoformat(timespec="seconds"), "mode": mode,
        "git_commit": _git("rev-parse", "HEAD"), "git_dirty": bool(_git("status", "--porcelain", "--", "../app", ".")),
        "model": model, "live": live_info,
        "dataset": {"version": meta["version"], "sha256": sha, "expected_sha256": meta["sha256"], "version_ok": sha == meta["sha256"],
                    "cases": len(cases), "by_kind": dict(Counter(c["kind"] for c in cases)), "by_area": dict(Counter(c["area"] for c in cases))},
        "elapsed_s": round(elapsed, 2),
        "summary": summarize(results),
        "baseline": compare_baseline(results, baseline, {c["id"] for c in cases if c["kind"] != "live"}),
        "legacy_offline": check_legacy(legacy_path, baseline),
        "not_evaluated": NOT_EVALUATED,
        "results": results,
    }
    report["gate_problems"] = gate(report) if mode != "live" else []
    return report


def baseline_from(report: dict, previous: dict | None) -> dict:
    """현재 결과로 기준선을 만든다. 이전 기준선의 실패 분류(classification, reason)와 legacy_offline은 그대로 옮긴다.
    새로 실패한 문항은 분류가 비어 있으므로(None) 사람이 채워야 한다(테스트가 비어 있는 분류를 막는다)."""
    prev = (previous or {}).get("cases", {})
    cases = {}
    for r in report["results"]:
        if r["kind"] == "live":
            continue
        entry = {"status": "pass" if passed(r) else "fail"}
        if not passed(r):
            p = prev.get(r["id"], {})
            entry.update(codes=sorted({f["code"] for f in r["failures"]}), classification=p.get("classification"), reason=p.get("reason"))
        cases[r["id"]] = entry
    return {"date": report["date"][:10], "git_commit": report["git_commit"], "dataset_version": report["dataset"]["version"],
            "cases": cases, "legacy_offline": (previous or {}).get("legacy_offline", {})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 답변 품질 평가")
    ap.add_argument("--mode", choices=["deterministic", "live"], default="deterministic")
    ap.add_argument("--legacy-summary", help="run_eval offline summary JSON(기존 59문항의 알려진 실패와 대조)")
    ap.add_argument("--ids", help="문항 id만(쉼표)")
    ap.add_argument("--out", help="보고서 JSON 경로(기본 evals/results/quality_latest.json, Markdown은 같은 이름 .md). 커밋되지 않는 경로다")
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--update-baseline", action="store_true", help="이번 결과로 기준선 파일을 다시 쓴다(분류는 이전 기준선에서 옮김)")
    ap.add_argument("--model", help="live 모델(기본 app.agent.MODEL_NAME)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    cases = load_cases()
    if args.ids:
        keep = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in keep]
    baseline = None
    if Path(args.baseline).exists():
        with open(args.baseline, encoding="utf-8") as f:
            baseline = json.load(f)
    t0 = time.perf_counter()
    live_info = None
    if args.mode == "live":
        from app.agent import MODEL_NAME

        model_name = args.model or MODEL_NAME
        results, live_info = run_live([c for c in cases if c["kind"] == "live"], model_name)
        model = {"name": model_name, "digest": live_info.get("digest")}
        if not live_info.get("ran"):
            print(f"[live] 건너뜀: {live_info.get('skipped_reason')}")
    else:
        results = [run_case(c) for c in cases if c["kind"] != "live"]
        model = {"name": None, "note": "결정론 평가: LLM을 부르지 않는다"}
    report = build_report(results, args.mode, model, time.perf_counter() - t0, args.legacy_summary, baseline, cases, live_info)
    out = Path(args.out) if args.out else RESULTS_DIR / ("quality_live_latest.json" if args.mode == "live" else "quality_latest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    md = markdown(report)
    with open(out.with_suffix(".md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print(md)
    print(f"\n보고서: {out} · {out.with_suffix('.md')}")
    if args.update_baseline and args.mode != "live" and not args.ids:
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(baseline_from(report, baseline), f, ensure_ascii=False, indent=1)
            f.write("\n")
        print(f"기준선 갱신: {args.baseline}")
        return 0
    return 1 if report["gate_problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
