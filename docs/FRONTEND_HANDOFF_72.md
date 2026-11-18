# 72차 프런트엔드 인수인계 (2026-11-18)

받는 사람: 서윤 씨(프런트엔드). 백엔드 `feature/backend` 72차 커밋 기준. 프런트 파일은 이번에 고치지 않았습니다.

이번 변경은 모두 **하위 호환 추가**입니다. 기존 키의 이름·뜻·값은 그대로라 지금 화면은 그대로 동작합니다. 현재 Zod 스키마(`z.object`, strict 아님)는 모르는 키를 버리므로 파싱도 깨지지 않습니다. 아래는 쓰면 화면이 더 정확해지는 것들입니다. 필드 정의 전체는 `backend/API.md` 맨 끝 "72차" 절에 있습니다.

## 1. 출처가 빈 이유: `source_status`

대상: `POST /api/research` 응답, `GET /api/research/{id}`(view=record 포함). 위치: `frontend/src/components/SourceFootnotes.tsx`, `ResearchPage.tsx`, `HistoryPage.tsx`.

`sources: []`는 그대로입니다. 빈 이유가 `source_status`로 옵니다.

| `status` | 뜻 | 화면 |
| --- | --- | --- |
| `available` | 출처 있음 | 지금처럼 각주 목록 |
| `not_applicable` | 뉴스·공시를 조회하지 않는 답(시세·계산·환율·안내) | 지금처럼 도구 칩만. 안내 문구 없음 |
| `none_found` | 조회했지만 기간 안에 그 종목 자료 없음 | 도구 칩 + `message` 한 줄 |
| `excluded_after_cutoff` | 시세 기준일 장 마감 뒤 자료만 있어 원인 근거에서 뺌(정상 정책) | 도구 칩 + `message` 한 줄 |
| `long_period` | 3개월 초과 원인 질문이라 기사를 원인으로 고르지 않음 | 도구 칩 + `message` 한 줄 |
| `error` | 조회 오류(`is_error: true`) | 오류 스타일 + `message` |

권장 규칙
- `sources.length === 0`이면 빈 출처 카드·빈 목록·"근거 자료" 제목을 보이지 않습니다. 지금 `SourceFootnotes`가 이미 그렇게 합니다(제목이 "사용한 분석 도구"로 바뀜).
- `status`가 `none_found`·`excluded_after_cutoff`·`long_period`·`error`면 칩 아래에 `source_status.message`를 한 줄 보입니다. 문구는 서버가 만들어 주므로 프런트에서 새로 쓰지 않아도 됩니다.
- `error`만 오류로 보입니다. 나머지 빈 상태는 정상 상태라 경고색을 쓰지 않습니다.
- `source_status`가 없으면(옛 응답·mock) 지금 동작 그대로 둡니다.

실데이터 예("삼성전자 오늘 왜 올랐어?", 시세 기준일 2026-11-12):

```json
{
  "sources": [],
  "source_status": {
    "status": "excluded_after_cutoff", "count": 0, "is_error": false, "empty_reason": "excluded_after_cutoff",
    "message": "시세 기준일 2026-11-12 장 마감(15:30) 뒤 자료 993건은 그 시세에 반영되지 않아 분석에 쓰지 않았고, 기준 시점 전의 유효한 근거가 없어 출처가 없습니다. 원인을 특정하지 않았습니다.",
    "excluded_after_cutoff_count": 993, "excluded_unknown_time_count": 0,
    "market_data_as_of": "2026-11-12", "evidence_cutoff": "2026-11-12T15:30:00+09:00"
  }
}
```

TypeScript 모양(`contracts/research.ts`에 선택 필드로):

```ts
source_status?: {
  status: 'available' | 'not_applicable' | 'none_found' | 'excluded_after_cutoff' | 'long_period' | 'error'
  count: number
  is_error: boolean
  empty_reason: string | null
  message: string | null
  excluded_after_cutoff_count: number
  excluded_unknown_time_count: number
  market_data_as_of: string | null
  evidence_cutoff: string | null
} | null
```

`sources[].company_names`: 같은 기사가 두 종목 결과에 있으면 한 번만 오고 두 종목 이름이 함께 들어 있습니다(복수 종목 원인 답).

## 2. 뉴스·공시 freshness 시각

대상: `GET /api/stocks/{ticker}/news`, `/disclosures`의 `freshness`. 위치: `contracts/stockFeed.ts`의 `FeedFreshness`·`feedEmptyReason`.

서윤 씨가 본 불일치(`last_collected_at` 11-17 대 `last_collection.finished_at` 11-03)의 원인은 이렇습니다. `last_collection`은 `refresh_data`의 `news` 단계만 보는데, 11-17 복구 작업은 `news_recovery` 단계로 기록됐습니다. 두 값 모두 정의대로는 맞지만 뜻이 다릅니다. 그래서 의미별로 나눈 필드를 더했습니다.

| 화면에서 말하려는 것 | 쓸 필드 | 삼성전자 2026-11-18 값 |
| --- | --- | --- |
| 데이터가 최신인가 | `freshness_state`(`fresh`/`stale`/`no_data`), `latest_record_at`, `record_lag_weekdays` | `fresh`, 11-17 09:32, 1 |
| 마지막으로 수집에 성공한 때 | `last_successful_collection_at` | 11-17 09:34 |
| 마지막 수집 시도와 결과 | `last_attempt_finished_at`, `last_collection_status`, `last_collection_step` | 11-17 09:34, `success`, `news_recovery` |
| (기존) 마지막 저장 | `last_collected_at` | 11-17 09:34 |
| (기존) refresh_data news 단계 | `last_collection` | 11-03 19:54 |

권장 규칙
- "마지막 수집" 표시는 `last_collection.finished_at` 대신 `last_successful_collection_at`을 씁니다. 없으면 `last_collected_at`을 씁니다.
- "데이터 지연" 배지는 `freshness_state === 'stale'`일 때만 보입니다.
- "수집 실패" 표시는 `last_collection_status === 'failed'`일 때 보입니다. `feedEmptyReason`도 `freshness.last_collection_status ?? freshness.last_collection?.status`로 바꾸면 복구 단계까지 반영됩니다.
- 데이터 최신성과 수집 성공은 별개입니다. 마지막 시도가 실패해도 데이터는 fresh일 수 있습니다.

## 3. 영문 공식 종목명

- `JYP Ent.`, `NAVER`, `NC`, `HLB`, `HMM`, `KT&G`, `S-Oil`, `LS ELECTRIC`, `F&F`, `SK`, `LG`, `CJ` 등 주식 23종목과 ETF 7종목(`KODEX 200` 등)은 한글이 없는 이름입니다. 모두 KRX 상장 목록(FinanceDataReader `StockListing`)이 준 원천값 그대로입니다.
- 번역하지 않습니다. 화면에도 `name` 그대로 보여 주세요. 검색은 이미 한글 별칭으로 됩니다(예: "네이버"→NAVER, "JYP엔터"→JYP Ent., "엔씨소프트"→NC). 다만 이 별칭은 검색용이라 표시명으로 쓰지 않습니다.
- 종목은 항상 `ticker`로 구분합니다(이름이 같은 종목은 없지만 이름만으로 링크하지 않습니다).

## 4. `sector`가 null

- 350종목 모두 `sector`가 null입니다. 이 프로젝트에는 업종을 수집하는 코드가 없어서 원래 비어 있는 값입니다(추정해서 채우지 않습니다).
- 화면에는 `—`로 보이고 "미분류"·"기타"처럼 값이 있는 것처럼 쓰지 않습니다. 업종별 집계 카드(실질 노출의 sector 등)는 지금처럼 "업종 분류 데이터 없음" 안내를 유지합니다.

## 5. 실시간 현재가와 저장 종가

예: 모의 주문 체결가 189,800원(네이버 실시간, 장중)과 차트의 2026-11-12 저장 종가 208,000원. 서로 다른 시점의 가격이라 오류가 아닙니다.

| 화면 이름 | API | 기준 |
| --- | --- | --- |
| 실시간 현재가 | `GET /api/stocks/{ticker}/realtime-price`: `is_realtime: true`, `source: "naver"`, `as_of`(체결 시각) | 정규장 OPEN일 때만. 아니면 `source: "fallback"` + 저장 종가 |
| 최근 저장 종가 | 시세·차트 API의 마지막 행, 종목 목록 `latest_close` | 그 행의 거래일 |
| 모의 주문 체결가 | `POST /api/portfolio/orders`: `price` + 새 `price_source`, `price_is_realtime`, `price_as_of` | naver면 체결 시각, fallback이면 저장 종가 거래일 |
| 보유 종목 현재가 | `GET /api/portfolio`의 `holdings[]`: `current_price`, `is_realtime` + 새 `price_source`, `price_as_of` | 위와 같음 |

권장 규칙
- 둘을 하나의 "현재가"로 부르지 않습니다. "현재가(실시간, 10:01 기준)"와 "최근 저장 종가(2026-11-12)"처럼 기준을 붙입니다.
- 주문 완료 안내에 `price_source === 'fallback'`이면 "실시간 시세를 받지 못해 2026-11-12 저장 종가로 체결했습니다"를 보입니다.

## 6. 답변 경로 변경(응답 키 변화 없음, 화면 확인만)

- 3개월 초과 원인 질문("삼성전자 지난 6개월 동안 왜 올랐어?"): 코드 답입니다. 등락은 계산하고 질문 전제가 틀리면 바로잡습니다. 기사는 건수만 쓰고 출처는 비어 있습니다(`long_period`).
- 두세 종목 원인 질문("삼성전자와 SK하이닉스 최근 한 달 동안 왜 올랐어?"): `### 1. 삼성전자`, `### 2. SK하이닉스` 단락으로 나뉘는 긴 답입니다. 마크다운 제목(`###`, `##`)이 잘 보이는지, 긴 답이 잘리지 않는지 확인해 주세요.
- 환율 질문("최근 환율 흐름과 5거래일 변동률을 알려 줘."): 코드 답이며 `used_tools`는 `["fx_tool"]`입니다. 하루 변동률과 5거래일 변동률이 다른 줄로 옵니다.
- 시장 원인 질문("코스피 한 달 동안 왜 떨어졌어?"): 지수 등락만 계산하고 원인은 특정하지 않는다고 답합니다.
- 종목 없는 원인 질문("최근 6개월 동안 상승한 이유가 뭐야?", 직전 대화 없음): 종목을 되묻는 한 줄 답입니다.

## 7. 다시 확인해 주실 것(백엔드 재시작 뒤)

72차 코드는 백엔드 재시작 뒤에 반영됩니다. 재시작은 별도 승인 대상입니다.

1. `.env.local`에 `VITE_USE_MOCK=false`가 있는지 확인해 주세요. `frontend/src/api/index.ts`는 값이 `'false'`가 아니면(값이 없을 때도) mock 모드입니다.
2. 리서치 화면에서 "삼성전자 오늘 왜 올랐어?"를 보내 주세요. 빈 출처 카드가 보이지 않고, 도구 칩과 (반영하셨다면) `message` 한 줄이 보여야 합니다.
3. 종목 상세 뉴스 탭에서 "마지막 수집"이 11-17로 보이는지 확인해 주세요(`last_successful_collection_at`을 반영하셨다면).
4. 모의 주문 뒤 체결가 옆에 기준(실시간/저장 종가)이 보이는지 확인해 주세요(반영하셨다면). 개발 DB 계정 대신 테스트 계정으로 해 주세요.
5. 두 종목 원인 답, 3개월 초과 원인 답처럼 긴 답이 모바일 폭에서 잘리지 않는지 확인해 주세요.
6. `sector`가 `—`로 보이는지 확인해 주세요.
