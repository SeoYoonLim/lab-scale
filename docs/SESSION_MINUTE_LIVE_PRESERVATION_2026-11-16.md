# 2026-11-16 분봉 원본 실제 수집·보존 (2026-11-17 실행)

**결론: 2026-11-16 하루치 분봉 원본을 350종목 모두 받아 보존했다. 52차 2.4절 정상 완료 판정 7개 항목을 모두 만족한다.**

- 52차 도구(`fetch_session_minute`·`preserve_session_minute`)를 실제 원천에 처음 돌린 기록이다.
- 첫 실행 추가 확인(52차 2.4 #7)도 통과했다. 하루 범위 요청이 그날 봉만 돌려준다.
- 54차 `--validate-only`: 350종목 모두 통과했고 거부는 0이다.
- 하지 않은 것:
  - 개발 DB 쓰기, 세션 표 생성·적재, 시험 DB 적재
  - 11-17~11-19 수집
  - 기존 보존본 변경

## 1. 사전 확인

| 항목 | 결과 |
| --- | --- |
| 브랜치·작업 트리 | `feature/backend`, HEAD = origin = `47c0226`, 작업 트리 깨끗함 |
| 52차 보고서 | `docs/SESSION_MINUTE_DATA_PRESERVATION_52.md` 2~3절 절차를 그대로 따랐다 |
| 59차 | 2026-11-15에는 대상일이 모두 미래라 수집 0건이었고, 보고서·커밋도 없었다. 이번이 그 후속 첫 실행이다 |
| 종목 목록 | `backend/backups/session_audit_2026-11-06_2026-11-13_snapshot_20261114/raw` 파일 **350개**(형식 위반 0). 도구 출력도 `종목 350개` |
| 기존 보존본 | 51·52차 두 세트 모두 `docs/session_audit_51/*.sha256`로 `sha256sum -c` 통과(352·351줄). 전후 비교용 지문도 기록했다(7절) |
| 보존 폴더 | `backups/session_minute`·`backups/session_minute_staging` 모두 없었다(첫 실행) |
| 11-16 일정 | KRX 거래일, 개장 09:00·마감 15:30. 세션 일정은 `default/data_verified` |
| 11-16 일정 창 | 정규 09:00~15:20, 종가 단일가 15:30(지연 창 15:31~15:39), 장후 15:40~15:59, 애프터마켓 16:00~19:59(주식만). `calendar_mismatches() == []` |

**CLI(실제 코드 확인)**
- `fetch_session_minute --date --tickers-from|--tickers-file --out [--sleep] [--dry-run]`
- `preserve_session_minute --source --dest-root --date --expected-from [--dry-run]`. 확인용은 `--verify-only --dest [--source]`
- `load_session_minute --database-url --source --date --validate-only [--report]`

## 2. 대상 날짜와 위치

| 항목 | 값 |
| --- | --- |
| 거래일 | **2026-11-16(월)** 하루 |
| 스테이징(원본) | `backend/backups/session_minute_staging/2026-11-16_20261117T1016/` |
| 최종 보존 | `backend/backups/session_minute/2026-11-16/` |
| 실행 기록 | `backend/backups/session_minute/PRESERVATION_LOG.tsv`(첫 줄) |
| 세션 검증 결과 | `backend/backups/session_validate_2026-11-16_20261117T1016.json`(보존 전용 폴더 밖) |
| 체크섬 목록(저장소) | `docs/session_minute/2026-11-16.sha256`(52차 2.6절 방식, 보존본 `MANIFEST.sha256`과 SHA-256 같음) |

스테이징 폴더 이름은 52차 문서와 같은 `<날짜>_<받은 시각 yyyyMMddTHHmm>` 형식이다. 시각은 KST다.
- 52차 문서의 `TS=$(date +%Y%m%dT%H%M)`(Git Bash)를 쓰지 않았다. 이 PC의 Git Bash가 UTC로 찍는 것을 63차에 확인했기 때문이다. 대신 PowerShell `Get-Date -Format yyyyMMddTHHmm`으로 KST를 썼다.
- 결과는 `20261117T1016`이다. 문서의 Git Bash 명령은 그대로 두었다. 고칠지는 9절에 적었다.

## 3. 수집 전 확인(dry-run)과 시간 제한

`fetch_session_minute --date 2026-11-16 ... --dry-run`:

```
대상 2026-11-16, 종목 350개, 출력 ...\session_minute_staging\2026-11-16_20261117T1016, 기준 시각 2026-11-17T10:16+09:00
dry-run: 요청하지 않고 끝냅니다
```

- 종료 코드 0이다. 실행 뒤 `backups/session_minute_staging`이 없었다. 폴더를 만들지 않았다.
- 코드상 dry-run은 `fetch()` 호출 전에 반환하므로 외부 요청이 없다.

**시간 제한 판단**
- `check_target` → `session_ohlcv.day_complete(d, now)`는 `d < now.date() or (d == now.date() and now.time() >= 20:10)`이다.
- 과거 거래일은 현재 시각과 관계없이 통과한다. 오늘 거래일만 20:10을 본다.
- 그래서 11-17 10:16에 11-16을 받는 것은 올바르게 허용됐다. 시스템 시각 변경이나 우회는 없었다.
- 59차(11-15)에 11-16을 넣으면 거부됐다는 기록과도 맞는다(그때는 미래 날짜였다).

## 4. 수집 결과

| 항목 | 값 |
| --- | --- |
| 실행 | 2026-11-17 10:17:02 ~ 10:20:59 KST(약 4분), 요청 간격 0.35초 |
| 요청 | 종목당 1회(`startDateTime` 202611160000, `endDateTime` 202611162359), 350회. 재시도 횟수는 로그에 따로 남지 않는다 |
| 종료 코드 / 출력 | **0** / `받음 350, 실패 0, 그날 봉 없음 0 / 350` |
| `fetch_log.json` | `complete: true`, `failed: []`, `no_bars: []`, **`other_date_bars: {}`** |
| 파일 | `raw/*.json` 350개 + `fetch_log.json` 1개. 빈 파일 0. 분봉 26,889,130바이트, 폴더 합계 26,910,608바이트 |
| 봉 수(종목당) | 최소 155(KODEX MSCI Korea TR 278540), 중앙값 463, 최대 639(삼성전자 005930) |

**첫 실행 필수 확인(52차 2.4 #7)**

| 확인 | 결과 |
| --- | --- |
| `other_date_bars` | 비어 있다. 350개 파일 전체를 다시 읽어도 `localDateTime`이 20261116이 아닌 행은 **0** |
| 첫 정규장 분봉 | 350종목 모두 첫 봉이 `20261116090000`. 09:00 전 봉 0 |
| 005930 | 첫 `20261116090000`, 마지막 `20261116195900`, 639봉, 오름차순 |
| 전체 마지막 봉 | `20261116195900`. 19:59 뒤 봉 0 |
| 형식 | 기존 보존본(11-06~11-13 스냅샷 `raw/005930.json`)과 같다. 응답 목록 그대로의 JSON 배열이고, 키는 `localDateTime`·`currentPrice`·`openPrice`·`highPrice`·`lowPrice`·`accumulatedTradingVolume` 6개다. 350개 파일의 키 구성이 한 가지다 |
| 대량 누락 | 없음(실패 0, 봉 없음 0) |

마지막 봉 시각 분포: 19:59 252종목, 19:55~19:58 29종목, 15:40대 33종목, 15:30 17종목 등.
- 15:30·15:40대에서 끝난 종목은 ETF(애프터마켓 없음)와 애프터마켓 체결이 없던 주식 2종목이다(6절).

## 5. 보존 결과

| 단계 | 출력 | 종료 코드 |
| --- | --- | ---: |
| 1. `--dry-run` | `날짜별 파일 {'2026-11-16': 350}` · `예상 종목 350, 발견 350, 이미 보존됨 0, 누락 0, 예상 밖 0, 중복 0, 비정상 0` · `복사 예정 351, 충돌 0` · `결과: ok`. 실행 뒤 `backups/session_minute` 없음(복사 안 함) | 0 |
| 2. 보존 | `복사 351, 이미 보존됨 0, 충돌 0, 비정상 제외 0, 실제 복사 351, 복사 실패 0` · `복사 뒤 재검증: ok (일치 351/351)` · `결과: ok`(warning 사유 없음) | 0 |
| 3. `--verify-only --dest ... --source ...` | `파일 351개 중 일치 351, 불일치 0, 없음 0, 목록 밖 0, 임시 파일 0, 분봉 파일 350개, 목록 파일 합계 26,910,608바이트` · `결과: ok` | 0 |
| 4. `sha256sum -c --quiet MANIFEST.sha256` | `sha256sum OK`(351줄, LF만) | 0 |

`PRESERVATION_LOG.tsv` 기록: `2026-11-17T10:21:31+09:00 preserve 2026-11-16 ok 350 350 351 0 0 0 0 26889130 <스테이징 경로>`

**원본과 사본 직접 대조**(도구 밖에서 별도 스크립트로 계산)

| 항목 | 원본(스테이징) | 사본(보존) | 결과 |
| --- | --- | --- | --- |
| 파일 수 | 351(`raw/` 350 + `fetch_log.json`) | 같은 351개. 그 밖에 도구 기록 3개(`MANIFEST.sha256`, `preserve_manifest.json`, `preserve_log.jsonl`)가 있어 폴더 합계 354 | 대응 351/351 |
| 경로 | `raw/{ticker}.json`, `fetch_log.json` | `raw/{ticker}.json` 같은 이름. `fetch_log.json`은 `source_meta/2026-11-16_20261117T1016__d29c0a76/fetch_log.json` | 빠진 파일 0, 남는 분봉 파일 0 |
| 총용량 | 26,910,608바이트 | 같은 파일 26,910,608바이트(폴더 전체 27,086,215바이트) | 같음 |
| SHA-256 | — | — | 351개 모두 같음(차이 0) |
| 수정 시각 | — | — | 351개 모두 `st_mtime_ns`까지 같음(차이 0) |

- `MANIFEST.sha256`의 SHA-256: `04f3f0bb4d4993943e79f8eeb416c7f4bd953a73b348b9eba2202648aabb6222`(저장소 사본 `docs/session_minute/2026-11-16.sha256`도 같음)
- 스테이징 원본은 지우지 않았다(52차 2.5절 원칙).

## 6. 세션 검증(54차 `--validate-only`)

```
python -m scripts.load_session_minute --database-url <127.0.0.1:15432/ai_investment_session_pipeline54> \
    --source backups/session_minute/2026-11-16 --date 2026-11-16 --validate-only --report backups/session_validate_2026-11-16_20261117T1016.json
```

**시험 DB를 쓴 이유**
- 이 모드도 종목 구분(`company`)을 읽으려고 DB 주소를 받는다.
- 기존 시험 DB `ai_investment_session_pipeline54`에 `SET TRANSACTION READ ONLY`로 붙어 `company`만 읽는다(코드 확인).
- 개발 DB는 `--allow-dev-db` 없이는 도구가 거부하고, 그 옵션은 쓰지 않았다.
- 실행 뒤에도 그 DB의 `stock_price_session`은 8,814행 그대로다.

| 항목 | 결과 |
| --- | --- |
| 출력 | `파일 350, 검증 통과 350일, … 거부 0, 그날 없음 0` · `표시 {'stock:late_closing_auction': 2}` · `결과: ok`(종료 0) |
| 원본 체크섬 사전 확인 | `MANIFEST.sha256` 351/351 ok |
| 일정·규칙 | 일정 파일 버전 `2026-11-14.1`, 규칙 버전 1, 11-16 `default/data_verified` |
| 수집 시각 기준 | `source_meta/.../fetch_log.json`(종목별 받은 시각) |

**세션별 분류 결과(종목 수)**

| 세션 | 주식(300) | ETF(50) | 첫 봉 | 마지막 봉 |
| --- | ---: | ---: | --- | --- |
| `regular` | 300 | 50 | 모두 09:00 | 15:19 346, 15:18 2, 15:17 2 |
| `closing_auction` | 300 | 50 | 15:30 348, **15:32 2** | 1봉(단일가) |
| `post_close` | 229 | 33 | 15:40 223, 15:41~15:4x 나머지 | 15:40~15:59 |
| `after_market` | 298 | 0(ETF는 대상 아님) | 16:00 121, 16:01~ 나머지 | 19:59 252, 19:55~19:58 등 |

세션 조합:
- 주식: 네 세션 모두 227, 장후 없음 71, 애프터마켓 없음 2
- ETF: 장후 있음 33, 장후 없음 17

**분류 거부: 0건.** 분류할 수 없는 종목·거래일은 없었다.

정상으로 분류됐지만 기록해 둘 것:
- **`late_closing_auction` 2건**: DB손해보험(005830), 삼성에피스홀딩스(0126Z0). 종가 단일가가 15:30이 아니라 15:32 봉으로 왔다. 지연 창(15:31~15:39) 안이라 `closing_auction`으로 분류하고 표시만 남겼다. 51차에 확인한 15:32 종가 단일가 사례와 같은 유형이다.
- **애프터마켓 없음 주식 2종목**: 메지온(140410, 마지막 봉 15:49), NHN(181710, 15:55). 16:00 뒤 체결 봉이 없었다.
  - 메지온은 11-06~11-13 스냅샷에서도 애프터마켓 체결이 하루도 없었다. NHN은 그 기간 중 4일 있었다.
  - 원천이 체결 없는 분에 봉을 주지 않는 방식이므로 세션 없음으로 처리했다(거부 아님).
- **장후 없음**: 주식 71·ETF 17종목. 15:40~15:59 체결이 없었다.
- **정규장 봉 수 380 미만**: 정규장 봉이 380개(09:00~15:19 매분)보다 적은 종목이 있다. 체결이 없는 분은 봉이 없기 때문이다. 최소는 KODEX MSCI Korea TR 154봉이다. 거래량 0인 세션은 없다.

시험 DB 적재(`--dry-run`·`--load`)는 지시대로 하지 않았다.

## 7. 기존 보존본 불변 확인

| 보존본 | 파일 | 바이트 | 지문(경로·크기·mtime·SHA-256), 전 = 후 | `sha256sum -c` 전·후 |
| --- | ---: | ---: | --- | --- |
| `backups/session_audit_2026-11-05_2026-11-12`(51차) | 353 | 165,649,984 | `b8db04147fc19e4aa8c4ab53e3993b6bb25f8352e709641e948f71951b43b844` | OK · OK |
| `backups/session_audit_2026-11-06_2026-11-13_snapshot_20261114`(51차 스냅샷·52차 목록 기준) | 352 | 164,448,226 | `5ebc738c9dcac0a6748169de1a5cb66195359e3732056bfa420fa9d936e6ae12` | OK · OK |

- 지문은 폴더 안 모든 파일의 (상대 경로, 크기, `st_mtime_ns`, SHA-256)을 정렬해 만든 SHA-256이다. 폴더 자체의 수정 시각도 전후 같다.
- 종목 목록 폴더는 이름만 읽었다.

## 8. 개발 DB 변경 없음 확인

| 항목 | 결과 |
| --- | --- |
| 방법 | 작업 전(10:16)·후 `ai_investment` 23개 표의 행 수와 md5(전 행 텍스트), `pg_stat_user_tables` 누적 INSERT·UPDATE·DELETE를 비교했다(읽기 전용 연결) |
| 23개 표 행 수·md5 | 모두 같음 |
| `pg_stat_user_tables` | 변화 없음(모든 표) |
| Alembic | `c7e2a9f4d158` 그대로 |
| `stock_price_session` | 개발 DB에 없음(전·후) |
| 시험 DB `ai_investment_session_pipeline54` | `stock_price_session` 8,814행 그대로(READ ONLY 검증만) |

받기·보존 도구는 DB를 쓰지 않는다(코드 확인). 마이그레이션, Ollama 설정 변경, 백엔드 재시작, 프런트엔드 변경은 하지 않았다.
- 백엔드는 11-17 09:02 재부팅 뒤 내려가 있는 상태 그대로다(63차 보고서 1절).

Git: 원본·보존본·검증 JSON은 `backend/backups/` 아래라 `.gitignore` 11행으로 빠진다(`git check-ignore`로 확인). 저장소에는 이 보고서와 체크섬 목록(`docs/session_minute/2026-11-16.sha256`, 약 30KB 텍스트)만 올린다.

## 9. 남은 일

1. **11-17~11-19는 이번에 하지 않았다.** 각 거래일 20:10 KST 뒤에 같은 절차로 하루씩 받는다.
   - 원천은 최근 약 7거래일만 준다. 11-16분은 11-24~25경 사라지므로 이번 확보로 그 위험은 없어졌다.
   - 11-19(수능일, 일정 `announced`)는 54차 지시대로 실제 분봉을 확인한 뒤 data_verified 전환을 따로 정한다.
2. 52차 문서 2.2절의 `TS=$(date +%Y%m%dT%H%M)`는 이 PC의 Git Bash에서 UTC로 찍힌다. KST 폴더 이름을 원하면 PowerShell `Get-Date`를 쓰도록 문서를 고칠지 정해야 한다(이번에는 문서를 고치지 않았다).
3. 시험 DB 적재(54차 `--dry-run`·`--load`)와 개발 DB 반영은 별도 승인 사항이다.
