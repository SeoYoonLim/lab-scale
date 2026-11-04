"""분기 실적(DART 단일회사 주요계정 fnlttSinglAcnt) 수집과 답변용 조회(2026-11-03).

- 대상: company.asset_type == "stock"이고 DART corp_code가 있는 종목(app.collectors.get_dart_corp_code, 기존 공시 수집기와 같은 매핑).
  ETF는 제외한다.
- 요청: 종목마다 YEARS x REPORT_CODES(최근 8개 보고서를 덮는 범위). 요청 사이 REQUEST_INTERVAL_S 쉬고, DART가 사용 한도 초과(020)를
  돌려주면 즉시 멈춘다. 재시도·타임아웃은 app.http_retry.
- 저장: 보고서마다 한 행(upsert). 연결(CFS)이 있으면 연결, 없으면 별도(OFS). 매출액·영업이익·당기순이익의 당기 금액(분기보고서는 그
  분기 3개월, 사업보고서는 연간)과 전기 금액(같은 보고서의 전년 동기).
- 답변: 신호 판정에는 쓰지 않는다(참고 정보). recent_for_answer가 최근 분기 ANSWER_ROWS개를 돌려준다. 4분기는 연간 - 3분기 누적.
- 잠정실적(2026-11-04): 정기보고서가 아직 없는 분기는 거래소 잠정실적 공정공시(app/preliminary.py, disclosure.content)를 쓴다.
  답변에 출처(확정 = 정기보고서, 잠정 = 공정공시)와 접수일·발표일을 함께 쓰고, 같은 분기의 정기보고서가 수집되면 확정치로 바뀐다.
- 2026-11-03 점검: SK하이닉스 2026년 2분기 순이익(93.9조)이 매출(79.3조)보다 큰 것은 DART 원본 값 그대로였다(세전이익 122.7조).
  누적(thstrm_add_amount)을 같이 저장해 "1~n분기 합 = 누적"과 이상치를 anomalies로 점검한다(scripts/check_earnings.py).
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
            """(당기 금액, 전기 금액, 당기 누적). 분기·반기보고서의 당기 금액은 그 분기 3개월, 누적은 thstrm_add_amount다."""
            for x in rows:
                if match(x.get("account_nm") or ""):
                    return _amount(x.get("thstrm_amount")), _amount(x.get("frmtrm_amount")), _amount(x.get("thstrm_add_amount"))
            return None, None, None

        rev, rev_prev, rev_cum = pick(lambda n: n in REVENUE_NAMES)
        op, op_prev, op_cum = pick(lambda n: n.startswith(OPERATING_PREFIX))
        net, net_prev, net_cum = pick(lambda n: n.startswith(NET_PREFIX))
        if rev is None and op is None and net is None:
            continue
        return {"fs_div": fs, "revenue": rev, "operating_income": op, "net_income": net, "revenue_prev": rev_prev,
                "operating_income_prev": op_prev, "net_income_prev": net_prev, "revenue_cum": rev_cum,
                "operating_income_cum": op_cum, "net_income_cum": net_cum, "rcept_no": rows[0].get("rcept_no")}
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


ACCOUNTS = ("revenue", "operating_income", "net_income")
QUARTER_OF = {"11013": 1, "11012": 2, "11014": 3}
SUM_TOLERANCE = 0.005  # 분기 합과 누적의 상대 차이 허용(반올림)
YOY_ANOMALY = 5.0  # 전년 동기 대비 ±500% 초과는 이상치 목록에 올린다


def period_label(bsns_year: int, quarter: int) -> str:
    return f"{bsns_year}년 {quarter}분기"


def _f(v):
    return float(v) if v is not None else None


def quarterly_series(rows: list) -> dict[tuple[int, int], dict]:
    """한 종목의 보고서 행들 -> {(사업연도, 분기): 분기(3개월) 값}. 1~3분기는 보고서의 당기 금액, 4분기는 사업보고서 연간 금액에서
    같은 해 3분기 누적(없으면 1~3분기 합)을 뺀 값이다. 연결·별도가 섞이지 않게, 같은 해 보고서가 모두 같은 기준일 때만 4분기를 만든다."""
    by_year: dict[int, dict[str, object]] = {}
    for r in rows:
        by_year.setdefault(r.bsns_year, {})[r.reprt_code] = r
    out: dict[tuple[int, int], dict] = {}
    for year, reps in by_year.items():
        for code, q in QUARTER_OF.items():
            r = reps.get(code)
            if r is not None:
                out[(year, q)] = {"fs_div": r.fs_div, "rcept_no": r.rcept_no, "disclosed_date": r.disclosed_date, "derived": False,
                                  "source": "final", "reprt_code": code,
                                  **{k: _f(getattr(r, k)) for k in ACCOUNTS},
                                  **{f"{k}_prev": _f(getattr(r, f"{k}_prev")) for k in ACCOUNTS}}
        annual = reps.get("11011")
        if annual is None:
            continue
        q3 = reps.get("11014")
        quarters = [reps.get(c) for c in ("11013", "11012", "11014")]
        if any(x is not None and x.fs_div != annual.fs_div for x in quarters):
            continue
        q4 = {"fs_div": annual.fs_div, "rcept_no": annual.rcept_no, "disclosed_date": annual.disclosed_date, "derived": True,
              "source": "final", "reprt_code": "11011"}
        for k in ACCOUNTS:
            total = _f(getattr(annual, k))
            cum = _f(getattr(q3, f"{k}_cum")) if q3 is not None else None
            if cum is None and all(x is not None and getattr(x, k) is not None for x in quarters):
                cum = sum(float(getattr(x, k)) for x in quarters)
            q4[k] = total - cum if total is not None and cum is not None else None
            q4[f"{k}_prev"] = None
        out[(year, 4)] = q4
    for (year, q), v in out.items():
        # 1~3분기의 전년 동기는 보고서에 적힌 전기 금액(같은 기준, 재작성 반영)을 쓰고, 4분기만 전년 4분기 계산값을 쓴다
        prev = out.get((year - 1, q))
        for k in ACCOUNTS:
            if v.get(f"{k}_prev") is None and prev is not None and prev["fs_div"] == v["fs_div"] and prev.get(k) is not None:
                v[f"{k}_prev"] = prev[k]
    return out


def recent_for_answer(db, company, n: int = ANSWER_ROWS) -> list[dict] | None:
    """최근 분기 n개(4분기는 연간 - 1~3분기 누적). ETF거나 테이블이 없으면 None(섹션 생략). 주식인데 행이 없으면 [].
    정기보고서가 없는 분기는 잠정실적 공정공시(app/preliminary.py)로 채운다(source "preliminary"). 같은 분기에 정기보고서가 있으면
    잠정치는 쓰지 않는다. 가장 최근 분기의 매출액이나 영업이익을 인식하지 못했으면(금융사 등 계정 이름이 다른 경우) None으로
    섹션을 생략한다(틀린 값을 보이느니 생략한다). 정정공시가 뒤따른 잠정치는 수치 없이 정정 사실만 보인다(corrected)."""
    if getattr(company, "asset_type", None) != "stock":
        return None
    from sqlalchemy.exc import ProgrammingError

    from app import preliminary
    from app.models import QuarterlyEarnings

    try:
        rows = db.query(QuarterlyEarnings).filter(QuarterlyEarnings.company_id == company.id).all()
    except ProgrammingError:
        db.rollback()
        return None
    series = quarterly_series(rows)
    # 잠정치는 정기보고서와 같은 기준(연결·별도)만 쓴다. 정기보고서가 없으면 연결 잠정치만.
    latest_final = max(series) if series else None
    basis = "별도" if latest_final and series[latest_final]["fs_div"] == "OFS" else "연결"
    cums = {(r.bsns_year, QUARTER_OF[r.reprt_code]): r.revenue_cum for r in rows if r.reprt_code in QUARTER_OF}
    for key, p in preliminary.for_company(db, company, basis).items():
        if key in series:
            continue  # 같은 분기 정기보고서(확정치)가 있으면 잠정치를 쓰지 않는다
        if key[1] > 1 and not preliminary.cumulative_ok(p, cums.get((key[0], key[1] - 1))):
            continue  # 분기·누적이 서로 맞지 않는 잠정치(기준이 다른 공시 등)는 쓰지 않는다
        if p["corrected"] is None and (p["revenue"] is None or p["operating_income"] is None
                                       or revenue_suspect(p["revenue"], p["operating_income"])):
            continue  # 표를 다 읽지 못한 잠정치는 건너뛰고 정기보고서 분기를 쓴다
        series[key] = {**p, "source": "preliminary", "derived": False}
    if not series:
        return []
    keys = sorted(series, reverse=True)[:n]
    if any(series[k].get("source") == "final" and (series[k].get("revenue") is None or series[k].get("operating_income") is None
           or revenue_suspect(series[k]["revenue"], series[k]["operating_income"])) for k in keys):
        return None
    out = []
    for year, q in keys:
        v = dict(series[(year, q)])
        v.update(period_label=period_label(year, q), bsns_year=year,
                 quarter=q, disclosed_date=v["disclosed_date"].isoformat())
        out.append(v)
    return out


def revenue_suspect(revenue: float | None, operating_income: float | None) -> bool:
    """영업이익이 매출액보다 크면 매출 계정을 잘못 고른 것으로 본다(지주사가 '영업수익' 대신 하위 '매출'을 고른 경우 등).
    영업이익 = 매출 - 비용이라 정상 값에서는 일어나지 않는다. 답변에서는 실적 섹션을 생략한다(틀린 값보다 없는 게 낫다)."""
    return revenue is not None and operating_income is not None and revenue > 0 and operating_income > revenue


def anomalies(rows: list) -> list[str]:
    """수집 결과 점검(DB 없음): |당기순이익| > 매출액, 분기 값 > 같은 해 누적, 1~n분기 합 != 누적, 보고서의 전기 금액 != 전년 행,
    전년 동기 대비 ±500% 초과. 사람이 확인할 목록이다(원본 값이 실제로 그런 경우도 있다)."""
    out = []
    by = {(r.bsns_year, r.reprt_code): r for r in rows}
    for r in rows:
        tag = f"{r.bsns_year} {r.reprt_code} {r.fs_div}"
        rev, net, op = _f(r.revenue), _f(r.net_income), _f(r.operating_income)
        if rev and net is not None and rev > 0 and abs(net) > rev:
            out.append(f"{tag}: |당기순이익| {net:,.0f} > 매출액 {rev:,.0f}")
        if revenue_suspect(rev, op):
            out.append(f"{tag}: 영업이익 {op:,.0f} > 매출액 {rev:,.0f}(매출 계정 인식 의심)")
        q = QUARTER_OF.get(r.reprt_code)
        if q is None:
            continue
        for k in ACCOUNTS:
            cur, cum = _f(getattr(r, k)), _f(getattr(r, f"{k}_cum"))
            if q == 1 and cur is not None and cum is not None and abs(cur - cum) > SUM_TOLERANCE * max(abs(cum), 1):
                out.append(f"{tag} {k}: 1분기 값 {cur:,.0f} != 누적 {cum:,.0f}")
            if q > 1 and cum is not None:
                parts = [by.get((r.bsns_year, c)) for c in ("11013", "11012", "11014")[:q]]
                if all(x is not None and getattr(x, k) is not None and x.fs_div == r.fs_div for x in parts):
                    total = sum(float(getattr(x, k)) for x in parts)
                    if abs(total - cum) > SUM_TOLERANCE * max(abs(cum), 1):
                        out.append(f"{tag} {k}: 1~{q}분기 합 {total:,.0f} != 누적 {cum:,.0f}")
                # 앞 분기가 모두 0 이상일 때만 이상치다(앞 분기 손실이면 누적이 분기 값보다 작을 수 있다)
                earlier = [by.get((r.bsns_year, c)) for c in ("11013", "11012", "11014")[:q - 1]]
                earlier_ok = all(x is not None and getattr(x, k) is not None and float(getattr(x, k)) >= 0 for x in earlier)
                if earlier_ok and cur is not None and cur > cum * (1 + SUM_TOLERANCE) + 1:
                    out.append(f"{tag} {k}: 분기 값 {cur:,.0f} > 누적 {cum:,.0f}")
            prev_row = by.get((r.bsns_year - 1, r.reprt_code))
            prev = _f(getattr(r, f"{k}_prev"))
            if prev_row is not None and prev_row.fs_div == r.fs_div and prev is not None and getattr(prev_row, k) is not None:
                if abs(prev - float(getattr(prev_row, k))) > SUM_TOLERANCE * max(abs(prev), 1):
                    out.append(f"{tag} {k}: 보고서 전기 금액 {prev:,.0f} != 전년 행 {float(getattr(prev_row, k)):,.0f}")
            if cur is not None and prev is not None and prev > 0 and cur >= 0 and abs(cur / prev - 1) > YOY_ANOMALY:
                out.append(f"{tag} {k}: 전년 동기 대비 {(cur / prev - 1) * 100:+.0f}%")
    return out
