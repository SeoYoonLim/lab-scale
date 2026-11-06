"""프런트엔드 번역 리소스 초안(docs/i18n/*.json)이 네 언어 모두 같은 키를 갖고, 백엔드 값(면책·안내 문구, 금융 용어, 분석 유형,
날짜 그룹, 플래그 코드)과 어긋나지 않는지 본다(2026-11-05)."""

import json
from pathlib import Path

import pytest

from app import i18n
from app.daily_briefing import (SECTION_EMPTY, SECTION_NO_DATA, SECTION_OK, SECTION_PARTIAL, STATUS_DELAYED, STATUS_LATEST,
                                STATUS_NO_DATA, STATUS_STALE)
from app.diagnosis_i18n import T as DIAG_T
from app.history_dates import DATE_GROUPS
from app.i18n_translate import FINANCE_TERMS
from app.reports import ANALYSIS_TYPES

DIR = Path(__file__).resolve().parents[2] / "docs" / "i18n"


def _load(locale):
    return json.loads((DIR / f"{locale}.json").read_text(encoding="utf-8"))


def _paths(node, prefix=""):
    if not isinstance(node, dict):
        return {prefix}
    return set().union(*[_paths(v, f"{prefix}.{k}" if prefix else k) for k, v in node.items()]) if node else {prefix}


@pytest.fixture(scope="module")
def res():
    return {loc: _load(loc) for loc in i18n.SUPPORTED_LOCALES}


def test_same_keys_and_no_empty_strings(res):
    keys = {loc: _paths({k: v for k, v in data.items() if k != "_meta"}) for loc, data in res.items()}
    assert all(k == keys["ko-KR"] for k in keys.values())

    def values(node):
        return [x for v in node.values() for x in (values(v) if isinstance(v, dict) else [v])]

    for loc, data in res.items():
        assert all(isinstance(v, str) and v.strip() for v in values({k: v for k, v in data.items() if k != "_meta"})), loc


def test_matches_backend_values(res):
    for loc, data in res.items():
        assert data["_meta"]["locale"] == loc
        for key in ("disclaimer", "fallback_answer", "timeout_answer"):
            assert data["system"][key] == i18n.text(key, loc)
        assert set(data["date_group"]) == set(DATE_GROUPS)
        assert set(data["analysis_type"]) == set(ANALYSIS_TYPES)
        assert set(data["diagnosis_flag"]) == set(DIAG_T["en-US"]["sug"])  # 진단 플래그 코드 전부
        assert set(data["finance_terms"]) == set(FINANCE_TERMS)
        for ko, by_loc in FINANCE_TERMS.items():
            assert data["finance_terms"][ko] == (ko if loc == "ko-KR" else by_loc[loc])
        assert set(data["locale_name"]) == set(i18n.SUPPORTED_LOCALES)
        # 일일 브리핑(2026-11-06): 상태 코드가 백엔드 값과 같다
        assert set(data["daily_briefing"]["market_data_status"]) == {STATUS_LATEST, STATUS_DELAYED, STATUS_STALE, STATUS_NO_DATA}
        assert set(data["daily_briefing"]["section_status"]) == {SECTION_OK, SECTION_PARTIAL, SECTION_NO_DATA, SECTION_EMPTY}
