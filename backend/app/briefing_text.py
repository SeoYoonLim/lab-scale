"""(D, 2026-10-28) 채팅용 브리핑 텍스트. GET /api/briefing과 같은 계산 결과(app.briefing_db.get_briefing)를 문장으로만 바꾼다.

새 숫자를 만들지 않는다: 아래 문장의 숫자는 모두 브리핑 dict에 있는 값을 형식만 바꿔 쓴 것이다(콤마, 부호, 소수 둘째 자리).
LLM을 쓰지 않고, 문장은 관찰 사실과 브리핑의 flag 문구(app/briefing.py, 금지 표현 검사 대상)만 쓴다. 면책 문구를 항상 붙인다.
scope: "all"(오늘 브리핑), "holdings"(내 보유종목), "watchlist"(관심종목).
"""

LOGIN_REQUIRED = "로그인하면 개인화 브리핑을 볼 수 있습니다."
SCOPES = ("all", "holdings", "watchlist")


def _pct(v) -> str:
    return f"{v:+.2f}%"


def _won(v) -> str:
    return f"{v:,.0f}원"


def _quote(q: dict | None) -> str | None:
    if not q:
        return None
    value = f"{q['close']:,.2f}원" if q["code"] == "USD/KRW" else f"{q['close']:,.2f}"
    change = f", 직전 대비 {_pct(q['change_pct'])}" if q.get("change_pct") is not None else ""
    return f"{q['name']} {value}({q['date']}{change})"


def _item_line(it: dict, holding: bool) -> str:
    if not it["data_available"]:
        return f"- {it['name']}: 수집된 시세가 없습니다."
    parts = [f"종가 {_won(it['latest_close'])}({it['latest_date']})"]
    if it.get("day_change_pct") is not None:
        parts.append(f"전 거래일 대비 {_pct(it['day_change_pct'])}")
    if it.get("week_change_pct") is not None:
        parts.append(f"5거래일 {_pct(it['week_change_pct'])}")
    if it.get("volume_ratio") is not None:
        parts.append(f"거래량은 직전 20거래일 평균의 {it['volume_ratio']:.2f}배")
    if holding:
        parts.append(f"보유 {it['quantity']:,}주")
        if it.get("weight_pct") is not None:
            parts.append(f"비중 {it['weight_pct']:.2f}%")
        if it.get("profit_loss") is not None:
            parts.append(f"평가손익 {it['profit_loss']:+,.0f}원")
    line = f"- {it['name']}: " + ", ".join(parts)
    if it.get("disclosure_count"):
        line += f" (최근 7일 공시 {it['disclosure_count']}건)"
    if it.get("notice"):
        line += f"\n  ※ {it['notice']}"
    return line


def render(brief: dict, scope: str = "all") -> str:
    lines: list[str] = []
    head = f"{brief['data_date']} 종가 기준 브리핑입니다." if brief.get("data_date") else "브리핑입니다."
    if brief.get("stale_notice"):
        head += f" {brief['stale_notice']}"
    lines.append(head)

    market = [s for s in (_quote(brief["market"].get(k)) for k in ("kospi", "kosdaq", "usd_krw")) if s]
    if market:
        lines += ["", "## 시장", "- " + " / ".join(market)]

    p = brief["portfolio"]
    if scope in ("all", "holdings"):
        lines += ["", "## 모의투자 계좌"]
        if p["has_account"]:
            lines.append(
                f"- 총자산 {_won(p['total_asset'])}(현금 {_won(p['cash_balance'])}, 보유 평가금액 {_won(p['holdings_value'])})")
            lines.append(f"- 초기 자금 {_won(p['initial_balance'])} 대비 {p['profit_loss']:+,.0f}원({_pct(p['profit_loss_pct'])})")
            if p.get("day_profit_loss") is not None:
                lines.append(f"- 전일 대비 평가손익 {p['day_profit_loss']:+,.0f}원")
        if p.get("message"):
            lines.append(f"- {p['message']}")
        if brief["holdings"]:
            lines += ["", "## 보유종목"] + [_item_line(it, True) for it in brief["holdings"]]

    if scope in ("all", "watchlist"):
        if brief["watchlist"]:
            lines += ["", "## 관심종목"] + [_item_line(it, False) for it in brief["watchlist"]]
        elif scope == "watchlist":
            lines += ["", "## 관심종목", "- 보유종목이 아닌 관심종목이 없습니다."]

    wanted = {"all": None, "holdings": "holding", "watchlist": "watchlist"}[scope]
    highlights = [f for f in brief["highlights"] if wanted is None or f["scope"] == wanted]
    if highlights:
        lines += ["", "## 눈에 띄는 변화"] + [f"- {f['text']}" for f in highlights]
    elif brief["holdings"] or brief["watchlist"]:
        # 임계값 숫자는 여기 쓰지 않는다(이 문장의 숫자도 브리핑 dict에 있는 값이어야 한다)
        lines += ["", "## 눈에 띄는 변화", "- 브리핑 기준(등락률, 거래량, 새 공시, 비중)에 해당하는 큰 변화가 없습니다."]

    if brief.get("message"):
        lines += ["", brief["message"]]
    lines += ["", brief["disclaimer"]]
    return "\n".join(lines)
