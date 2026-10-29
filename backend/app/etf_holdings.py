"""ETF 구성종목·비중(운용사 공시 PDF) 순수 로직: 응답 파싱, 비중 합계 검증, 기준일 stale 판정, 채팅 답변 템플릿(DB·네트워크 없음).

출처(2026-10-29 조사로 채택): 운용사가 자기 홈페이지에 공개하는 구성종목 PDF(Portfolio Deposit File)다.
- 삼성자산운용 KODEX: GET https://www.samsungfund.com/api/v1/kodex/product-pdf/{fId}.do?gijunYMD=YYYY.MM.DD (JSON, 응답의 gijunYMD가 기준일.
  요청한 날짜에 자료가 없으면 가장 가까운 이전 영업일로 답한다). fId는 GET /api/v1/kodex/product.do?pageNo=N의 stkTicker(종목코드)로 찾는다.
- 미래에셋자산운용 TIGER: pdf.ajax의 fixDate가 최신 기준일, pdfListAjax.ajax?ksdFund={ISIN}&fixDate=...&listCnt=2000이 표(HTML).
  ISIN은 종목코드에서 계산한다(KR7 + 코드 6자리 + 00 + 체크숫자).
채택하지 않은 소스: KRX 정보데이터시스템·pykrx(로그인 필요), 네이버 금융(robots.txt가 전부 금지, 상위 10개만, 해외 종목 비중 없음).
이 모듈의 SOURCES에 없는 운용사(RISE·ACE·SOL·HANARO·PLUS·TIME)의 ETF는 데이터가 없고, 그 경우 기존 "제공하지 않음" 안내를 유지한다.

저장 규칙: 현금성 자산(예금·설정현금액 등)과 비중이 없는 행은 저장하지 않는다. 그래서 비중 합계는 "현금성 제외 구성종목 비중의 합"이다.
KODEX는 운용사가 현금성 자산을 뺀 비중을 주고(합계 100%), TIGER는 현금 행에도 비중이 있어 이 모듈이 뺀다.
레버리지 ETF는 선물을 포함해 합계가 약 200%이고, 인버스 ETF의 선물 비중은 매도 포지션의 크기라 부호 없이 공시된다.
커버드콜 ETF의 매도 콜옵션과 TRS(스왑) 같은 파생 포지션은 음수 비중으로 공시된다(2026-10-29 수집분에서 7건, 모두 -0.14% 이상).
"""

import re
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup

from app.data_status import is_stale
from app.disclaimer import DISCLAIMER
from app.etf import leverage_kind, leverage_notice, mask_etf_names
from app.korean import josa

KODEX = "samsung_kodex"
TIGER = "mirae_tiger"
SOURCES = {
    KODEX: "삼성자산운용 KODEX 홈페이지의 구성종목(PDF)",
    TIGER: "미래에셋자산운용 TIGER ETF 홈페이지의 구성종목(PDF)",
}
TOP_N_CHAT = 10
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
WEIGHT_TOLERANCE_PCT = 5.0  # 합계가 기대값(보통 100, 레버리지 200)에서 이만큼 넘게 벗어나면 data_warnings
MAX_CHAT_ETFS = 3

_CASH_CODE_RE = re.compile(r"^(?:KRD|CASH)", re.IGNORECASE)
_CASH_NAME_RE = re.compile(r"(?:현금|예금)\s*$|^(?:KRW|USD)\s*(?:CASH|DEPOSIT)", re.IGNORECASE)

MSG_NO_DATA = "이 ETF의 구성종목·비중 데이터는 수집하지 않았습니다. 현재 삼성 KODEX와 미래에셋 TIGER ETF만 운용사 공시 PDF에서 수집합니다."
MSG_NOT_COLLECTED = "이 ETF의 구성종목·비중 데이터는 아직 수집되지 않았습니다(운용사 공시 PDF 수집 전)."
FRESHNESS_NOTE = "구성종목과 비중은 운용사가 영업일마다 공시하며, 이 서비스는 수동으로 수집해 반영이 늦을 수 있습니다."


@dataclass(frozen=True)
class Holding:
    name: str
    code: str | None
    weight_pct: float


@dataclass
class Snapshot:
    source: str
    as_of: date
    holdings: list[Holding]
    raw_rows: int = 0  # 운용사 표의 전체 행 수
    skipped_cash: int = 0
    skipped_no_weight: int = 0
    merged_duplicates: int = 0
    notes: list[str] = field(default_factory=list)


class ParseError(ValueError):
    """응답 형식이 예상과 달라 기준일이나 구성종목을 읽을 수 없다(운용사 페이지가 바뀌었을 수 있다). 메시지에 응답 본문은 넣지 않는다."""


def not_etf_message(name: str) -> str:
    return f"{josa(name, '은/는')} ETF가 아니라 구성종목 비중을 제공하지 않습니다."


def issuer_of(name: str) -> str | None:
    """ETF 이름 앞머리로 본 수집 출처. KODEX/TIGER가 아니면 None(수집 대상 아님)."""
    head = (name or "").strip().upper()
    if head.startswith("KODEX"):
        return KODEX
    if head.startswith("TIGER"):
        return TIGER
    return None


def issuer_supported(name: str) -> bool:
    return issuer_of(name) is not None


def is_cash(name: str, code: str | None) -> bool:
    return bool(_CASH_CODE_RE.match(code or "") or _CASH_NAME_RE.search(name or ""))


def _clean_code(raw) -> str | None:
    """운용사가 코드 칸에 "-"나 빈 값을 주면 코드 없음(None)."""
    code = (str(raw) if raw is not None else "").strip()
    return None if code in ("", "-") else code


def _to_float(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).replace(",", "").replace("%", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _finish(source: str, as_of: date, rows: list[tuple[str, str | None, float | None]]) -> Snapshot:
    """(이름, 코드, 비중) 행들에서 현금성·비중 없음을 빼고, 같은 (이름, 코드)는 비중을 합쳐 Snapshot을 만든다."""
    snap = Snapshot(source=source, as_of=as_of, holdings=[], raw_rows=len(rows))
    merged: dict[tuple[str, str | None], float] = {}
    for name, code, weight in rows:
        if is_cash(name, code):
            snap.skipped_cash += 1
        elif weight is None:
            snap.skipped_no_weight += 1
        else:
            key = (name, code)
            if key in merged:
                snap.merged_duplicates += 1
            merged[key] = round(merged.get(key, 0.0) + weight, 4)
    snap.holdings = sorted((Holding(n, c, w) for (n, c), w in merged.items()), key=lambda h: (-h.weight_pct, h.name))
    return snap


# ---------- 삼성 KODEX ----------


def parse_kodex_pdf(payload: dict) -> Snapshot:
    """product-pdf JSON → Snapshot. 기준일은 응답의 pdf.gijunYMD(YYYYMMDD)다."""
    try:
        pdf = payload["pdf"]
        as_of = date(int(pdf["gijunYMD"][:4]), int(pdf["gijunYMD"][4:6]), int(pdf["gijunYMD"][6:8]))
        items = pdf["list"]
        if not isinstance(items, list):
            raise TypeError("list")
        rows = [((r.get("secNm") or "").strip(), _clean_code(r.get("itmNo")), _to_float(r.get("ratio"))) for r in items]
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ParseError(f"KODEX 구성종목 응답 형식이 예상과 다릅니다({type(e).__name__})") from None
    return _finish(KODEX, as_of, [r for r in rows if r[0]])


def kodex_list_page(payload) -> list[dict]:
    """product.do 목록 응답(리스트 또는 {"list": [...]})에서 [{ticker, fid, name}]을 뽑는다."""
    items = payload if isinstance(payload, list) else (payload or {}).get("list")
    if not isinstance(items, list):
        raise ParseError("KODEX 상품 목록 응답 형식이 예상과 다릅니다")
    return [{"ticker": x["stkTicker"], "fid": x["fId"], "name": x.get("fNm")} for x in items if x.get("stkTicker") and x.get("fId")]


# ---------- 미래에셋 TIGER ----------


def tiger_isin(code: str) -> str:
    """종목코드(6자리, 영문 포함 가능) → ISIN. KR7 + 코드 + 00 + ISO 6166 체크숫자(Luhn)."""
    body = "KR7" + code + "00"
    digits = "".join(str(int(c, 36)) for c in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return body + str((10 - total % 10) % 10)


def parse_tiger_as_of(shell_html: str) -> date:
    """pdf.ajax 응답의 기준일(input name=fixDate, YYYY.MM.DD). 서버가 자료가 있는 가장 최근 영업일로 채운다."""
    soup = BeautifulSoup(shell_html or "", "lxml")
    tag = soup.find("input", attrs={"name": "fixDate"})
    value = (tag.get("value") if tag else "") or ""
    m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", value.strip())
    if not m:
        raise ParseError("TIGER 구성종목 기준일(fixDate)을 찾지 못했습니다")
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        raise ParseError("TIGER 구성종목 기준일 형식이 올바르지 않습니다") from None


def parse_tiger_pdf(as_of: date, list_html: str) -> Snapshot:
    """pdfListAjax.ajax 응답(<tr><td>코드·이름·수량·평가금액·비중·1주 수익률)을 Snapshot으로 바꾼다."""
    soup = BeautifulSoup(list_html or "", "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 5:
            continue
        rows.append((tds[1], _clean_code(tds[0]), _to_float(tds[4])))
    if not rows:
        raise ParseError("TIGER 구성종목 표에서 행을 찾지 못했습니다")
    declared = re.search(r'data-tot-cnt="(\d+)"', list_html or "")
    snap = _finish(TIGER, as_of, rows)
    if declared and int(declared.group(1)) != len(rows):
        snap.notes.append(f"표의 전체 건수({declared.group(1)})와 읽은 행 수({len(rows)})가 다릅니다.")
    return snap


# ---------- 검증 ----------


def total_weight(holdings: list[Holding]) -> float:
    return round(sum(h.weight_pct for h in holdings), 2)


def expected_total(name: str) -> float:
    """이름으로 본 비중 합계의 기대값: 레버리지·인버스 레버리지(2배)는 200, 그 밖은 100."""
    return 200.0 if leverage_kind(name) in ("레버리지", "인버스 레버리지") else 100.0


def weight_warnings(name: str, total: float | None, count: int) -> list[dict]:
    """비중 합계가 기대값에서 크게 벗어나면 경고. 데이터가 없으면 경고 없음(message로 따로 알린다)."""
    if not count or total is None:
        return []
    expected = expected_total(name)
    if abs(total - expected) > WEIGHT_TOLERANCE_PCT:
        return [{
            "kind": "weight_sum",
            "message": f"구성종목 비중 합계가 {total:.2f}%로 기대값({expected:.0f}%)과 {abs(total - expected):.1f}%p 다릅니다. "
                       "현금성 자산을 뺀 값이거나 일부 종목이 빠졌을 수 있습니다.",
        }]
    return []


def stale_info(as_of: date | None, today: date) -> tuple[bool, str | None]:
    """기준일이 최근 영업일보다 2영업일 이상 뒤처지면 stale(app.data_status와 같은 규칙)."""
    if as_of is None:
        return False, None
    stale = is_stale(as_of, today)
    return stale, (f"마지막으로 수집한 구성종목 기준일은 {as_of.isoformat()}입니다." if stale else None)


# ---------- 채팅 답변 ----------


_TOP_N_RE = re.compile(r"(?:상위|톱|top)\s*(\d{1,3})|(\d{1,3})\s*개\s*(?:만|정도)?(?=.{0,6}(?:알려|보여|말해|궁금|뭐|뭔))", re.IGNORECASE)


def requested_top_n(question: str) -> int | None:
    """질문에서 보고 싶다고 한 구성종목 수("상위 5개", "TOP3"). 없으면 None. ETF 이름 속 숫자("TIGER 반도체TOP10")는 가린 뒤 본다."""
    for m in _TOP_N_RE.finditer(mask_etf_names(question or "")):
        n = int(m.group(1) or m.group(2))
        if n >= 1:
            return n
    return None


def _line(rank: int, h: dict) -> str:
    code = f" ({h['code']})" if h.get("code") else ""
    return f"{rank}. {h['name']}{code} {h['weight_pct']:.2f}%"


def holdings_answer(view: dict, top_n: int | None = None) -> str:
    """구성종목 데이터가 있는 ETF 하나의 답변(코드 템플릿). view는 etf_holdings_db.holdings_view()의 결과.
    top_n은 질문이 요청한 개수이고 TOP_N_CHAT(10)을 넘기지 않는다(넘으면 10개까지만 보여주고 그렇게 알린다)."""
    name = view["name"]
    shown = min(top_n or TOP_N_CHAT, TOP_N_CHAT)
    items = view["items"][:shown]
    top_sum = round(sum(i["weight_pct"] for i in items), 2)
    parts = [
        f"{name}의 구성종목과 비중입니다. {view['source']}에 공시된 {view['as_of_date']} 기준 값입니다.",
        "상위 구성종목\n" + "\n".join(_line(i + 1, h) for i, h in enumerate(items)),
        f"위 {len(items)}개 비중 합계는 {top_sum:.2f}%이고, 현금성 자산을 뺀 전체 {view['holding_count']}개 구성종목의 "
        f"비중 합계는 {view['total_weight_pct']:.2f}%입니다.",
    ]
    if top_n and top_n > TOP_N_CHAT:
        parts.insert(2, f"한 번에 상위 {TOP_N_CHAT}개까지만 보여드립니다.")
    if kind := leverage_kind(name):
        parts.append(
            "레버리지·인버스 ETF는 선물 같은 파생상품을 담아서 비중 합계가 100%와 다를 수 있습니다"
            "(인버스 ETF의 선물 비중은 매도 포지션의 크기입니다)."
        )
        parts.append(leverage_notice(name, kind))
    if view.get("stale_notice"):
        parts.append(f"※ {view['stale_notice']} 최신 내용과 다를 수 있습니다.")
    for w in view.get("data_warnings") or []:
        parts.append(f"※ {w['message']}")
    parts.append(f"※ {FRESHNESS_NOTE} 정확한 최신 내용은 운용사 홈페이지의 상품 페이지에서 확인해주세요. "
                 "이 내용은 정보 제공용이며 투자 권유가 아닙니다.")
    return "\n\n".join(parts)


def constituents_chat_answer(views: list[dict], missing_names: list[str], not_provided_answer, top_n: int | None = None) -> str:
    """views: 데이터가 있는 ETF들, missing_names: 데이터가 없는 ETF 이름들, top_n: 질문이 요청한 개수.
    not_provided_answer(names)는 기존 "제공하지 않음" 답변을 만드는 함수(app.etf.constituents_answer)다."""
    blocks = [holdings_answer(v, top_n) for v in views[:MAX_CHAT_ETFS]]
    if len(views) > MAX_CHAT_ETFS:
        omitted = ", ".join(v["name"] for v in views[MAX_CHAT_ETFS:])
        blocks.append(f"{omitted}의 구성종목은 한 번에 {MAX_CHAT_ETFS}개까지만 보여드려서 생략했습니다. 따로 질문해주세요.")
    if missing_names:
        blocks.append(not_provided_answer(missing_names))
    return "\n\n---\n\n".join(blocks)


def api_disclaimer() -> str:
    return DISCLAIMER
