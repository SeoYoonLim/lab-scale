# 데이터 사전 검증·격리와 KRX 거래일 달력

2026-11-08(36차). 수집한 데이터를 **저장하기 전에** 검증해 잘못된 값이 분석을 오염시키지 않게 하고, 최신성 판정이 공휴일·대체공휴일·연말 휴장을
지연으로 잘못 세지 않도록 KRX 거래일 달력을 넣었다. 운영 절차 전체는 `docs/DATA_REFRESH_MONITORING.md`를 함께 본다.

- 코드: `backend/app/ingest_validation.py`(판정, DB 없음), `backend/app/quarantine.py`(격리 기록·조회·재검증·재처리), `backend/scripts/quarantine.py`(CLI),
  `backend/app/krx_calendar.py` + `backend/app/data/krx_calendar.json`(달력)
- 표: `data_ingestion_quarantine`(마이그레이션 `c7e2a9f4d158`). `data_refresh_run`·`data_refresh_log`는 그대로 쓴다.
- 테스트: `tests/test_data_quality_calendar.py`(순수), `tests/test_quarantine_devdb.py`(개발 DB, 가짜 종목만)

## 1. 이전 구조(조사 결과)

| 수집기 | 저장 지점 | 저장 전 검증 | 저장 후 검증 | 같은 데이터를 다시 받으면 |
|---|---|---|---|---|
| 주가·ETF 종가 `collectors.fetch_and_save_stock` | `stock_price` upsert(같은 날짜는 새 값으로 갱신) | 종가 NaN 행 제외뿐 | `data_monitor` 품질 SQL(0 이하·미래·중복·±30%) | 덮어씀 |
| 지수 `fetch_and_save_market_index` | `market_index` upsert | 소스 폴백 체인(10일 넘게 멈춘 소스 건너뜀) | 0 이하·미래·지수 간 날짜 차이 | 덮어씀 |
| 환율 `fetch_and_save_exchange_rate` | `exchange_rate` upsert | 없음 | 0 이하·미래·±5% | 덮어씀 |
| 뉴스 `fetch_and_save_news` | `news` insert | 종목별 url 중복, 비금융 도메인 제외 | url·발행 시각·종목 연결 누락, 미래 발행 | 건너뜀(url 기준) |
| 공시 `fetch_and_save_disclosures` | `disclosure` insert | 회사 명의 공시만, 접수번호 url 중복 | 접수번호 중복·미래 접수일·접수번호 날짜 차이 | 건너뜀 |
| ETF 구성 `etf_holdings_collect.collect` | `etf_holding` (ETF, 기준일) 단위 삭제 후 삽입 | 구성 0개는 실패 | 비중 합계·음수·중복 코드 | 같은 기준일을 통째로 교체 |

문제: 품질 검사가 **저장 뒤**에만 돌아서, 잘못된 값(0원·통화쌍 뒤바뀜·비중 0인 PDF)이 들어오면 기존 정상 값을 덮어쓴 뒤에야 "분석에 쓸 수 없음"으로 표시했다.
한 레코드 문제로 전체가 멈추지는 않았지만(종목·ETF 단위로 실패), 수집 실패와 품질 실패를 구분하는 코드도 없었다.
공시의 정정·철회는 접수번호가 달라 원본과 별개 행으로 저장되고, 원본 접수번호를 잇는 정보는 없다(그대로 둔다).

## 2. 사전 검증 흐름

```text
수집(외부) -> 파싱(collectors._price_row 등) -> 저장 전 검증(ingest_validation.check_*)
   valid       -> 기존 업무 테이블에 저장(이전과 같은 upsert/insert)
   warning     -> 저장 + data_ingestion_quarantine에 validation_status=warning 기록
   quarantined -> 저장하지 않음(기존 행을 갱신하지 않음) + validation_status=quarantined 기록
-> CollectResult(saved, fetched, quarantined, warned)
-> refresh_data 단계 결과: 격리가 있으면 partial(error_code=quality_quarantined), 수집 실패 코드와 별개
-> data_monitor --status 보고서: 미해결 격리 소스별·코드별 건수
```

- 묶음 일부만 문제면 나머지는 저장한다(종목 시세 4건 중 1건 격리 -> 3건 저장).
- ETF 구성은 (ETF, 기준일)이 한 단위다(저장이 그 날짜를 통째로 교체하므로). 묶음 판정이 격리면 저장하지 않아 **같은 기준일의 기존 구성이 그대로 남는다**.
- 잘못된 종목코드(6자리 KRX 코드가 아님)로는 `company` 행을 새로 만들지 않는다.
- 시험 실행(`dry_run`)은 판정 건수만 보여 주고 격리 기록을 남기지 않는다.
- 격리 기록에 실패해도(표 없음·DB 오류) 수집은 계속되고, 격리 대상은 여전히 저장하지 않는다(화면에 `[경고] 격리 기록 실패`).

## 3. 소스별 검증 기준

| 소스 | 격리(quarantined) | 경고(warning, 저장) | 일부러 막지 않는 것 |
|---|---|---|---|
| 주가·ETF 종가 | 종가 없음·0 이하, 음수 가격, 거래량 음수, 잘못된 종목코드, 미래 날짜, KRX 휴장일·주말 날짜, 같은 날짜 다른 값(둘 다), 같은 날짜 같은 값(두 번째), 고가<저가·종가가 고저 범위 밖(0.5% 여유), **기존 저장 종가와 ±30.5% 넘게 다름** | 직전 종가 대비 ±30.5% 초과(가격제한폭 밖) | 실제 급등락·상한가(±30%)·거래정지일(시가·고가·저가 0), 달력 미확정 날 |
| 지수(KS11·KQ11) | 지원하지 않는 코드(KOSPI200 포함), 0 이하, 미래, 휴장일·주말, 중복, 기존 값과 ±15% 넘게 다름 | 직전 대비 ±8% 초과(서킷브레이커 1단계) | |
| 환율(USD/KRW) | 0 이하, **500~5000원 밖(통화쌍 뒤바뀜 의심)**, 미래, 중복, 기존 값과 ±5% 넘게 다름 | 직전 대비 ±5% 초과, 정상 범위를 모르는 통화쌍(`unknown_pair_range`, 범위 검사 생략) | **주말·한국 휴장일 날짜**(출처가 일요일 날짜·한국 휴장일에도 값을 준다: 2026-04~11 개발 DB에서 일요일 행 30개, 휴장일 행 6개) |
| 뉴스 | 제목 없음, url 없음·형식 오류, 종목 연결 없음, 발행 시각 없음·시간대 없음, 1시간 넘게 미래, 묶음 안 같은 url | 발행 30일 넘은 기사 | 같은 사건의 다른 언론사 기사(url이 다르면 중복 아님) |
| 공시 | 접수번호 14자리 아님, 접수일 형식 오류, 미래 접수일, 제목 없음, 종목 연결 없음, 묶음 안 같은 접수번호 | 접수번호 날짜와 접수일이 다름: 다음 영업일이면 `receipt_next_business_day`, 그 밖이면 `receipt_date_mismatch` | 정정·추가 공시(`[기재정정]` 등)는 원본과 별개 공시로 정상 |
| ETF 구성(묶음) | 기준일 없음·내일보다 뒤, 구성 0개, 비중이 숫자가 아님, 이름 없는 종목, 양의 비중 합계 0 이하, 합계가 기대값(100·레버리지 200)의 1.5배 초과 | 합계가 기대값에서 ±5%p 넘게 벗어남, 음수 비중(look-through는 빼고 계산), 같은 종목코드 중복 | 오래된 기준일(데이터가 틀린 것이 아님: 최신성은 `data_monitor`가 따로 본다) |

**34차 공시 5건 재분석**(접수번호 날짜 ≠ 접수일, 2026-11-07 품질 warning): 고영·성호전자 2건·아시아나항공·녹십자. 모두 접수일이 접수번호 날짜의
**정확히 다음 영업일**이었고(예: 성호전자 20261002… -> 10-06, 주말·10-05 대체공휴일 다음), 4건은 정정 공시, 1건(성호전자 전환사채)은 원본이다.
정정 공시만의 특성이 아니라 영업시간 뒤 제출로 보인다(DART 내부 규칙은 확인하지 못했다). 데이터 오류가 아니므로 격리하지 않고
`receipt_next_business_day` 경고로 남긴다(기존 품질 SQL warning `receipt_date_mismatch`는 그대로 5건으로 나온다).

## 4. 격리 저장소 `data_ingestion_quarantine`

| 열 | 뜻 |
|---|---|
| `source`, `source_key` | `stock_price:005930:2026-11-03`, `market_index:KS11:…`, `exchange_rate:USD/KRW:…`, `news:<ticker>:<url>`, `disclosure:<접수번호>`, `etf_holding:<ticker>:<기준일>` |
| `validation_status` | `quarantined`(저장 안 함) / `warning`(저장함) |
| `error_code`, `error_detail` | 첫 사유 코드 / 모든 사유(500자) |
| `payload`, `payload_hash`, `payload_truncated` | 재검증에 필요한 최소 필드(화이트리스트, 긴 글 자름, 64KB 상한). 넘으면 잘라 두고 재처리하지 않는다(다시 수집) |
| `occurrences`, `first_seen_at`, `last_seen_at` | 같은 (source, source_key, error_code, payload_hash)는 한 행: 다시 걸리면 횟수·시각만 갱신(무한 중복 방지) |
| `resolution_status` | `open` -> `revalidated` -> `reprocessed` / `kept_existing`, 또는 `on_hold`, `discarded` |
| `run_id`, `step` | refresh_data 실행·단계(직접 실행한 수집이면 null) |

payload에 API 키·요청 URL·사용자 데이터는 들어가지 않는다(필드 화이트리스트). 공시는 제출인 이름(`flr_nm`)을 남기지 않는다.

## 5. 격리 데이터 확인·재검증·재처리(운영 CLI)

일반 사용자 API는 만들지 않았다(관리자 역할 체계가 없다). `backend`에서:

```bash
python -m scripts.quarantine summary                       # 소스별·코드별 미해결 건수
python -m scripts.quarantine list --source stock_price --code conflicts_existing
python -m scripts.quarantine list --warnings               # 저장했지만 경고가 붙은 레코드
python -m scripts.quarantine show 12                       # 사유·payload·발생 횟수
python -m scripts.quarantine revalidate 12 13              # 지금 규칙·지금 DB로 다시 판정 -> 통과면 revalidated
python -m scripts.quarantine revalidate --all-open --source exchange_rate
python -m scripts.quarantine reprocess 12                  # 계획만(would_insert / kept_existing)
python -m scripts.quarantine reprocess 12 --apply          # revalidated만 업무 테이블에 넣음
python -m scripts.quarantine hold 13 --note "출처 확인 중"
python -m scripts.quarantine discard 14 --note "잘못된 통화쌍"
python -m scripts.quarantine prune --keep-days 180 --dry-run   # 끝난 기록·경고만(미해결은 --include-open일 때만)
```

**기존 정상 데이터 보호**: `reprocess`는 같은 키의 데이터가 이미 있으면 넣지 않고 `kept_existing`으로 닫는다. 어떤 명령도 기존 행을 갱신하지 않는다.
`conflicts_existing`(기존 값과 크게 다름)은 기존 값이 그대로 있는 한 재검증해도 격리로 남는다. 새 값이 맞다고 판단되면(예: 액면분할로 과거
수정주가가 바뀜) 운영자가 기존 행을 확인·정리한 뒤 `revalidate` -> `reprocess --apply` 하거나, 해당 기간을 `scripts/backfill_history.py`로 다시 받는다.

## 6. 실행기 연결(`scripts/refresh_data.py`)

옵션·인터페이스는 그대로다. 단계마다 `quarantine.set_context(run_id, step)`을 설정하고, 단계가 끝나면 그 단계의 격리 건수를 센다.

| 결과 | 단계 status | error_code |
|---|---|---|
| 정상 저장 | success | - |
| 경고 후 저장(격리 없음) | success | - (경고는 격리 표에 기록) |
| 격리 있음(나머지는 저장) | **partial** | `quality_quarantined` |
| 수집 자체 실패 | failed | `timeout`·`network`·`rate_limited`… (기존) |
| 재시도 후 성공 | success | - (`retry_count` > 0) |

ETF 구성의 격리는 실패로 세지 않는다(`etf_holdings_outcome`: 격리만 있으면 부분 성공). 부분 성공이면 종료 코드 3이다(전체 성공으로 표시하지 않는다).

## 7. KRX 거래일 달력

### 출처와 버전

`backend/app/data/krx_calendar.json` — `version: krx-2026-11-08`, `updated: 2026-11-08`, 적용 범위 **2023-10-30 ~ 2026-12-31**, 휴장일(평일) 55일.

| 근거(`source`) | 내용 |
|---|---|
| `db` | 개발 DB 기록 대조: 2023-10-30~2026-11-03 평일 중 KS11·KQ11·주가가 모두 없는 날. 세 표 모두 733거래일로 같고 주말 행은 0개였다. 대체공휴일·선거일(2024-04-10, 2025-06-03, 2026-06-03)·임시공휴일(2024-10-01, 2025-01-27)·근로자의 날·연말 휴장·2026-07-17 제헌절이 모두 맞았다 |
| `statute` | 날짜가 고정된 법정 공휴일(2026-12-25 성탄절) |
| `krx_notice` | 한국거래소 공지(언론 보도): 2026-06-03 지방선거일·2026-07-17 제헌절 휴장 |

- **미확정(`unconfirmed`)**: 2026-12-31 연말 휴장. 관례상 휴장이지만 2026-11-08 현재 거래소 공지 전이라 단정하지 않는다(`unknown`).
- 2023-10-30 이전은 개발 DB 기록이 없고 이번에 공식 자료로 확인하지 못해 범위에 넣지 않았다.
- **범위 밖 규칙(`fixed_annual`)**: 날짜가 고정된 법정 공휴일(1/1, 3/1, 5/5, 6/6, 8/15, 10/3, 10/9, 12/25)과 KRX 업무규정상의 근로자의 날(5/1)은
  범위 밖 해에도 휴장이다. 설·추석·대체공휴일·선거일·임시공휴일·연말 휴장은 해마다 달라 범위 밖에서는 `unknown`이다.
- 특별 운영일(`special_sessions`, 예: 수능일 1시간 지연): 마감 시각이 다른 날을 KRX 공지 뒤 넣는다. 현재 비어 있다(2026-11-19 수능일은 공지 전).

### 날짜 상태와 함수(`app.krx_calendar`)

`day_status(d)`: `trading` / `weekend` / `holiday` / `unknown`(범위 밖 평일·미확정). `is_trading_day`(True/False/None), `is_holiday`, `holiday_name`,
`previous_trading_day`, `next_trading_day`, `last_trading_day`, `expected_trading_day(now)`, `trading_days(start, end)`, `lag(latest, ref) -> Lag(days, unknown)`,
`session_is_open(now)`, `info()`.

`unknown`은 거래일로 단정하지 않는다. 다만 기대 거래일·지연 일수처럼 값이 필요한 계산에서는 "거래일일 수 있는 날"로 센다(지연을 작게 잡지 않는 쪽).
대신 `Lag.unknown`이 그 날 수를 따로 주고, 단순 시세·전망 답("지연 계산에 KRX 휴장일 달력으로 확인할 수 없는 날 N일…")과 운영 보고서(`calendar_unknown`)가 밝힌다.

### 장중·장 마감

- 거래일 15:30(KST, `regular_close`) 전: 오늘 종가는 아직 없는 것이 정상 -> 기대 거래일은 직전 거래일
- 거래일 15:30 이후: 오늘 종가를 기대
- 주말·휴장일: 직전 실제 거래일 종가를 기대(휴장일 오전을 "장중"으로 보지 않는다: `briefing.session_is_open`)

예: 2026-10-05(월, 개천절 대체공휴일) 10시 -> 10-02. 10-06 10시 -> 10-02, 10-06 16시 -> 10-06. 2026-09-28(월) 10시 -> 9-23(추석·주말 다음).

### 적용한 곳(같은 기준)

`app.data_status.last_business_day`·`business_days_behind`·`is_stale`이 달력을 쓰므로 이 함수를 쓰는 모든 경로가 같은 기준이 됐다:
전망(`outlook.expected_trading_day`·`data_check`), 주가 흐름, 단순 시세·주문 참고 시세(`quote_freshness`), 휴장일 하루 질문의 사유(`period_answers`), ETF look-through·노출(`look_through`·`exposure`), 성과·벤치마크(`performance_compare`), 브리핑·일일 브리핑, `GET /api/status`의
`market_index.stale`, `health`, 운영 모니터(`data_monitor`), 갱신 실행기(장중 경고·공백 탐색). 평일 기준 함수는 `last_weekday`·`weekdays_behind`로 남겼다.
평가 판정기(`evals/quality_judges`)는 제품 코드를 import하지 않고 같은 JSON만 읽어 따로 계산한다.

## 8. 데이터별 최신성 정책(`data_monitor.Source.freshness`)

| 소스 | 정책 | 기대 기준일 | 지연 |
|---|---|---|---|
| 주가·ETF 종가·지수 | `krx_trading_close` | KRX 기대 거래일(15:30 기준) | KRX 거래일 수 |
| ETF 구성 | `etf_holdings` | 최근 KRX 거래일 | KRX 거래일 수(오래된 기준일 ≠ 틀린 데이터) |
| 환율 | `fx_weekday_close` | 최근 평일(15:30 기준) | 평일 수(출처가 한국 휴장일에도 값을 줌) |
| 뉴스·공시 | `weekday_publish` | 최근 평일 | 평일 수(발행·접수는 KRX 휴장과 무관. 휴장일에 뉴스가 없다고 stale이 되지 않고, 휴장일이라고 오래된 뉴스가 최신이 되지도 않는다) |

모두 지연 2 이상이면 `stale`. 보고서 행에 `lag_calendar_unknown_days`가 추가됐다.

**stale과 holiday의 구분**: 휴장일은 지연으로 세지 않는다. 예: 2026-10-08(목) 종가, 10-12(월) 10시 -> 지연 0(10-09 한글날). 10-13(화) 18시 -> 지연 2, stale
(평일 기준이었다면 3). 35차의 단순 시세 "늦은 이유(휴장일 추정)" 문장(수집 기록으로 추정)은 남아 있지만, 달력이 휴장일을 알기 때문에 대부분 필요 없어졌다.

## 9. 운영 중 생길 수 있는 오류

| 상황 | 증상 | 조치 |
|---|---|---|
| 달력에 없는 임시 휴장(정부 임시공휴일 지정) | 그날을 거래일로 세어 다음 날까지 "1영업일 이전"으로 보임 | JSON `holidays`에 추가(10절), 테스트 실행 |
| 달력에 휴장으로 적었는데 실제로 개장 | 그날 시세가 `non_trading_day`로 격리됨 | JSON에서 빼고 `revalidate --all-open --source stock_price` -> `reprocess --apply` |
| 2027년이 되었는데 달력 미갱신 | 보고서 `calendar_unknown`, 답에 "달력으로 확인할 수 없는 날" 문장 | 2027 휴장일 추가 |
| 액면분할 등으로 과거 수정주가가 바뀜 | `conflicts_existing` 격리가 대량 발생 | 5절 절차(기존 행 확인 뒤 재처리 또는 backfill) |
| 격리 표가 없는 DB(마이그레이션 전) | `[경고] 격리 기록 실패`, 보고서 "확인 불가" | `alembic upgrade head` |

## 10. 달력 갱신 방법

1. 한국거래소 휴장일 공지(보통 12월 중순 다음 해 일정·연말 휴장, 선거·임시공휴일은 그때그때)를 확인한다.
2. `app/data/krx_calendar.json`의 `holidays`에 `{"date", "name", "source": "krx_notice"}`를 넣고, 확정되지 않은 날은 `unconfirmed`에 둔다.
   해가 바뀌면 `coverage.end`를 늘리고 `version`·`updated`를 바꾼다.
3. 지난 기간은 개발 DB로 대조한다(KS11이 없는 평일 = 휴장일):
   `SELECT d FROM generate_series(...) d WHERE extract(isodow from d) < 6 AND d NOT IN (SELECT price_date FROM market_index WHERE index_code='KS11')`
4. `pytest tests/test_data_quality_calendar.py` — 파일을 읽을 때 주말·범위 밖·중복 휴장일, 범위 안에서 빠진 고정 공휴일을 오류로 잡는다.

## 11. 검증 방법과 결과(2026-11-08)

| 항목 | 방법 | 결과 |
|---|---|---|
| 소스별 검증 규칙, payload, 달력(평일·주말·공휴일·대체·연말·임시·경계·범위 밖·장중·장 마감·주말 뒤·연휴·공휴일 직후 정상/누락·중복 표시), 정책별 최신성, 실행기 부분 성공·ETF 결과·장중·공백 | `tests/test_data_quality_calendar.py`(DB·외부 API 없음) | 통과 |
| 수집기 저장 전 검증(가짜 원격 응답) + 기존 정상 값 보호, 혼합 묶음, 재수집 중복 방지, 잘못된 종목코드, 재검증·재처리·기존 데이터 보호, 뉴스·공시·ETF 격리, 실행 맥락 건수, CLI, 정리 | `tests/test_quarantine_devdb.py`(개발 DB, 가짜 종목만 만들고 지움) | 통과 |
| 마이그레이션 왕복 | 같은 파일, `MIGRATION_TEST_DATABASE_URL` 임시 서버(CI) | CI에서 실행 |
| **실제 외부 수집**(FinanceDataReader·네이버·DART·운용사) | 실행하지 않음(보호 규칙) | **검증 안 함**. 실제 응답에서 격리가 얼마나 나오는지는 첫 운영 실행에서 확인해야 한다 |

## 12. 알려진 한계

- 달력은 2023-10-30~2026-12-31만 확정이고 2026-12-31은 미확정이다. 2027년 일정은 KRX 공지 뒤 넣어야 한다.
- 특별 운영일(수능일 등 마감 16:30)은 비어 있다. 그날 15:30~16:30에는 오늘 종가를 기대해 "1영업일 이전"으로 보일 수 있다.
- 환율 출처의 날짜 관행(일요일 날짜, 금요일 누락)은 그대로다. 품질 SQL `weekday_gap` 경고가 계속 나온다.
- 가격 급등락은 경고만 한다. 액면분할을 자동으로 판별하지 않는다.
- 뉴스 제목 유사도로 같은 기사를 합치지 않는다(같은 url만 중복). 공시의 원본-정정 관계(원 접수번호)는 저장하지 않는다.
- 기간 질문의 행 수(`period_parser._business_days_between`, "최근 한 달" -> stock_tool이 읽을 최근 행 수)는 기존 평일 근사 그대로다. 기존 기간 분석의 의미이고
  고정 평가 문항(`evals/dataset.jsonl` per_*, holdout 포함)의 정의라 바꾸지 않았다. 휴장일이 낀 기간은 휴장일 수만큼 앞 거래일이 더 들어간다
  (거래일 수로 바꾸면 평가 문항 10개의 기대값이 바뀐다: 별도 결정 필요).
- 저장 전 검증은 이번 작업 이후 수집분부터다. 이미 저장된 데이터는 기존 품질 SQL(`data_monitor`)로만 본다.
- `backfill_history.py`·`collect_etf_holdings.py` 등 수집 함수를 거치는 스크립트는 같은 검증을 받지만, 표에 직접 쓰는 일회성 스크립트는 받지 않는다.
