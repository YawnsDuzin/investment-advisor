"""関심 종목 워치리스트 + 알림 구독 + 제안 메모 API + 개인화 페이지"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Body
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from shared.tier_limits import (
    get_watchlist_limit,
    get_subscription_limit,
    is_unlimited,
)
from psycopg2.extras import RealDictCursor
from api.serialization import serialize_row as _serialize_row
from api.auth.dependencies import get_current_user_required, quota_exceeded_detail
from api.auth.models import UserInDB
from api.templates_provider import templates
from api.deps import get_db_conn, make_page_ctx
from api.watchlist_health import compute_watchlist_health

router = APIRouter(tags=["개인화"])
pages_router = APIRouter(tags=["개인화 페이지"])  # prefix 없음 — path는 라우트별 명시


def _require_user(user: Optional[UserInDB] = Depends(get_current_user_required)) -> UserInDB:
    """인증 필수 + AUTH_ENABLED=false일 때 차단"""
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return user


# ── 워치리스트 ────────────────────────────────────


@router.get("/api/watchlist")
def list_watchlist(
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, ticker, asset_name, memo, created_at "
            "FROM user_watchlist WHERE user_id = %s ORDER BY created_at DESC",
            (user.id,),
        )
        rows = [_serialize_row(r) for r in cur.fetchall()]
    return rows


@router.post("/api/watchlist/{ticker}")
def add_watchlist(
    ticker: str,
    conn=Depends(get_db_conn),
    asset_name: str = Body(default=None, embed=True),
    memo: str = Body(default=None, embed=True),
    user: UserInDB = Depends(_require_user),
):
    ticker = ticker.upper().strip()
    tier = user.effective_tier()
    limit = get_watchlist_limit(tier)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 한도 체크: 무제한(None)이 아니고 신규 ticker면 카운트 확인
        if not is_unlimited(limit):
            cur.execute(
                "SELECT 1 FROM user_watchlist WHERE user_id = %s AND ticker = %s",
                (user.id, ticker),
            )
            already_exists = cur.fetchone() is not None
            if not already_exists:
                cur.execute(
                    "SELECT COUNT(*) FROM user_watchlist WHERE user_id = %s",
                    (user.id,),
                )
                count = cur.fetchone()["count"]
                if count >= limit:
                    raise HTTPException(
                        status_code=402,
                        detail=quota_exceeded_detail(
                            feature="watchlist",
                            current_tier=tier,
                            usage=count,
                            limit=limit,
                            message=f"워치리스트 한도({limit}종목)를 초과했습니다.",
                        ),
                    )

        cur.execute(
            "INSERT INTO user_watchlist (user_id, ticker, asset_name, memo) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (user_id, ticker) DO UPDATE SET asset_name = EXCLUDED.asset_name, memo = EXCLUDED.memo "
            "RETURNING id, ticker, asset_name, memo, created_at",
            (user.id, ticker, asset_name, memo),
        )
        row = cur.fetchone()
    conn.commit()
    return _serialize_row(row)


@router.delete("/api/watchlist/{ticker}")
def remove_watchlist(
    ticker: str,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    ticker = ticker.upper().strip()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM user_watchlist WHERE user_id = %s AND ticker = %s",
            (user.id, ticker),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="워치리스트에 없는 종목입니다")
    conn.commit()
    return {"ok": True}


# ── 워치리스트 헬스 체크 (Tier 1 #3) ────────────


def _fetch_watchlist_health_rows(conn, user_id: int) -> list[dict]:
    """워치리스트 종목 + stock_universe 메타 + 최신 PER 조회.

    `(ticker, market)` UNIQUE 이지만 미국 종목 한정 동일 ticker 가 NASDAQ/NYSE 양쪽에 있을 수 있어
    LATERAL 로 listed=TRUE 우선 + 시총 큰 row 1건만 매칭.
    """
    sql = """
        SELECT w.ticker,
               u.market,
               u.sector_norm,
               u.market_cap_krw,
               u.asset_name,
               f.per
        FROM user_watchlist w
        LEFT JOIN LATERAL (
            SELECT market, sector_norm, market_cap_krw, asset_name
            FROM stock_universe
            WHERE UPPER(ticker) = UPPER(w.ticker)
            ORDER BY listed DESC, market_cap_krw DESC NULLS LAST
            LIMIT 1
        ) u ON TRUE
        LEFT JOIN LATERAL (
            SELECT per
            FROM stock_universe_fundamentals
            WHERE ticker = w.ticker AND market = u.market
              AND per IS NOT NULL AND per > 0
              AND snapshot_date >= CURRENT_DATE - INTERVAL '30 days'
            ORDER BY snapshot_date DESC
            LIMIT 1
        ) f ON TRUE
        WHERE w.user_id = %s
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (user_id,))
        return [dict(r) for r in cur.fetchall()]


def _fetch_market_per_medians(conn) -> dict[str, Optional[float]]:
    """시장별 PER 중앙값 (최근 7일 latest snapshot per ticker 기준)."""
    sql = """
        WITH latest AS (
            SELECT DISTINCT ON (ticker, market)
                ticker, market, per
            FROM stock_universe_fundamentals
            WHERE snapshot_date >= CURRENT_DATE - INTERVAL '7 days'
              AND per IS NOT NULL AND per > 0
            ORDER BY ticker, market, snapshot_date DESC
        )
        SELECT market,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY per)::NUMERIC AS per_median
        FROM latest
        GROUP BY market
    """
    out: dict[str, Optional[float]] = {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        for r in cur.fetchall():
            mk = (r.get("market") or "").upper()
            med = r.get("per_median")
            out[mk] = float(med) if med is not None else None
    return out


def _build_watchlist_health(conn, user_id: int) -> dict:
    """페이지·API 양쪽에서 호출되는 합성 함수. DB 결측 시 빈 결과 반환."""
    try:
        rows = _fetch_watchlist_health_rows(conn, user_id)
    except Exception:
        rows = []
    try:
        medians = _fetch_market_per_medians(conn)
    except Exception:
        medians = {}
    return compute_watchlist_health(rows, medians)


@router.get("/api/watchlist/health")
def watchlist_health(
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    """워치리스트 분산도 진단 — 섹터 HHI, 시장 편향, 시총 분포, 평균 PER 비교."""
    return _build_watchlist_health(conn, user.id)


# ── 알림 구독 ─────────────────────────────────────


class SubscriptionCreate(BaseModel):
    sub_type: str  # 'ticker' | 'theme'
    sub_key: str
    label: str | None = None


@router.get("/api/subscriptions")
def list_subscriptions(
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, sub_type, sub_key, label, created_at "
            "FROM user_subscriptions WHERE user_id = %s ORDER BY created_at DESC",
            (user.id,),
        )
        rows = [_serialize_row(r) for r in cur.fetchall()]
    return rows


@router.post("/api/subscriptions")
def add_subscription(
    body: SubscriptionCreate,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    if body.sub_type not in ("ticker", "theme"):
        raise HTTPException(status_code=400, detail="sub_type은 'ticker' 또는 'theme'이어야 합니다")
    sub_key = body.sub_key.strip()
    if not sub_key:
        raise HTTPException(status_code=400, detail="sub_key가 비어있습니다")

    tier = user.effective_tier()
    limit = get_subscription_limit(tier)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 한도 체크
        if not is_unlimited(limit):
            cur.execute(
                "SELECT 1 FROM user_subscriptions "
                "WHERE user_id = %s AND sub_type = %s AND sub_key = %s",
                (user.id, body.sub_type, sub_key),
            )
            already_exists = cur.fetchone() is not None
            if not already_exists:
                cur.execute(
                    "SELECT COUNT(*) FROM user_subscriptions WHERE user_id = %s",
                    (user.id,),
                )
                count = cur.fetchone()["count"]
                if count >= limit:
                    raise HTTPException(
                        status_code=402,
                        detail=quota_exceeded_detail(
                            feature="subscription",
                            current_tier=tier,
                            usage=count,
                            limit=limit,
                            message=f"알림 구독 한도({limit}건)를 초과했습니다.",
                        ),
                    )

        cur.execute(
            "INSERT INTO user_subscriptions (user_id, sub_type, sub_key, label) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (user_id, sub_type, sub_key) DO NOTHING "
            "RETURNING id, sub_type, sub_key, label, created_at",
            (user.id, body.sub_type, sub_key, body.label),
        )
        row = cur.fetchone()
        if not row:
            return {"detail": "이미 구독 중입니다"}
    conn.commit()
    return _serialize_row(row)


@router.delete("/api/subscriptions/{sub_id}")
def remove_subscription(
    sub_id: int,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM user_subscriptions WHERE id = %s AND user_id = %s",
            (sub_id, user.id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="구독을 찾을 수 없습니다")
    conn.commit()
    return {"ok": True}


# ── 알림 ──────────────────────────────────────────


@router.get("/api/notifications")
def list_notifications(
    conn=Depends(get_db_conn),
    unread_only: bool = False,
    user: UserInDB = Depends(_require_user),
):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        q = "SELECT id, title, detail, link, is_read, created_at FROM user_notifications WHERE user_id = %s"
        params = [user.id]
        if unread_only:
            q += " AND is_read = FALSE"
        q += " ORDER BY created_at DESC LIMIT 50"
        cur.execute(q, params)
        rows = [_serialize_row(r) for r in cur.fetchall()]
    return rows


@router.post("/api/notifications/{noti_id}/read")
def mark_notification_read(
    noti_id: int,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE user_notifications SET is_read = TRUE WHERE id = %s AND user_id = %s",
            (noti_id, user.id),
        )
    conn.commit()
    return {"ok": True}


@router.post("/api/notifications/read-all")
def mark_all_read(
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE user_notifications SET is_read = TRUE WHERE user_id = %s AND is_read = FALSE",
            (user.id,),
        )
    conn.commit()
    return {"ok": True}


# ── 제안 메모 ─────────────────────────────────────


class MemoBody(BaseModel):
    content: str


@router.put("/api/proposals/{proposal_id}/memo")
def save_memo(
    proposal_id: int,
    body: MemoBody,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="메모 내용이 비어있습니다")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # proposal 존재 확인
        cur.execute("SELECT 1 FROM investment_proposals WHERE id = %s", (proposal_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="제안을 찾을 수 없습니다")

        cur.execute(
            "INSERT INTO user_proposal_memos (user_id, proposal_id, content) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id, proposal_id) DO UPDATE SET content = EXCLUDED.content, updated_at = NOW() "
            "RETURNING id, proposal_id, content, created_at, updated_at",
            (user.id, proposal_id, content),
        )
        row = cur.fetchone()
    conn.commit()
    return _serialize_row(row)


@router.delete("/api/proposals/{proposal_id}/memo")
def delete_memo(
    proposal_id: int,
    conn=Depends(get_db_conn),
    user: UserInDB = Depends(_require_user),
):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM user_proposal_memos WHERE user_id = %s AND proposal_id = %s",
            (user.id, proposal_id),
        )
    conn.commit()
    return {"ok": True}


# ──────────────────────────────────────────────
# 개인화 페이지 라우트 (pages_router)
# ──────────────────────────────────────────────

# ── Watchlist (관심 종목) ──────────────────────
@pages_router.get("/pages/watchlist")
def watchlist_page(conn=Depends(get_db_conn), ctx: dict = Depends(make_page_ctx("watchlist"))):
    """관심 종목 워치리스트 — 로그인 사용자만"""
    if not ctx["auth_enabled"] or ctx["_user"] is None:
        return RedirectResponse("/auth/login?next=/pages/watchlist", status_code=302)

    user = ctx["_user"]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM user_watchlist WHERE user_id = %s ORDER BY created_at DESC",
            (user.id,),
        )
        watchlist = [_serialize_row(r) for r in cur.fetchall()]

        for item in watchlist:
            cur.execute("""
                SELECT p.action, p.conviction, p.current_price, p.currency,
                       p.upside_pct, p.target_allocation, t.theme_name, s.analysis_date
                FROM investment_proposals p
                JOIN investment_themes t ON p.theme_id = t.id
                JOIN analysis_sessions s ON t.session_id = s.id
                WHERE UPPER(p.ticker) = UPPER(%s)
                ORDER BY s.analysis_date DESC LIMIT 1
            """, (item["ticker"],))
            latest = cur.fetchone()
            item["latest"] = _serialize_row(latest) if latest else None

        cur.execute(
            "SELECT * FROM user_subscriptions WHERE user_id = %s ORDER BY created_at DESC",
            (user.id,),
        )
        subscriptions = [_serialize_row(r) for r in cur.fetchall()]

    health = _build_watchlist_health(conn, user.id)

    return templates.TemplateResponse(request=ctx["request"], name="watchlist.html", context={
        **ctx,
        "watchlist": watchlist,
        "subscriptions": subscriptions,
        "health": health,
    })


# ── Notifications (알림) ───────────────────────
@pages_router.get("/pages/notifications")
def notifications_page(conn=Depends(get_db_conn), ctx: dict = Depends(make_page_ctx("notifications"))):
    """알림 목록 — 로그인 사용자만"""
    if not ctx["auth_enabled"] or ctx["_user"] is None:
        return RedirectResponse("/auth/login?next=/pages/notifications", status_code=302)

    user = ctx["_user"]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM user_notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT 100",
            (user.id,),
        )
        notifications = [_serialize_row(r) for r in cur.fetchall()]
        unread_count = sum(1 for n in notifications if not n.get("is_read"))

    return templates.TemplateResponse(request=ctx["request"], name="notifications.html", context={
        **ctx,
        "notifications": notifications,
        "unread_count": unread_count,
    })


# ── Profile (비밀번호 변경 + 연결된 계정) ────────────────────
@pages_router.get("/pages/profile")
def profile_page(ctx: dict = Depends(make_page_ctx("profile"))):
    """프로필 페이지 — 로그인 사용자만"""
    if not ctx["auth_enabled"] or ctx["_user"] is None:
        return RedirectResponse("/auth/login?next=/pages/profile", status_code=302)

    from api.auth.oauth_handlers import _list_linked_providers, _can_unlink
    from shared.config import AuthConfig

    conn = ctx["_conn"]
    user = ctx["_user"]
    auth_cfg = ctx["_auth_cfg"]

    linked = _list_linked_providers(conn, user.id) if auth_cfg.oauth_enabled else {}
    can_unlink_map = {p: _can_unlink(conn, user.id, p) for p in linked.keys()}

    return templates.TemplateResponse(request=ctx["request"], name="profile.html", context={
        **ctx,
        "error": ctx["request"].query_params.get("error", ""),
        "success": ctx["request"].query_params.get("success", ""),
        "linked_providers": linked,
        "can_unlink_map": can_unlink_map,
        "oauth_enabled": auth_cfg.oauth_enabled,
        "oauth_google_enabled": auth_cfg.google_active,
        "oauth_kakao_enabled": auth_cfg.kakao_active,
    })
