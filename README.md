# lab-scale

## Known Issues / TODO (tool-calling 신뢰도, llama3.1:8b)

`app/agent.py`의 tool 선택 신뢰도 관련 미해결 이슈. 재현/벤치마크 방법은
`backend/scripts/benchmark_models.py`, `backend/scripts/test_tool_calling.py` 참고.

- **no_tool 남발**: "주식 기본 용어 알려줘" 같은 개념성 질문에도 매번 불필요하게
  tool을 호출함 (반복 테스트 기준 0/5 ~ 6/6 실패 유지, rag_search_tool 도입 전엔
  stock_tool/disclosure_tool을, 도입 후엔 주로 rag_search_tool을 잘못 호출).
  답변 내용 자체는 안전(사실 왜곡 없음)하고, DB 조회 낭비(지연/비용)만 있는
  효율성 문제. 우선순위 낮음 — 아래 "4-tool 프롬프트 재설계"와 함께 처리.
- **특정 주제 질문이 rag_search_tool 대신 disclosure_tool로 라우팅됨**:
  "삼성전자 자사주 매입 관련 공시 내용 자세히 알려줘"류 질문은 rag_search_tool이
  적합한데, "공시"라는 단어가 disclosure_tool의 트리거 문구와 겹쳐서 대부분
  disclosure_tool(최신순 5건)로 감 (반복 테스트 기준 10회 중 1회만 rag_search_tool
  성공). disclosure_tool도 실제 DB 데이터를 반환하므로 사실 왜곡은 없지만, 의도한
  의미 기반 검색이 아니라서 답변 완성도가 떨어질 수 있음.
- **rag_search_tool 도입 시 SYSTEM_PROMPT/few-shot 국소 수정만으로는 위 이슈를
  못 고침**: disclosure_tool 트리거 문구와 안 겹치게 SYSTEM_PROMPT를 다듬고
  few-shot 예시(LG에너지솔루션)를 추가해봤지만, 목표 케이스는 거의 개선 안 됐고
  (0/5 -> 1/10) 오히려 기존에 안정적이던 multi-tool 케이스(stock_tool+news_tool
  동시 호출)가 80%대에서 50%로 떨어지는 회귀가 발생 (일부는 종목명이 깨진
  문자열로 환각되기도 함). 해당 변경은 되돌렸고, 현재 SYSTEM_PROMPT/
  FEW_SHOT_MESSAGES는 rag_search_tool 도입 이전과 동일 (TOOLS/AVAILABLE_FUNCTIONS
  등록만 유지) — 되돌린 뒤 multi-tool 케이스는 100%(5/5)로 복구 확인.
- **결론 / 다음 작업**: tool이 3개 -> 4개로 늘면서, 로컬 8B 모델 하나에
  국소적인 프롬프트 패치를 반복하는 방식은 한 곳을 고치면 다른 곳이 깨지는
  패턴이 반복됨 (whack-a-mole). 다음에 tool을 더 추가하기 전에 SYSTEM_PROMPT/
  TOOLS/FEW_SHOT_MESSAGES 전체를 한 번에 재설계하고,
  `scripts/benchmark_models.py` 같은 재현 가능한 벤치마크로 검증할 것. 위
  두 이슈(no_tool 남발, 공시 라우팅)는 그때 함께 처리.