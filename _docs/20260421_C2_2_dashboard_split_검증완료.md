# C2.2 — dashboard.html 분할 검증 완료 메모

- **일자**: 2026-04-21
- **스펙**: `docs/superpowers/specs/2026-04-21-c2-2-dashboard-split-design.md`
- **플랜**: `docs/superpowers/plans/2026-04-21-c2-2-dashboard-split.md`

## 결과 요약

| 항목 | 분할 전 | 분할 후 |
|---|---|---|
| 파일 수 | 1 (`dashboard.html` 660줄) | 8 (오케스트레이터 1 + partial 7) |
| 오케스트레이터 라인 | 660 | 104 (84% 감소) |
| 매크로 호출 위치 | dashboard.html (13건) | 7개 partial로 분산 (13건 동일) |
| 매크로 import 라인 | 4 (dashboard.html 내부) | 6 (각 partial 내부, 의존 매크로만) |

## 파일별 담당

| 파일 | 라인 | 매크로 의존 |
|---|---:|---|
| `dashboard.html` (오케스트레이터) | 104 | (본문 직접 호출 없음, ad_slot만 import 유지) |
| `partials/dashboard/_hero_row1.html` | 168 | change_indicator |
| `partials/dashboard/_hero_row2.html` | 75 | risk_gauge, discovery_stackbar, sector_chips |
| `partials/dashboard/_market_summary.html` | 63 | risk_gauge |
| `partials/dashboard/_yield_curve.html` | 41 | yield_curve |
| `partials/dashboard/_themes_list.html` | 82 | theme_header, indicator_tags |
| `partials/dashboard/_top_picks.html` | 186 | bullet_chart, conf_ring, external_links |
| `partials/dashboard/_news_by_category.html` | 60 | (없음) |
| **합계** | **779** | |

## 검증 통과 항목

- [x] 9개 템플릿 Jinja2 pre-load 무오류 (dashboard.html, base.html, partials/dashboard/_*.html × 7)
- [x] `python -c "import api.main"` 무오류
- [x] `pytest tests/` 신규 실패 0건 — baseline 동일 (10 failed, 59 passed, 6 errors)
- [x] `dashboard.html` 매크로 직접 호출 0건 (분할 전 13건이 모두 partial로 이동)
- [x] partial 매크로 호출 합계 13건 == 분할 전 dashboard.html 매크로 호출 13건
- [x] include 호출 7건이 모두 `partials/dashboard/_*.html` 경로

## 매크로 호출 분산 검증 (분할 전 13건 → partial 7개로 정확 매칭)

| Partial | 매크로 호출 수 |
|---|---:|
| `_hero_row1.html` | 3 (change_indicator × 3) |
| `_hero_row2.html` | 3 (risk_gauge, discovery_stackbar, sector_chips × 1) |
| `_market_summary.html` | 1 (risk_gauge) |
| `_yield_curve.html` | 1 (yield_curve) |
| `_themes_list.html` | 2 (theme_header, indicator_tags) |
| `_top_picks.html` | 3 (bullet_chart, conf_ring, external_links) |
| `_news_by_category.html` | 0 |
| **합계** | **13** ✓ |

## 수동 스모크 (별도 확인 권장)

- [ ] `python -m api.main` 기동 후 `/pages/dashboard` 로드, 콘솔 에러 0건
- [ ] Market Summary 접기/펼치기 동작
- [ ] Track Record 탭(1M/3M/6M/1Y) 전환 동작
- [ ] (로그인) 워치리스트 토글 동작 (★ ↔ ☆)
- [ ] 로그인/비로그인 두 경우 모두 정상 렌더

## 작업 커밋 (4개)

```
8f8d3e7 refactor(tpl): C2.2 — dashboard.html을 include 기반 오케스트레이터로 축소
42149da feat(tpl): C2.2 — partials/dashboard/ 7개 파일 생성 (VERBATIM 이동)
48a4fdf docs(refactor): C2.2 — dashboard.html 분할 구현 계획서 (3 Tasks)
4aa5397 docs(refactor): C2.2 — dashboard.html 분할 설계 문서 (7개 섹션 partial 추출)
```

(이 메모는 별도 commit으로 추가됨.)

## 핵심 결정 회고

- **`{% include %}` vs 매크로**: include 채택. 컨텍스트 자동 상속으로 호출부 단순화. 페이지 전용 섹션이라 매크로의 명시적 인자 장점이 없었음.
- **`partials/dashboard/` 서브디렉터리**: 기존 `partials/` 5개(크로스 페이지 재사용)와 명확히 구분. C2.3+ 확장(`partials/admin/` 등)에 동일 패턴 적용 가능.
- **각 partial 내부에서 매크로 자체 import**: 파일 독립성(partial만 열어봐도 의존 파악) 우선. C2.1의 `_macros/theme.html`이 `grade_badge`를 자체 import한 원칙과 동일.
- **JS는 C2.2 범위 외**: Track Record IIFE는 `_hero_row1.html`에 위젯과 함께 이동. 모바일 collapse + toggleWatchlist는 dashboard.html `{% block scripts %}`에 잔류. 외부 파일 분리는 C3 트랙.

## 남은 관심사 (후속 트랙)

- **C2.3** (admin.html 486줄 분할): 동일 패턴 적용. `partials/admin/_<section>.html`.
- **C2.4** (user_admin.html 424줄 분할): 동일 패턴.
- **C2.5+** (proposals.html 368줄, stock_fundamentals.html 349줄, base.html 325줄 검토)
- **C3** (인라인 JavaScript 외부화): `static/js/dashboard_track_record.js`, `static/js/watchlist_toggle.js` 등으로 분리

## 다음 단계

**C2.3** (admin.html 486줄 분할) — 본 트랙의 패턴을 그대로 적용. `partials/admin/` 서브디렉터리.
