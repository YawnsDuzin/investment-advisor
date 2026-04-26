# Education SVG Visualizations Phase 2C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Phase 2C — basics 카테고리 보강 7 토픽에 손코딩 SVG 8장 추가 + v38 UPDATE 마이그레이션. spec: `docs/superpowers/specs/2026-04-26-education-svg-phase-2c-design.md`.

**Architecture:** Phase 1/2 패턴 그대로. SVG 8장 정적 파일, basics.py 7 토픽 markdown 갱신, v38 마이그레이션은 v36/v37과 동일 `WHERE content IS DISTINCT FROM` 가드.

**Tech Stack:** SVG 1.1 inline, viewBox 800×450, 다크 팔레트.

---

## File Structure

| 종류 | 경로 | 책임 |
|---|---|---|
| NEW | `api/static/edu/charts/market-cap-1.svg` | 시총 구간 대표 종목 막대 |
| NEW | `api/static/edu/charts/financial-statements-1.svg` | 3종 재무제표 관계 다이어그램 |
| NEW | `api/static/edu/charts/eps-fcf-ebitda-1.svg` | 3 이익 지표 그룹 막대 |
| NEW | `api/static/edu/charts/orderbook-and-trading-1.svg` | 호가창 ladder |
| NEW | `api/static/edu/charts/tax-and-accounts-1.svg` | 계좌별 세금 비교 |
| NEW | `api/static/edu/charts/ipo-subscription-1.svg` | 청약 5단계 타임라인 |
| NEW | `api/static/edu/charts/ipo-subscription-2.svg` | 균등 vs 비례배정 |
| NEW | `api/static/edu/charts/rights-bonus-split-1.svg` | 증자·분할 3축 매트릭스 |
| MOD | `tests/test_education_seeds.py` | expected 35개 + Phase 2C 신규 테스트 |
| MOD | `shared/db/migrations/seeds_education/basics.py` | 7 토픽 markdown |
| MOD | `shared/db/migrations/versions.py` | `_migrate_to_v38()` |
| MOD | `shared/db/migrations/__init__.py` | `_MIGRATIONS[38]` |
| MOD | `shared/db/schema.py` | `SCHEMA_VERSION = 38` |

---

## SVG 작성 표준 (Phase 1/2와 동일)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450" font-family="system-ui, -apple-system, sans-serif">
  <rect width="800" height="450" fill="#0f1419"/>
</svg>
```

**팔레트**: `#0f1419` / `#1a1f2e` / `#2d3748` / `#e2e8f0` / `#94a3b8` / `#4ade80` / `#ef4444` / `#f59e0b` / `#60a5fa` / `#c084fc`.
**구조**: 제목(font 18, y=30) / 부제(font 12, y=50) / 차트(x=80, y=80, w=640, h=290) / 캡션(y=425~440). 모든 요소 y < 450. off-palette 색상(`#dc2626`/`#fbbf24`/`#64748b`) 자제.

---

## Task 1: 검증 테스트 보강 (TDD red)

**Files:** Modify `tests/test_education_seeds.py`

- [ ] **Step 1: `test_svg_files_exist` expected 리스트에 Phase 2C 8개 추가**

기존 expected 리스트 마지막 (`]` 직전) 에 다음 추가:

```python
        # Phase 2C
        "market-cap-1.svg", "financial-statements-1.svg",
        "eps-fcf-ebitda-1.svg", "orderbook-and-trading-1.svg",
        "tax-and-accounts-1.svg",
        "ipo-subscription-1.svg", "ipo-subscription-2.svg",
        "rights-bonus-split-1.svg",
```

총 expected 35개 (Phase 1 18 + Phase 2 9 + Phase 2C 8).

- [ ] **Step 2: 신규 테스트 함수 추가 (파일 끝)**

```python
def test_v38_phase2c_visual_topics_have_image_refs():
    """Phase 2C 시각화 적용된 7개 슬러그의 content 에 SVG 이미지 참조가 1개 이상 존재."""
    phase2c_slugs = {
        "market-cap", "financial-statements", "eps-fcf-ebitda",
        "orderbook-and-trading", "tax-and-accounts",
        "ipo-subscription", "rights-bonus-split",
    }
    matched = [t for t in ALL_TOPICS if t["slug"] in phase2c_slugs]
    assert len(matched) == 7, f"expected 7 phase2c topics, found {len(matched)}"
    for t in matched:
        assert "/static/edu/charts/" in t["content"], \
            f"{t['slug']} missing SVG image reference"
```

- [ ] **Step 3: TDD red 확인**

```bash
pytest tests/test_education_seeds.py::test_svg_files_exist tests/test_education_seeds.py::test_v38_phase2c_visual_topics_have_image_refs -v
```
Expected: 둘 다 FAIL.

기존 Phase 1+2 테스트 회귀 무 확인:
```bash
pytest tests/test_education_seeds.py::test_v36_visual_topics_have_image_refs tests/test_education_seeds.py::test_v37_phase2_visual_topics_have_image_refs -v
```
Expected: 둘 다 PASS.

- [ ] **Step 4: 커밋**

```bash
git add tests/test_education_seeds.py
git commit -m "test(edu-svg): Phase 2C 검증 — 35 SVG + Phase 2C 7 토픽 이미지 참조"
```

(Co-Authored-By 추가)

---

## Task 2: SVG 작성 — 데이터 시각화 차트 3장

**Files:**
- Create: `api/static/edu/charts/market-cap-1.svg`
- Create: `api/static/edu/charts/financial-statements-1.svg`
- Create: `api/static/edu/charts/eps-fcf-ebitda-1.svg`

### SVG 1: market-cap-1.svg — 시총 구간 비교

**디자인 사양**:
- 차트 종류: **수평 막대 5개**
- 제목: "시총 구간별 대표 종목 (한국 시장)"
- 부제: "메가캡부터 마이크로캡까지"
- 차트 영역 x=240, y=80, w=480, h=290
- 5개 막대 (각 막대 높이 25, 간격 30):
  - 메가캡 (300조+): 삼성전자 380조, y_top=110, fill `#4ade80` opacity 1.0
  - 라지캡 (10~300조): SK하이닉스 75조, y_top=160, fill `#4ade80` opacity 0.8
  - 미드캡 (1~10조): 카카오게임즈 4조, y_top=210, fill `#4ade80` opacity 0.6
  - 스몰캡 (1천억~1조): 펄어비스 5천억(0.5조), y_top=260, fill `#4ade80` opacity 0.45
  - 마이크로캡 (1천억 미만): 예시 종목 500억(0.05조), y_top=310, fill `#4ade80` opacity 0.3
- log scale 또는 비선형 매핑 권장 — 단순 선형이면 380조 막대만 보임. 권장: log scale `bar_width = log10(시총조 + 1) * 100` (변환: log10(381)≈2.58 → 258, log10(76)≈1.88 → 188, log10(5)≈0.78 → 78, log10(1.5)≈0.18 → 18)
  - 더 단순 대안: bar_width 직접 지정 (멀리 떨어진 값 시각적 가시성 확보):
    - 메가: 460
    - 라지: 350
    - 미드: 200
    - 스몰: 110
    - 마이크로: 50
- 좌측 종목 라벨 (text-anchor end, x=230, font-size 12, fill `#e2e8f0`):
  - "삼성전자" (y=125), 부제 "메가캡 380조" (y=140, font-size 10, fill `#94a3b8`)
  - "SK하이닉스" (y=175) "라지캡 75조"
  - "카카오게임즈" (y=225) "미드캡 4조"
  - "펄어비스" (y=275) "스몰캡 0.5조"
  - "예시 종목" (y=325) "마이크로캡 500억"
- 막대 우측 끝에 시총 라벨 (font-size 11, fill `#e2e8f0`)
- X축 (y=370): "시총 (시각적 비례, 비선형)" 또는 단순 라벨
- 캡션 (y=425): "*시총은 회사 크기 — 같은 산업도 시총별 변동성 다르다*"

### SVG 2: financial-statements-1.svg — 3종 재무제표 관계

**디자인 사양**:
- 차트 종류: **3박스 다이어그램 + 화살표 연결**
- 제목: "3종 재무제표 — 손익·재무상태·현금흐름의 관계"
- 부제: "당기순이익이 모든 흐름의 시작점"
- 3개 박스 (각 200×120, 둥근 모서리):
  - **IS (손익계산서)** 좌상단 (x=80, y=100): 라벨 "손익계산서 (IS)" + 항목 "매출 / 비용 / 순이익"
    - rect fill `#1a1f2e` opacity 0.7, stroke `#60a5fa`
    - 헤더 fill `#60a5fa`
  - **BS (재무상태표)** 우상단 (x=520, y=100): "재무상태표 (BS)" + "자산 / 부채 / 자본"
    - stroke `#4ade80`
  - **CF (현금흐름표)** 하단 가운데 (x=300, y=240): "현금흐름표 (CF)" + "영업 / 투자 / 재무"
    - stroke `#f59e0b`
- 박스 안 라벨: 헤더 (font-size 14, font-weight bold), 항목 (font-size 11, 줄바꿈)
- **연결선** (`#94a3b8`, stroke-width 1.5, marker-end arrow):
  - IS → CF: 라벨 "순이익이 영업CF의 시작" (font-size 10, fill `#60a5fa`)
  - CF → BS: 라벨 "현금잔고 누적"
  - IS → BS: 라벨 "이익잉여금 (자본)"
- 화살표 marker `<defs><marker id="arrow">` 정의
- 캡션 (y=425): "*세 재무제표는 분리되지 않는다 — 흐름이 자산이 되고, 자산이 새 흐름을 만든다*"

### SVG 3: eps-fcf-ebitda-1.svg — 3 이익 지표 비교

**디자인 사양**:
- 차트 종류: **그룹 막대 (4분기 × 3 지표)**
- 제목: "EPS·FCF·EBITDA — 한 종목 4분기 비교"
- 부제: "예시 종목 — 회계이익 vs 현금이익"
- 차트 영역 x=80, y=80, w=640, h=290
- X축 (y=370): 4분기 (Q1, Q2, Q3, Q4)
  - 각 분기 그룹 너비 약 140, 간격 약 30
  - 각 분기에 3개 막대 (폭 35, 간격 5)
- Y축 (x=80): 금액 (억원), 0~500 범위, 격자 0/100/200/300/400/500
  - y_svg = 370 - val / 500 * 290
- 데이터 (각 분기 [EPS, FCF, EBITDA] 억원):
  - Q1: [120, 80, 250]
  - Q2: [180, 200, 320]
  - Q3: [220, 150, 380]
  - Q4: [200, 280, 410]
- 막대 색상:
  - EPS: `#60a5fa` (회계이익, 수익)
  - FCF: `#4ade80` (현금이익, 흐름)
  - EBITDA: `#f59e0b` (영업이익, 운영효율)
- 우상단 범례 박스 (x=540, y=95, w=170, h=70, fill `#1a1f2e` opacity 0.5):
  - 사각형 + "EPS (회계이익)" / "FCF (현금이익)" / "EBITDA (운영효율)"
- 캡션 (y=425): "*세 지표가 같은 방향이면 건강 — 다른 방향이면 회계 조작·일회성 의심*"

- [ ] **Step 1~3: SVG 3장 작성**
- [ ] **Step 4: XML 유효성**

```bash
python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/market-cap-1.svg', 'api/static/edu/charts/financial-statements-1.svg', 'api/static/edu/charts/eps-fcf-ebitda-1.svg']]; print('OK')"
```

- [ ] **Step 5: 커밋**

```bash
git add api/static/edu/charts/market-cap-1.svg api/static/edu/charts/financial-statements-1.svg api/static/edu/charts/eps-fcf-ebitda-1.svg
git commit -m "feat(edu-svg): Phase 2C 데이터 차트 3장 — 시총·재무제표·EPS/FCF/EBITDA"
```

---

## Task 3: SVG 작성 — 다이어그램 차트 3장

**Files:**
- Create: `api/static/edu/charts/orderbook-and-trading-1.svg`
- Create: `api/static/edu/charts/tax-and-accounts-1.svg`
- Create: `api/static/edu/charts/rights-bonus-split-1.svg`

### SVG 1: orderbook-and-trading-1.svg — 호가창 구조

**디자인 사양**:
- 차트 종류: **호가 ladder (위 매도호가 / 가운데 현재가 / 아래 매수호가)**
- 제목: "호가창 구조 — 매도호가 / 현재가 / 매수호가"
- 부제: "예시: 삼성전자 70,000원대"
- 차트 중심 (x=400, y=240)
- 매도 호가 5개 (위로 갈수록 높은 가격, 적색 그라데이션):
  - rect 폭 200, 높이 25, x=300 (가격), y_top=80,105,130,155,180
  - 가격 라벨 (text-anchor end, x=290): 70,500 / 70,400 / 70,300 / 70,200 / 70,100
  - 수량 라벨 (text-anchor start, x=510): 5,000주 / 12,000 / 8,500 / 22,000 / 6,000
  - 색상 fill `#ef4444` opacity 0.5~0.9 (위로 갈수록 진하게)
- 현재가 (x=300, y=215, w=200, h=30):
  - rect fill `#f59e0b`, stroke `#e2e8f0`, stroke-width 2
  - 텍스트 "70,000원 (현재가)" font-size 14, fill `#0f1419`, font-weight bold, text-anchor middle
- 매수 호가 5개 (아래로 갈수록 낮은 가격, 녹색 그라데이션):
  - y_top=255,280,305,330,355
  - 가격: 69,900 / 69,800 / 69,700 / 69,600 / 69,500
  - 수량: 18,000 / 7,500 / 25,000 / 9,000 / 14,000
  - 색상 fill `#4ade80` opacity 0.9~0.5 (아래로 갈수록 옅게)
- 좌측 헤더 (x=290, y=70, text-anchor end): "매도호가" (font-size 12, fill `#ef4444`)
- 좌측 헤더 (x=290, y=395, text-anchor end): "매수호가" (font-size 12, fill `#4ade80`)
- 우측 헤더 (x=510, y=70): "수량(주)" (font-size 12, fill `#94a3b8`)
- 캡션 (y=425): "*매도호가가 매수호가와 만나야 체결 — 시장 호가가 가장 적극적*"

### SVG 2: tax-and-accounts-1.svg — 절세 계좌 비교

**디자인 사양**:
- 차트 종류: **그룹 막대 (3 계좌 × 2 시나리오)**
- 제목: "절세 계좌 비교 — 5천만원 수익 시 세금"
- 부제: "ISA / 연금저축 / 일반계좌"
- 차트 영역 x=80, y=80, w=640, h=290
- X축 (y=370): 3 계좌 (균등 배치)
  - 그룹 중심 x: 220, 400, 580
- Y축 (x=80): 세금 (만원), 0~1200 범위, 격자 0/300/600/900/1200
  - y_svg = 370 - val / 1200 * 290
- 각 계좌별 2 막대 (폭 50, 간격 10):
  - **ISA** (x=180~250):
    - 한도 내 수익: 0만원 (fill `#4ade80`)
    - 초과분 (9.9%): 약 495만원
  - **연금저축** (x=360~430):
    - 만 55세 후 수령 (3.3%): 165만원 (fill `#60a5fa`)
    - 해약 시 (16.5%): 825만원
  - **일반계좌** (x=540~610):
    - 양도세 (22%): 1,100만원 (fill `#ef4444`)
    - (두 번째 막대도 동일 — 1,100만원, 단일 시나리오)
- 막대 위 끝에 금액 라벨
- X축 라벨 (각 그룹 아래): ISA / 연금저축 / 일반계좌 (font-size 12)
- 우상단 범례:
  - 좌 (각 그룹 첫 막대): "최선 시나리오"
  - 우 (각 그룹 두번째 막대): "최악 시나리오"
- 캡션 (y=425): "*소액·중기 → ISA, 노후 → 연금저축, 단기 매매 → 일반*"

### SVG 3: rights-bonus-split-1.svg — 증자·분할 3축 비교

**디자인 사양**:
- 차트 종류: **3×3 비교 매트릭스**
- 제목: "유상증자 / 무상증자 / 액면분할 — 3축 비교"
- 부제: "자금 유입 / 주주 부담 / 가치 변화"
- 매트릭스 영역: 좌상단 (x=200, y=100), 셀 크기 170×80
- 행 (3개, 좌측 라벨 text-anchor end x=190):
  - 자금 유입 (y_label=140, fill `#f59e0b`)
  - 주주 부담 (y_label=220, fill `#ef4444`)
  - 가치 변화 (y_label=300, fill `#60a5fa`)
- 열 (3개, 상단 라벨 text-anchor middle, y=92):
  - 유상증자 (x=285)
  - 무상증자 (x=455)
  - 액면분할 (x=625)
- 9개 셀 (rect 170×80, fill `#1a1f2e` opacity 0.4, stroke `#2d3748`):
  - (자금 × 유상): "✓ 외부 자금 조달" (fill `#f59e0b` opacity 0.3)
  - (자금 × 무상): "✗ 자본잉여금 이동만"
  - (자금 × 분할): "✗ 회계 변화 없음"
  - (부담 × 유상): "신주 매입 부담 / 희석" (fill `#ef4444` opacity 0.3)
  - (부담 × 무상): "✗ 무료 분배"
  - (부담 × 분할): "✗ 종이만 잘게"
  - (가치 × 유상): "**희석 가능** (음의 영향)" (fill `#ef4444` opacity 0.4)
  - (가치 × 무상): "불변 (권리락 후 정상)"
  - (가치 × 분할): "불변 (시총 동일)"
- 각 셀 텍스트 (font-size 11, fill `#e2e8f0`, 가운데 정렬, 줄바꿈 OK)
- 캡션 (y=425): "*세 행위는 발행주식 수가 늘지만 의미는 전혀 다르다*"

- [ ] **Step 1~3: SVG 3장 작성**
- [ ] **Step 4: XML 유효성**

```bash
python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/orderbook-and-trading-1.svg', 'api/static/edu/charts/tax-and-accounts-1.svg', 'api/static/edu/charts/rights-bonus-split-1.svg']]; print('OK')"
```

- [ ] **Step 5: 커밋**

```bash
git add api/static/edu/charts/orderbook-and-trading-1.svg api/static/edu/charts/tax-and-accounts-1.svg api/static/edu/charts/rights-bonus-split-1.svg
git commit -m "feat(edu-svg): Phase 2C 다이어그램 3장 — 호가창·세금 계좌·증자/분할"
```

---

## Task 4: SVG 작성 — IPO 차트 2장

**Files:**
- Create: `api/static/edu/charts/ipo-subscription-1.svg`
- Create: `api/static/edu/charts/ipo-subscription-2.svg`

### SVG 1: ipo-subscription-1.svg — 청약 5단계 타임라인

**디자인 사양**:
- 차트 종류: **타임라인 5단계 박스 + 화살표**
- 제목: "공모주 청약 5단계 타임라인"
- 부제: "수요예측부터 락업 해제까지"
- 5개 박스 (각 130×100, 좌→우 일렬, y=120):
  - x_top: 60, 200, 340, 480, 620
  - 박스 색상 (gradient):
    1. 수요예측 (D-21): `#60a5fa` opacity 0.3, stroke `#60a5fa`
    2. 청약 (D-7~D-5): `#4ade80` opacity 0.4, stroke `#4ade80`
    3. 환불 (D-3): `#94a3b8` opacity 0.3
    4. 상장 (D-Day): `#f59e0b` opacity 0.4, stroke `#f59e0b`
    5. 락업 해제 (D+30/90/180): `#ef4444` opacity 0.3, stroke `#ef4444`
- 각 박스 안:
  - 단계명 (font-size 14, fill `#e2e8f0`, font-weight bold, y=135)
  - 시점 (font-size 11, fill `#94a3b8`, y=155)
  - 핵심 (font-size 10, fill `#e2e8f0`, y=180):
    - "기관 공모가 결정"
    - "일반 청약 2일"
    - "미배정분 환불"
    - "거래 시작"
    - "기관 매도 가능"
- 박스 사이 화살표 (`#94a3b8`, stroke 1.5, arrow marker)
- 좌측 위 추가 라벨 "단기 변동성 큰 구간" (락업 해제 박스 위, y=100)
- 캡션 (y=425): "*균등 50% + 비례 50% 의무 — 소액 투자자는 다증권사 분산 청약이 정석*"

### SVG 2: ipo-subscription-2.svg — 균등 vs 비례배정

**디자인 사양**:
- 차트 종류: **2 패널 비교 (좌 균등 / 우 비례)**
- 제목: "균등배정 vs 비례배정 — 결과 비교"
- 부제: "청약 증거금 1억 vs 1천만원, 같은 IPO"
- 좌 패널 균등 (x=80~390, y=80~380, fill `#1a1f2e` opacity 0.4):
  - 헤더 "균등배정" (font-size 16, fill `#4ade80`, x=235, y=110, text-anchor middle)
  - 부제 "참여자 1명당 동일 수량" (font-size 11, fill `#94a3b8`, y=130)
  - 사용자 4명 아이콘 (간단한 사람 모양 또는 큰 동그라미):
    - 4 동그라미 (cx=120/200/280/360, cy=200, r=20, fill `#4ade80` opacity 0.5)
    - 각 옆에 "1명, 1주" 라벨
  - 결과 박스 (x=120, y=300, w=270, h=50, fill `#1a1f2e`):
    - "각자 1주씩 = 4주 총 분배" (font-size 13, fill `#4ade80`)
  - 캡션 안 (font-size 11): "1억 vs 1천만원 차이 무관"
- 우 패널 비례 (x=410~720, 동일 구조):
  - 헤더 "비례배정" (fill `#f59e0b`)
  - 부제 "증거금 비례 — 큰 자금 유리"
  - 사용자 4명 (다른 크기 동그라미):
    - cx=460/540/620/700, cy=200, r=10/15/20/35, fill `#f59e0b` opacity 0.5
  - 각 옆 라벨: "1명 ~1주", "1명 ~2주", "1명 ~4주", "1명 ~8주"
  - 결과 박스: "큰 자금에 집중 분배 — 8주 vs 1주" (fill `#f59e0b`)
- 패널 사이 분리선 (x=400, stroke `#2d3748`)
- 캡션 (y=425): "*소액 청약자는 균등 의존, 다증권사 분산이 답*"

- [ ] **Step 1~2: SVG 2장 작성**
- [ ] **Step 3: XML 유효성**

```bash
python -c "import xml.etree.ElementTree as ET; [ET.parse(f) for f in ['api/static/edu/charts/ipo-subscription-1.svg', 'api/static/edu/charts/ipo-subscription-2.svg']]; print('OK')"
```

- [ ] **Step 4: 8 SVG 카운트 확인** (Phase 2C 누적)

```bash
ls api/static/edu/charts/ | wc -l
```
Expected: 35.

- [ ] **Step 5: 커밋**

```bash
git add api/static/edu/charts/ipo-subscription-*.svg
git commit -m "feat(edu-svg): Phase 2C IPO 차트 2장 — 청약 5단계·균등 vs 비례"
```

---

## Task 5: 시드 markdown 7 토픽 갱신

**Files:** Modify `shared/db/migrations/seeds_education/basics.py`

### 토픽별 가이드

| 토픽 | 위치 | 이미지 | caption |
|---|---|---|---|
| market-cap | "### 시총 구간별 특성" 표 직후 | market-cap-1.svg | *시총은 변동성과 함께 — 메가캡일수록 안정, 마이크로캡일수록 변동* |
| financial-statements | "## 재무제표 3종 한눈에" 도입 직후 | financial-statements-1.svg | *세 재무제표는 분리되지 않는다 — 순이익이 모든 흐름의 시작* |
| eps-fcf-ebitda | 3 지표 정의 끝난 직후 또는 비교 표 직후 | eps-fcf-ebitda-1.svg | *세 지표 일치 = 건강 / 다른 방향 = 회계 조작 의심* |
| orderbook-and-trading | 호가창 정의 단락 직후 | orderbook-and-trading-1.svg | *매도호가↓ vs 매수호가↑가 만나면 체결* |
| tax-and-accounts | "ISA·연금저축·일반계좌" 비교 표 직후 | tax-and-accounts-1.svg | *세금이 실수익률을 결정 — 5천만원 수익에 0원 vs 1,100만원 차이* |
| ipo-subscription (1) | "## 청약 일정 5단계" 표 직후 | ipo-subscription-1.svg | *5단계 일정 — 락업 해제는 단기 변동성 큰 구간* |
| ipo-subscription (2) | "## 균등배정 vs 비례배정" 단락 직후 | ipo-subscription-2.svg | *소액 청약자에 균등 절대 유리* |
| rights-bonus-split | 3가지 비교 표 직후 | rights-bonus-split-1.svg | *셋 다 발행주식 늘지만 의미는 전혀 다르다* |

### 삽입 형식 (Phase 1/2 동일)

```markdown

![차트: <설명>](/static/edu/charts/<file>.svg)

*<caption>*

```

- [ ] **Step 1: basics.py 7 토픽 갱신**

각 토픽 content 안에 위 가이드대로 markdown 이미지 삽입. ipo-subscription 토픽은 *2장* 삽입. 다른 필드 변경 금지.

먼저 basics.py read 하여 정확한 섹션 위치 파악.

- [ ] **Step 2: 통합 검증**

```bash
pytest tests/test_education_seeds.py::test_v38_phase2c_visual_topics_have_image_refs -v
```
Expected: PASS (7 토픽 모두 이미지 참조).

특별 검증 (ipo-subscription 2장):
```bash
python -c "from shared.db.migrations.seeds_education import basics; t = next(t for t in basics.TOPICS if t['slug'] == 'ipo-subscription'); assert t['content'].count('/static/edu/charts/') == 2; print('OK')"
```

전체 회귀:
```bash
pytest tests/test_education_seeds.py -v
```
Expected: 15/15 PASS.

- [ ] **Step 3: 커밋**

```bash
git add shared/db/migrations/seeds_education/basics.py
git commit -m "feat(edu-svg): Phase 2C basics 7 토픽 markdown 에 SVG 이미지 참조 삽입"
```

---

## Task 6: v38 마이그레이션

**Files:**
- Modify: `shared/db/schema.py`
- Modify: `shared/db/migrations/__init__.py`
- Modify: `shared/db/migrations/versions.py`

### Step 1: versions.py 끝에 `_migrate_to_v38` 추가

```python
def _migrate_to_v38(cur) -> None:
    """Education Phase 2C 시각화 — basics 7 토픽 markdown content 갱신.

    v36 (Phase 1) / v37 (Phase 2) 와 동일한 UPDATE 패턴.
    멱등성: WHERE content IS DISTINCT FROM 가드.
    """
    from shared.db.migrations.seeds_education import ALL_TOPICS

    phase2c_slugs = {
        "market-cap", "financial-statements", "eps-fcf-ebitda",
        "orderbook-and-trading", "tax-and-accounts",
        "ipo-subscription", "rights-bonus-split",
    }

    affected = 0
    for t in ALL_TOPICS:
        if t["slug"] not in phase2c_slugs:
            continue
        cur.execute(
            """UPDATE education_topics
               SET content = %s
               WHERE slug = %s
                 AND content IS DISTINCT FROM %s""",
            (t["content"], t["slug"], t["content"]),
        )
        affected += cur.rowcount

    cur.execute("""
        INSERT INTO schema_version (version) VALUES (38)
        ON CONFLICT (version) DO NOTHING;
    """)
    print(f"[DB] v38: Phase 2C basics 시각화 토픽 content 갱신 {affected}건 (대상 7건)")
```

### Step 2: __init__.py 갱신

```python
    37: _v._migrate_to_v37,
    38: _v._migrate_to_v38,
}
```

### Step 3: schema.py 갱신

```python
SCHEMA_VERSION = 38  # v38: education Phase 2C — basics 7 토픽 시각화 SVG
```

- [ ] **Step 4: 동적 검증**

```bash
python -c "
from shared.db.schema import SCHEMA_VERSION
from shared.db.migrations import _MIGRATIONS
from shared.db.migrations.versions import _migrate_to_v38
print('SCHEMA_VERSION:', SCHEMA_VERSION)
print('38 in _MIGRATIONS:', 38 in _MIGRATIONS)
print('callable:', callable(_migrate_to_v38))
"
```
Expected: `38`, `True`, `True`.

- [ ] **Step 5: 커밋**

```bash
git add shared/db/migrations/versions.py shared/db/migrations/__init__.py shared/db/schema.py
git commit -m "feat(db): v38 마이그레이션 — education Phase 2C basics 시각화 content 갱신"
```

---

## Task 7: 통합 검증 + 프롬프트 commit

**Files:** (검증만 + 프롬프트 commit)

- [ ] **Step 1: 전체 검증 테스트**

```bash
pytest tests/test_education_seeds.py -v
```
Expected: 15/15 PASS — 기존 14 + Phase 2C 신규 1 (`test_v38_phase2c_visual_topics_have_image_refs`).

- [ ] **Step 2: SVG 35장 카운트 + XML 검증**

```bash
python -c "
import os
import xml.etree.ElementTree as ET
base = 'api/static/edu/charts'
files = sorted(os.listdir(base))
print(f'Total SVG files: {len(files)}')
for f in files:
    ET.parse(os.path.join(base, f))
print('All SVG files valid XML')
"
```
Expected: `Total SVG files: 35`, `All SVG files valid XML`.

- [ ] **Step 3: v38 등록 + 누적 카운트 검증**

```bash
python -c "
from shared.db.schema import SCHEMA_VERSION
from shared.db.migrations import _MIGRATIONS
from shared.db.migrations.seeds_education import ALL_TOPICS

assert SCHEMA_VERSION == 38, f'expected 38, got {SCHEMA_VERSION}'
assert 38 in _MIGRATIONS

visual_slugs = {
    # Phase 1
    'per-pbr-roe', 'business-cycle', 'chart-key-five', 'momentum-investing',
    'diversification', 'risk-adjusted-return', 'correlation-trap',
    'interest-rates', 'yield-curve-inversion',
    'what-if-2015', 'korea-market-timeline', 'tesla-eight-years',
    'factor-six-axes', 'market-regime-reading',
    # Phase 2
    'stop-loss', 'position-sizing',
    'foreign-institutional-flow', 'short-selling-squeeze',
    'exchange-rates', 'scenario-thinking',
    'legendary-crashes', 'behavioral-biases',
    # Phase 2C
    'market-cap', 'financial-statements', 'eps-fcf-ebitda',
    'orderbook-and-trading', 'tax-and-accounts',
    'ipo-subscription', 'rights-bonus-split',
}
total_refs = sum(t['content'].count('/static/edu/charts/') for t in ALL_TOPICS if t['slug'] in visual_slugs)
print(f'Visual topics: {len(visual_slugs)}, Total refs: {total_refs}')
assert len(visual_slugs) == 29
assert total_refs == 35  # Phase 1: 18, Phase 2: 9, Phase 2C: 8
print('OK')
"
```
Expected: `Visual topics: 29, Total refs: 35`, `OK`.

- [ ] **Step 4: 프롬프트 commit**

```bash
git status --short _docs/_prompts/
```

modified 라면:
```bash
git add _docs/_prompts/20260426_prompt.md
git commit -m "docs(prompts): 2026-04-26 conversation Phase 2C basics 시각화 — 통합 검증 완료"
```

(Co-Authored-By 추가)

unmodified 라면 skip + 보고에 명시.

- [ ] **Step 5: Phase 2C commit chain 확인**

```bash
git log --oneline b9c25cd..HEAD
```

기대: spec(b9c25cd) 이후 plan + Task 1~7 commit 모두 정상.

---

## Self-Review

**Spec coverage**:
- spec §2.1 차트 매핑 8장 → Task 2~4 분배 (3 + 3 + 2) ✓
- spec §2.2 SVG 표준 → Plan 상단 + 디자인 사양 ✓
- spec §3.1 SVG 정적 파일 8장 → Task 2~4 ✓
- spec §3.2 basics.py 7 토픽 → Task 5 ✓
- spec §3.3 v38 마이그레이션 → Task 6 ✓
- spec §3.4 검증 테스트 → Task 1 + Task 7 ✓
- spec §5 검증 계획 → Task 7 ✓

**Placeholder scan**: TBD/TODO 없음. 차트 디자인 사양 모두 구체적. 마이그레이션 코드 그대로.

**Type/이름 일관성**: phase2c_slugs 7개 set Task 1 / Task 5 / Task 6 / Task 7 일치. 파일명 일관.

**잠재 위험**: market-cap log scale 단순화 — 작성자가 비선형 매핑 정확히 그릴 수 있어야. 부록 가이드에 두 옵션 (log scale or 직접 지정) 명시.
