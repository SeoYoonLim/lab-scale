"""내 기록 통합 검색: 리서치(research_report)와 포트폴리오 진단(portfolio_diagnosis)을 한 목록으로. 2026-11-05.

- 필터·정렬·페이지네이션은 DB에서 한다. 두 테이블의 (종류, id, created_at)만 UNION ALL로 모아 정렬하고 한 페이지만 자른 뒤,
  그 페이지의 행만 종류별로 한 번씩 읽는다(N+1 없음). 쿼리: 건수 2 + 페이지 1 + 리서치 3 + 진단 1 = 최대 7개.
- 건수(counts)는 목록과 따로 센다. 탭(전체·리서치·진단) 숫자를 한 번에 보여 줄 수 있게 type과 관계없이 둘 다 센다.
- 모든 조건은 소유자(user_id) 조건으로 시작한다(app.reports.research_conditions, app.diagnosis_history.diagnosis_conditions).
- 정렬: created_at, 같으면 종류, 같으면 id(모두 order 방향). 페이지를 넘겨도 중복·누락이 없다.
- 읽기만 한다. 어떤 행도 고치지 않는다.
"""

from datetime import datetime

from sqlalchemy import func, literal, select, union_all

from app.db.session import SessionLocal
from app.diagnosis_history import diagnoses_by_ids, diagnosis_conditions
from app.models import PortfolioDiagnosis, ResearchReport
from app.reports import reports_by_ids, research_conditions

KINDS = ("research", "diagnosis")


def search(
    user_id: int,
    *,
    kind: str = "all",
    terms: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
    order: str = "desc",
) -> dict:
    """{"counts": {"research", "diagnosis"}, "total": 이 kind의 건수, "items": [(kind, 항목 dict), ...]}.
    항목은 리서치면 app.reports 목록 항목, 진단이면 app.diagnosis_history 목록 항목이다."""
    r_conds = research_conditions(user_id, terms=terms, start=start, end=end)
    d_conds = diagnosis_conditions(user_id, terms=terms, start=start, end=end)
    wanted = KINDS if kind == "all" else (kind,)
    db = SessionLocal()
    try:
        counts = {
            "research": db.execute(select(func.count(ResearchReport.id)).where(*r_conds)).scalar(),
            "diagnosis": db.execute(select(func.count(PortfolioDiagnosis.id)).where(*d_conds)).scalar(),
        }
        parts = []
        if "research" in wanted:
            parts.append(select(literal("research").label("kind"), ResearchReport.id.label("id"),
                                ResearchReport.created_at.label("created_at")).where(*r_conds))
        if "diagnosis" in wanted:
            parts.append(select(literal("diagnosis").label("kind"), PortfolioDiagnosis.id.label("id"),
                                PortfolioDiagnosis.created_at.label("created_at")).where(*d_conds))
        u = (union_all(*parts) if len(parts) > 1 else parts[0]).subquery()
        keys = [u.c.created_at, u.c.kind, u.c.id]
        page = db.execute(
            select(u.c.kind, u.c.id)
            .order_by(*(k.asc() if order == "asc" else k.desc() for k in keys))
            .limit(limit)
            .offset(offset)
        ).all()
    finally:
        db.close()

    research = reports_by_ids(user_id, [i for k, i in page if k == "research"])
    diagnosis = diagnoses_by_ids(user_id, [i for k, i in page if k == "diagnosis"])
    by_kind = {"research": research, "diagnosis": diagnosis}
    items = [(k, by_kind[k][i]) for k, i in page if i in by_kind[k]]
    return {"counts": counts, "total": sum(counts[k] for k in wanted), "items": items}
