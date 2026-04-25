# Stock Cockpit Phase 2 — 정량 팩터 / 시장 레짐 / KRX 확장 설계

- 작성: 2026-04-25 KST
- 상태: Draft → 사용자 리뷰 대기
- 전제: Phase 1 구현 완료 ([_docs/20260425170650_stock-cockpit-design.md](20260425170650_stock-cockpit-design.md), 커밋 `c5a5a87 ~ 7a15c40`)
- 범위: Cockpit 페이지 § 2-B / § 3 / § 5 추가 + Phase 1 backlog CKPT-1/2/3 통합 + JS 파일 분리

---

## 1. 배경

Phase 1에서 § Hero / § 1 가격차트 / § 2-A 벤치마크 / § 4 펀더멘털 / § 6 추천 타임라인을 in-place 교체했다. 남은 청사진 섹션은 Phase 1 spec § 8 에 정의되어 있으며, 본 spec은 그것을 정밀화한다.

추가로 Phase 1 코드 리뷰에서 발견된 백로그 3건(차트 에러 overlay 패턴 / 벤치마크 정규화 기준일 갭 / stock OHLCV 토글 재조회)을 함께 처리하기로 결정한 이유는, JS 파일 분리(아래 § 3.3)와 함께 다루면 IIFE를 새 파일로 옮기는 같은 손이 닿는 시점이라 자연스럽기 때문이다.

## 2. 목표

1. **§ 2-B 정량 팩터 레이더** — `factor_snapshot` 6축을 한눈에 → 시장 cross-section 위치 자명화
2. **§ 3 시장 레짐 + 섹터 컨텍스트** — `market_regime` 4 인덱스 카드 + 섹터 내 종목 팩터 분위 비교
3. **§ 5 KRX 확장** — 한국주 한정 외국인 보유/순매수/공매도/지수 편입 가시화 (외국주는 섹션 hide)
4. **CKPT-1/2/3 backlog 정리** — 차트 에러 경로 overlay 패턴, 벤치마크 정규화 기준일 갭 처리, stock OHLCV 캐싱
5. **JS 파일 분리** — `static/js/stock_cockpit.js` 신설로 인라인 1000+ 라인 임계 회피

## 3. 결정 사항

### 3.1 § 3 섹터 비교 방식 — B (섹터 내 팩터 분위)

원래 Phase 1 spec § 8 은 "섹터 평균 PER/PBR vs 종목"을 적었으나, 데이터 인프라 검토 결과 **유니버스 전체 yfinance `.info` 배치 캐시가 필요**해 무거움. 대안:

| 옵션 | 동작 | 채택 |
|---|---|---|
| A. 섹터 평균 PER/PBR | 모든 종목 yfinance 배치 적재 + 평균 | ❌ 인프라 추가 부담 |
| **B. 섹터 내 팩터 분위 비교** | `stock_universe_ohlcv` 기반 같은 섹터 cross-section → "섹터 내 r3m 상위 X%" | ✅ |
| C. 단순 섹터 표시 + 같은 섹터 종목 5개 링크 | 정보 부족 | ❌ |
| D. § 3 섹터 비교 SKIP | 청사진 미달 | ❌ |

**B 채택 근거**: 새 인프라 0 (`factor_engine.py` 가 이미 시장 그룹별 cross-section을 계산하니 같은 패턴을 섹터 단위로 1회 더). 차별화 정보는 "섹터 내 모멘텀 순위"인 게 결정에 더 유용 (밸류에이션은 § 4 펀더멘털 카드에 이미 있음).

### 3.2 KRX 확장 (§ 5) — 외국주 hide, 한국주만 노출

`investment_proposals.foreign_*/squeeze_risk/index_membership` (v20 컬럼) 은 추천 시점 스냅샷이며, 외국주는 항상 NULL.

| 케이스 | 처리 |
|---|---|
| 한국주 추천 ≥ 1건 | § 5 카드 표시. 가장 최근 추천의 스냅샷 사용 |
| 한국주 추천 0건 | § 5 카드에 "추천 데이터 없음 — 한국주 KRX 수급 정보는 추천 발생 후 누적됩니다" 안내 |
| 외국주 (NASDAQ/NYSE 등) | § 5 통째 `display: none` |

### 3.3 JS 파일 분리 — Phase 2 첫 task에서 단행

Phase 1 종료 시점에 `stock_cockpit.html` 약 672줄. Phase 2가 § 2-B + § 3 + § 5 IIFE를 인라인으로 추가하면 1000+ 줄. 가독성·캐싱·리뷰 타깃 측면에서 분리 시점 도달.

**계획**: 첫 Phase 2 task에서 인라인 `<script>` 본문(IIFE-0/§1/§2-A/§6) 통째를 `api/static/js/stock_cockpit.js` 로 이동. 템플릿은 `<script src="/static/js/stock_cockpit.js" defer>` 로 참조. CDN(lightweight-charts)·Chart.js 만 인라인 유지.

CSS 도 같이 분리할지는 본 분리 task의 self-review 시점에 결정 — 인라인 약 200줄이라 임계는 아니지만, JS 분리하는 김에 같이 가는 것도 자연스러움. **결정 보류, JS 분리 후 판단**.

### 3.4 CKPT-1/2/3 통합 처리

JS 분리 후 첫 후속 task에서 처리:

- **CKPT-1**: § 1 + § 2-A 차트 에러 경로의 `container.innerHTML = '...'` 패턴을 overlay div 패턴으로 교체 (chart 인스턴스 보존)
- **CKPT-2**: § 2-A 벤치마크 정규화 — `commonStart` 이후 두 시리즈에서 **양쪽에 모두 존재하는 첫 거래일**을 기준으로 통일 (또는 console.warn 로 인지)
- **CKPT-3**: § 2-A 토글 시 stock OHLCV 재조회 → IIFE 스코프 `stockCache` 도입

### 3.5 비범위 (Phase 3 로 미룸)

- § 7 등장 테마 카드 + 신규 API `/themes`
- Hero 압축 sticky 모드
- 모바일 반응형 정밀 조정

## 4. 페이지 구조 (Phase 2 추가 섹션)

```
┌─────────────────────────────────────────────────────────────────────┐
│ § Hero (Phase 1)                                                    │
│ § 1. 가격 차트 (Phase 1) — overlay 패턴으로 변경                    │
├──────────────────────────┬──────────────────────────────────────────┤
│ § 2-A. 벤치마크 (Phase 1)│ § 2-B. 정량 팩터 레이더 ← Phase 2       │
│  overlay 패턴 + 캐싱     │  6축 (r1m/r3m/r6m/r12m/lowvol/volume)   │
│                          │  실선=종목, 점선=시장 중앙(0.5)         │
├──────────────────────────┴──────────────────────────────────────────┤
│ § 3. 시장 레짐 + 섹터 컨텍스트  ← Phase 2                           │
│  ┌─KOSPI─┐ ┌─KOSDAQ─┐ ┌─SP500─┐ ┌─NDX100─┐                         │
│  │ 위 ↑  │ │ 아래 ↓ │ │ 위 ↑  │ │ 위 ↑   │  + KRX 시장폭           │
│  │ +1.2% │ │ -3.5%  │ │ +5.1% │ │ +8.2%  │                         │
│  └───────┘ └────────┘ └───────┘ └────────┘                         │
│                                                                     │
│  섹터 내 모멘텀 순위 (B 안)                                         │
│   r1m 상위 22% │ r3m 상위 15% │ r6m 상위 30% │ r12m 상위 8%        │
│   저변동 상위 45% │ 거래량 상위 12%                                 │
├─────────────────────────────────────────────────────────────────────┤
│ § 4. 펀더멘털 (Phase 1)                                             │
├─────────────────────────────────────────────────────────────────────┤
│ § 5. KRX 확장 (한국주만)  ← Phase 2                                 │
│  외국인보유 도넛 │ 외국인순매수 신호 │ 숏스퀴즈 게이지 │ 지수편입  │
├─────────────────────────────────────────────────────────────────────┤
│ § 6. 추천 타임라인 (Phase 1)                                        │
└─────────────────────────────────────────────────────────────────────┘
```

## 5. 데이터 매핑 (Phase 2 추가 섹션)

| § | 섹션 | 1차 소스 | 비고 |
|---|---|---|---|
| 2-B | 팩터 레이더 6축 | `investment_proposals.factor_snapshot` 최신 1건 | r1m/r3m/r6m/r12m_pctile + low_vol_pctile + volume_pctile |
| 2-B | 데이터 부족 케이스 | factor_snapshot NULL | 회색 dashed + "데이터 부족 — 첫 추천 후 채워집니다" |
| 3 | 시장 레짐 4 인덱스 카드 | `analysis_sessions.market_regime` 최신 1건 | indices.{KOSPI/KOSDAQ/SP500/NDX100} 의 above_200ma·pct_from_ma200·vol_regime·return_1m_pct·drawdown_from_52w_high_pct |
| 3 | KRX 시장폭 | `analysis_sessions.market_regime.breadth_kr_pct` | 단일 % 값 |
| 3 | 섹터 팩터 분위 (B 안) | 신규 `/api/stocks/{ticker}/sector-stats` API → `factor_engine` 의 섹터 단위 cross-section | r1m/r3m/r6m/r12m + low_vol + volume 6축 분위 |
| 5 | 외국인 보유비율 도넛 | `investment_proposals.foreign_ownership_pct` 최신값 | 한국주만 |
| 5 | 외국인 순매수 신호 | `investment_proposals.foreign_net_buy_signal` 최신값 | enum: positive/neutral/negative/null |
| 5 | 숏스퀴즈 게이지 | `investment_proposals.squeeze_risk` 최신값 | enum: low/mid/high |
| 5 | 지수 편입 배지 | `investment_proposals.index_membership` 최신값 | TEXT[] (예: KOSPI200, KRX300) |

### 5.1 § 3 섹터 팩터 분위 산식

```
sector_pctile(metric, ticker, sector) =
    PERCENT_RANK() OVER (
        PARTITION BY sector
        ORDER BY metric ASC
    ) for the ticker

metric ∈ {r1m_pct, r3m_pct, r6m_pct, r12m_pct, vol60_pct (역순), volume_ratio}
```

`vol60_pct` 는 낮을수록 좋으므로 `low_vol_pctile = 1 - PERCENT_RANK(vol60_pct ASC)` 로 표기. `factor_engine._compute_pctiles()` 가 이미 시장 그룹 단위로 동일 산식을 적용 — 새 함수 `compute_sector_pctiles()` 로 섹터 그룹화 버전 추가.

섹터 풀 < 5 종목이면 분위 계산 skip → "섹터 표본 부족 (N개)" 표시.

## 6. 신규 백엔드 API

| 메서드 | 경로 | 책임 |
|---|---|---|
| GET | `/api/stocks/{ticker}/sector-stats` | 섹터 내 6축 분위 + 섹터 표본 크기 |

응답 스키마:

```json
{
  "ticker": "TXN",
  "sector": "Technology",
  "sector_size": 124,
  "ranks": {
    "r1m": {"value_pct": 8.4, "sector_pctile": 0.78, "sector_top_pct": 22},
    "r3m": {"value_pct": 12.4, "sector_pctile": 0.85, "sector_top_pct": 15},
    "r6m": {"value_pct": 25.1, "sector_pctile": 0.70, "sector_top_pct": 30},
    "r12m": {"value_pct": 48.0, "sector_pctile": 0.92, "sector_top_pct": 8},
    "low_vol": {"value_pct": 18.5, "sector_pctile": 0.55, "sector_top_pct": 45},
    "volume": {"value_ratio": 1.42, "sector_pctile": 0.88, "sector_top_pct": 12}
  },
  "computed_at": "2026-04-25T19:00:00+09:00"
}
```

`sector_top_pct` = `round((1 - sector_pctile) * 100)` — UI "상위 X%" 표기 직사용.

섹터 표본 < 5 → `ranks` 의 각 값에 `sector_pctile`/`sector_top_pct` = NULL 반환.

신규 라우트는 `api/routes/stocks.py` 에 추가. 백엔드 산식은 `analyzer/factor_engine.py` 에 `compute_sector_pctiles(db_cfg, ticker, market) -> dict` 신규 함수.

## 7. 기술 / 라이브러리

| 용도 | 라이브러리 | 비고 |
|---|---|---|
| § 2-B 레이더 | Chart.js v4 (CDN) | radar chart, 6축. defer load. |
| § 5 도넛 (외국인 보유) | Chart.js v4 | 같은 라이브러리 한 번 로드 |
| § 3 4 인덱스 카드 | 순수 HTML/CSS | 차트 라이브러리 불필요 |
| § 5 게이지 (숏스퀴즈) | 순수 CSS bar | 단순 3단계 표시면 충분 |
| 분리된 JS | `api/static/js/stock_cockpit.js` | 신규 파일 — Phase 1 인라인 IIFE 이동 |

Chart.js CDN: `https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js`. lightweight-charts 와 함께 Cockpit 페이지 `{% block scripts %}` 안에서만 로드 (전역 금지).

## 8. 단계별 task 분할

본 spec 의 implementation plan 이 다룰 task 순서:

| Task | 내용 | 비고 |
|---|---|---|
| 1 | JS 파일 분리 — Phase 1 인라인 → `static/js/stock_cockpit.js` | 회귀 0 보장 |
| 2 | CKPT-1: § 1 + § 2-A 차트 에러 overlay 패턴 | 차트 인스턴스 보존 |
| 3 | CKPT-2 + CKPT-3: § 2-A 벤치마크 갭 처리 + stockCache | 작은 fix 묶음 |
| 4 | `compute_sector_pctiles()` + `GET /api/stocks/{ticker}/sector-stats` | 백엔드 신규 |
| 5 | § 2-B 정량 팩터 레이더 (Chart.js radar, 6축) | 신규 IIFE — **Chart.js CDN 이 task 에서 처음 도입** |
| 6 | § 3 시장 레짐 4 인덱스 카드 + KRX 시장폭 + 섹터 팩터 분위 표 | 신규 IIFE, sector-stats API 호출 |
| 7 | § 5 KRX 확장 (외국인 보유 도넛 + 순매수 신호 + 숏스퀴즈 게이지 + 지수 편입) — 한국주만 | 외국주 자동 hide |
| 8 | 통합 검증 + 문서 업데이트 | CLAUDE.md / spec status |

각 task TDD 사이클 (failing test → 구현 → 통과 → commit), Phase 1 흐름 그대로.

## 9. 엣지케이스 / 리스크

| 케이스 | 처리 |
|---|---|
| 종목 추천 0건 → factor_snapshot 없음 | § 2-B 회색 dashed + "데이터 부족" 메시지. § 3 섹터 분위는 sector-stats API 가 OHLCV 직접 사용하므로 별개로 채움 |
| 섹터 표본 < 5 종목 | sector-stats API 가 분위 NULL 반환 → § 3 섹터 표 "섹터 표본 부족 (N개)" |
| `market_regime` 비어있음 (인덱스 OHLCV 미수집 초기) | § 3 시장 레짐 카드에 "레짐 계산 대기 중" placeholder. 섹터 표는 별도 동작 가능 |
| 외국주 (`stock_universe.market` ∈ {NASDAQ, NYSE, AMEX}) | § 5 `display: none` |
| KRX 한국주이나 추천 0건 | § 5 안내 텍스트 (위 § 3.2 참조) |
| `market_regime.indices` 일부만 존재 (예: KOSPI/KOSDAQ 만, US 미수집) | 존재하는 카드만 렌더, 빈 슬롯은 "데이터 없음" |
| Chart.js 로드 실패 (CDN 다운) | § 2-B / § 5 도넛에 placeholder 텍스트, 다른 섹션 정상 |

## 10. 인증 정책

Phase 1 과 동일 — 페이지 자체는 무인증. 신규 API `/api/stocks/{ticker}/sector-stats` 도 무인증 (다른 stocks API 와 동일).

## 11. 성공 기준

Phase 2 완료 시점 검증:

1. KRX 한국주 (예: `005930`/KOSPI) + US 종목 (예: `TXN`/NASDAQ) 양쪽에서 § 2-B / § 3 정상 렌더 (한국주는 § 5 도, US 종목은 § 5 hide)
2. 섹터 표본 부족 종목 (예: 신규 상장)에서 sector-stats API 의 분위 NULL → § 3 섹터 표 "표본 부족" 정상 표시
3. CKPT-1 검증: § 1/§ 2-A 차트에서 강제 OHLCV 빈 응답 (예: 미수집 ticker) 후 토글 클릭 시 차트 인스턴스 보존, 콘솔 에러 0
4. CKPT-2 검증: KRX/US 거래일 갭 케이스에서 § 2-A 두 라인 모두 같은 기준일 사용 (브라우저 콘솔 검증)
5. CKPT-3 검증: § 2-A 토글 클릭 시 네트워크 탭에서 stock OHLCV 추가 요청 없음
6. 신규 API `/sector-stats` 단위 테스트 — 정상 / 표본 부족 / 외국주 3 케이스
7. JS 분리 후 페이지 200 응답 + 모든 IIFE 동작 (회귀 테스트 14 + Phase 2 신규 테스트 모두 통과)

## 12. 후속 작업

Phase 2 완료 후:

- Phase 3 spec 분리 (§ 7 등장 테마 카드 + Hero sticky + 모바일 반응형)
- CKPT-4/5 (pre-existing test 깨짐) — Phase 와 무관, 별도 cleanup 시점에
- 섹터 평균 PER/PBR 인프라 검토 — yfinance 배치 캐시 가치 평가 (Phase 2 § 3 가 채택한 B 안의 보완재로)
