"""포트폴리오 진단 이력(portfolio_diagnosis) 저장/조회와 두 진단의 비교.

구성(위에서부터 DB 없는 순수 함수 → DB):
- history_item / build_comparison: 이력 항목 요약, 두 진단의 비교. 코드로만 계산한다(LLM 없음)
- save_diagnosis / list_diagnoses / get_diagnosis / get_previous_diagnosis / delete_diagnosis: DB.
  모든 쿼리는 user_id가 일치하는 행만 대상으로 한다(남의 것과 없는 것을 구분하지 않는다). user_id에 기본값을 두지 않아
  소유자 조건을 빠뜨릴 수 없게 했다.

record(get_diagnosis가 돌려주는 dict): {"id", "created_at"(datetime), "source", "total_asset", "holdings_count",
"result"(저장된 진단 응답 본문)}.
"""

import logging

from sqlalchemy import and_, delete, func, or_, select

from app.db.session import SessionLocal
from app.diagnosis import _pct, _r2
from app.models import PortfolioDiagnosis

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
    }


def save_diagnosis(user_id: int, response: dict) -> dict | None:
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
                result=response,
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


def list_diagnoses(user_id: int, limit: int, offset: int) -> tuple[int, list[dict]]:
    """(user_id의 전체 진단 수, 최신순 목록). 목록 항목에 필요한 JSON 조각(flags, summary)만 읽는다."""
    db = SessionLocal()
    try:
        total = db.query(func.count(PortfolioDiagnosis.id)).filter(_owned(user_id)).scalar()
        rows = db.execute(
            select(
                PortfolioDiagnosis.id,
                PortfolioDiagnosis.created_at,
                PortfolioDiagnosis.source,
                PortfolioDiagnosis.total_asset,
                PortfolioDiagnosis.holdings_count,
                PortfolioDiagnosis.result["flags"],
                PortfolioDiagnosis.result["summary"].as_string(),
            )
            .where(_owned(user_id))
            .order_by(PortfolioDiagnosis.created_at.desc(), PortfolioDiagnosis.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return total, [_history_item(*row) for row in rows]
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
