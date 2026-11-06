"""포트폴리오 진단 이력(portfolio_diagnosis) 저장/조회와 두 진단의 비교.

구성(위에서부터 DB 없는 순수 함수 → DB):
- history_item / build_comparison: 이력 항목 요약, 두 진단의 비교. 코드로만 계산한다(LLM 없음)
- record_fields / build_record_comparison: 기록 관리(2026-11-05) view=record의 추가 필드와 비교 보강. 저장본을 읽기만 한다
- save_diagnosis / list_diagnoses / get_diagnosis / get_previous_diagnosis / delete_diagnosis: DB.
  diagnosis_conditions / diagnoses_by_ids: 진단 이력 검색(q)과 통합 검색(app/history_search)이 같이 쓴다.
  모든 쿼리는 user_id가 일치하는 행만 대상으로 한다(남의 것과 없는 것을 구분하지 않는다). user_id에 기본값을 두지 않아
  소유자 조건을 빠뜨릴 수 없게 했다.

record(get_diagnosis가 돌려주는 dict): {"id", "created_at"(datetime), "source", "total_asset", "holdings_count",
"result"(저장된 진단 응답 본문)}.
"""

import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Text, and_, cast, delete, func, literal_column, or_, select

from app.db.session import SessionLocal
from app.diagnosis import NO_RISK_SENTENCE, _pct, _r2
from app.history_terms import ANY_FLAG, flag_codes, term_variants
from app.models import PortfolioDiagnosis
from app.pagination import escape_like, order_by_clauses

logger = logging.getLogger(__name__)

MAX_KEPT_PER_USER = 50  # 사용자당 보관하는 최근 진단 수. 저장 직후 초과분(오래된 것)을 지운다.
SUMMARY_PREVIEW_LEN = 80


# ---------- 순수 계산 ----------


def _flag_brief(flag: dict) -> dict:
    return {
        "code": flag.get("code"),
        "ticker": flag.get("ticker"),
        "company_name": flag.get("company_name"),
        "market": flag.get("market"),
    }


def _history_item(
    id_, created_at, source, total_asset, holdings_count, flags, summary
) -> dict:
    return {
        "id": id_,
        "created_at": created_at.isoformat(),
        "source": source,
        "total_asset": float(total_asset),
        "holdings_count": holdings_count,
        "flags": [_flag_brief(f) for f in flags or []],
        "summary_preview": (summary or "")[:SUMMARY_PREVIEW_LEN],
    }


def history_item(record: dict) -> dict:
    """이력 목록의 한 항목. 플래그는 코드(+종목/시장)만 남기고, 요약은 앞 SUMMARY_PREVIEW_LEN자만 남긴다."""
    result = record["result"]
    return _history_item(
        record["id"], record["created_at"], record["source"], record["total_asset"], record["holdings_count"],
        result.get("flags"), result.get("summary"),
    )


def _flag_key(flag: dict) -> tuple:
    """같은 플래그인지 가르는 키. BIG_LOSS는 종목마다, MARKET_SKEW는 시장마다 따로 있다."""
    return flag.get("code"), flag.get("ticker"), flag.get("market")


def _flag_full(flag: dict) -> dict:
    return {
        "code": flag.get("code"),
        "message": flag.get("message"),
        "value": flag.get("value"),
        "threshold": flag.get("threshold"),
        "ticker": flag.get("ticker"),
        "company_name": flag.get("company_name"),
        "market": flag.get("market"),
    }


def _diff(current: float | None, previous: float | None) -> float | None:
    return None if current is None or previous is None else _r2(current - previous)


def _holding_changes(current: list[dict], previous: list[dict]) -> list[dict]:
    """종목별 비중 변화(%p). status: added(신규 편입) / removed(전량 제외) / kept(양쪽 모두 보유).

    비중은 총자산 대비 %이고, 현재가를 못 구한 종목은 비중이 null이라 그 변화도 null이다. 신규 편입은 이전 비중을
    0으로, 전량 제외는 현재 비중을 0으로 본다. 변화 폭(절댓값)이 큰 순이고 변화를 모르는 종목은 뒤로 간다."""
    cur = {h["ticker"]: h for h in current}
    prev = {h["ticker"]: h for h in previous}
    rows = []
    for ticker in [*cur, *(t for t in prev if t not in cur)]:
        c, p = cur.get(ticker), prev.get(ticker)
        if p is None:
            status, change = "added", c["weight_pct"]
        elif c is None:
            status, change = "removed", None if p["weight_pct"] is None else _r2(-p["weight_pct"])
        else:
            status, change = "kept", _diff(c["weight_pct"], p["weight_pct"])
        rows.append({
            "ticker": ticker,
            "company_name": (c or p)["company_name"],
            "status": status,
            "previous_weight_pct": None if p is None else p["weight_pct"],
            "current_weight_pct": None if c is None else c["weight_pct"],
            "weight_change_pp": change,
        })
    rows.sort(key=lambda r: (r["weight_change_pp"] is None, -abs(r["weight_change_pp"] or 0)))
    return rows


def _return_changes(current: list[dict], previous: list[dict]) -> list[dict]:
    """20거래일 수익률 변화(%p). 양쪽 진단에 모두 있고 두 값 다 null이 아닌 종목만."""
    prev = {h["ticker"]: h for h in previous}
    rows = []
    for c in current:
        p = prev.get(c["ticker"])
        if p is None or c["return_20d_pct"] is None or p["return_20d_pct"] is None:
            continue
        rows.append({
            "ticker": c["ticker"],
            "company_name": c["company_name"],
            "previous_return_20d_pct": p["return_20d_pct"],
            "current_return_20d_pct": c["return_20d_pct"],
            "change_pp": _diff(c["return_20d_pct"], p["return_20d_pct"]),
        })
    rows.sort(key=lambda r: -abs(r["change_pp"]))
    return rows


def compute_changes(current: dict, previous: dict) -> dict:
    """저장된 두 진단 응답(result)의 변화. current가 최신 쪽, previous가 비교 기준이다."""
    cur_asset, prev_asset = current["metrics"]["total_asset"], previous["metrics"]["total_asset"]
    prev_flags = {_flag_key(f): f for f in previous.get("flags", [])}
    cur_flags = {_flag_key(f): f for f in current.get("flags", [])}
    return {
        "total_asset": {
            "previous": prev_asset,
            "current": cur_asset,
            "change_amount": _r2(cur_asset - prev_asset),
            "change_pct": _pct(cur_asset - prev_asset, prev_asset),  # 이전 총자산이 0이면 null
        },
        "holdings": _holding_changes(current["holdings"], previous["holdings"]),
        "flags": {
            "added": [_flag_full(f) for k, f in cur_flags.items() if k not in prev_flags],
            "resolved": [_flag_full(f) for k, f in prev_flags.items() if k not in cur_flags],
        },
        "returns_20d": _return_changes(current["holdings"], previous["holdings"]),
    }


def build_comparison(current: dict, previous: dict | None) -> dict:
    """{"current": 요약, "previous": 요약 또는 None, "changes": 변화 또는 None}. 비교할 이전 진단이 없으면 changes도 None."""
    return {
        "current": history_item(current),
        "previous": None if previous is None else history_item(previous),
        "changes": None if previous is None else compute_changes(current["result"], previous["result"]),
    }


# ---------- 기록 관리(2026-11-05): 목록 기록 필드, 비교 보강. 저장본을 읽기만 한다 ----------

TOP_HOLDINGS = 3
# 비교의 metrics 변화에 넣는 지표(저장된 metrics의 키). 비중은 %p, 금액은 원, HHI는 소수 4자리 차이다.
COMPARED_METRICS = (
    "cash_balance", "cash_weight_pct", "stock_eval_amount", "holding_count",
    "top1_weight_pct", "top3_weight_pct", "herfindahl_index", "total_profit_loss_pct",
)
BASIS_NOTE = ("두 진단은 각각 저장 당시의 보유 구성과 시세로 계산한 결과입니다. 기준 시점이 서로 다르고, "
              "현재 시세로 다시 계산하지 않았습니다.")
BASIS_NOTES = {
    "ko-KR": BASIS_NOTE,
    "en-US": ("Each diagnosis was calculated with the holdings and prices at the time it was saved. The two use different reference points, "
              "and neither was recalculated with current prices."),
    "ja-JP": "2つの診断は、それぞれ保存時点の保有構成と相場で計算した結果です。基準時点が互いに異なり、現在の相場で再計算していません。",
    "zh-CN": "两次诊断分别按保存当时的持仓构成和行情计算。两者的基准时点不同，且均未按当前行情重新计算。",
}
# 진단 제목(언어별): (보유 없음, 1종목, 여러 종목)
_TITLES = {
    "ko-KR": ("포트폴리오 진단", "{name} 진단", "{name} 외 {n}종목 진단"),
    "en-US": ("Portfolio diagnosis", "Diagnosis: {name}", "Diagnosis: {name} and {n} more"),
    "ja-JP": ("ポートフォリオ診断", "{name}の診断", "{name}ほか{n}銘柄の診断"),
    "zh-CN": ("投资组合诊断", "{name}诊断", "{name}等{total}只股票诊断"),
}


def _top_holdings(holdings: list[dict] | None) -> list[dict]:
    """비중이 큰 순 상위 TOP_HOLDINGS개(비중을 모르는 종목은 뒤). 저장된 비중 그대로다."""
    rows = [h for h in holdings or [] if isinstance(h, dict)]
    rows.sort(key=lambda h: (h.get("weight_pct") is None, -(h.get("weight_pct") or 0)))
    return [{"ticker": h.get("ticker"), "company_name": h.get("company_name"), "weight_pct": h.get("weight_pct")}
            for h in rows[:TOP_HOLDINGS]]


def diagnosis_title(top: list[dict], holdings_count: int, locale: str = "ko-KR") -> str:
    """진단 제목(제목 컬럼이 없어서 저장된 보유 종목으로 만든다): "삼성전자 외 2종목 진단". 기록을 만든 언어로 쓴다."""
    none, one, many = _TITLES.get(locale, _TITLES["ko-KR"])
    if not top or not top[0].get("company_name"):
        return none
    name = top[0]["company_name"]
    return one.format(name=name) if holdings_count <= 1 else many.format(name=name, n=holdings_count - 1, total=holdings_count)


def record_fields(result: dict, holdings_count: int, locale: str = "ko-KR") -> dict:
    """진단 이력 view=record 항목의 추가 필드. result는 저장된 진단 응답(또는 그 조각: metrics.cash_weight_pct, holdings,
    risks, flags, generated_at만 있어도 된다)."""
    top = _top_holdings(result.get("holdings"))
    risks = result.get("risks") or []
    codes = []
    for f in result.get("flags") or []:
        if f.get("code") and f["code"] not in codes:
            codes.append(f["code"])
    return {
        "title": diagnosis_title(top, holdings_count, locale),
        "cash_weight_pct": (result.get("metrics") or {}).get("cash_weight_pct"),
        "top_holdings": top,
        "risk_summary": risks[0] if risks else None,
        "flag_codes": codes,
        "generated_at": result.get("generated_at"),
    }


def _diff4(current, previous):
    if current is None or previous is None:
        return None
    return float((Decimal(str(current)) - Decimal(str(previous))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def metric_changes(current: dict, previous: dict) -> dict:
    """저장된 두 진단 응답(result)의 지표 변화: {지표: {"previous", "current", "change"}}. 어느 한쪽이 null이면 change도 null."""
    cur, prev = current.get("metrics") or {}, previous.get("metrics") or {}
    out = {}
    for key in COMPARED_METRICS:
        c, p = cur.get(key), prev.get(key)
        out[key] = {"previous": p, "current": c, "change": _diff4(c, p) if key == "herfindahl_index" else _diff(c, p)}
    return out


def comparison_basis(current: dict, previous: dict | None, locale: str = "ko-KR") -> dict:
    """두 진단이 각각 어떤 시점의 저장본인가. 시세 기준 시각은 저장된 generated_at이다."""
    return {
        "current_created_at": current["created_at"].isoformat(),
        "current_generated_at": current["result"].get("generated_at"),
        "previous_created_at": None if previous is None else previous["created_at"].isoformat(),
        "previous_generated_at": None if previous is None else previous["result"].get("generated_at"),
        "recomputed": False,
        "note": BASIS_NOTES.get(locale, BASIS_NOTE) if previous is not None else None,
    }


def build_record_comparison(current: dict, previous: dict | None, locale: str = "ko-KR") -> dict:
    """build_comparison + 기준 시점(basis)과 지표 변화(metric_changes) + 실질 노출 변화(exposure_changes).

    exposure_changes는 두 저장본 모두에 실질 노출 스냅샷(2026-11-06 이후 진단의 look_through)이 있을 때만 채운다. 그 전 진단은
    스냅샷이 없어서 null이다(현재 구성종목으로 과거 진단을 다시 계산하지 않는다)."""
    from app import look_through

    return {
        **build_comparison(current, previous),
        "basis": comparison_basis(current, previous, locale),
        "metric_changes": None if previous is None else metric_changes(current["result"], previous["result"]),
        "exposure_changes": None if previous is None else look_through.compare(current["result"].get("look_through"),
                                                                                previous["result"].get("look_through")),
    }


# ---------- DB ----------


def _owned(user_id: int):
    return PortfolioDiagnosis.user_id == user_id


def _record(row: PortfolioDiagnosis) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at,
        "source": row.source,
        "total_asset": float(row.total_asset),
        "holdings_count": row.holdings_count,
        "result": row.result,
        "locale": row.result.get("locale") or "ko-KR",
    }


def save_diagnosis(user_id: int, response: dict, locale: str | None = None) -> dict | None:
    """진단 응답 본문을 저장하고 {"id", "created_at"(ISO 문자열)}을 돌려준 뒤, 초과분(오래된 것)을 지운다.

    저장 중 어떤 오류가 나도 예외를 밖으로 내지 않는다(진단 응답을 막지 않기 위해). 실패하면 None.
    로그에는 오류 종류만 남긴다: SQLAlchemy 예외 메시지에는 INSERT 파라미터(= 포트폴리오 내용)가 들어 있어서
    exception()으로 메시지/트레이스백을 찍지 않는다. 정리(prune) 실패는 저장 성공에 영향을 주지 않는다."""
    try:
        db = SessionLocal()
        try:
            row = PortfolioDiagnosis(
                user_id=user_id,
                source=response["source"],
                total_asset=response["metrics"]["total_asset"],
                holdings_count=response["metrics"]["holding_count"],
                # 다국어(2026-11-05): ko-KR이 아닌 진단만 저장본에 locale을 넣는다(키가 없으면 ko-KR). ko-KR 저장본은 예전과 같다
                result={**response, "locale": locale} if locale else response,
            )
            db.add(row)
            db.commit()
            saved = {"id": row.id, "created_at": row.created_at.isoformat()}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        logger.error("portfolio_diagnosis 저장 실패 (진단 응답은 정상 반환됨): user_id=%s, %s", user_id, type(e).__name__)
        return None

    try:
        _prune(user_id)
    except Exception as e:
        logger.error("portfolio_diagnosis 오래된 이력 정리 실패: user_id=%s, %s", user_id, type(e).__name__)
    return saved


def _prune(user_id: int, keep: int = MAX_KEPT_PER_USER) -> None:
    """user_id의 진단 중 최신 keep건만 남기고 지운다."""
    db = SessionLocal()
    try:
        newest = (
            select(PortfolioDiagnosis.id)
            .where(_owned(user_id))
            .order_by(PortfolioDiagnosis.created_at.desc(), PortfolioDiagnosis.id.desc())
            .limit(keep)
        )
        db.execute(delete(PortfolioDiagnosis).where(_owned(user_id), PortfolioDiagnosis.id.not_in(newest)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _search_text():
    """진단 검색 대상 글: 요약·강점·위험·제안·플래그 문장·보유 종목명·종목코드(저장된 result에서, 키 이름은 넣지 않는다)."""
    r = PortfolioDiagnosis.result

    def path(p: str):
        # 상수 경로만 쓴다(사용자 입력이 아니다)
        return cast(func.jsonb_path_query_array(r, literal_column(f"'{p}'::jsonpath")), Text)

    return func.concat_ws(
        " ",
        r["summary"].as_string(),
        cast(r["strengths"], Text),
        # 플래그가 없을 때의 고정 문장("위험 항목은 없습니다")은 빼서 "위험" 검색에 걸리지 않게 한다
        func.replace(cast(r["risks"], Text), NO_RISK_SENTENCE, ""),
        cast(r["suggestions"], Text),
        path("$.flags[*].message"),
        path("$.holdings[*].company_name"),
        path("$.holdings[*].ticker"),
    )


def _term_condition(term: str):
    """검색 낱말 하나(app/history_terms 규칙): 검색 대상 글에 들어 있거나(별칭은 등록명으로도), 위험 키워드면 그 플래그가 있음."""
    text = _search_text()
    conds = [text.ilike(f"%{escape_like(v)}%", escape="\\") for v in term_variants(term)]
    codes = flag_codes(term)
    if ANY_FLAG in codes:
        conds.append(func.jsonb_array_length(PortfolioDiagnosis.result["flags"]) > 0)
    conds += [PortfolioDiagnosis.result["flags"].contains([{"code": c}]) for c in sorted(codes - {ANY_FLAG})]
    return or_(*conds)


def diagnosis_conditions(
    user_id: int, *, terms: list[str] | None = None, start: datetime | None = None, end: datetime | None = None
) -> list:
    """진단 이력 목록·통합 검색의 WHERE 조건. 항상 소유자 조건으로 시작한다."""
    conds = [_owned(user_id)]
    if start is not None:
        conds.append(PortfolioDiagnosis.created_at >= start)
    if end is not None:
        conds.append(PortfolioDiagnosis.created_at < end)
    return conds + [_term_condition(t) for t in terms or []]


def _list_columns():
    r = PortfolioDiagnosis.result
    return (
        PortfolioDiagnosis.id,
        PortfolioDiagnosis.created_at,
        PortfolioDiagnosis.source,
        PortfolioDiagnosis.total_asset,
        PortfolioDiagnosis.holdings_count,
        r["flags"],
        r["summary"].as_string(),
        # 기록 필드용 조각(2026-11-05)
        r["metrics"]["cash_weight_pct"],
        r["holdings"],
        r["risks"],
        r["generated_at"].as_string(),
        r["locale"].as_string(),
    )


def _list_item(row) -> dict:
    id_, created_at, source, total_asset, holdings_count, flags, summary, cash_pct, holdings, risks, generated_at, locale = row
    fragment = {"metrics": {"cash_weight_pct": cash_pct}, "holdings": holdings, "risks": risks, "flags": flags,
                "generated_at": generated_at}
    return {
        **_history_item(id_, created_at, source, total_asset, holdings_count, flags, summary),
        "created_at_dt": created_at,
        "locale": locale or "ko-KR",
        **record_fields(fragment, holdings_count, locale or "ko-KR"),
    }


def list_diagnoses(
    user_id: int,
    limit: int,
    offset: int,
    sort: str = "created_at",
    order: str = "desc",
    *,
    terms: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[int, list[dict]]:
    """(조건에 맞는 user_id의 진단 수, 목록(기본 최신순)). 목록 항목에 필요한 JSON 조각만 읽는다.
    sort는 created_at | id | total_asset | holdings_count, 같은 값은 id가 방향을 따라 정한다.
    terms(검색 낱말)·start/end(UTC 이상/미만)는 2026-11-05에 추가한 선택 조건이고 total도 그 조건을 적용한 건수다.
    항목은 history_item 필드 + 기록 필드(record_fields) + created_at_dt다(API 기본 응답은 history_item 필드만 내보낸다)."""
    db = SessionLocal()
    try:
        conds = diagnosis_conditions(user_id, terms=terms, start=start, end=end)
        total = db.query(func.count(PortfolioDiagnosis.id)).filter(*conds).scalar()
        sort_columns = {
            "created_at": PortfolioDiagnosis.created_at,
            "id": PortfolioDiagnosis.id,
            "total_asset": PortfolioDiagnosis.total_asset,
            "holdings_count": PortfolioDiagnosis.holdings_count,
        }
        rows = db.execute(
            select(*_list_columns())
            .where(*conds)
            .order_by(*order_by_clauses(sort_columns[sort], PortfolioDiagnosis.id, order))
            .limit(limit)
            .offset(offset)
        ).all()
        return total, [_list_item(row) for row in rows]
    finally:
        db.close()


def diagnoses_by_ids(user_id: int, diagnosis_ids: list[int]) -> dict[int, dict]:
    """통합 검색 한 페이지의 진단 항목(list_diagnoses 항목과 같은 형태, id별). 남의 것은 소유자 조건으로 빠진다(쿼리 1개)."""
    if not diagnosis_ids:
        return {}
    db = SessionLocal()
    try:
        rows = db.execute(select(*_list_columns()).where(_owned(user_id), PortfolioDiagnosis.id.in_(diagnosis_ids))).all()
        return {item["id"]: item for item in map(_list_item, rows)}
    finally:
        db.close()


def get_diagnosis(user_id: int, diagnosis_id: int) -> dict | None:
    """user_id 소유의 진단 한 건(record). 없거나 남의 것이면 None(둘을 구분하지 않는다)."""
    db = SessionLocal()
    try:
        row = db.query(PortfolioDiagnosis).filter(_owned(user_id), PortfolioDiagnosis.id == diagnosis_id).one_or_none()
        return None if row is None else _record(row)
    finally:
        db.close()


def get_previous_diagnosis(user_id: int, record: dict) -> dict | None:
    """같은 사용자의, record 바로 이전(created_at, 같으면 id 기준) 진단. 없으면 None."""
    db = SessionLocal()
    try:
        row = (
            db.query(PortfolioDiagnosis)
            .filter(
                _owned(user_id),
                or_(
                    PortfolioDiagnosis.created_at < record["created_at"],
                    and_(PortfolioDiagnosis.created_at == record["created_at"], PortfolioDiagnosis.id < record["id"]),
                ),
            )
            .order_by(PortfolioDiagnosis.created_at.desc(), PortfolioDiagnosis.id.desc())
            .first()
        )
        return None if row is None else _record(row)
    finally:
        db.close()


def delete_diagnosis(user_id: int, diagnosis_id: int) -> bool:
    """user_id 소유의 진단 한 건을 삭제한다. 없거나 남의 것이면 False."""
    db = SessionLocal()
    try:
        deleted = db.execute(
            delete(PortfolioDiagnosis).where(_owned(user_id), PortfolioDiagnosis.id == diagnosis_id)
        ).rowcount
        db.commit()
        return deleted > 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
