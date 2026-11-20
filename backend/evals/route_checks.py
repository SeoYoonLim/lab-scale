"""49차 실답 검사 추가 판정(evals/run_multilingual.py가 쓴다). 제품 코드의 판정 함수를 다시 쓰지 않는 독립 판정이다.

- observe(records, meta): 실제로 탄 경로·기간(거래일)·조회 종목
- route_ok(expect, observed, expected_companies): 기대 경로·기간과 맞는지(다국어 의도 판정 성공)
- period_wrong(expect, observed): 기대한 기간 숫자·방향과 다른지(잘못된 기간 판정)
- news_price_mix(korean_answer, records): 현재 주가 절(또는 제목이 없으면 답 전체)에 기사에서만 나온 시세 숫자가 있거나,
  다른 절의 기사 시세 숫자 문장에 그 기사 발행일이 없는 경우의 숫자 목록(현재 주가와 과거 뉴스 가격 혼입)
"""

from __future__ import annotations

import re

FORECAST_DAYS = {"1주일": 5, "1개월": 20, "3개월": 60}
_NUM_UNIT = re.compile(r"(?<![\d,.])([+-]?\d[\d,]*(?:\.\d+)?)\s?(만\s?원|원|%)")
_DOC_KEYS = ("news", "disclosures", "results", "indirect_news")
_SENT = re.compile(r"(?<=[.!?。])\s+|\n")
_CURRENT_HEAD = re.compile(r"^#{1,6}\s*(?:주가\s*동향|현재\s*주가|시세)")
_FIN = re.compile(r"영업이익|영업손실|순이익|매출|실적|수주|계약|배당|판매|출하|성장|점유율")


def parse_expect(spec: str) -> dict:
    out: dict = {}
    for part in (spec or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    if "days" in out:
        lo, _, hi = out["days"].partition("-")
        out["days"] = (int(lo), int(hi or lo))
    return out


def observe(records: list[dict], meta: dict) -> dict:
    meta = meta or {}
    obs = {"route": None, "days": None, "companies": []}
    hold = meta.get("etf_holdings")
    if hold:
        obs["route"] = "weight" if hold.get("kind") == "weight" else "holdings"
        return obs
    if (meta.get("forecast_horizon") or {}).get("kind") == "unsupported":
        obs["route"] = "unsupported_horizon"
        return obs
    names, past_days = [], []
    for r in records or []:
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        via = str(res.get("called_via") or "")
        if res.get("company_name") and r.get("tool_name") in ("stock_tool", "market_tool"):
            names.append(res["company_name"])
        if via.startswith("forecast_"):
            obs["route"] = "forecast"
            if res.get("period") in FORECAST_DAYS:  # 같은 경로의 market_tool 기록에는 기간이 없다(덮어쓰지 않는다)
                obs["days"] = FORECAST_DAYS[res["period"]]
        elif r.get("tool_name") == "stock_tool" and res.get("found"):
            args = r.get("arguments") or {}
            d = res.get("trading_days") or args.get("period_days") or res.get("period_days")
            try:
                past_days.append(int(d))
            except (TypeError, ValueError):
                pass
            if via == "price_flow" and obs["route"] is None:
                obs["route"] = "flow"
    obs["companies"] = sorted(set(names))
    if obs["route"] is None and names:
        obs["route"] = "past"
    if obs["route"] in ("past", "flow") and past_days:
        obs["days"] = max(past_days)
    return obs


def _in(days, rng) -> bool:
    return days is not None and rng[0] <= days <= rng[1]


def route_ok(expect: dict, obs: dict, expected_companies: list[str]) -> bool | None:
    route = expect.get("route")
    if not route or route == "any":
        return None
    got = obs.get("route")
    days = expect.get("days")
    if route == "forecast":
        return got == "forecast" and (days is None or _in(obs.get("days"), days))
    if route == "past":
        return got in ("past", "flow") and (days is None or _in(obs.get("days"), days))
    if route == "flow":
        return got == "flow"
    if route == "compare":
        return got in ("past", "flow") and set(expected_companies) <= set(obs.get("companies") or []) and \
            (days is None or _in(obs.get("days"), days))
    if route == "analysis":
        return got in ("past", "flow", "forecast") and any(c in (obs.get("companies") or []) for c in expected_companies)
    if route in ("holdings", "weight"):
        return got == route
    if route == "reject":
        return not obs.get("companies")
    return None


def period_wrong(expect: dict, obs: dict) -> bool:
    """기대 경로가 기간을 가진 경로(forecast/past/compare)인데 방향이 다르거나 기간 숫자가 범위 밖이면 True."""
    route, days = expect.get("route"), expect.get("days")
    if route not in ("forecast", "past", "compare") or days is None:
        return False
    got = obs.get("route")
    if route == "forecast":
        return got != "forecast" or not _in(obs.get("days"), days)
    return got not in ("past", "flow") or not _in(obs.get("days"), days)


def _num(s: str) -> float:
    return float(s.replace(",", "").replace("+", ""))


def _values(text: str) -> set[float]:
    out = set()
    for m in _NUM_UNIT.finditer(text or ""):
        v = _num(m.group(1))
        out.add(v * 10000 if m.group(2).startswith("만") else v)
        out.add(abs(v))
    return out


def _strings(obj, depth=0):
    if depth > 6:
        return
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield str(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, depth + 1)


def news_price_mix(answer: str, records: list[dict]) -> list[str]:
    docs, db_vals = [], set()
    for r in records or []:
        res = r.get("result")
        if not isinstance(res, dict):
            continue
        for k in _DOC_KEYS:
            for it in res.get(k) or []:
                if isinstance(it, dict):
                    docs.append(it)
        rest = {k: v for k, v in res.items() if k not in _DOC_KEYS}
        for s in _strings(rest):
            db_vals |= _values(s if not re.fullmatch(r"-?\d+(?:\.\d+)?", s) else f"{s}원") | _values(f"{s}%")
            try:
                db_vals.add(abs(float(s)))
            except ValueError:
                pass
    if not docs:
        return []
    # 숫자 읽기는 프로젝트 공용 추출기(app.answer_guard.extract_numbers: "19만2,600원" -> 192,600, "6억3000만원" -> 6.3억)를 쓴다.
    # 혼입 여부 판정(현재 절·발행일)은 이 함수가 따로 한다(제품의 strip_article_prices를 쓰지 않는다).
    from app.answer_guard import extract_numbers, support_values

    db_vals |= set(support_values([{k: v for k, v in (r.get("result") or {}).items() if k not in _DOC_KEYS}
                                   for r in records or [] if isinstance(r.get("result"), dict)]))
    doc_vals = []
    for it in docs:
        text = " ".join(_strings({k: v for k, v in it.items() if k in ("title", "content", "content_snippet", "summary")}))
        day = str(it.get("published_at") or it.get("disclosed_at") or "")[:10]
        doc_vals.append((_values(text) | set(support_values(text)), day))
    hits, current = [], False
    has_heads = bool(re.search(r"^#{1,6}\s", answer or "", re.M))
    titles = [str(it.get("title")).strip() for it in docs if len(str(it.get("title") or "").strip()) >= 8]
    for line in (answer or "").split("\n"):
        if line.lstrip().startswith("#"):
            current = bool(_CURRENT_HEAD.match(line.strip()))
            continue
        if any(t in line or t.rstrip(". …") in line for t in titles):
            continue  # 기사 제목을 그대로 인용한 줄은 출처 표시다
        fin_line = bool(_FIN.search(line) or re.search(r"시총|시가총액|과징금|판매량", line))
        for sent in _SENT.split(line):
            if fin_line and not re.search(r"주가|종가|마감", sent):
                continue
            for n in extract_numbers(sent):
                if n.unit not in ("원", "%") or n.value >= 1e8:
                    continue
                v = n.value
                if any(abs(v - x) < 1e-6 for x in db_vals):
                    continue
                days = [d for vals, d in doc_vals if any(abs(v - x) < 1e-6 for x in vals)]
                if not days:
                    continue
                dated = any(d and (d in sent or f"{int(d[5:7])}월 {int(d[8:10])}일" in sent) for d in days)
                if (current or not has_heads) or not dated:
                    hits.append(n.raw)
    return hits
