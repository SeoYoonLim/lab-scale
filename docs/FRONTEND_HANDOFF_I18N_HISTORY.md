# 프런트엔드 인계: 다국어 + 내 기록(리서치·진단) 관리

작성 2026-11-05 · 보완 2026-11-06(전망 간결 답·후속 답 언어별 템플릿) · 백엔드 `feature/backend` · 대상: 프런트엔드(서윤 씨)

API 전체 명세는 `backend/API.md`의 두 절에 있다: **"기록 관리: 날짜별 목록·검색·통합 검색 (2026-11-05)"**, **"다국어: ko-KR · en-US · ja-JP · zh-CN (2026-11-05)"**.
이 문서는 화면을 만들 때 필요한 것만 모았다. 아래 JSON 예시는 dev DB에 테스트용 사용자와 합성 기록을 넣고 실제로 호출한 응답에서 줄인 것이다(id는 예시).

---

## 1. 바로 알아야 할 것 5가지

1. **기존 화면은 그대로 동작한다.** 기존 API를 파라미터 없이 부르면 응답이 한 글자도 바뀌지 않는다. 새 필드는 `view=record`를 줄 때만 붙는다.
2. **기록 화면은 `view=record`를 붙여서 부른다.** 제목·종목 목록·분석 유형·출처 수·날짜 그룹(`date_group`)·작성 언어(`locale`)가 들어 있다.
3. **전체/리서치/진단 탭은 `GET /api/history/search` 하나로 된다.** `type=all|research|diagnosis`, 탭 숫자는 `counts`. 상세는 항목의 `detail_path`(기존 상세 API)로 연다.
4. **언어는 계정 설정이 기본이다.** 화면 언어를 바꾸면 `PUT /api/auth/preferences`로 저장하고, AI 요청에는 따로 `locale`을 안 보내도 계정 설정이 적용된다(보내면 그 값이 먼저).
5. **숫자·날짜·종목명은 언어와 무관하게 같다.** 종목명·ETF명은 공식 등록명(한글) 그대로, 뉴스·공시 제목은 원문 그대로다. 번역하지 말고 그대로 보여 준다.

---

## 2. 다국어

### 2-1. 지원 언어와 규칙

| locale | 언어 | 비고 |
| --- | --- | --- |
| `ko-KR` | 한국어 | **기본값.** 기존 사용자 전부 ko-KR |
| `en-US` | 영어 | |
| `ja-JP` | 일본어 | |
| `zh-CN` | 중국어 간체 | 번체(`zh-TW`, `zh-Hant`)는 지원하지 않음 → ko-KR |

- 결정 순서: **요청 `locale` > 계정 `preferred_locale` > ko-KR.**
- 요청에 지원하지 않는 값을 보내면 ko-KR이다(계정 설정으로 넘어가지 않는다). `en`, `en_us`, `EN-us`, `zh`, `zh-Hans`는 받아 준다.
- 백엔드는 `Accept-Language` 헤더를 보지 않는다. 브라우저 언어로 자동 전환하고 싶으면 프런트가 판단해서 `PUT /api/auth/preferences`로 저장한다.
- 비로그인 화면(로그인·가입·면책 문구)은 계정 설정이 없으니 프런트가 고른 언어를 쿼리로 보낸다(`GET /api/disclaimer?locale=en-US`).

### 2-2. 언어 설정 화면 (`GET/PUT /api/auth/preferences`)

```json
GET /api/auth/preferences
{"preferred_locale": "ko-KR", "preferred_timezone": null, "effective_timezone": "Asia/Seoul",
 "supported_locales": ["ko-KR", "en-US", "ja-JP", "zh-CN"], "default_locale": "ko-KR"}

PUT /api/auth/preferences  {"preferred_locale": "en-US", "preferred_timezone": "America/New_York"}
→ 200 {"preferred_locale": "en-US", "preferred_timezone": "America/New_York", "effective_timezone": "America/New_York", ...}

PUT /api/auth/preferences  {"preferred_locale": "fr-FR"}
→ 422 {"detail": [{"type": "unsupported_locale", "loc": ["body", "preferred_locale"], "msg": "지원하지 않는 언어입니다: ko-KR, en-US, ja-JP, zh-CN 중 하나", ...}]}
```

- 보내지 않은 필드는 그대로다. `preferred_timezone: null`을 보내면 기본(Asia/Seoul)으로 돌아간다.
- 설정은 계정에 저장돼 다시 로그인해도 유지된다(통합 테스트로 확인).
- 로그인 직후 한 번 `GET /api/auth/preferences`를 불러 화면 언어를 정하면 된다. `GET /api/auth/me`는 바꾸지 않았다.

### 2-3. AI 요청에 locale 보내기

| API | 어디에 | 예 |
| --- | --- | --- |
| `POST /api/research` | 본문 | `{"question": "PER이 뭐야?", "locale": "ja-JP"}` |
| `POST /api/portfolio/diagnosis` | 쿼리 | `?locale=zh-CN` |
| `GET /api/briefing` | 쿼리 | `?locale=en-US` |
| `GET /api/portfolio/exposure` | 쿼리 | `?locale=en-US` |
| `GET /api/portfolio/diagnosis/{id}/compare?view=record` | 쿼리 | `&locale=en-US`(비교 안내 문구) |
| `GET /api/disclaimer` | 쿼리 | `?locale=en-US` |

계정 설정을 이미 저장했다면 `locale`은 생략해도 된다. 응답 JSON의 **키 구조는 언어와 무관하게 같다**(문장 값만 다르다).

### 2-4. 리서치 답변의 언어 — 화면에서 구분할 것

리서치 답변은 백엔드가 먼저 한국어로 만들고(숫자 검증 등 전부 적용) 그 언어로 바꾼다. 결과는 세 가지다(`GET /api/research/{id}?view=record`의 `translation_status`):

| `translation_status` | 뜻 | 화면 |
| --- | --- | --- |
| `localized` | 그 언어로 직접 쓴 답(용어 사전, 고정 안내, 전망 간결 답·후속 답 템플릿) | 그대로 |
| `translated` | 숫자·이름·제목을 보호한 채 번역한 답. 끝에 "※ Translated from the Korean answer…" 안내가 붙어 있다 | 그대로. "한국어 원문 보기"로 `original_answer`를 보여 줄 수 있다 |
| `fallback_original` | 번역을 검증하지 못해 **한국어 원문**을 보낸 답. 맨 위에 그 언어로 안내 한 줄이 붙어 있다 | 그대로 보여 주되, 한국어임을 알 수 있게 `research.translation_fallback` 문구를 함께 |
| `null` | ko-KR 기록 | |

> 2026-11-06 현재 로컬 LLM(Ollama)이 응답하지 않는 상태라, 상세 전망 리서치·시세처럼 번역이 필요한 답은 `fallback_original`로 나온다.
> 용어 사전 답과 **전망 간결 답·후속 답**(결론·왜·위험·확률 등)은 LLM 없이 언어별 템플릿이라 그 언어로 정상 출력된다(`localized`).
> 영어·일본어·중국어로 **질문**하는 경우는 질문 해석이 한국어 규칙이라 품질이 낮을 수 있다(대부분 LLM 경로). 질문 입력은 당분간 한국어 안내를 권장한다.

### 2-5. 번역 리소스 (`docs/i18n/{locale}.json`)

네 파일 모두 같은 키(173개 문구)를 갖는다(`backend/tests/test_i18n_resources.py`가 키 일치와 백엔드 값 일치를 검사한다). 섹션:

| 섹션 | 쓰는 곳 |
| --- | --- |
| `common`, `nav` | 공통 버튼·메뉴. `common.written_in` = "작성 언어: {language}" |
| `history` | 내 기록 화면(탭, 검색창, 날짜 필터, 정렬, 빈 결과, 오류) |
| `date_group` | `today`·`yesterday`·`last_7_days`·`last_30_days`·`older` 헤더 |
| `analysis_type` | 리서치 `analysis_type` 배지 10종 |
| `research`, `diagnosis` | 상세·비교 화면 라벨 |
| `diagnosis_flag`, `briefing_flag`, `exposure_flag` | 플래그 코드·종류 → 짧은 이름(배지용). 문장은 API가 이미 그 언어로 준다 |
| `settings` | 언어·시간대 설정 화면 |
| `locale_name` | 언어 선택 목록(각 언어 자기 이름: 한국어 / English / 日本語 / 简体中文) |
| `errors` | HTTP 상태별 화면 문구(아래 4장) |
| `system` | 면책·답변 실패·지연 문구(백엔드와 같은 값) |
| `finance_terms` | 언어별 금융 용어 사전(키는 한국어 용어). 백엔드 번역 프롬프트와 같은 값이라 화면 라벨도 이 값을 쓰면 용어가 맞는다 |

`{page}`, `{total}`, `{language}`, `{count}` 같은 자리표시자는 프런트가 채운다. 날짜·숫자 표시는 `Intl.DateTimeFormat`/`Intl.NumberFormat`에 locale을 넘기면 된다. **API의 숫자 값 자체는 바꾸지 않는다.**
금융 용어 정의 40개는 백엔드 용어 사전(`backend/app/glossary_i18n.py`)이 답으로 준다(프런트에 둘 필요 없음).

### 2-6. 기존 한국어 사용자 하위 호환

- 설정을 바꾸지 않은 사용자: `preferred_locale = ko-KR`, 모든 응답이 이전과 같다(백엔드에서 기준선 응답과 바이트 비교로 확인).
- 과거 기록은 다시 번역되지 않는다. 언어를 바꿔도 과거 리서치·진단은 작성 당시 언어로 보인다. 기록 `locale`이 현재 화면 언어와 다르면 `common.written_in` 배지를 붙여 준다.
- 기존 기록은 모두 `locale = ko-KR`이다.

---

## 3. 내 기록(리서치·진단)

### 3-1. 날짜·시간대

- `created_at`은 UTC 그대로(`+00:00`). 화면에는 응답의 `local_date`(YYYY-MM-DD)·`local_time`(HH:MM)을 쓰면 된다.
- 기준 시간대: 요청 `timezone` > 계정 `preferred_timezone` > `Asia/Seoul`. 응답 최상위 `timezone`에 실제로 쓴 값이 있다.
- `date_group`(그 시간대의 오늘 기준 달력 날짜 차이): `today`(0일) · `yesterday`(1일) · `last_7_days`(2~6일) · `last_30_days`(7~29일) · `older`(30일 이상).
- 목록은 최신순이라 같은 `date_group`이 연속으로 온다. **페이지가 바뀌어도 그룹 헤더는 항목의 `date_group`이 바뀔 때만 새로 그리면 된다**(페이지 경계에서 같은 그룹이 이어질 수 있음).
- `date_from`/`date_to`는 그 시간대의 달력 날짜, `date_to`는 그날 끝까지 포함. 예: 서울 기준 `2026-11-04` 23:59에 쓴 기록은 `date_to=2026-11-04`에 들어가고, 서울 `2026-11-05 00:00`(UTC 11-04 15:00) 기록은 `date_from=2026-11-05`에 들어간다.

### 3-2. 통합 검색: `GET /api/history/search` (탭 3개)

| 파라미터 | 기본 | 범위 |
| --- | --- | --- |
| `type` | `all` | `all` / `research` / `diagnosis` |
| `keyword` | 없음 | 최대 50자. 공백으로 나눈 낱말(최대 5개)이 **모두** 들어간 기록 |
| `date_from`, `date_to` | 없음 | `YYYY-MM-DD` |
| `timezone` | 계정 설정 또는 Asia/Seoul | IANA 이름 |
| `page` | 1 | 1~10000 |
| `size` | 20 | 1~100 |
| `sort` | `created_at,desc` | `created_at,desc` / `created_at,asc` |

검색 대상: 리서치는 질문·요약·답변 전문·종목명·종목코드(여러 종목 포함), 진단은 요약·강점·위험·제안·플래그 문장·보유 종목명·종목코드.
별칭도 찾는다(`삼전` → 삼성전자, `하닉` → SK하이닉스). 위험 키워드는 플래그로도 찾는다(`집중도` → 한 종목 집중·시장 편중, `위험` → 플래그가 있는 진단, `현금`, `손실`, `레버리지` 등).

```json
GET /api/history/search?keyword=SK하이닉스&size=20
{
  "type": "all", "keyword": "SK하이닉스", "terms": ["SK하이닉스"], "date_from": null, "date_to": null,
  "timezone": "Asia/Seoul", "sort": "created_at,desc", "page": 1, "size": 20,
  "total": 3, "total_pages": 1, "has_next": false,
  "counts": {"all": 3, "research": 2, "diagnosis": 1},
  "items": [
    {"kind": "research", "id": 32500, "created_at": "2026-11-05T01:00:00+00:00",
     "local_date": "2026-11-05", "local_time": "10:00", "date_group": "today",
     "title": "SK하이닉스 전망 알려줘", "summary": "SK하이닉스 단기 방향은 상승 쪽입니다.", "locale": "ko-KR",
     "company_names": ["SK하이닉스"], "tickers": ["000660"], "detail_path": "/api/research/32500",
     "research": {"report_id": 32500, "previous_report_id": null, "question": "SK하이닉스 전망 알려줘",
                  "analysis_type": "outlook", "analysis_types": ["outlook", "news", "price"], "source_count": 2,
                  "used_tools": ["stock_tool", "news_tool"]},
     "diagnosis": null},
    {"kind": "diagnosis", "id": 51639, "created_at": "2026-11-05T00:30:00+00:00",
     "local_date": "2026-11-05", "local_time": "09:30", "date_group": "today",
     "title": "삼성전자 외 2종목 진단", "summary": "총자산 9,000,000원 중 현금이 3,000,000원(비중 33.33%)이고, …", "locale": "ko-KR",
     "company_names": ["삼성전자", "SK하이닉스", "에코프로비엠"], "tickers": ["005930", "000660", "247540"],
     "detail_path": "/api/portfolio/diagnosis/51639", "research": null,
     "diagnosis": {"diagnosis_id": 51639, "source": "rule_based", "total_asset": 9000000.0, "holdings_count": 3,
                   "cash_weight_pct": 33.33, "top_holdings": [{"ticker": "005930", "company_name": "삼성전자", "weight_pct": 22.22}],
                   "risk_summary": "규칙 기준으로 확인된 위험 항목은 없습니다.", "flags": [], "flag_codes": [],
                   "generated_at": "2026-11-05T00:56:00.344219+00:00"}}
  ]
}
```

- `kind`로 카드 모양을 나눈다. `research`/`diagnosis` 중 해당 종류만 채워지고 나머지는 null.
- `total` = 지금 탭의 건수, `counts` = 탭마다의 건수(같은 검색 조건). 탭을 바꿀 때 숫자를 다시 계산할 필요가 없다.
- 다음 페이지: `has_next`가 true인 동안 `page + 1`. `page`가 끝을 넘으면 200 + 빈 `items`.

### 3-3. 리서치 탭만 따로 쓸 때: `GET /api/research?view=record`

기존 목록 API에 `view=record`, `date_from`, `date_to`, `timezone`, `analysis_type`을 더할 수 있다(기존 `q`, `sort`, `order`, `limit`, `offset` 그대로).
`q`는 기존 규칙(질문·요약·리포트 종목 부분 일치)이고 답변 전문은 찾지 않는다. 넓은 검색은 통합 검색을 쓴다.

```json
GET /api/research?limit=2&view=record
{"total": 6, "has_next": true, "total_pages": 3, "timezone": "Asia/Seoul",
 "items": [{"report_id": 32502, "previous_report_id": null, "question": "삼성전자랑 카카오 비교해줘",
            "summary": "두 종목의 최근 등락률입니다.", "company_name": null, "created_at": "2026-11-04T15:00:00+00:00",
            "used_tools": ["stock_tool", "stock_tool"], "locale": "ko-KR",
            "title": "삼성전자랑 카카오 비교해줘", "company_names": ["삼성전자", "카카오"], "tickers": ["005930", "035720"],
            "analysis_type": "price", "analysis_types": ["price"], "source_count": 0,
            "local_date": "2026-11-05", "local_time": "00:00", "date_group": "today"}]}
```

`company_name`(기존)은 종목이 정확히 하나일 때만 채워진다. 여러 종목은 `company_names`를 쓴다.

### 3-4. 과거 리포트 상세: `GET /api/research/{id}?view=record`

당시 질문·답변·출처를 저장된 그대로 준다(현재 시세·최신 뉴스로 다시 만들지 않는다). `data_basis`는 당시 쓴 데이터의 기준일이다.

```json
{"report_id": 32500, "question": "SK하이닉스 전망 알려줘", "answer": "SK하이닉스 단기 방향은 상승 쪽입니다.",
 "sources": [{"tool": "news_tool", "type": "news", "title": "HBM 수요 확대", "company_names": ["SK하이닉스"], "url": "https://example.com/0"}],
 "disclaimer": "…", "title": "SK하이닉스 전망 알려줘", "analysis_type": "outlook", "source_count": 2,
 "local_date": "2026-11-05", "local_time": "10:00", "date_group": "today", "timezone": "Asia/Seoul",
 "data_basis": [{"tool": "stock_tool", "company_name": "SK하이닉스", "ticker": "000660", "base_date": "2026-11-04",
                 "period_start_date": null, "period_end_date": null, "latest_date": null, "data_date": null, "price_as_of": null, "as_of": null}],
 "locale": "ko-KR", "translation_status": null, "original_answer": null}
```

`data_basis`의 null 키는 그 tool 결과에 없던 값이다(있는 것만 표시).

### 3-5. 진단 탭: `GET /api/portfolio/diagnosis/history?view=record`

기존 파라미터에 `q`(통합 검색과 같은 낱말 규칙), `date_from`, `date_to`, `timezone`, `view`를 더할 수 있다. 항목에 붙는 필드:
`title`(그 진단의 작성 언어로 만든 제목, 예: "삼성전자 외 2종목 진단" / "Diagnosis: 삼성전자 and 2 more"), `locale`, `cash_weight_pct`, `top_holdings`(비중 상위 3),
`risk_summary`(위험 첫 문장), `flag_codes`, `generated_at`(그 진단이 쓴 시세의 기준 시각), `local_date`, `local_time`, `date_group`.
상세(`GET /api/portfolio/diagnosis/{id}`)는 저장본 그대로다(당시 보유 구성·당시 계산 결과, 다시 계산하지 않음).

### 3-6. 진단 비교: `GET /api/portfolio/diagnosis/{id}/compare?view=record`

`base`를 안 주면 바로 이전 진단과 비교한다. `view=record`면 기존 응답에 다음이 붙는다.

```json
"basis": {"current_created_at": "2026-11-05T00:30:00+00:00", "current_generated_at": "…",
          "previous_created_at": "2026-11-03T02:00:00+00:00", "previous_generated_at": "…",
          "recomputed": false, "note": "두 진단은 각각 저장 당시의 보유 구성과 시세로 계산한 결과입니다. 기준 시점이 서로 다르고, 현재 시세로 다시 계산하지 않았습니다."},
"metric_changes": {"cash_weight_pct": {"previous": 9.09, "current": 33.33, "change": 24.24},
                   "top1_weight_pct": {"previous": 90.91, "current": 22.22, "change": -68.69},
                   "herfindahl_index": {"previous": 1.0, "current": 0.3334, "change": -0.6666}, "…": "…"},
"exposure_changes": null
```

- 화면 구성: 총자산 변화(`changes.total_asset`), 현금 비중·집중도 변화(`metric_changes`), 종목별 비중 변화(`changes.holdings`의 `status`: added/removed/kept),
  새 위험(`changes.flags.added`)·개선된 위험(`changes.flags.resolved`). `basis.note`는 비교 화면 상단에 항상 보여 준다.
- `exposure_changes`는 항상 null이다(진단 저장본에 ETF 실질 노출이 없음). `diagnosis.exposure_not_compared` 문구를 쓰면 된다.

---

## 4. 로딩·빈 결과·오류

| 상황 | 응답 | 화면 |
| --- | --- | --- |
| 로딩 | – | `common.loading` |
| 결과 없음 | 200, `total: 0`, `items: []` | 검색어·날짜가 있으면 `history.empty_search`, 없으면 `history.empty` |
| 페이지 끝 넘음 | 200, 빈 `items`, `has_next: false` | 더 보기 버튼 숨김 |
| 토큰 없음·만료 | 401 | `errors.401` 후 로그인 화면(잘못된 파라미터를 같이 보내도 401이 먼저) |
| 남의 기록·없는 기록 | 404(둘을 구분하지 않음) | `errors.404` |
| 잘못된 파라미터 | 422(FastAPI 검증 오류 형식, `detail[].loc`에 필드) | `loc`이 `date_from`이면 `history.error_date_range`, 그 밖은 `errors.422` |
| 요청 과다 | 429 | `errors.429` |
| DB 연결 불가 | 503 | `errors.503` + `common.retry` |

HTTP 오류의 `detail` 문자열은 호환을 위해 **한국어 그대로**다. 화면 문구는 상태 코드로 `errors.*`를 쓴다.

---

## 5. 추천 화면 구성

1. **내 기록 메인**: 상단 검색창(`keyword`) + 날짜 범위 + 정렬, 탭(전체/AI 리서치/포트폴리오 진단, 숫자 = `counts`), 날짜 그룹 헤더(`date_group`) 아래 카드 목록. 데이터: `/api/history/search`
2. **AI 리서치 탭**: 같은 화면의 `type=research`. 카드: 제목·종목 배지(`company_names`)·분석 유형 배지(`analysis_type`)·출처 수·작성 시각·작성 언어(다를 때만)
3. **포트폴리오 진단 탭**: `type=diagnosis`. 카드: 제목·총자산·현금 비중·상위 종목·위험 요약·플래그 배지(`diagnosis_flag.*`)
4. **통합 검색**: 1과 같은 화면(검색어 입력 시). 하이라이트가 필요하면 `terms`의 낱말을 프런트에서 강조
5. **날짜별 그룹 목록**: `date_group`이 바뀔 때 헤더(`date_group.*`). 무한 스크롤은 `has_next`/`page`
6. **과거 리포트 상세**: `detail_path` + `?view=record`. 상단 "당시 저장된 결과" 안내(`history.past_basis_notice`), 기준일(`data_basis`), 출처 목록(제목 원문 그대로), 작성 언어·번역 상태
7. **과거 진단 비교**: 진단 상세에서 "이전 진단과 비교" → `/compare?view=record`. `basis.note`를 맨 위에
8. **언어 설정**: `locale_name` 목록 + 시간대 선택, 저장은 `PUT /api/auth/preferences`. 설명 문구 `settings.language_help`(과거 기록은 작성 언어 그대로라는 안내)

---

## 6. 아직 안 된 것 (백엔드 확인 결과)

- 로컬 LLM이 응답하지 않아 **실제 모델의 번역 품질은 측정하지 못했다.** 번역 경로는 가짜 LLM 테스트로만 검증했고, 지금은 전망·시세 등 번역이 필요한 답이 한국어 원문+안내로 나간다.
- 전망 **간결 답**과 후속 답 9종(결론·고르기·왜·한 줄·비교·확률·매수 가부·위험·데이터 기준)은 언어별 템플릿이다. 전망 **상세 리서치**(13섹션)·실적/공시 날짜/신호 의미 후속 답·기간 답변은 보호 번역이다(지금은 한국어 원문+안내). 용어 사전·진단 문장·브리핑·실질 노출·면책·안내 문구는 언어별 템플릿이다.
- 외국어 질문의 해석(라우팅)은 한국어 규칙 기반이다.
- 실질 노출 경고 중 백엔드가 만든 한국어 문장 형식과 다른 것은 한국어 그대로 남는다(값을 추측하지 않는다).
