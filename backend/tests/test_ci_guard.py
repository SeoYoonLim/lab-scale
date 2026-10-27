"""tests/ci_guard.py: CI에서만 skip을 실패로 바꾸고, 로컬 기본 동작은 그대로인지."""

import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

import ci_guard

BACKEND = Path(__file__).resolve().parents[1]


def skipped_report(reason="stock_price가 비어 있음"):
    return SimpleNamespace(outcome="skipped", skipped=True, longrepr=("tests/x.py", 12, f"Skipped: {reason}"))


def test_skip_becomes_failure_only_when_the_env_flag_is_on():
    r = skipped_report()
    assert ci_guard.skip_to_failure(r, env={}) is False
    assert r.outcome == "skipped"  # 로컬 기본: 그대로
    assert ci_guard.skip_to_failure(r, env={"CI_FAIL_ON_SKIP": "0"}) is False
    assert ci_guard.skip_to_failure(r, env={"CI_FAIL_ON_SKIP": "1"}) is True
    assert r.outcome == "failed" and "stock_price가 비어 있음" in r.longrepr and "needs_collected_data" in r.longrepr


@pytest.mark.parametrize("report", [
    SimpleNamespace(outcome="passed", skipped=False, longrepr=None),
    SimpleNamespace(outcome="failed", skipped=False, longrepr="boom"),
    SimpleNamespace(outcome="skipped", skipped=True, longrepr=("f", 1, "Skipped: x"), wasxfail="reason"),  # xfail은 skip이 아니다
])
def test_other_outcomes_are_untouched(report):
    before = report.outcome
    assert ci_guard.skip_to_failure(report, env={"CI_FAIL_ON_SKIP": "1"}) is False
    assert report.outcome == before


def _run_pytest(tmp_path, env_flag):
    """conftest의 훅이 실제로 연결돼 있는지: 이 폴더의 conftest를 그대로 쓰는 작은 테스트 파일을 따로 돌린다."""
    test = tmp_path / "test_tmp_skip.py"
    test.write_text(textwrap.dedent("""
        import pytest

        def test_skips():
            pytest.skip("데이터 없음")

        def test_passes():
            assert True
    """), encoding="utf-8")
    env = {k: v for k, v in __import__("os").environ.items() if k != "CI_FAIL_ON_SKIP"}
    if env_flag:
        env["CI_FAIL_ON_SKIP"] = "1"
    # tests/conftest.py의 훅만 쓰려고 같은 conftest 폴더 안에 임시 파일을 두지 않고, 훅을 -p로 불러오는 얇은 플러그인으로 확인한다.
    plugin = tmp_path / "ci_guard_plugin.py"
    plugin.write_text(textwrap.dedent(f"""
        import sys
        import pytest
        sys.path.insert(0, {str(Path(__file__).parent)!r})
        import ci_guard

        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_makereport(item, call):
            report = yield
            ci_guard.skip_to_failure(report)
            return report
    """), encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")  # backend/pytest.ini(-m "not slow")를 물려받지 않게
    env["PYTHONPATH"] = str(tmp_path)
    env["PYTHONIOENCODING"] = "utf-8"  # 한글 출력이 윈도우 로케일 인코딩으로 나가 디코딩이 깨지는 것을 막는다
    return subprocess.run([sys.executable, "-m", "pytest", str(test), "-p", "ci_guard_plugin", "-p", "no:cacheprovider", "-q"],
                          capture_output=True, text=True, encoding="utf-8", env=env, cwd=tmp_path)


def test_hook_wiring_end_to_end(tmp_path):
    off = _run_pytest(tmp_path, env_flag=False)
    assert "1 passed, 1 skipped" in off.stdout, off.stdout + off.stderr
    on = _run_pytest(tmp_path, env_flag=True)
    assert "1 failed, 1 passed" in on.stdout and "CI에서 skip됨" in on.stdout, on.stdout + on.stderr


def test_conftest_registers_the_same_hook():
    """실제 tests/conftest.py가 위 플러그인과 같은 훅을 정의하고 ci_guard를 부른다(훅이 빠지면 CI에서 skip이 조용히 묻힌다)."""
    src = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    assert "pytest_runtest_makereport" in src and "ci_guard.skip_to_failure(report)" in src
