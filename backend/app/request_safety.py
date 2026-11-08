"""확정할 수 없는 것을 확정해 달라는 요청(2026-11-07 35차). LLM에 보내지 않고 요청 종류별 안내 + 지원하는 대안 정보로 답한다.

33차 live 평가: "삼성전자 목표주가 확정해줘"가 단순 시세 경로로 가서 "222,000원입니다"만 답했다(거절·면책 없음). "SK하이닉스 내일 얼마까지
올라?", "이 종목 수익률 20% 보장 가능해?"도 같은 부류다. 종류마다 안 되는 이유와 대신 볼 수 있는 정보가 다르므로 같은 문장으로 거절하지 않는다.

- target_price: 목표주가 확정·제시 요청. 이 서비스는 목표주가를 만들지 않는다(증권사 목표가도 수집하지 않는다).
- price_prediction: 특정 시점의 가격·상승 폭을 정확히 알려 달라는 요청. 미래 가격을 계산하지 않는다(전망 경로는 방향 신호만).
- guarantee: 수익·상승 보장/확신 요청. 보장할 수 없다.
- timing_general: 종목 없이 "주식은 언제 사는 게 좋아?" 같은 매수 시점 일반 질문. 시점을 추천하지 않고 판단에 쓰는 일반 기준만.

종목이 정확히 하나면 호출측(app.agent)이 저장된 최신 종가 한 줄과 기준일·지연 안내(app.quote_freshness)를 reference로 넘긴다.
목표가·확률·기대수익률 숫자는 어떤 경우에도 쓰지 않는다.
"""

from __future__ import annotations

import re

from app.disclaimer import DISCLAIMER

_TARGET_RE = re.compile(r"목표\s*(?:주가|가격|가)(?!\s*(?:뜻|의미|란|이란|가\s*뭐|는\s*뭐|이\s*뭐))")
_FUTURE_WHEN = (r"내일|모레|다음\s*(?:주|달|날|거래일)|이번\s*주|한\s*(?:달|주)\s*(?:뒤|후)|일주일\s*(?:뒤|후)|"
                r"\d+\s*(?:일|주|개월|달|년)\s*(?:뒤|후)|연말|내년|올해\s*말|장\s*마감\s*때")
_PRICE_ASK = r"얼마|몇\s*원|정확"  # 가격을 정확히 묻는 말만("다음 주 주가 전망"은 전망 경로다)
_PREDICTION_RE = re.compile(rf"(?:{_FUTURE_WHEN}).{{0,20}}(?:{_PRICE_ASK})|얼마까지\s*(?:오를|올라|오르|떨어|내려|빠질)|"
                            r"정확(?:하게|히)\s*(?:알려|예측|맞춰)")
_GUARANTEE_RE = re.compile(r"보장|무조건|확실(?:히|하게)?\s*(?:오르|올라|수익|벌)|100\s*%\s*(?:오르|수익|상승)|"
                           r"절대\s*(?:안\s*떨어|손해)|손해\s*(?:안|없)|틀림없이\s*(?:오르|올라)")
_TIMING_RE = re.compile(r"(?:언제|어느\s*때|어떤\s*때|타이밍|시점).{0,12}(?:사|매수|사는|살|팔|매도)|"
                        r"(?:사|매수|팔|매도)(?:는|할)\s*(?:시점|타이밍|때)")


def classify(question: str, has_company: bool) -> str | None:
    q = question or ""
    if _TARGET_RE.search(q) and re.search(r"확정|정해|알려|제시|얼마|몇|예상|맞춰|줘", q):
        return "target_price"
    if _GUARANTEE_RE.search(q):
        return "guarantee"
    if _PREDICTION_RE.search(q):
        return "price_prediction"
    if not has_company and _TIMING_RE.search(q):
        return "timing_general"
    return None


_ALTERNATIVES_WITH = (
    "대신 볼 수 있는 정보:\n"
    "- 저장된 최근 종가와 기준일(아래 참고 시세)\n"
    "- \"{name} 최근 20거래일 주가 흐름 분석해줘\": 수익률·이동평균·거래량과 현재 국면\n"
    "- \"{name} 다음 주 전망 알려줘\": 과거 신호 기반 방향 판단과 근거(확정 예측이 아님)\n"
    "- \"{name} 최근 공시와 뉴스 알려줘\", \"{name} 최근 실적 알려줘\": 위험 요인 확인"
)
_ALTERNATIVES_WITHOUT = (
    "대신 종목 이름을 넣어 물어보면 볼 수 있는 정보:\n"
    "- \"삼성전자 최근 20거래일 주가 흐름 분석해줘\": 수익률·이동평균·거래량과 현재 국면\n"
    "- \"삼성전자 다음 주 전망 알려줘\": 과거 신호 기반 방향 판단과 근거(확정 예측이 아님)\n"
    "- \"삼성전자 최근 공시와 뉴스 알려줘\": 위험 요인 확인"
)
_HEAD = {
    "target_price": ("목표주가는 확정할 수 없습니다. 이 서비스는 목표주가를 계산하거나 제시하지 않고, 증권사 목표주가도 수집하지 않습니다. "
                     "목표주가는 작성자의 가정(실적 전망·적용 배수)에 따라 달라지는 의견이라 사실처럼 정할 수 없습니다."),
    "price_prediction": ("특정 날짜의 주가나 오를 수 있는 가격을 정확히 알려 드릴 수 없습니다. 미래 가격은 예측할 수 없고, "
                         "이 서비스는 미래 가격·상승 확률·기대수익률을 계산하지 않습니다."),
    "guarantee": ("수익이나 상승은 보장할 수 없습니다. 주식은 원금 손실이 날 수 있고, 과거 흐름이 같은 결과를 보장하지 않습니다. "
                  "이 서비스는 특정 수익률을 약속하거나 '무조건 오른다'고 단정하지 않습니다."),
    "timing_general": ("주식을 사기에 좋은 시점을 정해 드릴 수는 없습니다. 이 서비스는 매수·매도 시점을 추천하지 않습니다. "
                       "일반적으로는 다음을 스스로 점검합니다: 투자 기간과 목적, 잃어도 되는 금액, 한 번에 사지 않고 나눠 사는지(분할 매수), "
                       "한 종목에 몰리지 않았는지(분산), 실적·공시 같은 근거를 확인했는지."),
}


def answer(kind: str, company_name: str | None, reference: str | None = None) -> str:
    """요청 종류별 안내 + 대안 + (참고 시세) + 면책. reference는 호출측이 만든 참고 시세·기준일 안내(종목이 하나일 때)."""
    parts = [_HEAD[kind]]
    if kind != "timing_general":
        parts.append(_ALTERNATIVES_WITH.format(name=company_name) if company_name else _ALTERNATIVES_WITHOUT)
    elif company_name is None:
        parts.append("종목 이름을 넣어 \"삼성전자 최근 20거래일 주가 흐름 분석해줘\"처럼 물어보면 그 종목의 실제 데이터를 보여 드립니다.")
    if reference:
        parts.append(reference)
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)
