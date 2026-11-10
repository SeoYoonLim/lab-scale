"""36차(2026-11-08): 저장 전 검증(app/ingest_validation)·격리 payload(app/quarantine)·KRX 거래일 달력(app/krx_calendar)과
그 연결(data_status·outlook·quote_freshness·data_monitor·refresh_data·period)의 순수 테스트. DB·외부 API 없이 돈다(합성 데이터).
DB에 실제로 격리·재처리하는 테스트는 tests/test_quarantine_devdb.py."""

import json
from datetime import date, datetime, timedelta

import pytest

from app import data_monitor as DM
from app import ingest_validation as V
from app import krx_calendar as K
from app import quarantine as Q
from app import quote_freshness
from app.data_status import business_days_behind, is_stale, last_business_day, weekdays_behind
from app.ingest_validation import KST

TODAY = date(2026, 11, 6)  # 금요일(거래일)


def px(d, close=100.0, o=None, h=None, lo=None, vol=1000):
    return {"price_date": d, "open_price": o if o is not None else close, "high_price": h if h is not None else close,
            "low_price": lo if lo is not None else close, "close_price": close, "volume": vol, "change_pct": None}


def statuses(checked):
    return [c.status for c in checked]


def codes(checked):
    return [c.verdict.codes for c in checked]


# ======================= A. 데이터 검증 =======================


class TestPrices:
    def test_normal_prices_are_valid(self):  # 1
        got = V.check_prices("005930", [px(date(2026, 11, 4), 100), px(date(2026, 11, 5), 103)], today=TODAY)
        assert statuses(got) == [V.VALID, V.VALID] and got[0].key == "stock_price:005930:2026-11-04"

    @pytest.mark.parametrize("close,code", [(0, "close_nonpositive"), (-5, "close_nonpositive"), (None, "close_missing")])
    def test_nonpositive_or_missing_close_is_quarantined(self, close, code):  # 2
        got = V.check_prices("005930", [{**px(date(2026, 11, 4)), "close_price": close}], today=TODAY)
        assert got[0].status == V.QUARANTINED and code in got[0].verdict.codes

    @pytest.mark.parametrize("ticker", ["5930", "삼성전자", "", None, "00593O0"])
    def test_invalid_ticker_is_quarantined(self, ticker):  # 3
        got = V.check_prices(ticker, [px(date(2026, 11, 4))], today=TODAY)
        assert got[0].status == V.QUARANTINED and "invalid_ticker" in got[0].verdict.codes

    def test_etf_style_ticker_is_valid(self):
        assert V.valid_ticker("0080G0") and V.valid_ticker("069500")

    def test_future_date_is_quarantined(self):  # 4
        got = V.check_prices("005930", [px(date(2026, 11, 9))], today=TODAY)
        assert got[0].status == V.QUARANTINED and "future_date" in got[0].verdict.codes

    def test_duplicate_dates(self):  # 5
        d = date(2026, 11, 5)
        same = V.check_prices("005930", [px(d, 100), px(d, 100)], today=TODAY)
        assert statuses(same) == [V.VALID, V.QUARANTINED] and same[1].verdict.codes == ["duplicate_same"]
        conflict = V.check_prices("005930", [px(d, 100), px(d, 120)], today=TODAY)
        assert statuses(conflict) == [V.QUARANTINED, V.QUARANTINED]  # 어느 쪽이 맞는지 모르면 둘 다 저장하지 않는다

    def test_large_real_move_is_a_warning_not_quarantine(self):  # 6
        got = V.check_prices("005930", [px(date(2026, 11, 5), 133)], prev_close=100.0, today=TODAY)
        assert got[0].status == V.WARNING and got[0].verdict.codes == ["extreme_move"]
        within = V.check_prices("005930", [px(date(2026, 11, 5), 130)], prev_close=100.0, today=TODAY)  # 상한가 30%
        assert within[0].status == V.VALID

    def test_negative_volume_is_quarantined(self):  # 7
        got = V.check_prices("005930", [px(date(2026, 11, 5), vol=-1)], today=TODAY)
        assert "volume_negative" in got[0].verdict.codes and got[0].status == V.QUARANTINED

    def test_ohlc_contradiction_and_suspension_rows(self):
        bad = V.check_prices("005930", [px(date(2026, 11, 5), 120, h=110, lo=100)], today=TODAY)
        assert "ohlc_inconsistent" in bad[0].verdict.codes
        halted = V.check_prices("005930", [px(date(2026, 11, 5), 100, o=0, h=0, lo=0, vol=0)], today=TODAY)  # 거래정지일
        assert halted[0].status == V.VALID

    def test_non_trading_day_is_quarantined(self):
        got = V.check_prices("005930", [px(date(2026, 10, 9)), px(date(2026, 11, 7))], today=TODAY)  # 한글날, 토요일
        assert all("non_trading_day" in c for c in codes(got))
        unknown = V.check_prices("005930", [px(date(2026, 12, 31))], today=date(2026, 12, 31))  # 미확정 날은 막지 않는다
        assert unknown[0].status == V.VALID

    def test_existing_value_is_protected(self):  # 15(판정)
        d = date(2026, 11, 5)
        got = V.check_prices("005930", [px(d, 50)], existing={d: 100.0}, today=TODAY)
        assert got[0].status == V.QUARANTINED and "conflicts_existing" in got[0].verdict.codes
        small = V.check_prices("005930", [px(d, 101)], existing={d: 100.0}, today=TODAY)  # 장중 값 -> 종가 갱신은 정상
        assert small[0].status == V.VALID

    def test_mixed_batch_keeps_good_records(self):  # 16
        recs = [px(date(2026, 11, 3), 100), px(date(2026, 11, 4), 0), px(date(2026, 11, 5), 101)]
        keep, bad = V.split(V.check_prices("005930", recs, today=TODAY))
        assert [c.record["price_date"].day for c in keep] == [3, 5] and [c.record["price_date"].day for c in bad] == [4]


class TestIndexAndFx:
    def test_index_rules(self):
        ok = V.check_index("KS11", [px(date(2026, 11, 5), 4000)], today=TODAY)
        assert ok[0].status == V.VALID
        assert "unsupported_index" in V.check_index("KS200", [px(date(2026, 11, 5), 400)], today=TODAY)[0].verdict.codes
        assert V.check_index("KQ11", [px(date(2026, 11, 5), 0)], today=TODAY)[0].status == V.QUARANTINED
        jump = V.check_index("KS11", [px(date(2026, 11, 5), 4400)], prev_close=4000, today=TODAY)
        assert jump[0].status == V.WARNING

    def test_fx_nonpositive_and_pair_mismatch(self):  # 14
        assert V.check_fx("USD/KRW", [px(date(2026, 11, 5), 0)], today=TODAY)[0].verdict.codes == ["rate_nonpositive"]
        flipped = V.check_fx("USD/KRW", [px(date(2026, 11, 5), 0.00072)], today=TODAY)
        assert flipped[0].status == V.QUARANTINED and "pair_mismatch" in flipped[0].verdict.codes
        other = V.check_fx("EUR/JPY", [px(date(2026, 11, 5), 160)], today=TODAY)[0]  # 범위를 모르는 쌍: 막지 않고 경고
        assert other.status == V.WARNING and other.verdict.codes == ["unknown_pair_range"]

    def test_fx_does_not_use_krx_calendar(self):
        """출처가 일요일·한국 휴장일 날짜로도 값을 준다(2026-04~11 dev DB 실측). 격리하지 않는다."""
        got = V.check_fx("USD/KRW", [px(date(2026, 10, 4), 1390), px(date(2026, 10, 5), 1392)], today=TODAY)
        assert statuses(got) == [V.VALID, V.VALID]

    def test_fx_existing_conflict_and_jump(self):
        d = date(2026, 11, 5)
        assert V.check_fx("USD/KRW", [px(d, 1500)], existing={d: 1400.0}, today=TODAY)[0].status == V.QUARANTINED
        assert V.check_fx("USD/KRW", [px(d, 1480)], prev_close=1400.0, today=TODAY)[0].status == V.WARNING


NOW = datetime(2026, 11, 6, 18, 0, tzinfo=KST)


class TestNews:
    def item(self, **k):
        return {"title": "가나다전자, 신공장 착공", "url": "https://news.example.com/a/1", "source": "news.example.com",
                "published_at": datetime(2026, 11, 6, 9, 0, tzinfo=KST), "content": "본문", **k}

    def test_valid_news(self):
        assert V.check_news("005930", [self.item()], NOW)[0].status == V.VALID

    def test_missing_url_is_quarantined(self):  # 12
        assert V.check_news("005930", [self.item(url=None)], NOW)[0].verdict.codes == ["url_missing"]
        assert V.check_news("005930", [self.item(url="javascript:alert(1)")], NOW)[0].verdict.codes == ["url_invalid"]

    def test_company_link_error_is_quarantined(self):  # 13
        assert "company_missing" in V.check_news(None, [self.item()], NOW)[0].verdict.codes

    def test_other_rules(self):
        assert V.check_news("005930", [self.item(title=" ")], NOW)[0].verdict.codes == ["title_missing"]
        assert V.check_news("005930", [self.item(published_at=None)], NOW)[0].verdict.codes == ["published_at_invalid"]
        future = self.item(published_at=NOW + timedelta(hours=3))
        assert V.check_news("005930", [future], NOW)[0].verdict.codes == ["future_published"]
        old = self.item(published_at=NOW - timedelta(days=45))
        assert V.check_news("005930", [old], NOW)[0].status == V.WARNING

    def test_same_story_from_other_outlets_is_kept(self):
        """같은 사건의 다른 언론사 기사(url이 다름)는 중복이 아니다. 같은 url만 한 번."""
        items = [self.item(), self.item(url="https://other.example.com/x", title="가나다전자 신공장 착공 소식"), self.item()]
        assert statuses(V.check_news("005930", items, NOW)) == [V.VALID, V.VALID, V.QUARANTINED]


class TestDisclosures:
    def item(self, **k):
        return {"rcept_no": "20261105000123", "rcept_dt": "20261105", "report_nm": "주요사항보고서(자기주식취득결정)",
                "corp_name": "가나다전자", **k}

    def test_correction_disclosure_is_valid(self):  # 10
        got = V.check_disclosures("005930", [self.item(report_nm="[기재정정]주요사항보고서(자기주식취득결정)",
                                                        rcept_no="20261105000456")], NOW)
        assert got[0].status == V.VALID and V.is_correction(got[0].record["report_nm"])

    @pytest.mark.parametrize("rno,rdt", [("20260929000686", "20260930"), ("20261002000673", "20261006"),
                                         ("20260713000496", "20260714"), ("20260831001545", "20260901"),
                                         ("20260709000697", "20260710")])
    def test_dev_db_receipt_date_cases_are_next_business_day_warnings(self, rno, rdt):
        """34차 dev DB의 접수번호 날짜≠접수일 5건(정정 4·원본 1): 모두 다음 영업일 접수. 격리하지 않는다."""
        got = V.check_disclosures("005930", [self.item(rcept_no=rno, rcept_dt=rdt)], NOW)
        assert got[0].status == V.WARNING and got[0].verdict.codes == ["receipt_next_business_day"]

    def test_bad_disclosures_are_quarantined(self):  # 11
        assert "receipt_invalid" in V.check_disclosures("005930", [self.item(rcept_no="12345")], NOW)[0].verdict.codes
        assert "receipt_date_invalid" in V.check_disclosures("005930", [self.item(rcept_dt="2026-11-05")], NOW)[0].verdict.codes
        assert "future_disclosed" in V.check_disclosures("005930", [self.item(rcept_no="20261120000001", rcept_dt="20261120")], NOW)[0].verdict.codes
        assert "title_missing" in V.check_disclosures("005930", [self.item(report_nm="")], NOW)[0].verdict.codes
        assert "company_missing" in V.check_disclosures(None, [self.item()], NOW)[0].verdict.codes
        other = V.check_disclosures("005930", [self.item(rcept_no="20261020000001", rcept_dt="20261105")], NOW)
        assert other[0].status == V.WARNING and other[0].verdict.codes == ["receipt_date_mismatch"]


class TestEtfSnapshot:
    H = [{"name": "삼성전자", "code": "005930", "weight_pct": 30.0}, {"name": "SK하이닉스", "code": "000660", "weight_pct": 70.0}]

    def test_normal_snapshot(self):
        assert V.check_etf_snapshot("069500", "KODEX 200", date(2026, 11, 5), self.H, 100.0, TODAY).status == V.VALID

    def test_negative_weight_is_a_warning(self):  # 8
        h = self.H + [{"name": "코스피200 선물", "code": None, "weight_pct": -5.0}, {"name": "현금", "code": None, "weight_pct": 5.0}]
        v = V.check_etf_snapshot("069500", "KODEX 200", date(2026, 11, 5), h, 100.0, TODAY)
        assert v.status == V.WARNING and v.codes == ["negative_weight"]

    def test_wrong_weight_sums(self):  # 9
        zero = [{"name": "a", "code": "1", "weight_pct": 0.0}]
        assert V.check_etf_snapshot("069500", "KODEX 200", date(2026, 11, 5), zero, 100.0, TODAY).codes == ["nonpositive_weight_sum"]
        doubled = [{**h, "weight_pct": h["weight_pct"] * 2} for h in self.H]  # 합계 200%: 중복 집계
        assert V.check_etf_snapshot("069500", "KODEX 200", date(2026, 11, 5), doubled, 100.0, TODAY).status == V.QUARANTINED
        lev = V.check_etf_snapshot("122630", "KODEX 레버리지", date(2026, 11, 5), doubled, 200.0, TODAY)  # 레버리지는 200%가 정상
        assert lev.status == V.VALID
        low = [{**self.H[0]}, {**self.H[1], "weight_pct": 50.0}]  # 80%: 현금 제외 등
        assert V.check_etf_snapshot("069500", "KODEX 200", date(2026, 11, 5), low, 100.0, TODAY).codes == ["weight_sum_out_of_range"]

    def test_old_as_of_is_not_an_error_but_future_is(self):
        """오래된 데이터와 잘못된 데이터는 다르다."""
        assert V.check_etf_snapshot("069500", "KODEX 200", date(2025, 1, 2), self.H, 100.0, TODAY).status == V.VALID
        assert "future_as_of" in V.check_etf_snapshot("069500", "KODEX 200", date(2026, 11, 20), self.H, 100.0, TODAY).codes
        assert "empty_snapshot" in V.check_etf_snapshot("069500", "KODEX 200", date(2026, 11, 5), [], 100.0, TODAY).codes


class TestPayload:
    def test_payload_keeps_only_whitelisted_fields(self):
        p, cut = Q.sanitize("news", {"ticker": "005930", "title": "t" * 900, "url": "https://x.example/1", "api_key": "secret",
                                     "published_at": datetime(2026, 11, 6, 9, tzinfo=KST), "user_id": 7})
        assert set(p) == {"ticker", "title", "url", "source", "published_at", "content"} and len(p["title"]) == 300 and not cut
        assert "secret" not in json.dumps(p) and p["published_at"].startswith("2026-11-06T09:00")

    def test_payload_hash_is_stable_and_large_etf_payload_is_truncated(self):
        p1, _ = Q.sanitize("stock_price", px(date(2026, 11, 5)) | {"ticker": "005930"})
        p2, _ = Q.sanitize("stock_price", {"ticker": "005930", **px(date(2026, 11, 5))})
        assert Q.payload_hash(p1) == Q.payload_hash(p2)
        big = {"ticker": "069500", "name": "KODEX", "as_of": date(2026, 11, 5), "source": "kodex",
               "holdings": [{"name": "가" * 150, "code": f"{i:06d}", "weight_pct": 0.1} for i in range(2000)]}
        p, cut = Q.sanitize("etf_holding", big)
        assert cut and len(json.dumps(p, ensure_ascii=False).encode()) <= Q.MAX_PAYLOAD_BYTES


# ======================= B. 거래일 달력 =======================


class TestCalendar:
    def test_regular_weekday(self):  # 23
        assert K.day_status(date(2026, 11, 4)) == K.TRADING and K.is_trading_day(date(2026, 11, 4)) is True

    def test_saturday_and_sunday(self):  # 24, 25
        assert K.day_status(date(2026, 11, 7)) == K.WEEKEND and K.day_status(date(2026, 11, 8)) == K.WEEKEND

    def test_statutory_holiday(self):  # 26
        assert K.is_holiday(date(2026, 10, 9)) and K.holiday_name(date(2026, 10, 9)) == "한글날"

    def test_substitute_holiday(self):  # 27
        assert K.is_holiday(date(2026, 10, 5)) and "대체" in K.holiday_name(date(2026, 10, 5))
        assert K.is_holiday(date(2026, 3, 2)) and K.is_holiday(date(2026, 8, 17))

    def test_year_end_closure(self):  # 28
        assert K.is_holiday(date(2025, 12, 31)) and K.is_holiday(date(2024, 12, 31)) and K.is_holiday(date(2023, 12, 29))
        assert K.day_status(date(2026, 12, 31)) == K.UNKNOWN  # 2026년 연말 휴장은 KRX 공지 전: 단정하지 않는다
        assert "미확정" in K.holiday_name(date(2026, 12, 31))

    def test_temporary_holidays_and_elections(self):  # 29
        for d in (date(2024, 10, 1), date(2025, 1, 27), date(2024, 4, 10), date(2025, 6, 3), date(2026, 6, 3), date(2026, 7, 17)):
            assert K.is_holiday(d), d

    def test_coverage_boundaries(self):  # 30
        cal = K.calendar()
        assert cal.start == date(2023, 10, 30) and cal.end == date(2026, 12, 31)
        assert K.day_status(date(2023, 10, 30)) == K.TRADING and K.day_status(date(2023, 10, 27)) == K.UNKNOWN
        assert K.day_status(date(2026, 12, 30)) == K.TRADING

    def test_future_dates_outside_calendar(self):  # 31
        assert K.day_status(date(2027, 2, 8)) == K.UNKNOWN and K.is_trading_day(date(2027, 2, 8)) is None  # 설 연휴 날짜를 모른다
        assert K.day_status(date(2027, 1, 1)) == K.HOLIDAY  # 고정 법정 공휴일은 범위 밖에도 휴장
        lg = K.lag(date(2026, 12, 30), date(2027, 1, 5))
        assert lg == K.Lag(3, 3) and not lg.certain  # 12/31(미확정)·1/4·1/5: 거래일일 수 있는 날, 확인 불가로 표시

    def test_expected_day_intraday_and_after_close(self):  # 32, 33
        assert K.expected_trading_day(datetime(2026, 11, 4, 10, 0, tzinfo=KST)) == date(2026, 11, 3)
        assert K.expected_trading_day(datetime(2026, 11, 4, 15, 30, tzinfo=KST)) == date(2026, 11, 4)
        assert K.expected_trading_day(datetime(2026, 11, 4, 15, 29, tzinfo=KST)) == date(2026, 11, 3)

    def test_expected_day_after_weekend(self):  # 34
        assert K.expected_trading_day(datetime(2026, 11, 9, 8, 0, tzinfo=KST)) == date(2026, 11, 6)
        assert K.expected_trading_day(datetime(2026, 11, 8, 20, 0, tzinfo=KST)) == date(2026, 11, 6)

    def test_consecutive_holidays(self):  # 35
        # 추석 9/24·25 + 주말 9/26·27: 9/28(월) 장중의 기대 거래일은 9/23
        assert K.expected_trading_day(datetime(2026, 9, 28, 10, 0, tzinfo=KST)) == date(2026, 9, 23)
        assert K.previous_trading_day(date(2026, 9, 28)) == date(2026, 9, 23) and K.next_trading_day(date(2026, 9, 23)) == date(2026, 9, 28)
        assert K.trading_days(date(2026, 9, 21), date(2026, 9, 30)) == [date(2026, 9, d) for d in (21, 22, 23, 28, 29, 30)]
        # 설 연휴 2025-01-27~30 + 주말: 1/24(금) 다음 거래일은 1/31
        assert K.next_trading_day(date(2025, 1, 24)) == date(2025, 1, 31)

    def test_price_right_after_holiday_is_fresh(self):  # 36
        mon_am = datetime(2026, 10, 12, 10, 0, tzinfo=KST)  # 한글날(금) 다음 월요일 장중
        assert quote_freshness.status(date(2026, 10, 8), mon_am)["lag"] == 0
        assert not is_stale(date(2026, 10, 8), date(2026, 10, 12))
        assert "지연" not in quote_freshness.line(date(2026, 10, 8), mon_am)

    def test_price_actually_missing_after_holiday(self):  # 37
        tue_eve = datetime(2026, 10, 13, 18, 0, tzinfo=KST)
        s = quote_freshness.status(date(2026, 10, 8), tue_eve)
        assert s["lag"] == 2 and s["expected"] == date(2026, 10, 13)
        assert weekdays_behind(date(2026, 10, 8), date(2026, 10, 13)) == 3  # 평일 기준이었다면 3(한글날을 지연으로 셈)
        line = quote_freshness.line(date(2026, 10, 8), tue_eve)
        assert "2영업일 지연" in line and "최신 시세가 아닙니다" in line

    def test_delay_is_stated_once(self):  # 38
        line = quote_freshness.line(date(2026, 11, 3), datetime(2026, 11, 8, 12, 0, tzinfo=KST))
        assert line.count("영업일") == 1 and line.count("최신 시세가 아닙니다") == 1
        with_unknown = quote_freshness.line(date(2026, 12, 30), datetime(2027, 1, 5, 18, 0, tzinfo=KST))
        assert with_unknown.count("확인할 수 없는 날") == 1 and with_unknown.count("영업일 지연") == 1

    def test_holiday_is_not_an_open_session(self):
        from app.briefing import session_is_open

        assert session_is_open(datetime(2026, 11, 4, 10, 0, tzinfo=KST))
        assert not session_is_open(datetime(2026, 10, 9, 10, 0, tzinfo=KST))  # 한글날 오전은 장중이 아니다

    def test_data_status_uses_trading_days(self):
        assert last_business_day(date(2026, 10, 5)) == date(2026, 10, 2)
        assert business_days_behind(date(2026, 9, 23), date(2026, 10, 6)) == 6  # 평일 9일 중 휴장 3일

    def test_calendar_file_matches_rules(self):
        """달력 파일 형식 검증: 주말·범위 밖·중복 휴장일, 고정 휴장일 누락은 읽을 때 오류다."""
        data = json.loads(K.DATA_PATH.read_text(encoding="utf-8"))
        K.parse(data)
        for bad in ({**data, "holidays": data["holidays"] + [{"date": "2026-11-07", "name": "x", "source": "t"}]},
                    {**data, "holidays": data["holidays"] + [{"date": "2027-03-03", "name": "x", "source": "t"}]},
                    {**data, "holidays": [h for h in data["holidays"] if h["date"] != "2026-12-25"]}):
            with pytest.raises(ValueError):
                K.parse(bad)


class TestFreshnessPolicies:
    def facts(self, d):
        return DM.SourceFacts(data_as_of=d, record_count=10)

    def test_prices_use_krx_calendar_and_fx_news_use_weekdays(self):
        mon_eve = datetime(2026, 10, 12, 18, 0, tzinfo=KST)  # 한글날(10-09) 다음 월요일 장 마감 뒤
        stock = DM.evaluate_source(DM.SOURCE_BY_KEY["stock_price"], self.facts(date(2026, 10, 8)), {}, None, mon_eve)
        fx = DM.evaluate_source(DM.SOURCE_BY_KEY["exchange_rate"], self.facts(date(2026, 10, 8)), {}, None, mon_eve)
        news = DM.evaluate_source(DM.SOURCE_BY_KEY["news"], self.facts(date(2026, 10, 8)), {}, None, mon_eve)
        assert (stock["lag_business_days"], stock["status"]) == (1, "healthy")  # 10-12 종가만 아직: 휴장일은 세지 않는다
        assert (fx["lag_business_days"], fx["status"]) == (2, "stale")  # 환율 출처는 한국 휴장일에도 값을 준다
        assert news["lag_business_days"] == 2 and news["freshness_policy"] == "weekday_publish"

    def test_calendar_unknown_is_reported(self):
        jan = datetime(2027, 1, 5, 18, 0, tzinfo=KST)
        row = DM.evaluate_source(DM.SOURCE_BY_KEY["market_index"], self.facts(date(2026, 12, 30)), {}, None, jan)
        assert row["lag_calendar_unknown_days"] == 3 and any("calendar_unknown" in r for r in row["reasons"])

    def test_etf_holdings_stale_before_uses_trading_days(self):
        assert DM._stale_before(date(2026, 10, 12)) == date(2026, 10, 8)  # 10-12의 직전 거래일(10-09 휴장)


# ======================= 실행기 연결 =======================


class TestRefreshIntegration:
    def test_quarantine_makes_a_step_partial_with_its_own_code(self):
        from scripts import refresh_data as R

        now = datetime(2026, 11, 6, 18, 0, tzinfo=KST)
        res = R.StepResult("prices", "success", now, now)
        R.apply_quality_counts(res, {"quarantined": 3, "warning": 1})
        assert res.status == "partial" and res.error_code == R.QUALITY_QUARANTINED and "격리 3건" in res.warning
        failed = R.StepResult("prices", "failed", now, now, error_code="network")
        R.apply_quality_counts(failed, {"quarantined": 3})
        assert failed.status == "failed" and failed.error_code == "network"  # 수집 실패 코드는 그대로
        ok = R.apply_quality_counts(R.StepResult("prices", "success", now, now), {"quarantined": 0, "warning": 5})
        assert ok.status == "success"  # 경고만 있으면 성공(경고는 저장된 데이터)

    def test_run_steps_sets_context_and_counts(self):
        from scripts import refresh_data as R

        seen = []
        steps = [R.Step("prices", "주가", lambda: None), R.Step("index", "지수", lambda: None)]
        res = R.run_steps(steps, ["prices", "index"], before_step=seen.append,
                          quality_counts=lambda k: {"quarantined": 2} if k == "index" else {})
        assert seen == ["prices", "index"] and [r.status for r in res] == ["success", "partial"]
        assert R.final_exit_code(res) == R.EXIT_PARTIAL

    def test_etf_outcome_separates_quarantine_from_failure(self):
        from types import SimpleNamespace as NS

        from scripts import refresh_data as R

        rs = [NS(skipped=None, ok=True, quarantined=False, ticker="A", error=None),
              NS(skipped=None, ok=False, quarantined=True, ticker="B", error="quality_quarantined: x")]
        out = R.etf_holdings_outcome(rs)
        assert isinstance(out, R.Partial) and "격리 1/2" in out

    def test_holiday_is_not_market_hours_or_gap(self):
        from scripts import refresh_data as R

        assert not R.is_market_hours(datetime(2026, 10, 9, 10, 0, tzinfo=KST))
        present = {date(2026, 10, d) for d in (1, 2, 6, 7, 8, 12, 13, 14)}  # 10-05·10-09 휴장
        assert R.default_days(present, date(2026, 10, 15), lookback=14) == R.DAYS_MIN  # 휴장일을 공백으로 보지 않는다


class TestPeriodCalendar:
    def test_period_row_count_keeps_its_weekday_meaning(self):
        """기간 질문의 행 수(period_days)는 기존 평일 근사 그대로다(기존 기능의 의미 유지, 고정 평가 문항 per_*의 정의)."""
        from app.period_parser import _business_days_between

        assert _business_days_between(date(2026, 10, 5), date(2026, 10, 9)) == 5

    def test_closed_reason_uses_calendar(self):
        from app import period_answers as pa

        # 마지막 데이터(11-03) 뒤라도 달력의 휴장일이면 "아직 수집 안 됨"이 아니라 휴장이다
        assert pa.closed_reason(date(2026, 10, 9), False, date(2026, 10, 1)) == pa.REASON_HOLIDAY
        assert pa.closed_reason(date(2026, 11, 5), False, date(2026, 11, 3)) == pa.REASON_NOT_COLLECTED
