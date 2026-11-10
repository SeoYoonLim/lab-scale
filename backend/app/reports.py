"""리서치 리포트(research_report)와 tool 호출 이력(tool_call_log)의 저장/조회.

스키마에는 답변/sources/소요시간 전용 컬럼이 없어서 다음처럼 매핑한다:
- research_report.content = 답변 전문, summary = 목록 미리보기용 앞부분(SUMMARY_LEN자)
- research_report.company_id = 이번 질문에서 다룬 종목이 정확히 1개일 때만 채운다(여러 개면 NULL).
  종목 상세는 tool_call_log에 남는다.
- tool_call_log.arguments/result = 실제로 실행한 인자(보정 후)와 tool 결과 전체. sources는 이 result에서
  복원하므로 research_report에 별도 컬럼이 필요 없다. 소요시간은 result의 `_elapsed_ms` 메타 키에 넣는다.
- research_report.user_id = 리포트 소유자. 조회/목록/삭제는 user_id가 정확히 일치하는 행만 대상으로 한다
  (user_id=None이면 소유자 없는 행, 즉 로그인 도입 전 리포트만). 기본값을 두지 않아 소유자 조건을 빠뜨릴 수 없게 했다.

기록 관리(2026-11-05): 목록·상세의 제목·종목 목록·분석 유형·출처 수·기준일은 저장된 행과 tool 이력에서 읽기만 해서 만든다.
조회는 어떤 행도 고치지 않는다(과거 답변을 현재 시세·뉴스로 다시 만들지 않는다).
"""

import json
import logging
from datetime import datetime

from sqlalchemy import and_, exists, func, or_, select

from app.db.session import SessionLocal
from app.history_terms import term_variants
from app.models import Company, ResearchReport, ToolCallLog
from app.pagination import escape_like, order_by_clauses
from app.sources import build_sources

logger = logging.getLogger(__name__)

SUMMARY_LEN = 200
TITLE_LEN = 60

# 분석 유형(2026-11-05). 저장된 tool 호출 이력에서 코드로 정한다(LLM 없음). 한 리포트가 여러 유형일 수 있고,
# analysis_type은 이 순서에서 처음 걸린 유형이다. 아는 tool을 하나도 쓰지 않은 답(개념 설명 등)은 general.
# - outlook: 전망 경로(stock_tool 결과의 called_via가 forecast로 시작), etf: ETF 종목을 조회한 결과가 있음
ANALYSIS_TYPES = ("outlook", "event", "briefing", "discovery", "etf", "disclosure", "news", "price", "market", "general")
_TOOL_TYPES = {
    "event_reaction_tool": "event",
    "briefing": "briefing",
    "discovery_tool": "discovery",
    "disclosure_tool": "disclosure",
    "news_tool": "news",
    "rag_search_tool": "news",
    "stock_tool": "price",
    "market_tool": "market",
    "fx_tool": "market",
}
# 상세의 data_basis에 옮기는 기준일 키(tool 결과의 최상위와 indicators/data 한 단계 안)
_BASIS_KEYS = ("period_start_date", "period_end_date", "base_date", "latest_date", "data_date", "price_as_of", "as_of")
# 목록의 출처 수를 셀 때 tool 결과에서 읽는 부분(시세 목록처럼 큰 값은 읽지 않는다)
_SOURCE_KEYS = ("news", "disclosures", "events", "results")


def _jsonable(value):
    """JSONB에 넣을 수 있게 정리한다(직렬화되지 않는 값은 문자열로)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _summary(answer: str) -> str:
    s = " ".join(answer.split())
    return s if len(s) <= SUMMARY_LEN else s[: SUMMARY_LEN - 1] + "…"


def _title(question: str) -> str:
    """목록 제목: 질문을 공백 정리해 앞 TITLE_LEN자. 리포트에 제목 컬럼이 없어서 질문에서 만든다."""
    s = " ".join((question or "").split())
    return s if len(s) <= TITLE_LEN else s[: TITLE_LEN - 1] + "…"


def _owned_by(user_id: int | None):
    return ResearchReport.user_id.is_(None) if user_id is None else ResearchReport.user_id == user_id


def save_report(
    question: str,
    answer: str,
    tool_records: list[dict],
    previous_report_id: int | None = None,
    user_id: int | None = None,
    locale: str | None = None,
    original_content: str | None = None,
    translation_status: str | None = None,
) -> int | None:
    """질문/답변과 tool 실행 이력을 한 트랜잭션으로 저장하고 report_id를 돌려준다.

    locale·original_content·translation_status(2026-11-05)는 ko-KR이 아닌 답변일 때만 넘긴다: answer는 사용자에게 보인 그 언어의
    답변, original_content는 번역 전 한국어 답변이다. 넘기지 않으면 locale은 DB 기본값 ko-KR이다.

    previous_report_id는 후속 질문이 이어받은 직전 보고서다(없으면 None). 같은 사용자의 리포트인지는
    호출측(라우터)이 get_report(..., user_id=...)로 먼저 확인한다. user_id는 리포트 소유자(없으면 None).
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
                company_id=company_id,
                previous_report_id=previous_report_id,
                user_id=user_id,
                question=question,
                summary=_summary(answer),
                content=answer,
                **({"locale": locale, "original_content": original_content, "translation_status": translation_status}
                   if locale else {}),
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
    except Exception as e:  # noqa: BLE001 - 저장 실패는 답변을 막지 않는다
        # 질문 원문은 개인정보일 수 있어 남기지 않는다(길이와 예외 종류만, 2026-10-30)
        logger.error("research_report 저장 실패 (답변은 정상 반환됨): question_len=%s error=%s", len(question), type(e).__name__)
        return None


# ---------- 분석 유형·종목·출처 (저장된 tool 이력에서 읽기만) ----------


def analysis_types(tool_rows) -> list[str]:
    """[(tool_name, called_via, asset_type), ...] → ANALYSIS_TYPES 순서의 유형 목록(없으면 ["general"])."""
    found = set()
    for name, called_via, asset_type in tool_rows:
        if name == "stock_tool" and (called_via or "").startswith("forecast"):
            found.add("outlook")
        if asset_type == "etf":
            found.add("etf")
        if name in _TOOL_TYPES:
            found.add(_TOOL_TYPES[name])
    return [t for t in ANALYSIS_TYPES if t in found] or ["general"]


def _type_condition(analysis_type: str):
    """리포트가 그 분석 유형인가(analysis_types와 같은 규칙의 SQL)."""
    log = ToolCallLog.report_id == ResearchReport.id
    if analysis_type == "general":
        known = or_(ToolCallLog.tool_name.in_(list(_TOOL_TYPES)), ToolCallLog.result["asset_type"].as_string() == "etf")
        return ~exists().where(log, known)
    if analysis_type == "outlook":
        cond = and_(ToolCallLog.tool_name == "stock_tool", ToolCallLog.result["called_via"].as_string().like("forecast%"))
    elif analysis_type == "etf":
        cond = ToolCallLog.result["asset_type"].as_string() == "etf"
    else:
        cond = ToolCallLog.tool_name.in_([n for n, t in _TOOL_TYPES.items() if t == analysis_type])
    return exists().where(log, cond)


def _created_between(start: datetime | None, end: datetime | None) -> list:
    conds = []
    if start is not None:
        conds.append(ResearchReport.created_at >= start)
    if end is not None:
        conds.append(ResearchReport.created_at < end)
    return conds


def _term_condition(term: str):
    """통합 검색 낱말 하나: 질문·요약·답변 전문·리포트 종목(이름·코드)·tool 결과의 종목(이름·코드) 중 하나에 들어 있음.
    별칭이면 등록명으로도 찾고, "ETF"가 들어간 낱말은 ETF를 조회한 리포트도 찾는다(app/history_terms)."""
    conds = []
    for variant in term_variants(term):
        pattern = f"%{escape_like(variant)}%"

        def like(column):
            return column.ilike(pattern, escape="\\")

        conds += [
            like(ResearchReport.question),
            like(ResearchReport.summary),
            like(ResearchReport.content),
            ResearchReport.company_id.in_(select(Company.id).where(or_(like(Company.name), like(Company.ticker)))),
            exists().where(
                ToolCallLog.report_id == ResearchReport.id,
                or_(like(ToolCallLog.result["company_name"].as_string()), like(ToolCallLog.result["ticker"].as_string())),
            ),
        ]
    if "etf" in term.casefold():
        conds.append(_type_condition("etf"))
    return or_(*conds)


def research_conditions(
    user_id: int,
    *,
    terms: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    analysis_type: str | None = None,
) -> list:
    """통합 검색(app/history_search)의 리서치 쪽 WHERE 조건. 항상 소유자 조건으로 시작한다."""
    conds = [_owned_by(user_id), *_created_between(start, end)]
    conds += [_term_condition(t) for t in terms or []]
    if analysis_type:
        conds.append(_type_condition(analysis_type))
    return conds


def _tool_meta(db, report_ids: list[int]) -> dict[int, list[tuple]]:
    """리포트별 tool 이력 요약(호출 순서대로, 쿼리 1개). 시세 목록 같은 큰 값은 읽지 않는다.
    각 항목: (tool_name, called_via, asset_type, company_name, ticker, 출처 계산용 dict)."""
    if not report_ids:
        return {}
    r = ToolCallLog.result
    rows = db.execute(
        select(
            ToolCallLog.report_id,
            ToolCallLog.tool_name,
            r["called_via"].as_string(),
            r["asset_type"].as_string(),
            r["company_name"].as_string(),
            r["ticker"].as_string(),
            r["found"],
            *(r[k] for k in _SOURCE_KEYS),
        )
        .where(ToolCallLog.report_id.in_(report_ids))
        .order_by(ToolCallLog.id)
    ).all()
    out: dict[int, list[tuple]] = {}
    for report_id, name, called_via, asset_type, company_name, ticker, found, *docs in rows:
        mini = {"found": found, "company_name": company_name, **{k: v for k, v in zip(_SOURCE_KEYS, docs) if v is not None}}
        out.setdefault(report_id, []).append((name, called_via, asset_type, company_name, ticker, mini))
    return out


def _tickers_by_name(db, metas: list[tuple]) -> dict[str, str]:
    """tool 결과에 종목명만 있고 코드가 없을 때(news_tool 등) 쓸 종목명 → 종목코드(쿼리 1개)."""
    names = {m[3] for m in metas} - {None, ""}
    if not names:
        return {}
    return dict(db.execute(select(Company.name, Company.ticker).where(Company.name.in_(names))).all())


def _companies(report_company: tuple[str, str] | None, metas: list[tuple], tickers_by_name: dict[str, str]) -> tuple[list, list]:
    """(종목명 목록, 종목코드 목록). 리포트에 연결된 종목이 먼저, 그다음 tool 결과에 나온 순서. 중복 없음."""
    names: list[str] = []
    tickers: list[str] = []

    def add(name, ticker):
        if name and name not in names:
            names.append(name)
        ticker = ticker or tickers_by_name.get(name)
        if ticker and ticker not in tickers:
            tickers.append(ticker)

    if report_company:
        add(*report_company)
    for m in metas:
        add(m[3], m[4])
    return names, tickers


def _extra_fields(report: ResearchReport, report_company: tuple[str, str] | None, metas: list[tuple],
                  tickers_by_name: dict[str, str], source_count: int) -> dict:
    names, tickers = _companies(report_company, metas, tickers_by_name)
    types = analysis_types((m[0], m[1], m[2]) for m in metas)
    return {
        "title": _title(report.question),
        "company_names": names,
        "tickers": tickers,
        "analysis_type": types[0],
        "analysis_types": types,
        "source_count": source_count,
    }


def _data_basis(logs) -> list[dict]:
    """상세용: tool 결과에 남아 있는 기준일(시세 구간·기준일 등)을 tool 순서대로. 저장된 값을 그대로 옮긴다."""
    out = []
    for log in logs:
        result = log.result
        if not isinstance(result, dict):
            continue
        dates = {k: result[k] for k in _BASIS_KEYS if isinstance(result.get(k), str)}
        for inner in ("indicators", "data"):
            if isinstance(result.get(inner), dict):
                for k, v in result[inner].items():
                    if k in _BASIS_KEYS and isinstance(v, str) and k not in dates:
                        dates[k] = v
        if dates:
            out.append({"tool": log.tool_name, "company_name": result.get("company_name"), "ticker": result.get("ticker"), **dates})
    return out


# ---------- 조회 ----------


def _find_owned(db, report_id: int, user_id: int | None) -> ResearchReport | None:
    return db.query(ResearchReport).filter(ResearchReport.id == report_id, _owned_by(user_id)).one_or_none()


def get_report(report_id: int, *, user_id: int | None) -> dict | None:
    """user_id 소유의 리포트 한 건. 없거나 남의 것이면 None(둘을 구분하지 않는다). sources는 저장된 tool 결과에서 복원한다."""
    db = SessionLocal()
    try:
        report = _find_owned(db, report_id, user_id)
        if report is None:
            return None
        logs = (
            db.query(ToolCallLog)
            .filter(ToolCallLog.report_id == report_id)
            .order_by(ToolCallLog.id)
            .all()
        )
        sources = build_sources((log.tool_name, log.result) for log in logs)
        metas = [
            (log.tool_name, *((log.result or {}).get(k) if isinstance(log.result, dict) else None
                              for k in ("called_via", "asset_type", "company_name", "ticker")), None)
            for log in logs
        ]
        company = (report.company.name, report.company.ticker) if report.company else None
        return {
            "report_id": report.id,
            "previous_report_id": report.previous_report_id,
            "question": report.question,
            "answer": report.content or "",
            "summary": report.summary,
            "company_name": report.company.name if report.company else None,
            "created_at": report.created_at.isoformat(),
            "created_at_dt": report.created_at,
            "used_tools": [log.tool_name for log in logs],
            "sources": sources,
            **_extra_fields(report, company, metas, _tickers_by_name(db, metas), len(sources)),
            "data_basis": _data_basis(logs),
            "locale": report.locale,
            "translation_status": report.translation_status,
            # ko-KR이 아닌 기록의 번역 전 한국어 답변(후속 질문의 맥락으로 쓴다). ko-KR 기록은 None
            "original_answer": report.original_content,
        }
    finally:
        db.close()


def delete_report(report_id: int, *, user_id: int | None) -> bool:
    """user_id 소유의 리포트 한 건을 삭제한다. 없거나 남의 것이면 False.
    tool_call_log는 FK ON DELETE CASCADE(및 ORM cascade)로 함께 지워진다."""
    db = SessionLocal()
    try:
        report = _find_owned(db, report_id, user_id)
        if report is None:
            return False
        db.delete(report)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _list_items(db, rows: list[tuple]) -> list[dict]:
    """[(ResearchReport, 종목명, 종목코드), ...] → 목록 항목(행 순서 유지). 추가 쿼리 2개(tool 이력 요약, 종목코드)."""
    metas_by_report = _tool_meta(db, [r.id for r, _, _ in rows])
    tickers_by_name = _tickers_by_name(db, [m for metas in metas_by_report.values() for m in metas])
    items = []
    for r, company_name, ticker in rows:
        metas = metas_by_report.get(r.id, [])
        items.append({
            "report_id": r.id,
            "previous_report_id": r.previous_report_id,
            "question": r.question,
            "summary": r.summary,
            "company_name": company_name,
            "created_at": r.created_at.isoformat(),
            "created_at_dt": r.created_at,
            "used_tools": [m[0] for m in metas],
            "locale": r.locale,
            **_extra_fields(r, (company_name, ticker) if company_name else None, metas, tickers_by_name,
                            len(build_sources((m[0], m[5]) for m in metas))),
        })
    return items


def list_reports(
    *,
    user_id: int | None,
    limit: int = 20,
    offset: int = 0,
    sort: str = "created_at",
    order: str = "desc",
    q: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    analysis_type: str | None = None,
) -> tuple[int, list[dict]]:
    """(조건에 맞는 user_id의 리포트 수, 리포트 목록). 기본 정렬은 최신순(created_at desc, id desc).
    목록에는 sources를 빼고 미리보기(summary)만 담는다.

    sort는 created_at | id, 같은 값은 id가 방향을 따라 정한다.
    q(공백만이면 무시)는 질문·요약·종목명·종목코드에서 대소문자 무시 부분 일치로 찾는다. %, _, 백슬래시는 리터럴이다.
    start/end(UTC, 이상/미만)와 analysis_type(ANALYSIS_TYPES)은 2026-11-05에 추가한 선택 조건이다.
    total도 이 조건을 모두 적용한 건수다(조건이 없으면 내 전체 리포트 수)."""
    db = SessionLocal()
    try:
        base = db.query(ResearchReport).outerjoin(Company, Company.id == ResearchReport.company_id).filter(_owned_by(user_id))
        term = (q or "").strip()
        if term:
            pattern = f"%{escape_like(term)}%"
            base = base.filter(
                or_(
                    ResearchReport.question.ilike(pattern, escape="\\"),
                    ResearchReport.summary.ilike(pattern, escape="\\"),
                    Company.name.ilike(pattern, escape="\\"),
                    Company.ticker.ilike(pattern, escape="\\"),
                )
            )
        base = base.filter(*_created_between(start, end))
        if analysis_type:
            base = base.filter(_type_condition(analysis_type))
        total = base.with_entities(func.count(ResearchReport.id)).scalar()
        sort_columns = {"created_at": ResearchReport.created_at, "id": ResearchReport.id}
        rows = (
            base.with_entities(ResearchReport, Company.name, Company.ticker)
            .order_by(*order_by_clauses(sort_columns[sort], ResearchReport.id, order))
            .limit(limit)
            .offset(offset)
            .all()
        )
        return total, _list_items(db, rows)
    finally:
        db.close()


def reports_by_ids(user_id: int, report_ids: list[int]) -> dict[int, dict]:
    """통합 검색 한 페이지의 리서치 항목(목록 항목과 같은 형태, report_id별). 남의 것은 소유자 조건으로 빠진다(쿼리 3개)."""
    if not report_ids:
        return {}
    db = SessionLocal()
    try:
        rows = (
            db.query(ResearchReport, Company.name, Company.ticker)
            .outerjoin(Company, Company.id == ResearchReport.company_id)
            .filter(_owned_by(user_id), ResearchReport.id.in_(report_ids))
            .all()
        )
        return {item["report_id"]: item for item in _list_items(db, rows)}
    finally:
        db.close()
