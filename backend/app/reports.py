"""리서치 리포트(research_report)와 tool 호출 이력(tool_call_log)의 저장/조회.

스키마에는 답변/sources/소요시간 전용 컬럼이 없어서 다음처럼 매핑한다:
- research_report.content = 답변 전문, summary = 목록 미리보기용 앞부분(SUMMARY_LEN자)
- research_report.company_id = 이번 질문에서 다룬 종목이 정확히 1개일 때만 채운다(여러 개면 NULL).
  종목 상세는 tool_call_log에 남는다.
- tool_call_log.arguments/result = 실제로 실행한 인자(보정 후)와 tool 결과 전체. sources는 이 result에서
  복원하므로 research_report에 별도 컬럼이 필요 없다. 소요시간은 result의 `_elapsed_ms` 메타 키에 넣는다.
"""

import json
import logging

from sqlalchemy import func

from app.db.session import SessionLocal
from app.models import Company, ResearchReport, ToolCallLog
from app.sources import build_sources

logger = logging.getLogger(__name__)

SUMMARY_LEN = 200


def _jsonable(value):
    """JSONB에 넣을 수 있게 정리한다(직렬화되지 않는 값은 문자열로)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _summary(answer: str) -> str:
    s = " ".join(answer.split())
    return s if len(s) <= SUMMARY_LEN else s[: SUMMARY_LEN - 1] + "…"


def save_report(question: str, answer: str, tool_records: list[dict]) -> int | None:
    """질문/답변과 tool 실행 이력을 한 트랜잭션으로 저장하고 report_id를 돌려준다.

    저장 중 어떤 오류가 나도 예외를 밖으로 내지 않는다(답변 생성 흐름을 막지 않기 위해).
    실패하면 로그만 남기고 None을 돌려준다."""
    try:
        db = SessionLocal()
        try:
            names = {
                r["result"].get("company_name") for r in tool_records if isinstance(r["result"], dict)
            } - {None, ""}
            company_id = None
            if names:
                ids = [i for (i,) in db.query(Company.id).filter(Company.name.in_(names))]
                company_id = ids[0] if len(ids) == 1 else None

            report = ResearchReport(
                company_id=company_id, question=question, summary=_summary(answer), content=answer
            )
            db.add(report)
            db.flush()
            report_id = report.id

            for r in tool_records:
                result = _jsonable(r["result"])
                if isinstance(result, dict):
                    result["_elapsed_ms"] = r["elapsed_ms"]
                db.add(
                    ToolCallLog(
                        report_id=report_id,
                        tool_name=r["tool_name"][:50],
                        arguments=_jsonable(r["arguments"]),
                        result=result,
                        called_at=r["called_at"],
                    )
                )
            db.commit()
            return report_id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception:
        logger.exception("research_report 저장 실패 (답변은 정상 반환됨): question=%r", question[:80])
        return None


def get_report(report_id: int) -> dict | None:
    """저장된 리포트 한 건. 없으면 None. sources는 저장된 tool 결과에서 복원한다."""
    db = SessionLocal()
    try:
        report = db.get(ResearchReport, report_id)
        if report is None:
            return None
        logs = (
            db.query(ToolCallLog)
            .filter(ToolCallLog.report_id == report_id)
            .order_by(ToolCallLog.id)
            .all()
        )
        return {
            "report_id": report.id,
            "question": report.question,
            "answer": report.content or "",
            "summary": report.summary,
            "company_name": report.company.name if report.company else None,
            "created_at": report.created_at.isoformat(),
            "used_tools": [log.tool_name for log in logs],
            "sources": build_sources((log.tool_name, log.result) for log in logs),
        }
    finally:
        db.close()


def list_reports(limit: int = 20, offset: int = 0) -> tuple[int, list[dict]]:
    """(전체 리포트 수, 최근 리포트 목록(최신순)). 목록에는 sources를 빼고 미리보기(summary)만 담는다."""
    db = SessionLocal()
    try:
        total = db.query(func.count(ResearchReport.id)).scalar()
        rows = (
            db.query(ResearchReport, Company.name)
            .outerjoin(Company, Company.id == ResearchReport.company_id)
            .order_by(ResearchReport.created_at.desc(), ResearchReport.id.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        ids = [r.id for r, _ in rows]
        tools: dict[int, list[str]] = {}
        if ids:
            for report_id, tool_name in (
                db.query(ToolCallLog.report_id, ToolCallLog.tool_name)
                .filter(ToolCallLog.report_id.in_(ids))
                .order_by(ToolCallLog.id)
            ):
                tools.setdefault(report_id, []).append(tool_name)
        items = [
            {
                "report_id": r.id,
                "question": r.question,
                "summary": r.summary,
                "company_name": company_name,
                "created_at": r.created_at.isoformat(),
                "used_tools": tools.get(r.id, []),
            }
            for r, company_name in rows
        ]
        return total, items
    finally:
        db.close()
