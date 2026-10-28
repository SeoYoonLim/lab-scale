"""evals/run_eval.py의 채점·집계 부분(DB, Ollama 없이 가짜 응답으로)과 evals/compare.py."""

from evals import compare, run_eval as R


def _item(category="grounding", expect=None, split="dev"):
    return {"id": "x_01", "split": split, "category": category, "mode": "live", "question": "삼성전자 주가 알려줘",
            "expect": expect or {}}


STOCK = {"tool_name": "stock_tool", "arguments": {"ticker": "삼성전자", "period_days": 1},
         "result": {"found": True, "company_name": "삼성전자", "change_pct": 0.43, "prices": [{"close_price": 235000.0}]}}


def _score(item, answer, calls, sources=()):
    rec = R._record(item)
    R.score_live(item, rec, {"answer": answer, "sources": list(sources)}, calls)
    return rec


def test_grounded_answer_passes():
    rec = _score(_item(expect={"companies": ["삼성전자"], "tools_all": ["stock_tool"]}),
                 "삼성전자의 종가는 235,000원, 등락률은 0.43%입니다.", [STOCK])
    assert rec["failures"] == []
    assert rec["num_counts"] == (2, 2)
    assert rec["flags"] == {"tool_ok": True, "company_ok": True, "numbers_all_supported": True, "forbidden": False}


def test_made_up_number_and_wrong_company_fail():
    other = {**STOCK, "result": {**STOCK["result"], "company_name": "삼성SDI"}}
    rec = _score(_item(expect={"companies": ["삼성에스디에스"], "must_not_companies": ["삼성SDI"]}),
                 "종가는 250,000원입니다. 지금 사세요.", [other])
    checks = {f["check"] for f in rec["failures"]}
    assert checks == {"company", "number_support", "forbidden_expression"}
    assert rec["flags"]["company_wrong"] is True


def test_refusal_requires_phrase_and_no_unsupported_numbers():
    item = _item("refusal", {"must_refuse": True})
    assert _score(item, "'롯데칠성' 종목을 찾지 못했습니다.", [])["flags"]["refusal_ok"] is True
    assert _score(item, "롯데칠성 종가는 150,000원입니다.", [])["flags"]["refusal_ok"] is False


def test_docs_without_sources_fail():
    news = {"tool_name": "news_tool", "arguments": {}, "result": {"found": True, "company_name": "삼성전자", "news": []}}
    rec = _score(_item("tool_selection", {"tools_all": ["news_tool"]}), "뉴스 요약입니다.", [news])
    assert rec["flags"]["sources_ok"] is False


def test_live_period_uses_tool_arguments():
    rec = _score(_item("tool_selection", {"period_days": 20}), "흐름입니다.",
                 [{**STOCK, "arguments": {"ticker": "삼성전자", "period_days": 20}}])
    assert rec["flags"]["live_period_ok"] is True


def test_summarize_splits_and_failure_order():
    a = {**R._record(_item(split="dev")), "flags": {"entity_default": "correct", "gate_ok": True}}
    b = {**R._record(_item(split="holdout")), "flags": {"entity_default": "wrong", "gate_ok": False},
         "failures": [{"check": "entity_default", "expected": "A", "actual": "wrong: B"}]}
    c = {**R._record(_item(split="holdout")), "failures": [{"check": "sources", "expected": 1, "actual": 0}]}
    s = R.summarize([a, b, c])
    assert s["entity_default_accuracy"]["dev"] == 1.0
    assert s["entity_default_accuracy"]["holdout"] == 0.0
    assert s["entity_default_wrong_rate"]["all"] == 0.5
    assert "tool_selection_accuracy" not in s  # 표본 없는 지표는 빠진다
    assert [f["check"] for f in R.failure_list([c, b])] == ["entity_default", "sources"]


def test_compare_table_marks_direction():
    old = {"git_commit": "aaaaaaaa1", "metrics": {"entity_default_wrong_rate": {"dev": 0.1, "holdout": 0.2, "all": 0.15},
                                                 "routing_gate_accuracy": {"dev": 0.5, "holdout": None, "all": 0.5}}}
    new = {"git_commit": "bbbbbbbb2", "metrics": {"entity_default_wrong_rate": {"dev": 0.0, "holdout": 0.2, "all": 0.1},
                                                 "routing_gate_accuracy": {"dev": 0.4, "holdout": None, "all": 0.4}}}
    table = compare.compare_table(old, new)
    assert "| entity_default_wrong_rate | dev | 0.1 | 0 | -0.1 ✅ |" in table
    assert "| routing_gate_accuracy | dev | 0.5 | 0.4 | -0.1 ⚠️ |" in table
    assert "| routing_gate_accuracy | holdout | - | - | - |" in table


def test_partial_answer_needs_missing_note_and_supported_values():
    item = _item("refusal", {"must_partial": True})
    ok = "2026-01-01~2026-06-25 구간은 시세 데이터가 없습니다. 보유 구간 종가는 235,000원입니다."
    assert _score(item, ok, [STOCK])["flags"]["refusal_ok"] is True
    assert _score(item, "종가는 235,000원입니다.", [STOCK])["flags"]["refusal_ok"] is False
    assert _score(item, "데이터가 없습니다.", [STOCK])["flags"]["refusal_ok"] is False


def test_period_label_flag_uses_data_start():
    from datetime import date

    item = {**_item("refusal", {"must_refuse": True}), "question": "삼성전자 2024년 3월 주가 알려줘"}
    rec = R._record(item)
    R.score_live(item, rec, {"answer": "현재 주가는 235,000원입니다.", "sources": []}, [STOCK],
                 data_start=date(2026, 6, 26), today=date(2026, 10, 27))
    assert rec["flags"]["period_label_violation"] is True


def test_seen_unseen_holdout_splits():
    old = {**R._record(_item(split="holdout")), "flags": {"gate_ok": True}}
    new = {**R._record({**_item(split="holdout"), "batch": R.UNSEEN_BATCH}), "flags": {"gate_ok": False}}
    s = R.summarize([old, new])["routing_gate_accuracy"]
    assert (s["holdout"], s["holdout_seen"], s["holdout_unseen"]) == (0.5, 1.0, 0.0)


def test_unseen_holdout_is_the_latest_batch():
    old = {**R._record({**_item(split="holdout"), "batch": 2}), "flags": {"gate_ok": True}}
    new = {**R._record({**_item(split="holdout"), "batch": R.UNSEEN_BATCH}), "flags": {"gate_ok": False}}
    s = R.summarize([old, new])["routing_gate_accuracy"]
    assert (s["holdout_seen"], s["holdout_unseen"]) == (1.0, 0.0)


def test_must_answer_and_meta_flags():
    item = _item("stability", {"must_answer": True})
    rec = R._record(item)
    rec["flags"]["fallback"] = True
    R.score_live(item, rec, {"answer": "응답 생성이 지연되어...", "sources": [], "meta": {"fallback": "timeout"}}, [])
    assert rec["flags"]["answered_ok"] is False
    rec = R._record(item)
    R.score_live(item, rec, {"answer": "KODEX 레버리지는 ...", "sources": []}, [])
    assert rec["flags"]["answered_ok"] is True


def test_from_raw_merges_records(tmp_path):
    import json

    a = {**R._record(_item(split="dev")), "flags": {"gate_ok": True}}
    b = {**R._record(_item(split="holdout")), "flags": {"gate_ok": False}}
    for name, rec in (("raw_a.jsonl", a), ("raw_b.jsonl", b)):
        (tmp_path / name).write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    out = tmp_path / "summary_m.json"
    assert R.main(["--from-raw", str(tmp_path / "raw_a.jsonl"), str(tmp_path / "raw_b.jsonl"), "--out", str(out),
                   "--note", "합침"]) == 0
    s = json.loads(out.read_text(encoding="utf-8"))
    assert s["metrics"]["routing_gate_accuracy"]["all"] == 0.5 and s["note"] == "합침" and len(s["merged_from"]) == 2


# ---------- 3차 라운드(2026-10-28) 추가 지표 ----------

HYNIX = {"tool_name": "stock_tool", "arguments": {"ticker": "SK하이닉스"},
         "result": {"found": True, "company_name": "SK하이닉스", "change_pct": -1.2, "prices": [{"close_price": 1374000.0}]}}


def test_attachment_flags_number_put_on_the_other_company():
    item = _item("attachment", {"companies": ["삼성전자", "SK하이닉스"], "check_attachment": True})
    ok = _score(item, "삼성전자는 235,000원(+0.43%)입니다. SK하이닉스는 1,374,000원(-1.2%)입니다.", [STOCK, HYNIX])
    assert ok["flags"]["attach_ok"] is True
    bad = _score(item, "삼성전자는 1,374,000원입니다. SK하이닉스는 235,000원입니다.", [STOCK, HYNIX])
    assert bad["flags"]["attach_ok"] is False
    assert any(f["check"] == "attachment" for f in bad["failures"])
    # 두 종목을 한 문장에 함께 말하면 보지 않는다(차이 비교 문장)
    both = _score(item, "삼성전자 235,000원, SK하이닉스 1,374,000원입니다.", [STOCK, HYNIX])
    assert both["flags"]["attach_ok"] is True
    # attachment도 숫자 일치를 채점한다
    assert bad["flags"]["numbers_all_supported"] is True


def test_attachment_needs_two_resolved_companies():
    item = _item("attachment", {"companies": ["삼성전자", "SK하이닉스"], "check_attachment": True})
    assert "attach_ok" not in _score(item, "삼성전자는 235,000원입니다.", [STOCK])["flags"]


def test_briefing_uses_only_briefing_numbers_and_needs_disclaimer():
    item = _item("briefing", {"must_briefing": True})
    briefing = {"portfolio": {"total_asset": 10_000_000.0, "day_profit_loss": 173_200.0},
                "holdings": [{"name": "삼성전자", "day_change_pct": 5.39}]}
    disclaimer = " 이 서비스의 분석은 투자 권유나 자문이 아니며, 투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다."

    def score(answer):
        rec = R._record(item)
        R.score_live(item, rec, {"answer": answer, "sources": []}, [], briefing=briefing)
        return rec["flags"]["briefing_ok"]

    assert score("총자산은 10,000,000원이고 전일 대비 173,200원, 삼성전자 +5.39%입니다." + disclaimer) is True
    assert score("총자산은 10,000,000원입니다.") is False  # 면책 없음
    assert score("삼성전자 +7.5%입니다." + disclaimer) is False  # 브리핑에 없는 숫자
    assert score("브리핑을 볼 수 없습니다." + disclaimer) is False  # 숫자 없음
    rec = R._record(item)
    R.score_live(item, rec, {"answer": "총자산 10,000,000원" + disclaimer, "sources": []}, [], briefing=None)
    assert rec["flags"]["briefing_ok"] is False  # 브리핑을 못 구하면 질문 숫자만 근거


def test_new_metrics_are_summarized():
    recs = []
    for i, flags in enumerate([{"code_answer": True, "llm_truncated": False}, {"code_answer": False, "llm_truncated": True}]):
        recs.append({**R._record({**_item(), "id": f"x_{i}"}), "flags": flags})
    s = R.summarize(recs)
    assert s["code_answer_rate"]["all"] == 0.5 and s["loop_rate"]["all"] == 0.5
    assert R.HIGHER_IS_BETTER["code_answer_rate"] is True and R.HIGHER_IS_BETTER["loop_rate"] is False


def test_has_disclaimer():
    from evals import metrics as M

    assert M.has_disclaimer("제공되는 분석과 의견은 투자 권유나 자문이 아니며")
    assert M.has_disclaimer("투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다.")
    assert not M.has_disclaimer("삼성전자 종가는 225,000원입니다.")
