"""37차(2026-11-08): 프런트엔드 연동 전 API 계약 점검(DB·외부 API 없음). 개발 DB로 실제 흐름을 보는 것은 tests/test_api_contract_round37_devdb.py.

- 인증이 필요한 모든 라우트는 토큰 없이 401 + {"detail": str}이고, OpenAPI에 401·오류 형식이 선언돼 있다.
- API.md 상단의 "인증 필요"/"공개" 표가 실제 라우트의 인증 의존성과 같다.
- 잘못된 쿼리·본문은 422(loc가 실제 위치), 처리되지 않은 오류·DB 장애는 고정 문구(내부 경로·접속 정보·Traceback 없음).
- look-through 요약에 음수 0(-0.0)이 나오지 않는다.
"""

import json
import math
import re
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app import look_through as lt
from app.api.deps import get_current_user
from app.main import SERVER_ERROR_DETAIL, _api_routes, _uses_dependency, app

client = TestClient(app, raise_server_exceptions=False)
API_MD = Path(__file__).resolve().parents[1] / "API.md"
SKIP_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
PATH_VALUES = {"ticker": "005930", "report_id": "1", "diagnosis_id": "1", "briefing_id": "1", "provider": "google"}


def routes():
    out = []
    for r in _api_routes(app.routes):
        if r.path in SKIP_PATHS:
            continue
        for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
            out.append((m, r.path, _uses_dependency(r.dependant, get_current_user)))
    return out


def concrete(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: PATH_VALUES[m.group(1)], path)


AUTH_ROUTES = [(m, p) for m, p, auth in routes() if auth]


def test_route_inventory_is_what_the_handoff_documents():
    """핸드오프 문서(docs/FRONTEND_INTEGRATION_HANDOFF.md)의 전체 목록과 같은 50개 경로·56개 메서드(인증 33개).
    API가 늘거나 줄면 이 숫자와 핸드오프 문서 3절 목록을 같이 고친다.
    41차(2026-11-10): 공개 GET 3개 추가(/api/market/overview, /api/stocks/{ticker}/news, /api/stocks/{ticker}/disclosures)."""
    rs = routes()
    assert len({p for _, p, _ in rs}) == 50 and len(rs) == 56
    assert len(AUTH_ROUTES) == 33


@pytest.mark.parametrize("method,path", AUTH_ROUTES)
def test_every_authenticated_route_returns_401_without_token(method, path):  # 2
    r = client.request(method, concrete(path), json={} if method in ("POST", "PUT", "DELETE") else None)
    assert r.status_code == 401, (method, path, r.status_code, r.text[:200])
    assert r.json() == {"detail": "인증이 필요합니다. 다시 로그인해주세요."}
    bad = client.request(method, concrete(path), headers={"Authorization": "Bearer not-a-token"},
                         json={} if method in ("POST", "PUT", "DELETE") else None)
    assert bad.status_code == 401


def test_openapi_declares_error_responses():
    spec = app.openapi()
    assert spec["components"]["schemas"]["ErrorResponse"]["required"] == ["detail"]
    for method, path in AUTH_ROUTES:
        responses = spec["paths"][path][method.lower()]["responses"]
        assert "401" in responses and responses["401"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse"), path
    ops = {(m.upper(), p): op for p, v in spec["paths"].items() for m, op in v.items()}
    assert {"400", "404", "429"} <= set(ops[("POST", "/api/portfolio/orders")]["responses"])  # 잔고 부족·없는 종목·주문 한도
    assert "409" in ops[("POST", "/api/briefings/daily/generate")]["responses"]  # 하루 버전 한도
    assert "404" in ops[("GET", "/api/briefings/daily/current")]["responses"]  # 오늘 브리핑 없음
    assert "429" in ops[("POST", "/api/auth/find-username")]["responses"]  # 도우미 함수 안의 레이트 리밋
    assert "401" not in ops[("GET", "/api/companies")]["responses"]  # 공개 API


def _md_row(label: str) -> set[tuple[str, str]]:
    """API.md 첫 표의 한 행에서 (경로, 메서드) 집합. `/x/{id}`(GET/DELETE) 형식과 메서드 없는 형식을 모두 읽는다."""
    line = next(l for l in API_MD.read_text(encoding="utf-8").splitlines() if l.startswith(f"| {label} |"))
    out = set()
    for path, methods in re.findall(r"`(/api/[^`]+)`(?:\(([A-Z/]+)[^)]*\))?", line):
        for m in (methods.split("/") if methods else ["*"]):
            out.add((_norm(path), m))
    return out


def _norm(path: str) -> str:
    return re.sub(r"\{(id|report_id|diagnosis_id|briefing_id)\}", "{*}", path)


def test_api_md_auth_table_matches_routes():
    """API.md 상단 표가 실제 인증 의존성과 같다(37차: exposure·daily 브리핑 5개·ETF holdings가 빠져 있었다)."""
    auth_doc, public_doc = _md_row("**인증 필요**"), _md_row("공개")
    doc_auth_paths = {p for p, _ in auth_doc}
    doc_public_paths = {p for p, _ in public_doc}
    for method, path, auth in routes():
        p = _norm(path)
        if auth:
            assert p in doc_auth_paths and ((p, method) in auth_doc or (p, "*") in auth_doc), (method, path)
        elif not path.startswith("/api/auth/oauth/{provider}"):  # OAuth 콜백 둘은 한 칸에 `/login`·`/callback`으로 적혀 있다
            assert p in doc_public_paths, (method, path)


@pytest.fixture
def user(login_as):
    return login_as(7, "contract")


@pytest.mark.parametrize("url", [
    "/api/companies?sort=name_desc", "/api/companies?limit=301", "/api/companies?offset=-1",
    "/api/portfolio/trades?sort=xxx", "/api/portfolio/trades?limit=201", "/api/portfolio/trades?order=up",
    "/api/research?limit=101", "/api/research?sort=title", "/api/research?analysis_type=crypto",
    "/api/portfolio/diagnosis/history?sort=score", "/api/briefings/daily/history?page=0", "/api/briefings/daily/history?size=101",
    "/api/portfolio/performance?period=2y", "/api/portfolio/performance?benchmark=SPX", "/api/portfolio/performance?view=chart",
    "/api/portfolio/exposure?view=sector", "/api/portfolio/exposure?limit=51", "/api/history/search?type=news",
    "/api/history/search?sort=created_at", "/api/discovery/trending?category=volume_top", "/api/stocks/005930/price-history?days=366",
    "/api/stocks/005930/news?limit=51", "/api/stocks/005930/news?order=newest", "/api/stocks/005930/news?date_from=2026-11-31",
    "/api/stocks/005930/news?date_from=2026-11-03&date_to=2026-11-01", "/api/stocks/005930/disclosures?correction=yes",
    "/api/stocks/005930/disclosures?offset=-1",
    "/api/etf/069500/holdings?limit=0", "/api/history/search?date_from=2026-13-01",
])
def test_invalid_query_parameters_are_422_with_query_location(user, url):  # 3, 19, 20
    r = client.get(url)
    assert r.status_code == 422, (url, r.status_code)
    detail = r.json()["detail"]
    assert isinstance(detail, list) and detail[0]["loc"][0] == "query"


def test_body_timezone_error_points_to_the_body(user):
    """POST /api/briefings/daily/generate의 timezone은 본문 필드다(37차: 예전에는 loc이 ["query", "timezone"])."""
    r = client.post("/api/briefings/daily/generate", json={"timezone": "Mars/Nowhere"})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "timezone"]


def test_order_body_validation(user):
    for body in ({"ticker": "005930", "side": "hold", "quantity": 1}, {"ticker": "005930", "side": "buy", "quantity": 0},
                 {"ticker": "", "side": "buy", "quantity": 1}):
        r = client.post("/api/portfolio/orders", json=body)
        assert r.status_code == 422 and r.json()["detail"][0]["loc"][0] == "body", body


LEAK_RE = re.compile(r"Traceback|File \"|C:\\\\|/home/|postgres(?:ql)?(?:\+psycopg)?://|password|SECRET|\.py\b", re.I)


def test_unhandled_errors_do_not_leak_internals(monkeypatch):
    import app.api.status as status_api

    def boom(db):
        raise RuntimeError(r'File "C:\project\lab-scale\backend\app\x.py" postgresql+psycopg://u:p@h/db JWT_SECRET=abc')

    monkeypatch.setattr(status_api, "build_status", boom)
    app.dependency_overrides[status_api.get_db] = lambda: None
    try:
        r = client.get("/api/status")
    finally:
        app.dependency_overrides.pop(status_api.get_db, None)
    assert r.status_code == 500 and r.json() == {"detail": SERVER_ERROR_DETAIL} and not LEAK_RE.search(r.text)


def test_db_errors_are_503_without_sql(monkeypatch):
    import app.api.status as status_api

    def down(db):
        raise OperationalError("SELECT * FROM users WHERE email = 'a@b.c'", {}, Exception("connection refused at 127.0.0.1:15432"))

    monkeypatch.setattr(status_api, "build_status", down)
    app.dependency_overrides[status_api.get_db] = lambda: None
    try:
        r = client.get("/api/status")
    finally:
        app.dependency_overrides.pop(status_api.get_db, None)
    assert r.status_code == 503 and "데이터베이스" in r.json()["detail"] and not LEAK_RE.search(r.text) and "15432" not in r.text


def test_look_through_summary_has_no_negative_zero():
    """37차: 보유가 없거나 정확히 100% 분해되면 over_allocated_amount가 -0.0이었다(화면 "-0원")."""
    out = lt.compute([], {}, 10_000_000.0, datetime(2026, 11, 6, 18, 0), date(2026, 11, 6))
    v = out["summary"]["over_allocated_amount"]
    assert v == 0.0 and math.copysign(1.0, v) == 1.0
    assert "-0.0" not in json.dumps(out)


def test_cors_exposes_total_count_header():
    """목록 전체 건수(X-Total-Count, GET /api/companies)를 브라우저가 읽을 수 있다."""
    r = client.options("/api/companies", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
    from app.config import cors_expose_headers

    assert "X-Total-Count" in cors_expose_headers()


def test_requests_http_is_blocked_in_tests():
    """conftest의 block_external_http: 테스트가 실수로 외부(네이버 실시간 시세 등)에 요청하지 않는다."""
    import requests

    with pytest.raises(requests.ConnectionError):
        requests.get("https://polling.finance.naver.com/api/realtime/domestic/stock/005930", timeout=1)


def test_frontend_types_file_is_current():
    """docs/frontend-api-types.ts가 지금 OpenAPI와 같다(API를 바꾸면 python -m scripts.export_api_types로 다시 만든다)."""
    from scripts import export_api_types as X

    assert X.OUT.read_text(encoding="utf-8").replace("\r\n", "\n") == X.build()
