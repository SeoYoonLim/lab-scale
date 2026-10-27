"""CI에서 skip을 실패로 바꾸는 장치. 환경변수 CI_FAIL_ON_SKIP=1일 때만 켜지고, 로컬 기본 동작은 그대로다.

배경: 이 저장소의 통합 테스트 일부는 필요한 데이터(시세, ETF, 환율, 시장 지수, 소유자 없는 옛 리포트)가 없으면 스스로 pytest.skip을
부른다. 로컬에서는 편리하지만 CI에서 그대로 두면 "통과 N개"만 보이고 건너뛴 테스트는 조용히 묻힌다. CI에서는 skip이 하나라도 있으면 실패로 만들어서,
데이터가 필요한 테스트는 needs_collected_data 마커로 명시적으로 제외(deselect)하게 한다 - 제외된 개수는 로그에 그대로 나온다.
"""

import os


def skip_to_failure(report, env=None) -> bool:
    """report가 skip이고 CI_FAIL_ON_SKIP=1이면 실패로 바꾸고 True. xfail은 건드리지 않는다."""
    env = os.environ if env is None else env
    if env.get("CI_FAIL_ON_SKIP") != "1":
        return False
    if not getattr(report, "skipped", False) or hasattr(report, "wasxfail"):
        return False
    detail = report.longrepr
    reason = detail[2] if isinstance(detail, tuple) and len(detail) == 3 else str(detail)
    report.outcome = "failed"
    report.longrepr = f"CI에서 skip됨(조용한 skip 금지, 필요하면 needs_collected_data 마커로 명시적으로 제외): {reason}"
    return True
