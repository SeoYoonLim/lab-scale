# 62차 뉴스·공시 누락 구간 복구와 정기 수집 재개 준비 (2026-11-15)

**이번 작업에서는 네이버·DART에 요청하지 않았고 개발 DB에 쓰지 않았다.** 복구 도구·테스트·실행 계획까지 만들고 멈췄다.
실제 수집(외부 요청 + `news`·`disclosure` INSERT)은 14절 명령을 **승인받은 뒤** 실행한다.
기존 뉴스·공시는 지우거나 고치지 않는다. API 응답·DB 스키마는 바꾸지 않았다.

## 1. 현재 브랜치와 기준 커밋

| 항목 | 값 |
| --- | --- |
| 브랜치·커밋 | `feature/backend`, `77647e1`(61차), 원격과 같음, 작업 트리 깨끗함 |
| 서버 | PID 40824, 건드리지 않음 |
| 확인한 것 | 61차 보고서, `API.md` 뉴스·공시 절, `app/collectors.py`, `scripts/collect_universe.py`, `scripts/refresh_data.py`(argparse·`news` 단계), `app/refresh_runs.py`, 모델 `DataRefreshRun`·`DataRefreshLog`, `app/http_retry.py`, `docs/DATA_REFRESH_MONITORING.md` |

요청서 2절의 5개 항목을 코드로 확인한 결과:

| # | 확인 | 결과(근거) |
| --- | --- | --- |
| 1 | `refresh_data --only news`가 뉴스·공시를 모두 수집하나 | **예.** `news` 단계가 `collect_universe`를 두 번 부른다. `universe_top300.json`은 뉴스+공시, `universe_etf50.json`은 뉴스만(ETF는 공시 대상 아님). `--news-only`면 공시를 뺀다. 임베딩(`embed`)·공시 본문(`content`)은 별도 단계다 |
| 2 | 뉴스에 페이지 넘김이 없나 | **예(없음).** `fetch_naver_news`가 `start=1` 한 번만 요청했다. 주식은 최신순 30건을 받아 10건을 고르고, ETF는 최신순·관련도순 각 100건을 받아 이름이 나오는 기사만 10건 고른다 |
| 3 | DART 90일 조회가 누락 기간을 포함하나 | **예.** `fetch_dart_disclosures(corp_code, days=90)`는 `bgn_de = 오늘 - 90일`, `end_de = 오늘`, `page_count=100`, `total_page`까지 받는다. 2026-11-04는 그 안이다 |
| 4 | 개별 수집 스크립트가 실행 기록을 남기지 않나 | **예.** `data_refresh_log`는 `refresh_data.write_log`만 쓴다(`collect_universe`·`collect_prices` 등은 쓰지 않음, 61차 테스트) |
| 5 | 같은 데이터를 다시 받으면 중복 저장하지 않나 | **예.** 뉴스는 (종목, URL), 공시는 원문 URL(= 접수번호)로 저장 전에 확인한다. DB 유일 제약은 없다(앱 코드) |
| — | 주의 | 기존 `fetch_and_save_news(dry_run=True)`도 **네이버에 실제로 요청한다**(DB 쓰기만 안 함). `dry_run`이라는 이름이 네트워크 없음을 뜻하지 않는다 |

## 2. 뉴스·공시 공백 구간

| 데이터 | 마지막 수집(KST) | 공백 시작 | 공백 끝 |
| --- | --- | --- | --- |
| 뉴스 | 2026-11-03 19:54:42 | 2026-11-04 00:00 | 실제 실행일 |
| 공시 | 2026-11-03 19:53:51(접수일 11-03까지) | 2026-11-04 접수분 | 실제 실행일 |

- 2026-11-04 이후 게시된 뉴스, 11-04 이후 접수된 공시는 DB에 0건이다(`plan` 출력 `existing_in_window` 0, 61차 감사).
- 2026-11-15 기준 거래일 8일(11-04~11-13)이다. 11-16(월)에 실행하면 9일이다.

## 3. 뉴스 페이지 넘김 조사 결과

네이버 뉴스 검색 범위(네이버 클라우드 공식 API 문서 [뉴스 검색](https://api.ncloud-docs.com/docs/naver-api-hub-search-news.md)):

| 항목 | 값 |
| --- | --- |
| `display` | 1~100(기본 10) |
| `start` | 1~1000(기본 1) |
| `sort` | `sim`(기본)·`date`(날짜 내림차순) |
| 응답 | `total`, `start`, `display`, `items[]`(`title`, `originallink`, `link`, `description`, `pubDate`) |
| 오류 | 400 SE02(`display` 범위), SE03(`start` 범위), SE04(`sort`), 500 SE99 |
| 하루 호출 한도 | 25,000회 |

- 이 프로젝트가 쓰는 엔드포인트는 `openapi.naver.com/v1/search/news.json`이다(네이버 개발자 센터). 개발자 센터 문서는 이 환경에서 열리지 않았다.
- 같은 파라미터 범위·한도라는 것은 **[추정]**이다(같은 검색 API 계열이고, 기존 코드의 `display` 최대 100 가정과 맞는다).
- 한 검색어로 받을 수 있는 결과는 **최신 1,000건**이다(`start` 1, 101, …, 901 x `display` 100 = 10페이지).
  - `start + display - 1 > 1000` 조합을 거절한다는 프록시 문서가 있다. 그래서 도구는 `start ≤ 901`(10페이지)까지만 쓴다.
- 날짜로 직접 거르는 파라미터는 없다. 날짜순으로 받다가 기간 시작보다 오래된 기사가 나오면 멈춘다.
- 오래된 기사의 검색 가능 범위: 시간 범위가 아니라 **결과 개수(1,000건)**가 한계다.
  - 기사가 많은 종목(예: 삼성전자)은 1,000건이 며칠 분량일 수 있어 공백 앞부분을 받지 못할 수 있다.
  - 이 경우 도구가 `truncated=true`로 기록한다. 실제 분량은 요청 전에는 알 수 없다(**[미확인]**).
- 과거 기록: 2026-10-28~11-03 실행 1,331회(종목 x 실행일) 중 **74%가 10건 상한에 걸렸다**. 실행 사이(1~3일)에 10건 넘게 나오는 종목이 대부분이었다.

## 4. 네이버 요청 제한과 복구 범위

| 항목 | 값 |
| --- | --- |
| 대상 | 350종목(주식 300 + ETF 50, `refresh_data`와 같은 유니버스) |
| 종목당 요청 | 최소 1(기간 시작 전 기사가 첫 페이지에 나오면) ~ 최대 10 |
| 전체 요청 | **350 ~ 3,500회**(재시도 별도) |
| 하루 한도 | 25,000회 → 최대치도 한도의 14%다 |
| 실행 상한 | `--naver-budget` 기본 20,000. 다음 종목이 최대 페이지를 써도 상한을 넘지 않을 때만 진행 |
| 요청 간격 | 0.2초(기존 `collect_universe`와 같음) |
| 재시도 | `http_retry`: 연결 오류·timeout·5xx·429만 지수 백오프로 다시 시도. 그 밖의 4xx는 바로 실패 |
| 막힘 | 401·403·429가 나오면 나머지 종목을 요청하지 않고 멈춘다(`stopped=naver_blocked`) |

복구 결과는 종목마다 다음으로 나뉜다(보고서 JSON의 `rows[].news`):

| 구분 | 필드 |
| --- | --- |
| API 범위 안에서 복구한 뉴스 | `new`(그중 관련 기사 `relevant_new`) |
| 이미 DB에 있던 뉴스 | `existing` |
| API 검색 범위 때문에 받지 못함 | `truncated=true`, `stop=max_window`(그 종목의 가장 오래된 받은 기사 `oldest_seen`보다 앞은 받지 못함) |
| 시작일 이전 기사 | `out_of_range`(기간 밖이라 버림) |
| 호출 제한으로 미완료 | 요약의 `stopped`, `not_processed` |
| 기사가 실제로 없음 | 요약의 `no_articles_in_window` |
| 걸러낸 것 | `non_financial`(비금융 도메인), `etf_name_missing`(ETF 이름 없음), `quarantined`(저장 전 검증 실패) |

## 5. DART 최근 90일 복구 가능 범위

| 항목 | 값(DART 공식 문서 [공시검색](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001)) |
| --- | --- |
| 요청 | `list.json`, `corp_code` 지정, `bgn_de`~`end_de`, `page_no`, `page_count` 1~100 |
| 기간 제한 | `corp_code`를 주면 문서에 기간 제한이 없다(주지 않으면 3개월) |
| 응답 | `total_count`, `total_page`, `list[]`(`rcept_no` 14자리, `rcept_dt`, `report_nm`, `corp_name`, `flr_nm` …) |
| 오류 | 013(조회된 데이터 없음 → 빈 목록), **020(요청 제한 초과, "일반적으로 20,000건 이상")**, 그 밖의 코드는 실패 |
| 하루 한도 | 문서에 일 단위 한도 명시 없음. 020 기준 20,000건 |

- 복구 기간: 도구는 시작일(11-04)부터 오늘까지만 조회한다(`--disclosure-days` 기본 = 시작일~오늘, 최대 90).
  - 11-16 실행이면 13일이다. 공백이 90일보다 짧아서 범위 밖 공시는 없다.
  - `--disclosure-days 90`이면 기존 수집과 같은 90일을 다시 본다(이미 있는 접수번호는 건너뜀).
- 페이지: 종목별 `total_page`까지 받는다. 13일 창이면 사실상 종목당 1페이지다.
  - 90일 창에서는 기존 90일 건수가 가장 많은 종목이 478건이라 5페이지다. 100건 넘는 종목은 7개다.
- 중복: 접수번호 원문 URL이 이미 있으면 건너뛴다(정정 공시는 접수번호가 달라 별개 행).
- 회사 명의가 아닌 공시(제출인 ≠ 회사)는 기존과 같이 저장하지 않는다.
- `get_dart_corp_code`가 처음 한 번 `corpCode.xml`(11만 건 zip)을 받는다. 이것도 DART 요청 1회다.

## 6. 신규 복구 도구 구조

| 파일 | 역할 |
| --- | --- |
| `backend/app/news_recovery.py`(신규) | `paginate`(날짜순 페이지 넘김과 멈춤 조건), `store_news`(기존 수집과 같은 걸러내기·검증·관련성 판정으로 저장), `news_query`, `kst_window`, `disclosure_days` |
| `backend/scripts/recover_news_disclosures.py`(신규) | CLI·안전 장치·실행 계획·본체(`run`)·실행 기록 |
| `backend/app/collectors.py`(작은 변경) | `fetch_naver_news(..., start=1)` 선택 인자(기본값 = 예전과 같음), DART 요청 계수기 `dart_requests` |

모드(`--mode`, 기본 `plan`):

| 모드 | 외부 요청 | DB | 필요한 인자 |
| --- | --- | --- | --- |
| `plan` | **없음**(외부 연결을 막고 실행해 확인) | 읽기만(`SET TRANSACTION READ ONLY`) | 없음 |
| `dry-run` | **있음**(네이버·DART) | 읽기만(이미 있는 행 확인), 쓰기 없음 | `--allow-network`, DB 이름 `ai_investment` |
| `apply` | 있음 | 쓰기(아래 표) | `--allow-network`, `--confirm-database ai_investment`, 연결 뒤 `current_database()` 재확인 |

지원 기능(요청서 6절):
- 날짜 범위: `--start`(기본 2026-11-04), `--end`(기본 오늘 KST)
- 종목: `--tickers`(기본 유니버스 350)
- `--dry-run`: `--mode dry-run`
- 예상 요청 수: `plan`
- 신규 저장 예상 건수: 공시는 `plan`의 과거 비율 추정, 뉴스는 `dry-run`
- 중복 건너뛰기, 실패 종목 기록(`failed[]`)
- 중간 실패 뒤 재실행: `--state` 파일. 종목·종류(뉴스·공시)마다 끝나면 기록하고, 다시 실행하면 건너뛴다. 기간·설정이 다른 상태 파일은 거부한다.
- 결과 보고서: `--report` JSON(요약 + 종목별 행)
- 대상 목록은 `backend/scripts/universe_top300.json`·`universe_etf50.json`(`refresh_data`와 같음)이다.
  - 이 두 파일은 `build_universe`·`build_etf_universe`가 만들고 Git에 올리지 않는다(`.gitignore`). 로컬에는 있다(350종목 확인).

지키는 조건:
1. DB 주소를 실행 전에 출력한다(`대상 DB: … (호스트 …) · 모드 …`). dry-run·apply는 DB 이름이 `ai_investment`가 아니면 거부한다.
2. 쓰는 표는 `news`, `disclosure`, `data_ingestion_quarantine`(저장 전 검증 기록, 기존 수집과 같음), `data_refresh_run`·`data_refresh_log`(실행 기록)뿐이다.
   - 사용자·포트폴리오·거래·시세 등 나머지 표는 건드리지 않는다(`plan` 출력 `tables_never_touched`가 실제 표 목록에서 계산).
3. 기존 뉴스·공시를 지우거나 고치지 않는다. 새 행만 더한다.
4. 이미 있는 (종목, URL)·접수번호는 건너뛴다(기존 행 유지). 같은 실행을 다시 해도 0건이다(통합 테스트).
5. 종목을 새로 만들지 않는다(등록되지 않은 종목은 `company_not_registered`로 실패 기록).
6. apply는 `refresh_data`와 같은 advisory lock을 잡는다. 동시에 돌면 거부한다.

## 7. 예상 API 호출량

`plan` 실행(2026-11-15, 개발 DB 읽기 전용, 외부 연결 차단):

| 항목 | 값 |
| --- | --- |
| 기간 | 2026-11-04 00:00 ~ 2026-11-16 00:00 KST(11-15까지) |
| 대상 | 350(주식 300, ETF 50), 미등록 0 |
| 네이버 | 350 ~ 3,500회(종목당 최대 10쪽 = 1,000건) |
| DART | 약 301회(corpCode 1 + 주식 300종목 x 1쪽, 기간 12일) |
| 예상 시간 | 약 6 ~ 35분(요청당 0.35초 + 간격 0.2초 가정) |

## 8. 예상 신규 저장 건수

| 데이터 | 예상 | 근거와 한계 |
| --- | --- | --- |
| 공시 | **약 806건**(범위 400~1,576) | 시작 전 20거래일의 거래일당 접수 건수(평균 100.7, 최소 50, 최대 197, 대상 종목) x 기간 거래일 8. 11-16 실행이면 9거래일이라 약 906건 |
| 뉴스 | **요청 전에는 알 수 없다** | 외부 요청 없이 기사 수를 알 방법이 없다. 상한은 3,500쪽 x 100건이다(의미 있는 추정은 아님) |

뉴스 참고:
- 이전 수집은 실행마다 2,374~4,218건(종목당 10건 상한, 74%가 상한)이었다. 12일 공백이면 그보다 훨씬 많을 것으로 보인다.
- 정확한 수는 승인 뒤 `dry-run`(외부 요청 있음, DB 쓰기 없음)으로 센다. dry-run 결과를 보고 apply를 정하는 2단계를 권장한다.

## 9. 실행 이력 개선안

스키마를 바꾸지 않고 기존 표를 쓴다(이번 구현):

| 기록 | 위치 |
| --- | --- |
| 실행 종류·시작·끝·상태 | `data_refresh_run`: `trigger='recovery'`, `steps='news_recovery,disclosure_recovery'`, `status` success/partial/failed, `exit_code` |
| 단계별 성공·실패·신규 건수 | `data_refresh_log` 단계 `news_recovery`·`disclosure_recovery`: `status`, `rows_added`(신규), `latest_date`(복구 끝 날짜), `error_code`(`naver_blocked`·`naver_budget`·`dart_limit`·`partial_failures`), `warning_count`(격리 수), `duration_ms` |
| 중복 건너뜀·요청 수·실패 종목 수·중단 사유 | `data_refresh_run.note`(200자 요약)와 `--report` JSON(종목별 상세) |
| 마지막 처리 종목·재실행 가능 여부 | `--state` 파일(끝난 종목 목록) |

- 단계 이름을 `news`가 아니라 `news_recovery`로 둔다. 그래서 `GET /api/status`의 `last_refresh`(기존 단계만 셈)와 목록의 `last_collection`(`news` 단계) 뜻이 바뀌지 않는다(통합 테스트로 확인).
- 즉 복구 실행이 "마지막 갱신"으로 보이지 않는다. 실제 데이터 최신성은 `latest_published_date`·`last_collected_at`에 그대로 반영된다.

스키마 변경이 필요한 추가안(이번에는 하지 않음, 승인 필요):

| 안 | 내용 | 영향 |
| --- | --- | --- |
| A | `data_refresh_log`에 `detail jsonb`(중복·요청 수·실패 종목·잘린 종목) | 마이그레이션 1개. 기존 행은 null. `--status` 보고서가 읽을 수 있음 |
| B | 개별 수집 스크립트(`collect_universe` 등)도 단계 행을 남기기(`trigger='manual_script'`) | 코드 변경만. 단 `news` 단계 이름으로 남기면 `last_collection` 뜻이 "마지막 뉴스 수집"으로 넓어진다(프런트 합의) |
| C | 뉴스 `(company_id, url)`, 공시 `source_url` 유일 인덱스 | 마이그레이션. 지금 중복 0이라 바로 걸 수 있음(61차) |

## 10. 정기 수집 자동화 설계(등록하지 않음)

기존 설계(`docs/DATA_REFRESH_MONITORING.md` 1·4·11절)를 그대로 쓴다. `refresh_data`에는 이미 다음이 있다.
- 실행 기록·잠금(advisory lock, 겹치면 종료 코드 4)
- `--trigger scheduler`, 종료 코드 0~5, `--status` 보고서, `--retry-failed`

빠진 것은 **등록**뿐이다(61차 원인).

Windows 작업 스케줄러 권장안(사용자 계정, "로그온 여부와 관계없이 실행", "이미 실행 중이면 새 인스턴스 시작 안 함"):

| 작업 | 시각(KST) | 명령(`backend` 폴더, `.venv\Scripts\python.exe -m …`) |
| --- | --- | --- |
| 평일 전체 갱신 | 월~금 17:00 | `scripts.refresh_data --trigger scheduler` |
| 공시 보강 | 월~금 19:00 | `scripts.refresh_data --trigger scheduler --source dart,embeddings,disclosure_content` |
| 주말 뉴스 | 토·일 12:00 | `scripts.refresh_data --trigger scheduler --source news,embeddings --news-only` |
| 실패 재시도 | 월~금 21:00 | `scripts.refresh_data --trigger scheduler --retry-failed` |
| 상태 보고서 | 월~금 08:30 | `scripts.refresh_data --status`(종료 코드 3이면 확인) |
| 기록 정리 | 일 03:00 | `scripts.refresh_data --prune-logs` |

고려 사항:
- **시장 휴장일**:
  - `refresh_data`의 시세 단계는 KRX 달력으로 기대 기준일을 정한다(휴장일 실행은 새 행 없음, 실패 아님).
  - 뉴스·공시는 휴장일에도 나오므로 매일 돌려도 된다.
- **장 종료 뒤**:
  - 평일 17:00. 정규장 15:30 + 지수 확정 대기 10분 + 원천 갱신 여유. 수능일 2026-11-19는 마감 16:30이라 17:00이면 충분하다.
  - 34차 권장 16:40보다 20분 늦게 잡아 특수 마감일도 덮는다.
- **API 제한**: 기존 수집(`refresh_data`)을 매일 돌리면 뉴스는 하루 약 400회다(주식 300 x 1 + ETF 50 x 최신순·관련도순 2). 한도 25,000보다 훨씬 적다. DART는 약 310회다(corpCode 1 + 90일 창 종목당 1~5쪽).
- **중복 실행**: 잠금 + 스케줄러 "새 인스턴스 시작 안 함" 이중 장치.
- **서버·Ollama 자원**:
  - `embed` 단계가 Ollama(`bge-m3`, 13434)를 쓴다. 채팅 모델과 같은 Ollama라 실행 중에는 답변이 느려질 수 있다.
  - 17:00(장 마감 뒤)에 두고, 길게 걸리는 복구는 사용자가 쓰지 않는 시간에 돌린다.
  - API 서버는 행 잠금을 받지 않는다(34차 11절).
- **네트워크 오류**: 단계 안에서 `http_retry`가 재시도한다. 단계가 실패하면 21:00 `--retry-failed`가 다시 돌린다.
  - 다음 날 실행에서 공시는 90일 범위라 저절로 채워진다.
  - 뉴스는 종목당 10건이라 하루 넘게 실패하면 이 복구 도구로 채운다.
- **실패 알림**:
  - 종료 코드를 작업 기록에 남긴다(작업 스케줄러 "마지막 실행 결과").
  - 08:30 `--status`가 `logs\refresh\refresh_status.{json,md}`를 만든다.
  - 메일·메신저 알림은 없다(추가하려면 별도 작업).
- **비밀값**:
  - 작업 명령에 키를 넣지 않는다. 키는 `backend\.env`에만 둔다(앱이 읽음).
  - 작업은 그 파일을 읽을 수 있는 사용자 계정으로 실행한다. `.env`는 Git에 올리지 않는다(기존).

등록 예(실행하지 않음, 참고용):
```powershell
$py = "C:\project\lab-scale\backend\.venv\Scripts\python.exe"
$act = New-ScheduledTaskAction -Execute $py -Argument "-m scripts.refresh_data --trigger scheduler" -WorkingDirectory "C:\project\lab-scale\backend"
$trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 17:00
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "lab-scale refresh_data weekday" -Action $act -Trigger $trg -Settings $set
```

## 11. 외부 요청과 DB 쓰기 승인 대기 사항

| # | 항목 | 내용 |
| --- | --- | --- |
| 1 | 호출할 외부 API | 네이버 뉴스 검색 `openapi.naver.com/v1/search/news.json`(키 `NAVER_CLIENT_*`). DART `opendart.fss.or.kr/api/corpCode.xml` 1회 + `api/list.json`(키 `DART_API_KEY`) |
| 2 | 요청 수와 한도 | 네이버 350~3,500회(한도 25,000/일), DART 약 301회(11-16이면 같은 수준, 020 기준 20,000) |
| 3 | 복구 기간 | 2026-11-04 00:00 KST ~ 실행일 끝 |
| 4 | 대상 종목 | 350(주식 300, ETF 50). 공시는 주식 300 |
| 5 | 예상 신규 | 공시 약 806건(8거래일, 범위 400~1,576) / 11-16 실행이면 약 906건(9거래일, 범위 450~1,773). 뉴스는 dry-run으로 셈 |
| 6 | 쓰는 표 | `news`, `disclosure`, `data_ingestion_quarantine`, `data_refresh_run`, `data_refresh_log` |
| 7 | 기존 데이터 영향 | 삭제·수정 없음. 새 행 INSERT만. 이미 있는 (종목, URL)·접수번호는 건너뜀. 기존 단계(`news` 등)의 실행 기록 뜻은 그대로 |
| 8 | 재실행·실패 복구 | 같은 `--state` 파일로 다시 실행하면 끝난 종목을 건너뛴다. 상태 파일 없이 다시 실행해도 중복 저장은 없다(이미 있는 행 건너뜀, 요청만 다시 함). 막힘(429 등)이면 멈추고, 다음 날 같은 명령으로 이어간다 |
| 9 | 복구 뒤 | 새 뉴스·공시의 임베딩과 공시 본문은 이 도구가 만들지 않는다. `refresh_data --only embed,content`(Ollama 13434 + DART 원문 요청)를 별도 승인으로 돌린다 |

## 12. 테스트 결과

`tests/test_news_recovery_62.py`(신규 22): **22 통과**(순수 17, 통합 5).
- 첫 push의 CI는 유니버스 파일을 읽는 테스트 2개 때문에 실패했다(12.1절).
- 그 테스트를 임시 유니버스 파일로 바꿨고, 실제 파일 확인은 파일이 있을 때만 한다

| 요청 항목 | 테스트 |
| --- | --- |
| 뉴스 10건 초과 페이지 넘김·11-04 이후 복구 | `test_more_than_ten_articles_are_paged_until_the_start_date`(300건 → 3쪽, 기간 안 261건), 통합 `test_dry_run_counts_…`(131건) |
| 날짜 범위 | `test_window_end_filters_newer_articles`, `test_kst_window_and_disclosure_days` |
| 중복 URL 제거 | `test_duplicate_urls_across_pages_are_dropped`, 통합 재실행 0건 |
| 같은 뉴스 재실행 신규 0 | 통합 `test_dry_run_counts_without_writing_then_apply_then_rerun_is_zero` |
| 페이지 종료 조건 | `test_end_of_results_and_empty_page` |
| 최대 페이지 | `test_search_window_limit_marks_truncated`(start ≤ 901, 10쪽, 잘림 표시), `test_max_pages_cap_marks_truncated` |
| API 오류·재시도 | `test_api_error_keeps_partial_result_and_reports_error`(재시도 자체는 기존 `http_retry` 테스트) |
| 일부 종목 실패 뒤 재실행 | 통합 `test_partial_failure_then_rerun_with_state_finishes_only_the_rest` |
| 요청량 제한 | 통합 `test_blocked_and_budget_stop_before_other_companies`(429 → 멈춤, 예산 → 멈춤) |
| 공시 90일·페이지 넘김 | `test_dart_pagination_until_total_page`(3쪽), `disclosure_days`(최대 90) |
| 접수번호 중복 방지·재실행 0 | 통합 `test_disclosure_recovery_dedups_by_receipt_and_records_step` |
| 공시 오류(013·020) | `test_dart_no_data_and_limit_error` |
| 실행 기록: 복구 실행·부분 성공·재실행·신규 수 일치 | 통합: `data_refresh_run.trigger='recovery'`·`status` success/partial, 단계 행 `rows_added` = 실제 신규 행 수. `/api/status` `last_refresh`와 `last_attempt('news')`가 그대로 |
| 안전 장치 | `test_guard`, `test_dry_run_and_apply_refuse_without_flags_…`, 통합 `test_plan_mode_reads_only_and_makes_no_requests`(외부 요청 시 실패 + 행 수 그대로) |
| 기존 수집 호환 | `test_naver_fetch_passes_start`(기본 `start=1` 그대로) |

| 전체 | 결과 |
| --- | --- |
| 단위 전체(`-m "not slow and not integration"`, `DATABASE_URL` 닫힌 포트) | **6,474 통과**(61차 6,458 + 신규 순수 16, 테스트 수정 전 실행). 수정 뒤 이 파일만 다시 돌려 순수 17 통과(실제 유니버스 파일 확인 1개 추가) |
| 통합 전체(격리 DB `ai_investment_recovery62_it`) | **705 통과, 10 건너뜀**, 실패 0(61차 700 + 신규 5, 건너뜀은 이전과 같다) |
| 품질 평가(결정적) | **244/244**(이전과 같다) |
| offline 평가(개발 DB 읽기 전용) | **실패 11건**(알려진 `ent_*`, 61차와 문항·내용 같음) |

### 12.1 첫 CI 실패와 수정

- 첫 push(`5611a83`)의 CI run 43713438978에서 `backend-unit`·`backend-integration`이 실패했다.
- 원인: `FileNotFoundError: backend/scripts/universe_top300.json`. 유니버스 파일은 Git 밖이라 CI에 없다.
  - 실패한 테스트는 `test_targets_are_the_refresh_universes`, `test_plan_mode_reads_only_and_makes_no_requests`다.
- 수정: 두 테스트가 임시 유니버스 파일을 쓰게 했다(`_universe_dir`). 실제 파일 350종목 확인은 파일이 있을 때만 한다(`test_real_universe_files_when_present`, CI에서는 건너뜀).
- 도구 코드는 바꾸지 않았다. 운영 환경에는 유니버스 파일이 있어야 한다(`refresh_data`와 같은 조건).

## 13. 개발 DB 불변 확인

| 항목 | 작업 전 | 작업 후 |
| --- | --- | --- |
| Alembic | `c7e2a9f4d158` | `c7e2a9f4d158` |
| `stock_price` 행 수 / md5 | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` |
| `market_index` 행 수 / md5 | 1,480 / `3ba6fedca411ebff119bef854f227f43` | 1,480 / `3ba6fedca411ebff119bef854f227f43` |
| `news` / `disclosure` / `data_refresh_run` / `data_refresh_log` | 17,853 / 8,722 / 0 / 37 | 17,853 / 8,722 / 0 / 37 |

- 개발 DB에 한 일: READ ONLY 조회, `plan` 실행(읽기 전용 트랜잭션, 외부 연결 차단), 격리 DB용 `pg_dump` 1번, offline 평가(읽기 전용 세션).
- 통합 테스트는 새 격리 DB `ai_investment_recovery62_it`에서 돌렸다. 테스트 종목·복구 기록은 끝나고 지웠다(0행 확인).
- 기존 시험 DB는 지우지 않았다.
- 네이버·DART 요청은 0회다:
  - 테스트는 가짜 함수와 가짜 HTTP를 썼다.
  - `plan`은 `requests`·`http_retry.get`·외부 소켓 연결을 막은 채 정상 종료했다.
- 작업 스케줄러 등록·변경은 하지 않았다.

## 14. 실제 수집을 진행하기 위한 명령 (승인 뒤)

`backend` 폴더, Git Bash 또는 PowerShell. 각 단계 뒤 결과를 보고 다음 단계로 간다.

```bash
cd /c/project/lab-scale/backend
PY=.venv/Scripts/python.exe
# 0) 계획(외부 요청 없음, DB 읽기만)
$PY -m scripts.recover_news_disclosures --report backups/recovery62_plan.json
# 1) dry-run: 네이버·DART 실제 요청, DB 쓰기 없음. 뉴스 신규 예상 건수·잘린 종목을 센다
$PY -m scripts.recover_news_disclosures --mode dry-run --allow-network --report backups/recovery62_dryrun.json
# 2) apply: 쓰기(뉴스·공시·격리 기록·실행 기록). 상태 파일로 이어하기
$PY -m scripts.recover_news_disclosures --mode apply --allow-network --confirm-database ai_investment \
    --state backups/recovery62_state.json --report backups/recovery62_apply.json
# 3) 같은 명령을 다시 실행하면 끝난 종목은 건너뛴다(실패·미처리 종목만). 신규 0건이면 완료
# 4) 임베딩·공시 본문(별도 승인: Ollama 13434, DART 원문 요청)
$PY -m scripts.refresh_data --only embed,content
# 5) 확인
$PY -m scripts.refresh_data --status
```

- 보고서·상태 파일은 `backend/backups/`에 둔다(Git에 올리지 않음, 기존 백업 폴더).
- 승인 범위를 줄이려면 다음 인자를 쓴다:
  - `--tickers`로 일부 종목만
  - `--no-news` / `--no-disclosures`로 한쪽만
  - `--max-pages`로 종목당 요청 상한
- 확인할 것:
  - dry-run의 `news.new`·`truncated_companies`·`stopped`
  - apply 뒤 DB의 `news`·`disclosure` 증가 수 = 보고서 `new`
  - `data_refresh_run` 최신 행의 `trigger=recovery`·`status`

## 15. 다음 작업 우선순위

1. **승인 → dry-run → apply**(14절). 뉴스는 늦을수록 1,000건 범위 밖으로 밀려 회복할 수 없는 기사가 는다.
2. **정기 실행 등록**(10절, 승인 필요). 등록하지 않으면 같은 공백이 다시 생긴다.
3. 복구 뒤 `embed`·`content` 단계(승인 필요).
4. 기존 수집의 뉴스 10건 상한 검토. 매일 돌아도 기사가 많은 종목은 빠진다. `--display` 상향 또는 이 도구의 페이지 넘김을 정기 수집에 쓰는 안.
5. 9절 A·C(마이그레이션 승인 필요).
