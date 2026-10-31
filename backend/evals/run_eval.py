"""AI 리서치 에이전트 평가 실행기.

    cd backend
    python -m evals.run_eval --mode offline|live|all --split dev|holdout|all --repeats N --only 카테고리 --limit N --out 결과파일

offline은 DB만 쓰고(Ollama 불필요), live는 captest_ 평가 계정을 만들어 app.agent.ask_question을 실제로 호출한 뒤
끝에 그 계정과 리포트를 지운다. 채점 규칙은 evals/metrics.py, 방법과 한계는 evals/README.md.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, time as dtime
from pathlib import Path

from evals import metrics as M

EVALS_DIR = Path(__file__).resolve().parent
DATASET = EVALS_DIR / "dataset.jsonl"
RESULTS_DIR = EVALS_DIR / "results"
LIVE_TIMEOUT_S = 120
# Ollama가 멈추면 120초 timeout이 줄줄이 이어진다. 연속 이만큼 timeout이면 live 실행을 중단하고 summary에 남긴다.
ABORT_AFTER_TIMEOUTS = 3
SOURCE_TOOLS = {"news_tool", "disclosure_tool", "rag_search_tool", "event_reaction_tool"}
# holdout_unseen: 가장 최근 수정 라운드 전에 추가한 batch(UNSEEN_BATCH)의 holdout. 그 라운드의 수정 중에는 한 번도 돌리지 않는다.
# holdout_seen: 그 밖의 holdout(이전 라운드에서 결과가 공개된 것). 라운드마다 UNSEEN_BATCH를 올린다
# (batch 2: 2026-10-27 A-F 라운드, batch 3: 2026-10-27 안정성·숫자 검증 라운드, batch 4: 2026-10-28 3차 라운드,
#  batch 6: 2026-10-31 3년치 백필 뒤 기간 질문 라운드. batch 5는 ETF 구성종목 기능 문항이라 수정 라운드가 아니었다).
UNSEEN_BATCH = 6
# 브리핑 문항(must_briefing)용: 평가 계정에 모의 보유종목 1개(종목, 수량)와 관심종목 1개를 만든다. 계정을 지울 때 같이 지워진다.
EVAL_HOLDING = ("삼성전자", 5)
EVAL_WATCH = "SK하이닉스"
NUMBER_CATEGORIES = ("grounding", "attachment", "period_history")  # 숫자 일치(number_match/full_support)를 채점하는 카테고리
SPLITS = ("dev", "holdout", "holdout_seen", "holdout_unseen", "all")


def in_split(rec: dict, split: str) -> bool:
    if split == "all":
        return True
    if split == "holdout_seen":
        return rec["split"] == "holdout" and rec.get("batch", 1) != UNSEEN_BATCH
    if split == "holdout_unseen":
        return rec["split"] == "holdout" and rec.get("batch", 1) == UNSEEN_BATCH
    return rec["split"] == split


def load_dataset(path: Path = DATASET) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _fail(rec: dict, check: str, expected, actual) -> None:
    rec["failures"].append({"check": check, "expected": expected, "actual": actual})


def _record(item: dict, run: int = 0) -> dict:
    return {k: item[k] for k in ("id", "split", "category", "mode", "question")} | {
        "batch": item.get("batch", 1), "run": run, "flags": {}, "failures": [], "detail": {}}


# ---------- offline ----------


def eval_entity(db, item: dict) -> dict:
    from app.tools.company_resolver import resolve_company

    rec = _record(item)
    expected = (item["expect"].get("companies") or [None])[0]
    for mode, fuzzy in (("default", True), ("strict", False)):
        res = resolve_company(db, item["question"], allow_fuzzy=fuzzy)
        got = res.company.name if res.company else None
        outcome = M.classify_entity(expected, got, res.candidates)
        rec["detail"][mode] = {"outcome": outcome, "got": got, "candidates": res.candidates}
        rec["flags"][f"entity_{mode}"] = outcome
        if outcome != "correct":
            _fail(rec, f"entity_{mode}", expected or "(없음: 거부해야 함)",
                  f"{outcome}: {got or res.candidates or res.message}")
    return rec


def eval_period(item: dict) -> dict:
    from app.period_parser import KST, parse_period

    rec = _record(item)
    exp = item["expect"]
    now = datetime.combine(datetime.fromisoformat(exp["ref_date"]).date(), dtime(12, 0), tzinfo=KST)
    p = parse_period(item["question"], now=now)
    got = None if p is None else {"period_days": p.period_days, "start": p.start_date.isoformat(),
                                  "end": p.end_date.isoformat(), "matched": p.matched_text}
    ok = M.period_matches(p and p.period_days, exp["period_days"], cap=10**6)
    if ok and p is not None:
        ok = exp.get("start") in (None, got["start"]) and exp.get("end") in (None, got["end"])
    rec["flags"]["period_ok"] = ok
    rec["detail"]["parsed"] = got
    if not ok:
        _fail(rec, "period_parse", {k: exp.get(k) for k in ("period_days", "start", "end")}, got)
    return rec


def eval_gate(item: dict) -> dict:
    from app import routing

    rec = _record(item)
    wrong = {}
    for name, expected in item["expect"]["gates"].items():
        got = getattr(routing, name)(item["question"])
        rec["detail"][name] = got
        if got != expected:
            wrong[name] = (expected, got)
    rec["flags"]["gate_ok"] = not wrong
    for name, (e, g) in wrong.items():
        _fail(rec, f"gate:{name}", e, g)
    return rec


def run_offline(items: list[dict]) -> list[dict]:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        out = []
        for it in items:
            if it["category"] == "entity":
                out.append(eval_entity(db, it))
            elif it["category"] == "period":
                out.append(eval_period(it))
            elif it["category"] == "routing_gate":
                out.append(eval_gate(it))
        return out
    finally:
        db.close()


# ---------- live ----------


def ollama_status(model: str) -> tuple[bool, str]:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = "http://" + host
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=5) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except Exception as e:  # noqa: BLE001 - 연결 실패는 사유만 보고한다
        return False, f"Ollama 연결 실패({host}): {type(e).__name__}"
    if model not in names and f"{model}:latest" not in names:
        return False, f"Ollama에 모델 {model}이 없음 (있는 모델: {names})"
    return True, "ok"


def _expected_period(expect_value, question: str):
    if expect_value == "parser":
        from app.period_parser import parse_period

        p = parse_period(question)
        return p.period_days if p else None
    return expect_value


def score_live(item: dict, rec: dict, response: dict | None, calls: list[dict], data_start=None, today=None,
               data_end=None, briefing: dict | None = None) -> None:
    """ask_question 응답과 tool_call_log 기록(calls: [{tool_name, arguments, result}])으로 채점한다.

    data_start는 질문 종목(또는 전체)의 보유 시세 시작일로, 기간 라벨 일관성 판정에만 쓴다.
    briefing은 평가기가 같은 계정으로 따로 계산한 브리핑(app.briefing_db.get_briefing)으로, must_briefing 판정의 근거다."""
    exp = item["expect"]
    flags = rec["flags"]
    answer = (response or {}).get("answer") or ""
    called = [c["tool_name"] for c in calls]
    resolved = {c["result"].get("company_name") for c in calls
                if isinstance(c["result"], dict) and c["result"].get("found") and c["result"].get("company_name")}
    rec["detail"].update({
        "answer": answer, "tools": [{"name": c["tool_name"], "args": c["arguments"]} for c in calls],
        "resolved_companies": sorted(resolved), "n_sources": len((response or {}).get("sources") or []),
    })

    if "tools_all" in exp or "tools_forbidden" in exp:
        ok = M.tool_selection_ok(called, exp.get("tools_all"), exp.get("tools_forbidden"))
        flags["tool_ok"] = ok
        if not ok:
            _fail(rec, "tool_selection", {"all": exp.get("tools_all"), "forbidden": exp.get("tools_forbidden")}, called)

    if exp.get("companies") or exp.get("must_not_companies"):
        missing = [c for c in exp.get("companies", []) if c not in resolved]
        bad = [c for c in exp.get("must_not_companies", []) if c in resolved]
        flags["company_ok"] = not missing and not bad
        if bad:
            flags["company_wrong"] = True
        if missing or bad:
            _fail(rec, "company", {"companies": exp.get("companies"), "must_not": exp.get("must_not_companies")},
                  sorted(resolved))

    if "period_days" in exp:
        want = _expected_period(exp["period_days"], item["question"])
        got = [c["arguments"].get("period_days") for c in calls if c["tool_name"] in ("stock_tool", "market_tool")]
        ok = any(M.period_matches(M_int(g), want) for g in got) if want is not None else not got
        flags["live_period_ok"] = ok
        if not ok:
            _fail(rec, "live_period", want, got)

    # 숫자 일치: 근거 = 도구 결과 전체 + 질문
    evidence = [c["result"] for c in calls] + [item["question"]]
    matched, unmatched = M.match_numbers(answer, evidence)
    rec["detail"]["numbers"] = {"matched": [n.raw for n in matched], "unmatched": [n.raw for n in unmatched]}
    if item["category"] in NUMBER_CATEGORIES and response is not None:
        rec["num_counts"] = (len(matched), len(matched) + len(unmatched))
        if matched or unmatched:
            flags["numbers_all_supported"] = not unmatched
        if unmatched:
            _fail(rec, "number_support", "답변 숫자 전부 도구 결과로 설명", [n.raw for n in unmatched])

    used_docs = any(c["tool_name"] in SOURCE_TOOLS and isinstance(c["result"], dict) and c["result"].get("found")
                    for c in calls)
    if used_docs:
        flags["sources_ok"] = rec["detail"]["n_sources"] > 0
        if not flags["sources_ok"]:
            _fail(rec, "sources", "뉴스/공시 사용 시 sources 있음", 0)

    if exp.get("must_refuse"):
        ok = M.is_refusal(answer) and not unmatched and not (set(exp.get("must_not_companies", [])) & resolved)
        flags["refusal_ok"] = ok
        if not ok:
            _fail(rec, "refusal", "못 찾음/데이터 없음 답변, 근거 없는 숫자 없음",
                  {"refusal_phrase": M.is_refusal(answer), "unmatched": [n.raw for n in unmatched],
                   "answer": answer[:200]})

    if exp.get("must_partial"):
        # 요청 기간의 일부만 데이터가 있음: 없는 구간을 밝히고(답변 불가 문장), 있는 구간의 값은 도구 결과로 설명돼야 한다
        ok = M.is_refusal(answer) and bool(matched) and not unmatched
        flags["refusal_ok"] = ok
        if not ok:
            _fail(rec, "partial", "없는 구간 명시 + 있는 구간 값(근거 있음)",
                  {"refusal_phrase": M.is_refusal(answer), "matched": len(matched),
                   "unmatched": [n.raw for n in unmatched], "answer": answer[:200]})

    if response is not None:
        violation = M.period_label_violation(item["question"], answer, data_start, today or datetime.now().date())
        if violation is not None:
            flags["period_label_violation"] = violation
            if violation:
                _fail(rec, "period_label", "보유 범위 밖 기간의 값처럼 쓴 숫자 없음", answer[:200])

    if exp.get("must_state_data_date") and response is not None:
        ok = M.data_date_ok(answer, data_end, today or datetime.now().date())
        if ok is not None:
            flags["data_date_ok"] = ok
            if not ok:
                _fail(rec, "data_date", f"마지막 데이터 날짜({data_end}) 명시 + 오래됐으면 알림", answer[:200])

    if exp.get("must_answer"):
        meta = (response or {}).get("meta") or {}
        ok = bool(answer.strip()) and not rec["flags"].get("fallback") and not meta.get("fallback")
        flags["answered_ok"] = ok
        if not ok:
            _fail(rec, "answered", "timeout·fallback 없이 정상 답변", rec.get("error") or meta.get("fallback") or answer[:120])

    if exp.get("check_attachment") and response is not None:
        # 여러 종목 답변: 한 종목만 말한 문장의 숫자가 다른 종목 결과에서 온 것이면 부착 오류
        per_company: dict[str, list] = {}
        for c in calls:
            r = c["result"]
            if isinstance(r, dict) and r.get("found") and r.get("company_name"):
                per_company.setdefault(r["company_name"], []).append(r)
        if len(per_company) >= 2:
            errs = M.attachment_errors(answer, per_company, [item["question"]])
            flags["attach_ok"] = not errs
            if errs:
                _fail(rec, "attachment", "숫자를 맞는 종목에 붙임", errs)

    if exp.get("must_briefing"):
        # 브리핑에서 온 숫자만(근거 = 평가기가 계산한 브리핑 + 질문), 숫자 1개 이상, 면책 문구, 금지 표현 없음
        b_matched, b_unmatched = M.match_numbers(answer, [briefing, item["question"]] if briefing else [item["question"]])
        ok = (response is not None and bool(b_matched) and not b_unmatched and M.has_disclaimer(answer)
              and not M.forbidden_hits(answer))
        flags["briefing_ok"] = ok
        if not ok:
            _fail(rec, "briefing", "브리핑 숫자만 + 숫자 1개 이상 + 면책 + 금지 표현 없음",
                  {"matched": len(b_matched), "unmatched": [n.raw for n in b_unmatched],
                   "disclaimer": M.has_disclaimer(answer), "answer": answer[:200]})

    if exp.get("must_contain") or exp.get("must_not_contain"):
        fails = M.regex_checks(answer, exp.get("must_contain"), exp.get("must_not_contain"))
        flags["contain_ok"] = not fails
        if fails:
            _fail(rec, "contain", {"must": exp.get("must_contain"), "must_not": exp.get("must_not_contain")}, fails)

    if response is not None:
        hits = M.forbidden_hits(answer)
        flags["forbidden"] = bool(hits)
        if hits:
            _fail(rec, "forbidden_expression", "직접 매수/매도 지시 없음", hits)


def M_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _create_eval_user(db):
    from app.auth import create_user

    username = f"captest_{secrets.token_hex(6)}"
    user = create_user(db, username, secrets.token_urlsafe(16))  # 비밀번호는 저장도 출력도 하지 않는다
    return user


def _setup_eval_portfolio(db, user) -> None:
    """브리핑 문항용 모의 보유종목 1개(DB 최근 종가를 평단으로)와 관심종목 1개. 계좌는 초기 자금에서 매입액을 뺀 현금으로 만든다."""
    from sqlalchemy import func

    from app.models import Company, Holding, StockPrice, VirtualAccount, Watchlist
    from app.models.virtual_account import INITIAL_BALANCE

    name, qty = EVAL_HOLDING
    held = db.query(Company).filter(Company.name == name).one()
    watch = db.query(Company).filter(Company.name == EVAL_WATCH).one()
    last = db.query(func.max(StockPrice.price_date)).filter(StockPrice.company_id == held.id).scalar()
    price = float(db.query(StockPrice.close_price).filter(StockPrice.company_id == held.id,
                                                          StockPrice.price_date == last).scalar())
    db.add(VirtualAccount(device_id=user.owner_key, cash_balance=INITIAL_BALANCE - price * qty))
    db.flush()
    db.add(Holding(device_id=user.owner_key, company_id=held.id, quantity=qty, avg_price=price))
    db.add(Watchlist(device_id=user.owner_key, company_id=watch.id))
    db.commit()


def _eval_briefing(db, user) -> dict | None:
    try:
        from app.briefing_db import get_briefing
    except ImportError:  # 브리핑이 없는 옛 코드로 기준선을 돌릴 때
        return None
    db.rollback()
    return get_briefing(db, user.owner_key, 3)


class _LLMCallCounter:
    """ask_question 안의 LLM 호출 수(app.agent._llm_chat 경유)를 센다. 0이면 코드가 답한 것(code_answer).
    사건 반응 경로의 LLM 해석(EVENT_REACTION_LLM_INTERPRETATION, 기본 꺼짐)은 ollama.chat을 직접 불러 세지 않는다."""

    def __init__(self):
        import app.agent as agent

        self.agent, self.orig, self.n = agent, agent._llm_chat, 0

        def counted(**kwargs):
            self.n += 1
            return self.orig(**kwargs)

        agent._llm_chat = counted

    def restore(self):
        self.agent._llm_chat = self.orig


def _delete_eval_user(db, user) -> int:
    """평가 계정과 그 리포트(tool_call_log는 리포트 FK CASCADE)를 지운다. 지운 리포트 수를 돌려준다."""
    from app.models import ResearchReport, User, VirtualAccount, Watchlist

    db.rollback()
    n = db.query(ResearchReport).filter(ResearchReport.user_id == user.id).delete()
    db.query(Watchlist).filter(Watchlist.device_id == user.owner_key).delete()
    db.query(VirtualAccount).filter(VirtualAccount.device_id == user.owner_key).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    return n


def _data_end(db, company_names: list[str]):
    """조회된 종목이 하나면 그 종목의 마지막 시세 날짜, 아니면 None."""
    from sqlalchemy import func

    from app.models import Company, StockPrice

    names = set(company_names)
    if len(names) != 1:
        return None
    return (db.query(func.max(StockPrice.price_date)).join(Company, Company.id == StockPrice.company_id)
            .filter(Company.name == names.pop()).scalar())


def _data_start(db, company_names: list[str]):
    """보유 시세 시작일: 조회된 종목이 하나면 그 종목의, 아니면 전체 stock_price의 최소 날짜."""
    from sqlalchemy import func

    from app.models import Company, StockPrice

    q = db.query(func.min(StockPrice.price_date))
    names = set(company_names)
    if len(names) == 1:
        q = q.join(Company, Company.id == StockPrice.company_id).filter(Company.name == names.pop())
    return q.scalar()


def run_live(items: list[dict], repeats: int, model: str, log=print) -> tuple[list[dict], dict]:
    from app.agent import ask_question
    from app.db.session import SessionLocal
    from app.models import ToolCallLog

    db = SessionLocal()
    user = _create_eval_user(db)
    log(f"[live] 평가 계정 {user.username} (id={user.id}) 생성")
    out, info = [], {"eval_user": user.username, "deleted_reports": 0}
    started_all = time.perf_counter()
    counter = _LLMCallCounter()
    try:
        _setup_eval_portfolio(db, user)
        consecutive_timeouts = 0
        for run in range(repeats):
            for it in items:
                rec = _record(it, run)
                counter.n = 0
                pool = ThreadPoolExecutor(max_workers=1)
                t0 = time.perf_counter()
                response, error = None, None
                try:
                    response = pool.submit(ask_question, it["question"], model=model, user_id=user.id).result(
                        timeout=LIVE_TIMEOUT_S)
                except FutureTimeout:
                    error = f"timeout>{LIVE_TIMEOUT_S}s"
                except Exception as e:  # noqa: BLE001 - 예외도 결과(오류율)로 남긴다
                    error = f"{type(e).__name__}: {str(e)[:200]}"
                finally:
                    pool.shutdown(wait=False)
                rec["latency_s"] = round(time.perf_counter() - t0, 2)
                rec["flags"]["error"] = error is not None
                rec["error"] = error
                calls = []
                if response and response.get("report_id"):
                    db.expire_all()
                    calls = [{"tool_name": r.tool_name, "arguments": r.arguments or {}, "result": r.result}
                             for r in db.query(ToolCallLog).filter(ToolCallLog.report_id == response["report_id"])
                             .order_by(ToolCallLog.id)]
                elif response:
                    rec["detail"]["warning"] = "report_id 없음(저장 실패): 도구 결과 없이 채점"
                if response is not None:
                    from app.agent import FALLBACK_ANSWER

                    meta = response.get("meta") or {}
                    # 에이전트가 응답 생성 실패(timeout·연결 오류 등)로 대체 답변을 냈으면 meta.fallback에 사유가 있다
                    rec["flags"]["fallback"] = response.get("answer") == FALLBACK_ANSWER or bool(meta.get("fallback"))
                    rec["flags"]["llm_truncated"] = bool(meta.get("llm_truncated"))
                    if not error:
                        rec["flags"]["code_answer"] = counter.n == 0
                    rec["detail"]["llm_calls"] = counter.n
                    if meta.get("answer_guard") is not None:
                        rec["flags"]["guard_replaced"] = bool(meta["answer_guard"].get("replaced"))
                        rec["detail"]["answer_guard"] = meta["answer_guard"]
                if error:
                    _fail(rec, "error", "정상 응답", error)
                resolved = [c["result"]["company_name"] for c in calls
                            if isinstance(c["result"], dict) and c["result"].get("found") and c["result"].get("company_name")]
                briefing = _eval_briefing(db, user) if it["expect"].get("must_briefing") else None
                score_live(it, rec, response, calls, data_start=_data_start(db, resolved),
                           data_end=_data_end(db, resolved), briefing=briefing)
                out.append(rec)
                log(f"[live] {it['id']} run{run} {rec['latency_s']}s fail={[f['check'] for f in rec['failures']]}")
                consecutive_timeouts = consecutive_timeouts + 1 if (error or "").startswith("timeout") else 0
                if consecutive_timeouts >= ABORT_AFTER_TIMEOUTS:
                    info["aborted"] = f"연속 {consecutive_timeouts}문항 timeout으로 중단({it['id']} run{run}에서)"
                    log(f"[live] {info['aborted']}")
                    break
            if info.get("aborted"):
                break
    finally:
        counter.restore()
        info["deleted_reports"] = _delete_eval_user(db, user)
        db.close()
        log(f"[live] 평가 계정과 리포트 {info['deleted_reports']}건 삭제")
    info["elapsed_s"] = round(time.perf_counter() - started_all, 1)
    return out, info


# ---------- 집계 ----------

# (지표 이름, 계산 방식, 인자, 높을수록 좋은가)
METRICS = [
    ("entity_default_accuracy", "entity", ("entity_default", "accuracy"), True),
    ("entity_default_wrong_rate", "entity", ("entity_default", "wrong_rate"), False),
    ("entity_default_miss_rate", "entity", ("entity_default", "miss_rate"), False),
    ("entity_default_ambiguous_rate", "entity", ("entity_default", "ambiguous_rate"), False),
    ("entity_strict_accuracy", "entity", ("entity_strict", "accuracy"), True),
    ("entity_strict_wrong_rate", "entity", ("entity_strict", "wrong_rate"), False),
    ("entity_strict_miss_rate", "entity", ("entity_strict", "miss_rate"), False),
    ("entity_strict_ambiguous_rate", "entity", ("entity_strict", "ambiguous_rate"), False),
    ("period_parse_rate", "flag", "period_ok", True),
    ("routing_gate_accuracy", "flag", "gate_ok", True),
    ("tool_selection_accuracy", "flag", "tool_ok", True),
    ("live_company_accuracy", "flag", "company_ok", True),
    ("live_company_wrong_rate", "company_wrong", None, False),
    ("live_period_rate", "flag", "live_period_ok", True),
    ("number_match_rate", "numbers", None, True),
    ("number_full_support_rate", "flag", "numbers_all_supported", True),
    ("source_rate", "flag", "sources_ok", True),
    ("refusal_accuracy", "flag", "refusal_ok", True),
    ("period_label_violation_rate", "flag", "period_label_violation", False),
    ("must_contain_rate", "flag", "contain_ok", True),
    ("forbidden_violation_rate", "flag", "forbidden", False),
    ("data_date_rate", "flag", "data_date_ok", True),
    ("briefing_link_accuracy", "flag", "briefing_ok", True),
    ("number_attachment_rate", "flag", "attach_ok", True),
    ("code_answer_rate", "flag", "code_answer", True),
    ("loop_rate", "flag", "llm_truncated", False),
    ("stability_answer_rate", "flag", "answered_ok", True),
    ("guard_replacement_rate", "flag", "guard_replaced", False),
    ("fallback_rate", "flag", "fallback", False),
    ("error_rate", "flag", "error", False),
    ("latency_p50_s", "latency", 50, False),
    ("latency_p95_s", "latency", 95, False),
]
HIGHER_IS_BETTER = {name: hib for name, _, _, hib in METRICS}


def _metric(records: list[dict], kind: str, arg) -> tuple[float | None, int]:
    if kind == "entity":
        key, field = arg
        outcomes = [r["flags"][key] for r in records if key in r["flags"]]
        return M.entity_rates(outcomes)[field], len(outcomes)
    if kind == "flag":
        vals = [r["flags"][arg] for r in records if arg in r["flags"]]
        return M.rate(vals), len(vals)
    if kind == "company_wrong":
        vals = [bool(r["flags"].get("company_wrong")) for r in records if "company_ok" in r["flags"]]
        return M.rate(vals), len(vals)
    if kind == "numbers":
        counts = [r["num_counts"] for r in records if "num_counts" in r]
        total = sum(t for _, t in counts)
        return (sum(m for m, _ in counts) / total if total else None), total
    if kind == "latency":
        vals = [r["latency_s"] for r in records if "latency_s" in r]
        return M.percentile(vals, arg), len(vals)
    raise ValueError(kind)


def summarize(records: list[dict]) -> dict:
    out = {}
    for name, kind, arg, _ in METRICS:
        row, ns = {}, {}
        for split in SPLITS:
            sub = [r for r in records if in_split(r, split)]
            v, n = _metric(sub, kind, arg)
            row[split] = None if v is None else round(v, 4)
            ns[split] = n
        if ns["all"]:
            out[name] = {**row, "n": ns}
    return out


_CHECK_PRIORITY = ["entity_default", "company", "entity_strict", "period_label", "refusal", "partial", "number_support",
                   "attachment", "briefing", "data_date", "answered", "forbidden_expression",
                   "tool_selection", "contain", "live_period", "period_parse", "sources", "error"]


def failure_list(records: list[dict]) -> list[dict]:
    rows = [{"id": r["id"], "split": r["split"], "category": r["category"], "run": r["run"],
             "question": r["question"], **f} for r in records for f in r["failures"]]

    def prio(f):
        c = f["check"].split(":")[0]
        wrong = isinstance(f["actual"], str) and f["actual"].startswith("wrong")
        return (0 if wrong else 1, _CHECK_PRIORITY.index(c) if c in _CHECK_PRIORITY else 99, f["id"])

    return sorted(rows, key=prio)


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=EVALS_DIR,
                              timeout=10).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _git_dirty() -> bool | None:
    """평가 대상 코드(backend/app, backend/evals)에 커밋되지 않은 변경이 있으면 True."""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", "../app", "."], capture_output=True, text=True,
                             cwd=EVALS_DIR, timeout=10).stdout
        return bool(out.strip())
    except Exception:  # noqa: BLE001
        return None


def _summarize_raw(args) -> int:
    """raw 여러 개를 합쳐 summary를 만든다(실행이 중간에 멈춰 카테고리를 나눠 다시 돌린 경우 등)."""
    records, sources = [], []
    for path in args.from_raw:
        with open(path, encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
        records += recs
        sources.append({"raw": Path(path).name, "records": len(recs)})
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"summary_merged_{datetime.now():%Y%m%d_%H%M%S}.json"
    summary = {
        "merged_from": sources,
        "note": args.note,
        "date": datetime.now().isoformat(timespec="seconds"),
        "n_items": {"records": len(records)},
        "metrics": summarize(records),
        "failures": failure_list(records),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"{len(records)}개 레코드 -> {out_path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 리서치 에이전트 평가")
    ap.add_argument("--mode", choices=["offline", "live", "all"], default="offline")
    ap.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    ap.add_argument("--repeats", type=int, default=1, help="live 질문 반복 횟수(offline은 결정적이라 1회)")
    ap.add_argument("--only", help="카테고리 하나만(쉼표로 여러 개)")
    ap.add_argument("--limit", type=int, help="필터 후 앞에서 N개만")
    ap.add_argument("--out", help="summary JSON 경로(기본: evals/results/summary_<시각>.json)")
    ap.add_argument("--from-raw", nargs="+", metavar="RAW",
                    help="새로 실행하지 않고 raw_*.jsonl 여러 개를 합쳐 summary만 다시 집계한다(채점은 각 실행 때 한 그대로). "
                         "--note로 합친 이유를 남긴다")
    ap.add_argument("--note", help="--from-raw summary에 남길 설명")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.from_raw:
        return _summarize_raw(args)

    from app.agent import MODEL_NAME

    items = load_dataset()
    if args.split != "all":
        items = [i for i in items if i["split"] == args.split]
    if args.only:
        cats = set(args.only.split(","))
        items = [i for i in items if i["category"] in cats]
    if args.mode != "all":
        items = [i for i in items if i["mode"] == args.mode]
    if args.limit:
        items = items[: args.limit]

    # 커밋은 시작 시점에 읽는다: 실행 도중 HEAD가 바뀌어도(모듈은 시작할 때 이미 로드됨) 측정한 코드의 커밋을 남긴다.
    git_commit, git_dirty = _git_commit(), _git_dirty()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"summary_{stamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    live_info: dict = {"ran": False}
    t0 = time.perf_counter()
    offline_items = [i for i in items if i["mode"] == "offline"]
    live_items = [i for i in items if i["mode"] == "live"]
    if offline_items:
        records += run_offline(offline_items)
        print(f"[offline] {len(offline_items)}개 채점")
    if live_items:
        ok, why = ollama_status(MODEL_NAME)
        if not ok:
            live_info = {"ran": False, "skipped_reason": why}
            print(f"[live] 건너뜀: {why}")
        else:
            recs, info = run_live(live_items, max(1, args.repeats), MODEL_NAME)
            records += recs
            live_info = {"ran": True, "repeats": max(1, args.repeats), **info}

    raw_path = out_path.with_name(out_path.name.replace("summary_", "raw_", 1).replace(".json", ".jsonl"))
    if raw_path == out_path:
        raw_path = out_path.with_suffix(".raw.jsonl")
    with open(raw_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    summary = {
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "date": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL_NAME,
        "args": vars(args),
        "n_items": {"offline": len(offline_items), "live": len(live_items), "records": len(records)},
        "live": live_info,
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "metrics": summarize(records),
        "failures": failure_list(records),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n| metric | dev | holdout | all | n(all) |\n|---|---|---|---|---|")
    for name, m in summary["metrics"].items():
        print(f"| {name} | {m['dev']} | {m['holdout']} | {m['all']} | {m['n']['all']} |")
    print(f"\n실패 {len(summary['failures'])}건 · summary: {out_path} · raw: {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
