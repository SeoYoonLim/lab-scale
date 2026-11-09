"""도구 결과의 시각 값을 화면용 한국 날짜로(2026-11-09, 38차).

뉴스·공시 도구(news_tool·disclosure_tool·rag_search_tool)는 DB의 timestamptz를 UTC ISO 문자열("2026-10-27T15:00:00+00:00")로
돌려준다. 문자열 앞 10자를 날짜로 쓰면 한국 시간 00:00~09:00의 값이 하루 이르게 나온다. 공시는 한국 시간 자정으로 저장돼서
(UTC 전날 15:00) 모든 공시 날짜가 하루 이르게 나왔다(app.outlook.kst_date가 26차에 고친 것과 같은 문제).

- 오프셋이 있는 시각만 한국 시간으로 바꾼다.
- 날짜만 있는 값("2026-10-28"), 오프셋 없는 시각, rcept_dt("20261028")처럼 ISO 시각이 아닌 값은 예전처럼 앞 10자 그대로다
  (시간대를 모르는 값에 시간대를 임의로 붙이지 않는다).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def kst_day(value) -> str:
    """화면에 쓸 날짜 문자열. 값이 없으면 ""."""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value)
        if len(s) <= 10 or s[10] not in "T ":
            return s[:10]
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return s[:10]
    if dt.tzinfo is None:
        return dt.date().isoformat()
    return dt.astimezone(KST).date().isoformat()
