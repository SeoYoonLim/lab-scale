"""잠정실적(거래소 공정공시)을 disclosure.content에서 읽는다(2026-11-04). 외부 요청 없음.

정기보고서(분기·반기·사업보고서, DART fnlttSinglAcnt -> quarterly_earnings)는 분기가 끝나고 45일(사업보고서 90일) 안에 나온다.
그 사이 회사는 "연결재무제표기준영업(잠정)실적(공정공시)" 또는 "영업(잠정)실적(공정공시)"으로 감사 전 잠정치를 먼저 낸다.
공시 수집기(app/dart_documents.py)가 그 원문 앞 2,000자를 content에 이미 저장하므로(표가 앞부분에 있다) 여기서 표를 읽는다.

- 분기(3개월) 실적만: 당기실적 기간이 분기 첫날 ~ 같은 분기 말일. 월별 실적·판매 대수 공시는 쓰지 않는다.
- 당해실적(그 분기)과 누계실적(연초부터)을 따로 읽는다. 전년 동기는 같은 공시의 전년동기실적(같은 기준).
- 단위: 조원·억원·백만원·천원·원. 값이 "-"면 없음.
- 쓰지 않는 공시: 자회사의 주요경영사항(다른 회사 실적), 정정공시(정정전·정정후 표라 형식이 다르다).
  정정공시가 원 공시(정정관련 공시서류제출일로 찾는다) 뒤에 있으면 원 공시 수치를 싣지 않고 정정이 있었다고만 밝힌다.
- 같은 분기 잠정 공시가 여러 건이면(예: 삼성전자 매출·영업이익만 먼저, 순이익까지 나중) 같은 기준(연결·별도) 안에서 가장 나중 것.
- 연결·별도: 제목·표 머리("연결실적내용")를 먼저 보고, 없으면 그 공시가 자기를 설명하는 문장("별도재무제표기준 영업(잠정)실적입니다",
  "연결기준의 영업(잠정) 실적")을 본다. 한 회사가 같은 날 연결·별도 잠정치를 따로 내기도 하고(현대제철), 별도 잠정치 공시에
  "연결재무제표기준 영업실적은 반기보고서를 참조"라는 문장이 있기도 해서(한화) 아무 데나 있는 "연결"로 판단하지 않는다.
  끝까지 기준을 알 수 없으면 "기준 미표기"이고 답변에 쓰지 않는다(2026-11-04 점검: 카카오·NAVER의 미표기 잠정치는 별도 값이었다).
- 답변에 쓰는 조건(app.earnings.recent_for_answer): 같은 분기의 정기보고서가 없고, 정기보고서와 같은 기준이며, 2~3분기는
  직전 분기 정기보고서 누적 + 이번 분기 당해실적 = 잠정 누계(반올림 허용)일 것(scripts/check_preliminary.py).
"""

import re
from datetime import date

UNITS = {"조원": 1e12, "억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1.0}
ACCOUNT_NAMES = (("revenue", "매출액"), ("operating_income", "영업이익"), ("net_income", "당기순이익"))
SOURCE = "krx_preliminary"
CUM_TOLERANCE = 0.005

_PERIOD_RE = re.compile(r"당기실적 (\d{4})-(\d{2})-(\d{2}) ~ (\d{4})-(\d{2})-(\d{2})")
_UNIT_RE = re.compile(r"단위\s*:\s*(조원|억원|백만원|천원|원)")
_CORRECTION_RE = re.compile(r"^\[[^\]]*(?:정정|추가)[^\]]*\]")
_REF_DATE_RE = re.compile(r"정정관련 공시서류제출일 (\d{4})(?:년 |-)(\d{1,2})(?:월 |-)(\d{1,2})")
_RCEPT_RE = re.compile(r"rcptNo=(\d{14})")
# 계정 줄: "매출액 당해실적 당기 전기 전기대비% 흑자적자 전년동기 전년동기대비% 흑자적자 누계실적 당기누계 ..."
_ROW = r"(?<!\S){name} 당해실적 (\S+) \S+ \S+ \S+ (\S+) \S+ \S+ 누계실적 (\S+)"


def is_preliminary_title(title: str) -> bool:
    return "(잠정)실적" in (title or "")


def _number(token: str, mult: float) -> float | None:
    s = token.replace(",", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return None
    return float(s) * mult


_SELF_BASIS_RE = re.compile(r"(연결|별도)\s*(?:재무제표)?\s*기준(?:의|으로)?\s*(?:영업\s*)?\(?\s*잠정\s*\)?\s*(?:영업\s*)?실적")


def _basis(title: str, content: str) -> tuple[str, str | None]:
    """(표시 기준, fs_div). 제목·표 머리 -> 자기 설명 문장 -> 표 머리 "별도실적내용" 순서. 모르면 기준 미표기."""
    if "연결" in title or "연결실적내용" in content:
        return "연결", "CFS"
    m = _SELF_BASIS_RE.search(content)
    if m:
        return (m.group(1), "CFS" if m.group(1) == "연결" else "OFS")
    if "별도실적내용" in content:
        return "별도", "OFS"
    return "기준 미표기", None


def parse(title: str, content: str) -> dict | None:
    """분기 잠정실적 공시 한 건 -> {bsns_year, quarter, basis, fs_div, unit, revenue, revenue_prev, revenue_cum, ...}.
    분기 실적이 아니거나(월별·판매), 자회사 실적·정정공시이거나, 표를 읽지 못하면 None."""
    title, content = title or "", content or ""
    if not is_preliminary_title(title) or "자회사" in title or _CORRECTION_RE.match(title):
        return None
    p, u = _PERIOD_RE.search(content), _UNIT_RE.search(content)
    if not p or not u:
        return None
    y1, m1, d1, y2, m2, d2 = map(int, p.groups())
    if d1 != 1 or m1 not in (1, 4, 7, 10) or y2 != y1 or m2 != m1 + 2:
        return None
    basis, fs_div = _basis(title, content)
    mult = UNITS[u.group(1)]
    out = {"bsns_year": y1, "quarter": (m1 - 1) // 3 + 1, "basis": basis, "fs_div": fs_div, "unit": u.group(1)}
    for key, name in ACCOUNT_NAMES:
        m = re.search(_ROW.format(name=name), content)
        cur, prev, cum = (_number(x, mult) for x in m.groups()) if m else (None, None, None)
        out.update({key: cur, f"{key}_prev": prev, f"{key}_cum": cum})
    if out["revenue"] is None and out["operating_income"] is None:
        return None
    return out


def _rcept(url: str | None) -> str | None:
    m = _RCEPT_RE.search(url or "")
    return m.group(1) if m else None


def _rcept_date(rcept: str) -> date:
    return date(int(rcept[:4]), int(rcept[4:6]), int(rcept[6:8]))


def from_rows(rows: list[tuple], basis: str | None = None) -> dict[tuple[int, int], dict]:
    """(title, content, source_url) 목록(발표 순) -> {(사업연도, 분기): 잠정치}. basis("연결"/"별도")를 주면 그 기준만.
    정정이 뒤따른 원 공시는 corrected를 채운다."""
    originals, corrections = [], []
    for title, content, url in rows:
        rcept = _rcept(url)
        if rcept is None or not is_preliminary_title(title) or "자회사" in (title or ""):
            continue
        if _CORRECTION_RE.match(title or ""):
            ref = _REF_DATE_RE.search(content or "")
            if ref:
                corrections.append({"rcept_no": rcept, "date": _rcept_date(rcept),
                                    "ref": date(int(ref.group(1)), int(ref.group(2)), int(ref.group(3)))})
            continue
        p = parse(title, content)
        if p is not None and (basis is None or p["basis"] == basis):
            p.update(rcept_no=rcept, disclosed_date=_rcept_date(rcept), source=SOURCE, title=title)
            originals.append(p)
    out: dict[tuple[int, int], dict] = {}
    for p in sorted(originals, key=lambda x: x["rcept_no"]):
        fix = [c for c in corrections if c["ref"] == p["disclosed_date"] and c["date"] >= p["disclosed_date"]]
        p["corrected"] = max(fix, key=lambda c: c["rcept_no"]) if fix else None
        out[(p["bsns_year"], p["quarter"])] = p  # 같은 분기는 나중 공시가 덮는다
    return out


def for_company(db, company, basis: str | None = None) -> dict[tuple[int, int], dict]:
    from app.models import Disclosure

    rows = (db.query(Disclosure.title, Disclosure.content, Disclosure.source_url)
            .filter(Disclosure.company_id == company.id, Disclosure.title.like("%(잠정)실적%"))
            .order_by(Disclosure.disclosed_at, Disclosure.id).all())
    return from_rows([tuple(r) for r in rows], basis)


def cumulative_ok(p: dict, prev_cum: float | None) -> bool:
    """2~4분기 잠정치: 직전 분기 정기보고서 누적 + 이번 분기 당해실적 = 잠정 누계(공시 단위 반올림 2개분 또는 0.5% 허용).
    비교할 값이 없으면 True(막지 않는다)."""
    cur, cum = p.get("revenue"), p.get("revenue_cum")
    if prev_cum is None or cur is None or cum is None:
        return True
    unit = UNITS.get(p.get("unit"), 1.0)
    return abs(float(prev_cum) + cur - cum) <= max(abs(cum) * CUM_TOLERANCE, 2 * unit)
