# 68차 분봉 자동 수집·보존: Cloudflare R2 제거, GitHub Actions 아티팩트 사용 (2026-11-17)

**결론**: Cloudflare 계정·R2 버킷·API 토큰·추가 Secrets 없이, GitHub Actions만으로 거래일 분봉 350종목을 받아 날짜별 아티팩트로 보존하는 구조를 만들었다.

- 구현·테스트를 마쳤고, `feature/backend` push로 GitHub-hosted runner에서 실제로 돌려 확인했다(12절).
- **예약 실행은 아직 동작하지 않는다.** 예약은 workflow가 기본 브랜치(main)에 있어야 돈다. main 병합은 사용자 승인 사항이다(14절).
- GitHub Actions 아티팩트는 **임시 보존 수단이다.** 보관 기간(30일) 뒤 자동으로 지워진다. 영구 보존이 필요하면 내려받아 따로 보관해야 한다(13절).

| 구분 | 상태 |
| --- | --- |
| 구현 | 완료 |
| 테스트(가짜 API·가짜 원천) | 완료(15개, 9절) |
| 실제 운영 검증(GitHub runner, 실원천, 실아티팩트) | 12절 결과 |
| 예약 실행 | 미활성(main 반영 필요) |

## 1. 67차(Cloudflare R2) 대비 변경

| 항목 | 67차 | 68차 |
| --- | --- | --- |
| 보존 위치 | Cloudflare R2 비공개 버킷(영구) | 이 저장소의 GitHub Actions 아티팩트 `session-minute-YYYY-MM-DD`(보관 30일) |
| 필요한 설정 | Cloudflare 계정, 버킷, R2 토큰, Secrets 4개 | 없음. workflow가 자동 발급하는 `GITHUB_TOKEN`만 쓴다 |
| 의존성 | boto3(`requirements-cloud.txt`) | 기존 `requirements.txt`만 |
| 수집·검증 | 52차 fetch/preserve/verify + 54차 Loader | **같다.** 67차 실행기(`run_cloud_session_preservation`)를 `--store local`로 그대로 쓴다 |
| workflow | `session-minute-cloud.yml`(삭제) | `session-minute-actions.yml`(새) |

**67차 파일 처리**
- 지운 것: `.github/workflows/session-minute-cloud.yml`. Cloudflare 전용 설정이다.
- 남긴 것(보관, 어떤 workflow도 설치·실행하지 않음)
  - `R2Store` 클래스(`--store r2`)
  - `backend/requirements-cloud.txt`
  - `backend/tests_cloud/test_r2_store_67.py`
  - 각 파일 머리에 "68차부터 미사용"이라고 적었다.
- 계속 쓰는 것
  - 67차 실행기의 수집·보존·세션 검증·봉인 엔진
  - 그 단위 테스트 26개(기본 CI `backend-unit`)
- `docs/SESSION_MINUTE_CLOUD_AUTOMATION_67.md`는 역사 기록으로 남긴다. 머리에 중단 안내를 달았다.

## 2. 최종 구조

```
GitHub Actions (ubuntu-latest, 매 평일 20:20·23:20 KST, 수동 실행, feature/backend push)
 └ python -m scripts.run_actions_minute_preservation run
     1. KRX 달력(app/krx_calendar): 오늘이 휴장일·주말이면 SKIP(아티팩트 조회·원천 요청 없음)
     2. 대상일 = 분봉이 끝난 최근 거래일(20:10 KST 뒤면 오늘)부터 최근 7거래일
     3. 날짜마다 아티팩트 session-minute-<날짜>를 REST API로 찾음(만료 제외)
          → 내려받아 압축 해제 → 검증(봉인·파일별 크기·SHA-256·350종목·52차 verify)
          → 정상: 실행 폴더에 채움(=이미 보존, 다시 받지 않음) / 손상: 채우지 않음(지우지도 않음)
        session-minute-status-<실행 id> 안의 <날짜>.json = 복구 불가 표시
     4. 67차 엔진을 --store local로 실행: 채워진 날짜는 already_complete, 나머지는 원천에서 수집
          (3종목 탐침 → 전체 → 실패 종목 재수집 2회 → 52차 보존·verify → 54차 세션 검증 → 봉인 manifest.json 마지막)
     5. 이번 실행에서 새로 봉인된 날짜만 업로드 칸(slot1~7)에 옮김. 일부 종목만 받은 날짜는 올리지 않음
     6. 용량 점검: 저장소 전체 아티팩트 사용량(API) + 이번 업로드 압축 크기(실측) vs 500MB
 └ actions/upload-artifact@v4 × 칸 수(빈 칸은 건너뜀), retention-days 30, 압축 수준 6
 └ python -m scripts.run_actions_minute_preservation verify
     이번 실행의 아티팩트를 API로 다시 내려받아 SHA-256을 대조하고 최종 성공·실패를 정함
 └ 실행 기록 아티팩트 session-minute-run-<실행 id>(run_report.json: 상태·건수·용량, 원본 없음)
```

- DB·Ollama·사용자 PC 경로를 쓰지 않는다.
- 52·54차 검증 기준은 바꾸지 않았다.

## 3. 실행 시각

```yaml
schedule:
  - cron: "20 20 * * 1-5"   # 20:20 KST = 11:20 UTC
    timezone: "Asia/Seoul"
  - cron: "20 23 * * 1-5"   # 23:20 KST = 14:20 UTC(예비)
    timezone: "Asia/Seoul"
```

**시간대 표기**
- GitHub는 2026-03-19부터 예약의 `timezone`(IANA)을 지원한다.
- 그래서 cron 값은 KST 그대로이고, 주석에 UTC 환산을 적었다.
- `timezone`을 빼고 UTC로만 쓰려면 `"20 11 * * 1-5"`, `"20 14 * * 1-5"`다.

**예약 시각 보장**
- 예약은 지연·누락될 수 있다. 정각에 실행된다고 보장하지 않는다.
- 23:20 예비 실행이 있고, 다음 거래일 실행이 최근 7거래일을 다시 확인한다.
- 수동 실행 입력
  - `date`: 그 거래일만
  - `lookback`: 1~7
  - `dry_run`: 기존 아티팩트 상태만 확인

## 4. 아티팩트 저장 방식

| 이름 | 내용 | 언제 |
| --- | --- | --- |
| `session-minute-YYYY-MM-DD` | 그날 보존 폴더 전체(아래) | 그날 350종목 수집·검증·봉인이 끝난 실행에서 **한 번** |
| `session-minute-unsealed-YYYY-MM-DD` | 350종목 원본은 다 받았지만 54차 세션 검증이 거부한 날의 보존 폴더(**봉인 없음, 최종본 아님**, 거부 사유는 `session_validate.json`) | 그 거부를 처음 확인한 실행에서 한 번 |
| `session-minute-status-<실행 id>` | `<날짜>.json`(원천이 그날 봉을 주지 않음 = 복구 불가) | 복구 불가를 처음 확인한 실행 |
| `session-minute-run-<실행 id>` | `run_report.json`(상태·건수·용량·업로드 재검증) | 매 실행 |

**날짜 아티팩트 내용**(52차 보존 형식 그대로, 원본 바이트 변경 없음)

```text
raw/{ticker}.json × 350                  네이버 분봉 응답 그대로
source_meta/<원본 id>/fetch_log.json      수집 기록
MANIFEST.sha256                          sha256sum -c 입력
preserve_manifest.json                   파일별 크기·SHA-256·날짜별 봉 수
preserve_log.jsonl
session_validate.json                    54차 세션 검증 요약(350종목 valid)
manifest.json                            봉인: 거래일, 시각(KST·UTC), 종목별 행 수·크기·SHA-256, 파일 수·총 용량, 출처, code_version(GITHUB_SHA), 세션 검증, status complete
```

**저장 원칙(지시서 4절)과 구현**

| # | 원칙 | 구현 |
| --- | --- | --- |
| 1 | 매일 통째로 다시 올리지 않음 | 날짜마다 아티팩트 하나. 새로 봉인된 날짜만 올린다. 누적본을 만들지 않는다 |
| 2 | 먼저 존재 확인 | `GET /repos/{repo}/actions/artifacts?name=session-minute-<날짜>` |
| 3 | 정상이면 중복 업로드 안 함 | 내려받아 검증 통과 → `already_complete`, 칸 비움 |
| 4 | 없거나 손상일 때만 복구 | 만료·없음·검증 실패 → 원천에서 다시 수집(원천 조회 기간 안에서만) |
| 5 | 350종목 검증 전에는 최종 업로드 안 함 | 봉인(`manifest.json`)은 350종목·세션 검증·SHA-256이 모두 통과해야 생긴다. 봉인 없는 날짜는 칸에 넣지 않는다 |
| 6 | 일부 종목만 = 완료 아님 | `incomplete_tickers`·`session_validation_failed`면 실패로 끝난다. 세션 검증만 거부한 날은 원본이 사라지지 않게 `session-minute-unsealed-<날짜>`로 남기되 최종본 이름을 쓰지 않는다. 그 뒤 실행은 다시 요청하지 않고 `rejected_known`으로 적는다 |
| 7 | 기존 정상본을 지우거나 덮어쓰지 않음 | 지우기 API를 부르지 않는다(`actions: write` 권한 없음). 같은 이름이라도 실행마다 별도 아티팩트다. 정상본이 여럿이면 가장 먼저 만든 것을 기준으로 삼는다 |

## 5. 이전 아티팩트 복구 방식

- 권한
  - workflow `permissions: contents: read, actions: read`
  - 토큰은 `${{ github.token }}`(자동 발급)
  - PAT·추가 Secrets는 쓰지 않는다
- 조회: REST API로 이름 조회. 만료(`expired: true`)는 없는 것으로 본다.
- 내려받기
  - `GET /actions/artifacts/{id}/zip`(302 → 저장소 blob)
  - 압축을 풀 때 폴더 밖 경로는 거부한다
- 검증(`verify_day_dir`)
  - 봉인이 있고 `status complete`, 날짜가 같은지
  - 봉인에 적힌 모든 파일의 크기·SHA-256
  - 종목 수 350
  - 52차 `verify`(MANIFEST.sha256 전체 재해시, 예상 종목 누락)
- 기록: 날짜별로 찾은 아티팩트 id·크기·생성·만료 시각·`digest`를 `run_report.json`에 남긴다.

## 6. 최근 7거래일 복구 범위

- 원천(`api.stock.naver.com` 분봉)은 최근 7거래일만 준다(50차). 실행기는 그보다 앞을 요청하지 않는다(`--lookback` 1~7).
- 거래일 계산은 기존 KRX 달력이다. 평일 계산으로 대체하지 않았다. 달력 범위 밖(2027년 1월 첫 거래일부터)은 설정 오류로 멈추므로, 12월에 달력을 갱신해야 한다(58차 절차).
- 아티팩트가 없고 원천에도 그날 봉이 없으면(3종목 탐침 모두 0봉) 복구 불가로 표시한다. 그 실행은 실패로 알린다. 이후 실행은 표시를 보고 다시 요청하지 않는다.
- 오래된 날짜가 실패해도 다음 날짜로 넘어간다. 가장 최근 날짜는 항상 처리한다(67차 시간 예산 규칙).

**보관 기간과 연휴**
- 보관 30일은 7거래일보다 길다(긴 연휴로 7거래일이 약 2주를 넘어도 남는다).
- 아티팩트가 만료돼도 그 날짜가 아직 최근 7거래일 안이면 다시 수집된다.
- 7거래일을 넘긴 뒤 만료된 날짜는 **어디서도 복구할 수 없다.** 30일 안에 내려받아 따로 보관해야 한다.

## 7. 용량

**실측(로컬)**
- 2026-11-16 보존본: 원본 27.09MB, 354파일
- zip 압축 수준 6(upload-artifact 기본): **1.96MB**, 수준 9: 1.71MB
- GitHub 실측은 12절이다.

**500MB 한도 대비**

| 보관 | 날짜 수(거래일, 대략) | 예상 사용량 | 비율 |
| --- | --- | --- | --- |
| 14일 | 약 10 | 약 20MB | 4% |
| **30일(채택)** | 약 21 | 약 42MB | 8% |
| 90일(최대) | 약 62 | 약 122MB | 24% |

- 저장소의 다른 아티팩트(CI `eval-offline-results`, 30일)는 작성 시점 3.3MB였다(144개).
- 보관 기간
  - 14일로도 7거래일은 지킨다.
  - 30일을 고른 이유는 사용자가 내려받아 둘 여유다. 용량은 한도의 1/10 아래다.
  - 바꾸려면 workflow의 `ARTIFACT_RETENTION_DAYS`를 고친다(저장소 설정의 최대값 이하).
- 매 실행 기록
  - 저장소 전체 사용량(`used_bytes`), session-minute 사용량, 이번 업로드 압축 크기(날짜별), 예상 비율, 판정
  - 위치: `run_report.json`의 `storage`
- 경고·중단 기준
  - 사용량 + 이번 업로드가 한도의 60%를 넘으면 `warning`(올린다)
  - 80%를 넘으면 `stopped_over_limit`. 올리지 않고 실패로 끝난다.
  - 기존 아티팩트를 지워서 자리를 만들지 않는다. 사용자가 판단한다.
- 공식 문서(GitHub 요금 문서) 확인 결과
  - GitHub Free 아티팩트 저장은 500MB이고 GitHub Packages와 공유한다.
  - 저장 사용량은 시간 단위(GB-시간)로 쌓인다.
  - 결제 수단이 없으면 한도 초과 뒤 사용이 막힌다.
  - 공개 저장소의 아티팩트 저장이 무료인지는 문서에 명시되어 있지 않아 500MB를 한도로 잡았다.

## 8. GitHub Actions 사용량

- 공개 저장소의 표준 GitHub-hosted runner 실행 분은 무료다(GitHub 요금 문서).
- 실행 시간(12절 실측 기준)
  - 첫 실행(7거래일 수집): 약 N분
  - 이미 보존된 날짜만 있는 실행: 확인·내려받기만 해서 약 1~2분
- 하루 2회 × 월 21거래일 ≈ 42회. 대부분은 예비 실행이 확인만 하고 끝난다.
- 원천 호출: 거래일당 약 350회(0.35초 간격). 차단(401·403·429)이 3회 나오면 그 실행의 원천 요청을 멈춘다. 우회는 하지 않는다.
- 공개 저장소는 60일 동안 저장소 활동이 없으면 예약 workflow가 자동으로 꺼진다(GitHub 경고 메일). Actions 화면에서 다시 켠다.

## 9. 테스트

`backend/tests/test_actions_minute_preservation_68.py` 15개는 모두 통과했다. 가짜 GitHub API와 가짜 원천을 쓰고, 외부 요청은 없다.

| 지시서 9절 | 테스트 |
| --- | --- |
| 1 52차 수집 회귀 | 기존 `test_preserve_session_minute_52.py` 전부 통과. 67차 테스트 26개 통과 |
| 2 54차 세션 검증 회귀 | `test_session_bars_53.py`·`test_session_schedule_54.py` 통과. 67차 `test_session_validation_matches_direct_54_classification` |
| 3 업로드 | `test_new_day_is_sealed_uploaded_and_reverified`(칸 1에만 그날, 350파일, 봉인) |
| 4 이전 실행 아티팩트 조회 | `test_existing_valid_artifact_is_downloaded_verified_and_not_reuploaded` |
| 5 내려받기·압축 해제 | 같은 테스트, `test_unzip_rejects_paths_outside_the_folder` |
| 6 SHA-256 일치 | 업로드 재검증(`same_as_local`), `test_verify_detects_a_changed_upload` |
| 7 중복 업로드 방지 | #4: 원천 요청 0, 새 아티팩트 0 |
| 8 일부 종목 실패 시 차단 | `test_partial_day_is_not_uploaded_as_final_artifact` |
| 9 누락 날짜 복구 | `test_missing_days_in_window_are_recovered_and_expired_one_marked`(두 번째 실행: 정상본 2개 내려받아 건너뜀, 복구 불가 1개 표시 인식) |
| 10 만료·용량 초과 | `test_expired_artifact_counts_as_missing`, `test_storage_over_stop_threshold_uploads_nothing_and_deletes_nothing`, `test_storage_warning_still_uploads` |
| 11 350종목 실제 실행 | 12절(GitHub runner 실측) |
| 그 밖 | 손상본 재수집과 기존본 유지, 휴장일 SKIP(API·원천 0), 토큰 가리기, 압축 크기 측정, 54차 거부일의 비봉인 보존과 재요청 방지(`test_rejected_day_is_kept_as_unsealed_artifact_once_and_not_refetched`) |

전체 CI(`ci.yml`)도 돈다(12절).

## 10. 공개 저장소와 데이터 공개 범위

이 저장소는 공개(public)다. 공개 저장소의 Actions 아티팩트는 GitHub에 로그인한 누구나 내려받을 수 있다.

- 분봉 원본(비공식 원천 응답)이 사실상 공개된다. 67차는 이 이유로 원본을 아티팩트에 올리지 않고 비공개 R2에 두었다.
- 지시서가 아티팩트 방식을 정했으므로 그대로 구현했다. **원천 이용 조건상 문제가 되는지 사용자가 판단해야 한다.**
- 대안
  - 저장소를 비공개로 바꾸면 아티팩트도 비공개가 된다. 대신 실행 분이 무료 2,000분/월에서 차감된다.
  - 다른 대안은 67차 R2 방식이다.

## 11. 기존 시스템 보호

- 개발 DB(`ai_investment`), Ollama, 백엔드·프런트엔드 코드, API, `.env`: 이번 작업이 건드리지 않았다.
  - workflow는 GitHub runner에서 돌고 DB 주소가 없다.
  - 67차 테스트가 DB·Ollama 모듈을 import하지 않는 것을 확인했다.
- 기존 분봉 원본(`backend/backups/session_minute/2026-11-16`): 압축 크기 측정에서 읽기만 했다. `MANIFEST.sha256` 해시(앞 16자리 `04f3f0bb4d499394`)는 측정 전후로 같다.
- 350종목 목록(`app/data/session_minute_universe.csv`)은 그대로다.

## 12. 실제 운영 검증(GitHub-hosted runner)

### 12.1 첫 실행: `eda8a3c` push, run 44080564934(2026-11-17 21:09~21:44 KST, 34분)

- 대상: 2026-11-09~11-17(7거래일). 기존 아티팩트는 없었다(전부 `expired_or_missing`).
- 수집 단계: 33분 34초(12:10:21~12:43:55 UTC). 7일 × 350종목 = 원천 약 2,450회.
  - 차단·제한 응답(401·403·429)은 0이었다.
  - **11-09(7번째 거래일)도 원천이 줬다.**
- 결과

| 거래일 | 수집·검증 | 아티팩트(실제 크기, 압축) | 업로드 재검증(API로 다시 받아 SHA-256) |
| --- | --- | --- | --- |
| 2026-11-09 | completed(350/350, 세션 검증 통과, 봉인) | `session-minute-2026-11-09` 2,234,758B | 통과 |
| 2026-11-10 | **session_validation_failed** | 올리지 않음(최종본 금지) | - |
| 2026-11-11 | completed | 2,059,104B | 통과 |
| 2026-11-12 | completed | 2,026,395B | 통과 |
| 2026-11-13 | completed | 2,003,570B | 통과 |
| 2026-11-16 | completed | 1,997,312B | 통과 |
| 2026-11-17 | completed | 1,996,730B | 통과 |

- 아티팩트 6개 합계 12,317,869B(약 12.3MB). 500MB 대비 2.5%다. 만료는 2026-12-17(30일)이다.
- 2026-11-10 거부 원인
  - KODEX 200미국채혼합50(284430)의 10:18 분봉 거래량이 **-8**이었다(원천 응답 그대로).
  - 54차 규칙(`bad_values`)이 그날 그 종목을 거부한다. 나머지 349종목은 통과했다.
  - 로컬 51차 보존본(같은 날 원본)으로 같은 결과를 재현했다. 규칙은 완화하지 않았다.
- 실행은 설계대로 실패로 끝났다(11-10 미완료).
- 이 실행으로 드러난 보완점
  - 원천 이상은 다시 받아도 같다. 그런데 매 실행 350회를 다시 요청하고 실패하며, 그날 원본은 어디에도 남지 않았다.
  - → `238d2e1`에서 세션 검증만 거부한 날을 비봉인 아티팩트로 한 번 남기고, 다시 요청하지 않게 했다(4절).
- 저장소 전체 사용량(실행 전): API로 측정해 `run_report.json`에 남겼다. 작성 시점 공개 목록 기준 약 3.3MB(CI 평가 결과)였다.

### 12.2 두 번째 실행: `238d2e1` push, run 44088078362(21:58~22:04 KST, 6분 34초)

| 거래일 | 기존 아티팩트 | 이번 실행 | 새 업로드 |
| --- | --- | --- | --- |
| 11-09, 11-11, 11-12, 11-13, 11-16, 11-17 | `preserved`: 12.1의 아티팩트를 API로 내려받아 압축을 풀고 봉인·파일별 SHA-256·350종목·52차 verify 통과 | `already_complete`(원천 요청 0) | **없음**(중복 업로드 방지) |
| 11-10 | 없음 | 다시 수집 → 같은 거부(`session_validation_failed`) | `session-minute-unsealed-2026-11-10` 1,973,842B, 재검증 통과 |

- 수집 단계 5분 58초. 아티팩트 6개 내려받기·검증과 하루 350종목 수집을 포함한다.
- 실행은 실패로 끝났다. 11-10 거부를 이 버전에서 처음 확인했기 때문이며, 설계대로다(알림 1회).

### 12.3 세 번째 실행: 보고서 커밋 push

(결과를 채운다)

## 13. 사용자가 해야 할 일

1. **10절 판단**: 공개 저장소에 원본을 아티팩트로 두어도 되는지 정한다.
2. **main 반영 승인**: `feature/backend` → main 병합(PR)
   - 예약 실행은 이 workflow가 main에 있어야 돈다.
   - 수동 실행(Actions → "Session minute artifacts" → Run workflow)도 main 반영 뒤 화면에 나타난다.
3. 병합 뒤 Actions 화면에서 workflow가 켜져 있는지 확인한다.
   - 첫 예약 실행 뒤 요약표와 아티팩트 목록에 `session-minute-<날짜>`가 생겼는지 본다.
4. **영구 보관이 필요하면** 30일 안에 아티팩트를 내려받아 둔다.
   - 저장소 Actions 화면 → 실행 → Artifacts
   - 받은 zip은 `preserve_session_minute --verify-only --dest <푼 폴더>`로 검증할 수 있다.
5. 12월 중순 KRX 공지로 2027년 달력을 갱신한다(58차).
6. R2(67차) 설정은 하지 않아도 된다.

## 14. 알려진 제한

- 아티팩트는 임시 보존이다(30일 뒤 자동 삭제, 최대 90일).
- 예약은 main에서만 돌고, 지연·누락될 수 있다. 공개 저장소는 60일 무활동이면 예약이 꺼진다.
- 원천은 비공식이고 최근 7거래일만 준다. GitHub runner(해외 IP) 접근은 12절 실측에서 확인했지만, 원천이 앞으로 막을 수 있다. 막히면 우회하지 않고 실패로 알린다.
- 한 실행에 올릴 수 있는 날짜는 7개다(칸 7개 = 최근 7거래일).
- 같은 날짜의 아티팩트가 여러 개 생길 수 있다(손상본을 다시 수집한 경우). 지우지 않으므로 사용자가 정리한다.
- 업로드가 끝난 아티팩트는 공개 저장소에서 로그인 사용자에게 보인다(10절).
