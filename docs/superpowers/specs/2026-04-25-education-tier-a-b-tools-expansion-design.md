# Education 컨텐츠 확장 설계 — Tier A·B 14 토픽 + 신규 `tools` 카테고리 (26 → 40)

- **작성일**: 2026-04-25
- **상태**: 설계 확정 (구현 플랜 작성 대기)
- **범위**: 교육 토픽 14개 추가 + `tools` 카테고리 신설 + 마이그레이션 v35 + 카테고리 라벨 갱신
- **방식**: 일괄 작업(단일 PR), 단계별 커밋. v24 패턴(`NEW_TOPICS_VN` 노출 + ON CONFLICT 멱등 INSERT) 그대로 답습.

## 1. 배경 / 동기

### 현재 상태
- 교육 메뉴는 6개 카테고리(`basics`/`analysis`/`risk`/`macro`/`practical`/`stories`)에 26개 토픽 (v21 11개 + v24 15개).
- `analysis` 4개 / `risk` 2개 / `macro` 3개로 **갭이 명확** — 정량 지표·기술 분석·시장 매크로 핵심 영역이 얇다.
- 본 시스템이 v30~v34에 도입한 신규 데이터 레이어(B1 정량 팩터 6축, B2 시장 레짐, v34 프리마켓 브리핑) 사용자 가이드가 **0건**. UI는 노출되어 있는데 사용자가 해석 못한다.

### 문제
1. **정량/기술 분석 컨텐츠 부재**: 차트 지표·팩터·KPI별 핵심을 알려주는 토픽 부족. 사용자가 제안 카드의 `factor_snapshot`을 보고도 의미를 모름.
2. **리스크 관리 얕음**: 분산·손절만 있고 포지션 사이징·위험조정수익률·상관관계 함정 부재. post-return 추적(v19/v29)을 해석할 도구가 없다.
3. **본 시스템 신규 기능 가이드 부재**: B1 팩터 / B2 레짐 / v34 브리핑이 UI에만 존재. 사용자가 "AI가 본 실측 데이터" 섹션을 무시한다.
4. **시의성 컨텐츠 부재**: 2024-2025 AI 사이클·2020-2022 광기·테슬라 8년 등 *현재 시점에서 의미 있는* 사례가 없다.

### 목표
- 정량 분석·리스크·매크로 갭을 한 번에 메우고, 본 시스템 신규 기능 해석 가이드를 확보한다.
- 신규 카테고리 `tools` 신설 — 일반 지식과 *시스템 도구 사용법*을 분리해서 신규 유저 동선을 명확히 한다.
- 기존 운영 DB에 자동 적용되도록 v35 마이그레이션 신설 (v24와 동일한 `ON CONFLICT (slug) DO NOTHING` 멱등성).

## 2. 최종 구성

### 2.1 카테고리·난이도 분포

| 카테고리 | v24 후 | 추가 | v35 후 |
|---|---|---|---|
| basics | 8 | 2 (A) | 10 |
| analysis | 4 | 2 (B) | 6 |
| risk | 2 | 3 (C) | 5 |
| macro | 3 | 1 (D) | 4 |
| practical | 4 | 0 | 4 |
| stories | 5 | 3 (E) | 8 |
| **`tools` (신규)** | 0 | 3 (F) | **3** |
| **합계** | **26** | **14** | **40** |

난이도 — beginner 4 (basics 2 + stories 1 + tools 1) / intermediate 10. advanced는 후속 작업.

### 2.2 신규 토픽 14개 + 신규 카테고리 1개

#### A. `basics` 보강 (2개) — 흥미·실용

| slug | 제목 | 난이도 | sort_order |
|---|---|---|---|
| `ipo-subscription` | 공모주 청약 실전 — 균등·비례·환불·보호예수 | beginner | 9 |
| `rights-bonus-split` | 유·무상증자와 액면분할 — 희석 효과의 진짜 의미 | beginner | 10 |

#### B. `analysis` 보강 (2개) — 정량/실무

| slug | 제목 | 난이도 | sort_order |
|---|---|---|---|
| `chart-key-five` | 차트의 핵심 5: 이격도·RSI·MACD·볼린저밴드·거래량 | intermediate | 14 |
| `sector-kpi-cheatsheet` | 섹터별 핵심 KPI — 반도체/은행/제약/리츠/에너지 | intermediate | 15 |

#### C. `risk` 보강 (3개) — 정량 리스크

| slug | 제목 | 난이도 | sort_order |
|---|---|---|---|
| `position-sizing` | 포지션 사이징 — 1트레이드 1~2% 룰과 Kelly 직관 | intermediate | 22 |
| `risk-adjusted-return` | 위험조정수익률 4지표 — 베타·샤프·소르티노·MDD | intermediate | 23 |
| `correlation-trap` | 상관관계의 함정 — 진짜 분산 vs 가짜 분산 | intermediate | 24 |

#### D. `macro` 보강 (1개) — 매크로 시그널

| slug | 제목 | 난이도 | sort_order |
|---|---|---|---|
| `yield-curve-inversion` | 국채 금리 곡선 역전 — 침체 12개월 선행 신호 | intermediate | 33 |

#### E. `stories` 보강 (3개) — 시의성·흥미

| slug | 제목 | 난이도 | sort_order |
|---|---|---|---|
| `ai-vs-dotcom` | 2024-2025 AI CapEx 사이클 vs 1999 닷컴 — 같은 그림인가 | intermediate | 55 |
| `liquidity-mania-2020` | 밈주식·SPAC·ARKK 광기 (2020-2022) — 유동성이 만든 거품 | intermediate | 56 |
| `tesla-eight-years` | 테슬라 8년 복기 — 반대매매와 컨빅션의 경제학 | beginner | 57 |

#### F. `tools` ★신규 카테고리 (3개) — 본 시스템 사용 가이드

| slug | 제목 | 난이도 | sort_order | 참조 DB 필드 / 모듈 |
|---|---|---|---|---|
| `factor-six-axes` | 정량 팩터 6축 읽기 — r1m/3m/6m/12m·vol60·volume_ratio + percentile | intermediate | 60 | `factor_snapshot` JSONB (v30), `analyzer/factor_engine.py` |
| `market-regime-reading` | 시장 레짐 읽기 — above_200ma·vol_regime·drawdown 신호 해석 | intermediate | 61 | `market_regime` JSONB (v31), `analyzer/regime.py` |
| `pre-market-briefing-guide` | 프리마켓 브리핑 활용법 — 미국 야간 → 한국 수혜 매핑 | beginner | 62 | `pre_market_briefings` (v34), `analyzer/briefing_main.py` |

### 2.3 컨텐츠 품질 기준 (기존 26개와 동등)

- `summary`: 1~2줄 요약 (목록 화면 노출용)
- `content`: 1,000~2,000자 한국어 마크다운, 표 1~2개 포함
- `examples`: JSON 1~2개 사례 (`title`/`description`/`period`/`lesson` 키 — 기존 토픽과 동일 스키마)
- 사례는 **한국 시장 우선**, 미국·글로벌은 보조. `tools` 카테고리는 본 시스템 실제 출력 예시(가상이 아닌 실데이터 패턴)를 사용.
- 거장 인용·교훈은 `stories`에 이미 충분 — 신규 토픽은 **데이터·사례 중심**.

## 3. 변경 영역

### 3.1 시드 모듈 (8개 파일)

```
shared/db/migrations/seeds_education/
├── __init__.py            ← MOD: tools 모듈 import + V35_SLUGS 집계
├── basics.py              ← MOD: TOPICS에 2개 추가 + V35_SLUGS 추가
├── analysis.py            ← MOD: 2개 추가 + V35_SLUGS
├── risk.py                ← MOD: 3개 추가 + V35_SLUGS
├── macro.py               ← MOD: 1개 추가 + V35_SLUGS
├── stories.py             ← MOD: 3개 추가 + V35_SLUGS
└── tools.py               ← NEW: 카테고리 신설, TOPICS 3개 + V35_SLUGS
```

`__init__.py`는 v24 패턴 그대로 — `_MODULES = [basics, analysis, risk, macro, practical, stories, tools]` (tools 추가)와 `NEW_TOPICS_V35` 산출 로직 추가.

### 3.2 마이그레이션 (3개 파일)

| 파일 | 변경 |
|---|---|
| `shared/db/schema.py` | `SCHEMA_VERSION = 34` → `35` |
| `shared/db/migrations/__init__.py` | `_MIGRATIONS` dict에 `35: _v._migrate_to_v35,` 한 줄 추가 |
| `shared/db/migrations/versions.py` | `_migrate_to_v35(cur)` 함수 추가 — v24와 동일 패턴 (`NEW_TOPICS_V35` import → ON CONFLICT INSERT → `schema_version` UPDATE) |

`education_topics.category` 컬럼은 `VARCHAR(50)` *CHECK 제약 없음*으로 확인됨 ([versions.py:800](../../../shared/db/migrations/versions.py#L800) 인덱스만 있음). `'tools'` 값 추가에 ALTER 불필요.

### 3.3 라벨 매핑 (1개 파일)

| 파일 | 변경 |
|---|---|
| `api/routes/education.py:23-30` | `_EDU_CATEGORIES` 딕셔너리에 `"tools": "도구·시스템 가이드"` 한 줄 추가 |

UI 템플릿(`education.html`, `education_topic.html`, `education_chat_list.html`, `education_chat_room.html`)은 `_EDU_CATEGORIES`를 통해 라벨 조회하므로 **추가 변경 없음**.

### 3.4 변경 없음 (검증)

- `tier_limits.py`: `EDU_CHAT_DAILY_TURNS`는 토픽 수와 무관 — 카테고리 추가에 무영향.
- `education_engine.py`: 토픽별 시스템 프롬프트 주입 방식 동일 — 신규 토픽도 자동 지원.
- 권한: 교육은 Free 티어 접근 가능 정책 유지 (`tools` 포함).

## 4. 멱등성 / 롤백

### 멱등성
- 신규 DB: v21에서 `ALL_TOPICS` 시드 → v24/v35 마이그레이션은 ON CONFLICT NO-OP → 결과 동일.
- 기존 운영 DB (현재 v34): `_migrate_to_v35`가 신규 14 토픽만 INSERT, 기존 26 토픽은 ON CONFLICT 보호.
- 재실행 안전 — `schema_version`에 v35 INSERT는 `ON CONFLICT (version) DO NOTHING`.

### 롤백
- 잘못된 토픽 발견 시: `DELETE FROM education_topics WHERE slug IN (...)` 수동 SQL로 제거 (CASCADE로 `education_chat_sessions.topic_id`는 SET NULL — 기존 채팅은 토픽명만 사라지고 보존).
- 카테고리 자체 철회: `DELETE FROM education_topics WHERE category = 'tools'` 후 `_EDU_CATEGORIES`에서 키 제거.

## 5. 검증 계획

| 항목 | 방법 |
|---|---|
| 마이그레이션 멱등성 | `pytest tests/test_db_migrations.py`(존재 확인 후) 또는 신규 DB·기존 DB 양쪽 `init_db()` 2회 호출 → row count 변화 0 |
| 토픽 라우터 정상 응답 | `GET /education/topics` → 40개 반환, `GET /education/topics?category=tools` → 3개 반환 |
| UI 카테고리 그룹화 | `/pages/education` 페이지 렌더링 → 7개 카테고리 그룹 표시, `tools` 라벨 "도구·시스템 가이드" 노출 확인 |
| 챗 엔진 토픽 주입 | 신규 토픽 1개로 채팅 세션 생성 → 시스템 프롬프트에 토픽 content 포함되는지 로그 확인 |
| `JSON.dumps` 직렬화 | 각 토픽 `examples`가 `json.dumps()`로 직렬화 후 INSERT 시 PostgreSQL JSONB 파싱 성공 |

## 6. 작업 순서 (구현 플랜 작성 시 분해)

1. **시드 컨텐츠 작성** — 토픽 14개 본문 + examples 작성 (가장 많은 시간 소요, 카테고리별 커밋 분리 권장)
2. **시드 모듈 신설/수정** — `tools.py` NEW + 5개 모듈 MOD + `__init__.py` 집계 갱신
3. **마이그레이션 추가** — `versions.py:_migrate_to_v35` + `__init__.py:_MIGRATIONS` + `schema.py:SCHEMA_VERSION`
4. **라벨 매핑** — `_EDU_CATEGORIES`에 `tools` 추가
5. **검증** — 신규/기존 DB 양쪽 마이그레이션 실행 + UI 확인 + 챗 엔진 1개 토픽 스모크 테스트
6. **커밋** — 카테고리별 분리 (e.g., `feat(edu): tier A analysis 토픽 2개 추가`)

## 7. Out of Scope (후속 작업 후보)

- `derivatives` / `portfolio` / `crypto` 신규 카테고리 — 이번 v35는 `tools`만 도입
- advanced 난이도 토픽 세트 — 이번 분량 모두 intermediate 이하
- 토픽별 다국어 (영문) 컨텐츠 — 한국어 단일
- Cockpit 내 "관련 교육 토픽" 위젯 — 별도 UI 작업 필요
- 기존 토픽 내용 보강·갱신 — 신규 추가에 집중

---

## 부록: 컨텐츠 가이드 (작성자용)

### `tools` 카테고리 작성 톤
- "AI가 한 거 해석하는 법"이 아니라 **"이 데이터가 뭘 보여주는지 + 어떻게 활용할지"**
- 본 시스템 실제 출력 패턴 1~2개 인용 (실 DB 조회 결과 예시 그대로)
- 한계 명시 — "팩터 percentile은 60일 거래대금 ≥ 임계값 종목군 한정" 등

### `analysis`/`risk`/`macro` 작성 톤
- 일반론 → 한국 시장 적용 → 한 줄 교훈
- 실제 종목/사건 1개 이상 (확인 가능한 기간 명시)
- 표 1개 이상 (비교/구조 정리)

### `stories` 작성 톤
- 사실 베이스 + 교훈 1~2줄
- 시점·인물·수치 명시 (날짜·티커·수익률)
- 본 시스템 데이터로 *재현 가능한* 패턴이면 명시 (e.g., "동일한 vol60 + drawdown 패턴이 2024년에도 나타남")
