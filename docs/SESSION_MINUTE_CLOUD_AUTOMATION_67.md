# 67차 분봉 클라우드 자동 수집·보존 (GitHub Actions + Cloudflare R2)

작성 2026-11-17 · 브랜치 `feature/backend`

**결론: 사용자 PC 없이 클라우드에서 돌릴 수집·보존 코드, workflow, 테스트, 운영 문서를 만들었다. 아직 운영 중은 아니다.**

| 항목 | 상태 |
| --- | --- |
| 수집·검증·R2 업로드 코드 | 완료. 가짜 원천·가짜 저장소로 테스트 통과(15절) |
| R2 어댑터(boto3) | 완료. botocore Stubber 테스트 통과. **실제 R2 연결은 검증하지 않았다**(버킷·키가 없다) |
| GitHub-hosted runner의 원천 접근 | `source-probe` job으로 확인한다. 결과는 17절 |
| 예약 실행 | 설정만 했다. **main에 반영되기 전에는 돌지 않는다**(11절) |
| Windows 작업 스케줄러 | 쓰지 않는다 |

사용자가 할 일은 R2 버킷·토큰 만들기(8~9절), GitHub Secrets 등록(10절), main 반영 승인(11절)이다.

## 1. 전체 구조

```
GitHub Actions (ubuntu-latest, 매 평일 20:20·23:20 KST)
  └─ python -m scripts.run_cloud_session_preservation
       1. 설정 검사: 유니버스 350종목(주식 300·ETF 50), Python ≥ 3.11, R2 환경 변수 4개(이름만 출력)
       2. 한국 시간 → KRX 달력(app/krx_calendar): 휴장일·주말이면 SKIP 기록 후 종료
       3. 대상일 = 그날 분봉이 끝난 최근 거래일(20:10 KST 뒤면 오늘)부터 거슬러 최근 7거래일
       4. 날짜마다(오래된 순):
            R2 상태 확인 → 봉인됨이면 건너뜀 / 손상이면 보고만 / 복구 불가 표시면 건너뜀
            52차 fetch (3종목 탐침 → 나머지 → 실패 종목 재수집 2회, 30초·90초 간격)
            52차 preserve + verify (raw/{ticker}.json, MANIFEST.sha256, preserve_manifest.json)
            54차 세션 검증 (load_session_minute.Loader validate, DB 대신 유니버스 파일)
            R2 업로드 → 다시 내려받아 SHA-256 대조
            모두 통과하면 manifest.json(봉인)을 마지막에 쓴다
       5. 실행 기록: R2 _runs/<실행 id>.json, Actions 요약, run_report.json(artifact, 원본 없음)
```

- DB·Ollama·사용자 PC 경로를 쓰지 않는다. `.env`도 읽지 않는다.
- 테스트에서 새 프로세스로 전체 흐름을 돌렸다. 이때 `app.db`·`app.models`·`ollama`·`psycopg`·`app.embeddings`가 한 번도 import되지 않았다(15절 #15).
- 기존 52·54차 코드는 바꾸지 않고 불러서 쓴다.
  - `fetch_session_minute.fetch`·`check_target`
  - `preserve_session_minute.preserve`·`verify`
  - `load_session_minute.Loader`

**추가·수정 파일**

| 파일 | 내용 |
| --- | --- |
| `.github/workflows/session-minute-cloud.yml` | 새 workflow. job은 collect·cloud-unit·source-probe |
| `backend/scripts/run_cloud_session_preservation.py` | 클라우드 실행기(R2·로컬 저장소, 재시도, 봉인, 탐침) |
| `backend/app/data/session_minute_universe.csv` | 350종목(ticker, name, asset_type) |
| `backend/requirements-cloud.txt` | `boto3==1.43.100`. 클라우드 job만 설치한다 |
| `backend/tests/test_cloud_session_preservation_67.py` | 단위 테스트 25개(가짜 원천·저장소) |
| `backend/tests_cloud/test_r2_store_67.py` | R2 어댑터 테스트 6개(Stubber). boto3가 있어야 해서 `tests/` 밖에 둔다 |

기존 파일은 바꾸지 않았다. 대상은 API·DB·프런트엔드·Ollama 설정·`ci.yml`이다.

## 2. GitHub Actions 동작

| job | 언제 | 하는 일 | 비밀값 |
| --- | --- | --- | --- |
| `collect` | 예약(`schedule`), 수동 `mode=collect` | 수집·검증·R2 보존 | R2 4개(이 단계 env에만) |
| `cloud-unit` | `feature/backend` push(관련 파일), 수동 `mode=probe` | 67차·52차 단위 테스트 + R2 Stubber 테스트 | 없음 |
| `source-probe` | 위와 같음 | GitHub 러너 IP에서 원천에 3종목 요청 | 없음 |

**안전 설정**
- `concurrency: session-minute-cloud`, `cancel-in-progress: false`
  - 예약 두 개와 수동 실행이 겹쳐도 하나씩 돈다. R2에 동시에 쓰지 않는다.
- `permissions: contents: read`
- `timeout-minutes`: collect 60분, 나머지 10~15분
- 실행기 안의 시간 예산은 35분이다(`--time-budget-min`). 넘으면 남은 과거 날짜는 다음 실행으로 미룬다. **가장 최근 날짜는 항상 처리한다.**
- 수동 입력(`date`·`lookback`)은 env로만 넘기고 형식을 검사한다. run 줄에 직접 넣지 않는다(스크립트 주입 방지).
- 비밀값 처리
  - 실행기는 환경 변수를 `set`/`missing`으로만 적는다.
  - 오류 문장에 계정 ID·키 값이 들어 있으면 `***`로 가린다.
  - GitHub도 Secrets 값을 로그에서 가린다.
- **공개 저장소다.** 실행 로그와 artifact는 누구나 본다.
  - artifact로는 `run_report.json`(상태·건수)만 올린다(14일 보관).
  - 원본 분봉은 R2 비공개 버킷에만 둔다.

**재시도와 backoff**

| 대상 | 방식 |
| --- | --- |
| 원천 요청 하나 | 기존 `app.http_retry`(연결 오류·timeout·5xx·429, 2회, 지수형) |
| 실패 종목 | 전체를 받은 뒤 실패·봉 없음 종목만 다시 받는다. 30초 뒤, 90초 뒤 두 번 |
| R2 호출 | `RetryingStore`: 4번까지, 2·4·8초. 인증 오류(401·403)는 다시 시도하지 않는다. boto3 자체 재시도(3회 standard)도 있다 |
| 차단 | 401·403·429가 3번 나오면 그 실행의 원천 요청을 멈춘다(`source_blocked`). 우회 요청은 하지 않는다 |

## 3. 예약 실행 시간

```yaml
schedule:
  - cron: "20 20 * * 1-5"
    timezone: "Asia/Seoul"
  - cron: "20 23 * * 1-5"
    timezone: "Asia/Seoul"
```

- `timezone`은 GitHub가 2026-03-19에 추가한 필드다(changelog "GitHub Actions: Late March 2026 updates"). IANA 이름을 받는다.
- 20:20을 고른 이유
  - 52차 `DAY_COMPLETE_AT` 20:10(애프터마켓 ~20:00 + 여유) 뒤다.
  - 실행기도 20:10 전에는 그날을 받지 않는다(`check_target`).
- 23:20 실행은 예비다.
  - 20:20 실행이 지연·누락·실패했을 때 같은 날 다시 시도한다.
  - 이미 봉인됐으면 R2 확인만 하고 끝난다(원천 요청 0).
- 주말은 cron이 뺀다. 공휴일은 Python이 판단한다(4절).

## 4. KRX 휴장일·특별장

- 기준은 `app/data/krx_calendar.json`(36·55·56·58차)이다. 2026-12-31까지 확정이고, 2027은 신정만 들어 있다.
- 오늘이 휴장일이면 수집하지 않는다. `skip_non_trading_day`로 R2 `_runs/`에 기록하고 종료 코드 0으로 끝난다.
- 오늘이 달력 범위 밖이면(미확인일) 설정 오류로 끝난다(종료 코드 2).
  - **2027년 1월 첫 거래일 전에 달력을 갱신해야 한다.** 58차 12절 절차를 따른다.
  - 갱신하지 않으면 2027-01-04부터 매일 실패한다.
- 특별장(예: 2026-11-19 수능일 16:30 마감)
  - 세션 분류는 54차 일정(`app/data/krx_session_schedule.json`)을 따른다.
  - 봉인 manifest의 `krx`에 그날 개장·마감 시각과 일정 상태를 적는다.
  - 2026-11-19 일정은 `announced` 상태다. 검증(validate)은 통과할 수 있지만, 세션 표 적재(`--load`)에는 별도 승인이 필요하다(54차).

## 5. 350종목 수집 과정

- 대상은 `backend/app/data/session_minute_universe.csv`(주식 300, ETF 50)다.
  - `docs/PRICE_DATA_MIGRATION_APPLY_47_tickers.csv`에서 만들었다.
  - 2026-11-16 실제 보존본(`backups/session_minute/2026-11-16/raw`) 350개와 같은 집합이다(차집합 0).
- 원천은 `api.stock.naver.com` 분봉(비공식, KRX 체결만, NXT 제외)이다. 요청 간격은 0.35초로 52차와 같다.
- 하루 약 2~4분(350 요청)을 예상한다. 실측은 아직 없다.
- 탐침
  - 첫 3종목을 먼저 받는다.
  - 3종목 모두 "그날 봉 없음"이면 원천 조회 기간이 지난 것으로 본다. 나머지 347종목은 요청하지 않는다(6절 복구 불가).
- **완료 판정(봉인 조건). 하나라도 어기면 봉인하지 않는다.**
  1. 350종목 파일이 모두 있다. 누락 0이고, 52차 `verify`의 `missing_tickers`가 비어 있다.
  2. 파일마다 그날 봉이 1개 이상이다. 봉이 0개인 종목은 파일을 만들지 않는다. 빈 파일은 생기지 않는다.
  3. 필수 열(`localDateTime`·OHLC·`accumulatedTradingVolume`)과 형식을 52차 `inspect_raw`로 검사한다.
  4. 54차 세션 검증이 350종목 모두 `valid`다.
     - 이 검증이 시각 정렬, 중복 시각, 초 단위 0, KST 세션 경계, 종가 단일가·장후·애프터마켓 규칙, 수집 시각이 그날 완료 뒤인지를 본다.
     - 거부는 0이어야 한다.
  5. 업로드한 모든 객체를 다시 내려받아 SHA-256과 크기가 같다.
- 일부만 성공한 날은 봉인하지 않는다.
  - 받은 원본은 R2에 올려 둔다. 원천 기간이 지나기 전에 보존하기 위해서다.
  - 다음 실행이 다시 받아 빈 곳을 채운다.

## 6. 누락 거래일 복구와 멱등성

날짜마다 R2 상태를 먼저 본다.

| R2 상태 | 판정 | 처리 |
| --- | --- | --- |
| `manifest.json` 있고, 목록의 모든 객체 크기·SHA-256(메타데이터) 일치 | `already_complete` | 원천 요청 없이 건너뜀 |
| `manifest.json` 있는데 객체 없음·크기·SHA-256 다름 | `remote_damaged` | **보고만 한다**(자동 수리 없음). 실패로 끝나 알림 |
| `_status/<날짜>.json`(복구 불가 표시) | `unrecoverable_known` | 건너뜀(성공 취급) |
| 객체가 일부만 있음(봉인 없음) | 다시 수집 | 같은 SHA-256 원본은 건너뛰고 없는 것만 올린다 |
| 아무것도 없음 | 수집 | |

- **덮어쓰기 금지**
  - 원본(`raw/`·`source_meta/`)이 이미 있고 SHA-256이 다르면 `conflict`로 판정한다. 그날은 **파일을 하나도 올리지 않는다.**
  - 사용자 확인 대상이고, 실패로 끝난다.
  - 색인 파일(`MANIFEST.sha256`·`preserve_manifest.json`·`preserve_log.jsonl`·`session_validate.json`)만 봉인 전에 갱신할 수 있다.
  - 봉인(`manifest.json`)은 한 번 쓰면 다시 쓰지 않는다. 봉인된 날짜는 처리 자체를 하지 않는다.
- **복구 범위**
  - 원천은 최근 7거래일만 준다(`session_ohlcv.fetch_minute` 설명, 50차).
  - 실행기는 그보다 앞을 요청하지 않는다. `--lookback`은 1~7이고, `--date`도 그 범위 밖이면 거부한다(종료 코드 2).
- **복구 불가**
  - 범위 안인데 원천이 봉을 주지 않으면(탐침 3종목 모두 0봉) `_status/<날짜>.json`을 쓴다.
  - 그 실행은 실패로 끝나 한 번 알린다. 다음 실행부터는 `unrecoverable_known`이다.
- **최신일 우선 보장**
  - 오래된 날짜가 실패해도 다음 날짜로 넘어간다.
  - 시간 예산을 넘겨도 가장 최근 날짜는 처리한다.
- **재실행 결과**
  - 같은 작업을 다시 돌려도 R2 객체는 같다.
  - 실행마다 새로 생기는 것은 `_runs/<실행 id>.json`, 그리고 봉인 전 재수집의 `source_meta/<새 원본 id>/` 뿐이다.
  - 앞선 미완료 실행의 `source_meta`는 남는다(지우지 않음). 봉인 manifest에는 그 실행의 파일만 적힌다.

## 7. R2 저장 구조와 SHA-256

```text
<버킷>/session-minute/
├── 2026-11-16/
│   ├── raw/000080.json … raw/0193Y0.json      350개, 원천 응답 그대로(json.dumps). 로컬 보존본과 같은 형식
│   ├── source_meta/<원본 id>/fetch_log.json     52차 수집 기록(요청 시각·종목별 봉 수·실패)
│   ├── MANIFEST.sha256                          `sha256sum -c` 입력(52차)
│   ├── preserve_manifest.json                   파일별 크기·SHA-256·날짜별 봉 수(52차)
│   ├── preserve_log.jsonl
│   ├── session_validate.json                    54차 세션 검증 요약
│   └── manifest.json                            봉인(마지막에 씀)
├── _status/2026-11-13.json                      복구 불가 표시(있을 때만)
└── _runs/20261117T202003_123456789.json         실행 기록
```

- 지시서 예시의 `ticker.csv`·`sha256sum.txt` 대신 **기존 형식**(`raw/{ticker}.json`, `MANIFEST.sha256`)을 그대로 쓴다.
  - 50~52차 보존본, 54차 적재 도구와 같은 형식이다.
  - R2에서 내려받은 날짜 폴더를 `preserve_session_minute --verify-only`·`load_session_minute --validate-only`에 바로 줄 수 있다.
- 객체마다 메타데이터 `x-amz-meta-sha256`을 붙인다.
- 업로드 직후 같은 객체를 다시 내려받아 SHA-256과 크기를 대조한다. 불일치는 `upload_incomplete`다.
- `--deep-verify`는 봉인된 날짜도 객체를 모두 내려받아 다시 해시한다.

**`manifest.json`(봉인) 항목**

| 항목 | 내용 |
| --- | --- |
| `trade_date` | 거래일 |
| `sealed_at_kst`·`sealed_at_utc` | 봉인 시각 |
| `run_id` | 실행 id |
| `code_version` | `GITHUB_SHA` |
| `source` | 원천 URL, 설명 |
| `krx` | 그날 개장·마감, 세션 일정 |
| `ticker_count`·`assets` | 350, 주식 300·ETF 50 |
| `tickers` | 종목별 `rows`(그날 봉 수)·`bytes`·`sha256`·`asset_type` |
| `files`·`file_count`·`total_bytes` | 업로드한 모든 파일의 크기·SHA-256 |
| `session_validation` | `ok`·`counts`·`flags`·규칙 버전·일정 버전 |
| `fetch_attempts` | 탐침·전체·재수집별 요청·성공·실패·차단 수 |
| `status` | `complete` |

- 하루 크기는 2026-11-16 실측 약 27MB(원본 350개)다.

## 8. Cloudflare R2 버킷 만들기(사용자)

**아래는 사용자 계정에서 직접 할 일이다. 이번 작업에서 버킷이나 키를 만들지 않았다.**

1. Cloudflare 대시보드 → **R2 Object Storage**.
   - 처음이면 R2 구독을 켠다. 무료 사용량이 있어도 결제 수단 등록을 요구할 수 있다.
2. **Create bucket**
   - 이름 예: `lab-scale-session-minute`
   - 위치: Automatic(또는 Asia-Pacific 힌트)
   - 기본 스토리지 클래스: Standard
3. 버킷 **Settings**
   - **Public access**: `r2.dev` 하위 도메인과 Custom Domains를 켜지 않는다(기본 꺼짐 유지). 공개 URL을 만들지 않는다.
   - **CORS**: 설정하지 않는다.
   - **Object lifecycle rules**: 지우는 규칙을 넣지 않는다. 영구 보존이 목적이다.
4. 버킷 이름을 적어 둔다. GitHub Secret `R2_BUCKET_NAME`에 쓴다.

## 9. R2 API 토큰 발급(사용자)

1. R2 화면 → **Manage R2 API Tokens** → **Create API token**(Account API token 권장)
2. 설정
   - **Permissions**: `Object Read & Write`. Admin Read & Write는 쓰지 않는다.
   - **Specify bucket(s)**: 위 버킷 하나만(Apply to specific buckets only)
   - **TTL**: 원하는 기간. 만료되면 수집이 `store_auth_error`로 실패한다. 만료 전에 다시 발급한다.
   - **Client IP filtering**: 쓰지 않는다. GitHub 러너 IP는 고정이 아니다.
3. 만든 뒤 한 번만 보이는 값을 GitHub Secrets에 바로 넣는다(10절). 파일·메모·채팅에 붙여 넣지 않는다.
   - **Access Key ID** → `R2_ACCESS_KEY_ID`
   - **Secret Access Key** → `R2_SECRET_ACCESS_KEY`
   - 같은 화면의 S3 엔드포인트 `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`에서 `<ACCOUNT_ID>` → `R2_ACCOUNT_ID`
   - 엔드포인트는 실행기가 계정 ID로 만든다.

## 10. GitHub Secrets 설정(사용자)

저장소 **Settings → Secrets and variables → Actions → Repository secrets → New repository secret**에서 4개를 넣는다.

| 이름 | 값 |
| --- | --- |
| `R2_ACCOUNT_ID` | Cloudflare 계정 ID(32자 16진수) |
| `R2_ACCESS_KEY_ID` | 9절 Access Key ID |
| `R2_SECRET_ACCESS_KEY` | 9절 Secret Access Key |
| `R2_BUCKET_NAME` | 8절 버킷 이름 |

- 비밀값을 Variables(공개 값)에 넣지 않는다. `.env`에도 넣지 않는다.
- 공개 저장소이므로 fork에서 온 PR 실행에는 Secrets가 전달되지 않는다. collect job은 예약·수동 실행에서만 돈다.
- 값이 빠지면 collect가 다음 메시지와 함께 종료 코드 2로 끝난다. 값은 출력하지 않는다.
  - `R2 환경 변수가 없습니다: R2_ACCOUNT_ID, …`

## 11. workflow 활성화(main 반영 뒤)

1. **예약 실행은 기본 브랜치(main)의 workflow 파일로만 동작한다.**
   - `feature/backend`에 있는 동안 cron은 돌지 않는다.
   - 수동 실행(`workflow_dispatch`)도 workflow가 기본 브랜치에 있어야 Actions 화면에 나타난다.
2. 순서
   1. 10절 Secrets 등록
   2. 사용자 승인 뒤 `feature/backend` → main 병합(PR)
   3. Actions → "Session minute cloud preservation" → Run workflow
      - `mode=probe`로 원천 접근 확인
      - `mode=collect`, `dry_run=true`로 R2 연결 확인
      - `mode=collect`로 실제 수집
3. 첫 실제 수집 뒤 확인
   - R2 `session-minute/<날짜>/manifest.json` 존재
   - Actions 요약표의 결과 `completed`
   - 받은 350, 세션 검증 `True`
4. 로컬 검증(선택)
   - R2에서 날짜 폴더를 내려받는다.
   - `python -m scripts.preserve_session_minute --verify-only --dest <폴더>`를 돌린다.

## 12. 수동 실행

Actions 화면 → workflow 선택 → **Run workflow**

| 입력 | 의미 |
| --- | --- |
| `mode` | `collect`(기본) 또는 `probe`(원천 3종목만, 비밀값 불필요) |
| `date` | 그 거래일만. 최근 7거래일 안이어야 한다 |
| `lookback` | 확인할 최근 거래일 수(1~7, 기본 7) |
| `dry_run` | R2 상태와 대상일만 확인. 원천 요청·업로드 없음 |
| `deep_verify` | 봉인된 날짜도 내려받아 다시 해시 |

로컬에서 같은 코드를 시험하려면 R2 대신 폴더를 쓴다(외부 요청은 원천만).

```bash
cd backend
python -m scripts.run_cloud_session_preservation --work-dir /tmp/smc --store local --local-store-dir /tmp/fake_r2 --date 2026-11-16
```

## 13. 성공·실패 확인

| 위치 | 내용 |
| --- | --- |
| Actions 실행 화면 | 초록 = 종료 코드 0(봉인 완료·이미 완료·휴장 SKIP), 빨강 = 실패. GitHub가 실패를 이메일로 알린다(계정 알림 설정) |
| 실행 요약(Summary) | 날짜별 결과·받은 종목 수·업로드·같음·충돌·세션 검증·소요 초 |
| artifact `session-minute-run-report-<id>` | `run_report.json`(14일) |
| R2 `_runs/` | 실행마다 같은 내용. 영구 |

날짜별 `status` 값:

| 값 | 의미 | 종료 코드 |
| --- | --- | --- |
| `completed` | 이번에 봉인 | 0 |
| `already_complete` | 이미 봉인 | 0 |
| `unrecoverable_known` | 이미 복구 불가로 표시됨 | 0 |
| `would_collect` | dry-run에서 수집 대상 | 0 |
| `unrecoverable_new` | 원천 기간 경과로 처음 확인 | 1 |
| `incomplete_tickers` | 일부 종목 없음(받은 것은 보존, 다음 실행 재시도) | 1 |
| `session_validation_failed` | 54차 검증 거부(`session_validate.json`에 사유) | 1 |
| `conflict` | 기존 원본과 SHA-256 다름(아무것도 안 올림, **사용자 확인**) | 1 |
| `remote_damaged` | 봉인된 날짜 객체 손상(**사용자 확인**) | 1 |
| `upload_incomplete` | 업로드·재검증 실패(다음 실행 재시도) | 1 |
| `source_blocked`·`skipped_source_blocked` | 원천이 401·403·429 응답(우회 없음) | 1 |
| `store_auth_error` | R2 키 권한·만료 | 1 |
| `deferred_time_budget` | 시간 예산 초과로 다음 실행에 미룸 | 1 |
| `calendar_unknown`·`error` | 달력 범위 밖·예외 | 1 |
| 실행 전체 `config_error` | Secrets·인자·달력 오류 | 2 |

## 14. GitHub Actions 예약의 제한

- 예약은 기본 브랜치의 최신 커밋 workflow로만 돈다.
- 예약 시각은 보장되지 않는다.
  - 부하가 높으면 몇 분~수십 분 늦거나 드물게 건너뛴다(특히 정각 근처).
  - 그래서 20분에 두고, 23:20 예비 실행을 넣었다.
  - 다음 거래일 실행이 최근 7거래일을 다시 확인한다.
  - **원천 보존 기간(7거래일) 안에 한 번만 성공하면 그날 데이터는 남는다.**
- 공개 저장소는 **60일 동안 저장소 활동이 없으면 예약 workflow가 자동으로 꺼진다.**
  - GitHub가 경고 메일을 보낸다.
  - Actions 화면에서 다시 켜거나 커밋을 하면 된다.
- 최소 간격은 5분이다. 이 workflow는 하루 두 번이라 상관없다.
- GitHub-hosted runner IP는 Azure 대역이고 해외다.
  - 원천이 해외 IP를 막거나 제한하면 동작하지 않는다(17절).
  - 이때는 우회하지 않는다. self-hosted runner(국내) 같은 대안을 사용자와 정한다.

## 15. 테스트

`backend/tests/test_cloud_session_preservation_67.py`(25개, 외부 요청 없음, 약 60초)와 `backend/tests_cloud/test_r2_store_67.py`(6개)가 있다. 지시서 9절 16개 항목과의 대응은 다음과 같다.

| # | 항목 | 테스트 |
| --- | --- | --- |
| 1 | KRX 거래일 판별 | `test_targets_are_last_seven_trading_days_and_today_only_after_2010` |
| 2 | 주말·휴장일 SKIP | `test_weekend_and_holiday_skip_without_requests`(11-21 토, 12-25 성탄절: 요청 0, `_runs` 기록만) |
| 3 | 350종목 완료 판정 | `test_full_day_350_tickers_sealed_last_with_sha256` |
| 4 | 종목 누락 감지 | `test_missing_ticker_is_not_success_and_is_retried`(탐침·전체·재수집 2회, 봉인 없음) |
| 5 | 빈 파일 감지 | `test_empty_response_creates_no_file_and_blocks_seal` |
| 6 | 중복 타임스탬프 | `test_duplicate_timestamp_blocks_seal` |
| 7 | 기존 세션 검증과 일치 | `test_session_validation_matches_direct_54_classification`(오프라인 Loader = `session_bars.classify_day` 직접 결과, 349/350) |
| 8 | SHA-256 계산 | #3에서 업로드 객체를 다시 해시해 manifest·`MANIFEST.sha256`과 대조 |
| 9 | 보존된 날 SKIP | `test_rerun_skips_sealed_day_without_requests`(원천 요청 0, 새 객체는 `_runs`뿐) |
| 10 | 기존 파일 변경 감지 | `test_different_existing_raw_is_conflict_and_nothing_is_overwritten`, `test_damaged_sealed_day_is_reported_not_repaired` |
| 11 | 일부 업로드 실패 | `test_partial_upload_failure_is_not_sealed_and_next_run_fills_gap` |
| 12 | manifest 최종 작성 | #3: 그날 put 순서의 마지막이 `manifest.json`, 한 번만 |
| 13 | R2 연결 실패·재시도 | `test_store_connection_errors_are_retried_with_backoff`(2·4초), `test_store_auth_error_is_not_retried_and_fails`, #11(재시도 3회 뒤 실패) |
| 14 | 비밀값 미노출 | `test_secrets_are_redacted_from_errors_and_report`, `test_missing_r2_env_lists_names_only`, Stubber `test_secrets_not_in_repr_or_errors` |
| 15 | DB·Ollama 비의존 | `test_runs_without_db_or_ollama_modules`(새 프로세스, DB·Ollama 주소를 닫힌 포트로 두고 전체 흐름) |
| 16 | 복구 범위 | `test_recovery_range_is_limited_to_provider_window`, `test_expired_day_is_marked_unrecoverable_once_with_probe_only`, `test_old_missing_day_does_not_block_latest` |

**추가 테스트**
- 일시 실패 종목이 재수집으로 회복됨
- 원천 차단 시 요청 중단(3회 뒤 0)
- dry-run 요청 0
- 탐침 3종목
- 유니버스 300·50

**R2 Stubber 테스트(실제 boto3 1.43.100)**
- 엔드포인트·`region=auto`·체크섬 `when_required`
- 404 → 없음
- put에 sha256 메타데이터와 ContentType만 보냄. ACL 없음, 공개 아님
- 403 → `StoreAuthError`
- 목록 페이지 넘김

**실행 위치**
- `tests/`는 기존 CI(`ci.yml` backend-unit)가 그대로 돌린다.
- `tests_cloud/`는 이 workflow의 `cloud-unit` job이 boto3를 설치한 뒤 돌린다.

## 16. 비용

**R2(작성자가 알고 있는 공개 요금이다. 이번에 Cloudflare 요금 페이지를 다시 확인하지 않았으니 사용자 계정에서 확인한다)**
- 무료: 저장 10GB-월, Class A(쓰기·목록) 100만 회/월, Class B(읽기) 1,000만 회/월, egress 무료
- 하루 약 27MB × 월 21거래일이면 약 0.57GB/월씩 늘어난다.
  - **약 17개월 동안 무료 저장 범위 안이다.**
  - 그 뒤 초과분은 GB-월당 약 $0.015다. 예: 2년차 말 약 14GB → 월 약 $0.06
- 요청 수는 모두 무료 범위 안이다.
  - 쓰기: 거래일당 약 360회 + 실행 기록 → 월 약 1만 회
  - 읽기: 업로드 재검증 + HEAD → 월 약 3만 회
- 확인: Cloudflare 대시보드 → R2 → Overview/Metrics(저장량·Class A/B 요청), Billing

**GitHub Actions**
- 공개 저장소의 표준 GitHub-hosted runner는 무료다(분 단위 과금 없음).
- 실행당 약 5~10분(설치 포함)을 예상한다. 실측 전이다.
- 확인: 저장소 Insights 또는 계정 Settings → Billing → Actions 사용량

## 17. GitHub-hosted runner에서 원천 접근

- `source-probe` job이 `feature/backend` push 때 3종목(005930·000660·첫 ETF)을 요청한다.
- 최근 완료 거래일 하루만 요청하고, 저장·업로드는 하지 않는다.
- 결과는 이번 push의 실행 결과로 확인한다(아래 표에 적는다).
- 로컬 PC(국내 IP)에서는 2026-11-16 실제 수집이 350/350 성공했다(`docs/SESSION_MINUTE_LIVE_PRESERVATION_2026-11-16.md`). **해외 IP 결과는 이 표가 유일한 근거다.**

| 확인 | 결과 |
| --- | --- |
| push 실행 | (커밋 뒤 채움) |

## 18. 운영 중 장애와 대응

| 장애 | 증상 | 대응 |
| --- | --- | --- |
| 원천이 해외 IP 차단·제한 | `source_blocked`, probe 실패 | 우회 금지. self-hosted runner(국내) 또는 로컬 52차 수동 절차로 돌아감. 사용자 결정 |
| 원천 형식 변경 | `incomplete_tickers`·`session_validation_failed` 다수, `session_validate.json` 거부 사유 | 원본은 보존되므로 규칙 확인 뒤 판단. 54차 규칙 수정은 별도 작업 |
| 예약 누락·지연 | 그날 `_runs` 없음 | 23:20 예비 실행, 다음 거래일 실행이 채움. 급하면 수동 실행 |
| 7거래일 넘게 실패 | `unrecoverable_new` | 그날은 복구 불가. 원인(차단·키 만료·workflow 꺼짐) 해결 |
| R2 키 만료·권한 | `store_auth_error` | 9절로 새 토큰 → 10절 Secrets 갱신 |
| 충돌 | `conflict` | 기존 객체와 새 원본을 내려받아 비교. 덮어쓸지 사용자가 결정(실행기는 덮어쓰지 않음) |
| 봉인 손상 | `remote_damaged` | 버킷 객체가 지워졌거나 바뀜. 원천 기간 안이면 수동 확인 뒤 복구 방법 결정 |
| KRX 달력 만료 | `config_error`(오늘이 달력 범위 밖) | 12월 중순 KRX 공지로 2027 달력 갱신(58차) |
| 60일 무활동 | 예약이 꺼짐(GitHub 메일) | Actions에서 다시 켬 |
| 유니버스 변경(상장폐지·편입) | 그 종목이 계속 `incomplete_tickers` | `session_minute_universe.csv` 갱신은 별도 결정(지금은 350 고정) |

## 19. 하지 않은 것

- R2 버킷·API 토큰 생성, GitHub Secrets 등록. 사용자 계정 작업이다.
- 실제 R2 업로드, 클라우드 350종목 실수집. 비밀값이 없다.
- main 병합, 예약 활성화
- 로컬 64차(11-16) 원본 재수집·변경. 11-17~19 로컬 수집
- DB 쓰기, 세션 표 생성·적재, 마이그레이션
- 기존 API·프런트엔드·Ollama·`.env` 변경
- Windows 작업 스케줄러 등록
