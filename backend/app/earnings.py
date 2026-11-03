"""분기 실적(DART 단일회사 주요계정 fnlttSinglAcnt) 수집과 답변용 조회(2026-11-03).

- 대상: company.asset_type == "stock"이고 DART corp_code가 있는 종목(app.collectors.get_dart_corp_code, 기존 공시 수집기와 같은 매핑).
  ETF는 제외한다.
- 요청: 종목마다 YEARS x REPORT_CODES(최근 8개 보고서를 덮는 범위). 요청 사이 REQUEST_INTERVAL_S 쉬고, DART가 사용 한도 초과(020)를
  돌려주면 즉시 멈춘다. 재시도·타임아웃은 app.http_retry.
- 저장: 보고서마다 한 행(upsert). 연결(CFS)이 있으면 연결, 없으면 별도(OFS). 매출액·영업이익·당기순이익의 당기 금액(분기보고서는 그
  분기 3개월, 사업보고서는 연간)과 전기 금액(같은 보고서의 전년 동기).
- 답변: 신호 판정에는 쓰지 않는다(참고 정보). recent_for_answer가 최근 보고서 ANSWER_ROWS개를 돌려준다.
"""

import os
import time
from datetime import date

REPORT_CODES = {"11013": "1분기", "11012": "2분기", "11014": "3분기", "11011": "연간"}
REPORT_ORDER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
YEARS = (2024, 2025, 2026)
KEEP_REPORTS = 8
REQUEST_INTERVAL_S = 0.15
ANSWER_ROWS = 2
DART_ACCOUNT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
REVENUE_NAMES = ("매출액", "수익(매출액)", "영업수익", "매출")
OPERATING_PREFIX = "영업이익"
NET_PREFIX = "당기순이익"


class DartLimitExceeded(RuntimeError):
    pass


def _amount(raw) -> int | None:
    s = str(raw or "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_accounts(items: list[dict]) -> dict | None:
    """fnlttSinglAcnt list에서 연결(없으면 별도)의 손익 세 항목. 항목이 하나도 없으면 None."""
    for fs in ("CFS", "OFS"):
        rows = [x for x in items if x.get("fs_div") == fs and x.get("sj_div") in ("IS", "CIS")]
        if not rows:
            continue

        def pick(match):
            for x in rows:
                if match(x.get("account_nm") or ""):
                    return _amount(x.get("thstrm_amount")), _amount(x.get("frmtrm_amount"))
            return None, None

        rev, rev_prev = pick(lambda n: n in REVENUE_NAMES)
        op, op_prev = pick(lambda n: n.startswith(OPERATING_PREFIX))
        net, net_prev = pick(lambda n: n.startswith(NET_PREFIX))
        if rev is None and op is None and net is None:
            continue
        return {"fs_div": fs, "revenue": rev, "operating_income": op, "net_income": net, "revenue_prev": rev_prev,
                "operating_income_prev": op_prev, "net_income_prev": net_prev, "rcept_no": rows[0].get("rcept_no")}
    return None


def fetch_report(corp_code: str, year: int, reprt_code: str) -> dict | None:
    from app import http_retry

    resp = http_retry.get(DART_ACCOUNT_URL, params={"crtfc_key": os.environ["DART_API_KEY"], "corp_code": corp_code,
                                                    "bsns_year": str(year), "reprt_code": reprt_code},
                          label="DART fnlttSinglAcnt")
    data = resp.json()
    status = data.get("status")
    if status == "013":  # 조회 결과 없음(아직 제출 전 등)
        return None
    if status == "020":
        raise DartLimitExceeded("DART 사용 한도 초과(020)")
    if status != "000":
        raise RuntimeError(f"DART 오류 [{status}] {data.get('message')}")
    return parse_accounts(data.get("list") or [])


def collect(db, limit: int | None = None, log=print) -> dict:
    """주식 종목의 최근 보고서 실적을 upsert한다. 반환: 요청·저장·없음·오류 수."""
    from sqlalchemy.dialects.postgresql import insert

    from app.collectors import get_dart_corp_code
    from app.models import Company, QuarterlyEarnings

    stats = {"companies": 0, "requests": 0, "saved": 0, "empty": 0, "errors": 0, "no_corp_code": 0, "stopped": None}
    companies = db.query(Company).filter(Company.asset_type == "stock").order_by(Company.id).all()
    if limit:
        companies = companies[:limit]
    today = date.today()
    started = time.perf_counter()
    for i, c in enumerate(companies, 1):
        corp = get_dart_corp_code(c.ticker)
        if not corp:
            stats["no_corp_code"] += 1
            continue
        stats["companies"] += 1
        for year in YEARS:
            for code in REPORT_CODES:
                if year > today.year:
                    continue
                stats["requests"] += 1
                try:
                    row = fetch_report(corp, year, code)
                except DartLimitExceeded as e:
                    stats["stopped"] = str(e)
                    db.commit()
                    return stats
                except Exception as e:  # noqa: BLE001 - 종목 하나의 오류로 전체를 멈추지 않는다(요약에 센다)
                    stats["errors"] += 1
                    log(f"  {c.name} {year} {code} 오류: {type(e).__name__}")
                    continue
                finally:
                    time.sleep(REQUEST_INTERVAL_S)
                if row is None or not row.get("rcept_no"):
                    stats["empty"] += 1
                    continue
                values = {**row, "company_id": c.id, "bsns_year": year, "reprt_code": code,
                          "disclosed_date": date(int(row["rcept_no"][:4]), int(row["rcept_no"][4:6]), int(row["rcept_no"][6:8]))}
                stmt = insert(QuarterlyEarnings).values(**values)
                stmt = stmt.on_conflict_do_update(constraint="uq_quarterly_earnings_report",
                                                  set_={k: stmt.excluded[k] for k in values if k not in ("company_id", "bsns_year", "reprt_code")})
                db.execute(stmt)
                stats["saved"] += 1
        db.commit()
        if i % 25 == 0:
            log(f"[{i}/{len(companies)}] 요청 {stats['requests']} 저장 {stats['saved']} ({time.perf_counter() - started:.0f}s)")
    return stats


def period_label(bsns_year: int, reprt_code: str) -> str:
    return f"{bsns_year}년 {REPORT_CODES[reprt_code]}"


def recent_for_answer(db, company, n: int = ANSWER_ROWS) -> list[dict] | None:
    """최근 보고서 n개(사업연도·보고서 순서 내림차순). ETF거나 테이블이 없으면 None(섹션 생략), 주식인데 행이 없으면 []."""
    if getattr(company, "asset_type", None) != "stock":
        return None
    from sqlalchemy.exc import ProgrammingError

    from app.models import QuarterlyEarnings

    try:
        rows = db.query(QuarterlyEarnings).filter(QuarterlyEarnings.company_id == company.id).all()
    except ProgrammingError:
        db.rollback()
        return None
    rows.sort(key=lambda r: (r.bsns_year, REPORT_ORDER[r.reprt_code]), reverse=True)
    out = []
    for r in rows[:n]:
        d = {k: (float(getattr(r, k)) if getattr(r, k) is not None else None)
             for k in ("revenue", "operating_income", "net_income", "revenue_prev", "operating_income_prev", "net_income_prev")}
        d.update(period_label=period_label(r.bsns_year, r.reprt_code), fs_div=r.fs_div, rcept_no=r.rcept_no,
                 disclosed_date=r.disclosed_date.isoformat(), bsns_year=r.bsns_year, reprt_code=r.reprt_code)
        out.append(d)
    return out
