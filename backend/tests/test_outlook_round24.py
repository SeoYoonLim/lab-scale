"""24차(2026-11-04): 후속 결론의 현재 판단 먼저, 신호 강도와 예측력, 잠정실적, 시세 지연 구분."""

from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from app import earnings, outlook, preliminary
from app.briefing import KST
from evals.forecast_check import assertion_hits, check_judgment24
from tests.test_outlook_round23 import COMPANY, NOW, W1, _er, _skhynix_like, backtest  # noqa: F401  (backtest는 픽스처)

PRELIM = ("실적기간 당기실적 2026-07-01 ~ 2026-09-30 전기실적 2026-04-01 ~ 2026-06-30 전년동기실적 2025-07-01 ~ 2025-09-30 "
          "※ 동 정보는 잠정치로서 향후 확정치와는 다를 수 있음. 1. 연결실적내용 단위 : 백만원, % 구분 당기실적 전기실적 전기대비 "
          "전년동기실적 전년동기대비 매출액 당해실적 104,615,483 79,318,746 31.9 - 24,448,929 327.9 - 누계실적 236,510,516 - - - "
          "64,320,022 267.7 - 영업이익 당해실적 83,333,818 60,542,608 37.6 - 11,383,390 632.1 - 누계실적 181,486,709 - - - "
          "28,036,745 547.3 - 법인세비용차감전계속사업이익 당해실적 50,708,500 122,708,355 -58.7 - 14,790,428 242.8 - 누계실적 1 - - - 1 1 - "
          "당기순이익 당해실적 39,775,593 93,922,593 -57.7 - 12,597,538 215.7 - 누계실적 174,044,095 - - - 27,701,949 528.3 - "
          "지배기업 소유주지분 순이익 당해실적 39,534,392 93,820,236 -57.9 - 12,595,195 213.9 - 누계실적 1 - - - 1 1 -")
TITLE = "연결재무제표기준영업(잠정)실적(공정공시)"
URL = "https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20261028800036"


# ---------- 현재 판단·강도·예측력 ----------


def test_two_up_groups_out_of_five_is_a_weak_lead():
    t = outlook.vote_tally(_skhynix_like()["signals"])
    assert t == {"up": 2, "down": 0, "neutral": 3, "total": 5}
    assert outlook.tally_text(t) == "5개 묶음 중 상승 2·하락 0·중립 3"
    assert outlook.strength_label("상승 우위", t) == "약한 상승 우위"
    assert outlook.strength_label("상승 우위", {"up": 3, "down": 0, "neutral": 2, "total": 5}) == "상승 우위"
    assert outlook.strength_label("중립", t) == "중립"


def test_power_sentence_states_sample_interval_and_no_predictive_power(backtest):
    s = outlook.power_sentence(W1, "상승 우위")
    assert "표본 22,176, 차이 +1.9%p, 95% 구간 -1.4~+5.3%p" in s and s.endswith("방향 예측력은 확인되지 않았습니다.")
    assert "예측력을 말할 수 없습니다" in outlook.power_sentence(W1, "판단 유보")



# ---------- 후속 답 종류 ----------

@pytest.mark.parametrize("q,kind", [
    ("그래서 오르냐 내리냐?", "verdict"), ("니 판단은 뭐냐?", "verdict"), ("상승이야 하락이야?", "verdict"),
    ("하나만 골라봐.", "pick"), ("굳이 하나만 고르면?", "pick"), ("둘 중 하나만 고르라면?", "pick"), ("하나만 찍어줘", "pick"),
    ("상승 근거와 하락 위험 중 어느 쪽이 더 강해?", "compare"), ("상승 근거랑 하락 근거 중 뭐가 더 세?", "compare"),
    ("그래서 살 만하다는 거야?", "buy"), ("지금 사도 된다는 거야?", "buy"),
    ("지금은 상승 가능성이 더 커, 하락 가능성이 더 커?", "probability"), ("하락 위험은 어때?", "risk"),
])
def test_followup_kinds(q, kind):
    assert outlook.is_followup_verdict_question(q) and outlook.followup_kind(q) == kind


@pytest.mark.parametrize("q,kind", [
    ("최신 실적까지 반영해서 다시 판단해줘.", "earnings"), ("3분기 실적이 전망에 어떤 영향을 주는가?", "earnings"),
    ("실적 반영하면 판단이 달라져?", "earnings"), ("최신 주가 데이터가 없으면 어떻게 답하는가?", "data"),
    ("데이터 기준일이 언제야? 최신 시세 반영된 거야?", "data"), ("삼성전자 실적 알려줘", None), ("최근 뉴스는?", None),
])
def test_special_followup_kinds(q, kind):
    assert outlook.special_followup_kind(q) == kind


def test_quarter_in_question():
    assert outlook.quarter_in("3분기 실적이 전망에 어떤 영향을 주는가?", NOW) == (2026, 3)
    assert outlook.quarter_in("4분기 실적은?", NOW) == (2025, 4)
    assert outlook.quarter_in("2025년 2분기 실적", NOW) == (2025, 2)
    assert outlook.quarter_in("최신 실적", NOW) is None


def _lines(text):
    return [x for x in text.splitlines() if x.strip()]





def test_data_followup_separates_answer_time_and_market_date(backtest):
    res = _skhynix_like()
    res["data"] = {"hold": False, "expected_date": "2026-11-04", "status": "미수집",
                   "status_detail": "장 마감 뒤 시세 수집 기록 없음, 마지막 시세 수집 2026-11-03 19:45",
                   "freshness": "1영업일 이전 시세(미수집)", "lag_business_days": 1,
                   "warnings": ["마지막 시세(2026-11-03)가 기대 거래일(2026-11-04)보다 1영업일 이전 데이터입니다."]}
    lines = _lines(outlook.followup_answer(COMPANY, W1, res, "data", NOW))
    assert lines[1] == "이 답변은 2026-11-04 18:00 KST에 만들었고, 시세 기준일은 2026-11-03 종가입니다."
    assert lines[2].startswith("- 지금 상태: 기대 거래일 2026-11-04 시세가 없습니다(미수집: 장 마감 뒤 시세 수집 기록 없음, 마지막 시세 수집 2026-11-03 19:45).")
    assert "최신 거래일 전망이 아닙니다" in lines[2] and lines[3].startswith("- 답하는 방식: ")
    assert lines[4].startswith("- 방향: 상승 쪽입니다. 다만 신호가 약하고(5개 묶음 중 상승 2·하락 0·중립 3)")
    assert "데이터 경고" not in lines[0] and "시세:" not in lines[0]


def test_data_check_warning_names_the_reason():
    st = {"status": "수집 실패", "detail": "2026-11-04 19:40 시세 수집 실패 기록"}
    d = outlook.data_check(date(2026, 11, 3), NOW, [], st)
    assert d["warnings"][0] == ("마지막 시세(2026-11-03)가 기대 거래일(2026-11-04)보다 1영업일 이전 데이터입니다(수집 실패: 2026-11-04 19:40 "
                                "시세 수집 실패 기록). 이 분석은 2026-11-03 종가 기준이며 최신 거래일 전망이 아닙니다.")
    assert d["status"] == "수집 실패" and not d["hold"] and d["freshness"] == "1영업일 이전 시세(수집 실패)"


# ---------- 잠정실적 ----------

def test_preliminary_parse_reads_three_month_values_and_source():
    p = preliminary.parse(TITLE, PRELIM)
    assert (p["bsns_year"], p["quarter"], p["basis"], p["fs_div"], p["unit"]) == (2026, 3, "연결", "CFS", "백만원")
    assert (p["revenue"], p["revenue_prev"], p["revenue_cum"]) == (104_615_483e6, 24_448_929e6, 236_510_516e6)
    assert p["net_income"] == 39_775_593e6  # 지배기업 소유주지분 순이익이 아니다
    assert preliminary.parse(TITLE + "(자회사의 주요경영사항)", PRELIM) is None
    assert preliminary.parse("[기재정정]" + TITLE, PRELIM) is None
    assert preliminary.parse("영업(잠정)실적(공정공시)", PRELIM.replace("2026-07-01 ~ 2026-09-30", "2026-09-01 ~ 2026-09-30")) is None
    tril = PRELIM.replace("단위 : 백만원", "단위 : 조원").replace("104,615,483", "104.62")
    assert preliminary.parse(TITLE, tril)["revenue"] == pytest.approx(104.62e12)
    dash = PRELIM.replace("당기순이익 당해실적 39,775,593", "당기순이익 당해실적 -")
    assert preliminary.parse(TITLE, dash)["net_income"] is None


def test_from_rows_keeps_latest_per_quarter_and_marks_corrections():
    first = PRELIM.replace("104,615,483", "104,000,000")
    rows = [(TITLE, first, "x?rcptNo=20261007800001"), (TITLE, PRELIM, URL),
            ("[기재정정]" + TITLE, "정정신고(보고) 정정관련 공시서류제출일 2026년 10월 28일 ...", "x?rcptNo=20261030800001"),
            (TITLE + "(자회사의 주요경영사항)", PRELIM.replace("2026-07-01", "2026-04-01"), "x?rcptNo=20261028800099")]
    out = preliminary.from_rows(rows)
    assert list(out) == [(2026, 3)]
    p = out[(2026, 3)]
    assert p["rcept_no"] == "20261028800036" and p["disclosed_date"] == date(2026, 10, 28) and p["revenue"] == 104_615_483e6
    assert p["corrected"]["rcept_no"] == "20261030800001"


class _Db:
    """recent_for_answer용 가짜 세션: 정기보고서 행과 잠정실적 공시 행."""

    def __init__(self, finals, prelims):
        self.finals, self.prelims, self._prelim = finals, prelims, False

    def query(self, *cols):
        self._prelim = len(cols) > 1
        return self

    def filter(self, *a):
        return self

    def order_by(self, *a):
        return self

    def all(self):
        return self.prelims if self._prelim else self.finals


def test_recent_for_answer_uses_preliminary_only_where_no_regular_report():
    finals = [_er(2026, "11013", 52_576_287e6, 37_610_283e6, 40_345_909e6), _er(2026, "11012", 79_318_746e6, 60_542_608e6, 93_922_593e6)]
    rows = earnings.recent_for_answer(_Db(finals, [(TITLE, PRELIM, URL)]), NS(id=1, asset_type="stock"))
    assert [(r["period_label"], r["source"]) for r in rows] == [("2026년 3분기", "preliminary"), ("2026년 2분기", "final")]
    line = outlook.earnings_lines(rows)[1]
    assert line == ("- 2026년 3분기(연결, 잠정): 매출액 1,046,155억 원, 전년 동기 대비 +327.9%; 영업이익 833,338억 원, 전년 동기 대비 +632.1%; "
                    "당기순이익 397,756억 원, 전년 동기 대비 +215.7% (잠정실적 공정공시, 발표일 2026-10-28, DART 접수번호 20261028800036, "
                    "감사 전 잠정치)")
    # 같은 분기 정기보고서(확정치)가 나오면 잠정치를 쓰지 않는다
    finals.append(_er(2026, "11014", 104_000_000e6, 83_000_000e6, 39_000_000e6))
    rows = earnings.recent_for_answer(_Db(finals, [(TITLE, PRELIM, URL)]), NS(id=1, asset_type="stock"))
    assert [(r["period_label"], r["source"]) for r in rows] == [("2026년 3분기", "final"), ("2026년 2분기", "final")]
    assert "(연결, 확정)" in outlook.earnings_lines(rows)[1] and "3분기보고서, 접수일" in outlook.earnings_lines(rows)[1]


def test_earnings_followup_shows_latest_quarter_source_and_reaction(backtest):
    finals = [_er(2026, "11012", 79_318_746e6, 60_542_608e6, 93_922_593e6)]
    rows = earnings.recent_for_answer(_Db(finals, [(TITLE, PRELIM, URL)]), NS(id=1, asset_type="stock"), n=8)
    extra = {"earnings": rows, "quarter": (2026, 3),
             "reaction": {"before_date": "2026-10-27", "before_close": 1_374_000.0, "base_date": "2026-11-03", "base_close": 1_414_000.0}}
    lines = _lines(outlook.followup_answer(COMPANY, W1, _skhynix_like(), "earnings", NOW, extra))
    assert len(lines) <= 6
    assert lines[1].startswith("상승 쪽입니다. 다만 ") and "실적은 신호 판정 표에 넣지 않아" in lines[1]
    assert lines[2].startswith("- 최신 실적: 2026년 3분기(연결, 잠정): 매출액 1,046,155억 원")
    assert lines[3].startswith("- 직전 실적: 2026년 2분기(연결, 확정)")
    assert lines[4].startswith("- 발표 뒤 주가: 발표일(2026-10-28) 직전 거래일 2026-10-27 종가 1,374,000원 → 기준일 2026-11-03 종가 "
                               "1,414,000원(+2.91%).")
    extra["quarter"] = (2026, 4)
    text = outlook.followup_answer(COMPANY, W1, _skhynix_like(), "earnings", NOW, extra)
    assert "- 2026년 4분기 실적: 아직 공시(잠정·확정)가 없습니다." in text


def test_price_status_kinds():
    """미수집 / 수집 실패 / 휴장일 추정 / 종목만 누락 / 최신."""
    now = datetime(2026, 11, 4, 18, 0, tzinfo=KST)

    class Q:
        def __init__(self, idx, runs):
            self.idx, self.runs, self.kind = idx, runs, None

        def query(self, *a):
            self.kind = "idx" if "max" in str(a[0]) else "runs"
            return self

        def filter(self, *a):
            return self

        def order_by(self, *a):
            return self

        def limit(self, n):
            return self

        def scalar(self):
            return self.idx

        def all(self):
            return self.runs

    run = lambda st, h: NS(status=st, started_at=datetime(2026, 11, 4, h, 0, tzinfo=KST))  # noqa: E731
    base = date(2026, 11, 3)
    assert outlook.price_status(Q(base, []), date(2026, 11, 4), now)["status"] == "최신"
    assert outlook.price_status(Q(date(2026, 11, 4), []), base, now)["status"] == "종목만 누락"
    assert outlook.price_status(Q(base, [run("failed", 19)]), base, now)["status"] == "수집 실패"
    assert outlook.price_status(Q(base, [run("success", 19)]), base, now)["status"] == "휴장일 추정"
    st = outlook.price_status(Q(base, [run("success", 10)]), base, now)  # 장 마감 전 실행은 그날 시세를 받을 수 없다
    assert st["status"] == "미수집" and "마지막 시세 수집 2026-11-04 10:00" in st["detail"]


def test_basis_comes_from_the_disclosures_own_sentence_not_any_mention():
    """한화: 별도 잠정치 공시에 "연결재무제표기준 영업실적은 반기보고서를 참조" 문장이 있다. 현대제철: 같은 날 연결·별도를 따로 낸다."""
    body = PRELIM.replace("1. 연결실적내용", "1. 실적내용")
    hanwha = body + " 상기 자료는 ㈜한화의 별도재무제표기준 영업(잠정)실적입니다. - ㈜한화 연결재무제표기준 영업실적은 반기보고서를 참조"
    assert preliminary.parse("영업(잠정)실적(공정공시)", hanwha)["basis"] == "별도"
    assert preliminary.parse("영업(잠정)실적(공정공시)", body + " 별도기준의 영업(잠정) 실적입니다.")["basis"] == "별도"
    assert preliminary.parse("영업(잠정)실적(공정공시)", body)["basis"] == "기준 미표기"
    rows = [(TITLE, PRELIM, URL), ("영업(잠정)실적(공정공시)", body.replace("104,615,483", "50,000,000") + " 별도기준의 영업(잠정) 실적",
                                   "x?rcptNo=20261028800040")]
    assert preliminary.from_rows(rows, "연결")[(2026, 3)]["revenue"] == 104_615_483e6
    assert preliminary.from_rows(rows, "별도")[(2026, 3)]["revenue"] == 50_000_000e6


def test_preliminary_needs_cumulative_consistency():
    p = preliminary.parse(TITLE, PRELIM)
    assert preliminary.cumulative_ok(p, 131_895_033e6)  # 반기 누적 + 3분기 = 3분기 누계
    assert not preliminary.cumulative_ok(p, 100_000_000e6)
    assert preliminary.cumulative_ok(p, None)
    finals = [_er(2026, "11013", 52_576_287e6, 37_610_283e6, 40_345_909e6, cum=(52_576_287e6, 37_610_283e6, 40_345_909e6)),
              _er(2026, "11012", 79_318_746e6, 60_542_608e6, 93_922_593e6, cum=(100_000_000e6, 1, 1))]
    rows = earnings.recent_for_answer(_Db(finals, [(TITLE, PRELIM, URL)]), NS(id=1, asset_type="stock"))
    assert rows[0]["period_label"] == "2026년 2분기"  # 누적이 안 맞는 잠정치는 쓰지 않는다
