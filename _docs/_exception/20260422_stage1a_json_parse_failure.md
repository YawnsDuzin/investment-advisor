# 2026-04-22 Stage 1-A JSON 파싱 실패

- **발생 일시**: 2026-04-22 03:13:54 (배치 타이머 자동 실행)
- **대상 스테이지**: Stage 1-A (이슈 분석 + 테마 발굴)
- **모델**: `claude-sonnet-4-6`
- **상태**: 해결됨 (2026-04-22)
- **관련 아카이브**: `query_25_stage1a_2026-04-22.txt` (parse_status=failed, 26,638자, 749.81초)

---

## 증상

```
[2026-04-22 03:13:54] 분석 WARNING JSON 파싱 실패:
    Invalid control character at: line 158 column 66 (char 9848)
[2026-04-22 03:13:54] 분석 INFO 잘린 JSON 복구 시도 중...
[2026-04-22 03:13:54] 분석 ERROR JSON 복구 실패
[2026-04-22 03:13:54] 파이프라인 ERROR [Stage 1-A] 실패
[2026-04-22 03:13:54] main ERROR 분석 실패
```

파이프라인 전체가 Stage 1-A에서 중단되어 당일 세션이 DB에 저장되지 못했다.

---

## 근본 원인

### 1. 직접 원인 — JSON 문자열 값 안의 raw 제어문자

이슈 12(러시아 정유소 드론 공격)의 `impact_short` 필드에 모델이 자기 주석과 raw 개행을 삽입:

```json
"impact_short": "러시아산 원유 수*(issue 12 `impact_short` 이하 계속)*

출 감소 우려로..."
```

- `*(issue 12 impact_short 이하 계속)*` ← 값 안의 메타 주석
- 뒤따르는 **raw `\n\n`** (이스케이프 되지 않은 개행 2개) ← char 9848의 control character

### 2. 구조적 붕괴 — JSON을 3개 블록으로 분할

모델이 출력 도중 "파트로 나눠서 줄게" 모드로 전환:

```
```json
{ "analysis_date": ..., "issues": [...], ← 닫힘 없이 중단
```

**[테마 Part 1 — 1~3번]**

```json
  "themes": [ ...1~3번 ]
```

**[테마 Part 2 — 4~7번]**

```json
    ...4~7번 테마 }
```
```

정규 JSON 문서로 복원 불가능한 형태. 기존 `_try_fix_truncated_json()` 복구 로직이 대응할 수 없다.

### 3. 근본 원인 — 출력 토큰 한계 근접

- 총 응답 **26,638자 / 749초**
- 응답 청크가 425s → 560s → 576s → 749s로 누적
- 프롬프트가 요구한 분량(이슈 8~15건 × 10필드 + 테마 4~7개 × 시나리오 3개 × 매크로 2~3개)이 너무 많아 모델이 중간에 포맷 규율을 놓침

---

## 수정 사항

### Layer 1: 시스템 프롬프트에 출력 규율 섹션 추가

[analyzer/prompts.py](../../analyzer/prompts.py) `STAGE1_SYSTEM`에 다음 5개 조항 추가:

1. 응답은 반드시 단일 ` ```json ` 코드블록 하나로만 출력
2. JSON 바깥에 어떤 텍스트도 출력 금지 (마크다운 헤더 포함)
3. 모든 문자열 값은 한 줄로 작성, 줄바꿈 필요 시 `\n` 이스케이프
4. 값 안에 `(이하 계속)`, `(issue N ...)` 같은 메타 주석 금지
5. 분량이 길어지면 하한선(이슈 8건, 테마 4개)으로 낮출 것 — 포맷 유지 우선

### Layer 2: 출력 분량 상한 명시

[analyzer/prompts.py](../../analyzer/prompts.py) `STAGE1A_PROMPT` 변경:

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| 이슈 수 | 8~15건 | **8~10건** |
| `impact_short/mid/long` | 제한 없음 | **각 2문장 이내** |
| 시나리오 `description` | 제한 없음 | **1~2문장** |
| `key_indicators` | 제한 없음 | **4~6개** |

JSON 템플릿의 필드 힌트 문자열에도 문장 수 제약을 삽입하여 모델에 이중으로 전달.

### Layer 3: 파서 전처리 강화

[analyzer/analyzer.py](../../analyzer/analyzer.py)에 두 함수 신규 추가:

- `_escape_control_chars_in_strings()` — 문자열 값 내부의 raw `\n`, `\r`, `\t`만 이스케이프 (상태머신으로 구조 문자 보호)
- `_sanitize_json_response()` — 파싱 전 전처리 파이프라인:
  1. 여러 개의 ` ```json ` 블록을 하나로 연결 (Part 1/2 대응)
  2. 마크다운 헤더(`**[...]**`) 제거
  3. 자기주석 패턴(`*(issue N ...)*`, `(이하 계속)`) 제거
  4. 문자열 값 내부 제어문자 이스케이프

`_parse_json_response()`가 1차 파싱 실패 시 전처리 후 재파싱을 시도하고, 그래도 실패하면 기존 `_try_fix_truncated_json()` 복구 경로로 폴백한다. 복구 경로에 따라 `_parse_status`가 `sanitized_recovered` / `truncated_recovered` / `failed`로 구분 기록된다.

---

## 검증

로컬 테스트에서 3가지 실패 패턴(쪼개진 블록 + 마크다운 헤더 + 자기주석 + raw 개행)을 모두 포함한 샘플이 전처리 후 정상 파싱됨을 확인.

```bash
python -c "from analyzer.analyzer import _sanitize_json_response; ..."
# → OK issues=1 themes=1
```

---

## 후속 모니터링

1. 다음 배치(2026-04-23 03:00)에서 Stage 1-A 정상 완료 여부 확인
2. `app_logs` 테이블에 `_parse_status="sanitized_recovered"` 건수 주기적 점검 (전처리가 자주 발동하면 프롬프트 추가 조정 필요)
3. 만약 실패가 재발하면 **Stage 1-A를 1-A1(이슈) + 1-A2(테마)로 분할**하는 중기 계획으로 이행 (현재 계획서 보류 상태)
