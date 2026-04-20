# C2.1 — _macros.html 분할 검증 완료 메모

- **일자**: 2026-04-20
- **스펙**: `docs/superpowers/specs/2026-04-20-c2-1-macros-split-design.md`
- **플랜**: `docs/superpowers/plans/2026-04-20-c2-1-macros-split.md`

## 결과 요약

| 항목 | 분할 전 | 분할 후 |
|---|---|---|
| 파일 수 | 1 (`_macros.html` 655줄) | 3 (`_macros/*.html`) |
| 매크로 수 | 20 | 20 (동일) |
| 최대 파일 라인 | 655 | 250 (`proposal.html`) |
| 외부 참조 템플릿 | 6 | 6 (import 경로만 교체) |

## 파일별 담당

| 파일 | 라인 | 매크로 수 | 매크로 이름 |
|---|---:|---:|---|
| `_macros/proposal.html` | 250 | 6 | proposal_card_compact, proposal_card_full, krx_badges, change_indicator, risk_gauge, bullet_chart |
| `_macros/theme.html` | 218 | 9 | theme_header, scenario_grid, indicator_tags, macro_impact_table, conf_ring, discovery_donut, sector_bubbles, discovery_stackbar, sector_chips |
| `_macros/common.html` | 197 | 5 | market_summary_body, grade_badge, external_links, sparkline, yield_curve |

## 검증 통과 항목

- [x] 매크로 이름 셋 parity — 원본 20개 모두 3개 파일에 분배
- [x] Jinja2 pre-load — 9개 템플릿 오류 없이 로드
- [x] `grep -rn "_macros\.html" api/` 활성 참조 0건 (주석만 존재)
- [x] `api.main import` 성공
- [x] `pytest tests/` 신규 실패 0건 (17 failed, 52 passed — 분할 전과 동일)

## 작업 커밋 (4개)

```
7e2cb8e refactor(tpl): C2.1 — 구 _macros.html 삭제
3e53716 refactor(tpl): C2.1 — 6개 템플릿 import 경로 교체 (_macros → _macros/*)
45adf2e feat(tpl): C2.1 — _macros/ 패키지 생성 (proposal/theme/common)
b87ae67 docs(refactor): C2.1 — _macros.html 분할 구현 계획서 (4 Tasks)
a71b09e docs(refactor): C2.1 — _macros.html 분할 설계 문서 (3개 도메인 파일)
```

## 크로스 파일 호출 해결

- `theme_header → grade_badge`: `_macros/theme.html` 상단 `{% from "_macros/common.html" import grade_badge %}` 로 해결

## 남은 관심사 (후속 트랙 C2.2 / C3)

- **C2.2**: 긴 템플릿 partial 추출 (`dashboard.html` 658줄, `admin.html` 486줄 등)
- **C3**: 인라인 JavaScript 분할 (`base.html`의 fetch 인터셉터)

## 다음 단계

**C2.2** (긴 템플릿 partial 추출) 브레인스토밍 → 구현.
