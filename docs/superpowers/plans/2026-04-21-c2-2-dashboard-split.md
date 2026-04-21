# C2.2 — `dashboard.html` 분할 (페이지 섹션 partial 추출) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `api/templates/dashboard.html`(660줄)을 7개 섹션 partial(`partials/dashboard/*.html`)로 분리하고, 상위 템플릿을 `{% include %}` 기반 ~80줄 오케스트레이터로 축소.

**Architecture:** `{% include %}` 기반(매크로 아님) — 페이지 전용 섹션은 `partials/<page>/` 서브디렉터리로 분류하여 기존 크로스 페이지 partial(`partials/_ad_slot.html` 등)과 명확히 구분. 각 partial은 자체적으로 매크로(`_macros/*`)를 import하여 파일 독립성 확보. 4개 커밋(스펙 → partial 생성 → 본문 교체 → 검증 메모) 중 스펙 커밋은 이미 완료(`4aa5397`).

**Tech Stack:** Jinja2, FastAPI, Python 3.10+, pytest

**Spec:** `docs/superpowers/specs/2026-04-21-c2-2-dashboard-split-design.md`

---

## Task 0: Baseline 캡처

분할 전 dashboard.html 렌더 결과의 SHA-256과 라인 수를 기록하여 분할 후 parity 비교 기준을 만든다.

**Files:**
- Read: `api/templates/dashboard.html`

- [ ] **Step 0.1: dashboard.html 라인 수 확인**

Run:
```bash
wc -l api/templates/dashboard.html
```

Expected: `660 api/templates/dashboard.html`

- [ ] **Step 0.2: 매크로 호출 위치 grep**

Run:
```bash
grep -n "{{ change_indicator\|{{ risk_gauge\|{{ bullet_chart\|{{ conf_ring\|{{ external_links\|{{ yield_curve\|{{ theme_header\|{{ indicator_tags\|{{ discovery_stackbar\|{{ sector_chips" api/templates/dashboard.html
```

Expected (15개 호출, 8개 매크로):
```
23:                {{ issue_count }}{{ change_indicator(issue_delta) }}
29:                {{ theme_count }}{{ change_indicator(theme_delta) }}
35:                {{ buy_count }}{{ change_indicator(buy_delta) }}
127:            {{ risk_gauge(risk_pct, session.risk_temperature) }}
136:        {{ discovery_stackbar(discovery_counts) }}
144:        {{ sector_chips(top_sectors) }}
236:            {{ risk_gauge(risk_pct, session.risk_temperature) }}
304:        {{ yield_curve(bond_yields) }}
329:    {{ theme_header(theme, tk if tk else none) }}
331:    {{ indicator_tags(theme.key_indicators) }}
430:        {{ external_links(pk.ticker, pk.market or '') }}
438:        {{ bullet_chart(pk.current_price, pk.target_price_low, pk.upside_pct, pk.currency, pk.price_pct) }}
448:            {{ conf_ring(pk.theme_confidence, 22) }}
```

(NOTE: ad_slot은 `{{ ad_slot('banner') }}`로 별도 — partial이지 매크로지만 호출 형태가 동일. dashboard.html 라인 11에 1개 호출.)

- [ ] **Step 0.3: 현재 `partials/dashboard/` 디렉터리 부재 확인**

Run:
```bash
ls api/templates/partials/dashboard/ 2>&1 | head -3
```

Expected: `ls: cannot access 'api/templates/partials/dashboard/': No such file or directory`

- [ ] **Step 0.4: pytest baseline 캡처**

Run:
```bash
python -m pytest tests/ --tb=no -q 2>&1 | tail -5
```

Expected: 실패 개수 기록 (예: `17 failed, 52 passed`). 분할 후 동일 실패 개수 유지가 목표.

이 Task는 측정만 수행하며 commit하지 않는다.

---

## Task 1: `partials/dashboard/` 디렉터리 + 7개 partial 파일 생성 (VERBATIM 이동)

`dashboard.html`에서 7개 섹션의 본문을 byte-for-byte 복사하여 새 파일로 이동. 이 시점에는 `dashboard.html`이 아직 바뀌지 않았으므로 새 partial은 dead file 상태이며 렌더 결과에 영향 없음.

**Files:**
- Create: `api/templates/partials/dashboard/_hero_row1.html`
- Create: `api/templates/partials/dashboard/_hero_row2.html`
- Create: `api/templates/partials/dashboard/_market_summary.html`
- Create: `api/templates/partials/dashboard/_yield_curve.html`
- Create: `api/templates/partials/dashboard/_themes_list.html`
- Create: `api/templates/partials/dashboard/_top_picks.html`
- Create: `api/templates/partials/dashboard/_news_by_category.html`
- (Unchanged): `api/templates/dashboard.html`

### Step 1.1: 디렉터리 생성

Run:
```bash
mkdir -p api/templates/partials/dashboard
```

### Step 1.2: `partials/dashboard/_hero_row1.html` 작성

원본 라인:
- 18-88: Hero Row 1 HTML (KPI 6개 + Track Record 위젯)
- 148-224: Track Record 렌더 IIFE `<script>`

이 partial은 두 영역을 합친다 (HTML + 위젯 JS는 한 단위). 사이의 Row 2(90-147)는 `_hero_row2.html`로 별도 분리되므로, 합치는 시점에 자연스럽게 인접.

매크로 의존: `change_indicator` (`_macros/proposal.html`)

파일 내용:

```jinja
{#
  Dashboard — Hero Row 1 (KPI Strip + Track Record 위젯)
  출처: api/templates/dashboard.html (C2.2, 2026-04-21)
  CONTEXT:
    - issue_count, issue_delta (int)
    - theme_count, theme_delta (int)
    - buy_count, buy_delta (int)
    - high_conviction_count (int)
    - early_signal_count (int)
    - total_alloc (float)
    - avg_confidence (float, 0~1)
  매크로 의존:
    - change_indicator from _macros/proposal.html
  주의:
    - 하단 <script> 블록은 위젯 DOM(dash-tr-*)과 한 단위.
      C3 트랙에서 외부 JS로 분리 예정.
#}
{% from "_macros/proposal.html" import change_indicator %}

{# Row 1 — KPI Strip(좌) + Track Record(우) 통합 #}
<div class="hero-row1">
    <div class="stat-grid stat-grid-6 kpi-strip kpi-strip-tight">
        <div class="stat-card">
            <div class="stat-value">
                {{ issue_count }}{{ change_indicator(issue_delta) }}
            </div>
            <div class="stat-label">글로벌 이슈</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">
                {{ theme_count }}{{ change_indicator(theme_delta) }}
            </div>
            <div class="stat-label">투자 테마 · <span class="stat-inline-sub">신뢰도 {{ "%.0f"|format(avg_confidence * 100) }}%</span></div>
        </div>
        <div class="stat-card">
            <div class="stat-value">
                {{ buy_count }}{{ change_indicator(buy_delta) }}
            </div>
            <div class="stat-label">매수 제안</div>
        </div>
        <div class="stat-card stat-card-accent">
            <div class="stat-value">{{ high_conviction_count }}</div>
            <div class="stat-label">높은 확신도</div>
        </div>
        <div class="stat-card stat-card-signal">
            <div class="stat-value">{{ early_signal_count }}</div>
            <div class="stat-label">얼리 시그널</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ "%.1f"|format(total_alloc) }}%</div>
            <div class="stat-label">총 비중</div>
        </div>
    </div>

    <div class="dash-tr-widget hero-tr">
        <div class="tr-visual">
            <div class="tr-donut-wrap">
                <svg viewBox="0 0 80 80" width="80" height="80" class="tr-donut" aria-label="승률">
                    <circle cx="40" cy="40" r="32" fill="none" stroke="var(--border)" stroke-width="8"/>
                    <circle cx="40" cy="40" r="32" fill="none" stroke="var(--color-positive)" stroke-width="8"
                            stroke-dasharray="0 201" stroke-linecap="round"
                            transform="rotate(-90 40 40)" id="dash-tr-donut-arc"/>
                    <text x="40" y="38" text-anchor="middle" fill="var(--text)" font-size="14" font-weight="700" id="dash-tr-donut-pct">—</text>
                    <text x="40" y="52" text-anchor="middle" fill="var(--text-muted)" font-size="9">승률</text>
                </svg>
            </div>
            <div class="tr-return-block">
                <div class="dash-tr-title" style="margin-bottom:6px;">
                    📈 Track Record
                    <a href="/pages/track-record" class="widget-more" style="float:right;">자세히 →</a>
                </div>
                <div class="tr-bar-label">평균 수익률 · <span id="dash-tr-period-label">3M</span></div>
                <div class="tr-bar-track">
                    <div class="tr-bar-zero"></div>
                    <div class="tr-bar-fill" id="dash-tr-bar-fill" style="width:0%;left:50%;"></div>
                </div>
                <div class="tr-bar-value" id="dash-tr-bar-value">—</div>
                <div class="tr-sample" id="dash-tr-sample">샘플 로드 중...</div>
            </div>
        </div>
        <div class="dash-tr-right">
            <div class="dash-tr-tabs" role="tablist" aria-label="기간 선택">
                <button type="button" class="dash-tr-tab" data-period="1m" role="tab">1M</button>
                <button type="button" class="dash-tr-tab active" data-period="3m" role="tab" aria-selected="true">3M</button>
                <button type="button" class="dash-tr-tab" data-period="6m" role="tab">6M</button>
                <button type="button" class="dash-tr-tab" data-period="1y" role="tab">1Y</button>
            </div>
        </div>
    </div>
</div>

<script>
(function() {
    var cache = null;
    var labelByKey = { '1m': '1M', '3m': '3M', '6m': '6M', '1y': '1Y' };
    var DONUT_CIRC = 201;  // 2π * 32

    function render(period) {
        var p = {};
        if (cache && cache.overview && cache.overview.periods) p = cache.overview.periods[period] || {};

        var pctEl = document.getElementById('dash-tr-donut-pct');
        var arcEl = document.getElementById('dash-tr-donut-arc');
        var fillEl = document.getElementById('dash-tr-bar-fill');
        var valEl = document.getElementById('dash-tr-bar-value');
        var sampEl = document.getElementById('dash-tr-sample');
        var plEl = document.getElementById('dash-tr-period-label');
        if (plEl) plEl.textContent = labelByKey[period];

        if (p.win_rate_pct == null && !p.n) {
            if (pctEl) pctEl.textContent = '—';
            if (arcEl) arcEl.setAttribute('stroke-dasharray', '0 ' + DONUT_CIRC);
            if (fillEl) fillEl.style.width = '0%';
            if (valEl) { valEl.textContent = '샘플 없음'; valEl.className = 'tr-bar-value'; }
            if (sampEl) sampEl.textContent = '과거 모멘텀 기준 · 샘플 0건';
            return;
        }

        var winRate = p.win_rate_pct != null ? p.win_rate_pct : 0;
        var arc = (winRate / 100 * DONUT_CIRC).toFixed(1);
        if (pctEl) pctEl.textContent = winRate.toFixed(0) + '%';
        if (arcEl) arcEl.setAttribute('stroke-dasharray', arc + ' ' + DONUT_CIRC);

        var avg = p.avg_return_pct;
        if (avg != null) {
            var absAvg = Math.min(Math.abs(avg), 50);  // 50%를 max로 clamp
            var widthPct = (absAvg / 50 * 50).toFixed(1);  // 바 전체의 50%를 max
            if (fillEl) {
                fillEl.style.width = widthPct + '%';
                if (avg >= 0) {
                    fillEl.style.left = '50%';
                    fillEl.classList.remove('tr-bar-neg');
                    fillEl.classList.add('tr-bar-pos');
                } else {
                    fillEl.style.left = (50 - parseFloat(widthPct)) + '%';
                    fillEl.classList.remove('tr-bar-pos');
                    fillEl.classList.add('tr-bar-neg');
                }
            }
            if (valEl) {
                valEl.textContent = (avg >= 0 ? '+' : '') + avg.toFixed(2) + '%';
                valEl.className = 'tr-bar-value ' + (avg >= 0 ? 'color-green' : 'color-red');
            }
        } else {
            if (fillEl) fillEl.style.width = '0%';
            if (valEl) { valEl.textContent = '—'; valEl.className = 'tr-bar-value'; }
        }
        if (sampEl) sampEl.textContent = '샘플 ' + (p.n || 0) + '건';
    }

    document.querySelectorAll('.dash-tr-tab').forEach(function(btn) {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.dash-tr-tab').forEach(function(b) {
                b.classList.remove('active');
                b.setAttribute('aria-selected', 'false');
            });
            btn.classList.add('active');
            btn.setAttribute('aria-selected', 'true');
            render(btn.getAttribute('data-period'));
        });
    });

    fetch('/api/track-record/summary')
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) { cache = data; render('3m'); })
        .catch(function() { render('3m'); });
})();
</script>
```

**CRITICAL**: 매크로 호출/문자열/속성/주석을 byte-for-byte 그대로 복사. 들여쓰기·공백 변경 금지.

### Step 1.3: `partials/dashboard/_hero_row2.html` 작성

원본 라인: 90-147 (Watchlist / Discovery / Sectors 3-col)

매크로 의존: `risk_gauge` (proposal), `discovery_stackbar`, `sector_chips` (theme)

파일 내용:

```jinja
{#
  Dashboard — Hero Row 2 (워치리스트 / 발굴 유형 / 주요 섹터 3-col)
  출처: api/templates/dashboard.html (C2.2, 2026-04-21)
  CONTEXT:
    - current_user (optional): 로그인 사용자 (분기)
    - watched_in_today (list): 오늘 분석에 포함된 관심 종목
    - risk_pct (float): 비로그인 사용자에게 시장 상태 게이지로 표시
    - session (object): analysis_date, risk_temperature
    - discovery_counts (dict, optional)
    - top_sectors (list, optional)
  매크로 의존:
    - risk_gauge from _macros/proposal.html
    - discovery_stackbar, sector_chips from _macros/theme.html
#}
{% from "_macros/proposal.html" import risk_gauge %}
{% from "_macros/theme.html" import discovery_stackbar, sector_chips %}

{# Row 2 — 관심(1) + 발굴 유형(2) + 주요 섹터(3) — 3-col #}
<div class="hero-grid-3col">
    {# 1. 워치리스트 (로그인) 또는 시장 상태 게이지 (비로그인) #}
    {% if current_user %}
    <div class="watchlist-widget">
        <div class="widget-header">
            <span>⭐ 내 관심 종목</span>
            <a href="/pages/watchlist" class="widget-more">전체 →</a>
        </div>
        <div class="watchlist-quick">
            {% if watched_in_today %}
                {% for w in watched_in_today[:6] %}
                <a href="/pages/proposals/history/{{ w.ticker }}" class="wl-item {% if w.in_top_picks %}wl-item-hot{% endif %}">
                    <span class="wl-ticker">{{ w.ticker }}</span>
                    <span class="wl-name">{{ w.asset_name }}</span>
                    {% if w.current_price %}
                    <span class="wl-price">{{ w.current_price|fmt_price(w.currency) }}</span>
                    {% endif %}
                    {% if w.in_top_picks %}
                    <span class="wl-badge-pick">Top #{{ w.pick_rank }}</span>
                    {% endif %}
                </a>
                {% endfor %}
            {% else %}
                <div class="wl-empty">오늘 분석에 관심 종목이 없습니다.<br>
                    <a href="/pages/watchlist" style="color:var(--accent);">관심 종목 관리 →</a>
                </div>
            {% endif %}
        </div>
    </div>
    {% else %}
    <div class="watchlist-widget">
        <div class="widget-header">
            <span>🌡️ 시장 상태</span>
            <span style="font-size:12px;color:var(--text-muted);">{{ session.analysis_date }}</span>
        </div>
        <div style="display:flex;justify-content:center;padding:12px 0;">
            {{ risk_gauge(risk_pct, session.risk_temperature) }}
        </div>
    </div>
    {% endif %}

    {# 2. 발굴 유형 분포 (옵션) #}
    {% if discovery_counts %}
    <div class="insight-card insight-card-compact">
        <div class="insight-title">발굴 유형 분포</div>
        {{ discovery_stackbar(discovery_counts) }}
    </div>
    {% endif %}

    {# 3. 주요 섹터 Top 5 (옵션) #}
    {% if top_sectors %}
    <div class="insight-card insight-card-compact">
        <div class="insight-title">주요 섹터 Top 5</div>
        {{ sector_chips(top_sectors) }}
    </div>
    {% endif %}
</div>
```

### Step 1.4: `partials/dashboard/_market_summary.html` 작성

원본 라인: 231-277 (Market Summary 접이식 블록)

매크로 의존: `risk_gauge` (proposal)

파일 내용:

```jinja
{#
  Dashboard — Market Summary (접이식 블록)
  출처: api/templates/dashboard.html (C2.2, 2026-04-21)
  CONTEXT:
    - session (object): analysis_date, market_summary
    - current_user (optional): 로그인 시 inline risk gauge 표시
    - risk_pct (float)
  매크로 의존:
    - risk_gauge from _macros/proposal.html
  주의:
    - 토글 버튼의 onclick 인라인 JS는 DOM id 'market-summary-block' 참조.
      모바일 기본 접힘 JS는 dashboard.html {% block scripts %}에 잔류.
#}
{% from "_macros/proposal.html" import risk_gauge %}

{# Market Summary (접이식, 모바일 기본 접힘) #}
<div class="market-summary" id="market-summary-block">
    <div class="label" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
        <span>Market Summary &middot; {{ session.analysis_date }}</span>
        {% if current_user %}
            {# 로그인 사용자: hero에 없으므로 여기 게이지 표시 #}
            {{ risk_gauge(risk_pct, session.risk_temperature) }}
        {% endif %}
    </div>
    <div class="summary-body">
    {% if session.market_summary %}
        {% set summary_text = session.market_summary %}
        {% set has_sections = '[시장 환경]' in summary_text or '[핵심 이슈]' in summary_text %}
        {% if has_sections %}
            {% for line in summary_text.split('\n') %}
                {% set trimmed = line.strip() %}
                {% if trimmed == '' %}
                {% elif trimmed.startswith('[') and ']' in trimmed %}
                    {% set section_title = trimmed.split(']')[0][1:] %}
                    {% set section_content = trimmed.split(']', 1)[1].strip() %}
                    <div class="summary-section">
                        <div class="summary-section-title">{{ section_title }}</div>
                        {% if section_content %}
                        <div class="summary-section-content">{{ section_content }}</div>
                        {% endif %}
                    </div>
                {% elif trimmed.startswith('★') %}
                    <div class="summary-highlight summary-highlight-star">{{ trimmed }}</div>
                {% elif trimmed.startswith('▸') %}
                    <div class="summary-highlight summary-highlight-theme">{{ trimmed }}</div>
                {% elif trimmed.startswith('⚠') %}
                    <div class="summary-highlight summary-highlight-warn">{{ trimmed }}</div>
                {% else %}
                    <div class="summary-section-content">{{ trimmed }}</div>
                {% endif %}
            {% endfor %}
        {% else %}
            {{ summary_text }}
        {% endif %}
    {% else %}
        (분석 요약 없음)
    {% endif %}
    </div>
    <button type="button" class="market-summary-toggle"
            onclick="document.getElementById('market-summary-block').classList.toggle('collapsed'); this.textContent = document.getElementById('market-summary-block').classList.contains('collapsed') ? '▼ 시장 요약 펼치기' : '▲ 접기';">
        ▲ 접기
    </button>
</div>
```

### Step 1.5: `partials/dashboard/_yield_curve.html` 작성

원본 라인: 280-307 (`{% if bond_yields %}` 가드 포함)

매크로 의존: `yield_curve` (common)

파일 내용:

```jinja
{#
  Dashboard — Yield Curve (한국 금리 환경)
  출처: api/templates/dashboard.html (C2.2, 2026-04-21)
  CONTEXT:
    - bond_yields (dict, optional):
        yield_curve_status, spread_10y_2y, corp_aa
  매크로 의존:
    - yield_curve from _macros/common.html
  주의:
    - 호출부 dashboard.html이 {% if bond_yields %}로 가드.
      partial 자체는 None일 때 호출되지 않는다고 가정.
#}
{% from "_macros/common.html" import yield_curve %}

{# 금리 Yield Curve 차트 #}
<div class="card" style="margin-bottom:20px;">
    <div class="card-header">
        <span class="card-title">🏛️ 한국 금리 환경</span>
        <span style="font-size:13px;color:var(--text-muted);">
            {% if bond_yields.yield_curve_status == 'normal' %}
            <span style="color:var(--success);">● 정상 커브</span>
            {% elif bond_yields.yield_curve_status == 'flat' %}
            <span style="color:var(--warning);">● 평탄 커브</span>
            {% elif bond_yields.yield_curve_status == 'inverted' %}
            <span style="color:var(--danger);">● 역전 커브</span>
            {% endif %}
            {% if bond_yields.spread_10y_2y is not none %}
            &middot; 스프레드(10Y-2Y)
            <span {% if bond_yields.spread_10y_2y < 0 %}style="color:var(--danger);font-weight:600;"{% else %}style="color:var(--text);"{% endif %}>
                {{ bond_yields.spread_10y_2y }}%p
            </span>
            {% endif %}
            {% if bond_yields.corp_aa %}
            &middot; 회사채AA- {{ bond_yields.corp_aa }}%
            {% endif %}
        </span>
    </div>
    <div class="card-body" style="padding:16px;">
        {{ yield_curve(bond_yields) }}
    </div>
</div>
```

### Step 1.6: `partials/dashboard/_themes_list.html` 작성

원본 라인: 309-374 (투자 테마 + 소멸 테마 details)

매크로 의존: `theme_header`, `indicator_tags` (theme)

파일 내용:

```jinja
{#
  Dashboard — Investment Themes (투자 테마 + 소멸 테마)
  출처: api/templates/dashboard.html (C2.2, 2026-04-21)
  CONTEXT:
    - themes (list): 활성 투자 테마
    - active_tracking (list): 테마 연속성 추적 정보
    - tier (str, optional): 'free'면 theme_view_limit 적용
    - theme_view_limit (int, optional): None이면 999 (무제한)
    - watched_tickers (set/list): 관심 종목 ticker 셋
    - session (object): id
    - disappeared_themes (list, optional): 최근 3일 내 소멸 테마
  매크로 의존:
    - theme_header, indicator_tags from _macros/theme.html
#}
{% from "_macros/theme.html" import theme_header, indicator_tags %}

{# 투자 테마 (테마 설명 + 종목 태그, 소멸 테마 인라인 통합) #}
{% set free_theme_limit = theme_view_limit if theme_view_limit is not none else 999 %}
{% set is_free = (tier or 'free') == 'free' %}
<div class="section-title">투자 테마
    <a href="/pages/sessions/{{ session.id }}" class="btn btn-primary" style="float:right;font-size:12px;padding:4px 12px;">상세 보기</a>
</div>
{% for theme in themes %}
{% set tk = none %}
{% for t in active_tracking %}
    {% if t.theme_name == theme.theme_name %}{% set tk = t %}{% endif %}
{% endfor %}
{% set locked = is_free and loop.index0 >= free_theme_limit %}
<div class="card {% if locked %}card-locked{% endif %}" {% if locked %}aria-hidden="true"{% endif %}>
    {% if locked %}
    <div class="card-lock-overlay">
        <div class="card-lock-icon" aria-hidden="true">🔒</div>
        <div class="card-lock-title">Pro로 업그레이드하고 모든 테마 열람</div>
        <a href="/pages/pricing" class="btn btn-primary" style="margin-top:10px;">업그레이드 →</a>
    </div>
    {% endif %}
    {{ theme_header(theme, tk if tk else none) }}
    <div class="detail-text">{{ theme.description }}</div>
    {{ indicator_tags(theme.key_indicators) }}

    {% if theme.proposals %}
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;">
        {% for p in theme.proposals %}
        <a href="/pages/proposals/history/{{ p.ticker }}" style="font-size:13px;padding:3px 10px;border-radius:8px;background:{% if p.ticker in watched_tickers %}rgba(251,191,36,0.12){% else %}rgba(79,140,255,0.08){% endif %};border:1px solid {% if p.ticker in watched_tickers %}rgba(251,191,36,0.4){% else %}var(--border){% endif %};text-decoration:none;color:inherit;display:inline-flex;align-items:center;gap:4px;">
            {% if p.ticker in watched_tickers %}<span style="font-size:11px;" title="관심 종목">&#9733;</span>{% endif %}
            <span class="badge badge-{{ p.action }}" style="font-size:11px;padding:1px 6px;">{{ p.action|upper }}</span>
            <span style="font-weight:600;">{{ p.ticker }}</span>
            <span style="color:var(--text-muted);">{{ p.asset_name }}</span>
            {% if p.conviction == 'high' %}<span style="color:var(--green);font-size:11px;">&#9679;</span>{% endif %}
            {% if p.target_allocation %}<span style="color:var(--text-muted);font-size:11px;">{{ p.target_allocation }}%</span>{% endif %}
        </a>
        {% endfor %}
    </div>
    {% endif %}
</div>
{% endfor %}

{# 소멸 테마 인라인 통합 #}
{% if disappeared_themes %}
<details style="margin:16px 0;">
    <summary style="cursor:pointer;color:var(--text-muted);font-size:13px;padding:8px 0;">
        최근 3일 내 소멸 테마 {{ disappeared_themes|length }}개 보기
    </summary>
    <div style="margin-top:8px;">
        {% for t in disappeared_themes %}
        <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 14px;background:var(--bg-card);border:1px solid var(--border);border-radius:6px;opacity:0.6;margin-bottom:6px;">
            <span style="display:flex;align-items:center;gap:8px;">
                <span class="tracking-badge tracking-badge-gone">소멸</span>
                {% if t.theme_key %}
                <a href="/pages/themes/history/{{ t.theme_key }}" style="font-weight:600;text-decoration:none;color:inherit;">{{ t.theme_name }}</a>
                {% else %}
                <span style="font-weight:600;">{{ t.theme_name }}</span>
                {% endif %}
            </span>
            <span style="font-size:13px;color:var(--text-muted);">
                마지막: {{ t.last_seen_date }} &middot; 총 {{ t.appearances }}회
            </span>
        </div>
        {% endfor %}
    </div>
</details>
{% endif %}
```

### Step 1.7: `partials/dashboard/_top_picks.html` 작성

원본 라인: 381-540 (`{% if top_picks %}` 내부 본문 — 가드는 dashboard.html에 유지)

매크로 의존: `bullet_chart` (proposal), `conf_ring` (theme), `external_links` (common)

파일 내용:

```jinja
{#
  Dashboard — Top Picks 그리드
  출처: api/templates/dashboard.html (C2.2, 2026-04-21)
  CONTEXT:
    - top_picks (list, non-empty): 호출부 dashboard.html이 {% if top_picks %}로 가드
        각 pk: rank, ticker, market, asset_name, sector, currency,
                current_price, target_price_low, upside_pct, price_pct,
                conviction, has_stock_analysis, discovery_type,
                foreign_net_buy_signal, squeeze_risk, source,
                score_final, score_rule, score_breakdown,
                theme_key, theme_name, theme_confidence,
                rationale_text, proposal_rationale, key_risk,
                is_watched
    - current_user (optional): 로그인 시 watchlist 토글 버튼 표시
  매크로 의존:
    - bullet_chart from _macros/proposal.html
    - conf_ring from _macros/theme.html
    - external_links from _macros/common.html
  주의:
    - 카드의 onclick="toggleWatchlist(this)" 호출부는 partial에 유지.
      함수 정의는 dashboard.html {% block scripts %}에 잔류.
#}
{% from "_macros/proposal.html" import bullet_chart %}
{% from "_macros/theme.html" import conf_ring %}
{% from "_macros/common.html" import external_links %}

{# Top Picks — 오늘의 추천 종목 #}
<div class="section-title">
    오늘의 Top Picks
    {% set _is_ai = top_picks[0].source == 'ai_rerank' %}
    <span style="margin-left:10px;font-size:12px;font-weight:500;padding:2px 8px;border-radius:8px;vertical-align:middle;
                 {% if _is_ai %}background:rgba(168,85,247,0.12);color:#c084fc;border:1px solid rgba(168,85,247,0.35);{% else %}background:rgba(79,140,255,0.10);color:#7aa8ff;border:1px solid rgba(79,140,255,0.30);{% endif %}">
        {% if _is_ai %}AI 재정렬{% else %}룰 기반{% endif %}
    </span>
    <span style="margin-left:6px;font-size:12px;color:var(--text-muted);font-weight:400;">
        투자 제안 중 포트폴리오 관점에서 선별
    </span>
</div>
<div class="top-picks-grid">
    {% for pk in top_picks %}
    <div class="top-pick-card{% if pk.is_watched %} top-pick-watched{% endif %}">
        {# Zone A: 식별 #}
        <div class="top-pick-header">
            <div class="top-pick-rank-wrap">
                <span class="top-pick-rank">#{{ pk.rank }}</span>
                {% if pk.rank == 1 %}<span class="top-pick-trophy" title="Top Pick">&#128293;</span>{% endif %}
            </div>
            <div class="top-pick-badges">
                {# 핵심 뱃지만 제한 표시 (최대 3개 + 이상 신호) #}
                {% if pk.discovery_type == 'early_signal' %}
                <span class="top-pick-badge top-pick-badge-early">&#127793; 얼리</span>
                {% elif pk.discovery_type == 'deep_value' %}
                <span class="top-pick-badge top-pick-badge-deepvalue">&#128176; 딥밸류</span>
                {% elif pk.discovery_type == 'contrarian' %}
                <span class="top-pick-badge top-pick-badge-contrarian">&#8635; 역발상</span>
                {% endif %}
                {% if pk.conviction == 'high' %}
                <span class="top-pick-badge top-pick-badge-conviction">HIGH</span>
                {% endif %}
                {% if pk.has_stock_analysis %}
                <span class="top-pick-badge top-pick-badge-stage2" title="Stage 2 심층분석 완료">&#11088; 심층</span>
                {% endif %}
                {# 이상 신호만 강조 #}
                {% if pk.foreign_net_buy_signal == 'strong_buy' %}
                <span class="top-pick-badge" style="background:rgba(52,211,153,0.15);color:var(--green);border:1px solid rgba(52,211,153,0.35);" title="외국인 5일+ 연속 순매수">🏦</span>
                {% endif %}
                {% if pk.squeeze_risk == 'high' %}
                <span class="top-pick-badge" style="background:rgba(248,113,113,0.15);color:var(--red);border:1px solid rgba(248,113,113,0.35);" title="숏스퀴즈 위험">⚡</span>
                {% endif %}
            </div>
        </div>

        <div class="top-pick-title">
            <a href="/pages/proposals/history/{{ pk.ticker }}" class="top-pick-name">{{ pk.asset_name }}</a>
            <span class="top-pick-ticker">{{ pk.ticker }}{% if pk.market %} &middot; {{ pk.market }}{% endif %}</span>
            {{ external_links(pk.ticker, pk.market or '') }}
        </div>

        {% if pk.sector %}
        <div class="top-pick-sector">{{ pk.sector }}</div>
        {% endif %}

        {# Zone B: 판단 근거 — 불릿 차트 + 테마 + 근거 #}
        {{ bullet_chart(pk.current_price, pk.target_price_low, pk.upside_pct, pk.currency, pk.price_pct) }}

        <div class="top-pick-theme">
            <span class="top-pick-theme-label">테마</span>
            {% if pk.theme_key %}
            <a href="/pages/themes/history/{{ pk.theme_key }}" class="top-pick-theme-name">{{ pk.theme_name }}</a>
            {% else %}
            <span class="top-pick-theme-name">{{ pk.theme_name }}</span>
            {% endif %}
            {% if pk.theme_confidence %}
            {{ conf_ring(pk.theme_confidence, 22) }}
            {% endif %}
        </div>

        {% if pk.rationale_text %}
        <div class="top-pick-rationale top-pick-rationale-ai collapsible"
             onclick="this.classList.toggle('expanded')" title="클릭하여 펼치기/접기">
            <div class="top-pick-rationale-head">
                <span class="top-pick-rationale-tag">AI</span>
                <span class="top-pick-rationale-hint">AI 근거 보기</span>
                <span class="top-pick-rationale-hint-open">접기</span>
                <span class="top-pick-rationale-arrow">▼</span>
            </div>
            <div class="top-pick-rationale-body">{{ pk.rationale_text }}</div>
        </div>
        {% elif pk.proposal_rationale %}
        <div class="top-pick-rationale collapsible"
             onclick="this.classList.toggle('expanded')" title="클릭하여 펼치기/접기">
            <div class="top-pick-rationale-head">
                <span class="top-pick-rationale-hint">근거 보기</span>
                <span class="top-pick-rationale-hint-open">접기</span>
                <span class="top-pick-rationale-arrow">▼</span>
            </div>
            <div class="top-pick-rationale-body">{{ pk.proposal_rationale }}</div>
        </div>
        {% endif %}

        {% if pk.key_risk %}
        <div class="top-pick-risk">
            <span class="top-pick-risk-label">리스크</span> {{ pk.key_risk }}
        </div>
        {% endif %}

        {# 스코어 브레이크다운 #}
        <div class="top-pick-footer">
            <span class="top-pick-score" title="스코어 기여 내역 보기"
                  onclick="this.nextElementSibling.classList.toggle('open')">
                점수 {{ "%.0f"|format(pk.score_final|float) }}
                {% if pk.source == 'ai_rerank' and pk.score_rule != pk.score_final %}
                <span class="top-pick-score-sub">(룰 {{ "%.0f"|format(pk.score_rule|float) }})</span>
                {% endif %}
                <span class="top-pick-score-toggle">&#9662;</span>
            </span>
            <div class="top-pick-breakdown">
                {% if pk.score_breakdown %}
                {% for key, val in pk.score_breakdown.items() %}
                <div class="top-pick-breakdown-row">
                    <span class="top-pick-breakdown-key">
                        {% if key == 'conviction_high' %}고확신
                        {% elif key == 'stage2_done' %}심층분석
                        {% elif key == 'discovery_early' %}얼리/딥밸류
                        {% elif key == 'action_buy' %}매수 제안
                        {% elif key == 'upside_high' %}상승여력 상
                        {% elif key == 'upside_mid' %}상승여력 중
                        {% elif key == 'theme_confidence' %}테마 신뢰도
                        {% elif key == 'theme_streak' %}테마 연속
                        {% elif key == 'already_priced_penalty' %}급등 감점
                        {% elif key == 'no_price_penalty' %}가격 결측
                        {% else %}{{ key }}{% endif %}
                    </span>
                    <span class="top-pick-breakdown-val {% if val|float < 0 %}top-pick-breakdown-neg{% endif %}">
                        {% if val|float > 0 %}+{% endif %}{{ val }}
                    </span>
                </div>
                {% endfor %}
                {% endif %}
            </div>
        </div>

        {# Zone C: 액션 버튼 #}
        <div class="top-pick-actions">
            {% if current_user %}
                <button class="action-btn {% if pk.is_watched %}action-watched{% endif %}"
                        data-ticker="{{ pk.ticker }}"
                        data-watched="{{ '1' if pk.is_watched else '0' }}"
                        onclick="toggleWatchlist(this)"
                        title="{% if pk.is_watched %}관심 종목에서 제거{% else %}관심 종목 추가{% endif %}">
                    {% if pk.is_watched %}★ 관심중{% else %}☆ 관심{% endif %}
                </button>
            {% endif %}
            <a href="/pages/stocks/{{ pk.ticker }}{% if pk.market %}?market={{ pk.market }}{% endif %}"
               class="action-btn" title="기초정보">
                📊 정보
            </a>
            {% if pk.theme_key %}
            <a href="/pages/themes/history/{{ pk.theme_key }}" class="action-btn action-btn-signal" title="테마 히스토리">
                📈 테마
            </a>
            {% endif %}
        </div>
    </div>
    {% endfor %}
</div>
```

### Step 1.8: `partials/dashboard/_news_by_category.html` 작성

원본 라인: 545-592 (`{% if news_by_category %}` 내부 본문 — 가드는 dashboard.html에 유지)

매크로 의존: 없음

파일 내용:

```jinja
{#
  Dashboard — 수집 뉴스 (카테고리별 아코디언)
  출처: api/templates/dashboard.html (C2.2, 2026-04-21)
  CONTEXT:
    - news_by_category (dict, non-empty): 호출부 dashboard.html이 {% if %}로 가드
        각 entry: label, articles[]
        각 article: title, title_ko, link, source, published, summary, summary_ko
    - session (object): analysis_date
  매크로 의존: 없음
#}

{# 수집 뉴스 (카테고리별 아코디언) #}
<div class="section-title">수집 뉴스 ({{ session.analysis_date }})</div>

<div class="news-categories">
    {% for cat, data in news_by_category.items() %}
    <div class="news-category-card">
        <div class="news-category-header" onclick="this.parentElement.classList.toggle('open')">
            <span>
                <span class="news-category-label">{{ data.label }}</span>
                <span class="news-category-count">{{ data.articles|length }}건</span>
            </span>
            <span class="news-toggle">&#9660;</span>
        </div>
        <div class="news-category-body">
            {% for article in data.articles %}
            <div class="news-item">
                <div class="news-item-title">
                    {% if article.title_ko and article.title_ko != article.title %}
                    <div style="margin-bottom:2px;">
                        {% if article.link %}
                        <a href="{{ article.link }}" target="_blank" rel="noopener">{{ article.title_ko }}</a>
                        {% else %}
                        {{ article.title_ko }}
                        {% endif %}
                    </div>
                    <div style="font-size:12px;color:var(--text-muted);font-weight:normal;">{{ article.title }}</div>
                    {% else %}
                    {% if article.link %}
                    <a href="{{ article.link }}" target="_blank" rel="noopener">{{ article.title }}</a>
                    {% else %}
                    {{ article.title }}
                    {% endif %}
                    {% endif %}
                </div>
                <div class="news-item-meta">
                    <span class="news-source">{{ article.source }}</span>
                    {% if article.published %}
                    <span>{{ article.published }}</span>
                    {% endif %}
                </div>
                {% if article.summary_ko or article.summary %}
                <div class="news-item-summary">{{ article.summary_ko or article.summary }}</div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>
    {% endfor %}
</div>
```

### Step 1.9: 9개 템플릿 pre-load 검증

dashboard.html은 아직 변경되지 않았으므로 정상 로드, 새 7개 partial은 자체 import만으로 파싱 가능해야 함.

Run:
```bash
python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('api/templates'))
tpls = [
    'dashboard.html', 'base.html',
    'partials/dashboard/_hero_row1.html',
    'partials/dashboard/_hero_row2.html',
    'partials/dashboard/_market_summary.html',
    'partials/dashboard/_yield_curve.html',
    'partials/dashboard/_themes_list.html',
    'partials/dashboard/_top_picks.html',
    'partials/dashboard/_news_by_category.html',
]
for t in tpls:
    env.get_template(t)
    print('OK', t)
"
```

Expected: 9 lines, all `OK`. 어느 한 줄이라도 `TemplateSyntaxError` 발생 시 해당 partial을 다시 검토.

### Step 1.10: api.main import 검증

Run:
```bash
python -c "import api.main; print('OK')"
```

Expected: `OK` (FastAPI 앱 import 성공). FastAPI 앱이 Jinja2 환경을 초기화하므로 partial 파일 존재 자체로 인한 충돌이 없는지 재확인.

### Step 1.11: pytest baseline 재확인

Run:
```bash
python -m pytest tests/ --tb=no -q 2>&1 | tail -3
```

Expected: Task 0.4와 동일한 실패 개수 (예: `17 failed, 52 passed`). 신규 실패 0건.

### Step 1.12: Commit

Run:
```bash
git add api/templates/partials/dashboard/
git commit -m "$(cat <<'EOF'
feat(tpl): C2.2 — partials/dashboard/ 7개 파일 생성 (VERBATIM 이동)

- _hero_row1.html: KPI 6개 + Track Record 위젯 (+IIFE script)
- _hero_row2.html: 워치리스트 / 발굴 유형 / 주요 섹터 3-col
- _market_summary.html: Market Summary 접이식 블록
- _yield_curve.html: 한국 금리 환경 카드
- _themes_list.html: 투자 테마 + 소멸 테마
- _top_picks.html: Top Picks 그리드
- _news_by_category.html: 수집 뉴스 카테고리 아코디언

이 시점에는 dashboard.html이 변경되지 않아 새 partial은 dead file.
다음 커밋(Task 2)에서 dashboard.html을 include 기반으로 교체.

Spec: docs/superpowers/specs/2026-04-21-c2-2-dashboard-split-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit 생성, 7개 파일 추가.

---

## Task 2: `dashboard.html`을 include 기반 오케스트레이터로 교체

Task 1에서 만든 7개 partial을 `{% include %}`로 호출하고, 본문에서 매크로 사용처가 사라졌으므로 헤더의 매크로 import를 정리한다. `{% block scripts %}`의 두 JS 함수(모바일 collapse, toggleWatchlist)는 그대로 유지.

**Files:**
- Modify: `api/templates/dashboard.html` (전체 교체)

### Step 2.1: dashboard.html 새 본문 작성

기존 660줄 전체를 다음 ~80줄로 교체:

```jinja
{% extends "base.html" %}
{% from "partials/_ad_slot.html" import ad_slot with context %}
{% block title %}Dashboard — AlphaSignal{% endblock %}
{% block page_title %}Dashboard{% endblock %}

{% block content %}
{# Free 사용자에게만 렌더 (tier 기준) #}
{{ ad_slot('banner') }}

{% if session %}
{# ════════════════════════════════════════════════════
   TIER 1 — First Glance (스크롤 없이 핵심 파악)
   ════════════════════════════════════════════════════ #}
{% include "partials/dashboard/_hero_row1.html" %}
{% include "partials/dashboard/_hero_row2.html" %}

{# ════════════════════════════════════════════════════
   TIER 3 — Deep Dive (맥락 분석)
   ════════════════════════════════════════════════════ #}
{% include "partials/dashboard/_market_summary.html" %}

{% if bond_yields %}
{% include "partials/dashboard/_yield_curve.html" %}
{% endif %}

{% include "partials/dashboard/_themes_list.html" %}

{# ════════════════════════════════════════════════════
   TIER 2 — Today's Action (핵심 투자 판단)
   ════════════════════════════════════════════════════ #}
{% if top_picks %}
{% include "partials/dashboard/_top_picks.html" %}
{% endif %}

{% if news_by_category %}
{% include "partials/dashboard/_news_by_category.html" %}
{% endif %}

{% else %}
<div class="empty-state">
    <h3>분석 데이터 없음</h3>
    <p>아직 분석이 실행되지 않았습니다. <code>python -m analyzer.main</code>을 실행하세요.</p>
</div>
{% endif %}
{% endblock %}

{% block scripts %}
<script>
// 모바일: Market Summary 기본 접힘
(function() {
    if (window.innerWidth <= 768) {
        var ms = document.getElementById('market-summary-block');
        if (ms) {
            ms.classList.add('collapsed');
            var btn = ms.querySelector('.market-summary-toggle');
            if (btn) btn.textContent = '▼ 시장 요약 펼치기';
        }
    }
})();

// 관심 종목 토글 (추가/제거)
function toggleWatchlist(btn) {
    var ticker = btn.getAttribute('data-ticker');
    var watched = btn.getAttribute('data-watched') === '1';
    var url = '/api/watchlist/' + encodeURIComponent(ticker);
    var method = watched ? 'DELETE' : 'POST';

    if (btn.dataset.busy === '1') return;
    btn.dataset.busy = '1';

    fetch(url, {
        method: method,
        headers: {'X-Requested-With': 'XMLHttpRequest'}
    })
    .then(function(r) {
        if (r.ok) {
            if (watched) {
                btn.textContent = '☆ 관심';
                btn.className = 'action-btn';
                btn.setAttribute('data-watched', '0');
                btn.setAttribute('title', '관심 종목 추가');
            } else {
                btn.textContent = '★ 관심중';
                btn.className = 'action-btn action-watched';
                btn.setAttribute('data-watched', '1');
                btn.setAttribute('title', '관심 종목에서 제거');
            }
        } else if (r.status === 402) {
            if (typeof openUpgradeModal === 'function') openUpgradeModal();
        } else {
            return r.json().then(function(d) {
                if (typeof showModal === 'function') showModal(d.detail || (watched ? '제거 실패' : '추가 실패'));
            });
        }
    })
    .catch(function(e) {
        if (typeof showModal === 'function') showModal('네트워크 오류');
    })
    .finally(function() {
        delete btn.dataset.busy;
    });
}
</script>
{% endblock %}
```

**변경점 요약** (이전 → 이후):
- 매크로 import 제거: `_macros/theme.html`, `_macros/proposal.html`, `_macros/common.html` 의 4개 import 라인 → 0개 (각 partial이 자체 import)
- `partials/_ad_slot.html` import는 유지 (ad_slot은 dashboard.html에서 직접 호출)
- 본문 7개 섹션이 `{% include %}` 7개로 축소
- TIER 1·2·3 그룹핑 주석은 dashboard.html에 유지 (오케스트레이션 의도 표시)
- `{% block scripts %}` 내부 JS는 변경 없음

### Step 2.2: 라인 수 확인

Run:
```bash
wc -l api/templates/dashboard.html
```

Expected: 약 80~100줄 (정확히는 84줄 ±5).

### Step 2.3: 9개 템플릿 pre-load 재검증

Run:
```bash
python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('api/templates'))
tpls = [
    'dashboard.html', 'base.html',
    'partials/dashboard/_hero_row1.html',
    'partials/dashboard/_hero_row2.html',
    'partials/dashboard/_market_summary.html',
    'partials/dashboard/_yield_curve.html',
    'partials/dashboard/_themes_list.html',
    'partials/dashboard/_top_picks.html',
    'partials/dashboard/_news_by_category.html',
]
for t in tpls:
    env.get_template(t)
    print('OK', t)
"
```

Expected: 9 lines, all `OK`.

### Step 2.4: api.main import 검증

Run:
```bash
python -c "import api.main; print('OK')"
```

Expected: `OK`.

### Step 2.5: pytest 재확인

Run:
```bash
python -m pytest tests/ --tb=no -q 2>&1 | tail -3
```

Expected: Task 0.4와 동일 실패 개수. 신규 실패 0건.

### Step 2.6: dashboard.html 매크로 호출 grep — 0건 확인

Run:
```bash
grep -n "{{ change_indicator\|{{ risk_gauge\|{{ bullet_chart\|{{ conf_ring\|{{ external_links\|{{ yield_curve\|{{ theme_header\|{{ indicator_tags\|{{ discovery_stackbar\|{{ sector_chips" api/templates/dashboard.html
```

Expected: (출력 없음). 매크로 호출은 모두 partial로 이동했음을 확인.

### Step 2.7: 신규 partial에 매크로 호출이 있는지 grep

Run:
```bash
grep -rn "{{ change_indicator\|{{ risk_gauge\|{{ bullet_chart\|{{ conf_ring\|{{ external_links\|{{ yield_curve\|{{ theme_header\|{{ indicator_tags\|{{ discovery_stackbar\|{{ sector_chips" api/templates/partials/dashboard/
```

Expected: Task 0.2에서 본 13개 매크로 호출 라인이 partial 7개 파일로 분산되어 출력. (총 13개 호출, 단 라인 번호는 partial 파일 기준으로 다름)

### Step 2.8: include 호출 grep

Run:
```bash
grep -n "include.*partials/dashboard" api/templates/dashboard.html
```

Expected: 7 lines:
```
... include "partials/dashboard/_hero_row1.html"
... include "partials/dashboard/_hero_row2.html"
... include "partials/dashboard/_market_summary.html"
... include "partials/dashboard/_yield_curve.html"
... include "partials/dashboard/_themes_list.html"
... include "partials/dashboard/_top_picks.html"
... include "partials/dashboard/_news_by_category.html"
```

### Step 2.9: 수동 스모크 테스트 — 로컬 서버

이 Step은 자동 검증이 어려우므로, 자동 검증 후 사용자가 별도 확인할 수 있도록 안내 메시지를 출력한다.

Run (백그라운드 기동, 분석 데이터가 없으면 empty-state만 보일 수 있음):
```bash
echo "수동 스모크 가이드:"
echo "  1. python -m api.main  (별도 터미널)"
echo "  2. http://localhost:8000/pages/dashboard 접속"
echo "  3. 콘솔 에러 0건, Market Summary 접기/펼치기 동작, Track Record 탭 전환 동작 확인"
echo "  4. 로그인 후 Top Picks 카드의 ★ 관심 토글 동작 확인"
```

(실 실행은 사용자에게 위임 — Auto 모드라도 dev 서버 백그라운드 기동은 destructive 잠재성이 있어 회피.)

### Step 2.10: Commit

Run:
```bash
git add api/templates/dashboard.html
git commit -m "$(cat <<'EOF'
refactor(tpl): C2.2 — dashboard.html을 include 기반 오케스트레이터로 축소

- 660줄 → ~84줄 (87% 감소)
- 7개 섹션을 partials/dashboard/*.html로 위임
- 매크로 import 4개 제거 (각 partial이 자체 import)
- ad_slot import는 유지 (dashboard.html에서 직접 호출)
- {% block scripts %}의 JS 2개 (모바일 collapse, toggleWatchlist)는 유지
  → C3 트랙에서 외부 파일로 분리 예정

Spec: docs/superpowers/specs/2026-04-21-c2-2-dashboard-split-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit 생성, dashboard.html 1개 파일 변경.

---

## Task 3: 검증 완료 메모 작성 + 커밋

C2.1과 동일한 형식의 검증 메모를 `_docs/`에 작성하여 작업 종료 기록을 남긴다.

**Files:**
- Create: `_docs/20260421_C2_2_dashboard_split_검증완료.md`

### Step 3.1: 검증 메모 작성

파일 내용:

````markdown
# C2.2 — dashboard.html 분할 검증 완료 메모

- **일자**: 2026-04-21
- **스펙**: `docs/superpowers/specs/2026-04-21-c2-2-dashboard-split-design.md`
- **플랜**: `docs/superpowers/plans/2026-04-21-c2-2-dashboard-split.md`

## 결과 요약

| 항목 | 분할 전 | 분할 후 |
|---|---|---|
| 파일 수 | 1 (`dashboard.html` 660줄) | 8 (오케스트레이터 1 + partial 7) |
| 오케스트레이터 라인 | 660 | ~84 (87% 감소) |
| 매크로 호출 위치 | dashboard.html | 각 partial로 분산 |
| 매크로 import 라인 | 4 (dashboard.html 내부) | 7 (각 partial 내부, 의존 매크로만) |

## 파일별 담당

| 파일 | 라인 (대략) | 매크로 의존 |
|---|---:|---|
| `dashboard.html` (오케스트레이터) | ~84 | (없음 — 본문에서 매크로 직접 호출 없음, ad_slot만 유지) |
| `partials/dashboard/_hero_row1.html` | ~155 | change_indicator |
| `partials/dashboard/_hero_row2.html` | ~60 | risk_gauge, discovery_stackbar, sector_chips |
| `partials/dashboard/_market_summary.html` | ~50 | risk_gauge |
| `partials/dashboard/_yield_curve.html` | ~30 | yield_curve |
| `partials/dashboard/_themes_list.html` | ~66 | theme_header, indicator_tags |
| `partials/dashboard/_top_picks.html` | ~165 | bullet_chart, conf_ring, external_links |
| `partials/dashboard/_news_by_category.html` | ~50 | (없음) |

## 검증 통과 항목

- [x] 9개 템플릿 Jinja2 pre-load 무오류
- [x] `python -c "import api.main"` 무오류
- [x] `pytest tests/` 신규 실패 0건 (분할 전과 동일)
- [x] `dashboard.html` 매크로 직접 호출 0건 (모두 partial로 이동)
- [x] partial 매크로 호출 합계 == 분할 전 dashboard.html 매크로 호출 수
- [x] include 호출 7건이 모두 `partials/dashboard/_*.html` 경로

## 수동 스모크 (별도 확인 권장)

- [ ] `python -m api.main` 기동 후 `/pages/dashboard` 로드, 콘솔 에러 0건
- [ ] Market Summary 접기/펼치기 동작
- [ ] Track Record 탭(1M/3M/6M/1Y) 전환 동작
- [ ] (로그인) 워치리스트 토글 동작 (★ ↔ ☆)
- [ ] 로그인/비로그인 두 경우 모두 정상 렌더

## 작업 커밋 (4개 + 스펙 1개)

```
<commit3-sha> docs(refactor): C2.2 — dashboard.html 분할 검증 완료 메모
<commit2-sha> refactor(tpl): C2.2 — dashboard.html을 include 기반 오케스트레이터로 축소
<commit1-sha> feat(tpl): C2.2 — partials/dashboard/ 7개 파일 생성 (VERBATIM 이동)
4aa5397      docs(refactor): C2.2 — dashboard.html 분할 설계 문서 (7개 섹션 partial 추출)
```

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
````

(SHA 자리 `<commit*-sha>`는 실제 커밋 후 git log로 확인하여 채워넣을 것. Step 3.2에서 처리.)

### Step 3.2: 실제 SHA 채우기

Run:
```bash
git log --oneline -5 | head -5
```

위에서 얻은 commit2/commit1 SHA를 메모 파일의 placeholder에 치환:

```bash
COMMIT1=$(git log --oneline | grep "C2.2 — partials/dashboard/" | head -1 | cut -d' ' -f1)
COMMIT2=$(git log --oneline | grep "C2.2 — dashboard.html을 include" | head -1 | cut -d' ' -f1)
sed -i "s/<commit1-sha>/$COMMIT1/g; s/<commit2-sha>/$COMMIT2/g" _docs/20260421_C2_2_dashboard_split_검증완료.md
```

(commit3-sha는 다음 Step에서 커밋 후 값이 정해지므로 placeholder 유지 → 사용자가 직접 또는 후속 PR에서 채움. 또는 메모에서 commit3 라인을 제거하는 방안도 가능.)

대안 (간단): `<commit3-sha>` 라인을 제거하고 commit2/commit1만 기록. 메모 자체가 commit3에 포함되므로 자기 참조는 불필요.

```bash
sed -i '/<commit3-sha>/d' _docs/20260421_C2_2_dashboard_split_검증완료.md
```

### Step 3.3: 검증 메모 라인 수 확인

Run:
```bash
wc -l _docs/20260421_C2_2_dashboard_split_검증완료.md
```

Expected: 약 60~80줄.

### Step 3.4: Commit

Run:
```bash
git add _docs/20260421_C2_2_dashboard_split_검증완료.md
git commit -m "$(cat <<'EOF'
docs(refactor): C2.2 검증 완료 메모 — dashboard.html 분할

- 660줄 → 8개 파일 (오케스트레이터 84줄 + partial 7개)
- 9개 템플릿 pre-load, api.main import, pytest 신규 실패 0건 통과
- 매크로 호출 13건이 partial 7개 파일로 정확히 분산
- 후속 관심사: C2.3 (admin.html), C3 (인라인 JS 외부화)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 3.5: 최종 확인

Run:
```bash
git log --oneline -5
ls api/templates/partials/dashboard/
wc -l api/templates/dashboard.html api/templates/partials/dashboard/*.html
```

Expected:
- 최근 5개 커밋: spec → partial 생성 → orchestrator 교체 → 검증 메모 + 그 위 1개
- `partials/dashboard/`: 7개 파일
- `dashboard.html`: ~84줄, 7개 partial 합계: ~576줄

---

## 부록: 트러블슈팅

### Jinja2 `TemplateSyntaxError`

증상: pre-load 검증에서 partial 파싱 실패.

원인: 매크로 import 누락, `{% block %}` 사용 시도(partial은 block 정의 불가), 짝이 안 맞는 `{% if/endif %}`.

해결: 해당 partial을 원본 dashboard.html과 라인 단위 diff. import 라인이 빠지지 않았는지 확인.

### `UndefinedError: 'X' is undefined`

증상: 페이지 렌더 시 변수가 None/undefined.

원인: `{% include %}`는 부모 컨텍스트를 상속하지만, 부모가 그 변수를 정의하지 않은 경우 UndefinedError.

해결: 해당 partial이 의존하는 변수가 `routes/pages.py`의 dashboard 핸들러에서 모두 주입되는지 확인. 누락 시 핸들러를 수정하지 말고(Non-goal), 원본 dashboard.html이 어떻게 그 변수를 참조했는지 다시 확인 (예: `{% if X %}` 가드가 있었는데 partial에 빠뜨렸을 가능성).

### 렌더 결과 공백 차이

증상: `{% include %}` 사이에 빈 줄이 추가되어 시각적 미세 변화.

원인: Jinja2 include는 기본적으로 줄바꿈을 보존.

해결: 대부분 영향 없음 (`<div>` 단위 섹션). 필요 시 `{%- include "..." -%}`로 트리밍.

### 매크로 호출이 partial에서 작동하지 않음

증상: `'X' is not defined` (매크로 이름).

원인: partial 내부 `{% from %}` 누락.

해결: partial 상단 import 블록을 다시 확인. dashboard.html에 상속받는 매크로는 include 컨텍스트로 자동 전파되지 않음 (Jinja2 사양 — import는 namespace, 부모 매크로는 명시적 `with context` 없이는 partial에 보이지 않음).

---

## Self-Review

- [x] 스펙 5개 결정(메커니즘/디렉터리/역할분리/import/JS유지) 모두 반영
- [x] 7개 partial 모두 본문 코드 포함 (placeholder 없음)
- [x] dashboard.html 새 본문 전체 코드 포함
- [x] 검증 5종(pre-load/import/pytest/grep/manual) 모두 단계화
- [x] 커밋 메시지 3개 모두 명시 (스펙 커밋 1개는 이미 완료됨)
- [x] 라인 번호와 매크로 호출 수 일관성 (Task 0.2 grep 13개 → Task 2.7에서 동일 13개 확인)
- [x] 트러블슈팅 4종 사전 기록
