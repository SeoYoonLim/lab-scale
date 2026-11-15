# 61차 뉴스·공시 데이터 최신성과 수집 이력 정합성 감사 (2026-11-15)

**결론: 뉴스·공시는 2026-11-03 19:54 KST 이후 실제로 수집되지 않았다.**
- 기록 오류나 조회 로직 결함이 아니다. 수집기(`refresh_data`의 `news` 단계 = `collect_universe`)가 그 뒤 한 번도 실행되지 않았다.
- 같은 기간 시세·지수는 2026-11-13 10시에 개별 스크립트로 들어왔다. 그래서 "마지막 갱신 기록 11-03"과 "시세 11-12"가 어긋나 보였다.
- 뉴스·공시에는 그런 개별 수집도 없었다.

개발 DB는 READ ONLY로만 읽었다. 네이버·DART 요청, 수집 스크립트·`refresh_data` 실행은 하지 않았다. API 응답·DB 스키마는 바꾸지 않았다.

## 1. 시작 브랜치와 커밋

| 항목 | 값 |
| --- | --- |
| 브랜치·커밋 | `feature/backend`, `dfdcb4b`(60차), 원격과 같음, 작업 트리 깨끗함 |
| 서버 | PID 40824(127.0.0.1:8000), `/api/health` 200. 건드리지 않았다 |
| 확인한 문서 | `docs/BACKEND_API_RUNTIME_AUDIT_60.md`, `docs/FRONTEND_API_HANDOFF_60.md`, `backend/API.md` 뉴스·공시·상태 절 |
| 확인한 코드 | `app/collectors.py`(`fetch_naver_news`·`fetch_and_save_news`·`fetch_dart_disclosures`·`fetch_and_save_disclosures`), `scripts/collect_universe.py`, `scripts/refresh_data.py`(`news` 단계·`write_log`), `app/data_status.py`(`build_status`·`summarize_last_run`·`last_attempt`), `app/stock_feed.py`(`freshness`·`news_page`·`disclosure_page`), `app/http_retry.py`, 모델 `News`·`Disclosure`·`DataRefreshLog`·`DataRefreshRun` |

## 2. 뉴스 데이터 실제 최신일

개발 DB `ai_investment`, READ ONLY 연결(`transaction_read_only=on`), 세션 표시 시간대만 KST로 바꿔 조회했다.
저장 열은 모두 `timestamptz`라 같은 순간을 KST로 읽은 값이다.

| 항목 | 값 |
| --- | --- |
| 전체 행 | 17,853(모두 종목 연결, 종목 350개 전부) |
| 종목당 행 | 최소 8, 중앙 58, 최대 63 |
| 게시 시각(`published_at`) 범위 | 2020-04-13 ~ **2026-11-03 19:51 KST** |
| 수집 시각(`created_at`) 범위 | 2026-09-24 18:10 ~ **2026-11-03 19:54:42 KST** |
| 11-04 이후 생성된 행 | **0** |
| 관련 없음(`is_relevant=false`) | 1,558(목록에서 뺀다) |
| 출처·URL·게시 시각 없음 | 0·0·0 |
| 같은 종목·같은 URL 중복 | 0 |
| 같은 URL이 여러 종목에 | 2,518개 URL(한 기사가 여러 종목에 저장되는 설계) |
| 같은 종목·제목·게시 시각 | 3쌍(URL이 서로 다른 별개 기사: 외신 사진 기사 2, 다른 매체 같은 제목 1) |

수집일별 생성 행(KST): 10-28 3,327, 10-31 3,000, 11-02 2,620, 11-03 2,374. 11-04 이후는 없다.

## 3. 공시 데이터 실제 최신일

| 항목 | 값 |
| --- | --- |
| 전체 행 | 8,722(주식 299종목. 주식 300개 중 1개는 기간 안 공시 없음, ETF는 수집 대상 아님) |
| 접수일(`disclosed_at`) 범위 | 2026-06-24 ~ **2026-11-03**. 모두 KST 자정 00:00:00 저장(DART는 접수일만 준다) |
| 수집 시각(`created_at`) 범위 | 2026-09-21 20:22 ~ **2026-11-03 19:53:51 KST** |
| 11-04 이후 생성된 행 | **0** |
| DART 접수번호(`rcptNo=` 14자리) 없음 | 0 |
| 접수번호 중복 | 0(같은 종목·다른 종목 모두) |
| 본문 미수집(`content` null) | 49 |
| 유형 상위 | 증권발행실적보고서 967, 투자설명서(일괄신고) 899, 기업설명회(IR) 680, 일괄신고추가서류(파생결합사채) 508, 잠정실적(공정공시) 305, 반기보고서 291 |

수집일별 생성 행(KST): 10-28 891, 10-31 495, 11-03 168.

## 4. 게시일·접수일·수집일 비교

| 비교 | 결과 |
| --- | --- |
| 뉴스 게시 → 수집 | 중앙 5.3시간, 90% 50.5시간, 99% 506시간. 수집이 게시보다 이른 행 0 |
| 공시 접수 → 수집 | 중앙 34.8일, 90% 78.8일, 99% 90.8일. 수집이 접수보다 이른 행 0 |
| 해석 | 뉴스는 실행 때마다 최신 기사를 받아 지연이 짧다. 공시는 실행마다 최근 90일을 다시 조회하고, 첫 적재(9-21·9-24)가 90일치를 한꺼번에 넣어서 중앙값이 길다. 지연이 아니다 |

2026-11-15 기준 경과:

| 기준 | 11-03 이후 경과 |
| --- | --- |
| 달력 일 | 12일 |
| 평일(뉴스·공시 최신성 계산 기준) | 8일(11-04~11-13) |
| KRX 거래일 | 8일(그 사이 휴장일 없음) |
| 시세 최신일(11-12) 대비 | 뉴스·공시 수집이 시세보다 7거래일 뒤처짐 |

## 5. 종목별 최신성 분포

| 뉴스: 종목별 마지막 수집일 | 종목 수 |
| --- | --- |
| 11-03 | 320 |
| 11-02 | 15 |
| 10-31 | 6 |
| 10-28 | 5 |
| 10-14 | 4 |

- 종목별 최신 게시일은 11-03이 320종목이고, 나머지는 10-03~11-02에 흩어져 있다.
- 마지막 수집에서 새 기사가 없던 종목은 예전 수집일이 그대로 남는다(지연이 아니라 새 기사 없음).
- 표본: 삼성전자·SK하이닉스·현대차는 60건, 최신 게시 11-03 19:4x. 카카오 63건. KODEX 200 48건, 최신 게시 11-03 18:38.

| 공시: 종목별 최신 접수일 | 종목 수 |
| --- | --- |
| 11-03 | 38 |
| 11-02 | 49 |
| 10-30 | 45 |
| 10-29 | 32 |
| 10-28 이전 | 135 |

공시는 종목마다 제출 빈도가 달라 최신 접수일이 퍼진다. 수집 시각은 11-03이 87종목, 10-31이 102종목, 10-28이 60종목이다. 수집 시각이 새 공시가 있을 때만 생기기 때문이다.

## 6. 데이터 지연 원인

요청서의 질문 6개에 근거를 붙여 답한다.

| # | 질문 | 답 | 근거 |
| --- | --- | --- | --- |
| 1 | 11-03 이후 실제로 수집되지 않았나 | **예** | 뉴스·공시 모두 11-04 이후 생성 행 0. 뉴스는 평소 실행마다 2,300~4,200건이 새로 들어왔다 |
| 2 | 데이터가 있는데 최신일 로직이 놓치나 | **아니오** | `GET /api/status`의 `news.latest_published_date`·`disclosure.latest_date`(2026-11-03), 목록 `freshness.last_collected_at`(11-03 19:54:42.431681)이 DB 최댓값과 같다 |
| 3 | 개별 수집은 됐는데 기록만 안 바뀌었나 | 뉴스·공시: **아니오**(개별 수집도 없음). 시세·지수: **예** | 시세 2,450행(11-04~11-12, 350종목 x 7거래일), 지수 14행이 2026-11-13 10시에 생성됐다. 그날 `data_refresh_log`는 없다(47·50차 작업의 개별 스크립트) |
| 4 | 수집은 성공했는데 DB 반영에서 빠졌나 | **아니오** | 11-03 `news` 단계 `rows_added` 2,542 = 그날 생성 뉴스 2,374 + 공시 168. 뉴스·공시 격리 기록 0건, 11-04 이후 격리는 시세 1건뿐 |
| 5 | 공급자 제공 범위·호출 제한 때문인가 | **아니오**(11-03 뒤로는 요청이 없었으므로 원인이 될 수 없다) | 11-03 이후 실행 기록 없음. 마지막 실행 4번(10-14~11-03)은 모두 `success`이고 `error_code`가 없다 |
| 6 | 잘못된 필드로 최신성을 판단하나 | **아니오** | 상태 API는 게시일·접수일을, 목록의 수집 지연은 `created_at`을, 종목 최신은 게시·접수 시각을 쓴다. 모두 KST 날짜로 바꾼 뒤 계산한다(테스트 3·4) |

수집이 멈춘 이유:
- `refresh_data`는 **자동 실행되지 않는다.**
  - 작업 스케줄러에 프로젝트 작업이 없다. 이름에 Refresh가 들어간 것은 Windows 시스템 작업뿐이다(34차 "스케줄러 미등록"과 같음).
  - 실행 중인 수집 프로세스도 없다.
- `data_refresh_run`(34차, 11-07에 생긴 실행 기록 표)이 0행이다. 그러니 34차 뒤로 `refresh_data`는 한 번도 실행되지 않았다.
- 마지막 수동 실행은 11-03 19:45(전체)와 20:03(환율만)이다.

**[미확인]** 11-03 이후 사람이 `collect_universe`를 직접 실행했는데 아무것도 저장하지 못했을 가능성. 개별 스크립트는 실행 기록을 남기지 않아 직접 확인할 수 없다. 하지만 실행했다면 종목당 최신 기사가 들어왔을 것이다(평소 실행마다 2천 건 이상). 그래서 실행하지 않은 것과 맞다.

## 7. 수집 파이프라인 조사 결과

| 항목 | 뉴스(`fetch_and_save_news`) | 공시(`fetch_and_save_disclosures`) |
| --- | --- | --- |
| 실행 경로 | `refresh_data`의 `news` 단계 → `collect_universe` x2(`universe_top300.json` 뉴스+공시, `universe_etf50.json` 뉴스만). 직접 `python -m scripts.collect_universe`도 가능 | 같은 단계, top300만 |
| 실행 조건 | 수동 실행만(스케줄러 없음). 같은 단계 중복 실행은 advisory lock으로 막는다(34차) | 같음 |
| 수집 대상 | 유니버스 파일의 종목(주식 300 + ETF 50) | 주식 300(ETF 없음) |
| 원천 요청 | 네이버 뉴스 검색 `sort=date`, **`start=1` 한 번**. 주식은 `display=30`(10 x 과다 요청 3), ETF는 최신순·관련도순 각 100건 | DART `list.json` 최근 **90일**(`--days 90`), `page_count=100`, **`total_page`까지 페이지 넘김** |
| 저장 건수 | 종목당 **최대 10건**(`--display 10`). 관련성 높은 기사를 먼저 고른다 | 기간 안의 새 접수번호 전부 |
| 호출 제한·재시도 | 요청 간 0.2초(`--delay`). `http_retry`: 연결 오류·timeout·5xx·429만 지수 백오프로 다시 시도, 그 밖의 4xx는 바로 실패 | 같음 |
| 중복 제거 | 같은 종목·같은 URL이면 저장하지 않음(앱 코드, DB 유일 제약 없음). 목록 API도 같은 종목·같은 URL은 id가 큰 행 하나만 보인다 | 같은 원문 URL(= 접수번호)이면 저장하지 않음. 정정 공시는 접수번호가 달라 별개 행 |
| 저장 전 검증 | 제목·URL·게시 시각(미래 금지)·종목 연결. 실패 행만 격리(36차) | 접수번호 형식·접수일 등. 실패 행만 격리 |
| DB 반영 | 종목마다 한 번에 INSERT 후 commit | 같음 |
| 성공 기록 | `refresh_data`가 단계마다 `data_refresh_log`에 한 행(`rows_added` = 뉴스+공시 신규 합). **개별 스크립트는 기록하지 않는다**(테스트 7) | 같음 |
| 실패 비율 | 대상의 절반 이상이 실패하면 단계 실패(`check_failure_ratio`), 일부 실패면 `partial` | 같음 |

공백 회복성:
- **공시**: 다음 실행이 최근 90일을 끝까지 다시 받는다. 그래서 11-04 이후 공시는 지금 수집해도 모두 들어온다(90일 안이면).
- **뉴스**: 다음 실행에서 종목마다 최신 10건만 들어온다. 11-04~11-14 기사 중 그보다 오래된 것은 **영구히 빠진다**(페이지 넘김 없음). 기사가 많은 종목일수록 공백이 크다.
- 그러니 뉴스 데이터는 "종목별 최신 표본"이지 전체 기사가 아니다. 매일 수집하더라도 하루 10건이 넘는 종목은 빠지는 기사가 있다.

## 8. 마지막 갱신 기록의 실제 의미

| 개념 | 지금 어디에 있나 | 2026-11-15 값 |
| --- | --- | --- |
| 마지막 `refresh_data` 실행 완료 | `GET /api/status`의 `last_refresh.finished_at`(마지막 실행 한 번의 가장 늦은 단계 종료) | 2026-11-03 20:03:27(**환율만 돈 실행**, 나머지 단계 `skipped`) |
| 마지막 뉴스·공시 수집 시도 | 시장 요약 `last_collection`은 지수·시세만. 뉴스·공시 목록 `freshness.last_collection`이 `news` 단계 마지막 시도(skipped 제외) | 2026-11-03 19:54:42, success |
| 마지막 뉴스 DB 반영 | 목록 `freshness.last_collected_at`(뉴스 표 전체 `created_at` 최대) | 2026-11-03 19:54:42 |
| 마지막 공시 DB 반영 | 공시 목록 `freshness.last_collected_at`(공시 표 `created_at` 최대) | 2026-11-03 19:53:51 |
| 실제 뉴스 최신 게시일 | `status.sources.news.latest_published_date` | 2026-11-03 |
| 실제 공시 최신 접수일 | `status.sources.disclosure.latest_date` | 2026-11-03 |
| 종목별 최신 | 목록 `freshness.company_latest_at` | 종목마다 다름 |
| 개별 스크립트 실행 | **기록 없음** | 시세·지수 11-13 10시 반영이 어디에도 기록되지 않음 |

문제점:
1. `last_refresh`는 "마지막 `refresh_data` 실행"이다. 일부 단계만 돈 실행(11-03 20:03 환율만)도 같은 이름으로 보인다.
   - 화면에 "마지막 갱신 11-03 20:03 성공"으로 보이면 뉴스도 그때 갱신된 것처럼 읽힌다. 실제 뉴스는 19:54다.
2. 개별 스크립트 수집은 어디에도 기록되지 않는다. 그래서 갱신 기록(11-03)이 실제 데이터(시세 11-12)보다 오래된 것처럼 보인다.
3. 하나의 `last_updated_at` 필드는 없다. 다만 프런트가 `last_refresh.finished_at`을 "마지막 갱신"으로 쓰면 1·2번 오해가 생긴다.
   - 지금 프런트 푸터는 `sources.stock_price.latest_date`와 `last_refresh.ok`만 쓴다. `finished_at`은 보이지 않는다(60차 조사).

## 9. API 최신성 표시의 정확성

| 응답 | 표시 | 정확한가 |
| --- | --- | --- |
| `status.sources.news.latest_published_date` | 2026-11-03 | 정확(DB 최대 게시일의 KST 날짜) |
| `status.sources.disclosure.latest_date` | 2026-11-03 | 정확 |
| 뉴스 목록 `freshness.collection_lag_weekdays` / `stale` | 8 / true | 정확(11-03 → 11-13까지 평일 8) |
| `freshness.last_collection` | success, 11-03 19:54 | 정확. 다만 "그 뒤 실행 없음"과 "성공"이 함께 보이므로, `stale`과 같이 읽어야 지연이라고 알 수 있다 |
| `status.last_refresh` | 11-03 20:03 ok | 기록으로는 정확하나 의미가 좁다(8절 1) |
| 시장 요약 `last_collection.index/prices` | 11-03 | 기록으로는 정확하나 실제 데이터(11-12)보다 이르다(8절 2, 60차에 문서화) |

## 10. 안전하게 수정한 결함

**코드 결함: 없음.** 최신성 계산이 쓰는 필드·시간대 변환, 중복 방지, 단계 기록 모두 의도대로 동작했다.
11-03 뒤로 데이터가 없는 것은 수집을 실행하지 않았기 때문이다. 갱신 시각을 임의로 바꾸지 않았다.

문서 정정(응답 변경 없음, `backend/API.md`):
- 뉴스 목록 절에 **수집 범위** 추가: 실행마다 종목당 최신 10건, 페이지 넘김 없음, 공백 기간이 채워지지 않음, 공시는 90일 재조회로 채워짐, 11-03 19:54가 마지막 수집.
- `last_refresh.finished_at`에 "일부 단계만 돈 실행도 한 실행이라 뉴스 갱신 시각이 아닐 수 있다"(11-03 20:03 사례)를 추가.

## 11. 추가 개선이 필요한 항목 (승인 필요)

| 우선 | 항목 | 내용 | 필요한 것 |
| --- | --- | --- | --- |
| 1 | **뉴스·공시 수집 재개** | `python -m scripts.refresh_data --only news`(또는 전체). 공시는 90일 범위라 공백이 채워지고, 뉴스는 최신 10건만 들어온다 | 외부 요청 승인. 개발 DB 쓰기 |
| 2 | 정기 실행 | 스케줄러 등록(34차 문서 `docs/DATA_REFRESH_MONITORING.md`의 권장 시각) | 사용자 승인(자동 작업 등록) |
| 3 | 뉴스 공백 회복 | 공백이 있을 때 `start` 페이지 넘김(네이버 최대 1,000건)으로 마지막 수집 시각까지 거슬러 받기, 또는 `--display`를 늘림 | 코드 변경 + 요청 수 증가(네이버 일 25,000회 한도 안인지 확인) |
| 4 | 개별 스크립트 실행 기록 | `collect_universe`·`collect_prices`·`collect_market_index`가 실행하면 `data_refresh_log`에 단계 행을 남기게(예: `trigger` 구분) | 코드 변경. 스키마는 그대로 가능하다(`data_refresh_run.trigger`가 이미 있음) |
| 5 | 상태 API 의미 분리 | `status`에 선택 필드 추가: `last_refresh.steps_run`(그 실행에서 실제로 돈 단계 목록, string[]), `sources.news.last_collected_at`·`sources.disclosure.last_collected_at`(표의 마지막 저장 시각, string\|null) | API 계약 추가(프런트 합의). 프런트 영향: 선택 필드라 기존 파싱은 그대로다 |
| 6 | DB 유일 제약 | 뉴스 `(company_id, url)`, 공시 `source_url`에 유일 인덱스(지금 중복 0이라 바로 걸 수 있음) | 마이그레이션 승인 |

## 12. 개발 DB 불변 확인

READ ONLY 연결로 작업 전후 같은 값이다:

| 항목 | 작업 전 | 작업 후 |
| --- | --- | --- |
| Alembic | `c7e2a9f4d158` | `c7e2a9f4d158` |
| `stock_price` 행 수 / md5 | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` | 251,455 / `ca9ddfa3ef424f90b7cb612a03a72c62` |
| `market_index` 행 수 / md5 | 1,480 / `3ba6fedca411ebff119bef854f227f43` | 1,480 / `3ba6fedca411ebff119bef854f227f43` |
| `news` / `disclosure` / `data_refresh_log` 행 수 | 17,853 / 8,722 / 37 | 17,853 / 8,722 / 37 |

- 개발 DB에 한 일: READ ONLY 조회, 격리 DB용 `pg_dump` 1번, offline 평가(읽기 전용 세션).
- 통합 테스트는 새 격리 DB `ai_investment_news61_it`에서 돌렸다. 테스트용 가짜 종목은 끝나고 지웠다(0행 확인).
- 기존 시험 DB는 지우지 않았다.

## 13. 외부 원천 요청이 없었다는 확인

- 네이버·DART 요청은 하지 않았다. 수집 함수는 코드로만 읽었다.
- 테스트는 `fetch_naver_news`·`fetch_dart_disclosures`·`get_dart_corp_code`를 가짜 함수로 바꿨다.
- 테스트 환경은 `requests` HTTP를 막는다(37차 `test_requests_http_is_blocked_in_tests`).
- `refresh_data`·`collect_universe`·`collect_disclosure_content`·52차 분봉 스크립트를 실행하지 않았다.
- 작업 스케줄러는 조회만 했고 등록·변경하지 않았다.

## 14. 테스트 결과

`tests/test_news_disclosure_freshness_61.py`(신규 13): **13 통과**(순수 10, 통합 3)

| 요청 항목 | 테스트 |
| --- | --- |
| 1 뉴스 게시일과 수집일 구분 | `test_news_item_keeps_published_and_collected_apart` |
| 2 공시 접수일과 수집일 구분 | `test_disclosure_item_keeps_receipt_date_and_collected_apart`, 통합 `test_disclosure_collector_dedups_by_receipt_...`(접수일 = KST 자정, 수집 시각 따로) |
| 3 UTC·KST 변환 | `test_status_dates_are_kst_dates`, `test_collection_lag_uses_the_kst_collection_date` |
| 4 종목별 최신 | 통합 `test_company_latest_and_table_last_collected_differ` |
| 5 빈 데이터 | `test_empty_tables_are_stale_and_have_no_dates` |
| 6 중복 처리 | 통합 수집기 2개(같은 원천 응답 두 번 → 두 번째 0건). 목록 단계 중복은 기존 `test_stock_feed_api` |
| 7 개별 수집과 전체 갱신 이력 구분 | 통합 2개(개별 수집 뒤 `data_refresh_log` 행 수 그대로), `test_last_refresh_is_the_last_refresh_data_run_even_if_it_skipped_news`(11-03 실제 기록 재현), `test_last_attempt_skips_skipped_rows_and_reports_failure` |
| 8 수집 실패와 지연 구분 | `test_delay_and_failure_are_told_apart_by_last_collection` |
| 9 게시는 최근, 수집은 오래됨 | `test_recent_publication_but_old_collection_is_stale` |
| 10 수집은 최근, 게시는 오래됨 | `test_recent_collection_but_old_publication_is_not_stale` |

| 전체 | 결과 |
| --- | --- |
| 단위 전체(`-m "not slow and not integration"`, `DATABASE_URL` 닫힌 포트) | **6,458 통과**(60차 6,448 + 신규 순수 10), 실패 0 |
| 통합 전체(격리 DB `ai_investment_news61_it`) | **700 통과, 10 건너뜀**, 실패 0(신규 3 포함, 건너뜀은 이전과 같다) |
| 품질 평가(결정적) | **244/244**(이전과 같다) |
| offline 평가(개발 DB 읽기 전용) | **실패 11건**(알려진 `ent_*`, 60차와 문항·내용 같음) |
| 평가 세트 v1·v2 | 바꾸지 않음(SHA-256 `29a21346…`·`3fe790cd…`) |

기존 테스트는 고치지 않았다.

## 15. CI 결과

push 뒤 GitHub Actions(backend-unit·backend-integration·eval-offline·secret-scan) 결과를 최종 보고에 적는다(이 문서를 담은 커밋이라 여기에 쓸 수 없다).

## 16. 커밋 해시와 push 여부

- 커밋 대상:
  - `backend/API.md`(설명 추가)
  - `backend/tests/test_news_disclosure_freshness_61.py`
  - 이 문서
- 커밋 해시·push 결과는 최종 보고에 적는다. main 병합은 하지 않는다.
