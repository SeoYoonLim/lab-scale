"""일별 종가 시계열의 품질 점검(DB 없음). scripts/backfill_history.py의 수집 후 점검과 백테스트 응답의 data_warnings가 같이 쓴다.

- 큰 일간 변동: 직전 거래일 종가 대비 |등락률| > JUMP_THRESHOLD(30%) + LIMIT_TOLERANCE(0.5%p). 한국 시장의 가격제한폭(±30%)을
  넘는 값이라 액면분할·병합 등 가격 불연속(수정주가로 조정되지 않은 구간)일 가능성이 있다. 여유 0.5%p를 두는 이유: 상한가·하한가는
  기준가와 호가 단위로 정해져 수정주가로 보면 30.0x%가 될 수 있다(2026-10-29 백필 기준 30.0~30.5% 2건). 레버리지 ETF처럼 제한폭이 더 넓은
  상품이나 실제 급등락일 수도 있으니 "가능성"으로만 알린다.
- 중복 날짜, 역순 날짜(입력 순서가 오름차순이 아님), 0 이하 종가.
- 같은 종가가 STALE_RUN일 이상 이어지는 구간(거래정지 중 같은 값으로 채운 행일 수 있다).
"""

from dataclasses import dataclass
from datetime import date

JUMP_THRESHOLD = 0.30
LIMIT_TOLERANCE = 0.005
STALE_RUN = 5


@dataclass(frozen=True)
class Issue:
    kind: str  # jump | duplicate_date | out_of_order | non_positive | flat_run
    date: date
    detail: str
    value: float | None = None  # jump: 등락률(%), flat_run: 이어진 거래일 수, non_positive: 종가

    def as_dict(self) -> dict:
        return {"kind": self.kind, "date": self.date.isoformat(), "detail": self.detail, "value": self.value}


def check_series(rows: list[tuple[date, float]], threshold: float = JUMP_THRESHOLD + LIMIT_TOLERANCE,
                 stale_run: int = STALE_RUN) -> list[Issue]:
    """rows는 저장/조회된 순서 그대로의 (날짜, 종가). 문제 목록을 날짜 순서대로 돌려준다."""
    issues: list[Issue] = []
    seen: set[date] = set()
    prev_day: date | None = None
    for d, v in rows:
        if d in seen:
            issues.append(Issue("duplicate_date", d, "같은 날짜가 두 번 이상 있습니다."))
        elif prev_day is not None and d < prev_day:
            issues.append(Issue("out_of_order", d, f"{prev_day.isoformat()} 다음에 더 이른 날짜가 있습니다."))
        seen.add(d)
        prev_day = d if prev_day is None or d > prev_day else prev_day
        if v is None or v <= 0:
            issues.append(Issue("non_positive", d, "종가가 0 이하입니다.", v))

    clean = sorted({d: v for d, v in rows if v is not None and v > 0}.items())
    run_start, run_len = None, 1
    for (d0, v0), (d1, v1) in zip(clean, clean[1:]):
        change = v1 / v0 - 1
        if abs(round(change, 9)) > threshold:  # 부동소수점 오차로 경계값이 걸리지 않게 반올림해서 비교
            issues.append(Issue("jump", d1, f"직전 거래일({d0.isoformat()}) 대비 {change * 100:+.1f}%", round(change * 100, 2)))
        if v1 == v0:
            run_start = run_start or d0
            run_len += 1
        else:
            if run_len >= stale_run:
                issues.append(Issue("flat_run", run_start, f"같은 종가가 {run_len}거래일 이어집니다(거래정지 가능성).", run_len))
            run_start, run_len = None, 1
    if run_len >= stale_run:
        issues.append(Issue("flat_run", run_start, f"같은 종가가 {run_len}거래일 이어집니다(거래정지 가능성).", run_len))
    return sorted(issues, key=lambda i: (i.date, i.kind))
