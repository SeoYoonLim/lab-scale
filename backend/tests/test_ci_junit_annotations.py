"""scripts/ci_junit_annotations.py: pytest junit XML -> GitHub Actions ::error 어노테이션."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ci_junit_annotations", ROOT / "scripts" / "ci_junit_annotations.py")
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
  <testcase classname="tests.test_a" name="test_ok"/>
  <testcase classname="tests.test_a.TestX" name="test_bad[005930]">
    <failure message="assert 404 == 200">tb line 1
AssertionError: {"detail": "not found: 100%"}</failure>
  </testcase>
  <testcase classname="tests.test_b" name="test_err"><error message="fixture failed">E  boom, with: colon</error></testcase>
  <testcase classname="tests.test_b" name="test_skip"><skipped message="skip me"/></testcase>
</testsuite></testsuites>
"""


@pytest.fixture
def xml_path(tmp_path):
    p = tmp_path / "junit.xml"
    p.write_text(XML, encoding="utf-8")
    return str(p)


def test_failures_include_failed_and_errored_but_not_skipped(xml_path):
    got = A.failures(xml_path)
    assert [n for n, _ in got] == ["tests.test_a.TestX::test_bad[005930]", "tests.test_b::test_err"]
    assert "assert 404 == 200" in got[0][1] and "not found" in got[0][1]  # 메시지 + 트레이스백 마지막 줄


def test_annotations_escape_workflow_command_characters(xml_path):
    lines = A.annotations(xml_path)
    assert lines[0].startswith("::error title=tests.test_a.TestX%3A%3Atest_bad[005930]::")  # 제목의 ':'는 이스케이프
    assert "100%25" in lines[0] and "\n" not in lines[0]  # 데이터의 '%'와 줄바꿈
    assert lines[-1] == "::error title=pytest 실패 요약::실패 2개"


def test_only_the_first_nine_tests_are_annotated(tmp_path):
    body = "".join(f'<testcase classname="t" name="n{i}"><failure message="m{i}"/></testcase>' for i in range(15))
    p = tmp_path / "many.xml"
    p.write_text(f'<testsuites><testsuite>{body}</testsuite></testsuites>', encoding="utf-8")
    lines = A.annotations(str(p))
    assert len(lines) == 10 and lines[-1].endswith("실패 15개 (처음 9개만 표시)")


def test_no_failures_prints_nothing(tmp_path, capsys):
    p = tmp_path / "ok.xml"
    p.write_text('<testsuites><testsuite><testcase classname="t" name="ok"/></testsuite></testsuites>', encoding="utf-8")
    assert A.main([str(p)]) == 0 and capsys.readouterr().out.strip() == ""


def test_unreadable_junit_is_a_warning_not_a_crash(tmp_path, capsys):
    assert A.main([str(tmp_path / "missing.xml")]) == 0
    assert capsys.readouterr().out.startswith("::warning title=junit 읽기 실패::")
