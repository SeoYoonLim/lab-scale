# 프런트엔드 PR #17~#19 동기화 검토 (2026-11-18)

대상: `merge/final-release-20261118`(worktree `scratchpad/merge-wt`). `feature/backend`·`main`은 바꾸지 않았다. DB 쓰기 없음.

## 1. 원격 main 최신 HEAD

`git fetch origin --prune` 뒤 `origin/main` = `5d0dc2350567f4167f2e6a2433365e9dad86ce22`(Merge pull request #19).

## 2. PR 포함 여부(`git merge-base --is-ancestor`)

| PR | 병합 커밋 | origin/main | 통합 브랜치 |
| --- | --- | --- | --- |
| #17 | `963e7a0` | 포함 | 포함(`3742d80` 병합 때부터) |
| #18 | `34b64aa` | 포함 | 포함(`6d180fd`) |
| #19 | `5d0dc23` | 포함 | 포함(`6d180fd`) |

`origin/main`에만 있는 커밋 0개. `git merge origin/main` 결과 `Already up to date.` 새 병합 커밋은 필요 없었다.

## 3. PR별 변경 파일

| PR | 파일 |
| --- | --- |
| #17 | `components/SourceFootnotes.tsx`, `pages/CompanyPage.tsx`(실시간 기준 시각), `i18n/extra.en.ts`, `real-data-notes.test.tsx` |
| #18 | `App.css`, `components/SourceFootnotes.tsx`(출처 없으면 영역 숨김), `pages/ResearchPage.tsx`(지원 범위 안내), `pages/portfolio/HoldingsSection.tsx`(체결가 안내), `i18n/extra.en.ts`, `a11y.test.tsx`, `order-picks.test.tsx`, `real-data-notes.test.tsx` |
| #19 | `App.css`, `pages/ResearchPage.tsx`(이어서 묻기 토글), `i18n/extra.en.ts`, `a11y.test.tsx` |

모두 `frontend/` 안이다. backend·DB·workflow·`.env*`는 없다.

## 4. 통합 브랜치에 반영된 커밋

`3742d80`(41~72차 백엔드 + PR #17까지의 main) → `a7c2faf` → `ad09b7e` → `6d180fd`(origin/main `5d0dc23` 병합) → `329be78`(`used_tools` 중복 제거) → `c98d418`(최종 보고서) → 이 보고서 커밋. 백엔드 커밋은 모두 남아 있다(`8110d69`, `92eb16b` 등이 통합 브랜치 조상).

## 5~8. 기능 검증

검증 방법:
- 코드 검토와 단위 테스트
- 실제 브라우저 검증. 헤드리스 Chrome에서 실제 프런트(`VITE_USE_MOCK=false`, vite 5173)를 열었다. 공개 GET은 실제 백엔드(8000)로 보냈다. 로그인 사용자(`/auth/me`·`/auth/preferences`·`/watchlist`·`/portfolio` GET)와 `POST /api/research`는 CDP `Fetch`로 가로채 브라우저 안에서 가짜 응답을 줬다. 따라서 DB 쓰기는 없다. 가로채지 않은 쓰기·인증 요청은 0건이다.
- 스크립트: `scratchpad/cdp/intercept_check.mjs`. 결과: `out_sync/log.json`과 스크린샷.

### 5. 출처 표시(PR #17·#18)

| 확인 | 결과 |
| --- | --- |
| `sources=[]`이면 `SourceFootnotes` 전체 숨김(도구 칩만 남지 않음) | 통과. 브라우저 `footer.evidence` 0개, 단위 테스트 통과 |
| `sources`가 있으면 "주요 근거 자료"·건수 칩·번호 각주 | 통과. "뉴스 1건 공시 1건 … [1] 기사 제목 [2] 분기보고서", 링크 href가 응답 url과 같음, "새 탭에서 열림" 안내 |
| 백엔드의 근거 부족 안내 유지 | 백엔드 답 본문은 그대로다. 코드 원인 경로는 본문에 "기준일 뒤 기사 N건 제외" 등을 쓴다(리허설 확인). 단 프런트는 `source_status.message`를 쓰지 않는다(13절) |

### 6. 실시간 시세 기준 시각(PR #17)

| 응답(가로채 준 값) | 화면 |
| --- | --- |
| `is_realtime:true`, `as_of:"2026-11-18T14:05:31+09:00"` | "실시간 시세 · 오후 02:05 기준" |
| 같은 순간 UTC 표기 `"…T05:05:31Z"`, 브라우저 시간대 America/New_York | "실시간 시세 · 오후 02:05 기준"(Asia/Seoul 고정 확인) |
| `is_realtime:true`, `as_of:""` | "실시간 시세"(대체 표시) |
| `is_realtime:false`, `as_of:"2026-11-17"` | "2026.11.17 종가 기준" |

백엔드 계약상 `as_of`는 항상 문자열이다. 실시간이면 KST 체결 시각, 폴백이면 `YYYY-MM-DD`이고, 프런트 zod 스키마도 `z.string()`이다. `as_of`가 null이면 스키마 검증에서 실패하지만 백엔드는 null을 보내지 않는다.

### 7. 모의 주문 가격 안내(PR #18)

- 매수·매도 모두 같은 안내가 보인다. 한국어: "체결가는 주문하는 순간의 실시간 시세예요. 다른 화면에 보이는 종가와 다를 수 있어요." 영어 번역도 확인했다. 실제 주문은 하지 않았다.
- **백엔드와 다른 점**: 백엔드 체결가는 정규장 OPEN일 때만 네이버 실시간이다(`price_source=naver`). 장외·애프터마켓·조회 장애 때는 DB 저장 종가로 체결된다(`price_source=fallback`, `price_is_realtime=false`). 리허설 주문(19:27)도 11-17 종가 1,098,000원으로 체결됐다. 안내가 장외 상황에는 맞지 않는다(13절).

### 8. AI 후속 질문(PR #19)

브라우저에서 실제로 보낸 `POST /api/research` 본문을 기록했다. 한국어·영어 화면 모두 같다.

| # | 확인 | 결과 |
| --- | --- | --- |
| 1 | 답변이 와도 자동으로 이어지지 않음 | 통과. 2번째 질문 `previous_report_id: null` |
| 2 | 기본은 새 질문 | 통과 |
| 3 | 최근 성공 답변 아래에만 버튼 | 통과. 답이 늘어도 버튼은 항상 1개, 마지막 성공 답변 안 |
| 4 | 켜면 그 report_id로 한 번만 이어짐 | 통과. `previous_report_id: 9002` |
| 5 | 전송 후 자동으로 꺼짐 | 통과. 전송 직후 `aria-pressed="false"`, 이어서 대화 안내 사라짐 |
| 6 | 이전 report_id 자동 재사용 없음 | 통과. 그 뒤 질문 2개 모두 `null` |
| 7 | 다시 누르면 끄기 | 통과(Enter), 안내 "이어서 묻기를 껐어요. 새 질문으로 보내요." |
| 8 | report_id가 null이면 버튼 없음 | 통과. 버튼 0개 |
| 9 | 다시 시도는 원래 previous 유지 | 통과. 503 실패 뒤 "다시 시도" → `previous_report_id: 9002` |
| 10 | 다른 종목 질문에 맥락 섞임 없음 | 통과. 토글을 켜지 않은 SK하이닉스·카카오 질문은 `null` |

접근성: 버튼은 `aria-pressed`로 상태를 알리고 Space·Enter 키로 켜고 끌 수 있다. 켜면 초점이 입력창으로 간다. 화면 읽기 알림(`role=status`)은 "#9002 답변에 이어서 질문해요."(영어 "Your next question continues from answer #9002.")와 끌 때 문구가 나온다. 콘솔 오류 0, 예외 0, 가로 넘침 0.

## 9. 다국어

| 키 | ko(코드 fallback) | en | ja | zh |
| --- | --- | --- | --- | --- |
| `research.scope_hint` | 있음 | 있음 | 없음 → 영어 | 없음 → 영어 |
| `holdings.price_note` | 있음 | 있음 | 없음 → 영어 | 없음 → 영어 |
| `research.followup_btn` | 있음 | 있음 | 없음 → 영어 | 없음 → 영어 |
| `research.followup_on_status` | 있음(`{id}`) | 있음(`{id}`) | 없음 → 영어 | 없음 → 영어 |
| `research.followup_off_status` | 있음 | 있음 | 없음 → 영어 | 없음 → 영어 |
| `company.realtime_at` | 있음(`{time}`) | 있음(`{time}`) | 없음 → 영어 | 없음 → 영어 |
| `sources.title`·`news_count`·`disclosure_count` | 있음 | 있음 | 있음 | 있음 |

- 변수 치환은 브라우저에서 확인했다(`{id}`→9002, `{time}`→오후 02:05).
- 영어 화면에 한국어 라벨이 섞이지 않는다. 예시 질문과 입력 예시의 한국어는 "질문은 한국어가 가장 정확" 정책에 따른 의도된 것이다.
- 영어 모바일(390px)에서 범위 안내·체결가 안내·버튼이 줄바꿈되고 잘리지 않는다(스크린샷).
- 일본어·중국어는 번역을 추가하지 않았다. 프런트 정책([coverage.test.ts](../frontend/src/i18n/coverage.test.ts)): "일본어·중국어 번역은 더 이상 관리하지 않는다(선택지에서도 숨김), 새 라벨은 영어만 필수". 설정 화면 선택지도 한국어·영어뿐이다. 계정 설정이 ja/zh인 사용자에게는 새 라벨 6개가 영어로 보인다(한국어 노출은 아님).
- 참고(이번 PR 밖): 영어 시장 카드 "Closing prices are 1 business days behind"의 단수·복수.

## 10. 프런트 테스트·빌드(`c98d418`, 병합 worktree)

| 검사 | 결과 |
| --- | --- |
| `npx tsc -b` | 통과 |
| `npm run lint`(oxlint) | 통과 |
| `vitest run` | 261개 중 253 통과, 8 skip, 실패 0(70차 점검 기록은 246개) |
| skip 8개 | `api/http.integration.test.ts`의 실제 백엔드 통합 테스트(`describe.skipIf(!enabled)`, 실행하면 DB 쓰기). 이번 PR과 무관하며 원래부터 skip이다 |
| PR이 추가한 테스트 | 7개 모두 실행·통과(이어서 묻기 2, 지원 범위 안내 1, 체결가 안내 1, 출처 숨김·표시 2, formatTime 1) |
| `npm run build`(`tsc -b && vite build`) | 성공 |
| `.env.local` | `frontend/.gitignore`의 `*.local`로 무시됨, 추적 파일 아님 |
| `package-lock.json` | 변경 없음(`npm ci` 불필요) |

## 11. 백엔드 API 호환성

| 항목 | 결과 |
| --- | --- |
| Research 요청 `{question, previous_report_id}` | 백엔드 요청 모델과 같다. previous는 같은 사용자 리포트인지 서버가 확인 |
| 응답 `report_id`·`previous_report_id`(nullable)·`sources`(6필드)·`disclaimer` | 프런트 `researchResponseSchema`와 같다. 백엔드의 `source_status`·`locale`·`translation_status`는 하위 호환 추가(프런트가 무시해도 됨) |
| `used_tools` | 문자열 배열. 2026-11-18 `8110d69`부터 같은 이름은 한 번만(화면 칩 `key={tool}` 중복 없음) |
| 실시간 `as_of`·`is_realtime`·`source` | 계약과 스키마 일치 |
| 주문 체결가 안내 | **불일치**: 안내는 항상 실시간이라고 하지만 백엔드는 장외에 저장 종가로 체결 |
| 종목 범위 | 백엔드는 시총 상위 주식 300개(KOSPI·KOSDAQ) + ETF 50개. "국내 상장 종목만"이라는 안내는 해외 종목 미지원을 알리는 데는 맞다. 다만 국내 상장 전 종목을 지원한다고 읽힐 수 있다 |
| ETF | 일봉·뉴스·구성종목(KODEX·TIGER 38개) 분석 지원. 안내와 모순 없음 |

백엔드 수정이 필요한 항목은 없다.

## 12. GitHub CI

`ca39643`(이 보고서 첫 커밋) push 뒤 run 44308744147: secret-scan, backend-unit, backend-integration, eval-offline, detect-frontend, frontend **6 job 모두 성공**. 로컬 secret scan(`scripts/secret_scan.sh`)도 통과했다.

## 13. 남은 문제

1. 체결가 안내 문구가 장외 체결(저장 종가)과 맞지 않는다.
2. 지원 범위 안내가 "국내 상장 전 종목"으로 읽힐 수 있다(실제 300+50개).
3. 출처 영역을 숨기면서 백엔드 `source_status.message`(빈 출처 이유)를 화면에 쓰지 않는다. 코드 원인 경로는 본문에 같은 설명이 있다. LLM 경로에서 근거를 못 찾은 경우(`none_found`)에도 본문에 그 설명이 있는지는 이번에 확인하지 않았다.
4. 코드 경로 답은 본문 끝에 면책을 넣고, 화면도 `disclaimer`를 따로 보여 면책이 두 번 보인다(최종 리허설에서 발견, 이번 PR 밖).
5. ja/zh 새 라벨 6개가 영어로 보인다(정책상 의도).

## 14. 서윤 씨 확인 사항

1. 체결가 안내를 "정규장에는 주문 순간의 실시간 시세, 장외에는 최근 저장 종가로 체결돼요"처럼 바꿀지. 주문 응답 `price_is_realtime`·`price_source`·`price_as_of`로 체결 결과 메시지에 기준을 붙일 수도 있다.
2. 지원 범위 안내에 "시가총액 상위 300개 종목과 ETF 50개"를 밝힐지.
3. 근거 없는 답에서 `source_status.message`를 한 줄로 보여줄지(72차 인계 1절).
4. 면책 두 번 표시를 화면에서 하나로 줄일지(예: 답 본문이 `disclaimer`로 끝나면 따로 그리지 않기).
5. ja/zh 사용자의 영어 대체 표시를 그대로 둘지.

## 15. 최종 클로즈아웃의 프런트 변경(2026-11-18 밤~11-20)

13절의 1~4번은 최종 브랜치에서 고쳤다. `origin/main`은 여전히 `5d0dc23`이고, 이 변경은 최종 브랜치 PR로 main에 들어간다. 서윤 씨 검토 대상이다.

| 커밋 | 파일 | 변경 | 13절 |
| --- | --- | --- | --- |
| `3fcb808` | `pages/portfolio/HoldingsSection.tsx`, `i18n/extra.en.ts` | 체결가 안내: "정규장 중에는 실시간 시세를, 장외에는 저장된 최근 종가를 기준으로 모의 체결해요. 체결 기준 가격은 시세 시점에 따라 다른 화면의 종가와 다를 수 있어요." | 1 |
| `3fcb808` | `pages/ResearchPage.tsx` | 지원 범위: "현재 등록된 국내 주식 300종목과 ETF 50개를 지원해요(예: 삼성전자). 그 외 종목과 해외 주식은 지원하지 않을 수 있어요." | 2 |
| `3fcb808` | `components/SourceStatusNote.tsx`(새 파일), `pages/ResearchPage.tsx`·`pages/HistoryPage.tsx`, `api/schemas.ts`·`types.ts`(`source_status` 타입) | 출처가 비었고 `source_status.message`가 있으면 답 아래 한 줄로 보여 준다 | 3 |
| `3fcb808` | `utils/disclaimer.ts`(새 파일, `stripDuplicateDisclaimer`) | 답 본문 끝 문단이 응답 `disclaimer`와 정확히 같으면 본문에서만 뺀다. 면책은 답 아래에 한 번 | 4 |
| `3fcb808` | `components/PriceChartCard.tsx` | 종목 상세 "60거래일" 요약 칸이 `—`이던 문제: 60거래일 수익률에 기준 종가 1행이 더 필요해 `price-history?days=61`을 따로 조회한다 | 72차 인계 |
| `4fa99f8` | `utils/plainSummary.ts`(새 파일), `pages/HistoryPage.tsx` | 기록 목록 요약에 `## 주가 동향`처럼 마크다운 기호가 보이던 문제. 제목(`#`)·강조(`**`, `__`)·코드(`` ` ``)·인용(`>`) 기호만 뗀다. 숫자·부호·번호 목록·"#1위"는 그대로 | 새로 발견 |

검증:

- vitest 269 통과, 8 skip(원래 skip인 실제 백엔드 통합 테스트), 실패 0. `tsc -b`, oxlint 통과.
- 2026-11-20 실제 로그인 브라우저 검증(실백엔드·실DB·실LLM, mock 꺼짐): 체결가 안내 표시, 장중 주문 실시간가 체결(삼성전자 185,300원, KODEX 200 77,020원), 지원 범위 안내 표시, 면책 카드마다 1회, 도구 칩 중복 0, 60거래일 -23.11% 표시, 기록 목록 `##` 0건(PC·모바일), 콘솔 오류 0, 가로 넘침 0.
- 이어서 묻기(PR #19)를 실제 계정으로 다시 확인: 토글을 켠 질문만 `previous_report_id`가 실리고 보낸 뒤 꺼진다(FINAL_RELEASE_REPORT 7절).

남은 것: 13절 5번(ja/zh 영어 대체, 정책상 의도). 키보드 초점에서 입력창 자체는 outline이 없고 부모 `.composer-row:focus-within`이 초점을 보여 준다(11-18과 같음).
