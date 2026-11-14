# 52차 분봉 원본 일일 보존 도구와 운영 절차 (2026-11-14)

작업일: 2026-11-14(토, KST). 브랜치 `feature/backend`. 시작 커밋 `6c4a879`(51차 `ff769eb`·`6c4a879` 포함, 원격과 동기화).
API 계약은 바뀌지 않았다. 그래서 `backend/API.md`에는 아무것도 더하지 않았다.

## 요약: 무엇이 보존됐고 무엇이 아직인가

| 날짜 | 상태 | 근거 |
| --- | --- | --- |
| 2026-11-05 | **보존·검증 완료**(51차 원본 보존본, 유일한 사본) | 오늘 51차 커밋 체크섬 목록으로 352/352 일치(5.3절) |
| 2026-11-06 ~ 11-12 | **보존·검증 완료**(51차 원본 보존본과 스냅샷 두 곳) | 352/352, 351/351 일치 |
| 2026-11-13 | **보존·검증 완료**(51차 스냅샷) | 351/351 일치 |
| 2026-11-14·15 | 휴장(주말) | — |
| **2026-11-16 이후** | **아직 확보하지 못함.** 분봉이 아직 생기지 않았다 | 이 문서 2절 절차로 매일 확보해야 한다 |

- 이번에 만든 것은 **도구와 절차**다. 11-16 분봉은 아직 없으므로 보존하지 않았고, 보존했다고 보고하지 않는다.
- 원본 확보 스크립트(`fetch_session_minute`)는 이번 차수 금지 사항(원천 재수집 금지)에 따라 **실제 원천에 한 번도 요청하지 않았다**.
  - 검증 범위: 가짜 응답 테스트, 거부·dry-run 실행.
  - 이 스크립트가 쓰는 함수(`app.session_ohlcv.fetch_minute`)는 51차 스냅샷 수집에서 350/350 성공했다.
  - 다만 **하루 범위 요청**(`startDateTime=그날0000`, `endDateTime=그날2359`)을 실제 원천에 보낸 적은 없다. 그래서 11-16 첫 실행 결과를 2.4절 기준으로 반드시 확인해야 한다.

## 1. 만든 것

| 파일 | 역할 |
| --- | --- |
| `backend/scripts/fetch_session_minute.py` | **원본 확보.** 거래일 하루의 분봉을 새 스테이징 폴더에 받는다. 50·51차와 같은 형식이다: `raw/{ticker}.json` + `fetch_log.json`. DB를 쓰지 않는다 |
| `backend/scripts/preserve_session_minute.py` | **보존·검증.** 원본 폴더를 거래일별 보존 폴더로 바이트 그대로 복사하고 SHA-256으로 검증한다. `--dry-run`·`--verify-only`를 지원한다. DB·네트워크를 쓰지 않는다 |
| `backend/tests/test_preserve_session_minute_52.py` | 단위 테스트 37개(임시 폴더만 사용, 네트워크·DB 없음) |
| 이 문서 | 운영 절차 + 52차 보고 |

두 단계로 나눈 이유:
- 11-16 분봉의 "원본"은 어디에서도 자동으로 생기지 않는다. 원천이 네이버 분봉 API뿐이라 그날 20:10 뒤에 누군가 받아야 원본 폴더가 생긴다.
- 받기(네트워크)와 보존(파일 복사·검증)을 나누면 보존 단계는 네트워크·DB 없이 몇 번이든 다시 돌릴 수 있다.

DB 의존성: 두 스크립트는 `app.krx_calendar`(달력 파일), `app.session_ohlcv`·`app.http_retry`(받기 단계만)만 쓴다. import 뒤 `sqlalchemy`·`app.db` 모듈이 하나도 올라오지 않음을 확인했다. **운영에 DB 연결과 수집 서버는 필요 없다.** 필요한 것은 인터넷 연결(받기 단계)과 `backend/.venv` 파이썬이다.

## 2. 매일 운영 절차

### 2.1 언제

| 항목 | 값 |
| --- | --- |
| 가장 이른 시각 | **그 거래일 20:10 KST 이후.** 애프터마켓이 20:00에 끝난다. 그 전에는 스크립트가 거부한다 |
| 권장 | 그날 밤 또는 다음 날 오전 |
| 마감 | 원천은 최근 약 7거래일만 준다. 11-13에는 11-05부터, 11-14(토)에는 11-06부터 줬다. 정확한 규칙은 **[미확인]**이다. **거래일 뒤 3거래일 안**에 끝낸다 |
| 휴장일 | 실행하지 않는다(스크립트가 거부). 다음 거래일에 그날 것만 받는다 |
| 소요 | 받기 약 4분(350건, 요청 간격 0.35초, 51차 실측 3분 50초). 보존 약 10초 |

### 2.2 명령(Git Bash, `backend` 폴더에서)

```bash
cd /c/project/lab-scale/backend
export PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe
D=2026-11-16                                                     # 보존할 거래일
TS=$(date +%Y%m%dT%H%M)                                          # 이번 받기 시각(스테이징 폴더를 매번 새로)
LIST=backups/session_audit_2026-11-06_2026-11-13_snapshot_20261114   # 종목 목록 기준(파일 이름 350개, 읽기만)
STAGE=backups/session_minute_staging/${D}_${TS}                  # 원본 폴더(새로 생김)
ROOT=backups/session_minute                                      # 보존 전용 상위 폴더

# 1) 받기 전 확인(요청·쓰기 없음)
$PY -m scripts.fetch_session_minute --date $D --tickers-from $LIST --out $STAGE --dry-run
# 2) 원본 받기
$PY -m scripts.fetch_session_minute --date $D --tickers-from $LIST --out $STAGE
# 3) 보존 계획 확인(복사 없음)
$PY -m scripts.preserve_session_minute --source $STAGE --dest-root $ROOT --date $D --expected-from $LIST --dry-run
# 4) 보존(+복사 뒤 재검증)
$PY -m scripts.preserve_session_minute --source $STAGE --dest-root $ROOT --date $D --expected-from $LIST
# 5) 독립 재검증: 도구 검증 + 원본 대조, 그리고 표준 도구 sha256sum
$PY -m scripts.preserve_session_minute --verify-only --dest $ROOT/$D --source $STAGE
(cd $ROOT/$D && sha256sum -c --quiet MANIFEST.sha256 && echo "sha256sum OK")
```

PowerShell에서는 변수만 바꾼다.

```powershell
cd C:\project\lab-scale\backend
$env:PYTHONIOENCODING = "utf-8"
$D = "2026-11-16"; $TS = Get-Date -Format "yyyyMMddTHHmm"
$LIST = "backups/session_audit_2026-11-06_2026-11-13_snapshot_20261114"
$STAGE = "backups/session_minute_staging/${D}_$TS"; $ROOT = "backups/session_minute"
.venv\Scripts\python.exe -m scripts.fetch_session_minute --date $D --tickers-from $LIST --out $STAGE
.venv\Scripts\python.exe -m scripts.preserve_session_minute --source $STAGE --dest-root $ROOT --date $D --expected-from $LIST
.venv\Scripts\python.exe -m scripts.preserve_session_minute --verify-only --dest "$ROOT/$D" --source $STAGE
```

- **종목 목록**은 매일 같은 고정 목록(51차 스냅샷의 350개 파일 이름)을 쓴다.
  - 전날 보존본을 목록으로 쓰지 않는다. 전날 거래정지로 빠진 종목이 있으면 목록이 조용히 줄어들기 때문이다.
  - 수집 대상 종목(`company`)이 바뀌면 그때 목록 파일을 새로 만든다(6절 4번). 사용법: `--tickers-file <파일>`, `--expected-file <파일>`(한 줄에 하나, `#`은 주석).
- **날짜 지정**: 하루는 `--date YYYY-MM-DD`다. 보존 단계는 `--start`·`--end`로 기간도 받는다(보존 폴더 이름 `시작_끝`, 최대 31거래일).
  - 받기 단계는 하루씩만 받는다. 놓친 날이 여러 날이면 날짜마다 1)~5)를 반복한다.

### 2.3 원본 폴더 확인 방법

- 받기가 끝나면 `$STAGE/raw/`에 종목별 `{ticker}.json`이 있다. `$STAGE/fetch_log.json`에는 대상 날짜, 요청 범위, 종목별 받은 시각·봉 수, `failed`·`no_bars`가 들어 있다.
- 받기 출력 마지막 줄: `받음 N, 실패 0, 그날 봉 없음 0 / 350`
- 3)의 dry-run 출력에서 `날짜별 파일 {'2026-11-16': 350}`과 `누락 0`을 확인한다.

### 2.4 정상 완료 판정(모두 만족해야 "그날 보존 완료")

| # | 확인 | 기대 |
| --- | --- | --- |
| 1 | 받기 종료 코드·출력 | `0`, `실패 0, 그날 봉 없음 0` |
| 2 | 보존 출력 | `예상 종목 350, 발견 350, … 누락 0, 예상 밖 0, 중복 0, 비정상 0`, `충돌 0`, `복사 실패 0` |
| 3 | 보존 결과 줄 | `결과: ok`. 하루치 원본이면 warning 사유가 없어야 한다. warning이면 경고 문장을 읽고 7절에 따라 판단한다 |
| 4 | 복사 뒤 재검증 | `복사 뒤 재검증: ok (일치 351/351)`(분봉 350 + `fetch_log.json`) |
| 5 | verify-only | `결과: ok`, 불일치·없음·목록 밖·임시 파일 0 |
| 6 | `sha256sum -c` | `sha256sum OK` |
| 7 | 첫 실행(11-16)만 추가 확인 | `fetch_log.json`의 `other_date_bars`가 비어 있다(하루 범위 요청이 그날 것만 돌려줌). 아무 종목이나 `raw/005930.json`의 첫·마지막 봉이 `20261116090000`·`2026111619…`다 |

### 2.5 보존 폴더 형식과 위치

```
backend/backups/session_minute/                 <- 보존 전용 상위 폴더(이 도구만 씀)
  PRESERVATION_LOG.tsv                          <- 모든 날짜의 실행 기록(한 줄씩 추가)
  2026-11-16/
    raw/{ticker}.json                           <- 원본과 바이트까지 같은 사본(수정 시각 보존)
    source_meta/<원본 폴더 이름>__<8자리>/fetch_log.json   <- 원본 폴더 바로 아래 파일의 사본(출처 기록)
    MANIFEST.sha256                             <- sha256sum -c 입력(LF)
    preserve_manifest.json                      <- 파일별 크기·SHA-256·원본/사본 수정 시각·날짜별 봉 수, 대상 날짜, 예상 종목
    preserve_log.jsonl                          <- 이 날짜의 실행마다 한 줄
backend/backups/session_minute_staging/<날짜>_<받은 시각>/   <- 받기 단계 원본(지우지 않음)
```

- 하루 크기: 원본(스테이징) 약 27MB + 보존본 약 27MB(51차 실측 평균, 압축 안 함)
- 스테이징 원본은 보존이 끝나도 **지우지 않는다**. 원본과 사본을 둘 다 두는 것이 원칙이고, 정리는 사용자가 따로 결정한다.

### 2.6 매일 기록하는 방법

1. 자동: `backups/session_minute/PRESERVATION_LOG.tsv`에 실행마다 한 줄이 쌓인다(시각·날짜·결과·예상/발견·복사·이미 보존·충돌·누락·비정상·바이트·원본 경로). 날짜 폴더의 `preserve_log.jsonl`에도 남는다.
2. 수동(권장, 주 1회 묶어도 됨): 체크섬 목록을 저장소에 남긴다. 51차 방식과 같고, 30KB 정도의 텍스트다.
   ```bash
   mkdir -p ../docs/session_minute && cp backups/session_minute/$D/MANIFEST.sha256 ../docs/session_minute/$D.sha256
   ```
   `.gitattributes`의 `*.sha256 text eol=lf`가 줄바꿈을 LF로 유지한다. 그래서 다른 PC에서도 `sha256sum -c`가 통과한다.

### 2.7 Git에 올리지 않는 방법

- `backend/backups/`는 `.gitignore` 11번째 줄에 있다. `session_minute/`와 `session_minute_staging/`도 그 아래라 자동으로 빠진다.
- 커밋 전 확인:
  ```bash
  git status --short | grep backups          # 아무것도 나오지 않아야 함
  git check-ignore -v backend/backups/session_minute/$D/raw/005930.json   # .gitignore:11 이 나와야 함
  ```
- 체크섬 목록만 `docs/session_minute/`에 복사해 커밋한다. `git add -f`로 백업 폴더를 강제로 추가하지 않는다.

## 3. 문제가 생겼을 때

| 상황 | 증상 | 조치 |
| --- | --- | --- |
| 20:10 전 실행 | 받기가 `거부: … 아직 끝나지 않았습니다` | 20:10 뒤 다시 실행 |
| 휴장일 지정 | `거부: … KRX 거래일이 아닙니다` | 날짜 확인 |
| 몇 종목 받기 실패 | 받기 `실패 N`, 보존 `누락 N`·`결과: failed`. 받은 종목은 이미 보존된다 | **실패 종목만** 새 스테이징에 다시 받아 **같은 명령으로 보존**한다(아래). 하루치 전체를 다시 받지 않는다(원천이 바뀌었으면 충돌이 나기 때문) |
| 그날 봉 없음 | 받기 `그날 봉 없음 N`(파일을 만들지 않음) | 거래정지·상장폐지인지 확인한다. 확인한 경우만 보존에 `--allow-missing 종목,종목`을 붙인다(결과 warning). 확인하지 못했으면 빈 파일을 만들지 말고 failed로 두고 보고한다 |
| 비정상 파일 | `비정상: raw/X.json: …`(형식 오류, 그날 봉 없음, 20:10 전 수집) | 그 파일은 복사하지 않는다. 실패 종목 재시도와 같은 방법으로 다시 받는다 |
| **충돌** | `기존 보존본과 SHA-256이 다른 파일 N개 … 이번 실행은 복사하지 않음`, `결과: failed` | **아무것도 지우거나 덮어쓰지 않는다.** 1) `--verify-only --dest <보존 폴더>`로 기존 보존본이 목록과 맞는지 본다. 2) 맞으면 기존 보존본은 정상이고 새 원본이 다른 것이다. 원천이 값을 바꿨을 수 있다. 새 원본은 별도 위치에 보존한다: `--dest-root backups/session_minute_alt/<시각>`. 3) 기존 보존본이 목록과 다르면(변조·손상) 즉시 작업을 멈추고 보고한다 |
| 복사 도중 중단(전원·Ctrl+C) | 일부만 복사됨 | **같은 명령을 다시 실행**한다. 확정된 파일은 SHA-256이 같아 "이미 보존됨"으로 건너뛴다. 남은 임시 파일(`.이름.partial`)은 최종 파일이 아니며 도구가 지우고 새로 쓴다 |
| 받기 도중 중단 | `fetch_log.json`의 `complete: false` | 받지 못한 종목만 새 스테이징에 받는다(아래) |
| 보존 위치 거부 | `거부: --dest-root에 이 도구가 만들지 않은 항목이 있습니다` 또는 `이 도구가 만든 보존 디렉터리가 아닙니다` | 보호 규칙이다. `backups/`나 51차 보존본을 보존 위치로 쓰지 말고 `backups/session_minute`을 쓴다 |

실패 종목만 다시 받아 보태기:

```bash
RETRY=backups/session_minute_staging/retry_${D}_$(date +%Y%m%dT%H%M).txt
printf "%s\n" 000100 000660 > $RETRY                                 # 받기 출력의 failed 종목
STAGE2=backups/session_minute_staging/${D}_$(date +%Y%m%dT%H%M)
$PY -m scripts.fetch_session_minute --date $D --tickers-file $RETRY --out $STAGE2
$PY -m scripts.preserve_session_minute --source $STAGE2 --dest-root $ROOT --date $D --expected-from $LIST
#  -> "발견 2, 이미 보존됨(이번 원본 밖) 348, 누락 0 … 결과: ok" 이면 그날 완료
$PY -m scripts.preserve_session_minute --verify-only --dest $ROOT/$D
```

종료 코드: 받기 0 = 전부 받음 / 1 = 실패·봉 없음 있음 / 2 = 거부. 보존 0 = ok·warning / 1 = failed / 2 = 거부.

## 4. 11-16 자료를 꼭 확보해야 하는 이유

- 51차 보존으로 11-05~11-13은 파일로 남았다. 다음으로 잃을 수 있는 첫 날이 **11-16(월)**이다.
- 원천 보관 창이 약 7거래일이라 11-16 분봉은 **11-24~25경** 원천에서 사라진다. 11-05는 11-14에 이미 사라졌다(51차 확인).
- 세션별 저장 테이블(`stock_price_session`)은 승인 전이다. 승인되면 보존 파일로 백필한다(51차 3.11절). 파일이 없으면 그날 정규장 OHLCV는 영영 만들 수 없다.

## 5. 52차 검증 결과(보고)

### 5.1 기존 파일 구조 조사

| 항목 | 실제 |
| --- | --- |
| 요청서의 `session_audit_2026-11-05_2026-11-12_snapshot_20261114/` | **없다.** 실제 스냅샷 이름은 `session_audit_2026-11-06_2026-11-13_snapshot_20261114/`(11-06~11-13)다. 이 문서는 실제 이름을 쓴다 |
| `session_audit_2026-11-05_2026-11-12/` | `README.md`, `raw/`(350), `session_audit_rows.csv`, `session_audit_summary.json` → 353개 165,649,984바이트(README 포함) |
| `session_audit_2026-11-06_2026-11-13_snapshot_20261114/` | `README.md`, `fetch_log.json`, `raw/`(350) → 352개 164,448,226바이트 |
| `raw/{ticker}.json` | 종목 하나의 분봉 목록, **여러 날짜가 한 파일에** 들어 있다. 필드 6개(`localDateTime` 14자리, 가격 4개, `accumulatedTradingVolume` = 그 분 거래량). 이름은 6자리 영숫자(`0009K0` 포함) |
| 종목 수 | 350(주식 300·ETF 50). 고정값으로 넣지 않았다. 예상 종목은 폴더의 파일 이름이나 목록 파일에서 읽는다 |
| 체크섬 목록 | `docs/session_audit_51/*.sha256`: `sha256sum` 형식(`<64자리>  <상대 경로>`, LF), README는 목록에 없음 |
| 메타데이터 | `docs/session_audit_51/metadata.json`, 범위 CSV(종목·날짜별 봉 수) |
| 수집 방식 | 50차 `scripts/audit_session_ohlcv.py --keep-raw`(DB 읽기 필요, 6거래일 범위), 51차 스크래치 스크립트(`fetch_minute`, DB 없음, 범위 요청). 둘 다 `json.dumps(응답, ensure_ascii=False)`로 썼다. 이번 받기 스크립트도 같은 형식이다 |

설계 결정:
- 보존은 **바이트 복사**다. 원본 파일을 날짜별로 쪼개면 원본과 사본의 SHA-256을 직접 비교할 수 없다. 그래서 파일을 바꾸지 않고, 대상 날짜의 봉이 모든 파일에 있는지는 내용 검사로 확인한다.
- 대상 밖 날짜가 함께 든 파일(51차 파일)은 그대로 보존하고 warning으로 알린다.
- 매일 운영은 받기 단계가 하루치만 받으므로 대상 밖 날짜가 없다.

### 5.2 실제 자료 실증(51차 보존본을 원본으로, 보존 위치는 스크래치)

51차 보존본은 **원본(읽기 전용)**으로만 썼다. 보존 결과는 세션 스크래치 `r52/session_minute_v2/`에 만들었다(저장소 `backend/backups/`에는 새 폴더를 만들지 않음).

| 시험 | 명령 요지 | 결과 |
| --- | --- | --- |
| A. 11-13 보존 | 스냅샷 → `--date 2026-11-13` | 분봉 350 + 출처 기록 2(`fetch_log.json`, `README.md`) = **352개 복사**, 분봉 164,432,469바이트. 예상 350·발견 350·누락 0. 복사 뒤 재검증 352/352. warning(파일에 11-06~11-12도 있음) |
| B. 같은 명령 재실행 | 그대로 | **복사 0, 이미 보존됨 352**, 충돌 0 |
| C. verify-only + 원본 대조 | `--verify-only --source 스냅샷` | 352/352 일치, 원본과도 같음, `ok` |
| 독립 검증 | `sha256sum -c MANIFEST.sha256` | 352줄 OK. 51차 커밋 목록과 분봉 350개 해시 모두 같음(다른 해시 0) |
| D. 기간 dry-run | `--start 2026-11-06 --end 2026-11-12 --dry-run` | 날짜별 파일 5일 × 350, 복사 예정 352. 실행 전후 보존 폴더 해시 동일, 새 폴더 없음 |
| E. 11-05 보존 | 원본 보존본 → `--date 2026-11-05` | 353개 복사(분봉 350 + rows·summary·README), 재검증 353/353, `sha256sum -c` 353줄 OK. 수집 시각 근거는 `session_audit_summary.json`(11-13 15:03 ≥ 11-05 20:10) |
| F. 51차 보존본 검증 | `--verify-only --manifest docs/session_audit_51/…sha256` | 원본 보존본 352/352, 스냅샷 351/351 일치, `ok`(README는 목록 밖 허용) |
| G. 충돌 | 스크래치 보존본 `raw/000660.json`에 1바이트 추가 → A 명령 재실행 | `충돌 1`, **복사 0**, `결과: failed`, 종료 코드 1. 변조된 파일의 SHA-256·수정 시각 그대로(덮어쓰지 않음). verify-only도 `mismatch`·`size_mismatch`로 실패 |
| 보호 규칙 | `--dest-root backups` / `--dest-root <51차 보존본>` | 둘 다 거부(종료 코드 2): "원본과 보존 위치가 겹칩니다" / "이 도구가 만들지 않은 항목이 있습니다: raw" |
| 받기 거부·dry-run | `--date 2026-11-16`(아직 20:10 전) / `2026-11-15`(일요일) / `2026-11-13 --dry-run` | 거부 / 거부 / 요청 없이 종료. 스테이징 폴더가 하나도 생기지 않음 |

### 5.3 SHA-256·누락 결과

| 대상 | 파일 수 | 분봉 바이트 | SHA-256 | 누락 종목 | 비정상 |
| --- | --- | --- | --- | --- | --- |
| 51차 원본 보존본(검증만) | 352 | 165,476,854 | 352/352 일치 | 0 | 0 |
| 51차 스냅샷(검증만) | 351 | 164,432,469 | 351/351 일치 | 0 | 0 |
| 실증 A(11-13) | 352 | 164,432,469 | 352/352, `sha256sum -c` OK | 0 | 0 |
| 실증 E(11-05) | 353 | 165,476,854 | 353/353, `sha256sum -c` OK | 0 | 0 |

### 5.4 단위 테스트(요청 10항목 대응)

| 요청 항목 | 테스트 |
| --- | --- |
| 1~3 정상 복사·SHA-256·개수·용량 | `test_normal_copy_matches_source_sha_count_bytes_and_mtime`(수정 시각 보존, `MANIFEST.sha256` 형식·LF 포함) |
| 4 같은 날짜 두 번 | `test_rerun_same_date_creates_nothing_and_overwrites_nothing` |
| 5 충돌 | `test_conflict_with_different_hash_never_overwrites_and_copies_nothing`(충돌이 있으면 새 종목도 복사하지 않음) |
| 6 원본 파일 누락 | `test_source_file_vanishing_during_copy_is_error_and_not_finalized`, `test_verify_only_reports_deleted_preserved_file` |
| 7 예상 종목 누락 | `test_missing_expected_ticker_fails_without_placeholder`(빈 파일을 만들지 않음), `test_allow_missing_…`, `test_unexpected_ticker_…`, `test_duplicate_tickers_…`, `test_case_duplicates` |
| 8 복사 도중 실패 | `test_failure_mid_copy_leaves_no_final_or_partial_file_and_rerun_recovers`, `test_corrupted_copy_is_not_finalized`, `test_interrupted_first_run_is_resumed_not_refused` |
| 9 dry-run 무변경 | `test_dry_run_changes_nothing`, `test_dry_run_on_existing_preservation_changes_nothing` |
| 10 verify-only 무변경 | `test_verify_only_is_read_only_and_detects_tamper`, `test_verify_only_with_external_manifest_like_round51` |
| 보호 규칙 | 51차형 폴더 거부, 다른 파일이 있는 dest-root 거부, 원본·보존 위치 겹침 거부, 휴장일·인자 오류 거부, `--json-out` 위치 제한 |
| 내용 검사 | 그날 봉 없는 파일, 형식 오류 4종, 20:10 전 수집, 대상 밖 날짜, 기간 이름·날짜별 수, 수집 기록 없을 때 파일 시각 |
| 재시도 | `test_retry_with_only_failed_tickers_completes_same_date`(실패 종목만 다시 받아 보탬) |
| 받기 | 20:10 전·휴장일 거부, 비어 있지 않은 출력 폴더 거부, dry-run 무요청, 받기→보존 종단(실패·봉 없음 종목은 파일 없음, 하루 범위 요청 파라미터), 20:10 전 수집 기록을 보존 단계가 막음 |

- 실행: `pytest tests/test_preserve_session_minute_52.py` 37 통과.
- 단위 테스트 전체(`-m "not slow and not integration"`): **6,035 통과**(51차 5,998 + 신규 37).
- 구현 중 테스트로 찾아 고친 결함 3개:
  1. 검증 단계가 지워진 보존 파일을 "누락 종목"으로 세지 못했다.
  2. 비정상 사유가 출력되지 않았다.
  3. 실패 종목만 다른 스테이징에서 다시 받으면 `fetch_log.json`끼리 충돌했다. → 출처 기록을 원본 폴더별 하위 폴더로 분리했다.

### 5.5 기존 보존본이 바뀌지 않았다는 증거

작업 시작 전(13:04)과 모든 실증 뒤에 두 폴더의 모든 파일을 비교했다(README 포함, SHA-256·크기·수정 시각).

| 폴더 | 전 | 후 | 비교 |
| --- | --- | --- | --- |
| `session_audit_2026-11-05_2026-11-12/` | 353 | 353 | 전부 같음 |
| `session_audit_2026-11-06_2026-11-13_snapshot_20261114/` | 352 | 352 | 전부 같음 |

커밋한 51차 체크섬 목록으로 `sha256sum -c`를 돌려도 작업 전·후 모두 통과한다. 도구는 이 폴더들을 원본·예상 종목 목록·검증 대상으로만 열었다(읽기 모드).

### 5.6 개발 DB가 바뀌지 않았다는 증거

- 두 스크립트는 DB에 연결하지 않는다(import 뒤 `sqlalchemy`·`app.db` 없음).
- 작업 끝에 READ ONLY 연결로 확인한 값은 51차 기록과 같다.

| 항목 | 값 |
| --- | --- |
| `stock_price` 행 수 / md5 | 251,455 / `b02b4d72f8c265c98591f3097564ddd4` |
| `market_index` 행 수 / md5 | 1,480 / `7ca607b4c6ee84f34613a6b54d01f212` |
| Alembic | `c7e2a9f4d158` |
| `users` / 격리 행 | 2 / 1 |
| `stock_price_session` 테이블 | 없음 |

### 5.7 Git 분리

- 커밋 대상: 스크립트 2개, 테스트 1개, 이 문서. 실증 결과·원본은 스크래치와 `backend/backups/`(Git 무시)에만 있다.
- `.gitignore`·`.gitattributes`는 바꾸지 않았다(`backend/backups/` 무시, `*.sha256 eol=lf` 그대로).
- 커밋 해시·CI 결과는 커밋 메시지와 최종 보고에 남긴다(이 문서는 커밋 전에 썼다).

### 5.8 하지 않은 것

DB 쓰기, 마이그레이션, 테이블 생성, 원천 재수집(받기 스크립트를 실제 원천에 실행하지 않음), NXT 데이터 생성, 기존 원본·백업 삭제·이동·덮어쓰기, 저장소 `backend/backups/`에 새 보존 폴더 만들기, 자동 실행 등록, 프런트엔드·Ollama·평가 세트 변경, main 병합.

## 6. 11-16 실제 운영을 위해 사용자가 할 일

1. **11-16(월) 20:10 KST 이후**(늦어도 11-19까지) 2.2절 명령 1)~5)를 실행한다. 인터넷 연결만 있으면 되고 DB·서버는 필요 없다.
2. 2.4절 판정표를 확인한다. 첫날이라 7번(하루 범위 요청이 그날 것만 돌려주는지)도 확인한다.
   - 그날 봉이 0개인 종목이 대량으로 나오면 하루 범위 요청이 원천에서 다르게 동작하는 것일 수 있다. 그때는 보존하지 말고 결과를 공유한다.
3. 이후 거래일(11-17·18·19·20·23 …)마다 `D`만 바꿔 같은 절차를 반복한다. 휴장일은 건너뛴다.
4. 수집 대상 종목이 바뀌면(`collect_universe` 실행 등) 목록 파일을 새로 만들어 `--tickers-file`·`--expected-file`로 넘긴다. 개발 DB에서 읽기만 해서 만든다.
5. 실패·충돌이 나면 3절 표대로 처리한다. 지우거나 덮어쓰지 않는다.
6. (선택) 주 1회 `docs/session_minute/<날짜>.sha256`을 커밋한다(2.6절).
7. 세션 테이블 승인 여부는 51차 3.13절 결정 사항으로 남아 있다. 이 보존은 그 결정 전까지의 보험이다.
