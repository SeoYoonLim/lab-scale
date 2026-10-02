"""모의투자(가상 계좌/보유 종목/체결 내역). 로그인이 없어 디바이스ID(X-Device-Id)로 구분한다(app/api/deps.py).

db/schema.sql 원안에는 없던 확장이다(README "서윤님이 설계한 스키마와..." 참고). 현재가는 app/realtime_price.py의
get_realtime_price_data를 그대로 재사용한다(실시간 실패 시 DB 최근 종가 폴백도 그대로 적용됨).
호가 단위·장 시간 체크는 범위 밖이라 매수/매도 모두 현재가로 즉시 체결되는 것으로 단순화한다.

매수/매도의 잔고·수량·평균단가 계산(핵심 로직)은 DB를 전혀 건드리지 않는 apply_buy/apply_sell에 분리해뒀다.
place_order는 그 계산 결과를 Holding/VirtualAccount/Trade row에 반영하는 DB 오케스트레이션만 한다.
"""

from app.models import Company, Holding, Trade, VirtualAccount
from app.models.trade import SIDE_BUY
from app.models.virtual_account import INITIAL_BALANCE
from app.realtime_price import get_realtime_price_data


class InsufficientBalance(Exception):
    """매수 주문 금액이 보유 현금보다 많을 때."""


class InsufficientQuantity(Exception):
    """매도 주문 수량이 보유 수량보다 많을 때."""


class PriceUnavailable(Exception):
    """실시간 소스와 DB 폴백 모두 가격을 구하지 못했을 때(app.realtime_price.get_realtime_price_data가 None)."""


def apply_buy(
    cash_balance: float, quantity: int, price: float, holding_qty: int = 0, holding_avg_price: float = 0.0
) -> dict:
    """매수 체결 결과를 계산한다(DB 없음). 잔고가 모자라면 InsufficientBalance.

    평균단가는 가중평균((기존수량*기존평균 + 이번수량*체결가) / 합산수량)으로 다시 계산한다.
    기존 보유가 없으면(holding_qty=0) 평균단가는 이번 체결가 그대로다.
    반환: {"cash_balance", "quantity", "avg_price"} (모두 갱신된 값).
    """
    total = round(price * quantity, 2)
    if cash_balance < total:
        raise InsufficientBalance(f"잔고가 부족합니다(필요 {total:,.0f}원, 보유 {cash_balance:,.0f}원).")

    new_qty = holding_qty + quantity
    if holding_qty == 0:
        new_avg = price
    else:
        new_avg = round((holding_avg_price * holding_qty + price * quantity) / new_qty, 2)

    return {"cash_balance": round(cash_balance - total, 2), "quantity": new_qty, "avg_price": new_avg}


def apply_sell(cash_balance: float, quantity: int, price: float, holding_qty: int) -> dict:
    """매도 체결 결과를 계산한다(DB 없음). 보유 수량이 모자라면 InsufficientQuantity.

    평균단가는 매도로 바뀌지 않는다(남은 수량의 매입 단가는 그대로이므로 반환값에 avg_price가 없다).
    quantity가 0이 되면(전량 매도) 호출측이 보유 레코드를 지운다 - 그래야 다음 매수가 과거 평균단가에
    영향받지 않고 새로 시작한다. 반환: {"cash_balance", "quantity"}.
    """
    if holding_qty < quantity:
        raise InsufficientQuantity(f"보유 수량이 부족합니다(매도 요청 {quantity}주, 보유 {holding_qty}주).")

    total = round(price * quantity, 2)
    return {"cash_balance": round(cash_balance + total, 2), "quantity": holding_qty - quantity}


def get_or_create_account(db, device_id: str) -> VirtualAccount:
    """디바이스ID의 첫 요청이면 초기 잔고(INITIAL_BALANCE)로 계좌를 만든다."""
    account = db.get(VirtualAccount, device_id)
    if account is None:
        account = VirtualAccount(device_id=device_id, cash_balance=INITIAL_BALANCE)
        db.add(account)
        db.commit()
        db.refresh(account)
    return account


def place_order(db, device_id: str, company: Company, side: str, quantity: int) -> dict:
    """현재가로 즉시 체결한다. 체결되면 잔고/보유 종목/거래 내역을 한 트랜잭션으로 갱신한다.

    계산 자체는 apply_buy/apply_sell이 하고, 여기서는 그 결과를 row에 반영만 한다.
    """
    price_data = get_realtime_price_data(db, company)
    if price_data is None:
        raise PriceUnavailable(f"'{company.name}'의 가격을 구하지 못해 주문을 체결할 수 없습니다.")
    price = price_data["current_price"]

    account = get_or_create_account(db, device_id)
    holding = (
        db.query(Holding)
        .filter(Holding.device_id == device_id, Holding.company_id == company.id)
        .first()
    )

    if side == SIDE_BUY:
        result = apply_buy(
            float(account.cash_balance),
            quantity,
            price,
            holding_qty=holding.quantity if holding else 0,
            holding_avg_price=float(holding.avg_price) if holding else 0.0,
        )
        account.cash_balance = result["cash_balance"]
        if holding is None:
            holding = Holding(
                device_id=device_id, company_id=company.id, quantity=result["quantity"], avg_price=result["avg_price"]
            )
            db.add(holding)
        else:
            holding.quantity = result["quantity"]
            holding.avg_price = result["avg_price"]
    else:
        result = apply_sell(float(account.cash_balance), quantity, price, holding.quantity if holding else 0)
        account.cash_balance = result["cash_balance"]
        holding.quantity = result["quantity"]
        if holding.quantity == 0:
            db.delete(holding)
            holding = None

    trade = Trade(device_id=device_id, company_id=company.id, side=side, quantity=quantity, price=price)
    db.add(trade)
    db.commit()
    db.refresh(account)
    if holding is not None:
        db.refresh(holding)

    return {
        "ticker": company.ticker,
        "company_name": company.name,
        "side": side,
        "quantity": quantity,
        "price": price,
        "executed_at": trade.executed_at.isoformat(),
        "cash_balance": float(account.cash_balance),
        "holding": None if holding is None else {"quantity": holding.quantity, "avg_price": float(holding.avg_price)},
    }


def get_portfolio(db, device_id: str) -> dict:
    """잔고 + 보유 종목(현재가/평가손익 포함). 현재가를 못 구한 종목은 평가 필드가 null이고 note로 안내한다."""
    account = get_or_create_account(db, device_id)
    rows = (
        db.query(Holding, Company)
        .join(Company, Company.id == Holding.company_id)
        .filter(Holding.device_id == device_id)
        .order_by(Holding.id)
        .all()
    )

    holdings = []
    total_eval = 0.0
    unpriced = []
    for h, c in rows:
        price_data = get_realtime_price_data(db, c)
        current_price = price_data["current_price"] if price_data else None
        avg_price = float(h.avg_price)

        eval_amount = profit_loss = profit_loss_pct = None
        if current_price is not None:
            eval_amount = round(current_price * h.quantity, 2)
            profit_loss = round((current_price - avg_price) * h.quantity, 2)
            profit_loss_pct = round((current_price / avg_price - 1) * 100, 2) if avg_price else None
            total_eval += eval_amount
        else:
            unpriced.append(c.name)

        holdings.append(
            {
                "ticker": c.ticker,
                "company_name": c.name,
                "quantity": h.quantity,
                "avg_price": avg_price,
                "current_price": current_price,
                "is_realtime": bool(price_data and price_data["is_realtime"]),
                "eval_amount": eval_amount,
                "profit_loss": profit_loss,
                "profit_loss_pct": profit_loss_pct,
            }
        )

    cash_balance = float(account.cash_balance)
    out = {
        "cash_balance": cash_balance,
        "holdings": holdings,
        "total_eval_amount": round(total_eval, 2),
        "total_asset": round(cash_balance + total_eval, 2),
    }
    if unpriced:
        out["note"] = f"{', '.join(unpriced)}의 현재가를 가져오지 못해 평가손익 계산에서 제외했습니다."
    return out
