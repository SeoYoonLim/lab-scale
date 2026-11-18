# 65차 뉴스·공시 후처리(공시 본문·임베딩) 실행 준비 (2026-11-17)

**이번 차수는 조사와 실행 계획까지만 했다.**
- DART 원문 요청, Ollama 임베딩 호출, 개발 DB 쓰기는 하지 않았다(15절).
- 코드는 바꾸지 않았다. 이유는 13절에 있다.

요약:

| 작업 | 실제 대상 | 예상 시간 | 외부 호출 | DB 쓰기 |
| --- | ---: | ---: | --- | --- |
| DART 공시 본문 | **1,214건**(본문 없음 1,263 − 원문 없음 기록 49) | 약 5분(4~8분) | DART `document.xml` 1,214회 | `disclosure.content` UPDATE(NULL인 행만) |
| 공시 임베딩 | **1,214건** | 약 30초 | Ollama `bge-m3`(로컬) 32건 배치 38회 | `disclosure.embedding` UPDATE(NULL인 행만) |
| 뉴스 임베딩 | **112,787건** | 약 40~70분 | Ollama `bge-m3`(로컬) 32건 배치 3,525회 | `news.embedding` UPDATE(NULL인 행만) |

- 누락은 모두 63차 신규 행이다. 기존 행의 재처리는 필요 없다.
- 예외는 기존 공시 49건이다. 본문이 없지만 이미 "원문 없음(014)"으로 기록돼 있어 자동으로 건너뛴다.

## 1. 시작 브랜치와 기준 커밋

| 항목 | 값 |
| --- | --- |
| 브랜치·HEAD | `feature/backend`, HEAD = origin = `3bd2fec`. 작업 트리 깨끗함 |
| 63차 커밋 | `1b3a620`(복구 도구 `--relevant-only`)·`47c0226`(apply 보고서) 모두 HEAD 안에 있음 |
| 63차 보고서 | `docs/NEWS_DISCLOSURE_RECOVERY_APPLY_63.md` 확인(16절 후처리 추정을 이번에 실제 값으로 다시 계산함) |
| `backend/API.md` | 종목 뉴스·공시 목록(243·244행), 채팅 근거(`rag_search_tool` 등 902·908행), 잠정실적 표가 `disclosure.content`를 읽는 부분(1181행)을 확인했다. API 계약 변경 없음 |
| 백엔드 | **떠 있지 않다.** 11-17 09:02:38 재부팅 뒤 8000 포트에 프로세스가 없다(63차 1절). 이번에도 띄우지 않았다 |
| Ollama | PID 19152(`ollama`), 127.0.0.1:13434, v0.40.16. 설치 모델 `bge-m3:latest`(566.7M F16, 1.16GB), `llama3.1:8b`(채팅 기본값 `DEFAULT_CHAT_MODEL`), `qwen3:8b`, `qwen2.5:7b-instruct`. 지금 메모리에 올라간 모델 없음(`/api/ps` 빈 목록). 확인은 읽기 전용 `/api/tags`·`/api/ps`·`/api/version`만 썼다 |
| GPU | NVIDIA GeForce RTX 3050 8GB(조사 시점 사용 1.6GB, 30%) |
| Alembic | `c7e2a9f4d158` |
| 프런트엔드 | `lab-scale-frontend` 코드는 보지도 바꾸지도 않았다(영어 단일 언어 결정 유지). 그 저장소의 추적되지 않은 파일 2개(`package-lock.json` 등)도 건드리지 않았다 |

## 2. 뉴스 임베딩 누락

개발 DB를 READ ONLY 연결로 조회했다(11-17 10:58 KST).

| 항목 | 값 |
| --- | ---: |
| `news` 전체 | 130,640 |
| 임베딩 있음 | 17,853(63차 이전 행 전부) |
| **임베딩 없음** | **112,787** |
| 그중 63차 신규(id > 18,867) | 112,787(신규 전부) |
| 63차 이전 행 중 없음 | 0 |
| 임베딩 없음 행의 `is_relevant` | 전부 `true` |
| 제목 빈 행 / 본문 빈 행 / 둘 다 빈 행(전체) | 0 / 39 / 0 |
| 신규 행 제목+본문 길이 | 평균 161자, 최대 292자. 500자를 넘는 행 0(임베딩 입력이 잘리지 않음) |

**검색 가능 여부**
- 종목 뉴스 목록 API·`news_tool`·브리핑은 SQL 조회라 신규 기사를 **이미 쓴다**(63차 15절).
- RAG 벡터 검색(`rag_search_tool._top_unique`)은 `embedding IS NOT NULL` 행만 본다. 그래서 신규 112,787건은 **RAG에 나오지 않는다**.

## 3. 공시 본문 누락

| 항목 | 값 |
| --- | ---: |
| `disclosure` 전체 | 9,936 |
| 본문 있음 | 8,673 |
| **본문 없음**(`content IS NULL`, 빈 문자열은 0) | **1,263** |
| 그중 63차 신규(id > 8,722) | 1,214(신규 전부) |
| 63차 이전 행 | 49. 모두 `backups/disclosure_content_failures.json`에 `no_file:014`(DART에 원문 파일 없음)로 기록돼 있어 수집이 건너뛴다 |
| 접수번호(`source_url`의 `rcptNo`) 없음 / URL 없음 | 0 / 0 |
| 신규 중 잠정실적 공시(제목에 "잠정"·"실적") | 124. 전망 답변의 잠정실적 표가 `content`를 읽으므로(API.md 1181행), 본문을 받기 전에는 이 124건이 실적 표에 없다 |

## 4. 공시 임베딩 누락

| 항목 | 값 |
| --- | ---: |
| 임베딩 있음 | 8,722(63차 이전 행 전부) |
| **임베딩 없음** | **1,214**(63차 신규 전부) |
| 63차 이전 행 중 없음 | 0 |

## 5. 실제 처리 대상

| 구분 | 건수 |
| --- | ---: |
| 공시 본문 DART 호출 | 1,214 |
| 공시 임베딩 | 1,214 |
| 뉴스 임베딩 | 112,787 |
| **합계(신규 행 기준 처리 건수)** | **115,215** |
| 기존 데이터 재처리 필요 | **0**(기존 49건은 원문 없음 기록으로 건너뜀) |

두 경로로 확인했다.
- (1) DB 직접 집계
- (2) 임베딩 모듈의 dry-run(`embed_news(dry_run=True)`·`embed_disclosure(dry_run=True)`)
  - READ ONLY 연결로 돌렸고, Ollama 주소는 막아 두었다.
  - 결과 112,787·1,214가 (1)과 같다. 대상 행을 읽는 데 2.5초 걸렸다.

63차의 "본문 1,263회 요청" 추정은 실패 기록 49건을 빼지 않은 값이었다. 실제 호출 대상은 **1,214회**다.

## 6. DART 공시 본문 처리 구조

코드: `app/dart_documents.py`(`collect_disclosure_content`, `fetch_document_text`, `extract_text`)와 `scripts/collect_disclosure_content.py`.

| 항목 | 실제 동작 |
| --- | --- |
| 원천 | `GET https://opendart.fss.or.kr/api/document.xml?crtfc_key=…&rcept_no=…`. 성공하면 zip(안에 `접수번호.xml`)이 온다 |
| 접수번호 | `source_url`에서 `rcptNo=(\d+)`로 뽑는다. 없으면 `no_rcept_no`로 기록하고 호출하지 않는다(지금 0건) |
| 대상 | `content IS NULL AND source_url IS NOT NULL`, id 순서. 실패 기록 파일에 있는 id는 건너뛴다(`--retry-failed`면 다시 시도) |
| 이미 본문이 있는 행 | 대상이 아니다. **기존 본문을 덮어쓰지 않는다.** 행을 새로 만들지 않고 그 행의 `content`만 채우므로 중복 원문이 생기지 않는다 |
| 형식 처리 | zip이 아니면 응답 XML의 `<status>`를 읽는다. 013·014는 "원문 없음"(영구 실패, 기록), 020은 요청 제한(즉시 중단), 그 밖은 일시 실패(기록 안 함, 다음 실행에 다시 시도). 빈 zip·텍스트 없음은 `empty`(기록). 잘못된 zip은 일시 실패 |
| 본문 만들기 | zip의 첫 파일 앞 2MB를 읽는다. UTF-8 → EUC-KR 순으로 해독하고 태그·주석·스타일을 걷어낸다. 제목과 같은 앞머리와 서식 버전, 표지 정형 문구(수신처·회사명~전화번호 등)를 지운 뒤 **앞 2,000자**를 저장한다 |
| 재시도 | 요청 단위로 `http_retry` 최대 2회(연결·timeout·5xx만, 지수 대기). 4xx는 바로 실패 |
| 요청 간격 | 0.2초(`DEFAULT_DELAY`). DART 하루 한도는 약 2만 회(코드 주석 기준). 오늘 사용량은 63차 301회 + 본문 1,214회 = 약 1,515회 |
| DB 쓰기 | ORM으로 `row.content = text`. **50건마다 커밋**하고 실패 기록 파일을 저장한다. 예외가 나면 커밋 안 된 것만 롤백하고 기록 파일을 저장한다 |
| 재개 | 같은 명령을 다시 실행하면 `content IS NULL`인 행부터 이어서 한다. 상태 파일은 영구 실패만 담는다(`backups/disclosure_content_failures.json`, 지금 49건) |
| 과거 실측 | 853·891·493·168건을 각각 4.2~4.3건/초로 처리(10-14·10-28·10-31·11-03 `content` 단계) |

**주의 1: 이 스크립트의 dry-run(`--apply` 없음)은 DART를 실제로 호출한다.**
- 코드 주석에 "호출은 하되 DB에는 쓰지 않는다"고 적혀 있다.
- 그래서 이번 조사에서는 dry-run도 돌리지 않았다. 대상 수는 DB와 실패 기록 파일로 계산했다.

**주의 2: DART 인증·키 오류(010·011·012·901)는 일시 실패로 분류된다.**
- 멈추지 않고 대상 전체를 계속 요청한다(020만 멈춘다).
- 그래서 실행 계획 12절에서 먼저 `--limit 20` 시험 실행으로 키 상태를 확인한다.

## 7. Ollama 임베딩 처리 구조

코드: `app/embeddings.py`(`_embed_pending`, `embed_news`, `embed_disclosure`).

| 항목 | 실제 동작 |
| --- | --- |
| 모델 | `bge-m3`(`EMBEDDING_MODEL`, Ollama 태그 `bge-m3:latest`). 모델을 바꾸지 않는다 |
| 대상 표·열 | `news.embedding`, `disclosure.embedding`. 둘 다 pgvector `vector(1024)`, HNSW 코사인 인덱스(`idx_news_embedding_hnsw`·`idx_disclosure_embedding_hnsw`, m=16, ef_construction=64) |
| 대상 행 | `embedding IS NULL`, id 순서. **이미 임베딩이 있는 행은 건너뛴다.** 다시 만들기는 공시에만 있는 `--reembed-disclosure`(`reembed=True`)를 줄 때뿐이다. **그 옵션은 쓰지 않는다** |
| 입력 텍스트 | 뉴스 = `제목\n본문`(본문은 네이버 검색 요약)을 500자로 자른 값(`MAX_TEXT_LEN`). 공시 = `회사명 제목 (접수일 KST)`, 500자 이하. **공시 임베딩은 본문(`content`)을 쓰지 않는다**(코드 주석에 근거 실험 기록: 본문을 붙이면 top-10 적중이 78/80에서 70~73/80으로 떨어짐) |
| 청크 분할 | 없다. 행당 벡터 1개다. 신규 뉴스는 최대 292자라 자르기도 일어나지 않는다 |
| 최대 길이 | 500자로 자른다. 토큰 상한은 따로 두지 않았다. bge-m3 문맥(8,192 토큰)보다 훨씬 짧다 |
| 정규화 | 코드가 따로 하지 않고 Ollama `/api/embed` 결과를 그대로 저장한다. 기존 벡터의 노름은 1.0(뉴스 0.99999993, 공시 0.99999998)이다. 같은 모델·같은 엔드포인트라 신규도 같은 형식이 된다 |
| 배치 | 32건(`BATCH_SIZE`). `ollama.Client(timeout=120).embed(model, input=[32개], keep_alive=30m)` |
| 호출 사이 대기 | 없다. 배치를 연달아 보낸다 |
| 타임아웃·재시도 | 배치마다 120초 상한. 일시 오류(timeout·연결)는 `http_retry.call_with_retry`로 최대 2회 다시 시도 |
| 실패 처리 | 재시도 뒤에도 실패하면 예외를 내고 실행이 멈춘다. **배치마다 커밋**하므로 끝난 배치는 남는다. 행별 실패를 따로 기록하지 않는다(실패 행 = 그대로 NULL) |
| 재개 | 같은 명령을 다시 실행하면 NULL 행부터 이어서 한다. 상태 파일이 필요 없다. NULL 여부가 곧 상태다 |
| 진행 표시 | 배치마다 `임베딩 저장 완료: n/N건 (news) — r건/초` |
| 메모리 | 시작할 때 대상 행을 한 번에 읽는다(`query.all()`). 11만 행을 읽는 데 2.5초 걸렸다. 커밋 뒤 만료된 행은 접근할 때 한 건씩 다시 읽는다(SQLAlchemy 기본 `expire_on_commit`). 과거 실측 속도에 이 비용이 이미 들어 있다 |
| 과거 실측 | 3,875·4,218·3,495·2,620·2,542행을 각각 50·52·41·39·37행/초로 처리(10-14~11-03 `embed` 단계, 뉴스+공시) |

## 8. 배치 크기와 예상 시간

| 작업 | 배치 | 근거 속도 | 예상 |
| --- | --- | --- | --- |
| 공시 본문 | 요청 1건 + 0.2초, 50건마다 커밋 | 4.2~4.3건/초 | 1,214 ÷ 4.2 ≈ **4.8분**(응답이 느리면 8분) |
| 공시 임베딩 | 32건 | 37~52행/초 | **약 25~35초** |
| 뉴스 임베딩 | 32건 | 37~52행/초 | 112,787 ÷ 52 ≈ 36분 ~ ÷ 37 ≈ 51분. HNSW 인덱스가 1.3만 → 13만 벡터로 커지면 삽입이 조금 느려질 수 있어 **40~70분**으로 잡는다 [추정] |

**DB 크기 증가**(실측 단가)
- 지금 벡터 1개당 TOAST 약 5.7~8.0KB, HNSW 인덱스 약 6.6~7.1KB다(`news` 17,853개, `disclosure` 8,722개 기준).
- 11.4만 개면 약 **1.4GB** 늘어난다. DB는 지금 493MB이니 약 1.9GB가 된다 [추정].
- 본문 1,214건(평균 1,381자)은 수 MB 수준이다.
- 디스크 여유는 903GB다.

## 9. 중단 후 재개 가능성

| 상황 | 공시 본문 | 임베딩 |
| --- | --- | --- |
| Ctrl+C·전원 | 마지막 커밋(최대 50건 전) 뒤만 잃는다. 다시 실행하면 이어서 한다 | 마지막 커밋(최대 32건 전) 뒤만 잃는다. 다시 실행하면 이어서 한다 |
| DB 연결 끊김 | 예외 → 커밋 안 된 것만 롤백, 상태 파일 저장 → 다시 실행 | 예외 → 해당 배치만 롤백 → 다시 실행 |
| 외부 오류 | 020이면 멈추고 다음 날 이어서 한다. 일시 실패는 기록하지 않고 다음 실행 때 다시 시도한다 | Ollama가 꺼지거나 시간을 넘기면 재시도 2회 뒤 멈춘다. Ollama를 확인하고 다시 실행한다 |
| 이미 끝난 건 | `content IS NOT NULL`이라 대상이 아니다 | `embedding IS NOT NULL`이라 대상이 아니다 |
| 덮어쓰기 | 없음(NULL인 행만 씀) | 없음(`--reembed-disclosure`를 주지 않는 한) |
| 처리·실패 건수 | 끝날 때 요약 출력: 성공·원문 없음·빈 본문·일시 실패·건너뜀. 영구 실패는 상태 파일에 분리 | 진행 줄의 누적 건수. 실패는 NULL로 남은 행 수로 센다(12절 확인 쿼리) |

## 10. 백엔드 응답에 미치는 영향

**DB**
- UPDATE는 해당 행만 잠근다. 조회는 MVCC라 기다리지 않는다.
- 목록 API·`news_tool`은 영향이 거의 없다(34차 11절과 같은 판단).
- 처리 중에는 임베딩이 채워진 신규 행부터 RAG에 차례로 나타난다. 결과가 바뀌는 중간 상태가 생긴다.

**Ollama·GPU**
- 채팅(`llama3.1:8b`, 약 4.9GB)과 임베딩(`bge-m3`, 약 1.2GB)이 같은 Ollama(13434)와 같은 RTX 3050 8GB를 쓴다.
  - 두 모델을 함께 올리면 약 6.1GB에 KV 캐시가 더해져 여유가 적다.
  - 임베딩이 도는 동안 채팅 답변은 느려지거나 기다릴 수 있다. 모델을 내렸다 올리면 첫 답변이 수 초~수십 초 늦어질 수 있다 [추정].
- RAG 질의도 같은 `bge-m3`로 질문을 임베딩한다. 32건 배치 뒤에 줄을 선다.

**지금 상태**: 백엔드가 꺼져 있어 당장 사용자 영향은 없다. 백엔드를 다시 띄운 뒤 시연하려면 후처리를 시연 시간과 겹치지 않게 둔다.

**`refresh_data`로 실행할 때의 부작용**(12절에서 직접 실행을 권하는 이유)
- `refresh_data --only embed,content`는 실행 기록을 남긴다. 고르지 않은 prices·index·fx·news 단계도 `skipped` 행으로 남는다.
- `GET /api/status`의 `last_refresh`는 마지막 실행 한 번의 행만 보고, `failed` 단계가 없으면 `ok: true`다(`app/data_status.summarize_last_run`).
- 그래서 시세(최신 11-12)를 받지 않았는데도 `last_refresh`가 후처리 시각·`ok: true`로 바뀐다. 63차가 복구 기록에 별도 단계 이름을 쓴 것과 같은 이유다.

**부하 확인 방법**(실행 중, 읽기만)

```powershell
nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv -l 10      # GPU 사용률·VRAM
curl.exe -s http://127.0.0.1:13434/api/ps                                                 # Ollama에 올라간 모델·VRAM
Get-Counter '\Processor(_Total)\% Processor Time','\Memory\Available MBytes' -SampleInterval 10 -MaxSamples 6
Get-Process ollama,python | Select-Object Id,ProcessName,CPU,@{n='MB';e={[int]($_.WorkingSet64/1MB)}}
```

진행 확인(SQL, READ ONLY):

```sql
SELECT (SELECT count(*) - count(embedding) FROM news) AS news_left,
       (SELECT count(*) - count(embedding) FROM disclosure) AS disc_left,
       (SELECT count(*) FROM disclosure WHERE content IS NULL) AS content_left;
```

## 11. DB 백업과 복구 방안

**백업 필요**
- 쓰는 열은 `news.embedding`·`disclosure.embedding`·`disclosure.content` 세 개이고, NULL인 행만 채운다.
- 기존 값은 바뀌지 않지만 11만 행 UPDATE라 실행 직전 전체 백업을 권한다.
- 절차는 63차 2절과 같다:
  1. 컨테이너 안에서 `pg_dump -Fc`
  2. `docker cp`로 복사
  3. 양쪽 SHA-256 대조
  4. `pg_restore -l`로 목차 확인
- 새 이름을 쓰고 기존 백업을 덮어쓰지 않는다. 덤프 시간은 약 30초~1분이다(63차 148MB).
- 파일 이름의 시각은 PowerShell `Get-Date`로 KST를 쓴다(Git Bash `date`는 이 PC에서 UTC로 찍힌다).

**되돌리기**(필요할 때만, 별도 승인)
- 좁은 방법: 신규 행에 채운 값만 다시 NULL로 돌린다.
  - `UPDATE news SET embedding = NULL WHERE id > 18867`
  - `UPDATE disclosure SET embedding = NULL, content = NULL WHERE id > 8722`
  - 기존 행은 건드리지 않는다.
- 넓은 방법: 새 백업을 새 DB에 복원해 확인한 뒤 바꾼다. 기존 DB를 바로 덮어쓰지 않는다.
- 63차 백업(`ai_investment_backup_20261117_090913_pre_recovery63_full.dump`)은 63차 apply 전 상태라 후처리 되돌리기용으로는 맞지 않는다.

## 12. 실제 실행 명령(승인 뒤)

권장안은 **`refresh_data`를 거치지 않고 기존 모듈을 직접 차례로 실행**하는 것이다.
- `last_refresh` 뜻을 지킨다(10절).
- 단계별 제한 시간 문제가 없다. `refresh_data` 기본 30분이면 뉴스 임베딩 중간에 `StepTimeout`이 난다. 일시 오류로 분류돼 1회 재시도로 이어지지만, 기록에는 실패 뒤 재시도로 남는다.
- DART 호출 단계와 GPU 단계를 따로 정할 수 있다.
- 직접 실행은 advisory lock을 잡지 않는다. 그래서 그 시간에 다른 `refresh_data`가 돌지 않게 한다(스케줄러는 아직 없다).

```bash
cd /c/project/lab-scale/backend
export PYTHONIOENCODING=utf-8
# localhost는 이 PC에서 첫 연결이 약 10초 걸린다(63차 15절). 127.0.0.1로 바꾼 주소를 이 셸에서만 쓴다(.env는 그대로)
export DATABASE_URL="postgresql+psycopg://<사용자>:<비밀번호>@127.0.0.1:15432/ai_investment"
PY=.venv/Scripts/python.exe

# 0) 직전 백업(11절) + 진행 확인 쿼리(10절)로 시작 값 기록

# 1) 공시 본문 시험 20건(DART 20회, 키·형식 확인). 성공 수가 0이면 멈추고 원인을 본다
$PY scripts/collect_disclosure_content.py --apply --limit 20
# 2) 공시 본문 나머지(DART 약 1,194회, 약 5분)
$PY scripts/collect_disclosure_content.py --apply

# 3) 공시 임베딩(1,214건, 약 30초) — 작은 단계로 Ollama 쓰기를 먼저 확인
$PY -c "from app.embeddings import embed_disclosure; print(embed_disclosure(dry_run=False))"
# 4) 뉴스 임베딩(112,787건, 40~70분). 끊기면 같은 명령을 다시 실행
$PY -c "from app.embeddings import embed_news; print(embed_news(dry_run=False))"
#   (3)+(4)를 한 번에: $PY -m app.embeddings --apply   ※ --reembed-disclosure는 절대 붙이지 않는다

# 5) 확인: 진행 쿼리가 0·0·(원문 없음 기록 수), 신규 행 RAG 검색 표본, 기존 행 값 불변(아래)
```

**순서**
- 의존성은 없다. 공시 임베딩은 본문을 쓰지 않고, 뉴스 임베딩은 이미 있는 네이버 요약을 쓴다.
- 그래도 본문을 먼저 둔다. 짧고, 외부 요청이 있는 단계를 먼저 끝내 두면 Ollama 단계 중 DART 문제를 신경 쓸 필요가 없다.
- 순서를 바꿔도 결과는 같다.
- `refresh_data --only embed,content`는 고정 순서(`STEP_ORDER`)대로 **embed → content**로 돈다. 그래도 위 이유로 결과는 같다.

**`refresh_data`를 쓰기로 정한다면**(실행 기록을 남기고 싶을 때):

```bash
$PY -m scripts.refresh_data --only embed,content --timeout-min 90
```

- 실제 동작은 다음과 같다(dry-run으로 계획 확인, READ ONLY 연결).
  - advisory lock을 잡는다.
  - 끝나지 못한 이전 실행이 있으면 aborted로 표시한다(지금은 없음).
  - `data_refresh_run` 1행과 `data_refresh_log` 6행(embed·content 실행 + prices·index·fx·news skipped)을 남긴다. 선택 단계 etf_holdings는 고르지 않으면 기록하지 않는다(`refresh_data.py` 791행).
  - embed(`embed_news` → `embed_disclosure`, apply) → content(`collect_disclosure_content --apply`) 순서로 돈다. 단계별 상한은 90분이다.
  - 끝나면 품질 보고서(`logs/refresh/refresh_status.{json,md}`)를 쓴다.
- 장중(09:00~15:30)에 실행하면 경고가 나온다(시세 단계가 없어 영향은 없다).
- `last_refresh`가 바뀌는 점(10절)을 감수해야 한다.

**실행 뒤 확인 항목**
- 진행 쿼리 `news_left = 0`, `disc_left = 0`, `content_left` = 실패 기록 수(49 + 이번 영구 실패)
- 기존 행 불변: 63차 방식으로 `id ≤ 18867`(뉴스)·`id ≤ 8722`(공시) 행 md5가 전후 같음(임베딩·본문 포함)
- 신규 벡터 차원 1024, 노름 1.0 표본
- RAG 표본: `rag_search_tool`로 11-04 이후 사건(예: "삼성전자 11월 반도체 급락", "NH투자증권 공시")에서 신규 행이 나오는지
- 잠정실적 124건 본문 채움 확인
- 실패 기록 파일의 새 항목
- `pg_stat_user_tables`: `news`·`disclosure` UPDATE 수 = 채운 행 수, DELETE 0

## 13. 별도 승인이 필요한 작업과 코드 변경 판단

**승인이 필요한 작업**(이번에 하지 않음):

| # | 작업 | 외부 호출 | DB 쓰기 |
| --- | --- | --- | --- |
| 1 | 실행 직전 백업(`pg_dump`) | 없음 | 없음(읽기) |
| 2 | 공시 본문 시험 20건 → 나머지 | DART 1,214회 | `disclosure.content` 1,214행 이하 |
| 3 | 공시 임베딩 | Ollama 38배치 | `disclosure.embedding` 1,214행 |
| 4 | 뉴스 임베딩 | Ollama 3,525배치 | `news.embedding` 112,787행 |
| 5 | (선택) 끝난 뒤 `VACUUM (ANALYZE) news, disclosure` | 없음 | 유지보수(죽은 행 약 11.4만 정리). 자동 vacuum도 처리하지만 즉시 통계를 맞추려면 |
| 6 | 실행 방식 결정 | — | 직접 실행(권장) 또는 `refresh_data --only embed,content --timeout-min 90` |

**코드는 바꾸지 않았다.** 찾은 점은 운영 절차로 피할 수 있어 이번 범위에서 고칠 필요가 없다고 판단했다.

| 찾은 점 | 영향 | 이번 대응 |
| --- | --- | --- |
| 본문 수집 dry-run이 실제로 DART를 호출 | 조사용으로 쓸 수 없음 | 쓰지 않음(DB·상태 파일로 계산) |
| DART 010·011 등 인증 오류가 일시 실패로 분류돼 멈추지 않음 | 키 문제면 1,214회 헛호출 | `--limit 20` 시험 실행으로 먼저 확인 |
| `refresh_data` 부분 실행이 `last_refresh`를 바꿈 | 시세가 오래됐는데 최근 갱신 성공으로 보임 | 직접 실행 권장 |
| `refresh_data` 단계 제한 기본 30분 | 뉴스 임베딩이 시간 초과 → 재시도 | 직접 실행, 또는 `--timeout-min 90` |
| `.env`의 `localhost` 연결 지연 | 새 연결마다 약 10초 | 셸에서만 `127.0.0.1` 주소 사용 |

고칠지는 다음 차수에 정한다(16절).

## 14. 테스트 결과

| 범위 | 결과 |
| --- | --- |
| `test_embeddings.py`·`test_dart_documents.py`·`test_refresh_data.py`·`test_data_refresh_ops.py` 단위(통합 제외) | 74 통과 |
| 위 + `test_data_refresh_ops_devdb.py`(통합) | **158 통과**. 격리 DB `ai_investment_apply63_it`, 외부 HTTP 차단 상태(테스트 설정) |
| 임베딩 dry-run(READ ONLY, Ollama 주소 차단) | 대상 112,787·1,214, 오류 없음 |
| `refresh_data --only embed,content --timeout-min 90 --dry-run`(READ ONLY) | 종료 0, 계획: embed·content 실행, 나머지 skipped, 단계별 90분 |

코드 변경이 없어 새 테스트는 없다. CI는 이 보고서 커밋에서 확인한다(최종 응답에 결과).

## 15. 개발 DB 불변 확인

| 항목 | 결과 |
| --- | --- |
| 방법 | 직전 작업(11-16 분봉 보존) 종료 때인 10:23 스냅샷과 이번 조사 끝(11:02) 스냅샷을 비교했다. 23개 표의 행 수·md5(전 행 텍스트), `pg_stat_user_tables` 누적 INSERT·UPDATE·DELETE |
| 결과 | 23개 표 모두 같음. `pg_stat` 변화 없음 |
| `news` / `disclosure` md5 | `c6ef3e5c957cc54b916aaa3c8ca97db6` / `592fc75933124c9369bb0de881b8a609`(63차 apply 직후 값과 같음) |
| Alembic / `stock_price_session` | `c7e2a9f4d158` / 없음 |
| 연결 | 모든 조회는 `SET TRANSACTION READ ONLY` 또는 `default_transaction_read_only=on` 연결 |
| 외부 호출 | DART 0회. Ollama는 읽기 전용 목록 조회(`/api/tags`·`/api/ps`·`/api/version`)만, 임베딩 호출 0회 |
| 파일 | `backups/disclosure_content_failures.json` 그대로(11-11 16:40 수정, 49건) |
| 그 밖 | 마이그레이션·스케줄러 등록·Ollama 설정 변경·백엔드 기동·프런트엔드 변경·평가 세트 변경·시험 DB 삭제 없음 |

## 16. 다음 작업 우선순위

1. **후처리 실행 승인**(12절 0)~5)): 공시 본문 → 공시 임베딩 → 뉴스 임베딩.
   - 권장 시각: 시연·채팅 사용이 없는 시간. 본문은 언제든 되고(5분), 뉴스 임베딩(40~70분)은 밤이나 주말이다.
   - 백엔드가 지금 꺼져 있으니, 다시 띄우기 전에 끝내면 채팅 영향이 없다.
2. **백엔드 기동 방식**: `DATABASE_URL` 호스트를 `127.0.0.1`로 쓸지(63차 20절 4). 후처리 뒤에 띄우면 RAG가 신규 데이터를 바로 쓴다.
3. **정기 자동화 등록**(62차 계획 10절, 등록 안 함):
   - 평일 17:00 전체, 19:00 공시 보강(`--source dart,embeddings,disclosure_content`), 21:00 `--retry-failed`, 08:30 `--status`, 주말 12:00 뉴스(`--source news,embeddings --news-only`), 일 03:00 `--prune-logs`.
   - 후처리를 하기 전에 등록하면 19:00 작업이 11만 건 임베딩을 30분 상한으로 시작한다. 그래서 **후처리 확인 뒤** 등록한다.
   - 등록 전에 63차 20절의 남은 결정(관련성 낮음 10,128건, 잘린 58종목)을 정리한다.
4. (선택, 코드) 13절 표의 개선 후보:
   - 본문 수집의 DART 인증 오류 즉시 중단
   - `refresh_data` 부분 실행이 `last_refresh`를 바꾸지 않게 하기
5. 11-17~11-19 분봉 보존(각 거래일 20:10 뒤, 2026-11-16 보고서 9절).
