"""scripts/refresh_data.py: 단계 선택, 실패 격리, 종료 코드, 마스킹, 장중 판정(순수), --dry-run(integration)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scripts import refresh_data as rd
from scripts.refresh_data import Step

KST = ZoneInfo("Asia/Seoul")


class TestSelectSteps:
    def test_default_is_all_in_order(self):
        assert rd.select_steps(None, None) == list(rd.STEP_ORDER)

    def test_only_keeps_pipeline_order(self):
        assert rd.select_steps(["fx", "prices"], None) == ["prices", "fx"]

    def test_skip(self):
        assert rd.select_steps(None, ["news", "embed", "content"]) == ["prices", "index", "fx"]

    def test_only_and_skip_combined(self):
        assert rd.select_steps(["prices", "index"], ["index"]) == ["prices"]

    def test_unknown_step_rejected(self):
        with pytest.raises(ValueError):
            rd.select_steps(["prices", "bogus"], None)
        with pytest.raises(ValueError):
            rd.select_steps(None, ["bogus"])

    def test_parse_csv(self):
        assert rd.parse_csv(None) is None
        assert rd.parse_csv("a, b,,c") == ["a", "b", "c"]


def _steps(behaviors: dict):
    ran = []

    def make(key):
        def run():
            ran.append(key)
            b = behaviors.get(key)
            if isinstance(b, BaseException):
                raise b
            return b if isinstance(b, str) else None

        return run

    counts = {k: [10, 25] for k in rd.STEP_ORDER}

    def counter(key):
        it = iter(counts[key])
        return lambda: next(it)

    return [
        Step(k, k, make(k), counter(k), lambda: date(2026, 10, 14)) for k in rd.STEP_ORDER
    ], ran


class TestRunSteps:
    def test_all_success(self):
        steps, ran = _steps({})
        res = rd.run_steps(steps, list(rd.STEP_ORDER))
        assert [r.status for r in res] == ["success"] * 6
        assert ran == list(rd.STEP_ORDER)
        assert res[0].rows_added == 15 and res[0].latest_date == date(2026, 10, 14)
        assert rd.exit_code(res) == 0

    def test_failure_is_isolated_and_exit_code_is_one(self):
        steps, ran = _steps({"index": RuntimeError("boom")})
        res = rd.run_steps(steps, list(rd.STEP_ORDER))
        assert [r.status for r in res] == ["success", "failed", "success", "success", "success", "success"]
        assert ran == list(rd.STEP_ORDER)  # 실패 뒤 단계도 실행됐다
        assert res[1].error_type == "RuntimeError"
        assert rd.exit_code(res) == 1

    def test_system_exit_nonzero_is_failure_zero_is_success(self):
        steps, _ = _steps({"fx": SystemExit(1), "news": SystemExit(0)})
        res = {r.step: r for r in rd.run_steps(steps, list(rd.STEP_ORDER))}
        assert res["fx"].status == "failed" and res["fx"].error_type == "SystemExit"
        assert res["news"].status == "success"

    def test_timeout_is_failure_not_crash(self):
        steps, _ = _steps({"news": rd.StepTimeout("x")})
        res = {r.step: r for r in rd.run_steps(steps, list(rd.STEP_ORDER))}
        assert res["news"].status == "failed" and res["news"].error_type == "StepTimeout"
        assert res["embed"].status == "success"

    def test_unselected_steps_are_skipped_and_recorded(self):
        steps, ran = _steps({})
        recorded = []
        res = rd.run_steps(steps, ["prices"], recorder=recorded.append)
        assert ran == ["prices"]
        assert [r.status for r in res] == ["success"] + ["skipped"] * 5
        assert len(recorded) == 6  # 단계마다 한 행
        assert rd.exit_code(res) == 0

    def test_error_message_is_masked_and_not_the_recorded_field(self):
        steps, _ = _steps({"prices": RuntimeError("GET https://x/y?crtfc_key=ABCDEF123456&a=1 failed")})
        res = rd.run_steps(steps, ["prices"])[0]
        assert "ABCDEF123456" not in res.error_message and "crtfc_key=***" in res.error_message
        assert res.error_type == "RuntimeError"

    def test_recorder_failure_does_not_stop_run(self):
        steps, ran = _steps({})

        def bad(_):
            raise OSError("db down")

        res = rd.run_steps(steps, list(rd.STEP_ORDER), recorder=bad)
        assert len(res) == 6 and ran == list(rd.STEP_ORDER)

    def test_warning_does_not_fail_step(self):
        steps, _ = _steps({"prices": "일부 실패 3/300건"})
        res = rd.run_steps(steps, list(rd.STEP_ORDER))
        assert res[0].status == "success" and res[0].warning == "일부 실패 3/300건"

    def test_table_lists_every_step(self):
        steps, _ = _steps({"index": RuntimeError("x")})
        table = rd.format_table(rd.run_steps(steps, ["prices", "index"]))
        for k in rd.STEP_ORDER:
            assert k in table
        assert "failed" in table and "skipped" in table and "RuntimeError" in table


class TestMasking:
    @pytest.mark.parametrize("key", ["crtfc_key", "api_key", "apikey", "client_secret", "token", "key"])
    def test_query_string_secrets(self, key):
        out = rd.mask_secrets(f"HTTPError for url: https://h/p?{key}=SECRETVALUE99&x=1", secrets=())
        assert "SECRETVALUE99" not in out and f"{key}=***&x=1" in out

    def test_env_secret_values(self):
        assert rd.mask_secrets("token abcdefgh12345 end", secrets=("abcdefgh12345",)) == "token *** end"

    def test_plain_text_unchanged(self):
        assert rd.mask_secrets("삼성전자 OK 신규 3건", secrets=()) == "삼성전자 OK 신규 3건"


class TestFailureRatio:
    def test_no_summary_is_none(self):
        assert rd.check_failure_ratio("nothing") is None

    def test_zero_failures(self):
        assert rd.check_failure_ratio('{\n "targets": 300,\n "failures": 0\n}') is None

    def test_partial_failures_warn(self):
        assert "3/300" in rd.check_failure_ratio('"targets": 300,\n "failures": 3')

    def test_majority_failures_raise(self):
        with pytest.raises(rd.FailureRatioTooHigh):
            rd.check_failure_ratio('"targets": 300,\n "failures": 200')


class TestMarketHours:
    @pytest.mark.parametrize(
        "dt,expected",
        [
            (datetime(2026, 10, 14, 9, 0, tzinfo=KST), True),
            (datetime(2026, 10, 14, 15, 30, tzinfo=KST), True),
            (datetime(2026, 10, 14, 15, 31, tzinfo=KST), False),
            (datetime(2026, 10, 14, 8, 59, tzinfo=KST), False),
            (datetime(2026, 10, 10, 11, 0, tzinfo=KST), False),  # 토요일
        ],
    )
    def test_is_market_hours(self, dt, expected):
        assert rd.is_market_hours(dt) is expected


class TestTee:
    def test_masks_throttles_progress_and_shows_failures(self, capsys):
        t = [0.0]
        tee = rd._Tee(__import__("sys").stdout, deadline=None, interval=30, mono=lambda: t[0])
        tee.write("[1/300] 삼성전자 OK\n[2/300] 하이닉스 OK\n[3/300] x FAIL crtfc_key=SECRET99\n")
        t[0] = 31
        tee.write("[4/300] 네이버 OK\n")
        out = capsys.readouterr().out
        assert "[1/300]" in out and "[2/300]" not in out and "[4/300]" in out
        assert "FAIL" in out and "SECRET99" not in out

    def test_per_ticker_save_lines_are_hidden_but_summary_shown(self, capsys):
        tee = rd._Tee(__import__("sys").stdout, deadline=None, mono=lambda: 0.0)
        tee.write("DB 저장 완료: 신규 1건\n[삼성전자(005930)] 수집 중...\n===== 요약 =====\n")
        out = capsys.readouterr().out
        assert "DB 저장 완료" not in out and "수집 중" not in out and "요약" in out

    def test_timeout_raises_step_timeout(self):
        t = [0.0]
        tee = rd._Tee(__import__("sys").stdout, deadline=10, mono=lambda: t[0])
        t[0] = 11
        with pytest.raises(rd.StepTimeout):
            tee.write("[1/2] x\n")


@pytest.mark.integration
class TestDryRunDevDb:
    def test_dry_run_prints_plan_and_writes_nothing(self, dev_db, capsys):
        from sqlalchemy import text

        before = dev_db.execute(text("select count(*) from data_refresh_log")).scalar()
        assert rd.main(["--dry-run"]) == 0
        out = capsys.readouterr().out
        for k in rd.STEP_ORDER:
            assert k in out
        assert dev_db.execute(text("select count(*) from data_refresh_log")).scalar() == before

    def test_dry_run_with_skip(self, capsys):
        assert rd.main(["--dry-run", "--skip", "news,embed,content"]) == 0
        assert "skipped" in capsys.readouterr().out
