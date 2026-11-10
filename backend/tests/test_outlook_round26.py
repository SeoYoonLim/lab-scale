"""26차(2026-11-04 밤): 간결·상세 모드, 첫 줄 방향 배치, 근거와 묶음 판정 일치, 뉴스·공시 날짜, 공시 날짜·신호 의미 후속 답."""

from datetime import date, datetime, timezone

import pytest

from app import outlook
from evals.forecast_check import check_evidence26, check_layout26, check_mode26
from tests.test_outlook_round23 import COMPANY, NOW, W1, _now_for, _result, _rows, _skhynix_like, backtest  # noqa: F401  (backtest는 픽스처)


def _lines(text):
    return [x for x in text.splitlines() if x.strip()]


def _res():
    res = _skhynix_like()
    for s in res["signals"]:
        s.setdefault("value", "-")
        s.setdefault("rule", "-")
    res["data"] = {"hold": False, "warnings": [], "lag_business_days": 0, "missing_days": 0, "expected_date": "2026-11-03"}
    return res


# ---------- 근거와 묶음 판정 ----------

def test_evidence_comes_from_the_group_votes(backtest):
    res = _res()
    votes = outlook.group_votes(res["signals"])
    ge = outlook.group_evidence(res["indicators"], res["signals"], W1)
    tag = lambda x: x[1:x.index("]")]  # noqa: E731
    assert all(votes[tag(x)] == outlook.UP for x in ge["up"])        # 상승 근거 = 상승 쪽 묶음
    assert all(votes[tag(x)] == outlook.DOWN for x in ge["down"])    # 하락 위험 = 하락 쪽 묶음
    assert all(votes[tag(x)] == outlook.NEUTRAL for x in ge["mixed"])
    # 중기 추세 묶음은 중립: 그 안의 골든크로스·5일선>20일선(상승)은 상승 근거로 따로 서지 않는다
    assert not any("골든크로스" in x for x in ge["up"]) and "골든크로스" in ge["mixed"][0]
    # 같은 사실(교차와 이동평균 배열)은 한 문장: 골든크로스가 두 번 나오지 않는다
    assert sum(x.count("골든크로스") for x in ge["up"] + ge["down"] + ge["mixed"]) == 1
    # 개별 신호 합(상승 5개)과 묶음 표(상승 2)를 섞지 않는다: 상승 근거 항목 수 = 상승 묶음 수
    assert len(ge["up"]) == sum(v == outlook.UP for v in votes.values()) == 2
    assert sum(s["side"] == outlook.UP for s in res["signals"]) == 5
    assert ge["context"] and all("[" not in x for x in ge["context"])  # 추세 참고는 묶음 표시가 없다


def test_brief_and_detail_share_direction_and_evidence(backtest):
    rows = _rows()
    res, now = _result(rows), _now_for(rows)  # 상세 답을 만들 수 있는 전체 지표
    brief = outlook.brief_answer(COMPANY, W1, res, now)
    detail = outlook.forecast_answer(COMPANY, W1, res, [], [], None, now)
    assert brief.splitlines()[0] == detail.splitlines()[0]  # 같은 방향·신뢰도
    ge = outlook.group_evidence(res["indicators"], res["signals"], W1)
    assert next(x for x in brief.splitlines() if x.startswith("- 상승 근거: ")) == f"- 상승 근거: {outlook._support_text(ge)}"
    assert next(x for x in brief.splitlines() if x.startswith("- 하락 위험: ")) == f"- 하락 위험: {outlook._risk_text(ge)}"
    up_sec = detail.split("## 상승을 뒷받침하는 근거\n")[1].split("\n\n")[0].splitlines()
    assert up_sec == outlook.evidence_section_lines(ge)[0]
    assert check_evidence26(brief, "brief") == [] and check_evidence26(detail, "detail") == []


# ---------- 간결 모드 ----------

def test_brief_answer_layout(backtest):
    text = outlook.brief_answer(COMPANY, W1, _res(), NOW)
    lines = _lines(text)
    assert len(lines) <= 8 and "## " not in text
    assert lines[0] == "SK하이닉스(000660) · 향후 5거래일 · 방향: 상승 쪽(신뢰도 낮음)"
    assert lines[1].startswith("상승 쪽입니다. 다만 신호가 약하고(5개 묶음 중 상승 2·하락 0·중립 3)")
    assert lines[3].startswith("- 하락 위험: 하락 쪽으로 판정된 묶음은 없습니다. 엇갈린 묶음: [중기 추세] ")
    assert "- SK하이닉스(000660), 분석 기간 1주일(5거래일): 상승 우위" in lines
    past = next(x for x in lines if x.startswith("- 과거 검증: "))
    assert "표본 22,176" in past and "예측력은 확인되지 않았습니다" in past and "확률이나 예측이 아닙니다" in past
    assert next(x for x in lines if x.startswith("- 시장 국면: ")).startswith(
        "- 시장 국면: 현재는 단기 반등 시도, 추세 전환은 미확인 (기준일 2026-11-03 종가) · 기준일(최신 거래일): 2026-11-03")
    assert "투자 권유가 아닙니다" in lines[-1] and "자세히 분석해줘" in lines[-1]
    assert check_mode26(text, "brief") == [] and check_layout26(text, "SK하이닉스", "1주일", "brief") == []


def test_brief_keeps_the_data_warning(backtest):
    res = _res()
    res["data"].update(lag_business_days=1, warnings=["마지막 시세(2026-11-03)가 기대 거래일(2026-11-04)보다 1영업일 이전 데이터입니다."])
    text = outlook.brief_answer(COMPANY, W1, res, NOW)
    assert "데이터 경고: 마지막 시세(2026-11-03)가 기대 거래일(2026-11-04)보다 1영업일 이전 데이터입니다." in text
    assert text.count("1영업일 이전 데이터") == 1  # 경고는 한 번


# ---------- 상세 모드 ----------

def test_detail_intro_puts_direction_first_then_warning_then_regime(backtest):
    rows = _rows()
    res, now = _result(rows), _now_for(rows)
    res["data"]["warnings"] = ["마지막 시세(2026-11-03)가 기대 거래일(2026-11-04)보다 1영업일 이전 데이터입니다."]
    lines = outlook.forecast_answer(COMPANY, W1, res, [], [], None, now).splitlines()
    assert lines[2].startswith(("상승 쪽입니다.", "하락 쪽입니다.", "현재는 상승과 하락 중", "데이터가 부족해"))
    assert lines[3].startswith("데이터 경고: ") and lines[4].startswith("현재는 ")
    text = "\n".join(lines)
    assert "근거 요약:" not in text  # 종합 판단·판정 상세와 겹치던 줄을 뺐다
    assert text.count("1영업일 이전 데이터") == 1  # 출처 섹션에서 경고를 다시 쓰지 않는다
    assert check_layout26(text, "SK하이닉스", "1주일", "detail") == [] and check_mode26(text, "detail") == []
    assert check_evidence26(text, "detail") == []


def test_detail_headline_for_a_weak_lead(backtest):
    assert outlook.direction_headline(_res(), outlook.REGIME_REBOUND, W1) == (
        "상승 쪽입니다. 다만 신호가 약하고 신뢰도는 낮습니다(5개 묶음 중 상승 2·하락 0·중립 3).")


# ---------- 날짜 ----------

def test_dates_are_kst_and_disclosures_use_the_receipt_number():
    assert outlook.kst_date("2026-10-27T15:00:00+00:00") == "2026-10-28"
    assert outlook.kst_date(datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)) == "2026-10-28"
    assert outlook.disclosure_date("https://dart.fss.or.kr/dsaf001/main.do?rcptNo=20261028800036", "2026-10-27T15:00:00+00:00") == "2026-10-28"
    assert outlook.disclosure_date(None, "2026-10-27T15:00:00+00:00") == "2026-10-28"
    disc = [{"title": "연결재무제표기준영업(잠정)실적(공정공시)", "disclosed_at": "2026-10-27T15:00:00+00:00",
             "source_url": "x?rcptNo=20261028800036"}]
    news = [{"title": "SK하이닉스 컨콜", "published_at": "2026-10-27T23:30:00+00:00"}]
    lines = outlook.evidence_lines(news, disc, False, W1)
    assert lines[0].startswith("- 뉴스 2026-10-28 ") and lines[1] == "- 공시 2026-10-28 접수: \"연결재무제표기준영업(잠정)실적(공정공시)\""
    facts = outlook.fact_lines(news, disc, None)
    assert '  - 2026-10-28 "연결재무제표기준영업(잠정)실적(공정공시)" 공시가 있었습니다.' in facts


# ---------- 질문 분류 ----------

@pytest.mark.parametrize("q,detail", [
    ("하이닉스 일주일 안에 오를 것 같아?", False), ("다음 주 전망 알려줘", False), ("앞으로 한 달은 어떨까?", False),
    ("자세히 분석해 줘", True), ("SK하이닉스 일주일 전망을 자세히 분석해줘", True), ("기술적 지표까지 확인해 줘", True),
    ("뉴스와 공시까지 모두 반영해 줘", True), ("투자 리서치 보고서 형태로 작성해 줘", True),
])
def test_detail_or_brief(q, detail):
    assert outlook.wants_detail(q) is detail


@pytest.mark.parametrize("q,forecast", [
    ("삼성전자 뉴스와 공시까지 모두 반영해서 다음 주 전망 분석해줘", True), ("LG전자 실적까지 포함해서 한 달 전망 리포트로 써줘", True),
    ("SK하이닉스 기술적 지표까지 분석해줘", True), ("삼성전자 실적 알려줘", False), ("삼성전자 최근 뉴스 알려줘", False),
    ("LG전자 10주 매수해줘", False),
])
def test_detail_requests_with_news_or_earnings_stay_on_the_forecast_path(q, forecast):
    assert outlook.is_forecast_question(q) is forecast


@pytest.mark.parametrize("q,kind", [
    ("전체적으로 자세히 분석해 줘.", "detail"), ("기술적 지표까지 확인해줘", "detail"), ("3분기 잠정실적 공시는 언제 접수됐어?", "filing"),
    ("골든크로스가 무슨 뜻이야?", "signal"), ("최신 실적까지 반영해서 다시 판단해줘.", "earnings"), ("데이터 기준일이 언제야?", "data"),
    ("왜 그렇게 생각하는데?", "why"),
])
def test_special_followups(q, kind):
    assert outlook.special_followup_kind(q) == kind


# ---------- 공시 날짜·신호 의미 ----------

def test_filing_answer_states_the_receipt_date_first():
    rows = [{"source": "preliminary", "period_label": "2026년 3분기", "disclosed_date": "2026-10-28", "rcept_no": "20261028800036",
             "basis": "연결", "revenue": 1.04615483e14, "revenue_prev": 2.4448929e13, "corrected": None},
            {"source": "final", "period_label": "2026년 2분기", "disclosed_date": "2026-08-14", "rcept_no": "20260814003509",
             "reprt_code": "11012", "fs_div": "CFS", "revenue": 7.9e13}]
    lines = _lines(outlook.filing_answer(COMPANY, "제목", rows))
    assert lines[1] == ("SK하이닉스의 최신 실적 공시는 2026-10-28에 접수된 2026년 3분기 잠정실적 공정공시입니다"
                        "(DART 접수번호 20261028800036, 거래소 공시라 접수일이 곧 발표일입니다).")
    assert "접수·발표일 2026-10-28" in lines[2]
    assert lines[3] == "- 정기보고서: 2026년 3분기 정기보고서는 아직 수집되지 않았습니다. 최근 정기보고서는 2026년 2분기(반기보고서, 2026-08-14 접수)입니다."


def test_signal_answer_separates_the_signal_from_its_group(backtest):
    res = _res()
    text = outlook.signal_answer("제목", res, W1, ("cross", "골든크로스"))
    lines = _lines(text)
    assert lines[1].startswith("골든크로스: 골든크로스는 단기 이동평균선이 장기 이동평균선을 아래에서 위로")
    assert lines[3].startswith("- 판정: 개별 신호로는 상승 쪽이고, 이 신호가 속한 중기 추세 묶음은 중립입니다.")
    assert "따로 세지 않습니다" in lines[3]
