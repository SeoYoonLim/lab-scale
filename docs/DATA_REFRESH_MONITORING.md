# 데이터 자동 갱신과 운영 모니터링

2026-11-07 작성. 대상 독자: 데이터 갱신을 스케줄러에 등록하고, 실패·지연을 확인해 복구하는 운영자와 백엔드 개발자.

이 문서의 명령은 모두 `backend/` 폴더에서 실행한다(`.venv`의 Python). **이 프로젝트에는 스케줄러가 설치돼 있지 않다.** 아래 17·18절의
등록 절차를 운영자가 직접 실행해야 자동 갱신이 된다. 외부 알림(Slack·이메일)도 연결돼 있지 않다(11절).

이번 작업(2026-11-07)에서는 실제 외부 수집을 실행하지 않았다. 수집기 동작은 기존 수집 코드 그대로이고, 새로 만든 실행·재시도·락·기록·품질 검사·보고서는
mock·합성 데이터와 개발 DB로 시험했다(22절).

---

## 1. 전체 구조

```
스케줄러(Windows 작업 스케줄러 / cron, 운영자가 등록)
  └─ python -m scripts.refresh_data [옵션]          backend/scripts/refresh_data.py
       ├─ Postgres advisory lock 획득(중복 실행 방지)   app/refresh_runs.py
       ├─ data_refresh_run 한 행(running)
       ├─ 단계 실행(순서 고정, 단계마다 data_refresh_log 한 행)
       │    prices -> index -> fx -> news -> embed -> content [-> etf_holdings]
       │    (각 단계는 기존 수집 스크립트·함수를 그대로 호출)
       ├─ 품질 검사 + 상태 보고서(JSON·Markdown) + 알림(표준 오류)   app/data_monitor.py
       └─ data_refresh_run 종료(success | partial | failed), 락 해제
python -m scripts.refresh_data --status   -> 수집 없이 상태 보고서만
GET /api/status                           -> 기존 공개 API(데이터 최신 날짜 + 마지막 실행 요약), 응답 형식 그대로
```

수집했다는 사실과 데이터를 분석에 쓸 수 있다는 사실을 따로 본다.
- **status**: 운영 상태. 마지막 실행 결과와 데이터 지연으로 정한다.
- **usable_for_analysis**: 데이터 기준일이 최신성 기준 안에 있고 품질 검사에 error가 없을 때만 `true`다. 마지막 실행이 실패해도 그 전에 받은 데이터가
  최신이면 `true`다. 최근 실패 때문에 멀쩡한 데이터가 사라진 것처럼 보이지 않게 하기 위해서다.

## 2. 수집기 목록과 파일 위치

| 단계 | 데이터(--source 이름) | 호출하는 기존 코드 | 저장 표 | 중복 방지 |
|---|---|---|---|---|
| `prices` | `stock_price`, `etf_price` | `scripts/collect_prices.py` x2(universe_top300, universe_etf50) → `app/collectors.fetch_and_save_stock` | `stock_price` | `(company_id, price_date)` 고유키 upsert |
| `index` | `market_index` | `scripts/collect_market_index.py` → `fetch_and_save_market_index` | `market_index` | `(index_code, price_date)` upsert |
| `fx` | `exchange_rate` | `scripts/collect_exchange_rate.py` → `fetch_and_save_exchange_rate` | `exchange_rate` | `(pair_code, price_date)` upsert |
| `news` | `news`, `dart`(=`disclosure`) | `scripts/collect_universe.py` x2(top300: 뉴스+공시, etf50: 뉴스만) | `news`, `disclosure` | 뉴스: 같은 종목·같은 url이면 저장 안 함. 공시: 접수번호가 든 `source_url`이 있으면 저장 안 함 |
| `embed` | `embeddings` | `app.embeddings.embed_news`, `embed_disclosure`(Ollama bge-m3) | `news.embedding`, `disclosure.embedding` | 임베딩이 없는 행만 |
| `content` | `disclosure_content` | `scripts/collect_disclosure_content.py --apply` | `disclosure.content` | 본문이 없는 행만 |
| `etf_holdings`(선택) | `etf_holdings` | `app.etf_holdings_collect.collect`(KODEX·TIGER 공시 PDF·구성 파일) | `etf_holding` | (ETF, 기준일) 단위 교체 + 고유키 `(company_id, as_of_date, holding_name, holding_code)` |
| (따로) | 분기 실적 | `refresh_data --earnings-only` → `app.earnings.collect` | `quarterly_earnings` | `(company_id, bsns_year, reprt_code)` |

뉴스와 공시는 기존 수집기에서 한 단계로 받는다. `--source dart`를 지정해도 news 단계가 돌아 뉴스도 같이 받는다. 공시만 받는 기존 옵션이 없고,
이번 작업에서 수집기 인터페이스는 바꾸지 않았다.

## 3. 데이터 소스별 수집 방식

| 소스 | 출처 | 요청 제한·재시도(기존 코드) |
|---|---|---|
| 주가·ETF 종가 | FinanceDataReader(KRX) | 호출당 상한 60초(`http_retry.with_timeout`) + 일시적 오류 2회 재시도. 종목 실패는 다음 종목으로 넘어간다 |
| 시장 지수 KS11·KQ11 | FinanceDataReader → 별칭 → Yahoo 심볼 폴백 | 같은 재시도 |
| 환율 USD/KRW | FinanceDataReader | 같은 재시도. 출처가 전일 값까지만 주는 날이 있다 |
| 뉴스 | 네이버 뉴스 검색 API(키: `.env`의 `NAVER_CLIENT_*`) | 요청 간 0.2초. 401·403·429면 재시도 뒤에도 바로 멈춘다(종료 코드 3 → 단계 `failed`, `rate_limited`) |
| DART 공시·본문·실적 | OpenDART(키: `DART_API_KEY`) | `http_retry.get`(5xx·429·연결 오류만 재시도, 4xx는 바로 실패) |
| ETF 구성종목 | 삼성자산운용 KODEX·미래에셋 TIGER 웹 공시 | 요청 간 3초. 403·429를 받은 호스트로는 그 실행 동안 더 요청하지 않는다. 일시적 오류만 30초 쉬고 한 번 더 |

API 일일 한도: 네이버 검색 API는 하루 25,000회, OpenDART는 키당 하루 20,000회로 안내돼 있다(각 서비스의 현재 정책은 운영 전에 다시 확인할 것).
news 단계 한 번은 네이버 약 400회(주식 300 + ETF 50 x 2, ETF는 이름으로 두 번 검색), DART 공시 목록 약 300회를 쓴다(본문 단계는 새 공시 수만큼 따로).

## 4. 실행 주기(권장)

| 데이터 | 권장 시각(KST) | 근거 |
|---|---|---|
| 주가·지수·환율 | 평일 16:40 | 장 마감 15:30 뒤 출처 갱신 시간 여유. 장중 실행은 당일 행이 장중 값이다(다음 실행이 종가로 덮어쓴다) |
| 뉴스·공시·임베딩·본문 | 평일 16:40(위 실행에 포함), 18:40(`--source dart,embeddings,disclosure_content`) | 공시는 장 마감 뒤에도 접수된다 |
| 뉴스(주말) | 토·일 12:00(`--source news,embeddings`, 선택) | 주말 기사 |
| ETF 구성종목 | 평일 16:40(`--etf-holdings`) | 운용사가 영업일마다 공시. look-through가 2영업일 넘게 지난 구성 정보를 `holdings_stale`로 표시하므로 매 영업일 1회 |
| 상태 보고서 | 평일 08:30(`--status`) | 전날 수집 결과를 장 시작 전에 확인. 종료 코드 3이면 확인 필요 |
| 기록 정리 | 일요일 03:00(`--prune-logs`) | 180일 보관 |
| 분기 실적 | 분기 보고서 공시 뒤 수동(`--earnings-only`) | 종목당 12회, 약 3,600회. 자동 실행 대상 아님 |

기존 기록(2026-10~11, `data_refresh_log`)의 단계 소요 시간: prices 평균 175초, index 1초, fx 1초, news 평균 339초(최대 380초), embed 76초,
content 평균 142초(최대 212초). 전체 약 12분이다. etf_holdings는 기존 문서 기준 3~4분이다(이번에 실행하지 않음).
단계별 제한 시간은 `--timeout-min`(기본 30분)이다.

## 5. 거래일과 휴장일 처리

2026-11-08(36차)부터 **KRX 거래일 달력**(`app/krx_calendar.py`, 데이터 `app/data/krx_calendar.json`, 적용 범위 2023-10-30~2026-12-31)을 쓴다.
자세한 출처·갱신 방법은 **`docs/DATA_QUALITY_AND_TRADING_CALENDAR.md`** 7·10절.

- 주가·지수·ETF 구성: 영업일 = KRX 거래일(주말·법정 공휴일·대체공휴일·선거일·임시공휴일·근로자의 날·연말 휴장 제외). 휴장일은 지연이 아니다.
- 환율·뉴스·공시: KRX 휴장과 무관해 **평일** 기준이다(환율 출처는 한국 휴장일·일요일 날짜에도 값을 준다).
- 기대 기준일(`expected_as_of`): 주가·지수는 "종가가 있어야 할 가장 최근 거래일"(거래일 15:30 전이면 직전 거래일, 휴장일 오전은 장중이 아니다),
  환율은 같은 규칙의 평일, 뉴스·공시는 최근 평일, ETF 구성은 최근 거래일.
- 지연(`lag_business_days`)이 **2영업일 이상**이면 `stale`이다. 달력 범위 밖·미확정 날(2026-12-31)은 `lag_calendar_unknown_days`와 사유 `calendar_unknown`으로 밝힌다.
- 주말·휴장일에 실행해도 출처에 새 행이 없으므로 저장되는 행이 없다. 스케줄은 평일에 건다(휴장일 실행은 무해하다).

## 6. 전체 수집

```powershell
cd C:\project\lab-scale\backend
.venv\Scripts\python.exe -m scripts.refresh_data --all                  # 기본 6단계
.venv\Scripts\python.exe -m scripts.refresh_data --all --etf-holdings   # + ETF 구성종목
.venv\Scripts\python.exe -m scripts.refresh_data --dry-run --etf-holdings   # 계획만(호출·기록 없음)
```

## 7. 개별 수집

```powershell
.venv\Scripts\python.exe -m scripts.refresh_data --source stock_price          # prices 단계(주식·ETF 종가)
.venv\Scripts\python.exe -m scripts.refresh_data --source market_index,exchange_rate
.venv\Scripts\python.exe -m scripts.refresh_data --source dart                 # news 단계(뉴스+공시)
.venv\Scripts\python.exe -m scripts.refresh_data --source etf_holdings
.venv\Scripts\python.exe -m scripts.refresh_data --only fx --step-retries 0    # 기존 단계 이름도 그대로
```

지원하지 않는 데이터(`kospi200`, `interest_rate`, `overseas_stock`)와 따로 실행하는 `earnings`를 `--source`로 주면 사용법 오류(종료 코드 2)다.

## 8. 상태 조회

```powershell
.venv\Scripts\python.exe -m scripts.refresh_data --status            # 표 + logs\refresh\refresh_status.{json,md}
.venv\Scripts\python.exe -m scripts.refresh_data --status --json     # JSON을 표준 출력으로(파일도 씀)
```

출력 예(2026-11-07 토요일 개발 DB 실제 값, 일부):

```
source                    status      usable  data_as_of  expected     lag  quality last_success
stock_price               stale       no      2026-11-03  2026-11-06     3  warning 2026-11-03T19:48:21+09:00
market_index              stale       no      2026-11-03  2026-11-06     3  ok      2026-11-03T19:48:22+09:00
exchange_rate             stale       no      2026-11-02  2026-11-06     4  warning 2026-11-03T20:03:27+09:00
etf_holdings              stale       no      2026-10-29  2026-11-06     6  warning -
earnings                  disabled    yes     2026-10-30  -              -  -       -
kospi200                  unsupported no      -           -              -  -       -
```

JSON 보고서의 소스별 필드: `source`, `status`, `usable_for_analysis`, `last_run_at`, `last_run_status`, `last_success_at`, `last_failure_at`,
`last_failure_reason`, `data_as_of`, `expected_as_of`, `lag_business_days`, `duration_ms`, `records_inserted`, `records_updated`, `records_skipped`,
`error_code`, `retry_count`, `quality_status`, `warning_count`, `quality_checks`, `reasons`, `schedule`, `origin`.
기존 수집기가 세지 않는 `records_updated`·`records_skipped`는 `null`이다. `records_inserted`는 단계 전후 행 수 차이다.
`etf_holdings`는 따로 실행한 수집(`scripts/collect_etf_holdings.py`)이 실행 기록을 남기지 않아서 `last_success_at`이 비어 있을 수 있다.
그때는 `extra.last_collected_at`(`etf_holding.collected_at` 최대값)을 본다.

**상태 값**(우선순위 순):

| 상태 | 뜻 |
|---|---|
| `unsupported` | 수집기가 없다(KOSPI200, KODEX·TIGER 외 ETF 구성, 금리, 해외 주식) |
| `disabled` | 자동 실행 대상이 아니다(분기 실적) |
| `running` | 지금 실행 중인 갱신이 이 단계를 포함한다(락이 잡혀 있음) |
| `never_run` | 실행 기록도 데이터도 없다 |
| `failed` | 마지막 시도가 실패 |
| `partial` | 마지막 시도에서 일부 대상(종목·ETF)만 실패(절반 미만) |
| `stale` | 데이터 기준일이 기대 기준일보다 2영업일 이상 늦거나 데이터가 없다 |
| `healthy` | 그 밖 |

웹 화면용 공개 API `GET /api/status`는 바꾸지 않았다(응답 필드 그대로). 관리자 권한 체계가 없어서 이 운영 상태(오류 코드·품질)를 새 API로 내보내지 않았다.

## 9. 재실행

```powershell
.venv\Scripts\python.exe -m scripts.refresh_data --retry-failed             # 마지막 시도가 failed·partial인 단계만
.venv\Scripts\python.exe -m scripts.refresh_data --retry-failed --dry-run   # 어떤 단계를 다시 할지만 출력
```

성공한 단계는 다시 받지 않는다. 다시 받아도 수집은 upsert·중복 검사라 기존 정상 행은 바뀌지 않는다. 같은 날짜의 값만 최신 값으로 갱신된다.

## 10. 오류 코드와 실패 처리

종료 코드:

| 코드 | 뜻 | 스케줄러에서 |
|---|---|---|
| 0 | 실행한 단계가 전부 성공하고 품질 검사 error 없음 | 정상 |
| 1 | 실패한 단계가 있음(다른 단계는 끝까지 실행됨) | 확인 후 `--retry-failed` |
| 2 | 사용법 오류(모르는 단계·데이터 이름, 미지원 데이터) | 등록 명령 확인 |
| 3 | 실패는 없지만 부분 성공 단계가 있거나 품질 검사 error. `--status`에서는 분석에 쓸 수 없는 소스가 있음 | 보고서 확인 |
| 4 | 다른 갱신이 실행 중(락). 이번 실행은 아무것도 하지 않음 | 정상(겹친 실행) |
| 5 | DB에 연결할 수 없음 | Docker `ai_investment_db` 확인 |

단계 오류 코드(`data_refresh_log.error_code`): `timeout`, `network`, `server_error`(이 셋은 일시적 오류라 단계 재시도 대상),
`rate_limited`(429·운용사 차단·네이버 401·403·429로 멈춤), `auth`(401·403), `http_error`(그 밖의 4xx), `bad_data`(형식 오류),
`failure_ratio`(종목별 실패가 절반 이상), `collector_error`, `script_exit`, `unknown`.
품질 격리(2026-11-08): 수집은 끝났지만 저장 전 검증에서 격리한 레코드가 있으면 단계가 `partial`, `error_code=quality_quarantined`다(수집 실패 코드와 별개,
종료 코드 3). 격리 확인·재처리는 `python -m scripts.quarantine`(`docs/DATA_QUALITY_AND_TRADING_CALENDAR.md` 5절).
예외 메시지는 DB에 저장하지 않고(URL·키가 섞일 수 있음) 화면 출력만 하며, 화면 출력에서도 키 값은 `***`로 가린다.

단계 결과는 `success` / `partial` / `failed` / `skipped`다. 실행 결과(`data_refresh_run.status`)는 실행한 단계가 전부 실패면 `failed`,
일부 실패나 부분 성공이면 `partial`, 아니면 `success`다. 전체를 단순히 `success`로 묶지 않는다.

## 11. 중복 실행 방지

- 갱신 실행 전체가 Postgres **세션 advisory lock** 하나(`app/refresh_runs.LOCK_KEY`)를 잡는다. 다른 실행이 잡고 있으면 바로 종료 코드 4로 끝난다.
- 락 연결은 풀을 쓰지 않는 전용 연결이다. 프로세스가 죽으면(강제 종료·정전) 연결이 끊기면서 Postgres가 락을 풀어 준다. 파일 락처럼 남아서
  다음 실행을 막는 일이 없고, 따로 만료 시간을 둘 필요도 없다.
- 끝나지 못한 실행의 `data_refresh_run` 행(`running`)은 다음 실행이 락을 잡은 뒤 `aborted`로 바꾼다. 락을 잡았다는 것은 살아 있는 다른 실행이 없다는 뜻이다.
- 락은 행·표를 잠그지 않아 API 서버의 조회·주문·저장을 막지 않는다(통합 테스트가 확인). 수집은 종목 단위로 커밋해서 긴 표 잠금이 없다.
- 락은 하나다. 같은 외부 출처의 요청 제한을 지키기 위해 서로 다른 단계도 동시에 돌리지 않는다.
- 스케줄러에 같은 시각의 작업을 두 개 걸어도 하나만 실행된다. 스케줄러 쪽 설정("이미 실행 중이면 새 인스턴스 시작 안 함")도 같이 켜 둔다.

## 12. 재시도 정책

| 수준 | 대상 | 규칙 |
|---|---|---|
| 요청(기존 `app/http_retry`) | 연결 실패·timeout·5xx·429 | 최대 2회, 0.5초 x 2^n, 합계 60초 상한. 4xx(429 제외)·파싱 오류는 다시 하지 않는다 |
| ETF 운용사(기존) | 일시적 오류로 실패한 ETF | 전체가 끝난 뒤 30초 쉬고 한 번 더. 403·429 호스트는 그 실행 동안 요청하지 않는다 |
| 단계(이번 추가) | 단계 오류 코드 `timeout`·`network`·`server_error` | `--step-retries`(기본 1)번, 60초 x 2^n 대기. `rate_limited`·`auth`·`bad_data`·`failure_ratio`는 다시 하지 않는다(같은 결과이거나 상대 서버에 부담) |

재시도 횟수는 `data_refresh_log.retry_count`와 보고서에 남는다. 같은 요청을 무한히 반복하지 않는다.

## 13. 데이터 품질 검사

실행 뒤와 `--status`에서 읽기 전용 SQL로 검사한다(`app/data_monitor.QUALITY_CHECKS`). **error**는 그 데이터를 분석에 쓰면 안 되는 문제이고
(`usable_for_analysis=false`, 실행 종료 코드 3), **warning**은 확인이 필요한 이상이다.

| 소스 | error | warning |
|---|---|---|
| 주가 | 종가 null·0 이하, 미래 기준일, 같은 종목·날짜 중복 | 주식 하루 ±30% 초과(가격제한폭 밖, 최근 60일), 마지막 날짜가 전체 최신일보다 이른 종목, 시세가 하나도 없는 종목 |
| 지수 | 0 이하, 미래 기준일 | 한 지수만 늦음, 주가 거래일인데 KS11이 없는 날, KS11 거래일인데 주가가 없는 날(최근 60일) |
| 환율 | 0 이하, 미래 기준일 | 하루 ±5% 초과, KS11 거래일인데 환율이 없는 날(최근 60일) |
| 뉴스 | 1시간 넘게 미래인 발행 시각 | 같은 종목·url 중복, 발행 시각·링크·종목 연결 없음, 최근 7일 저장분 중 발행이 30일 넘게 전인 기사 |
| 공시 | 접수번호(`source_url`) 중복, 미래 접수일 | 접수번호 없음, 접수일 없음, 접수번호 앞 8자리와 저장한 접수일이 다름 |
| ETF 구성 | 마지막 기준일에 비중 100% 초과, 양의 비중 합계 0 이하, ETF가 아닌 종목에 구성 | 음수 비중, 양의 비중 합계 95% 미만·100.5% 초과, 같은 기준일 같은 종목코드 두 번, 2영업일 넘게 지난 구성, 구성이 없는 KODEX·TIGER |
| 임베딩·본문 | - | 최근 7일 저장분 중 임베딩·본문 없음 |

ETF 기준은 `app/look_through`와 같다. 비중 100% 초과 또는 양의 합계 0 이하는 look-through가 분해하지 않는 경우이고, 95%·100.5%는 `app/exposure`의 경고 기준이다.
검사 SQL이 실패하면 그 검사는 `count: null`, `error`로 남는다(검사를 못 했으면 통과가 아니다).

2026-11-07 개발 DB 결과: error 0건. warning은 다음과 같다.
- 주식 하루 +33% 1건(삼현 2026-09-15)
- 환율이 비어 있는 거래일 5일(9-11, 9-18, 10-02, 10-16, 10-23)
- 접수번호 날짜와 접수일이 1~4일 다른 공시 5건(주로 정정 공시)
- 발행 30일 넘은 기사 13건
- ETF 음수 비중 7행, 비중 합계 범위 밖 7개, 구성 기준일 지연 38개 ETF 전부(10-29~10-30)

**저장 전 검증(2026-11-08, 36차)**: 위 SQL 검사는 저장된 데이터를 보는 사후 검사다. 이제 수집기가 **저장하기 전에** 레코드마다 검증해
불가능한 값·연결 오류·기존 정상 값과 크게 어긋나는 값은 저장하지 않고 `data_ingestion_quarantine`에 격리한다(기존 행을 갱신하지 않음).
규칙은 `docs/DATA_QUALITY_AND_TRADING_CALENDAR.md` 3절. `--status` 보고서에 소스별 미해결 격리 건수가 나온다(`ingestion_quarantine`).
접수번호 날짜와 접수일이 다른 공시 5건은 모두 다음 영업일 접수였다(데이터 오류 아님, 같은 문서 3절).

## 14. 최신성 기준

| 소스 | 데이터 기준일(`data_as_of`) | 기대 기준일 | stale |
|---|---|---|---|
| 주가·ETF 종가 | 자산 유형별 최대 `price_date` | 종가가 있어야 할 최근 **KRX 거래일**(15:30 기준) | 2거래일 이상 늦음 |
| 지수 | 두 지수 중 더 늦은 쪽의 최대 날짜 | 같음 | 같음 |
| 환율 | 최대 `price_date` | 최근 **평일**(15:30 기준) | 2평일 이상(1일 늦음은 정상 범위) |
| 뉴스 | 최대 `published_at`(KST 날짜) | 최근 평일 | 2영업일 이상 |
| 공시 | 최대 `disclosed_at`(KST 날짜) | 최근 평일 | 2영업일 이상 |
| ETF 구성 | 수집된 ETF들의 마지막 기준일 중 가장 이른 것 | 최근 KRX 거래일 | 2거래일 이상(look-through의 `holdings_stale`과 같은 규칙) |
| 임베딩·본문 | 날짜 없음 | - | 마지막 실행 결과·품질로만 판단 |

수집 시각과 데이터 기준일은 다르다. 예: 2026-11-03 19:48에 수집했고 주가 기준일은 2026-11-03이다. 2026-11-07(토)의 기대 기준일은 2026-11-06이라
지연 3영업일, `stale`이다.

## 15. 수집 이력 구조

마이그레이션 `b8d4f2a6c913`(2026-11-07)에서 다음을 바꿨다.

`data_refresh_run`(실행 한 건): `id`, `started_at`, `finished_at`, `heartbeat_at`(단계가 끝날 때마다 갱신), `status`(`running`/`success`/`partial`/`failed`/`aborted`),
`trigger`(`manual`/`scheduler`/`retry`), `steps`(실행한 단계), `exit_code`, `pid`, `note`.

`data_refresh_log`(단계 한 건, 기존 표 확장): 기존 `step`, `status`(+`partial`), `started_at`, `finished_at`, `rows_added`, `latest_date`, `error_type`에
`run_id`, `duration_ms`, `retry_count`, `quality_status`, `warning_count`, `error_code`를 더했다. 모두 null 허용이라 기존 행은 그대로다.

보관: `--prune-logs`가 `--keep-days`(기본 180, 최소 30)보다 오래된 행을 지운다. 단계마다 마지막 성공·마지막 실패 행은 오래돼도 남기고, 실행 중인 실행은 지우지 않는다.
수집 데이터(주가·뉴스 등)는 지우지 않는다. 지금 가장 오래된 기록은 2026-10월이라 정리 대상이 없다.

## 16. 로그 확인

- 실행 출력: 단계 진행(30초 간격), 단계별 결과 표, 실패·부분 성공 사유, 데이터 상태 표, 알림(`[ALERT:error] ...`는 표준 오류). 비밀값은 `***`로 가린다.
- 스케줄러에 등록할 때 출력을 파일로 보낸다(17·18절, `backend/logs/refresh/`는 커밋되지 않는다). 파일 이름에 월을 넣으면 크기가 무한히 커지지 않는다.
- 상태 보고서: `backend/logs/refresh/refresh_status.json`·`.md`. 실행할 때마다 덮어쓴다.
- DB에서 직접 확인:

```sql
SELECT id, started_at, finished_at, status, trigger, steps, exit_code, note FROM data_refresh_run ORDER BY id DESC LIMIT 10;
SELECT step, status, error_code, retry_count, duration_ms, rows_added, latest_date, quality_status
FROM data_refresh_log WHERE run_id = (SELECT max(id) FROM data_refresh_run) ORDER BY id;
```

## 17. Windows 작업 스케줄러

PowerShell(관리자 권한 불필요, 현재 사용자로 등록)에서 운영자가 직접 실행한다. 이 저장소의 작업은 등록하지 않았다.
경로는 `C:\project\lab-scale\backend` 기준이다. 수집에는 Docker DB가, 임베딩 단계에는 Ollama가 켜져 있어야 한다.

```powershell
$backend = "C:\project\lab-scale\backend"
$py = "$backend\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force "$backend\logs\refresh" | Out-Null

function New-RefreshTask($name, $cliArgs, $trigger) {
  # 월별 로그 파일, 출력과 오류를 함께 기록. 같은 작업이 실행 중이면 새로 시작하지 않는다(IgnoreNew)
  $cmd = "-NoProfile -Command `"Set-Location '$backend'; & '$py' -m scripts.refresh_data $cliArgs *>> ('logs\refresh\refresh_' + (Get-Date -Format yyyyMM) + '.log'); exit `$LASTEXITCODE`""
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $cmd -WorkingDirectory $backend
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3) -StartWhenAvailable
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description "lab-scale data refresh"
}

$weekdays = "Monday","Tuesday","Wednesday","Thursday","Friday"
New-RefreshTask "lab-scale refresh close" "--trigger scheduler --etf-holdings" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 16:40)
New-RefreshTask "lab-scale refresh dart"  "--trigger scheduler --source dart,embeddings,disclosure_content" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 18:40)
New-RefreshTask "lab-scale refresh status" "--status" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 08:30)
New-RefreshTask "lab-scale refresh prune" "--prune-logs" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 03:00)
```

확인·해제:

```powershell
Get-ScheduledTask -TaskName "lab-scale refresh*" | Get-ScheduledTaskInfo   # LastRunTime, LastTaskResult(= 종료 코드)
Start-ScheduledTask -TaskName "lab-scale refresh status"                    # 바로 한 번 실행
Unregister-ScheduledTask -TaskName "lab-scale refresh close" -Confirm:$false
```

`LastTaskResult`가 종료 코드다(10절). PC가 꺼져 있어 놓친 실행은 `-StartWhenAvailable`로 켜진 뒤 실행된다. 장중에 실행되면 경고가 나오고, 다음 실행이 종가로 덮어쓴다.

## 18. 서버 cron(Linux)

서버로 옮길 때의 예다. 시간대는 KST로 둔다. 이 프로젝트는 아직 서버 배포 설정이 없다.

```cron
CRON_TZ=Asia/Seoul
40 16 * * 1-5  cd /srv/lab-scale/backend && .venv/bin/python -m scripts.refresh_data --trigger scheduler --etf-holdings >> logs/refresh/refresh_$(date +\%Y\%m).log 2>&1
40 18 * * 1-5  cd /srv/lab-scale/backend && .venv/bin/python -m scripts.refresh_data --trigger scheduler --source dart,embeddings,disclosure_content >> logs/refresh/refresh_$(date +\%Y\%m).log 2>&1
30 8  * * 1-5  cd /srv/lab-scale/backend && .venv/bin/python -m scripts.refresh_data --status >> logs/refresh/status.log 2>&1
0  3  * * 0    cd /srv/lab-scale/backend && .venv/bin/python -m scripts.refresh_data --prune-logs >> logs/refresh/prune.log 2>&1
```

겹친 실행은 advisory lock이 막는다(종료 코드 4). API 서버(uvicorn worker) 안에서는 수집을 돌리지 않는다. worker마다 중복 실행되고 요청 처리를 붙잡기 때문이다.
GitHub Actions scheduled workflow는 쓰지 않는다. CI 러너는 개발 DB·`.env` 키에 접근할 수 없고, 외부 수집 비밀값을 CI에 올리지 않는다.

## 19. 수집 실패 시 복구 절차

1. `--status`로 어느 소스가 `failed`·`partial`·`stale`인지와 `last_failure_reason`(오류 코드)을 본다.
2. 원인별 조치:
   - 코드 5·`network`: Docker `ai_investment_db`와 네트워크를 확인한다.
   - `auth`: `.env`의 키를 확인한다(이 문서는 키를 다루지 않는다).
   - `rate_limited`: 다음 날 다시 하거나, 한도를 확인한 뒤 `--source`로 그 데이터만 다시 한다.
   - `failure_ratio`·`collector_error`: 실행 로그에서 종목별 실패 줄을 본다.
   - `timeout`: 출처가 느린 경우다. `--timeout-min`을 조정한다.
3. `--retry-failed`로 실패한 단계만 다시 실행한다. 성공한 단계는 다시 받지 않는다.
4. 실행이 중간에 끊긴 경우(정전·강제 종료)에는 따로 할 일이 없다. 다음 실행이 락을 잡고 남은 `running`을 `aborted`로 정리하고, 같은 단계를 다시 받아도 upsert라 안전하다.
5. 날짜 공백이 있으면 `--days N`으로 기간을 넓혀 다시 받는다(기본 기간도 최근 30일의 빈 평일까지 넓힌다).
6. 품질 error면 해당 SQL(13절)로 행을 찾아 원인을 확인한다. 자동 삭제·수정은 하지 않는다.

## 20. 운영 시 주의사항

- 장중(평일 09:00~15:30)에 돌리면 당일 행이 장중 값이다. 16:40 실행을 권장한다.
- embed 단계는 Ollama(bge-m3)가 필요하다. Ollama가 꺼져 있으면 그 단계만 실패하고 나머지는 진행된다.
- `.env`의 키는 이 스크립트가 읽기만 한다. 보고서·DB 기록에 키가 남지 않는다.
- 뉴스 수집은 네이버 일일 한도를 쓴다. 같은 날 여러 번 돌릴 때는 한도를 계산한다(3절).
- 실적 수집(`--earnings-only`)은 분기마다 수동으로 한다(약 3,600회).
- 백엔드 서버(8000)를 재시작할 필요가 없다. 수집은 별도 프로세스이고, 서버는 DB의 새 행을 바로 읽는다.

## 21. 알려진 한계와 아직 자동화되지 않은 것

- **스케줄러 미등록**: 17·18절의 등록은 운영자가 직접 해야 한다.
- **외부 알림 없음**: 알림은 표준 오류·로그로만 나간다. `app.data_monitor.Notifier`를 구현해 Slack·이메일을 붙일 수 있다. 토큰은 환경변수로 받고 코드에 두지 않는다.
- **관리자 API 없음**: 권한 체계가 없어 운영 상태 API를 만들지 않았다(CLI만).
- ~~공휴일 달력 없음~~ → 36차에 KRX 거래일 달력 추가. 단 2026-12-31(미확정)과 2027년 이후는 KRX 공지 뒤 갱신해야 한다.
- ~~저장 전 격리 없음~~ → 36차에 저장 전 검증·격리 추가. 이미 저장된 과거 데이터는 사후 SQL 검사로만 본다.
- **특별 운영일**: 수능일처럼 마감이 16:30인 날은 달력에 아직 넣지 않았다(KRX 공지 뒤 `special_sessions`).
- **단계 제한 시간**: 진행 출력이 있는 동안만 감시한다. 출력 없이 멈춘 외부 호출은 각 수집 코드의 요청 timeout(최대 60초 x 재시도)에 기댄다.
- **공시만 수집 불가**: `dart`는 news 단계(뉴스와 함께)다.
- **정정 공시 연결**: 원 공시와 정정 공시를 잇는 정보(원 접수번호)를 저장하지 않아 검사하지 못한다.
- **DART 기업코드 대조**: 공시 표에 기업코드를 저장하지 않아 대조하지 못한다.
- **미지원 데이터**: KOSPI200 지수, KODEX·TIGER 외 운용사 ETF 구성종목, 금리, 해외 주식은 수집기가 없어 `unsupported`로만 표시한다.
- **실시간 시세**: `GET /api/realtime/...`는 요청 때 조회하며 저장하지 않으므로 이 모니터링 대상이 아니다.

## 22. 이번 작업의 검증 범위

| 항목 | 검증 방법 | 결과 |
|---|---|---|
| 단계 실행·실패 격리·부분 성공·재시도(5xx·timeout·연결)·재시도 안 함(401·429·형식)·종료 코드·단계 선택·CLI·락 충돌(코드 4)·DB 불가(코드 5) | mock 단위 테스트 `tests/test_data_refresh_ops.py` | 통과 |
| 상태 판정(주말·장중·1/3영업일 지연·뉴스·공시·ETF·환율 없음·최근 실패와 마지막 성공 분리·품질 error)·보고서 JSON·Markdown·알림 | 같은 파일 | 통과 |
| advisory lock 획득·충돌·해제, 연결이 끊기면 락 해제, 락을 잡은 동안 API 조회·DB 쓰기, 실행 기록·중단 복구, 기록 보관, 품질 SQL(읽기 전용), 실제 CLI 실행 기록, 재수집 멱등성(합성 환율) | 개발 DB 통합 테스트 `tests/test_data_refresh_ops_devdb.py` | 통과 |
| 실제 외부 수집(FinanceDataReader·네이버·DART·운용사) | 실행하지 않음(보호 규칙) | **검증 안 함**. 수집 코드는 바꾸지 않았다 |
| Windows 작업 스케줄러·cron 등록 | 등록하지 않음 | 명령만 문서화 |
| (36차) 저장 전 검증·격리, 기존 정상 값 보호, 재검증·재처리, 실행기 부분 성공(`quality_quarantined`), KRX 거래일 달력·데이터별 최신성 정책 | `tests/test_data_quality_calendar.py`(순수), `tests/test_quarantine_devdb.py`(개발 DB, 가짜 종목·가짜 원격 응답) | 통과. 실제 외부 수집 응답으로는 **검증 안 함** |
