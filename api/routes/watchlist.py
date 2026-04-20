"""관심 종목 워치리스트 + 알림 구독 + 제안 메모 API + 개인화 페이지"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Body, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from shared.config import DatabaseConfig, AuthConfig
from shared.db import get_connection
from shared.tier_limits import (
    get_watchlist_limit,
    get_subscription_limit,
    is_unlimited,
)
from psycopg2.extras import RealDictCursor
from api.serialization import serialize_row as _serialize_row
from api.page_context import base_ctx as _base_ctx
from api.auth.dependencies import get_current_user, get_current_user_required, quota_exceeded_detail, _get_auth_cfg
from api.auth.models import UserInDB
from api.templates_provider import templates
from api.deps import get_db_cfg as _get_cfg

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
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, ticker, asset_name, memo, created_at "
                "FROM user_watchlist WHERE user_id = %s ORDER BY created_at DESC",
                (user.id,),
            )
            rows = [_serialize_row(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


@router.post("/api/watchlist/{ticker}")
def add_watchlist(
    ticker: str,
    asset_name: str = Body(default=None, embed=True),
    memo: str = Body(default=None, embed=True),
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    ticker = ticker.upper().strip()
    tier = user.effective_tier()
    limit = get_watchlist_limit(tier)

    conn = get_connection(cfg)
    try:
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
    finally:
        conn.close()
    return _serialize_row(row)


@router.delete("/api/watchlist/{ticker}")
def remove_watchlist(
    ticker: str,
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    ticker = ticker.upper().strip()
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_watchlist WHERE user_id = %s AND ticker = %s",
                (user.id, ticker),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="워치리스트에 없는 종목입니다")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ── 알림 구독 ─────────────────────────────────────


class SubscriptionCreate(BaseModel):
    sub_type: str  # 'ticker' | 'theme'
    sub_key: str
    label: str | None = None


@router.get("/api/subscriptions")
def list_subscriptions(
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, sub_type, sub_key, label, created_at "
                "FROM user_subscriptions WHERE user_id = %s ORDER BY created_at DESC",
                (user.id,),
            )
            rows = [_serialize_row(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


@router.post("/api/subscriptions")
def add_subscription(
    body: SubscriptionCreate,
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    if body.sub_type not in ("ticker", "theme"):
        raise HTTPException(status_code=400, detail="sub_type은 'ticker' 또는 'theme'이어야 합니다")
    sub_key = body.sub_key.strip()
    if not sub_key:
        raise HTTPException(status_code=400, detail="sub_key가 비어있습니다")

    tier = user.effective_tier()
    limit = get_subscription_limit(tier)

    conn = get_connection(cfg)
    try:
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
    finally:
        conn.close()
    return _serialize_row(row)


@router.delete("/api/subscriptions/{sub_id}")
def remove_subscription(
    sub_id: int,
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_subscriptions WHERE id = %s AND user_id = %s",
                (sub_id, user.id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="구독을 찾을 수 없습니다")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ── 알림 ──────────────────────────────────────────


@router.get("/api/notifications")
def list_notifications(
    unread_only: bool = False,
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    conn = get_connection(cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            q = "SELECT id, title, detail, link, is_read, created_at FROM user_notifications WHERE user_id = %s"
            params = [user.id]
            if unread_only:
                q += " AND is_read = FALSE"
            q += " ORDER BY created_at DESC LIMIT 50"
            cur.execute(q, params)
            rows = [_serialize_row(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


@router.post("/api/notifications/{noti_id}/read")
def mark_notification_read(
    noti_id: int,
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_notifications SET is_read = TRUE WHERE id = %s AND user_id = %s",
                (noti_id, user.id),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.post("/api/notifications/read-all")
def mark_all_read(
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_notifications SET is_read = TRUE WHERE user_id = %s AND is_read = FALSE",
                (user.id,),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ── 제안 메모 ─────────────────────────────────────


class MemoBody(BaseModel):
    content: str


@router.put("/api/proposals/{proposal_id}/memo")
def save_memo(
    proposal_id: int,
    body: MemoBody,
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="메모 내용이 비어있습니다")

    conn = get_connection(cfg)
    try:
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
    finally:
        conn.close()
    return _serialize_row(row)


@router.delete("/api/proposals/{proposal_id}/memo")
def delete_memo(
    proposal_id: int,
    user: UserInDB = Depends(_require_user),
    cfg: DatabaseConfig = Depends(_get_cfg),
):
    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_proposal_memos WHERE user_id = %s AND proposal_id = %s",
                (user.id, proposal_id),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ──────────────────────────────────────────────
# 개인화 페이지 라우트 (pages_router)
# ──────────────────────────────────────────────

# ── Watchlist (관심 종목) ──────────────────────
@pages_router.get("/pages/watchlist")
def watchlist_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """관심 종목 워치리스트 — 로그인 사용자만"""
    if not auth_cfg.enabled or user is None:
        return RedirectResponse("/auth/login?next=/pages/watchlist", status_code=302)

    ctx = _base_ctx(request, "watchlist", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
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
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="watchlist.html", context={
        **ctx,
        "watchlist": watchlist,
        "subscriptions": subscriptions,
    })


# ── Notifications (알림) ───────────────────────
@pages_router.get("/pages/notifications")
def notifications_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """알림 목록 — 로그인 사용자만"""
    if not auth_cfg.enabled or user is None:
        return RedirectResponse("/auth/login?next=/pages/notifications", status_code=302)

    ctx = _base_ctx(request, "notifications", user, auth_cfg)
    conn = get_connection(_get_cfg())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM user_notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT 100",
                (user.id,),
            )
            notifications = [_serialize_row(r) for r in cur.fetchall()]
            unread_count = sum(1 for n in notifications if not n.get("is_read"))
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="notifications.html", context={
        **ctx,
        "notifications": notifications,
        "unread_count": unread_count,
    })


# ── Profile (비밀번호 변경) ────────────────────
@pages_router.get("/pages/profile")
def profile_page(request: Request, user: Optional[UserInDB] = Depends(get_current_user), auth_cfg: AuthConfig = Depends(_get_auth_cfg)):
    """프로필 페이지 — 로그인 사용자만"""
    if not auth_cfg.enabled or user is None:
        return RedirectResponse("/auth/login?next=/pages/profile", status_code=302)
    ctx = _base_ctx(request, "profile", user, auth_cfg)
    return templates.TemplateResponse(request=request, name="profile.html", context={
        **ctx,
        "error": "",
        "success": "",
    })
