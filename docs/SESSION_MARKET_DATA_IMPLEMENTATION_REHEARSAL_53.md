# 53차 세션별 시장 데이터 구현 리허설 — 분류 규칙 확정과 시험 DB 마이그레이션 (2026-11-14)

**이번 차수는 시험 DB에서만 검증했다. 개발 DB(`ai_investment`)에는 `stock_price_session`을 만들지 않았고, 마이그레이션도 적용하지 않았다.**
개발 DB 적용은 17절 절차와 18절 승인이 남아 있다.

확인 수준 표시: **[원천]** 원천 데이터를 직접 확인, **[표본]** 보존 분봉 전수·표본 대조(공식 정의 문서 없음), **[미확인]** 공식 자료 없음.

## 1. 브랜치와 기준 커밋

| 항목 | 값 |
| --- | --- |
| 브랜치 | `feature/backend`(원격과 동기화, 작업 트리 깨끗한 상태에서 시작) |
| 기준 커밋 | `bd138a6`(52차). 51차 `ff769eb`·`6c4a879` 포함 |
| 읽은 문서 | `docs/SESSION_MARKET_DATA_DESIGN_51.md`, `docs/SESSION_MINUTE_DATA_PRESERVATION_52.md`, `backend/API.md` 50·51차 절 |
| 확인한 보존본 | `backend/backups/session_audit_2026-11-05_2026-11-12/`(353개), `backend/backups/session_audit_2026-11-06_2026-11-13_snapshot_20261114/`(352개). 51차 커밋 체크섬 목록으로 작업 전·후 모두 일치 |
| 52차 스크립트 | `fetch_session_minute.py`(이번에 실행하지 않음), `preserve_session_minute.py`(검증·수집 시각 함수만 재사용) |

## 2. 안 B·C·D 비교와 최종 권고

51차 대안에 이번 요구서의 "분봉 1개 = 1행" 해석(안 D-분)을 더해 실제 데이터로 비교했다.

| 안 | 저장 | 행 수(실측 기반, 1년 245거래일) | 크기 | 가능한 것 | 판단 |
| --- | --- | --- | --- | --- | --- |
| B | 파일만(52차 보존) | — | gzip 약 390MB/년(51차) | 원본 보존. 서비스 조회 불가 | 승인 전 보험으로 계속 유지 |
| C | 정규장 행만 | 350/일 → 약 8.6만 | 약 28MB/년 | 정규장 OHLCV | 종가 단일가·장후 구분과 하루 고가·저가 출처 감사를 못 함 |
| **D(권고)** | **세션 집계 행(4세션)** | **실측 1,207~1,288/일(평균 1,259) → 약 31만** | **실측 327B/행 → 약 100MB/년** | 세션별 OHLCV, 정규장 거래량, 장후 분리, 저장 일봉 감사 | **권고** |
| D-분 | 분봉 1개 = 1행 | 실측 170,031/일 → **약 4,170만** | 실측 156B/행 → **약 6.5GB/년** | 분 단위 차트 | 지금 개발 DB 전체(435MB)의 15배/년. 분 단위 필요는 원본 파일(52차)로 충분 |

- 권고: **안 D**(종목·거래일·거래소·세션 하나당 한 행)다. 분 단위 원본은 52차 파일 보존으로 남긴다.
- 분 단위 시각 정보는 행의 `first_bar_at`·`last_bar_at`·`bar_count`로 남긴다. 예: 15:32 종가 단일가는 `first_bar_at` 15:32로 보인다.
- 51차 추정(908행/일)보다 많은 이유: 종가 단일가를 정규장에서 떼어 별도 행(`closing_auction`)으로 두었기 때문이다(하루 약 350행 증가).

## 3. 실제 분봉 원본 구조

조사 대상: 두 보존본 전체를 겹침 없이 합쳤다. 분봉 1,192,628개, 종목-날짜 2,450개(350종목 × 2026-11-05~11-13 7거래일), 주식 300·ETF 50. 원천 재요청은 하지 않았다.

| 항목 | 관찰 | 수준 |
| --- | --- | --- |
| 형식 | `raw/{ticker}.json` = 응답 목록. 필드 6개: `localDateTime`(14자리), `openPrice`·`highPrice`·`lowPrice`·`currentPrice`, `accumulatedTradingVolume`(= 그 분 거래량) | [원천] |
| **시각 필드 = 봉 시작 시각(KST)** | 첫 봉이 09:00(2,354/2,450, 나머지는 09:01~09:05 첫 체결), 장후 종가매매 첫 봉 15:40, 애프터마켓 첫 봉 16:00, 마지막 봉 19:59(20:00 아님), 초는 항상 00. 끝 시각 표기라면 09:01·15:41·16:01·20:00이 보여야 한다 | [표본] |
| 15:20~15:29 | 주식 0봉. ETF 1봉(122630 11-10 15:20, 20주, 58,525원 — 그 분 종가 단일가 58,435원과 다르고 직전 흐름과 이어짐 → 15:20:00 직후 마지막 연속매매 체결) | [표본] |
| 15:30 봉 | 주식 2,065·ETF 343개 **모두 시가=고가=저가=종가**(단일가 체결의 모양) | [표본] |
| 15:31~15:39 | 15:32 봉 35개뿐(주식, 11-05 1·11-09 3·11-12 31). 모두 단일가 모양. **모두 15:30 봉이 없는 날에만** 있다(둘 다 있는 날 0) | [표본] |
| 15:40~15:59 | 6,943봉 모두 단일가 모양이고 그날 종가 단일가 가격과 같다 | [표본] |
| 16:00~19:59 | 주식만(278,163봉). ETF 0 | [표본] |
| 소수 가격 | 수정주가 ETF 4종목(214980·284430·441640·498400) 최대 **소수 12자리**(예: 17929.949874686718) | [원천] |
| 거래량 | 모두 정수. 단 **음수 2개**: 284430 11-06 12:29 `-1`, 11-10 10:18 `-8`(수정 비율 반영 과정의 부산물로 추정, 원인 [미확인]) | [원천] |
| 중복 시각 | 0 | [표본] |

## 4. 세션 분류 규칙과 근거

구현: `backend/app/session_bars.py`(순수 함수, `RULES_VERSION = 1`). 50차 감사 도구 `app/session_ohlcv.py`는 바꾸지 않았다.

| 세션 | 내부 값 | 규칙(정규 일정, 마감 15:30인 날) | 근거 |
| --- | --- | --- | --- |
| 정규장 | `regular` | 09:00~15:20 봉 | 연속매매. 시가 단일가 체결은 09:00 봉에 섞여 나눌 수 없다(나누지 않음) |
| 종가 단일가 | `closing_auction` | 15:30 봉 하나. 15:30 봉이 없으면 15:31~15:39의 봉 **하나**. 둘 다 단일가 모양이어야 함 | 5절 |
| 장후 시간외 종가매매 | `post_close` | 15:40~15:59 봉. 모두 단일가 모양이고 가격 = 그날 종가 단일가 가격 | 6,943/6,943 관찰 |
| KRX 애프터마켓 | `after_market` | 16:00~19:59 봉. **주식, 2026-09-14 이후만** | 7절 |

- 요구서의 3개 세션에 `post_close`를 더했다. 15:40~16:00 체결은 15:30 단일가와 다른 별도 시장(종가로만 체결)이다. 애프터마켓에 합치면 애프터마켓의 시가·거래량이 바뀐다.
- 지금 나눠 두면 나중에 다시 분류할 필요가 없다(요구 4.2 "재분류 불필요").
- 날짜 단위 거부 조건:
  - KRX 휴장일·달력 범위 밖
  - 특수 일정(달력 마감 시각 ≠ 15:30)
  - 수집 시각 없음·그날 20:10 KST 전 수집
  - 초 ≠ 00
  - 값 오류(가격 ≤ 0, OHLC 모순, 음수·비정수 거래량)
  - 중복 시각
  - 정규장 봉 없음
  - **분류할 수 없는 봉이 하나라도 있음**(8절)
- 공식 종가는 분류에 쓰지 않는다. 분류가 끝난 뒤 대조만 한다(13절).

### 4.1 분류 결과(보존본 전수, 종목-날짜 2,450개)

| 자산 | 통과 | 거부 | 표시 |
| --- | --- | --- | --- |
| 주식 | 2,100/2,100 | 0 | `late_closing_auction` 35(11-05 1, 11-09 3, 11-12 31) |
| ETF | 348/350 | 2(284430 11-06·11-10 음수 거래량) | `no_closing_auction` 7(273130 11-12, 310970 11-12, 385540 11-05·11-09·11-12·11-13, 214980 11-13) |

- 분류되지 않은 봉: 0개(통과한 날 기준)
- 두 보존본의 겹치는 날(11-06~11-12) 세션 집계 6,319키는 값이 모두 같다
- 거래일별 세션 행 수: 13.1절

### 4.2 오분류 가능성

| 위험 | 대응 |
| --- | --- |
| 달력에 없는 특수 일정(수능일은 개장·마감 1시간 늦음. 2026년 날짜는 이번에 공식 확인하지 않음 **[미확인]**) | 15:21~15:29 연속매매 봉, 단일가가 아닌 15:30·15:40대 봉이 생겨 **그날 전체가 거부**된다(시험 `test_session_boundaries` 등). 특수일을 달력(`special_close`)에 넣기 전까지 적재되지 않는다 |
| 15:3x에 단일가가 아닌 봉, 또는 봉 2개 이상 | 거부(`unclassified_bars`) |
| 장후 종가매매 가격 ≠ 종가 단일가 | 거부 |
| 2026-09-14 이전 16:00~18:00(당시 시간외 단일가) | 애프터마켓으로 분류하지 않고 거부 |
| 09:00 봉 안의 시가 단일가 | 정규장에 포함(나눌 정보 없음). 문서화 |

## 5. 15:32 종가 단일가 처리

판정 근거(가격 일치 하나에 기대지 않음):
1. **시각**: 15:31~15:39는 정규 일정에서 어느 시장도 체결하지 않는 구간이다(연속매매는 15:20, 장후 종가매매는 15:40부터). 관찰된 봉은 15:32뿐이다.
2. **모양**: 35봉 모두 시가=고가=저가=종가(단일가)이고 하루에 하나뿐이다.
3. **배타성**: 15:30 봉이 있는 날(주식 2,065 + ETF 343 = 2,408일)에는 15:31~15:39 봉이 한 번도 없다.
4. **거래량**: 35봉 합계 2,121,271주로 그날 종가 단일가 규모다(15:30 봉과 비슷).
5. **사후 대조**(분류 근거 아님): 저장 공식 종가와 35/35 같다(51차).

- 원인: 종가 단일가 중 변동성 완화장치(VI)가 걸려 2분 연장됐다는 시장 규칙으로 설명되지만 공식 자료로 확인하지 않았다 **[미확인]**.
- 규칙은 15:32만이 아니라 15:31~15:39의 단일 봉을 허용한다. 봉이 둘 이상이거나 단일가가 아니면 그날을 거부한다.
- 적재 결과: `closing_auction` 행의 `first_bar_at`이 15:32로 남는다. 예: 000150 11-12는 667,000원, 20,716주.

## 6. ETF 종가 단일가 예외

- 7개 종목-날짜에 15:30~15:39 봉이 없다. 마지막 체결이 15:16~15:19다. 저장 종가와 같은 것은 51차에서 확인했다(5/5). 오늘 확인 기준으로는 2,098건 모두 일치(13절).
- 처리: **`closing_auction` 행을 만들지 않는다**(체결이 없었으므로). 정규장 행의 종가가 그날 마지막 체결가다. 결과에 `no_closing_auction` 표시를 남긴다.
- 그날 종목-날짜는 한 트랜잭션으로 모든 세션을 넣는다. 그래서 `regular` 행이 있는데 `closing_auction` 행이 없으면 "체결 없음"이다. "적재 안 됨"과 헷갈리지 않는다.

## 7. 16:00~20:00 데이터의 세션 판정

- **KRX 애프터마켓**으로 판정한다. NXT가 아니다. 근거:
  - 50차: 분봉 원천은 KRX 체결만 담는다(장중 분봉 합계 = 실시간 KRX 거래량, NXT 거래량 별도).
  - 하루 KRX 일봉 거래량 = 이 봉들을 포함한 분봉 합계(1,695/1,800행 정확히 일치).
  - NXT 거래량이 KRX의 수십 %인데도 합계에 들어 있지 않다.
  - ETF(애프터마켓 대상 아님)에는 이 봉이 없다.
  - 2026-09-14(KRX 애프터마켓 개장일, `official_close.AFTER_MARKET_START`) 이후에만 분류한다.
- 테이블에는 거래소 열 `venue`(지금은 `'KRX'`만 허용)를 두었다. NXT 세션은 만들지 않았고, CHECK 제약이 NXT 값을 거부한다(12절 R2).

## 8. 분류할 수 없는 데이터 처리

| 방안 | 내용 | 판단 |
| --- | --- | --- |
| 가까운 세션에 강제로 넣기 | — | 금지(요구) |
| 표에 `unclassified` 세션 행으로 저장 | 집계가 의미 없고, 세션 값이 늘어나 조회 쪽이 매번 걸러야 함 | 채택 안 함 |
| **그 종목-날짜 전체를 넣지 않고 보고서에 남김** | 원본 파일은 보존돼 있어 규칙을 고친 뒤 다시 적재할 수 있다. 일부 세션만 넣으면 하루가 완전한 것처럼 보이는 문제가 없다 | **채택** |

- 적재 보고서(`--report`)의 `rejected`에 종목·날짜·사유·분류 안 된 봉 시각이 남는다. 이번 리허설에서 거부된 것은 284430의 2일(`bad_values`, 음수 거래량)뿐이다.
- 운영에서 거부가 생기면 원본을 확인하고, 규칙을 바꿔야 하면 `RULES_VERSION`을 올린다.

## 9. 테이블 설계(`stock_price_session`, 대기 마이그레이션 `0f6d0ec52ac3`)

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | BIGINT PK | |
| `company_id` | INTEGER NOT NULL, FK `company(id)` ON DELETE CASCADE | `stock_price`와 같은 방식. 종목 코드는 JOIN으로 |
| `price_date` | DATE | KRX 거래일(KST) |
| `venue` | VARCHAR(8) DEFAULT 'KRX' | NXT 확장용. 키에 포함해 NXT를 더할 때 기존 행을 바꾸지 않는다 |
| `session` | VARCHAR(20) | `regular`·`closing_auction`·`post_close`·`after_market`(CHECK) |
| `open_price`·`high_price`·`low_price`·`close_price` | **NUMERIC(자릿수 제한 없음)** | 소수 12자리 원본 보존. 51차 초안의 NUMERIC(14,4)는 원본 일치가 깨져 바꿨다 |
| `volume` | BIGINT | 세션 분봉 거래량 합계 |
| `bar_count` | INTEGER | |
| `first_bar_at`·`last_bar_at` | TIMESTAMPTZ | 첫·마지막 봉 **시작** 시각(원천 KST 시각을 시간대 포함으로 저장) |
| `source` | VARCHAR(24) | `naver_minute` |
| `source_file_sha256` | VARCHAR(64) | 원본 파일 SHA-256(보존본 체크섬 목록과 대조) |
| `rules_version` | SMALLINT | 분류 규칙 버전 |
| `price_basis_date` | DATE | 수집일 = 가격의 수정주가 체계 기준일 |
| `collected_at` | TIMESTAMPTZ | |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |

- 키: `UNIQUE (company_id, price_date, venue, session)`. 이 인덱스가 종목·기간 조회도 맡는다.
- 인덱스: `price_date` 하나만 더한다(날짜별 수집 확인·품질 검사). 분봉 시각 인덱스는 두지 않는다. 행이 세션 단위라 조회 조건이 아니다.
- `stock_price`로의 FK는 두지 않는다(독립 원천, 그날 `stock_price` 행이 없어도 저장).
- CHECK(DB가 마지막으로 막는 것):
  - `ck_sps_venue`·`ck_sps_session`: 허용 값
  - `ck_sps_ohlc`: 저가 > 0, 저가 ≤ 시가·종가 ≤ 고가
  - `ck_sps_volume`: 거래량 ≥ 0
  - `ck_sps_bars`: 봉 수 > 0, 첫 봉 ≤ 마지막 봉
  - `ck_sps_single_price`: 종가 단일가·장후 종가매매는 네 가격이 같음
  - `ck_sps_auction_one_bar`: 종가 단일가는 봉 1개
  - `ck_sps_bar_date`: 봉 시각의 KST 날짜 = 거래일
  - `ck_sps_complete_day`: **수집 시각 ≥ 그날 20:10 KST(장중 행 금지)**
  - `ck_sps_basis_date`: 기준일 ≥ 거래일
  - `ck_sps_sha256`: 해시 형식
- **불변**: `BEFORE UPDATE` 트리거(`stock_price_session_no_update`)가 모든 UPDATE를 거부한다. 적재 도구도 UPDATE·`ON CONFLICT DO UPDATE`를 쓰지 않는다. DELETE는 막지 않는다(downgrade·정리용).
- ENUM 대신 VARCHAR+CHECK: 저장소의 기존 방식과 같다. 세션·거래소를 더할 때 CHECK만 바꾸면 되고 기존 행은 그대로다(PostgreSQL ENUM은 값을 지울 수 없음).
- 모델 `app/models/stock_price_session.py`는 마이그레이션과 컬럼이 같다(테스트로 확인). **승인 전이라 `app/models/__init__.py`에 등록하지 않았다.** 등록하면 autogenerate가 이 테이블을 만들려 한다.
- **마이그레이션 위치는 `backend/alembic/pending/`이다.**
  - `alembic/versions`에 두면 코드 head가 바뀐다. 그러면 지금 실행 중인 개발 백엔드(uvicorn, 포트 8000)의 `/health`가 즉시 `degraded`가 된다.
  - 누군가 `alembic upgrade head`를 돌리면 승인 없이 개발 DB에 적용된다.
  - 시험은 `scripts/alembic_pending.py`(versions + pending, 개발·보존 DB 거부)로만 한다.

## 10. 예상 행 수와 저장 공간

| 항목 | 값 |
| --- | --- |
| 리허설 적재 | 8,814행 / 7거래일(1,207~1,288/일), 2,883,584바이트 = 데이터 1,960kB + 인덱스 824kB, **327B/행** |
| 1년(245거래일, 같은 350종목) | 약 30.8만 행, 약 100MB(작은 표의 페이지 여유가 포함된 값이라 상한에 가깝다) |
| 대안 D-분(실측) | 6거래일 1,020,185행 159.2MB(데이터 103.0 + 인덱스 56.2MB, 156B/행) → 1년 약 4,170만 행·6.5GB |
| 개발 DB 현재 | 435MB(물리 크기, 오늘 측정) |

## 11. 기존 기능 영향

- `stock_price`를 직접 읽는 코드: `app/` 33개 파일(차트 `price_history`, 시장 요약 `market_overview`·`market_snapshot`, 발굴 `discovery`·`market_ranking`, 브리핑 `briefing_db`, 사건 반응 `event_reaction*`, 전망 `outlook`, 성과 `performance_*`, 진단 `diagnosis`, 노출 `exposure_db`, 백테스트 `backtest_db`, 에이전트 도구 `tools/market_tool`, 신선도 `data_status`·`data_monitor`·`health` 등)와 `scripts/` 9개.
- **새 테이블을 읽거나 쓰는 코드는 새 파일 3개뿐**이다(`alembic/pending/…`, `app/models/stock_price_session.py`, `scripts/load_session_minute.py`). 기존 기능은 하나도 새 테이블로 전환하지 않았다.
- 실측 불변 확인(시험 DB, 스냅샷 S0~S4):
  - 대상: 기존 표 22개 전부의 행 수·md5와 공개 GET 13개 응답 해시
  - 공개 GET 13개: 종목 차트 6종목(주식·ETF·수정주가 ETF·레버리지 포함), 사건 반응, 시장 요약, 종목 뉴스, 발굴 목록 4종
  - 시장 요약은 생성 시각 `generated_at`만 빼고 비교(실행마다 다른 유일한 필드임을 확인)
  - 결과: S0(upgrade 전) = S1(upgrade 직후) = S2(전체 적재 뒤) = S3(데이터 있는 downgrade 뒤) = S4(재upgrade·재적재 뒤), **다른 항목 0**
- 기존 통합 테스트 전체를 격리 DB `ai_investment_session_design53_it`에서 돌렸다: **706 통과, 1 건너뜀**(19절).
- 마이그레이션은 `stock_price`의 구조·행을 건드리지 않는다. 시험 DB에서 `stock_price` 251,455행, md5 `b02b4d72…`가 모든 단계에서 같았다.

## 12. 시험 DB 마이그레이션 결과

시험 DB: **`ai_investment_session_design53`**.
- 만든 방법: 같은 서버에 새로 만든 뒤, 개발 DB를 `pg_dump`로 읽어(읽기만) `pg_restore`했다. 만들기 전 같은 이름이 없음을 확인했다.
- 연결 주소: `127.0.0.1:15432/ai_investment_session_design53`

| 단계 | 결과 |
| --- | --- |
| 계보 | 기본 head `c7e2a9f4d158` 그대로. versions + pending의 head = `0f6d0ec52ac3`, `down_revision = c7e2a9f4d158`(충돌·분기 없음) |
| R1 | 빈 표에서 downgrade → S0 → upgrade → S1, S0 = S1 |
| upgrade | 성공. 컬럼 19, CHECK 11, FK 1, 인덱스 3(PK·UNIQUE 키·`price_date`), 트리거 1 확인(`\d stock_price_session`) |
| R2 제약 | 정상 1행 삽입. 다음은 모두 거부: UNIQUE 위반, FK 위반(company -1), `session='nxt_after'`, `venue='NXT'`, OHLC 모순, 거래량 -8, 종가 단일가 가격 여러 개, 봉 날짜 ≠ 거래일, 장중 수집(15:03), **UPDATE(트리거: rows are immutable)**. UPDATE 뒤에도 값 그대로. 시험 행 삭제 후 0행 |
| R8 downgrade(데이터 8,814행 있음) | 성공. 표·트리거 함수 삭제, 리비전 `c7e2a9f4d158`, `stock_price` 251,455행·md5 동일, S3 = S0 |
| R9 재upgrade | 성공, 빈 표 → 재적재 8,814행. **id·created_at을 뺀 데이터 md5가 downgrade 전과 같다**(`4b0d4128650c91fcd39f39b47a66bc52`) |
| 개발·보존 DB 보호 | `alembic_pending`으로 `ai_investment` upgrade → "개발 DB에는 쓰지 않습니다" 거부, `ai_investment_postcheck47` → "보존 중인 DB" 거부(둘 다 연결 전 거부) |

## 13. 실제 적재 행 수와 원본 대조

적재 도구: `scripts/load_session_minute.py`. 원본은 51차 커밋 체크섬 목록으로 먼저 검증한 뒤 읽었다.

| 단계 | 원본 | 결과 |
| --- | --- | --- |
| R3 표본 | 스냅샷, 9종목(주식 4·ETF 5: 005930·000660·087010·000150·069500·122630·385540·273130·284430), 11-10~11-12 | 26일·81행 넣음, 거부 1(284430 11-10). 15:32·종가 단일가 없음 사례 확인 |
| R4 전체 | 스냅샷 11-06~11-13 + 원본 보존본 11-05 | 2,072일·7,445행 + 350일·1,288행 넣음(R3 81행은 "이미 있음"). 합계 **8,814행**, 22초 |
| 공식 종가 대조 | 종가 단일가(없으면 정규장 마지막) 종가 vs 같은 날 `stock_price.close_price` | 11-05~11-12 **2,098/2,098 일치**. 11-13은 `stock_price`에 아직 없음(350) |
| 원본 재계산 대조 | DB 행 vs 원본에서 다시 계산한 값(`--verify-only`) | 스냅샷 기준 2,098일·7,526행 **전부 일치**. 원본 보존본 기준 2,098일·7,607행 전부 일치(11-06~11-12는 다른 파일로 교차 확인) |

13.1 세션·자산별 행 수:

| 자산 | regular | closing_auction | post_close | after_market |
| --- | --- | --- | --- | --- |
| 주식(300) | 2,100 | 2,100 | 1,592 | 2,091 |
| ETF(50) | 348 | 341 | 242 | — |

거래일별 행 수: 11-05 1,288 / 11-06 1,266 / 11-09 1,233 / 11-10 1,278 / 11-11 1,261 / 11-12 1,281 / 11-13 1,207.

- 정합성 예: 삼성전자 11-12 `regular` 13,867,664주 + `closing_auction` 5,004,149주 = 18,871,813주. 50차가 분봉으로 계산한 정규장 거래량과 같다.
- 원본 파일: 작업 전·후 두 보존본의 모든 파일(README 포함) SHA-256·크기·수정 시각이 같다(15절).

## 14. 중복·충돌·롤백 시험

| 시험 | 결과 |
| --- | --- |
| 같은 원본 재적재(R6) | 넣음 0, 이미 있음 2,098일·7,526행. 표 md5 그대로(`083dbc6b…`) |
| dry-run | 넣음 0(READ ONLY 트랜잭션), 이미 있음 2,098일 |
| 충돌(R7) | 스크래치 사본에서 005930 11-12 11:00 봉 거래량 +1 → **충돌 1**, 상세에 DB 13,867,664 / 원본 13,867,665. 아무것도 쓰지 않음(표 md5 그대로), 종료 코드 1 |
| UPDATE | 트리거가 거부(R2, 통합 테스트) |
| downgrade 데이터 손실 | 8,814행 모두 사라짐(표 삭제). 원본 파일에서 같은 데이터로 다시 만들어짐(R9, 데이터 md5 동일) |
| 통합 테스트(CI 방식) | 별도 서버 임시 DB에서 upgrade → 적재 → 재적재 → 충돌 → UPDATE·NXT 거부 → downgrade(표 없으면 적재 거부) → upgrade → 재적재 통과 |

## 15. 개발 DB와 원본이 바뀌지 않았다는 확인

개발 DB(작업 끝, READ ONLY 연결):

| 항목 | 값(51·52차 기록과 같음) |
| --- | --- |
| `stock_price` 행 수 / md5 | 251,455 / `b02b4d72f8c265c98591f3097564ddd4` |
| `market_index` 행 수 / md5 | 1,480 / `7ca607b4c6ee84f34613a6b54d01f212` |
| Alembic | `c7e2a9f4d158` |
| `users` / 격리 행 | 2 / 1 |
| `stock_price_session` 테이블 / 트리거 함수 | 없음 / 없음 |

- 개발 DB에 한 일: `pg_dump`(읽기) 2번, READ ONLY 조회.
- `ai_investment_postcheck47`·`ai_investment_verify`에는 연결하지 않았다(유일한 시도는 보호 규칙이 연결 전에 거부).
- 보존본: `session_audit_2026-11-05_2026-11-12/` 353개, `session_audit_2026-11-06_2026-11-13_snapshot_20261114/` 352개. 작업 전(13:48)·후 모든 파일의 SHA-256·크기·수정 시각이 같다.

## 16. API·프런트엔드 영향

이번에 API 응답은 바꾸지 않았다(`backend/API.md` 변경 없음). 새 테이블 데이터는 어떤 API에도 나오지 않는다.

| 구분 | 항목 |
| --- | --- |
| 1. 지금 API로 가능 | 지수 기준일(`indices[].date`), 지연 배지(`lag_business_days`·`freshness`), 거래량 기준 문구(고정), 차트 고·저 애프터마켓 표시(주식·2026-09-14 이후 규칙) |
| 2. 서버 필드 추가 필요(세션 테이블 불필요) | `price-history.price_basis`, 시장 요약·일일 브리핑 `indices[].confirmation_pending`(51차 5.3절 명세 그대로) |
| 3. 세션 테이블이 있어야 가능 | 정규장 OHLCV(정규장 = `regular` + `closing_auction`), 장후 종가매매·애프터마켓 분리, 정규장 거래량 기준 신호, 종가 단일가 지연(15:32) 표시 |
| 4. 프런트 협의 필요 | 2의 두 필드(51차 5.4절 질문), 3을 화면에 보일지, 정규장 값이 없는 과거(2026-11-05 이전) 표시 방법 |

별도 조회 API 설계안(구현하지 않음, 협의용):
- `GET /api/stocks/{ticker}/sessions?start=YYYY-MM-DD&end=YYYY-MM-DD`
- 응답: `{ticker, venue: "KRX", rules_version, items: [{date, sessions: {regular: {open, high, low, close, volume, first_bar_at, last_bar_at, bar_count}, closing_auction: {...}|null, post_close: {...}|null, after_market: {...}|null}, flags: ["late_closing_auction"|"no_closing_auction"], price_basis_date}], available_from: "2026-11-05"}`
- 가격은 수집 시점 체계라 `stock_price`와 비교하려면 날짜별 조정 계수가 필요하다(51차 3.6절). 그래서 기존 `price-history`에 섞지 않고 별도 경로로 둔다.

## 17. 개발 DB 실제 적용 절차(승인 뒤, 이번에 하지 않음)

1. **사전 검사**
   - `feature/backend` CI 성공 확인
   - 시험 DB 리허설 결과 재확인(`python -m scripts.alembic_pending --database-url <시험 DB> current`)
   - `refresh_data` 실행 중 아님 확인(`python -m scripts.refresh_data --status`)
2. **백업**(적용 직전)
   - `docker exec ai_investment_db pg_dump -U postgres -Fc ai_investment > backend/backups/ai_investment_backup_<시각>_pre_stock_price_session.dump`
   - `stock_price`·`market_index` 행 수·md5와 Alembic 리비전을 기록한다(15절 식)
3. **코드 반영**(한 커밋): `git mv backend/alembic/pending/0f6d0ec52ac3_add_stock_price_session.py backend/alembic/versions/`, `app/models/__init__.py`에 `StockPriceSession` 등록, CI 확인
4. **적용**
   - 개발 백엔드가 실행 중이면, 코드를 받는 순간부터 `/health`가 `degraded`(HTTP 200)가 된다. `check_migrations`가 매번 파일에서 head를 읽기 때문이다.
   - 그래서 코드를 받은 직후 바로 `cd backend && python -m alembic upgrade head`를 실행해 불일치 시간을 최소화한다.
   - 주소는 `127.0.0.1`을 쓴다(`localhost`는 IPv6 시도로 오래 멈춘 사례가 있음, 12절)
5. **적용 후 검증**
   - `alembic current` = `0f6d0ec52ac3`
   - `\d stock_price_session`
   - `/api/health`의 migrations `ok`
   - `stock_price`·`market_index` md5가 2단계 기록과 같음
   - 기존 GET 스냅샷이 적용 전과 같음(이번 `snapshot` 방식)
6. **적재**(별도 승인)
   - 명령: `python -m scripts.load_session_minute --database-url <개발 DB> --allow-dev-db --source <보존본> --manifest <체크섬 목록> --start … --end …`
   - 순서: 11-05(원본 보존본) → 11-06~11-13(스냅샷) → 52차로 보존한 날짜
   - 마지막에 `--verify-only`로 원본 재계산 대조가 전부 일치하는지 본다
7. **실패 시 롤백**
   - 적용 직후(적재 전): `python -m alembic downgrade c7e2a9f4d158` → 표·트리거 삭제, 기존 표 영향 없음. 이어서 3단계 커밋을 되돌린다(`git revert`)
   - 적재 뒤: 같은 downgrade가 **세션 데이터를 전부 지운다.** 원본 파일(52차 보존)에서 다시 만들 수 있지만 원본이 없는 날은 복구할 수 없다
   - downgrade 전에 `pg_dump -t stock_price_session`으로 표만 따로 백업한다
   - 전체 복구는 2단계 덤프로 한다(그 사이 다른 쓰기가 있었다면 그 쓰기도 되돌아간다)
8. **downgrade의 한계**
   - 이 리비전 뒤에 다른 리비전이 쌓이면 그것부터 내려야 한다.
   - 표 일부만 되돌릴 수 없다.
   - 트리거·함수도 같이 지워진다.

## 18. 남은 위험과 최종 승인 필요 사항

남은 위험:
1. 15:32 연장의 원인과 15:31~15:39 규칙의 일반성 **[미확인]**(관찰 35건, 모두 15:32)
2. 특수 일정일이 달력에 없다. 그날은 거부되어 적재가 비게 된다. 수능일 등을 `app/data/krx_calendar.json` `special_close`에 넣고 그날 규칙을 따로 검증해야 한다
3. 수정주가 ETF 분봉의 음수 거래량(284430). 그날은 거부되고, 수정주가 ETF 분봉 거래량의 신뢰성은 **[미확인]**
4. 가격 체계: 행은 수집 시점 체계로 고정된다. 이후 기업 이벤트가 생기면 `stock_price`와 비교할 때 날짜별 조정 계수가 필요하다(51차 3.6절)
5. 09:00 봉의 시가 단일가 체결을 정규장 연속매매와 나눌 수 없다
6. 확보한 분봉은 2026-11-05~11-13뿐이다. **11-16 이후는 52차 절차를 매일 실행해야 생기며, 아직 하나도 확보되지 않았다**
7. NXT 체결·거래대금은 원천에 없다
8. `localhost` 접속 지연(IPv6) — 운영 명령은 `127.0.0.1` 사용

승인이 필요한 것:
1. 안 D(4세션 집계 행) 채택, 분 단위 행(D-분) 미채택
2. 세션 이름과 4번째 세션 `post_close` 추가(요구서 3개 → 4개)
3. 분류 불가 시 종목-날짜 전체 거부 정책, 음수 거래량 날짜 거부
4. 대기 마이그레이션을 `alembic/versions`로 옮겨 개발 DB에 적용(17절 1~5단계)
5. 개발 DB 적재(17절 6단계, `--allow-dev-db`)
6. 시험 DB 2개 유지 기간(아래)

## 19. 테스트 결과와 CI

| 테스트 | 결과 |
| --- | --- |
| `tests/test_session_bars_53.py`(분류 규칙 33개) | 통과. 정상·15:32·단일가 아닌 15:32·15:30과 15:3x 동시·ETF 종가 단일가 없음·장후가 단일가와 다름·ETF/자산 미상 16시 이후·애프터마켓 개장 전·경계(15:20/15:21/15:29/08:59/20:00)·시각 형식 오류·초 ≠ 0·중복 시각·값 오류 6종(음수 거래량 포함)·결측 봉·정규장 없음·휴장일·달력 밖·특수 일정·미완성 수집·공식 종가 미사용·소수 12자리 |
| `tests/test_stock_price_session_53.py`(14개) | 단위 13 통과(개발·보존 DB 거부 7종, 플래그 동작, CLI 거부, 기본 head 불변·pending head, 모델 = 마이그레이션 컬럼, 충돌 기록 KST, 적재 도구 거부). 통합 1 통과(임시 컨테이너의 임시 DB, 14절) |
| 단위 전체(`-m "not slow and not integration"`) | **6,081 통과**(52차 6,035 + 신규 46) |
| 통합 전체(`-m "integration and not slow"`) | **706 통과, 1 건너뜀**. 격리 DB `ai_investment_session_design53_it`. 마이그레이션 왕복 테스트는 임시 컨테이너(포트 15433, 끝나고 삭제)에서 실행 |
| 수정 중 발견·수정 | 48차 보호 테스트(`os.environ["DATABASE_URL"]` 금지)에 걸린 줄을 `os.environ.update`로 변경. 적재 도구가 휴장일을 DB 연결 전에 거부하도록 변경. 테스트 하위 프로세스 출력 UTF-8 |

CI 결과·커밋 해시는 20절(커밋 뒤 최종 보고에 기록).

시험 DB(유지, 자동 삭제하지 않음):

| DB | 크기 | 용도 | 상태 |
| --- | --- | --- | --- |
| `ai_investment_session_design53` | 397MB | 마이그레이션·적재 리허설 | 리비전 `0f6d0ec52ac3`, `stock_price_session` 8,814행(11-05~11-13), 나머지는 개발 DB 사본 그대로 |
| `ai_investment_session_design53_it` | 395MB | 기존 통합 테스트 실행 | 리비전 `c7e2a9f4d158`, 통합 테스트가 쓴 시험 데이터 포함 |

리허설 기록 원문: 세션 스크래치 `r53/rehearsal.log`, 적재 보고서 `r53/R3.json`·`R4a.json`·`R4b.json`·`R7.json`, 스냅샷 `r53/snap_S0~S4.json`.

## 20. 커밋과 push

- 커밋 대상:
  - `backend/app/session_bars.py`
  - `backend/app/models/stock_price_session.py`
  - `backend/alembic/pending/0f6d0ec52ac3_add_stock_price_session.py`, `backend/alembic/pending/README.md`
  - `backend/scripts/alembic_pending.py`, `backend/scripts/load_session_minute.py`
  - 테스트 2개, 이 문서
- `backend/API.md`·`.gitignore`·`.gitattributes`·기존 파일은 바꾸지 않았다.
- 커밋 해시·push·CI 결과는 최종 보고에 적는다(이 문서는 커밋 전에 썼다).
- main 병합은 하지 않는다.
