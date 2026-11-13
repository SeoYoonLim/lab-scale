"""49차 문제 4: DART 공식 영문명 별칭(app/company_names_dart.py). 수정 전에는 86종목만 외국어 표기가 있었다.

- "Park Systems", "HD Hyundai Marine Solution", "Hanmi Science" 같은 공식 영문명이 종목으로 해석되지 않았다(수정 전 resolver: 찾지 못함).
- 일본어·중국어 표기는 공식 출처를 확인하지 못해 늘리지 않았다(48차 86종목 그대로인지 확인한다).
"""

import json
from pathlib import Path

import pytest

from app.company_names_dart import DART_ENGLISH_NAMES
from app.company_names_i18n import FOREIGN_NAMES, foreign_aliases
from app.tools.company_resolver import _key, extract_from_text, resolve_company

SEED = json.loads((Path(__file__).resolve().parents[1] / "scripts" / "ci_seed_companies.json").read_text(encoding="utf-8"))
SEED_ROWS = SEED if isinstance(SEED, list) else SEED.get("companies") or next(iter(SEED.values()))
SEED_NAMES = sorted({r["name"] for r in SEED_ROWS})


def test_every_dart_target_is_registered_and_every_entry_has_its_source():
    assert [n for n in DART_ENGLISH_NAMES if n not in SEED_NAMES] == []
    text = (Path(__file__).resolve().parents[1] / "app" / "company_names_dart.py").read_text(encoding="utf-8")
    assert text.count("위, DART: ") == len(DART_ENGLISH_NAMES)  # 줄마다 시가총액 순위와 DART 원문 표기


def test_coverage_grew_without_new_unverified_cjk_names():
    covered = set(foreign_aliases().values())
    assert len(covered) >= 250
    assert len(FOREIGN_NAMES) == 86  # 일본어·중국어(와 48차 영어) 사전은 그대로


@pytest.mark.parametrize("word", ["DOOSAN", "HANWHA", "HYOSUNG", "Lotte", "Youngone", "Sphere", "TAESUNG", "APR", "PSK", "ORION"])
def test_group_names_common_words_and_short_acronyms_are_excluded(word):
    assert _key(word) not in {_key(a) for a in foreign_aliases()}


@pytest.mark.parametrize("q, want", [
    ("Park Systems stock over the last 5 trading days", ["파크시스템스"]),
    ("How is HD Hyundai Marine Solution doing?", ["HD현대마린솔루션"]),
    ("Hanmi Science vs Hanmi Semiconductor", ["한미사이언스", "한미반도체"]),
    ("Korean Air Lines and Asiana Airlines", ["대한항공", "아시아나항공"]),
    ("PearlAbyss and Pearl Abyss", ["펄어비스"]),
])
def test_dart_names_in_questions(make_db, q, want):
    assert [c.name for c in extract_from_text(make_db(*SEED_NAMES), q)] == want


def test_longer_name_wins_over_group_prefix(make_db):
    db = make_db(*SEED_NAMES)
    assert [c.name for c in extract_from_text(db, "HD Hyundai Heavy Industries outlook")] == ["HD현대중공업"]
    assert resolve_company(db, "HD HYUNDAI").company.name == "HD현대"
