# Lucide Icons 도입 + 모바일 탭바 Pill 스타일 + 이모지 점진 교체

- **작성일**: 2026-04-22
- **상태**: 초안 (사용자 승인 — Option A)
- **담당 영역**: `api/templates/`, `api/static/css/src/`, `api/static/js/`

## 배경

모바일 하단 탭바(`_bottom_tabbar.html`)가 이모지(🏠💡📊📈⭐💎)를 사용하여 OS별 렌더링 편차가 크고(Windows/Android 납작, iOS 컬러풀) 현대 투자 앱 톤과 어울리지 않는다는 사용자 피드백. PC 사이드바는 텍스트만 사용 중.

기존 `base.html`에 이미 inline SVG(알림 종, 드롭다운 화살표)가 쓰이고 있어 벡터 아이콘 경로와 자연스럽게 결합 가능.

## 목표

1. **OS 무관 일관된 벡터 아이콘** 확보 — 라즈베리파이 24/7 배포 환경에서도 동일 렌더링
2. **모바일 탭바 모던 pill 활성 스타일** 적용 (iOS Human Interface Guideline 패턴)
3. 향후 페이지 곳곳의 이모지를 **동일 라이브러리로 점진 교체**할 수 있는 기반(유틸 클래스 + 로드 훅) 구축

비목표: Phase 1에서는 탭바만 교체. 대시보드·테마·제안 카드 이모지는 Phase 2 이후.

## 접근

**Lucide Icons** (https://lucide.dev) — Robinhood·Linear·Vercel·shadcn/ui 계열 라인 아이콘. 1,400+ SVG, MIT 라이선스.

### 로딩 전략: Self-host (CDN 비사용)

라즈베리파이·제한된 네트워크 환경 고려하여 **self-host**. `api/static/js/lucide.min.js`를 저장소에 포함.

| 항목 | 값 |
|---|---|
| 출처 | `https://unpkg.com/lucide@latest` 의 최신 안정 버전 IIFE 번들 |
| 용량 | ~40KB gzip, ~120KB raw (전체 아이콘 포함) |
| 버전 고정 | 파일 상단 주석에 버전·다운로드 일자 기록 |
| 외부 통신 | 런타임 0 요청 |

### 활성 상태 디자인: Pill 배경 (B-3 채택)

현재 `::before`로 상단 3px 언더라인 bar → 제거하고 **아이콘 영역 pill 배경**으로 전환. 아이콘 + 레이블 둘 다 `var(--accent)` 컬러.

```
[  ⌂    ✦   (◎)   ⤢   ☆  ]   ← Picks가 활성 (pill 배경 + accent color)
[ Today Themes Picks Record Watch ]
```

## 컴포넌트

| 파일 | 변경 | 설명 |
|---|---|---|
| `api/static/js/lucide.min.js` | **신규** | Self-host 번들 |
| `api/templates/base.html` | 수정 | `</body>` 직전 `<script>` + `lucide.createIcons()` 호출 |
| `api/templates/partials/_bottom_tabbar.html` | 수정 | 이모지 → `<i data-lucide="..." class="icon">` |
| `api/static/css/src/18_mobile_features.css` | 수정 | pill 활성 스타일, 언더라인 제거, `.icon` 유틸 |
| `api/static/css/style.css` | 재빌드 | `python -m tools.build_css` |

### 아이콘 매핑

| 탭 | 현재 | Lucide 이름 | 선정 이유 |
|---|---|---|---|
| Today | 🏠 | `layout-dashboard` | 대시보드 느낌 — "home"보다 정보 밀도 강조 |
| Themes | 💡 | `sparkles` | 발굴·인사이트. "lightbulb"는 너무 정적 |
| Picks | 📊 | `target` | 선별된 핵심 추천. "bar-chart"는 Record와 충돌 |
| Record | 📈 | `line-chart` | 시계열 수익률 추적 |
| Watch | ⭐ | `star` | 관심 종목 — 동일 시맨틱 |
| Pricing | 💎 | `crown` | 프리미엄 플랜. "gem"은 희소하지만 덜 보편적 |

## 데이터 흐름

```
페이지 요청
  ↓
base.html → <script src="/static/js/lucide.min.js">
  ↓ (DOMContentLoaded)
lucide.createIcons()
  ↓
<i data-lucide="star"> → <svg class="lucide lucide-star" ...>
  ↓
CSS .icon 규칙 적용 (크기·색상·stroke)
```

## CSS 유틸리티 규칙

```css
/* .icon — Lucide SVG 공통 기본값 */
.icon {
    width: 20px;
    height: 20px;
    stroke-width: 2;
    flex-shrink: 0;
}
.icon-sm { width: 16px; height: 16px; }
.icon-lg { width: 24px; height: 24px; }
```

기존 `.bottom-tab-icon`(`font-size: 20px`)은 Lucide가 들어오면서 **text → svg** 컨텍스트가 바뀌므로 규칙 재작성.

## 에러 처리

- **스크립트 로드 실패**: 아이콘 자리가 빈 공간으로 남음. 텍스트 레이블(Today/Themes/Picks/Record/Watch)로 기능 식별 가능하므로 치명적이지 않음 → 별도 fallback 미추가 (복잡도 최소화).
- **아이콘 이름 오타**: Lucide 런타임이 조용히 무시 → QA 단계에서 시각적으로 확인.

## 테스팅

수동 체크리스트:

- [ ] Chrome DevTools 모바일 에뮬레이션(iPhone 12, Galaxy S20) — 탭바 5개 아이콘 모두 렌더
- [ ] 활성 탭 pill 배경 정상 (`var(--accent)` 기반 반투명)
- [ ] 비활성 탭 `var(--text-muted)` stroke
- [ ] iOS Safari notch / 세이프 에어리어 유지 (`env(safe-area-inset-bottom)`)
- [ ] PC(>768px)에서는 `display: none` 유지
- [ ] 네트워크 탭에서 lucide.min.js가 `/static/js/`에서 응답 (CDN 0회)
- [ ] 비로그인 상태: Watch 탭이 Pricing 탭으로 치환 (`crown` 아이콘)

## Phase 2 로드맵 (본 스펙 범위 밖)

이번 PR 이후, 별도 스프린트로 다음 영역의 이모지를 Lucide로 교체.

### Priority 1 — 사용자 노출 빈도 최상
- 유저 드롭다운 메뉴 항목 (관심 종목, 알림, 프로필, 플랜)
- 대시보드 Hero/KPI/Insight 카드 아이콘
- 알림 페이지 타입 아이콘 (theme_update, new_proposal, price_alert)

### Priority 2 — 데이터 시각화 배지
- 제안 카드 (`_macros/proposal.html`) KRX 배지, grade badge
- 테마 헤더, 시나리오 그리드, risk-temp dot
- Track Record 위젯

### Priority 3 — 관리/부가
- 관리자 페이지 (audit log 타입)
- 교육 토픽 카테고리 (basics/analysis/risk/macro/practical/stories)
- 문의 카테고리 (general/bug/feature)

### 전환 원칙
1. 페이지 단위 PR (작은 단위 보장)
2. 이모지 → Lucide 매핑표는 본 문서 "아이콘 매핑" 섹션을 확장하며 유지
3. `.icon` 유틸 클래스로 통일 — 크기/컬러는 CSS만으로 제어
4. 접근성: `aria-hidden="true"` 유지, 텍스트 레이블은 반드시 병기

## 롤백 전략

커밋 단위 revert로 즉시 복구 가능. Lucide 의존성 외에 데이터베이스·API 변경 없음.
