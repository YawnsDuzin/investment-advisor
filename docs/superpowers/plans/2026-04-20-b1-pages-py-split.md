# B1: `pages.py` 도메인 콜로케이션 분할 — 구현 계획서

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `api/routes/pages.py` (1,673줄, 27개 페이지 라우트)를 도메인별 파일에 콜로케이션 흡수하고, `pages.py`를 삭제한다. 동작·URL·DB는 그대로 유지하며, baseline diff로 회귀 0을 보장한다.

**Architecture:** 도메인별 파일에 `router`(JSON, 기존 prefix) + `pages_router`(HTML, prefix=`/pages/<domain>`) 이중 라우터 패턴을 표준화한다. 공용 유틸 3개(템플릿 필터·page_context·serialization)는 신규 모듈로 추출한다. baseline 스크립트가 모든 페이지 URL의 `(status, content_length, sha256)`을 캡처해 단계별 diff = 0을 검증한다.

**Tech Stack:** Python 3.10+, FastAPI, Jinja2, psycopg2 (RealDictCursor), httpx (baseline), 수동 스모크 테스트.

**Spec:** [docs/superpowers/specs/2026-04-20-b1-pages-py-split-design.md](../specs/2026-04-20-b1-pages-py-split-design.md)

---

## 사전 준비 (모든 작업 전)

- API 서버를 한시적으로 `AUTH_ENABLED=false` 환경에서 기동할 수 있어야 한다 (baseline 캡처용).
- baseline 캡처는 작업 디렉토리에서 서버 부팅 → 스크립트 실행 → 서버 종료 순으로 진행한다.
- 모든 작업은 같은 git 브랜치(현재 `dev`)에서 진행한다. 각 Task 마지막에 commit.

---

## Task 1: baseline 스크립트 작성 + 초기 baseline 캡처

**Files:**
- Create: `scripts/route_baseline.py`
- Create: `_baselines/` (디렉토리)

- [ ] **Step 1: baseline 스크립트 작성**

`scripts/route_baseline.py` 신규 작성:

```python
"""페이지 라우트 회귀 테스트용 baseline 캡처/diff 도구.

사용법:
  # 캡처 (서버는 별도 터미널에서 AUTH_ENABLED=false uvicorn ...로 기동)
  python scripts/route_baseline.py capture --label before
  python scripts/route_baseline.py capture --label after

  # 비교
  python scripts/route_baseline.py diff before after
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

BASE_URL = "http://localhost:8000"
OUT_DIR = Path("_baselines")

# pages.py에서 추출한 모든 GET 페이지 URL.
# 동적 path는 실데이터 의존 없이 검증 가능한 값을 사용 (없으면 404 OK).
ROUTES = [
    "/",
    "/pages/sessions",
    "/pages/sessions/date/2026-04-20",       # 없으면 404 — 그것도 baseline에 포함
    "/pages/sessions/1",
    "/pages/stocks/AAPL",
    "/pages/themes",
    "/pages/themes/history/test_key",
    "/pages/proposals",
    "/pages/proposals/history/AAPL",
    "/proposals/1/stock-analysis",
    "/pages/watchlist",
    "/pages/notifications",
    "/pages/profile",
    "/pages/chat",
    "/pages/chat/new/1",
    "/pages/chat/1",
    "/pages/education",
    "/pages/education/topic/intro",
    "/pages/education/chat",
    "/pages/education/chat/new/1",
    "/pages/education/chat/1",
    "/pages/track-record",
    "/pages/landing",
    "/pages/pricing",
    "/pages/inquiry",
    "/pages/inquiry/new",
    "/pages/inquiry/1",
]


def capture(label: str) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    results = []
    with httpx.Client(base_url=BASE_URL, timeout=30.0, follow_redirects=False) as client:
        for url in ROUTES:
            try:
                r = client.get(url)
                body = r.content
                results.append({
                    "url": url,
                    "status": r.status_code,
                    "length": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "location": r.headers.get("location"),  # 리다이렉트 추적
                })
            except Exception as e:
                results.append({"url": url, "error": str(e)})

    out = OUT_DIR / f"route_baseline_{label}.json"
    out.write_text(
        json.dumps({"label": label, "captured_at": datetime.now().isoformat(), "routes": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[OK] {len(results)}개 라우트 캡처 → {out}")


def diff(label_a: str, label_b: str) -> int:
    a = json.loads((OUT_DIR / f"route_baseline_{label_a}.json").read_text(encoding="utf-8"))
    b = json.loads((OUT_DIR / f"route_baseline_{label_b}.json").read_text(encoding="utf-8"))
    by_url_a = {r["url"]: r for r in a["routes"]}
    by_url_b = {r["url"]: r for r in b["routes"]}

    diffs = []
    for url in sorted(set(by_url_a) | set(by_url_b)):
        ra, rb = by_url_a.get(url), by_url_b.get(url)
        if ra != rb:
            diffs.append((url, ra, rb))

    if not diffs:
        print(f"[OK] diff 없음 ({label_a} vs {label_b})")
        return 0

    print(f"[FAIL] {len(diffs)}개 라우트에서 변화 감지:")
    for url, ra, rb in diffs:
        print(f"\n  {url}")
        print(f"    BEFORE: {ra}")
        print(f"    AFTER : {rb}")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--label", required=True)
    df = sub.add_parser("diff")
    df.add_argument("a")
    df.add_argument("b")
    args = parser.parse_args()

    if args.cmd == "capture":
        capture(args.label)
    elif args.cmd == "diff":
        sys.exit(diff(args.a, args.b))
```

- [ ] **Step 2: 서버 기동 (baseline 캡처용)**

별도 터미널에서:
```bash
AUTH_ENABLED=false uvicorn api.main:app --host 0.0.0.0 --port 8000
```
서버 부팅 로그에 `Application startup complete.` 출력 확인.

- [ ] **Step 3: 초기 baseline 캡처**

```bash
python scripts/route_baseline.py capture --label 00-before
```
Expected 출력: `[OK] 27개 라우트 캡처 → _baselines/route_baseline_00-before.json`
파일이 생성되었는지 확인:
```bash
ls _baselines/
```

- [ ] **Step 4: 서버 종료**

uvicorn 터미널에서 Ctrl+C.

- [ ] **Step 5: `_baselines/`를 `.gitignore`에 추가**

`.gitignore` 마지막 줄에:
```
_baselines/
```

- [ ] **Step 6: 커밋**

```bash
git add scripts/route_baseline.py .gitignore
git commit -m "chore(refactor): B1 — 페이지 라우트 baseline 캡처 도구 신설"
```

---

## Task 2: 공용 유틸 3개 신설 — `template_filters.py`

**Files:**
- Create: `api/template_filters.py`

- [ ] **Step 1: 파일 작성**

`api/template_filters.py` 신규 작성. `pages.py:34-131`의 3개 함수(`_nl_numbered`, `_fmt_price`, `_markdown_to_html`)와 관련 상수를 그대로 이전:

```python
"""Jinja2 커스텀 필터 — pages.py에서 추출 (B1).

main.py에서 `register(env)`를 호출해 모든 템플릿 환경에 등록한다.
"""
import re
import markdown as _markdown
import bleach
from markupsafe import Markup
from jinja2 import Environment


def nl_numbered(text: str) -> Markup:
    """①②③ 또는 1. 2. 3. 형태의 번호 리스트를 줄바꿈으로 분리."""
    if not text:
        return Markup("")
    parts = re.split(r'\s*(?=[①-⑳])', text)
    if len(parts) > 1:
        stripped = [p.strip() for p in parts if p.strip()]
        return Markup('<br>'.join(stripped))
    return Markup(re.sub(r'(?<=\S)\s+(\d+)\.\s', r'<br>\1. ', text))


_CURRENCY_SYMBOLS = {"KRW": "₩", "USD": "$", "EUR": "€", "JPY": "¥", "GBP": "£", "CNY": "¥"}


def fmt_price(value, currency: str = "") -> str:
    """가격을 통화 기호 + 천 단위 쉼표로 포맷팅 (정수 통화는 소수점 제거)."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)
    if num == 0:
        return "-"
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper(), "")
    if (currency or "").upper() in ("KRW", "JPY"):
        return f"{symbol}{num:,.0f}"
    return f"{symbol}{num:,.2f}"


_MD_ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "em", "b", "i", "u", "s", "del", "mark", "sub", "sup",
    "blockquote", "code", "pre",
    "ul", "ol", "li",
    "a",
    "table", "thead", "tbody", "tr", "th", "td",
    "img",
    "span", "div",
]
_MD_ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title"],
    "th": ["align", "colspan", "rowspan"],
    "td": ["align", "colspan", "rowspan"],
    "code": ["class"],
    "pre": ["class"],
    "span": ["class"],
    "div": ["class"],
}
_MD_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def markdown_to_html(text) -> Markup:
    """AI가 생성한 마크다운 원문을 sanitize된 HTML로 렌더링."""
    if not text:
        return Markup("")
    html = _markdown.markdown(
        str(text),
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
        output_format="html",
    )
    cleaned = bleach.clean(
        html,
        tags=_MD_ALLOWED_TAGS,
        attributes=_MD_ALLOWED_ATTRS,
        protocols=_MD_ALLOWED_PROTOCOLS,
        strip=True,
    )
    cleaned = re.sub(
        r'<a\s+([^>]*?)>',
        lambda m: f'<a {m.group(1)} target="_blank" rel="noopener noreferrer">',
        cleaned,
    )
    return Markup(cleaned)


def register(env: Environment) -> None:
    """Jinja2 환경에 모든 커스텀 필터를 등록."""
    env.filters["nl_numbered"] = nl_numbered
    env.filters["fmt_price"] = fmt_price
    env.filters["markdown_to_html"] = markdown_to_html
```

- [ ] **Step 2: import 검증**

```bash
python -c "from api.template_filters import nl_numbered, fmt_price, markdown_to_html, register; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
git add api/template_filters.py
git commit -m "feat(refactor): B1 — Jinja2 필터 모듈(api/template_filters.py) 신설"
```

---

## Task 3: 공용 유틸 — `serialization.py`

**Files:**
- Create: `api/serialization.py`

- [ ] **Step 1: 파일 작성**

`pages.py`에서 import 중인 `_serialize_row`(현재 `sessions.py:132-144`에 정의)를 신규 모듈로 이전:

```python
"""DB row 직렬화 헬퍼 — sessions.py에서 추출 (B1).

RealDictRow의 date/datetime/Decimal 타입을 JSON 직렬화 가능 형태로 변환한다.
sessions/chat/education/inquiry/pages 라우트에서 공통으로 사용.
"""
from datetime import date, datetime
from decimal import Decimal


def serialize_row(row: dict) -> dict:
    """RealDictRow의 date/datetime/Decimal 타입을 JSON 직렬화 가능하도록 변환."""
    result = {}
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            result[k] = v.isoformat()
        elif isinstance(v, Decimal):
            result[k] = float(v)
        else:
            result[k] = v
    return result
```

- [ ] **Step 2: import 검증**

```bash
python -c "from api.serialization import serialize_row; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
git add api/serialization.py
git commit -m "feat(refactor): B1 — DB 행 직렬화 모듈(api/serialization.py) 신설"
```

---

## Task 4: 공용 유틸 — `page_context.py`

**Files:**
- Create: `api/page_context.py`

- [ ] **Step 1: 파일 작성**

`pages.py:138-196`의 `_base_ctx`를 신규 모듈로 이전. import는 외부에서 주입하지 않고 내부에서 그대로 (시그니처·동작 보존):

```python
"""페이지 라우트용 공통 템플릿 컨텍스트 빌더 — pages.py에서 추출 (B1).

모든 HTML 페이지 라우트가 호출해 base.html에 필요한 공통 변수를 채운다.
"""
from typing import Optional

from fastapi import Request

from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from shared.tier_limits import (
    TIER_INFO,
    get_watchlist_limit,
    get_subscription_limit,
    get_chat_daily_limit,
)
from api.auth.models import UserInDB


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


def base_ctx(
    request: Request,
    active_page: str,
    user: Optional[UserInDB],
    auth_cfg: AuthConfig,
) -> dict:
    """모든 템플릿에 공통으로 전달할 컨텍스트.

    tier 정보와 사용량/한도는 업그레이드 CTA/사용량 배지 표시에 쓰인다.
    """
    effective_tier = user.effective_tier() if user else "free"
    tier_info = TIER_INFO.get(effective_tier)

    ctx = {
        "request": request,
        "active_page": active_page,
        "current_user": user,
        "auth_enabled": auth_cfg.enabled,
        "unread_notifications": 0,
        "tier": effective_tier,
        "tier_label": tier_info.label_ko if tier_info else None,
        "tier_badge_color": tier_info.badge_color if tier_info else "free",
        "watchlist_limit": get_watchlist_limit(effective_tier),
        "subscription_limit": get_subscription_limit(effective_tier),
        "chat_daily_limit": get_chat_daily_limit(effective_tier),
        "watchlist_usage": 0,
        "subscription_usage": 0,
    }
    if user and auth_cfg.enabled:
        try:
            conn = get_connection(_get_cfg())
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT 'noti'   AS k, COUNT(*) FROM user_notifications
                            WHERE user_id = %s AND is_read = FALSE
                        UNION ALL
                        SELECT 'watch'  AS k, COUNT(*) FROM user_watchlist
                            WHERE user_id = %s
                        UNION ALL
                        SELECT 'sub'    AS k, COUNT(*) FROM user_subscriptions
                            WHERE user_id = %s
                        """,
                        (user.id, user.id, user.id),
                    )
                    for key, cnt in cur.fetchall():
                        if key == "noti":
                            ctx["unread_notifications"] = cnt
                        elif key == "watch":
                            ctx["watchlist_usage"] = cnt
                        elif key == "sub":
                            ctx["subscription_usage"] = cnt
            finally:
                conn.close()
        except Exception as e:
            print(f"[page_context.base_ctx] 사용량 조회 실패 (user_id={user.id}): {e}")
    return ctx
```

- [ ] **Step 2: import 검증**

```bash
python -c "from api.page_context import base_ctx; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
git add api/page_context.py
git commit -m "feat(refactor): B1 — 페이지 공통 컨텍스트 빌더(api/page_context.py) 신설"
```

---

## Task 5: 신규 모듈 적용 (pages.py 내부에서) + baseline diff 검증

**핵심**: 라우트는 옮기지 않고, `pages.py` 내부의 함수 정의·필터 등록만 신규 모듈 호출로 교체. 이로써 신규 모듈 정합성을 검증한다.

**Files:**
- Modify: `api/routes/pages.py:1-196` (헤더 + 함수 정의 + 필터 등록)
- Modify: `api/routes/sessions.py:132-144` (`_serialize_row` 정의 제거 + 재export)

- [ ] **Step 1: `pages.py` 헤더 교체**

`pages.py`의 1~196줄을 다음으로 **완전 교체**:

```python
"""Jinja2 템플릿 기반 웹 페이지 라우트 — B1 진행 중 (단계적 도메인 이전)."""
from typing import Optional

from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from shared.tier_limits import (
    TIER_INFO,
    WATCHLIST_LIMITS,
    SUBSCRIPTION_LIMITS,
    STAGE2_DAILY_LIMITS,
    CHAT_DAILY_TURNS,
    HISTORY_DAYS_LIMITS,
    get_watchlist_limit,
    get_subscription_limit,
    get_chat_daily_limit,
)
from api.serialization import serialize_row as _serialize_row
from api.page_context import base_ctx as _base_ctx
from api.template_filters import register as _register_filters
from api.auth.dependencies import get_current_user, _get_auth_cfg
from api.auth.models import UserInDB

router = APIRouter(tags=["페이지"])
templates = Jinja2Templates(directory="api/templates")
_register_filters(templates.env)


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()
```

> 197줄 이후의 모든 `@router.get(...)` 라우트는 **그대로 유지**한다. 도메인 이전은 다음 Task부터 진행.

- [ ] **Step 2: `sessions.py`의 `_serialize_row` 정의 제거 + 재export**

`api/routes/sessions.py` 상단 import에 추가:
```python
from api.serialization import serialize_row as _serialize_row
```

`sessions.py:132-144`의 `def _serialize_row(...)` 블록을 **삭제**.

> 다른 파일(`chat.py`, `education.py`, `inquiry.py`, `pages.py`)은 여전히 `from api.routes.sessions import _serialize_row`로 import 중이므로, `sessions.py`에서 재export로 노출된 이름으로 계속 동작. cross-import 제거는 Task 17에서 일괄 처리.

- [ ] **Step 3: 서버 부팅 검증**

```bash
python -c "import api.main; print('OK')"
```
Expected: `OK` (ImportError 없음)

- [ ] **Step 4: 서버 기동 + baseline 캡처 (`05-utils-extracted`)**

별도 터미널:
```bash
AUTH_ENABLED=false uvicorn api.main:app --host 0.0.0.0 --port 8000
```
캡처:
```bash
python scripts/route_baseline.py capture --label 05-utils-extracted
```

- [ ] **Step 5: baseline diff 검증**

```bash
python scripts/route_baseline.py diff 00-before 05-utils-extracted
```
Expected: `[OK] diff 없음 (00-before vs 05-utils-extracted)`
실패 시 즉시 원인 파악. 통과 시 서버 종료.

- [ ] **Step 6: 커밋**

```bash
git add api/routes/pages.py api/routes/sessions.py
git commit -m "refactor(api): B1 — pages.py 공용 유틸을 신규 모듈로 이전 (동작 무변)"
```

---

## Task 6: 마케팅 페이지 이전 — 신규 `marketing.py`

**대상 라우트:** `pages.py:1483-1525`
- `GET /pages/landing` → landing_page
- `GET /pages/pricing` → pricing_page

**Files:**
- Create: `api/routes/marketing.py`
- Modify: `api/routes/pages.py` (해당 라우트 제거)
- Modify: `api/main.py` (라우터 include)

- [ ] **Step 1: `pages.py:1483-1525` 코드 확인**

```bash
sed -n '1483,1525p' api/routes/pages.py
```
출력된 두 함수(landing_page, pricing_page)의 시그니처와 본문을 그대로 복사할 준비.

- [ ] **Step 2: `marketing.py` 신규 작성**

```python
"""마케팅/가격 페이지 라우트 — B1: pages.py에서 이전."""
from typing import Optional

from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

from shared.config import AuthConfig
from shared.tier_limits import TIER_INFO
from api.page_context import base_ctx as _base_ctx
from api.template_filters import register as _register_filters
from api.auth.dependencies import get_current_user, _get_auth_cfg
from api.auth.models import UserInDB

pages_router = APIRouter(tags=["마케팅 페이지"])
templates = Jinja2Templates(directory="api/templates")
_register_filters(templates.env)


# 기존 pages.py의 landing_page / pricing_page 함수 본문을 그대로 복사.
# 함수 시그니처·데코레이터 path·내부 로직 무변경.
@pages_router.get("/pages/landing")
def landing_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    # ↓ pages.py:1484-1492 본문 그대로 복사
    ...


@pages_router.get("/pages/pricing")
def pricing_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    # ↓ pages.py:1494-1525 본문 그대로 복사
    ...
```

> 본문 복사 시 들여쓰기·주석·공백을 보존. `_base_ctx`/`templates`/`TIER_INFO` 등 의존 객체는 신규 파일 내에서 동일 이름으로 사용 가능 (위 import 참조).

- [ ] **Step 3: `pages.py:1483-1525` 삭제**

해당 두 함수와 데코레이터를 통째로 제거.

- [ ] **Step 4: `api/main.py` 라우터 등록 추가**

`main.py:11`의 import 라인을 다음으로 교체:
```python
from api.routes import (
    sessions, themes, proposals, pages, chat, admin,
    auth as auth_routes, user_admin, watchlist, track_record,
    stocks, education, inquiry, marketing,
)
```

`main.py:56` 직전(즉 `app.include_router(pages.router)` 위)에:
```python
# 도메인 콜로케이션된 페이지 라우터들 (B1 진행 중)
app.include_router(marketing.pages_router)
```

- [ ] **Step 5: import 검증**

```bash
python -c "import api.main; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: baseline 캡처 + diff**

서버 기동 → `python scripts/route_baseline.py capture --label 06-marketing` → 서버 종료
```bash
python scripts/route_baseline.py diff 00-before 06-marketing
```
Expected: `[OK] diff 없음`

- [ ] **Step 7: 커밋**

```bash
git add api/routes/marketing.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — 마케팅 페이지(/pages/landing, /pages/pricing) marketing.py로 이전"
```

---

## Task 7: 대시보드 이전 — 신규 `dashboard.py`

**대상 라우트:** `pages.py:202-501`
- `GET /` → dashboard

**Files:**
- Create: `api/routes/dashboard.py`
- Modify: `api/routes/pages.py` (해당 라우트 제거)
- Modify: `api/main.py` (라우터 include)

- [ ] **Step 1: `pages.py:200-501` 함수 본문 확인**

```bash
sed -n '200,501p' api/routes/pages.py
```
약 300줄의 단일 dashboard 함수. 통째로 복사 준비.

- [ ] **Step 2: `dashboard.py` 신규 작성**

```python
"""대시보드(/) 페이지 라우트 — B1: pages.py에서 이전.

복잡도: dashboard 함수는 약 300줄 — 어제 대비 변화·테마 요약·발굴유형 분포·
sector 카운트·all_proposals 정렬 등 단일 페이지 다수 쿼리. 본문은 무변경 이전.
"""
from typing import Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg2.extras import RealDictCursor

from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from shared.tier_limits import TIER_INFO
from api.serialization import serialize_row as _serialize_row
from api.page_context import base_ctx as _base_ctx
from api.template_filters import register as _register_filters
from api.auth.dependencies import get_current_user, _get_auth_cfg
from api.auth.models import UserInDB

pages_router = APIRouter(tags=["대시보드"])
templates = Jinja2Templates(directory="api/templates")
_register_filters(templates.env)


def _get_cfg() -> DatabaseConfig:
    return DatabaseConfig()


# ↓ pages.py:202-501 본문을 그대로 복사. 데코레이터 path 보존: "/"
@pages_router.get("/")
def dashboard(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    # 인증 활성 + 비로그인 → 랜딩 페이지로 안내
    if auth_cfg.enabled and user is None:
        return RedirectResponse(url="/pages/landing", status_code=302)

    ctx = _base_ctx(request, "dashboard", user, auth_cfg)
    # ... (pages.py:209-501 본문 전체 복사)
```

> 본문 복사 시 들여쓰기·주석·중간 변수명을 모두 보존. import 누락 주의 — 본문에서 사용하는 모든 심볼(`get_connection`, `RealDictCursor`, `_serialize_row`, `_base_ctx`, `templates`, `TIER_INFO`)이 모두 위에서 import되어야 함.

- [ ] **Step 3: `pages.py:198-501` 삭제**

dashboard 함수와 그 위 주석 블록(`# ──────... # Dashboard (Home) ...`) 통째로 제거.

- [ ] **Step 4: `api/main.py` 등록**

import 라인에 `dashboard` 추가:
```python
from api.routes import (
    sessions, themes, proposals, pages, chat, admin,
    auth as auth_routes, user_admin, watchlist, track_record,
    stocks, education, inquiry, marketing, dashboard,
)
```

`marketing.pages_router` include 다음 줄에:
```python
app.include_router(dashboard.pages_router)
```

- [ ] **Step 5: 부팅 검증**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 6: baseline 캡처 + diff**

서버 기동 → 캡처 (`07-dashboard`) → 서버 종료 → diff (`00-before` 대비)
```bash
python scripts/route_baseline.py capture --label 07-dashboard
python scripts/route_baseline.py diff 00-before 07-dashboard
```
Expected: `[OK] diff 없음`

- [ ] **Step 7: 커밋**

```bash
git add api/routes/dashboard.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — 대시보드(/) dashboard.py로 이전"
```

---

## Task 8: track_record 페이지 이전

**대상 라우트:** `pages.py:1475-1482`
- `GET /pages/track-record` → track_record_page

**Files:**
- Modify: `api/routes/track_record.py` (pages_router 추가)
- Modify: `api/routes/pages.py` (해당 라우트 제거)
- Modify: `api/main.py` (pages_router include)

- [ ] **Step 1: `track_record.py` 상단에 pages_router 추가**

`api/routes/track_record.py:13` 의 `router = APIRouter(...)` 다음 줄에 추가:
```python
pages_router = APIRouter(prefix="/pages/track-record", tags=["트랙레코드 페이지"])
```

상단 import 보강 (이미 있는 것은 중복 추가 안 함):
```python
from fastapi import Request
from fastapi.templating import Jinja2Templates

from shared.config import AuthConfig
from api.page_context import base_ctx as _base_ctx
from api.template_filters import register as _register_filters
from api.auth.dependencies import get_current_user, _get_auth_cfg

# 파일 하단(또는 적절한 위치)에:
templates = Jinja2Templates(directory="api/templates")
_register_filters(templates.env)
```

- [ ] **Step 2: 라우트 함수 추가**

`pages.py:1475-1482`의 `track_record_page` 함수를 `track_record.py` 하단에 복사하되, 데코레이터를 변경:
- BEFORE: `@router.get("/pages/track-record")`
- AFTER: `@pages_router.get("")`  (pages_router의 prefix와 합쳐 결과 path 동일)

함수 본문은 그대로:
```python
@pages_router.get("")
def track_record_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    ctx = _base_ctx(request, "track_record", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="track_record.html", context=ctx)
```

- [ ] **Step 3: `pages.py:1475-1482` 삭제**

- [ ] **Step 4: `api/main.py` 등록**

`track_record.router` include 다음 줄에:
```python
app.include_router(track_record.pages_router)
```

- [ ] **Step 5: 부팅 검증**

```bash
python -c "import api.main; print('OK')"
```

- [ ] **Step 6: baseline diff**

서버 기동 → 캡처 (`08-track-record`) → 종료 → diff
```bash
python scripts/route_baseline.py diff 00-before 08-track-record
```
Expected: `[OK] diff 없음`

- [ ] **Step 7: 커밋**

```bash
git add api/routes/track_record.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — /pages/track-record를 track_record.py로 콜로케이션"
```

---

## Task 9: stocks 페이지 이전

**대상 라우트:** `pages.py:623-642`
- `GET /pages/stocks/{ticker}` → stock_fundamentals_page

**Files:**
- Modify: `api/routes/stocks.py` (pages_router 추가)
- Modify: `api/routes/pages.py` (해당 라우트 제거)
- Modify: `api/main.py`

- [ ] **Step 1: `stocks.py` pages_router 추가**

기존 `router = APIRouter(prefix="/api/stocks", ...)` 다음에:
```python
pages_router = APIRouter(prefix="/pages/stocks", tags=["종목 페이지"])
```

필요 import (`Request`, `templates`, `_base_ctx`, `_register_filters`, `get_current_user`, `_get_auth_cfg`, `UserInDB`, `Optional`)는 상단에 보강.

- [ ] **Step 2: 라우트 함수 복사**

`pages.py:623-642`의 `stock_fundamentals_page`를 `stocks.py` 하단에 복사.
데코레이터: `@pages_router.get("/{ticker}")` (prefix와 합쳐 `/pages/stocks/{ticker}` 동일).

- [ ] **Step 3: `pages.py:623-642` 삭제**

- [ ] **Step 4: `api/main.py` 등록**

`stocks.router` include 다음 줄에:
```python
app.include_router(stocks.pages_router)
```

- [ ] **Step 5: 부팅 + baseline diff**

```bash
python -c "import api.main; print('OK')"
# (서버 기동 → 캡처 09-stocks → 종료)
python scripts/route_baseline.py diff 00-before 09-stocks
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/routes/stocks.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — /pages/stocks/{ticker}를 stocks.py로 콜로케이션"
```

---

## Task 10: watchlist + notifications + profile 이전

**대상 라우트:** `pages.py:1025-1116` (총 3개)
- `GET /pages/watchlist`
- `GET /pages/notifications`
- `GET /pages/profile`

세 라우트 모두 개인화 도메인. `watchlist.py`에 통합한다.

**Files:**
- Modify: `api/routes/watchlist.py`
- Modify: `api/routes/pages.py`
- Modify: `api/main.py`

- [ ] **Step 1: `watchlist.py` pages_router 추가**

기존 `router = APIRouter(tags=["개인화"])` 다음에:
```python
pages_router = APIRouter(tags=["개인화 페이지"])  # prefix 없음 — 라우트별 path 명시
```

필요 import 보강.

- [ ] **Step 2: 3개 라우트 본문 복사**

`pages.py:1025-1116`의 3개 함수를 그대로 복사. 데코레이터:
- `@pages_router.get("/pages/watchlist")`
- `@pages_router.get("/pages/notifications")`
- `@pages_router.get("/pages/profile")`

- [ ] **Step 3: `pages.py:1025-1116` 삭제**

- [ ] **Step 4: `api/main.py` 등록**

`watchlist.router` include 다음 줄에:
```python
app.include_router(watchlist.pages_router)
```

- [ ] **Step 5: 부팅 + baseline diff**

```bash
python -c "import api.main; print('OK')"
# (캡처 10-personalization → diff)
python scripts/route_baseline.py diff 00-before 10-personalization
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/routes/watchlist.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — /pages/watchlist|notifications|profile를 watchlist.py로 콜로케이션"
```

---

## Task 11: chat 페이지 이전

**대상 라우트:** `pages.py:1117-1277`
- `GET /pages/chat`
- `GET /pages/chat/new/{theme_id}`
- `GET /pages/chat/{chat_session_id}`

**Files:**
- Modify: `api/routes/chat.py`
- Modify: `api/routes/pages.py`
- Modify: `api/main.py`

- [ ] **Step 1: `chat.py` pages_router 추가**

기존 `router = APIRouter(prefix="/chat", ...)` 다음에:
```python
pages_router = APIRouter(prefix="/pages/chat", tags=["채팅 페이지"])
```

필요 import 보강.

- [ ] **Step 2: 3개 라우트 본문 복사**

`pages.py:1117-1277`의 함수들을 `chat.py` 하단에 복사. 데코레이터:
- `@pages_router.get("")` ← `/pages/chat`
- `@pages_router.get("/new/{theme_id}")` ← `/pages/chat/new/{theme_id}`
- `@pages_router.get("/{chat_session_id}")` ← `/pages/chat/{chat_session_id}`

> 등록 순서 보존: `/new/{theme_id}` → `/{chat_session_id}` (정적 path 우선).

- [ ] **Step 3: `pages.py:1117-1277` 삭제**

- [ ] **Step 4: `api/main.py` 등록**

`chat.router` include 다음 줄에:
```python
app.include_router(chat.pages_router)
```

- [ ] **Step 5: 부팅 + baseline diff**

```bash
python -c "import api.main; print('OK')"
python scripts/route_baseline.py diff 00-before 11-chat
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/routes/chat.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — /pages/chat/*를 chat.py로 콜로케이션"
```

---

## Task 12: education 페이지 이전

**대상 라우트:** `pages.py:1278-1474`
- `GET /pages/education`
- `GET /pages/education/topic/{slug}`
- `GET /pages/education/chat`
- `GET /pages/education/chat/new/{topic_id}`
- `GET /pages/education/chat/{session_id}`

**Files:**
- Modify: `api/routes/education.py`
- Modify: `api/routes/pages.py`
- Modify: `api/main.py`

- [ ] **Step 1: `education.py` pages_router 추가**

기존 `router = APIRouter(prefix="/education", ...)` 다음에:
```python
pages_router = APIRouter(prefix="/pages/education", tags=["교육 페이지"])
```

- [ ] **Step 2: 5개 라우트 본문 복사**

`pages.py:1278-1474`의 함수들을 `education.py` 하단에 복사. 데코레이터:
- `@pages_router.get("")` ← `/pages/education`
- `@pages_router.get("/topic/{slug}")`
- `@pages_router.get("/chat")`
- `@pages_router.get("/chat/new/{topic_id}")`
- `@pages_router.get("/chat/{session_id}")`

> 등록 순서 보존 (정적 path `/chat/new/...` 가 동적 `/chat/{session_id}` 보다 먼저).

- [ ] **Step 3: `pages.py:1278-1474` 삭제**

- [ ] **Step 4: `api/main.py` 등록**

`education.router` include 다음 줄에:
```python
app.include_router(education.pages_router)
```

- [ ] **Step 5: 부팅 + baseline diff**

```bash
python -c "import api.main; print('OK')"
python scripts/route_baseline.py diff 00-before 12-education
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/routes/education.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — /pages/education/*를 education.py로 콜로케이션"
```

---

## Task 13: inquiry 페이지 이전

**대상 라우트:** `pages.py:1526-1672`
- `GET /pages/inquiry`
- `GET /pages/inquiry/new`
- `GET /pages/inquiry/{inquiry_id}`

**Files:**
- Modify: `api/routes/inquiry.py`
- Modify: `api/routes/pages.py`
- Modify: `api/main.py`

- [ ] **Step 1: `inquiry.py` pages_router 추가**

기존 `router = APIRouter(prefix="/inquiry", ...)` 다음에:
```python
pages_router = APIRouter(prefix="/pages/inquiry", tags=["문의 페이지"])
```

- [ ] **Step 2: 3개 라우트 본문 복사**

데코레이터:
- `@pages_router.get("")`
- `@pages_router.get("/new")`
- `@pages_router.get("/{inquiry_id}")`

> `/new` 가 `/{inquiry_id}` 보다 먼저 등록되어야 정적 path 우선 매칭.

- [ ] **Step 3: `pages.py:1526-1672` 삭제**

- [ ] **Step 4: `api/main.py` 등록**

`inquiry.router` include 다음 줄에:
```python
app.include_router(inquiry.pages_router)
```

- [ ] **Step 5: 부팅 + baseline diff**

```bash
python -c "import api.main; print('OK')"
python scripts/route_baseline.py diff 00-before 13-inquiry
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/routes/inquiry.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — /pages/inquiry/*를 inquiry.py로 콜로케이션"
```

---

## Task 14: sessions 페이지 이전

**대상 라우트:** `pages.py:502-622`
- `GET /pages/sessions`
- `GET /pages/sessions/date/{analysis_date}`
- `GET /pages/sessions/{session_id}`

**Files:**
- Modify: `api/routes/sessions.py`
- Modify: `api/routes/pages.py`
- Modify: `api/main.py`

- [ ] **Step 1: `sessions.py` pages_router 추가**

기존 `router = APIRouter(prefix="/sessions", ...)` 다음에:
```python
pages_router = APIRouter(prefix="/pages/sessions", tags=["세션 페이지"])
```

필요 import 보강 (`Request`, `templates`, `_base_ctx`, `_register_filters`, `get_current_user`, `_get_auth_cfg`).

- [ ] **Step 2: 3개 라우트 본문 복사**

데코레이터:
- `@pages_router.get("")` ← `/pages/sessions`
- `@pages_router.get("/date/{analysis_date}")`
- `@pages_router.get("/{session_id}")`

> 정적 path `/date/...` 가 `/{session_id}` 보다 먼저.

- [ ] **Step 3: `pages.py:502-622` 삭제**

- [ ] **Step 4: `api/main.py` 등록**

`sessions.router` include 다음 줄에:
```python
app.include_router(sessions.pages_router)
```

- [ ] **Step 5: 부팅 + baseline diff**

```bash
python -c "import api.main; print('OK')"
python scripts/route_baseline.py diff 00-before 14-sessions
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/routes/sessions.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — /pages/sessions/*를 sessions.py로 콜로케이션"
```

---

## Task 15: themes 페이지 이전

**대상 라우트:** `pages.py:643-693`, `pages.py:802-876`
- `GET /pages/themes/history/{theme_key}` (643-693)
- `GET /pages/themes` (802-876)

**Files:**
- Modify: `api/routes/themes.py`
- Modify: `api/routes/pages.py`
- Modify: `api/main.py`

- [ ] **Step 1: `themes.py` pages_router 추가**

기존 `router = APIRouter(prefix="/themes", ...)` 다음에:
```python
pages_router = APIRouter(prefix="/pages/themes", tags=["테마 페이지"])
```

- [ ] **Step 2: 2개 라우트 본문 복사**

데코레이터:
- `@pages_router.get("")` ← `/pages/themes`
- `@pages_router.get("/history/{theme_key}")`

> 정적 `/history/...` 먼저, `""`(루트) 나중. 또는 `/history/...` 가 prefix로 인해 자동 분리되므로 순서 무관하지만 안전을 위해 `/history/{theme_key}` 먼저.

- [ ] **Step 3: `pages.py:643-693`과 `pages.py:802-876` 삭제**

두 블록 모두 제거. 줄 번호는 이전 Task로 인해 이미 어긋났을 수 있으니 grep으로 위치 재확인:
```bash
grep -n "/pages/themes" api/routes/pages.py
```

- [ ] **Step 4: `api/main.py` 등록**

`themes.router` include 다음 줄에:
```python
app.include_router(themes.pages_router)
```

- [ ] **Step 5: 부팅 + baseline diff**

```bash
python -c "import api.main; print('OK')"
python scripts/route_baseline.py diff 00-before 15-themes
```
Expected: `[OK] diff 없음`

- [ ] **Step 6: 커밋**

```bash
git add api/routes/themes.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — /pages/themes/*를 themes.py로 콜로케이션"
```

---

## Task 16: proposals 페이지 이전

**대상 라우트:** `pages.py:694-735`, `pages.py:736-801`, `pages.py:877-1024`
- `GET /proposals/{proposal_id}/stock-analysis` (694-735) — **prefix `/pages` 아님**
- `GET /pages/proposals/history/{ticker}` (736-801)
- `GET /pages/proposals` (877-1024)

**Files:**
- Modify: `api/routes/proposals.py`
- Modify: `api/routes/pages.py`
- Modify: `api/main.py`

- [ ] **Step 1: `proposals.py` pages_router 추가**

기존 `router = APIRouter(prefix="/proposals", ...)` + `api_router = APIRouter(prefix="/api/proposals", ...)` 다음에:
```python
pages_router = APIRouter(prefix="/pages/proposals", tags=["투자 제안 페이지"])
```

- [ ] **Step 2: `/proposals/{id}/stock-analysis` 라우트는 기존 `router`에 등록**

이 라우트만 prefix가 `/pages/...`가 아니라 `/proposals/...`이므로, 기존 `router`(prefix=`/proposals`)에 다음과 같이 추가:
```python
from fastapi.responses import HTMLResponse  # 명시적 응답 타입

@router.get("/{proposal_id}/stock-analysis", response_class=HTMLResponse)
def stock_analysis_page(...):
    # pages.py:695-735 본문 그대로
    ...
```
> 이로써 JSON `/proposals/{id}` (만약 있다면)와 path 충돌 가능성을 점검. 실제로는 `proposals.py`의 기존 `router`에 `/{id}` 형태 라우트가 없는지 grep으로 사전 확인:
> ```bash
> grep -n '@router.get' api/routes/proposals.py
> ```

- [ ] **Step 3: 나머지 2개 라우트는 pages_router에 등록**

`pages.py:736-801`과 `pages.py:877-1024` 본문을 `proposals.py` 하단에 복사. 데코레이터:
- `@pages_router.get("")` ← `/pages/proposals`
- `@pages_router.get("/history/{ticker}")` ← `/pages/proposals/history/{ticker}`

> 정적 `/history/...` 먼저, `""` 나중.

- [ ] **Step 4: `pages.py`에서 3개 블록 삭제**

```bash
grep -n "/proposals/.*/stock-analysis\|/pages/proposals" api/routes/pages.py
```
로 위치 재확인 후 통째로 제거.

- [ ] **Step 5: `api/main.py` 등록**

`proposals.api_router` include 다음 줄에:
```python
app.include_router(proposals.pages_router)
```

- [ ] **Step 6: 부팅 + baseline diff**

```bash
python -c "import api.main; print('OK')"
python scripts/route_baseline.py diff 00-before 16-proposals
```
Expected: `[OK] diff 없음`

- [ ] **Step 7: 커밋**

```bash
git add api/routes/proposals.py api/routes/pages.py api/main.py
git commit -m "refactor(api): B1 — /pages/proposals/* + /proposals/{id}/stock-analysis 콜로케이션"
```

---

## Task 17: cross-import 정리 — `_serialize_row` 사용처를 신규 모듈로

**대상:** `chat.py`, `education.py`, `inquiry.py`에서 `from api.routes.sessions import _serialize_row` 를 `from api.serialization import serialize_row as _serialize_row`로 변경.

**Files:**
- Modify: `api/routes/chat.py:10`
- Modify: `api/routes/education.py:10`
- Modify: `api/routes/inquiry.py:8`
- Modify: `api/routes/sessions.py` (재export 라인 제거 가능)

- [ ] **Step 1: 사용처 확인**

```bash
grep -rn "from api.routes.sessions import" api/ --include="*.py"
```
Expected: `chat.py`, `education.py`, `inquiry.py` 3곳.

- [ ] **Step 2: 3개 파일에서 import 라인 교체**

각 파일의 `from api.routes.sessions import _serialize_row` 라인을 다음으로 교체:
```python
from api.serialization import serialize_row as _serialize_row
```

- [ ] **Step 3: `sessions.py`에서 재export 라인 정리**

Task 5에서 추가했던 `from api.serialization import serialize_row as _serialize_row` 라인은, 이제 sessions.py 자체에서만 쓰이므로 그대로 둔다 (사용처가 sessions.py 내부의 list_sessions/get_session에 남아있음). 변경 없음.

- [ ] **Step 4: 부팅 + baseline diff**

```bash
python -c "import api.main; print('OK')"
python scripts/route_baseline.py diff 00-before 17-crossimport
```
Expected: `[OK] diff 없음`

- [ ] **Step 5: 커밋**

```bash
git add api/routes/chat.py api/routes/education.py api/routes/inquiry.py
git commit -m "refactor(api): B1 — _serialize_row cross-import 제거 (api.serialization 직접 사용)"
```

---

## Task 18: `pages.py` 삭제 + `main.py` 정리

**Files:**
- Delete: `api/routes/pages.py`
- Modify: `api/main.py`

- [ ] **Step 1: `pages.py` 잔존 라우트 확인**

```bash
grep -n "@router\." api/routes/pages.py
```
Expected: 출력 없음 (모든 라우트가 이전됨). 만약 출력이 있으면 해당 라우트가 누락된 것 — 즉시 원인 파악 후 적절한 도메인 파일로 이전.

- [ ] **Step 2: `pages.py` 삭제**

```bash
git rm api/routes/pages.py
```

- [ ] **Step 3: `main.py` 정리**

`api/main.py`에서:
1. `from api.routes import (..., pages, ...)` 에서 `pages,` 제거.
2. `app.include_router(pages.router)` 라인과 그 위 주석(`# HTML 페이지 라우트 ...`) 제거.
3. 이제 의도대로라면 `main.py`의 라우터 include 순서는:
   ```
   app.include_router(auth_routes.router)
   app.include_router(sessions.router)
   app.include_router(sessions.pages_router)
   app.include_router(themes.router)
   app.include_router(themes.pages_router)
   app.include_router(proposals.router)
   app.include_router(proposals.api_router)
   app.include_router(proposals.pages_router)
   app.include_router(chat.router)
   app.include_router(chat.pages_router)
   app.include_router(admin.router)
   app.include_router(user_admin.router)
   app.include_router(watchlist.router)
   app.include_router(watchlist.pages_router)
   app.include_router(track_record.router)
   app.include_router(track_record.pages_router)
   app.include_router(stocks.router)
   app.include_router(stocks.pages_router)
   app.include_router(education.router)
   app.include_router(education.pages_router)
   app.include_router(inquiry.router)
   app.include_router(inquiry.pages_router)
   app.include_router(marketing.pages_router)
   app.include_router(dashboard.pages_router)
   ```
   (도메인별 `router` 다음에 `pages_router` — 가독성)

- [ ] **Step 4: 부팅 + 최종 baseline diff**

```bash
python -c "import api.main; print('OK')"
# (서버 기동 → 캡처 99-final → 종료)
python scripts/route_baseline.py capture --label 99-final
python scripts/route_baseline.py diff 00-before 99-final
```
Expected: `[OK] diff 없음 (00-before vs 99-final)`

- [ ] **Step 5: 커밋**

```bash
git add api/main.py
git commit -m "refactor(api): B1 완료 — pages.py 삭제 + main.py 라우터 등록 정리"
```

---

## Task 19: 수동 스모크 테스트

**Files:** (없음 — 검증만)

- [ ] **Step 1: 정상 데이터로 서버 기동 (`AUTH_ENABLED` 원복)**

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

- [ ] **Step 2: 5개 핵심 페이지 브라우저 확인**

각각 200 응답 + 핵심 요소 렌더 확인:

| URL | 확인 요소 |
|---|---|
| `http://localhost:8000/` | 어제 대비 변화·발굴유형·sector 매크로 등 대시보드 위젯 정상 |
| `http://localhost:8000/pages/sessions` | 세션 목록 30개 표시 |
| `http://localhost:8000/pages/themes` | 테마 카드 + 필터 동작 |
| `http://localhost:8000/pages/proposals` | 제안 목록 + 페이지네이션 |
| `http://localhost:8000/pages/proposals/history/<실재_ticker>` | 티커 히스토리 카드 |

각 페이지에서 상단 우측 사용자 드롭다운·알림 배지 정상 표시 (`_base_ctx` 정상 동작 확인).

- [ ] **Step 3: 1개 인터랙션 테스트**

`/pages/themes`에서 horizon 필터를 클릭 → URL이 갱신되고 결과가 변하는지 확인.

- [ ] **Step 4: 서버 종료**

uvicorn Ctrl+C.

- [ ] **Step 5: 검증 완료 마커 커밋 (선택)**

스모크 테스트 결과를 spec 하단이나 plan 하단에 한 줄로 추가:
```markdown
## 검증 완료
- 2026-04-20 baseline diff: 00-before vs 99-final = 0
- 2026-04-20 수동 스모크: 5개 페이지 + 1개 인터랙션 OK
```

```bash
git add docs/superpowers/specs/2026-04-20-b1-pages-py-split-design.md
git commit -m "docs(refactor): B1 검증 완료 메모"
```

---

## 검증 요약

- 자동: `route_baseline.py diff` 가 모든 단계에서 `[OK] diff 없음`
- 수동: 5개 핵심 페이지 + 1개 인터랙션
- 회귀 위험: cross-import 제거 후 `chat.py`/`education.py`/`inquiry.py` 정상 동작 (Task 17 baseline 통과로 검증)

## 본 plan의 범위 밖 (B2/B3로 이월)

- `_get_cfg`/`_get_auth_cfg` 통합 (B2)
- `Depends(get_current_user) + auth_cfg = Depends(_get_auth_cfg) + ctx = _base_ctx(...)` 보일러플레이트를 단일 의존성으로 추출 (B2)
- JSON 응답 포맷 통일·HTTPException 표준화 (B3)
