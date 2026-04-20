# CSS 빌드 가이드

## 구조

이 디렉터리는 빌드된 결과물(`style.css`)과 소스 파일(`src/*.css`)로 구성됩니다.

- `style.css` — **빌드 결과물.** 직접 편집하지 마세요. 파일 상단에 `AUTO-GENERATED` 표기.
- `src/01_tokens.css` ~ `src/19_responsive.css` — 도메인별 분할된 소스 파일. 이곳에서 편집.
- `src/00_legacy.css` — **C1 리팩토링 진행 중에만 존재.** T10에서 삭제 예정.

## 빌드

```bash
python -m tools.build_css
```

`src/*.css`를 alphabetical 순서로 합쳐 `style.css`를 갱신합니다. 파일명의 숫자 prefix가 로드 순서(cascade)를 결정합니다.

## 파일 목록

| 파일 | 담당 |
|---|---|
| `01_tokens.css` | `:root` CSS 변수 (색상·여백·alias) |
| `02_base.css` | Reset & Base |
| `03_layout.css` | Sidebar, Header, Content, Notifications, User dropdown, Mobile menu button |
| `04_cards_grids.css` | Cards, Stat Grid, Market Summary/Insights |
| `05_badges.css` | Badges, Tier, Theme Type, Importance, Grade, Role/Status |
| `06_tables_bars.css` | Tables, Screener, Confidence/Allocation bars, Indicators, Sparkline |
| `07_modals.css` | Upgrade/Common/Confirm modals, Toast |
| `08_buttons_forms.css` | Button variants, Filters, Pagination, Empty state, External links |
| `09_proposals.css` | Proposal card/row/detail tabs, Sticky CTA, Tracking badges, Price target |
| `10_themes.css` | Scenario, Macro impact, Issue timeline, Theme 신뢰도 ring, Sector bubble |
| `11_dashboard_hero.css` | Tier1 Hero, KPI Strip, Insight cards, Yield curve, Market summary 접이식 |
| `12_dashboard_rest.css` | Signals, News, Top Picks, Track Record widget, Card locked |
| `13_stock_analysis.css` | Stock Analysis 페이지 전용 |
| `14_chat.css` | Theme Chat |
| `15_admin.css` | Admin, User Admin |
| `16_inquiry.css` | 고객 문의 게시판 |
| `17_history.css` | History Timeline |
| `18_mobile_features.css` | Bottom tabbar, Touch target, Safe area, Table stack, Ad slot, Disclaimer banner, Risk Temperature SVG |
| `19_responsive.css` | Responsive base, Tablet(UI-18), 모바일 전용 |

## 새 CSS 추가 방법

1. 적절한 파일에 규칙 추가 (없으면 새 파일 생성하되 기존 prefix 체계 따를 것)
2. `python -m tools.build_css` 실행
3. `src/*.css` + `style.css` 함께 커밋
