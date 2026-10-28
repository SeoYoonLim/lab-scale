"""목록 API 공통: 페이징 메타(has_next, total_pages), 정렬 절, LIKE 검색어 이스케이프. 2026-10-28 (7주차 과제).

정렬 규칙: sort는 API마다 화이트리스트(Literal)로 받아 허용되지 않은 값은 FastAPI 기본 422다. 같은 값끼리의 순서가
페이지마다 달라지지 않도록 항상 id를 보조 키로 붙이고, id의 방향은 order를 따른다(기본 정렬이 예전과 같게 하려는 것).
"""

import math

from sqlalchemy import ColumnElement

# offset 상한: DB의 bigint 최대값. 이보다 크면 DB가 DataError를 내서 503이 되므로 검증 단계에서 422로 막는다.
MAX_OFFSET = 9223372036854775807


def page_meta(total: int, limit: int, offset: int) -> dict:
    """has_next: 이 페이지 뒤에 더 있는가. total_pages: limit 기준 올림(total이 0이면 0).
    offset이 limit의 배수가 아니어도 has_next는 (offset + limit < total)로 정확하다."""
    return {"has_next": offset + limit < total, "total_pages": math.ceil(total / limit) if total > 0 else 0}


def order_by_clauses(column: ColumnElement, id_column: ColumnElement, order: str) -> list:
    """(정렬 컬럼, id) 순서의 ORDER BY 절. id의 방향은 order를 따른다."""
    asc = order == "asc"
    clauses = [column.asc() if asc else column.desc()]
    if column is not id_column:
        clauses.append(id_column.asc() if asc else id_column.desc())
    return clauses


def escape_like(term: str) -> str:
    """LIKE 패턴에 넣을 검색어에서 와일드카드(%, _)와 이스케이프 문자(\\)를 리터럴로 만든다(escape="\\"와 함께 쓴다).
    따옴표는 바인딩 파라미터로 전달되므로 따로 처리할 필요가 없다."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
