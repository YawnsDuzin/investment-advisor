# 관리자 웹 UI에서 systemd 서비스 제어 — 설계 명세

작성일: 2026-04-25 10:39 KST
작성자: 브레인스토밍 합의(Claude + 사용자)
상태: Draft → 사용자 리뷰 대기

---

## 1. 목적

라즈베리파이에서 `deploy/systemd/README.md` 로 운영되는 7개 unit을
SSH 없이 웹 관리자 페이지(`/admin`) 에서 수동 제어한다.
동시에 기존 `admin.html` 의 5개 자산(분석 실행 / 뉴스 번역 / 진단 / 데이터 삭제 / 원격 DB 복사)을
일관된 UX로 통합하여 운영자 혼란을 제거한다.

## 2. 범위

### 포함

- `/admin` 페이지를 탭 3개(운영 / 도구 / 위험구역) 구조로 재편
- systemd unit 7종에 대한 상태 조회·시작·중지·재시작·enable·disable·로그 스트리밍 API + UI
- API 서비스(`investment-advisor-api.service`)의 self-protected 정책
- 기존 "분석 파이프라인 실행" 버튼을 환경 자동 감지(γ) 로 단일화
- SSE 로그 뷰어 공통 컴포넌트 추출
- sudoers NOPASSWD 화이트리스트 문서화
- 단위 테스트 (`tests/test_admin_systemd.py`)

### 제외

- 신규 systemd unit 추가
- 기존 진단(`/admin/diagnostics`) / 데이터 삭제 / 원격 DB 복사 기능의 동작 변경 (위치만 재배치)
- API 자체의 재시작/중지 (self-protected — SSH 필수)
- 외부 모니터링 시스템 연동 (Prometheus 등)

## 3. 관리 대상 unit

| key | category | service | timer | self_protected | schedule |
|---|---|---|---|---|---|
| `api` | A | investment-advisor-api.service | — | ✓ | 상시 (Restart=always) |
| `analyzer` | A | investment-advisor-analyzer.service | investment-advisor-analyzer.timer | | 매일 06:30 KST |
| `sync-price` | B | universe-sync-price.service | universe-sync-price.timer | | 매일 06:30 KST |
| `sync-meta` | B | universe-sync-meta.service | universe-sync-meta.timer | | 매주 일 07:30 KST |
| `ohlcv-cleanup` | B | ohlcv-cleanup.service | ohlcv-cleanup.timer | | 매주 일 08:00 KST |
| `sector-refresh` | C | monthly-sector-refresh.service | monthly-sector-refresh.timer | | 매월 1일 07:45 KST |
| `briefing` | D | pre-market-briefing.service | pre-market-briefing.timer | | 매일 06:30 KST |

## 4. 아키텍처

### 4.1 모듈 구성

```
api/routes/admin_systemd.py    [신설]  /admin/systemd/* 라우터
api/routes/admin.py            [수정]  run_analysis() 환경 분기 추가
api/main.py                    [수정]  admin_systemd 라우터 등록
api/templates/admin.html       [재구성] 탭 3개 + 매크로 호출
api/templates/_macros.html     [확장]  sse_log_panel / unit_card / tool_card 매크로
api/static/js/sse_log_viewer.js [신설]  공용 SSE 컨트롤러
api/static/css/admin.css       [신설]  admin-status-*, admin-tab-* 클래스
deploy/systemd/README.md       [확장]  "웹 UI에서 관리하기" 섹션 + sudoers 정책
tests/test_admin_systemd.py    [신설]  단위 테스트
```

### 4.2 권한·보안 게이트

```
요청 → require_role("admin")           # JWT 인증 + Admin 역할
     → _systemd_available()            # platform.system()=="Linux" + shutil.which("systemctl")
                                       # 미지원 시 503 {"error":"systemd_unavailable"}
     → key 화이트리스트 검증            # MANAGED_UNITS 에 없으면 400 + audit log
     → self_protected 검증 (mutation만) # api 키에 mutation 시 403
     → subprocess.run([...], shell=False, timeout=10)
        ├─ 읽기(show/is-active/journalctl): sudo 불필요
        └─ 쓰기(start/stop/restart/enable/disable): sudo NOPASSWD
     → admin_audit_logs INSERT
     → JSON 응답
```

## 5. 백엔드 API 명세 — `api/routes/admin_systemd.py`

### 5.1 Unit 레지스트리 (단일 진실)

```python
MANAGED_UNITS: list[dict] = [
    {
        "key": "api",
        "category": "A",
        "label": "FastAPI 웹서버",
        "service": "investment-advisor-api.service",
        "timer": None,
        "self_protected": True,
        "schedule": "상시 (Restart=always)",
        "description": "웹 UI 호스팅 서비스",
    },
    {
        "key": "analyzer", "category": "A",
        "label": "일일 분석 배치",
        "service": "investment-advisor-analyzer.service",
        "timer": "investment-advisor-analyzer.timer",
        "self_protected": False,
        "schedule": "매일 06:30 KST",
        "description": "RSS → Claude Stage 1~4 → DB",
    },
    # sync-price / sync-meta / ohlcv-cleanup / sector-refresh / briefing
    # ... (총 7개)
]
```

### 5.2 엔드포인트 목록

| Method | Path | 설명 | 응답 |
|---|---|---|---|
| `GET` | `/admin/systemd/units` | 7개 unit 일괄 상태 | `{units: [{key, label, category, schedule, active, enabled, sub_state, last_trigger, next_trigger, self_protected, description}, ...], systemd_available: bool, platform: str}` |
| `GET` | `/admin/systemd/units/{key}` | 단일 unit 상세 (show 전체 + journal 100줄) | `{unit: {...}, journal: ["...", ...]}` |
| `POST` | `/admin/systemd/units/{key}/start` | service 시작 | `{ok: true, before, after}` |
| `POST` | `/admin/systemd/units/{key}/stop` | service 중지 | 동상 |
| `POST` | `/admin/systemd/units/{key}/restart` | service 재시작 | 동상 |
| `POST` | `/admin/systemd/units/{key}/enable` | timer enable --now | 동상 |
| `POST` | `/admin/systemd/units/{key}/disable` | timer disable --now | 동상 |
| `GET` | `/admin/systemd/units/{key}/logs/stream` | journalctl SSE | `text/event-stream` |

### 5.3 핵심 헬퍼

```python
def _systemd_available() -> tuple[bool, str]:
    """(가능 여부, 플랫폼명)"""
    if platform.system() != "Linux":
        return False, platform.system()
    if not shutil.which("systemctl"):
        return False, "Linux (systemctl not found)"
    return True, "Linux"

def _find_unit(key: str) -> dict | None:
    return next((u for u in MANAGED_UNITS if u["key"] == key), None)

def _systemctl_show(unit_name: str) -> dict:
    """systemctl show <unit> --property=... 파싱"""
    props = ["ActiveState", "SubState", "UnitFileState", "LoadState",
             "NextElapseUSecRealtime", "LastTriggerUSec"]
    result = subprocess.run(
        ["systemctl", "show", unit_name, "--property=" + ",".join(props)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    parsed = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            parsed[k] = v
    return parsed

def _systemctl_action(verb: str, unit_name: str) -> tuple[bool, str]:
    """(성공 여부, stderr)"""
    cmd = ["sudo", "-n", "systemctl", verb, unit_name]
    if verb in ("enable", "disable"):
        cmd.insert(3, "--now")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    return result.returncode == 0, result.stderr.strip()

def _audit(actor, action: str, key: str, before: dict | None, after: dict | None, reason: str | None = None):
    """admin_audit_logs INSERT"""
    cfg = _get_cfg()
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO admin_audit_logs
                (actor_id, actor_email, target_user_id, target_email,
                 action, before_state, after_state, reason)
                VALUES (%s, %s, NULL, NULL, %s, %s::jsonb, %s::jsonb, %s)
            """, (actor.id, actor.email, action,
                  json.dumps(before) if before else None,
                  json.dumps(after) if after else None,
                  reason))
        conn.commit()
    finally:
        conn.close()
```

### 5.4 SSE 로그 스트리밍

```python
@router.get("/units/{key}/logs/stream")
def stream_logs(key: str, _admin = Depends(require_role("admin"))):
    avail, _ = _systemd_available()
    if not avail:
        raise HTTPException(503, "systemd_unavailable")
    unit = _find_unit(key)
    if not unit:
        raise HTTPException(400, "invalid unit key")

    def generate():
        proc = subprocess.Popen(
            ["journalctl", "-u", unit["service"], "-n", "100", "-f", "--no-pager", "-o", "short-iso"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, text=True,
        )
        try:
            for line in iter(proc.stdout.readline, ""):
                yield f"data: {line.rstrip()}\n\n"
        finally:
            proc.terminate()
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired: proc.kill()

    return StreamingResponse(generate(), media_type="text/event-stream")
```

복수 admin 동시 접속 시 각자 별도 `journalctl` 프로세스 보유 → 격리.

### 5.5 분석 실행 환경 자동 감지 (γ 정책)

`api/routes/admin.py:run_analysis()` 수정:

```python
def run_analysis(_admin = Depends(require_role("admin"))):
    avail, _ = _systemd_available()
    if avail and Path("/etc/systemd/system/investment-advisor-analyzer.service").exists():
        # systemctl start로 위임 + journalctl SSE
        return _stream_via_systemd("investment-advisor-analyzer.service")
    else:
        # 기존 in-process subprocess 경로 (Windows 개발 등)
        return _stream_via_inprocess()
```

운영 탭의 "analyzer" 카드 Start 버튼과 도구 탭의 "분석 실행" 버튼 모두 같은 결과.

## 6. 프론트엔드 — `admin.html` 재구성

### 6.0 페이지 렌더링 흐름

`api/routes/admin.py:admin_page()` 에서 `_collect_units_status()` 호출 후 `units` dict (key→상태)를 템플릿에 전달.
초기 렌더는 SSR (서버가 현재 상태로 카드를 그림). 이후 액션 버튼 클릭마다 `GET /admin/systemd/units` 재요청 →
JS가 카드 상태 부분만 patch (`status badge` + `enable/disable 라벨` + `next_trigger` 텍스트).

```python
# admin.py
def admin_page(...):
    avail, plat = _systemd_available()
    units_dict = _collect_units_status() if avail else {}  # admin_systemd.py에서 import
    ctx["units"] = units_dict
    ctx["systemd_available"] = avail
    ctx["platform"] = plat
    return templates.TemplateResponse(...)
```

### 6.1 탭 구조

```html
<div class="admin-tab-bar">
  <div class="admin-tab" data-tab="operations">운영</div>
  <div class="admin-tab" data-tab="tools">도구</div>
  <div class="admin-tab" data-tab="danger">위험구역</div>
</div>

<div class="admin-tab-pane" id="tab-operations">
  <h3>A. 필수 서비스</h3>
  {{ unit_card(units.api, can_mutate=False) }}
  {{ unit_card(units.analyzer, can_mutate=True) }}
  <h3>B. Universe / OHLCV 자동화</h3>
  {{ unit_card(units["sync-price"]) }} ...
  <h3>C. 섹터 분류</h3> ...
  <h3>D. 프리마켓 브리핑</h3> ...
</div>

<div class="admin-tab-pane" id="tab-tools">
  {{ tool_card(label="분석 실행", ...) }}      <!-- 기존 분석 실행 카드 -->
  {{ tool_card(label="뉴스 한글 번역", ...) }} <!-- 기존 번역 카드 -->
  {{ tool_card(label="진단 페이지", ...) }}    <!-- 진단 링크 -->
  {{ tool_card(label="원격 DB 복사", ...) }}   <!-- 원격 DB 폼 -->
</div>

<div class="admin-tab-pane" id="tab-danger">
  {{ tool_card(label="전체 데이터 삭제", danger=True, ...) }}
</div>
```

### 6.2 탭 동기화 JS (`admin.html` 인라인)

```javascript
function activateTab(tabId) {
  document.querySelectorAll('.admin-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabId));
  document.querySelectorAll('.admin-tab-pane').forEach(p => p.classList.toggle('active', p.id === `tab-${tabId}`));
  history.replaceState(null, '', `#${tabId}`);
}
window.addEventListener('hashchange', () => activateTab(location.hash.slice(1) || 'operations'));
document.addEventListener('DOMContentLoaded', () => {
  activateTab(location.hash.slice(1) || 'operations');
  document.querySelectorAll('.admin-tab').forEach(t =>
    t.addEventListener('click', () => activateTab(t.dataset.tab)));
});
```

### 6.3 unit_card 매크로 (`_macros.html`)

```jinja2
{% macro unit_card(unit, can_mutate=True) %}
<div class="card admin-unit-card {% if unit.self_protected %}admin-card-protected{% endif %}"
     data-unit-key="{{ unit.key }}" style="padding:16px 20px;margin-bottom:12px;">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
    <div>
      <div style="font-size:15px;font-weight:600;">{{ unit.label }}</div>
      <div style="font-size:12px;color:var(--text-muted);">{{ unit.description }}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">
        <code>{{ unit.service }}</code>
        {% if unit.schedule %}· {{ unit.schedule }}{% endif %}
      </div>
    </div>
    <div style="display:flex;gap:6px;align-items:center;">
      <span class="admin-status admin-status-{{ unit.active or 'inactive' }}">{{ unit.active or 'inactive' }}</span>
      {% if unit.self_protected %}
        <span style="font-size:11px;color:var(--text-muted);">자기 자신 제어 불가 — SSH 사용</span>
      {% else %}
        <button class="btn" onclick="systemdAction('{{ unit.key }}', 'start')">Start</button>
        <button class="btn" onclick="systemdAction('{{ unit.key }}', 'stop')">Stop</button>
        <button class="btn" onclick="systemdAction('{{ unit.key }}', 'restart')">Restart</button>
        {% if unit.timer %}
          <button class="btn" onclick="systemdAction('{{ unit.key }}',
                  '{{ 'disable' if unit.enabled else 'enable' }}')">
            {{ 'Disable' if unit.enabled else 'Enable' }}
          </button>
        {% endif %}
      {% endif %}
      <button class="btn" onclick="openLogModal('{{ unit.key }}')">로그</button>
    </div>
  </div>
</div>
{% endmacro %}
```

### 6.4 sse_log_panel 매크로 (`_macros.html`)

```jinja2
{% macro sse_log_panel(panel_id, height='360px') %}
<pre id="{{ panel_id }}" class="sse-log-panel"
     style="height:{{ height }};overflow:auto;background:#0a0a0a;color:#cfcfcf;
            padding:12px;font-size:12px;line-height:1.5;border-radius:6px;
            font-family:'Consolas',monospace;"></pre>
{% endmacro %}
```

### 6.5 공용 SSE 뷰어 (`api/static/js/sse_log_viewer.js`)

```javascript
const _sseConnections = new Map();

export function attachSseLog(panelId, url, opts = {}) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  detachSseLog(panelId);
  const maxLines = opts.maxLines || 1000;
  const es = new EventSource(url);
  es.onmessage = (e) => {
    panel.appendChild(document.createTextNode(e.data + '\n'));
    while (panel.childNodes.length > maxLines) panel.removeChild(panel.firstChild);
    panel.scrollTop = panel.scrollHeight;
  };
  es.addEventListener('done', () => detachSseLog(panelId));
  es.onerror = () => { /* 자동 재시도는 EventSource 기본 동작 */ };
  _sseConnections.set(panelId, es);
}

export function detachSseLog(panelId) {
  const es = _sseConnections.get(panelId);
  if (es) { es.close(); _sseConnections.delete(panelId); }
}
window.attachSseLog = attachSseLog;
window.detachSseLog = detachSseLog;
```

`base.html`에 `<script type="module" src="/static/js/sse_log_viewer.js"></script>` 추가.

### 6.6 admin-status / admin-tab CSS (`api/static/css/admin.css` 신설)

§4의 CSS 블록 그대로. `admin.html` 에 `<link rel="stylesheet" href="/static/css/admin.css">` 추가.

## 7. 감사 로그

모든 mutation 엔드포인트 (성공·실패 모두) `admin_audit_logs` INSERT.

| action | 트리거 |
|---|---|
| `systemd_start` | POST .../start 성공 |
| `systemd_stop` | POST .../stop 성공 |
| `systemd_restart` | POST .../restart 성공 |
| `systemd_enable` | POST .../enable 성공 |
| `systemd_disable` | POST .../disable 성공 |
| `systemd_action_failed` | 위 어느 것의 실패. `reason` = stderr |
| `systemd_invalid_target` | 화이트리스트 위반 시도. `reason` = raw key |
| `systemd_self_protected_violation` | api 키에 mutation 시도 |

`before_state`/`after_state` JSONB: `{"key": "analyzer", "active": "inactive"}` 형태.

## 8. sudoers 정책 — `deploy/systemd/README.md` 추가 섹션

```
# /etc/sudoers.d/investment-advisor-systemd
# 권한: chmod 0440, visudo -c 로 검증 필수

Cmnd_Alias INV_SVC_ACTIONS = \
  /bin/systemctl start   investment-advisor-analyzer.service, \
  /bin/systemctl stop    investment-advisor-analyzer.service, \
  /bin/systemctl restart investment-advisor-analyzer.service, \
  /bin/systemctl start   universe-sync-price.service, \
  /bin/systemctl stop    universe-sync-price.service, \
  /bin/systemctl restart universe-sync-price.service, \
  /bin/systemctl start   universe-sync-meta.service, \
  /bin/systemctl stop    universe-sync-meta.service, \
  /bin/systemctl restart universe-sync-meta.service, \
  /bin/systemctl start   ohlcv-cleanup.service, \
  /bin/systemctl stop    ohlcv-cleanup.service, \
  /bin/systemctl restart ohlcv-cleanup.service, \
  /bin/systemctl start   monthly-sector-refresh.service, \
  /bin/systemctl stop    monthly-sector-refresh.service, \
  /bin/systemctl restart monthly-sector-refresh.service, \
  /bin/systemctl start   pre-market-briefing.service, \
  /bin/systemctl stop    pre-market-briefing.service, \
  /bin/systemctl restart pre-market-briefing.service

Cmnd_Alias INV_TIMER_ACTIONS = \
  /bin/systemctl enable  --now investment-advisor-analyzer.timer, \
  /bin/systemctl disable --now investment-advisor-analyzer.timer, \
  /bin/systemctl enable  --now universe-sync-price.timer, \
  /bin/systemctl disable --now universe-sync-price.timer, \
  /bin/systemctl enable  --now universe-sync-meta.timer, \
  /bin/systemctl disable --now universe-sync-meta.timer, \
  /bin/systemctl enable  --now ohlcv-cleanup.timer, \
  /bin/systemctl disable --now ohlcv-cleanup.timer, \
  /bin/systemctl enable  --now monthly-sector-refresh.timer, \
  /bin/systemctl disable --now monthly-sector-refresh.timer, \
  /bin/systemctl enable  --now pre-market-briefing.timer, \
  /bin/systemctl disable --now pre-market-briefing.timer

dzp ALL=(root) NOPASSWD: INV_SVC_ACTIONS, INV_TIMER_ACTIONS
```

**주의사항:**
- `__SYSTEM_USER__` (예: `dzp`) 자리에 실제 운영 유저 치환
- 화이트리스트 외 systemctl 명령 절대 추가 금지 — 권한 확장 위험
- API 자체 service(`investment-advisor-api`) 는 sudoers 미포함 (self-protected)
- journalctl 권한: `sudo usermod -aG adm,systemd-journal $SYSTEM_USER` 후 재로그인

## 9. 테스트 — `tests/test_admin_systemd.py`

`tests/conftest.py` 의 mock 패턴 따름. 추가로 `subprocess.run` / `Popen` / `platform.system` / `shutil.which` mock.

| 케이스 | 검증 |
|---|---|
| Linux + 화이트리스트 key + GET /units | 200, 7개 unit 반환 |
| Linux + invalid key + POST /start | 400 + audit log `systemd_invalid_target` |
| Linux + `api` key + POST /stop | 403 + audit log `systemd_self_protected_violation` |
| Windows | 503 `systemd_unavailable` |
| Linux + 비-admin | 403 (require_role) |
| 정상 mutation | audit log `systemd_<verb>` 1건, before/after 정확 |
| subprocess timeout | 500 + audit log `systemd_action_failed` |
| sudo 거부(stderr "a password is required") | 500 + audit log `systemd_action_failed` reason 포함 |

## 10. 마이그레이션 / 호환성

- DB 스키마 변경 없음 (admin_audit_logs v17 재활용)
- 기존 분석 실행 / 번역 SSE 동작 동일 (인라인 코드를 매크로 호출로 교체하되 EventSource URL 동일)
- Windows 개발 환경: systemd 패널 503 + 도구 탭만 정상 동작
- Linux 운영 환경: sudoers 미설정 시 mutation 호출 → 500 + 명확한 에러 메시지

## 11. 보안 고려사항

1. **권한 확장 차단**: subprocess 인자는 항상 list, `shell=False`. 사용자 입력은 `key` 문자열 하나뿐이며 `MANAGED_UNITS` 멤버십으로만 검증.
2. **CSRF**: 기존 admin 페이지와 동일하게 SameSite=lax 쿠키 + JWT 인증으로 차단.
3. **감사 추적**: 모든 시도(성공/실패/위반) 영구 기록.
4. **API self-protection**: 백엔드 + 프론트 이중 방어.
5. **로그 노출**: journalctl 출력에 secrets 포함 가능성 — 라즈베리파이 단일 사용자 환경 가정. 프로덕션 멀티 사용자라면 별도 마스킹 검토.

## 12. 향후 확장

- unit별 `Run history` 테이블 — 최근 N회 실행 결과 + duration + exit code (현재는 journalctl 의존)
- 커스텀 트리거 인자 (예: analyzer 의 `--from-checkpoint`)
- Slack/Discord 알림 (시작·실패 시)

위 셋은 본 명세에 포함하지 않음 — 별도 후속 spec.

---

## 변경 이력

- 2026-04-25 v0.1: 초안 작성
