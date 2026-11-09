"""evals/ci_gate.py: 종목 오매칭률 게이트와 job summary 표."""

import json
from pathlib import Path

import pytest

from evals import ci_gate

RESULTS = Path(__file__).resolve().parents[1] / "evals" / "results"


def metric(dev=0.0, holdout=0.0, all_=0.0, n=20):
    return {"dev": dev, "holdout": holdout, "holdout_seen": holdout, "holdout_unseen": None, "all": all_, "n": {"all": n}}


def summary(default=None, strict=None, extra=True):
    metrics = {
        "entity_default_wrong_rate": default or metric(),
        "entity_strict_wrong_rate": strict or metric(),
    }
    if extra:
        metrics["entity_default_accuracy"] = metric(1.0, 0.75, 0.9)
        metrics["period_parse_rate"] = metric(0.5, None, 0.5, n=14)
    return {"metrics": metrics, "failures": [{"id": "ent_08", "split": "dev", "question": "JB금융", "check": "entity_strict"}]}


def test_gate_passes_when_both_wrong_rates_are_zero():
    assert ci_gate.evaluate_gate(summary()["metrics"]) == []


@pytest.mark.parametrize("which, split", [("default", "dev"), ("default", "holdout"), ("default", "all"), ("strict", "dev"), ("strict", "all")])
def test_gate_fails_on_any_nonzero_wrong_rate(which, split):
    bad = metric()
    bad[split] = 0.05
    problems = ci_gate.evaluate_gate(summary(**{which: bad})["metrics"])
    assert len(problems) == 1 and f"[{split}]" in problems[0] and f"entity_{which}_wrong_rate" in problems[0]


def test_other_metrics_do_not_fail_the_gate():
    s = summary()
    s["metrics"]["period_parse_rate"] = metric(0.0, 0.0, 0.0)  # 기간 파싱이 전부 틀려도 게이트는 오매칭만 본다
    assert ci_gate.evaluate_gate(s["metrics"]) == []


@pytest.mark.parametrize("metrics, expected_problems", [
    ({}, 2),  # 지표 자체가 없음: 두 게이트 지표 모두 측정 안 됨
    ({"entity_default_wrong_rate": metric(n=0), "entity_strict_wrong_rate": metric()}, 1),  # 한쪽 표본 0
])
def test_unmeasured_metrics_fail_instead_of_passing_silently(metrics, expected_problems):
    problems = ci_gate.evaluate_gate(metrics)
    assert len(problems) == expected_problems and all("측정되지 않음" in p for p in problems)


def test_markdown_shows_gate_status_table_and_failures():
    text = ci_gate.markdown(summary())
    assert "게이트: **통과**" in text and "| entity_default_wrong_rate 🚧 | 0 | 0 | 0 | 20 |" in text
    assert "| period_parse_rate | 0.5 | - | 0.5 | 14 |" in text and "`ent_08` (dev) JB금융 - entity_strict" in text
    bad = ci_gate.markdown(summary(default=metric(all_=0.05)))
    assert "게이트: **실패**" in bad and "❌" in bad


def test_markdown_truncates_long_failure_lists():
    s = summary()
    s["failures"] = [{"id": f"x{i}", "split": "dev", "question": "q", "check": "c"} for i in range(40)]
    assert "외 25건" in ci_gate.markdown(s)


def test_main_exit_codes_and_stderr(tmp_path, capsys):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(summary()), encoding="utf-8")
    assert ci_gate.main([str(ok)]) == 0
    capsys.readouterr()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(summary(strict=metric(all_=0.1))), encoding="utf-8")
    assert ci_gate.main([str(bad)]) == 1
    captured = capsys.readouterr()
    assert "게이트 실패" in captured.err and "entity_strict_wrong_rate[all]" in captured.err


@pytest.mark.parametrize("name", ["summary_baseline_r4_2026-10-27.json", "summary_after_r4_2026-10-27.json"])
def test_committed_summaries_pass_the_gate(name):
    """저장소에 커밋된 기준선/수정 후 summary는 게이트를 통과한다(오매칭 0)."""
    s = json.loads((RESULTS / name).read_text(encoding="utf-8"))
    assert ci_gate.evaluate_gate(s["metrics"]) == []
