# 2026-04-22 Stage 1-A 재발 — 문자열 값 내부 ```json 펜스 삽입으로 파싱 실패

- **발생 일시**: 2026-04-22 09:30:24 KST (쿼리 시작 09:21:14, 총 550초)
- **대상 스테이지**: Stage 1-A (이슈 분석 + 테마 발굴)
- **모델**: `claude-sonnet-4-6`
- **상태**: ✅ 해결됨 (커밋 pending)
- **관련 아카이브**: `ai_query_archive` #27 (first chunk 20,161자 / 총 23,198자)
- **선행 사례**: [20260422_stage1a_json_parse_failure.md](20260422_stage1a_json_parse_failure.md) — 1차 개선 후 재발

## 증상

```
[2026-04-22 09:30:24] 분석 WARNING JSON 파싱 실패: Unterminated string starting at: line 370 column 22 (char 20151)
[2026-04-22 09:30:24] 분석 INFO JSON 전처리(sanitize) 시도 중...
[2026-04-22 09:30:24] 분석 INFO 전처리 후에도 파싱 실패 — 잘린 JSON 복구로 진행: Expecting ',' delimiter: line 370 column 30 (char 20159)
[2026-04-22 09:30:24] 분석 INFO 잘린 JSON 복구 시도 중...
[2026-04-22 09:30:24] 분석 ERROR JSON 복구 실패
[2026-04-22 09:30:25] 파이프라인 ERROR [Stage 1-A] 실패
```

아카이브 원문에서 6번째 테마 `fed_independence_rate_trajectory`의 `description` 필드가 다음과 같이 망가져 있었음:

```
"theme_name": "연준 독립성 재확인 — ...",
"description": "일```json
빕 상원 연준 의장 후보의 금리 독립 선언은 ...
```

- `"description": "` 로 문자열을 열고 `"일"` 한 글자만 쓴 뒤
- 문자열 값 **내부에** ` ```json\n ` 리터럴을 삽입
- 이후 한국어 산문으로 설명을 이어 쓴 뒤 닫는 `"` + 외부 펜스 ` ``` ` 배치
- 추가 해설 텍스트 + **두 번째** ` ```json ` 블록 (`themes_continued` 래퍼) + 최종 해설까지 출력

총 4개 청크로 수신 (`+0 / +20,161 / +0 / +3,037` — #2와 #4 사이 ~27초 공백).

## 근본 원인

### 1. 직접 원인
모델이 JSON 문자열 값 내부에 ` ```json ` 마크다운 펜스를 **리터럴로** 삽입. `re.findall(r"```json\s*(.*?)```", ...)` 가 이 내부 펜스를 코드블록 종결로 오인하여 첫 블록이 `"description": "일` 위치에서 잘린 상태로 추출됨. 이어 `_sanitize_json_response`가 잘린 첫 블록과 두 번째 블록(themes_continued)을 `"".join()` 으로 단순 연결 → 열린 문자열 안에 `{`·`"` 등이 쑤셔 들어가 구조가 꼬임.

### 2. 구조적 원인
- `STAGE1A_PROMPT`가 이슈 8~10건 + 테마 4~7개 + 시나리오 3개 × 다수 필드 + macro_impacts를 **한 번의 쿼리**로 생성 요구 → 평균 20KB+ 출력.
- 기존 `_sanitize_json_response`의 multi-block 병합 경로는 "Part 1/2 식 정상 분할"만 전제 — **"값 내부에 펜스가 잘못 삽입된 탈선 패턴"** 에 대응 없음.
- 기존 `_try_fix_truncated_json`은 응답 **끝이** 깔끔히 잘린 경우만 처리 → 문자열 중간에서 다른 컨텍스트가 붙은 상태는 범위 밖.

### 3. 근본 원인
- 긴 JSON 출력 시 Claude가 보이는 **self-interruption 행동**: "여기까지 응답하고 이어서 continuation block으로 계속" 이라는 helper-style 메타 해설을 자발적으로 삽입 → 프롬프트 규율만으로 완전 차단 어려움.
- 청크 #2(20,161자) → 27초 공백 → 청크 #4(3,037자) 수신 패턴이 근거.

## 수정 사항

### 프롬프트 레이어
- [analyzer/prompts.py:49-56](../../analyzer/prompts.py#L49-L56) — `STAGE1_SYSTEM` "출력 형식 엄수" 섹션에 규칙 5·6번 추가:
  - **5**: JSON 문자열 값 내부에 ` ``` ` / ` ```json ` / 백틱 삽입 금지 (`'` 또는 「」로 인용)
  - **6**: self-interruption ("이어서 계속") 금지
- [analyzer/prompts.py:266-277](../../analyzer/prompts.py#L266-L277) — `STAGE1A_PROMPT` 테마 상한을 4~7 → 4~6(권장 5)로 축소, description 2~3문장, 시나리오 설명 1문장, key_indicators 4개 고정, macro_impacts 2개로 제한.

### 파서 레이어
- [analyzer/analyzer.py:65-84](../../analyzer/analyzer.py#L65-L84) — 신규 `_has_unterminated_string()` 헬퍼 추가. 텍스트가 JSON 문자열 값 내부에서 끝났는지 상태머신으로 판정.
- [analyzer/analyzer.py:87-120](../../analyzer/analyzer.py#L87-L120) — 신규 `_trim_to_last_complete_array_item()` 헬퍼. 깊이 기반 스캔으로 배열 내 마지막 완전 닫힌 `}` 직후까지 절단 → 부분 객체 드롭.
- [analyzer/analyzer.py:123-141](../../analyzer/analyzer.py#L123-L141) — `_try_fix_truncated_json()` 를 개선. 미종료 문자열 감지 시 마지막 완전 item 직후로 절단 → 반쪽짜리 theme/issue 자동 제거.
- [analyzer/analyzer.py:232-243](../../analyzer/analyzer.py#L232-L243) — `_sanitize_json_response()` 에서 multi-block 감지 후 첫 블록이 **미종료 문자열**이면 병합 차단하고 첫 블록만 사용. (병합 시 오히려 구조 파괴 방지)
- [analyzer/analyzer.py:280-314](../../analyzer/analyzer.py#L280-L314) — 신규 `_drop_partial_items()` 도입. 복구 성공 후 필수 필드(`theme_key/theme_name/description/time_horizon`, `category/title/summary/impact_short`)가 빠진 항목을 자동 제거하여 다운스트림 검증기 통과.

### 스테이지 분할 레이어 (구조 변경)
- [analyzer/prompts.py:357-468](../../analyzer/prompts.py#L357-L468) — 신규 `STAGE1A1_PROMPT`(이슈만) / `STAGE1A2_PROMPT`(테마만) 추가.
- [analyzer/analyzer.py:572-610](../../analyzer/analyzer.py#L572-L610) — 신규 `stage1a1_analyze_issues` / `stage1a2_build_themes` 함수.
- [analyzer/analyzer.py:667-741](../../analyzer/analyzer.py#L667-L741) — 기존 `stage1a_discover_themes` 를 오케스트레이터로 교체. 내부적으로 A1→A2를 순차 호출, 반환 스키마는 기존과 동일하여 `run_pipeline` 무수정.
- 예상 효과: 각 쿼리 출력 ≤12KB → self-interruption 조건 미달. 분석 품질은 이슈↔테마가 별도 쿼리로 **더 집중**되어 오히려 개선 기대.

### 아카이빙 재분석 레이어 (운영)
- [analyzer/replay.py](../../analyzer/replay.py) — 신규 CLI. `python -m analyzer.replay --id N` 으로 `ai_query_archive` 원문을 현재 파서로 재분석 / `--list-failed` 로 실패 아카이브 최근 20건 조회 / `--dump-json` 으로 복구 결과 저장.

## 검증

### 단위 레벨 smoke test (수행 완료)
```python
# 재발 패턴 재현: 첫 블록 description 안에 ```json 펜스 + 두 번째 블록
from analyzer.analyzer import _parse_json_response
# ... fake multi-block with unterminated string in first block's description
r = _parse_json_response(fake)
# 결과: parse_status=truncated_recovered, issues=1, themes=2 (부분 테마 t3 자동 드롭)
```

### 정상 JSON 경로 유지 확인 (수행 완료)
```python
# 정상 단일 코드블록은 fast path 그대로 → parse_status=success
```

### 후속 실배치 검증 필요
- 다음 03:00 systemd 배치 혹은 admin 페이지 수동 실행 → `parse_status` 분포 확인
- `stage1a1`·`stage1a2` 로그가 각각 10~12KB 이하, 완결되는지 모니터링
- 청크별 수신 타임라인에 27초+ 공백이 재발하는지 관찰

## 후속 모니터링
- **관찰 지표**: `ai_query_archive` 테이블에서 stage별 `response_chars` 분포 + `parse_status` 분포. `stage1a1`/`stage1a2` 분리 후 `stage1a` 단일 쿼리는 사라져야 정상.
- **알림 조건**: `parse_status IN ('failed','empty')` 가 주 1건 이상 발생하거나, `truncated_recovered` 가 월 5건 이상 누적되면 프롬프트/모델 재점검.
- **재발 시 에스컬레이션**: `python -m analyzer.replay --list-failed` 로 실패 목록 확인 → `--id N` 으로 원문 재분석 → 새 패턴이면 본 리포트 형식으로 신규 예외 문서 생성.
