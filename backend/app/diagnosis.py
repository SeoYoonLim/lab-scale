"""내 포트폴리오 AI 진단.

원칙: 숫자와 판단(지표·플래그)은 이 모듈의 코드가 결정적으로 계산하고, LLM은 그 결과를 한국어로 설명만 한다.
종목명과 숫자가 짝을 이루는 사실 문장(강점 strengths, 위험 요인 risks)도 코드가 템플릿으로 만든다. LLM에게 맡겼을 때
숫자도 종목명도 입력에 있는 값인데 짝이 틀린 문장("비중 상위 1개 종목은 삼성전자로 6.33%" - 실제 1위는 다른 종목)이
3회 호출 중 3회 나왔고, 이런 오류는 숫자 검증으로도 종목명 대조로도 잡히지 않기 때문이다.
LLM은 전체 요약(summary)과 제안(suggestions)만 쓴다. 그 출력은 검증(JSON 형식, 필수 키, 허용 숫자, 종목명-숫자 짝,
보유하지 않은 종목명, 직접 매매 지시 금지)을 통과해야 쓰고, 통과하지 못하면 한 번만 재시도한 뒤 규칙 기반 문장(폴백)으로
대신한다. Ollama 연결 실패/타임아웃은 재시도 없이 바로 폴백이다(시연 중 멈추지 않게).

구성(위에서부터 DB 없는 순수 함수 → LLM → DB 오케스트레이션):
- compute_portfolio: 종목별 숫자 + 포트폴리오 지표
- compute_flags: 임계값(아래 상수) 기반 플래그
- allowed_numbers / find_disallowed_numbers / holding_numbers / find_mismatched_numbers / find_unknown_companies /
  validate_llm_output: LLM 출력 검증
- rule_based_text: 폴백 문장 템플릿 / fact_sentences: LLM 설명일 때의 강점·위험 문장 템플릿
- explain_with_llm: Ollama 호출(재시도 1회) → 검증 → 실패 시 None
- load_inputs: 보유 종목/잔고/현재가/20거래일 수익률 조회(DB)
- build_diagnosis: 위 함수들을 조합해 응답을 만든다(DB 없음) / diagnose = load_inputs + build_diagnosis
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import ollama
from sqlalchemy import func, select

from app.agent import MODEL_NAME
from app.company_aliases import COMPANY_ALIASES
from app.disclaimer import DISCLAIMER
from app.models import Company, Holding, StockPrice
from app.portfolio import get_or_create_account
from app.realtime_price import get_realtime_price_data

logger = logging.getLogger(__name__)

# ---------- 임계값(플래그 규칙). 단위는 %이고 비중은 총자산(현금+평가금액) 대비 ----------
CONCENTRATION_TOP1_PCT = 40.0  # 상위 1종목 비중 >= 이 값이면 집중도 높음
MIN_HOLDING_COUNT = 3  # 보유 종목 수 < 이 값이면 종목 수 적음
CASH_HIGH_PCT = 70.0  # 현금 비중 >= 이 값이면 현금 과다
CASH_LOW_PCT = 5.0  # 현금 비중 < 이 값이면 현금 부족
BIG_LOSS_PCT = -10.0  # 종목 손익률 <= 이 값이면 큰 손실 종목
MARKET_SKEW_PCT = 80.0  # 한 시장 비중(주식 평가금액 대비) >= 이 값이면 시장 편중

RETURN_WINDOW_DAYS = 20  # 최근 N거래일 수익률(stock_price 종가 기준)
LLM_TIMEOUT_SECONDS = 60
LLM_MAX_ATTEMPTS = 2  # 첫 시도 + 재시도 1회(검증 실패일 때만 재시도)
MAX_HOLDING_FACTS = 3  # 강점/위험 요인 각각에 넣는 종목별 사실 문장 수(20거래일 등락률의 절댓값이 큰 순)

NO_HOLDINGS_MESSAGE = "보유 종목이 없습니다. 모의투자 주문 후 진단할 수 있어요."

# 투자 권유로 읽힐 수 있는 직접 지시. 출력에 있으면 검증 실패로 본다.
FORBIDDEN_PHRASES = ("매수하세요", "매도하세요", "매수하십시오", "매도하십시오", "사세요", "파세요", "매수해야", "매도해야")
# 입력에 없는 지표 이름. 모델이 20거래일 등락률(return_20d_pct)을 "20일 이동평균"이라고 부르는 일이 잦다(8회 중 3회).
UNSUPPORTED_TERMS = ("이동평균", "이동 평균")


class NoHoldings(Exception):
    pass


@dataclass
class HoldingInput:
    """진단 입력 한 종목. current_price가 None이면 현재가를 못 구한 종목이다."""

    ticker: str
    company_name: str
    market: str | None
    quantity: int
    avg_price: float
    current_price: float | None
    is_realtime: bool = False
    return_20d_pct: float | None = None


# ---------- 순수 계산 ----------


def _r2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _r0(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _pct(part: float, whole: float) -> float | None:
    return _r2(part / whole * 100) if whole else None


def normalize_market(market: str | None) -> str:
    """KOSDAQ GLOBAL은 KOSDAQ으로 묶는다. 시장 정보가 없으면 OTHER."""
    if not market:
        return "OTHER"
    m = market.upper()
    if m.startswith("KOSDAQ"):
        return "KOSDAQ"
    if m.startswith("KOSPI"):
        return "KOSPI"
    return "OTHER"


def compute_portfolio(cash_balance: float, holdings: list[HoldingInput]) -> tuple[list[dict], dict]:
    """(종목별 결과, 포트폴리오 지표). 현재가를 못 구한 종목은 평가금액/비중/손익 계산에서 빠진다.

    - 비중(weight_pct), 상위 1·3종목 비중, 현금 비중: 총자산(현금 + 현재가를 구한 종목의 평가금액) 대비 %
    - 허핀달 지수: 현재가를 구한 보유 종목끼리의 비중(주식 평가금액 대비, 0~1)으로 계산한 sum(w^2). 1이면 한 종목뿐
    - 시장별 비중: 주식 평가금액 대비 %
    - 손익률: 평단가 대비(종목), 매입금액 합계 대비(포트폴리오)
    분모가 0이면 해당 값은 None이다.
    """
    cash = _r0(cash_balance)
    priced = [h for h in holdings if h.current_price is not None]
    stock_eval = _r0(sum(h.current_price * h.quantity for h in priced))
    total_asset = _r0(cash + stock_eval)

    items = []
    for h in holdings:
        item = {
            "ticker": h.ticker,
            "company_name": h.company_name,
            "market": normalize_market(h.market),
            "quantity": h.quantity,
            "avg_price": _r2(h.avg_price),
            "current_price": None if h.current_price is None else _r2(h.current_price),
            "is_realtime": bool(h.is_realtime and h.current_price is not None),
            "price_unavailable": h.current_price is None,
            "eval_amount": None,
            "weight_pct": None,
            "profit_loss": None,
            "profit_loss_pct": None,
            "return_20d_pct": None if h.return_20d_pct is None else _r2(h.return_20d_pct),
        }
        if h.current_price is not None:
            eval_amount = _r0(h.current_price * h.quantity)
            item["eval_amount"] = eval_amount
            item["weight_pct"] = _pct(eval_amount, total_asset)
            item["profit_loss"] = _r0((h.current_price - h.avg_price) * h.quantity)
            item["profit_loss_pct"] = _r2((h.current_price / h.avg_price - 1) * 100) if h.avg_price else None
        items.append(item)

    priced_items = [i for i in items if not i["price_unavailable"]]
    by_eval = sorted(priced_items, key=lambda i: i["eval_amount"], reverse=True)
    cost_basis = sum(h.avg_price * h.quantity for h in priced)
    total_pl = _r0(sum(i["profit_loss"] for i in priced_items))

    markets: dict[str, float] = {}
    for i in priced_items:
        markets[i["market"]] = markets.get(i["market"], 0.0) + i["eval_amount"]

    ranked = [i for i in priced_items if i["profit_loss_pct"] is not None]
    best = max(ranked, key=lambda i: i["profit_loss_pct"], default=None)
    worst = min(ranked, key=lambda i: i["profit_loss_pct"], default=None)

    def brief(i):
        return None if i is None else {
            "ticker": i["ticker"], "company_name": i["company_name"], "profit_loss_pct": i["profit_loss_pct"],
        }

    metrics = {
        "total_asset": total_asset,
        "cash_balance": cash,
        "cash_weight_pct": _pct(cash, total_asset),
        "stock_eval_amount": stock_eval,
        "holding_count": len(holdings),
        "priced_holding_count": len(priced_items),
        "top1_weight_pct": _pct(sum(i["eval_amount"] for i in by_eval[:1]), total_asset) if by_eval else None,
        "top3_weight_pct": _pct(sum(i["eval_amount"] for i in by_eval[:3]), total_asset) if by_eval else None,
        "herfindahl_index": (
            float(Decimal(str(sum((i["eval_amount"] / stock_eval) ** 2 for i in priced_items))).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP))
            if stock_eval else None
        ),
        "market_weights_pct": {m: _pct(v, stock_eval) for m, v in sorted(markets.items())} if stock_eval else {},
        "total_profit_loss": total_pl,
        "total_profit_loss_pct": _pct(total_pl, cost_basis) if cost_basis else None,
        "best_holding": brief(best),
        "worst_holding": brief(worst),
    }
    return items, metrics


def compute_flags(items: list[dict], metrics: dict) -> list[dict]:
    """임계값 기반 플래그. 각 플래그: {code, message, value, threshold[, ticker, company_name]}."""
    flags = []

    def add(code, message, value, threshold, **extra):
        flags.append({"code": code, "message": message, "value": value, "threshold": threshold, **extra})

    top1 = metrics["top1_weight_pct"]
    if top1 is not None and top1 >= CONCENTRATION_TOP1_PCT:
        add("CONCENTRATION_HIGH", f"상위 1종목 비중이 {top1}%로 기준({CONCENTRATION_TOP1_PCT:g}%) 이상입니다.",
            top1, CONCENTRATION_TOP1_PCT)

    n = metrics["holding_count"]
    if n < MIN_HOLDING_COUNT:
        add("FEW_HOLDINGS", f"보유 종목이 {n}개로 기준({MIN_HOLDING_COUNT}개) 미만입니다.", n, MIN_HOLDING_COUNT)

    cash_pct = metrics["cash_weight_pct"]
    if cash_pct is not None and cash_pct >= CASH_HIGH_PCT:
        add("CASH_HIGH", f"현금 비중이 {cash_pct}%로 기준({CASH_HIGH_PCT:g}%) 이상입니다.", cash_pct, CASH_HIGH_PCT)
    if cash_pct is not None and cash_pct < CASH_LOW_PCT:
        add("CASH_LOW", f"현금 비중이 {cash_pct}%로 기준({CASH_LOW_PCT:g}%) 미만입니다.", cash_pct, CASH_LOW_PCT)

    for i in items:
        pl = i["profit_loss_pct"]
        if pl is not None and pl <= BIG_LOSS_PCT:
            add("BIG_LOSS", f"{i['company_name']}의 손익률이 {pl}%로 기준({BIG_LOSS_PCT:g}%) 이하입니다.",
                pl, BIG_LOSS_PCT, ticker=i["ticker"], company_name=i["company_name"])

    for market, pct in metrics["market_weights_pct"].items():
        if pct is not None and pct >= MARKET_SKEW_PCT:
            add("MARKET_SKEW", f"주식 평가금액의 {pct}%가 {market}에 몰려 있습니다(기준 {MARKET_SKEW_PCT:g}%).",
                pct, MARKET_SKEW_PCT, market=market)
    return flags


# ---------- LLM 출력 검증 ----------

_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def _dec(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "")).normalize()
    except InvalidOperation:
        return None


def _variants(x: Decimal) -> set[Decimal]:
    """한 숫자의 허용 표기: 그대로, 소수 0~2자리 반올림, 천/만/억 단위 환산(각각 소수 0~2자리 반올림)."""
    x = abs(x)
    out = set()
    for base in (x, x / Decimal(1_000), x / Decimal(10_000), x / Decimal(100_000_000)):
        out.add(base.normalize())
        for q in ("1", "0.1", "0.01"):
            out.add(base.quantize(Decimal(q), rounding=ROUND_HALF_UP).normalize())
    return out


def allowed_numbers(payload) -> set[Decimal]:
    """LLM 입력(payload)에 나온 모든 숫자(숫자 값 + 문자열 안의 숫자)와 그 표기 변형, 그리고 1~10 정수."""
    found: list[Decimal] = []

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            found.append(Decimal(str(v)))
        elif isinstance(v, str):
            found.extend(d for d in (_dec(m) for m in _NUMBER_RE.findall(v)) if d is not None)
        elif isinstance(v, dict):
            for k, val in v.items():
                walk(k)
                walk(val)
        elif isinstance(v, (list, tuple)):
            for val in v:
                walk(val)

    walk(payload)
    allowed = {Decimal(i) for i in range(1, 11)}
    for d in found:
        allowed |= _variants(d)
    return allowed


def find_disallowed_numbers(text: str, allowed: set[Decimal]) -> list[str]:
    """text에 나온 숫자 중 allowed에 없는 것(원문 표기 그대로). 부호(-, +)는 무시하고 절대값으로 비교한다."""
    bad = []
    for m in _NUMBER_RE.findall(text):
        d = _dec(m)
        if d is not None and d not in allowed:
            bad.append(m)
    return bad


NO_RISK_SENTENCE = "규칙 기준으로 확인된 위험 항목은 없습니다."


def holding_numbers(payload: dict) -> dict[str, set[Decimal]]:
    """종목명 -> 그 종목을 말하는 문장에 쓸 수 있는 숫자.

    그 종목의 holdings 항목에 있는 숫자, 그 종목에 붙은 플래그(BIG_LOSS)의 값·기준, 수익률 기간(return_window_days)과
    그 표기 변형이다(allowed_numbers와 같은 규칙). 포트폴리오 전체 숫자(metrics)나 다른 종목의 숫자는 들어가지 않는다."""
    flags = payload.get("flags", [])
    out = {}
    for h in payload.get("holdings", []):
        name = h.get("company_name")
        if not name:
            continue
        own_flags = [f for f in flags if f.get("company_name") == name]
        out[name] = allowed_numbers([h, own_flags, payload.get("return_window_days")])
    return out


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def find_mismatched_numbers(text: str, per_holding: dict[str, set[Decimal]]) -> list[str]:
    """보유 종목명이 나온 문장에서, 그 문장에 나온 종목(들)의 숫자가 아닌 숫자(원문 표기 그대로).

    "삼성전자의 비중은 6.33%입니다"(6.33은 다른 종목의 비중)처럼 숫자도 종목도 입력에 있지만 짝이 틀린 문장을
    잡는다. 종목명이 없는 문장은 검사하지 않는다(그 숫자는 allowed_numbers 검사가 본다). 이름이 겹치는 종목
    (카카오/카카오뱅크)은 긴 이름부터 찾아 지운 뒤 짧은 이름을 찾는다. 종목명과 포트폴리오 전체 숫자를 한 문장에
    쓴 정상 문장("삼성전자 등을 보유하고 현금 비중은 87.44%")도 걸리지만, 그때는 재시도·폴백으로 갈 뿐이라 안전한 쪽이다."""
    names = sorted(per_holding, key=len, reverse=True)
    bad = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        rest, allowed = sentence, set()
        for name in names:
            if name in rest:
                rest = rest.replace(name, " ")
                allowed |= per_holding[name]
        if allowed:
            bad.extend(find_disallowed_numbers(rest, allowed))
    return bad


def _has_hangul(s: str) -> bool:
    return any("가" <= ch <= "힣" for ch in s)


# 보유하지 않은 종목명을 찾을 때 쓰는 이름 사전: {표기: company.name}. COMPANY_ALIASES의 통칭과 등록명이다.
# 'SM', 'NC', 'KAI'처럼 짧은 영문 이름은 다른 낱말(플래그 코드 CONCENTRATION 등)의 일부로 걸려서 뺀다.
_KNOWN_COMPANY_NAMES = {
    name: registered
    for alias, registered in COMPANY_ALIASES.items()
    for name in (alias, registered)
    if _has_hangul(name) or len(name) >= 5
}


def find_unknown_companies(text: str, held_names) -> list[str]:
    """text에 나온 종목명 중 보유 종목이 아닌 것(표기 그대로, 정렬). 보유 종목의 통칭("삼전")은 보유 종목으로 본다.

    이름 사전이 COMPANY_ALIASES(대표 종목의 통칭·등록명 약 100개)라서 그 밖의 종목명은 잡지 못한다.
    모델이 지어낼 가능성이 높은 대형주 이름을 막는 용도다."""
    held = set(held_names)
    rest = text
    for name in sorted(held, key=len, reverse=True):
        rest = rest.replace(name, " ")
    found = []
    # 긴 이름부터 찾아 지운다: "SK하이닉스"를 찾았으면 그 안의 통칭 "하이닉스"를 또 세지 않는다.
    for name in sorted(_KNOWN_COMPANY_NAMES, key=len, reverse=True):
        if _KNOWN_COMPANY_NAMES[name] not in held and name in rest:
            found.append(name)
            rest = rest.replace(name, " ")
    return sorted(found)


# 비중 1위를 가리키는 표현. 숫자가 없어서 숫자 검사로는 못 잡는 "비중 상위 1개 종목인 삼성전자"(실제 1위는 다른 종목)
# 같은 문장을 잡는다. 모델이 holdings의 첫 항목을 1위로 가정해서 6회 중 3회 나왔다.
_TOP_WEIGHT_RE = re.compile(r"상위\s*1\s*(?:개|종목|위)|비중\s*1\s*위|비중이\s*가장\s*(?:큰|높은)|가장\s*(?:큰|높은)\s*비중")


def top_weight_names(payload: dict) -> set[str]:
    """비중(weight_pct)이 가장 큰 보유 종목의 이름(동률이면 모두). 비중을 구한 종목이 없으면 빈 집합."""
    weighted = [h for h in payload.get("holdings", []) if h.get("weight_pct") is not None]
    if not weighted:
        return set()
    top = max(h["weight_pct"] for h in weighted)
    return {h["company_name"] for h in weighted if h["weight_pct"] == top}


def find_wrong_top_claims(text: str, held_names, top_names: set[str]) -> list[str]:
    """비중 1위를 말하면서 보유 종목을 거론했는데 실제 1위 종목은 빠져 있는 문장들.

    "비중 상위 1개/3개 종목은 A, B, C입니다"처럼 1위 종목이 같이 나오면 통과시킨다(누가 1위인지까지는 따지지 않는다)."""
    if not top_names:
        return []
    wrong = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not _TOP_WEIGHT_RE.search(sentence):
            continue
        mentioned = {name for name in held_names if name in sentence}
        if mentioned and not mentioned & top_names:
            wrong.append(sentence.strip())
    return wrong


def validate_llm_output(
    content: str, allowed: set[Decimal], payload: dict | None = None
) -> tuple[dict | None, str | None]:
    """({"summary", "suggestions"}, None) 또는 (None, 실패 사유).

    LLM은 summary와 suggestions만 쓴다(강점·위험 문장은 코드가 만든다, fact_sentences). 출력에 다른 키가 있어도
    무시한다. 숫자가 든 제안 문장은 실패로 치지 않고 버리므로 suggestions가 빈 리스트로 돌아올 수 있다. payload(build_llm_payload의 결과)를 주면 보유 종목 기준 검사(보유하지 않은 종목명, 종목명-숫자 짝,
    비중 1위 종목)도 한다.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None, "JSON 파싱 실패"
    if not isinstance(data, dict):
        return None, "JSON 객체가 아님"

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None, "summary 누락"
    suggestions = data.get("suggestions")
    if not isinstance(suggestions, list) or not all(isinstance(s, str) for s in suggestions):
        return None, "suggestions 누락 또는 형식 오류"
    # 같은 문장이 반복되면 한 번만 남긴다(순서 유지)
    out = {"summary": summary.strip(), "suggestions": list(dict.fromkeys(s.strip() for s in suggestions if s.strip()))}
    if not out["suggestions"]:
        return None, "suggestions가 비어 있음"
    # 제안은 '무엇을 점검해볼지'만 남긴다. 숫자가 든 제안은 지표를 다시 읊거나("상위 3개 종목 합계 비중은 12.56%입니다")
    # 없는 기준을 지어낸("집중도는 0.4352로 기준 이상입니다") 문장이라서 버린다. 숫자는 summary와 코드가 만든
    # 강점/위험 문장에 이미 있다. 검증 실패로 처리하면 모델이 재시도에서도 숫자를 빼지 못해 거의 매번 폴백이 됐다
    # (13회 중 12회). 전부 걸러져 빈 리스트가 되면 build_diagnosis가 규칙 기반 제안으로 채운다.
    out["suggestions"] = [s for s in out["suggestions"] if not _NUMBER_RE.search(s)]

    joined = "\n".join([out["summary"], *out["suggestions"]])
    bad = find_disallowed_numbers(joined, allowed)
    if bad:
        return None, f"입력에 없는 숫자 {bad[:5]}"
    hit = [p for p in FORBIDDEN_PHRASES if p in joined]
    if hit:
        return None, f"직접 매매 지시 표현 {hit}"
    hit = [t for t in UNSUPPORTED_TERMS if t in joined]
    if hit:
        return None, f"입력에 없는 지표 {hit}(return_20d_pct는 최근 20거래일 주가 등락률이다)"
    per_holding = holding_numbers(payload) if payload else {}
    if per_holding:
        unknown = find_unknown_companies(joined, per_holding)
        if unknown:
            return None, f"보유하지 않은 종목명 {unknown[:5]}"
        mismatched = find_mismatched_numbers(joined, per_holding)
        if mismatched:
            return None, f"종목명과 맞지 않는 숫자 {mismatched[:5]}(종목명을 쓴 문장에는 그 종목의 숫자만 쓴다)"
        top = top_weight_names(payload)
        if find_wrong_top_claims(joined, per_holding, top):
            return None, f"비중 1위 종목이 틀림(비중이 가장 큰 종목은 {', '.join(sorted(top))})"
    return out, None


# ---------- 폴백(규칙 기반 문장) ----------


def _won(x: float) -> str:
    return f"{x:,.0f}원"


def rule_based_text(items: list[dict], metrics: dict, flags: list[dict]) -> dict:
    """지표와 플래그로 만든 고정 문장. 숫자는 모두 metrics/items에 있는 값을 그대로 쓴다."""
    m = metrics
    codes = {f["code"] for f in flags}

    summary = (
        f"총자산 {_won(m['total_asset'])} 중 현금이 {_won(m['cash_balance'])}"
        + (f"(비중 {m['cash_weight_pct']}%)" if m["cash_weight_pct"] is not None else "")
        + f"이고, 보유 종목은 {m['holding_count']}개입니다."
    )
    if m["total_profit_loss_pct"] is not None:
        summary += f" 총 평가손익은 {_won(m['total_profit_loss'])}({m['total_profit_loss_pct']}%)입니다."
    if flags:
        summary += f" 규칙 점검에서 {len(flags)}개 항목이 확인됐습니다."
    else:
        summary += " 규칙 점검에서 특별히 확인된 항목은 없습니다."

    strengths = []
    if m["top1_weight_pct"] is not None and "CONCENTRATION_HIGH" not in codes:
        strengths.append(f"상위 1종목 비중이 {m['top1_weight_pct']}%로 한 종목에 크게 쏠려 있지 않습니다.")
    if "FEW_HOLDINGS" not in codes:
        strengths.append(f"{m['holding_count']}개 종목에 나눠 보유하고 있습니다.")
    if m["best_holding"] and m["best_holding"]["profit_loss_pct"] is not None and m["best_holding"]["profit_loss_pct"] > 0:
        b = m["best_holding"]
        strengths.append(f"{b['company_name']}의 손익률이 {b['profit_loss_pct']}%로 가장 높습니다.")
    if not codes & {"CASH_HIGH", "CASH_LOW"} and m["cash_weight_pct"] is not None:
        strengths.append(f"현금 비중이 {m['cash_weight_pct']}%로 규칙 기준 범위 안에 있습니다.")
    if not strengths:
        strengths.append("규칙 기준으로 뚜렷한 강점을 판단하기 어렵습니다.")

    risks = [f["message"] for f in flags] or [NO_RISK_SENTENCE]

    suggestion_by_code = {
        "CONCENTRATION_HIGH": "한 종목 비중을 낮추는 분산 방안을 검토해볼 수 있습니다.",
        "FEW_HOLDINGS": "업종이나 시장이 다른 종목을 함께 보유하는 방안을 검토해볼 수 있습니다.",
        "CASH_HIGH": "현금 비중이 높은 이유와 투자 계획을 점검해볼 수 있습니다.",
        "CASH_LOW": "예상치 못한 하락에 대비한 현금 여유를 검토해볼 수 있습니다.",
        "BIG_LOSS": "손실이 큰 종목은 처음 매수한 이유가 여전히 유효한지 점검해볼 수 있습니다.",
        "MARKET_SKEW": "다른 시장의 종목도 함께 살펴보는 방안을 검토해볼 수 있습니다.",
    }
    suggestions = [suggestion_by_code[c] for c in suggestion_by_code if c in codes]
    if not suggestions:
        suggestions.append("현재 구성을 유지하면서 종목별 뉴스와 공시를 주기적으로 확인해볼 수 있습니다.")
    return {"summary": summary, "strengths": strengths, "risks": risks, "suggestions": suggestions}


def _topic_particle(word: str) -> str:
    """보조사 '은/는'. 마지막 글자가 받침 있는 한글이면 '은', 그 외(받침 없음, 영문·숫자로 끝남)는 '는'."""
    last = word[-1:]
    if "가" <= last <= "힣" and (ord(last) - ord("가")) % 28:
        return "은"
    return "는"


def holding_fact(item: dict) -> str:
    """한 종목의 사실 문장: 총자산 대비 비중과 최근 20거래일 주가 등락률. 한 문장의 숫자는 모두 그 종목(item)의 값이다."""
    name = item["company_name"]
    head = f"{name}{_topic_particle(name)} "
    tail = f"최근 {RETURN_WINDOW_DAYS}거래일 주가 등락률은 {item['return_20d_pct']:+}%입니다."
    if item["weight_pct"] is None:
        return head + tail
    return head + f"총자산의 {item['weight_pct']}%이고, " + tail


def fact_sentences(items: list[dict], metrics: dict, flags: list[dict]) -> dict:
    """LLM 설명(source="llm")일 때의 강점·위험 요인 문장. 전부 코드 템플릿이라 종목명과 숫자의 짝이 틀릴 수 없다.

    - strengths: 포트폴리오 사실(rule_based_text와 같은 문장) + 최근 20거래일 주가가 오른 종목의 사실 문장
    - risks: 플래그 문장(rule_based_text와 같은 문장) + 최근 20거래일 주가가 내린 종목의 사실 문장
    종목 문장은 등락률 절댓값이 큰 순으로 각각 MAX_HOLDING_FACTS개까지. 등락률이 없거나 0인 종목은 넣지 않는다.
    """
    base = rule_based_text(items, metrics, flags)
    moved = sorted((i for i in items if i["return_20d_pct"]), key=lambda i: abs(i["return_20d_pct"]), reverse=True)
    up = [holding_fact(i) for i in moved if i["return_20d_pct"] > 0][:MAX_HOLDING_FACTS]
    down = [holding_fact(i) for i in moved if i["return_20d_pct"] < 0][:MAX_HOLDING_FACTS]
    return {"strengths": base["strengths"] + up, "risks": base["risks"] + down}


# ---------- LLM ----------

SYSTEM_PROMPT = """너는 모의투자 포트폴리오 진단 결과를 한국어로 설명하는 도우미다.
입력 JSON의 지표(metrics), 플래그(flags), 종목별 숫자(holdings)는 이미 코드가 계산한 값이다. 너는 설명만 한다.
holdings는 비중(weight_pct)이 큰 순서로 정렬돼 있다(첫 항목이 비중 1위 종목이다).
강점과 위험 요인 목록은 코드가 따로 만들어 보여준다. 너는 전체 요약(summary)과 제안(suggestions)만 쓴다.

규칙:
1. 입력에 있는 숫자와 종목만 쓴다. 숫자는 입력 값을 그대로 옮긴다(새로 더하거나 빼거나 평균 내지 않는다).
2. 입력에 없는 수치, 종목, 뉴스, 업종, 전망을 만들지 않는다.
3. "매수하세요", "매도하세요" 같은 직접 지시를 쓰지 않는다. "~를 검토해볼 수 있습니다"처럼 쓴다.
4. 종목명을 쓰는 문장에는 holdings에서 그 종목 항목에 있는 숫자만 쓴다. 다른 종목의 숫자나 metrics의 숫자를
   같은 문장에 섞지 않는다.
5. summary는 포트폴리오 전체 상태를 1~2문장으로 요약한다. suggestions는 flags를 근거로 무엇을 점검해볼지 1~3개,
   각 1문장으로 쓴다(숫자는 summary에만 쓴다). flags에 없는 위험은 새로 만들지 않는다. flags가 비어 있으면 지금
   구성을 점검해보라는 제안 1개만 쓴다.
6. 출력은 아래 JSON 하나만. 다른 텍스트는 쓰지 않는다.

필드 뜻(%는 모두 퍼센트):
- weight_pct: 총자산(현금+주식) 대비 그 종목 비중. cash_weight_pct: 총자산 대비 현금 비중
- profit_loss_pct: 평균 매입가 대비 손익률. return_20d_pct: 최근 20거래일 동안 주가가 오르내린 비율(등락률), null이면 데이터 부족
- top1_weight_pct/top3_weight_pct: 비중 상위 1개/3개 종목 합계 비중. herfindahl_index: 0~1, 1에 가까울수록 한 종목에 집중
- market_weights_pct: 주식 평가금액 대비 시장별 비중. flags[].threshold: 그 플래그의 기준값
{"summary": "...", "suggestions": ["..."]}"""

_client: ollama.Client | None = None


def _chat(messages: list[dict]) -> str:
    """Ollama 호출(JSON 모드, 타임아웃 LLM_TIMEOUT_SECONDS). 테스트는 이 함수를 바꿔 끼운다."""
    global _client
    if _client is None:
        _client = ollama.Client(timeout=LLM_TIMEOUT_SECONDS)
    resp = _client.chat(model=MODEL_NAME, messages=messages, format="json", options={"temperature": 0.2})
    return resp["message"]["content"]


def build_llm_payload(items: list[dict], metrics: dict, flags: list[dict]) -> dict:
    """LLM에 넘기는 입력. 사용자 식별 정보(username, user id, owner key)는 넣지 않는다.

    holdings는 비중이 큰 순으로 넣는다(비중이 없는 종목은 뒤). 모델이 목록의 첫 종목을 '비중 1위'로 읽기 때문이다
    (매수 순서대로 넣었을 때 "비중 상위 1개 종목은 삼성전자" 같은 틀린 문장이 반복됐다). 응답의 holdings 순서와는 무관하다."""
    by_weight = sorted(items, key=lambda i: i["weight_pct"] if i["weight_pct"] is not None else -1.0, reverse=True)
    return {
        "metrics": metrics,
        "flags": [{k: v for k, v in f.items()} for f in flags],
        "holdings": [
            {k: i[k] for k in ("company_name", "ticker", "market", "quantity", "avg_price", "current_price",
                                "eval_amount", "weight_pct", "profit_loss", "profit_loss_pct", "return_20d_pct",
                                "price_unavailable")}
            for i in by_weight
        ],
        "return_window_days": RETURN_WINDOW_DAYS,
    }


def explain_with_llm(payload: dict) -> tuple[dict | None, str | None]:
    """(검증 통과한 {"summary", "suggestions"}, None) 또는 (None, 실패 사유).

    검증 실패면 한 번 재시도, 연결 실패/타임아웃은 바로 포기."""
    allowed = allowed_numbers(payload)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    reason = None
    for attempt in range(LLM_MAX_ATTEMPTS):
        try:
            content = _chat(messages)
        except Exception as e:  # 연결 실패, 타임아웃, 모델 없음(ResponseError) 등
            logger.warning("진단 LLM 호출 실패(%s), 규칙 기반으로 대체", type(e).__name__)
            return None, f"LLM 호출 실패({type(e).__name__})"
        result, reason = validate_llm_output(content, allowed, payload)
        if result is not None:
            return result, None
        logger.info("진단 LLM 출력 검증 실패(%d회차): %s", attempt + 1, reason)
        messages = messages[:2] + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": f"규칙 위반: {reason}. 규칙을 지켜 JSON 하나만 다시 출력해."},
        ]
    return None, f"출력 검증 실패({reason})"


# ---------- DB 오케스트레이션 ----------


def _recent_returns(db, company_ids: list[int]) -> dict[int, float]:
    """회사별 최근 RETURN_WINDOW_DAYS거래일 수익률(%) = 최신 종가 / N거래일 전 종가 - 1. 데이터가 모자라면 빠진다."""
    if not company_ids:
        return {}
    rn = func.row_number().over(partition_by=StockPrice.company_id, order_by=StockPrice.price_date.desc()).label("rn")
    sub = (
        select(StockPrice.company_id, StockPrice.close_price, rn)
        .where(StockPrice.company_id.in_(company_ids))
        .subquery()
    )
    rows = db.execute(
        select(sub.c.company_id, sub.c.close_price, sub.c.rn).where(sub.c.rn.in_([1, RETURN_WINDOW_DAYS + 1]))
    ).all()
    latest, base = {}, {}
    for company_id, close, n in rows:
        (latest if n == 1 else base)[company_id] = float(close)
    return {cid: (latest[cid] / base[cid] - 1) * 100 for cid in latest if base.get(cid)}


def load_inputs(db, owner_key: str) -> tuple[float, list[HoldingInput]]:
    """(현금 잔고, 종목별 입력). owner_key의 행만 읽는다. 현재가는 get_realtime_price_data(실시간 → DB 폴백)."""
    account = get_or_create_account(db, owner_key)
    rows = (
        db.query(Holding, Company)
        .join(Company, Company.id == Holding.company_id)
        .filter(Holding.device_id == owner_key)
        .order_by(Holding.id)
        .all()
    )
    if not rows:
        return float(account.cash_balance), []

    returns = _recent_returns(db, [c.id for _, c in rows])
    inputs = []
    for h, c in rows:
        price = get_realtime_price_data(db, c)
        inputs.append(
            HoldingInput(
                ticker=c.ticker,
                company_name=c.name,
                market=c.market,
                quantity=h.quantity,
                avg_price=float(h.avg_price),
                current_price=price["current_price"] if price else None,
                is_realtime=bool(price and price["is_realtime"]),
                return_20d_pct=returns.get(c.id),
            )
        )
    return float(account.cash_balance), inputs


def build_diagnosis(cash_balance: float, inputs: list[HoldingInput]) -> dict:
    """입력으로 진단 응답을 만든다(DB 없음, LLM 호출 포함). 보유 종목이 없으면 NoHoldings(LLM 호출 없음)."""
    if not inputs:
        raise NoHoldings(NO_HOLDINGS_MESSAGE)
    items, metrics = compute_portfolio(cash_balance, inputs)
    flags = compute_flags(items, metrics)

    notes = []
    unpriced = [i["company_name"] for i in items if i["price_unavailable"]]
    if unpriced:
        notes.append(f"{', '.join(unpriced)}의 현재가를 가져오지 못해 평가금액·비중·손익 계산에서 제외했습니다.")
    fallback_priced = [i["company_name"] for i in items if not i["price_unavailable"] and not i["is_realtime"]]
    if fallback_priced:
        notes.append(f"{', '.join(fallback_priced)}는 실시간 시세 대신 DB에 저장된 최근 종가로 계산했습니다.")
    no_return = [i["company_name"] for i in items if i["return_20d_pct"] is None]
    if no_return:
        notes.append(f"{', '.join(no_return)}는 저장된 주가가 부족해 최근 {RETURN_WINDOW_DAYS}거래일 수익률을 계산하지 못했습니다.")

    explanation, reason = None, None
    if metrics["priced_holding_count"] == 0:
        reason = "현재가를 구한 종목이 없음"
    else:
        explanation, reason = explain_with_llm(build_llm_payload(items, metrics, flags))

    if explanation is None:
        explanation = rule_based_text(items, metrics, flags)
        notes.append("AI 설명을 생성하지 못해 규칙 기반 문장으로 대신했습니다.")
        logger.info("진단 폴백 사유: %s", reason)
        source, model = "rule_based", None
    else:
        # LLM이 쓴 것은 summary와 suggestions뿐이다. 종목명·숫자가 들어가는 강점/위험 문장은 코드 템플릿을 쓴다.
        facts = fact_sentences(items, metrics, flags)
        explanation = {
            "summary": explanation["summary"],
            "strengths": facts["strengths"],
            "risks": facts["risks"],
            # 숫자가 든 제안이 전부 걸러졌으면 규칙 기반 제안을 쓴다
            "suggestions": explanation["suggestions"] or rule_based_text(items, metrics, flags)["suggestions"],
        }
        source, model = "llm", MODEL_NAME

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "model": model,
        "metrics": metrics,
        "holdings": items,
        "flags": flags,
        **explanation,
        "notes": notes,
        "disclaimer": DISCLAIMER,
    }


def diagnose(db, owner_key: str) -> dict:
    """owner_key의 포트폴리오를 진단한다. 보유 종목이 없으면 NoHoldings(LLM 호출 없음)."""
    return build_diagnosis(*load_inputs(db, owner_key))
