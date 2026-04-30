"""관리자 — RSS 피드 health 모니터링 (v45).

`news_feed_health` 테이블의 14일 rolling 데이터를 표 + sparkline 으로 시각화.
만성 실패 피드(7일 연속 dead/stale/parse_error) 즉시 알림.

엔드포인트:
  - GET  /admin/news-feeds       — HTML 표 (admin 전용)
  - GET  /admin/news-feeds/data  — JSON (테이블 갱신용)
  - GET  /admin/news-feeds/chronic — 7일 연속 실패 목록 JSON
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from api.auth.dependencies import require_role
from api.auth.models import UserInDB
from api.deps import get_db_cfg, make_page_ctx
from api.templates_provider import templates
from shared.config import DatabaseConfig
from shared.db import list_recent_feed_health, detect_chronic_failures


router = APIRouter(prefix="/admin/news-feeds", tags=["관리자-뉴스피드"])


@router.get("")
def news_feeds_page(ctx: dict = Depends(make_page_ctx("admin"))):
    """피드 health 모니터링 페이지 (HTML)."""
    if ctx["auth_enabled"]:
        if ctx["_user"] is None:
            return RedirectResponse("/auth/login?next=/admin/news-feeds", status_code=302)
        if ctx["_user"].role != "admin":
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    cfg = DatabaseConfig()
    try:
        feeds = list_recent_feed_health(cfg, days=14)
        chronic = detect_chronic_failures(cfg, threshold_days=7)
    except Exception as e:
        feeds = []
        chronic = []
        ctx["error"] = f"DB 조회 실패: {e}"

    ctx["feeds"] = feeds
    ctx["chronic"] = chronic
    ctx["chronic_count"] = len(chronic)
    return templates.TemplateResponse(
        request=ctx["request"], name="admin_news_feeds.html", context=ctx
    )


@router.get("/data")
def news_feeds_data(
    days: int = 14,
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """피드 health 14일 rolling JSON — admin UI 비동기 갱신용."""
    cfg = DatabaseConfig()
    feeds = list_recent_feed_health(cfg, days=days)
    # JSON 직렬화 — date 객체 ISO 문자열로 변환
    out = []
    for f in feeds:
        out.append({
            **f,
            "latest_check_date": f["latest_check_date"].isoformat() if f["latest_check_date"] else None,
            "latest_pub_at": f["latest_pub_at"].isoformat() if f["latest_pub_at"] else None,
        })
    return {"feeds": out, "count": len(out), "days": days}


@router.get("/chronic")
def news_feeds_chronic(
    threshold_days: int = 7,
    _admin: Optional[UserInDB] = Depends(require_role("admin")),
):
    """N일 연속 실패 피드 목록 — 알림/교체 검토 대상."""
    cfg = DatabaseConfig()
    chronic = detect_chronic_failures(cfg, threshold_days=threshold_days)
    out = []
    for c in chronic:
        out.append({
            **c,
            "last_check": c["last_check"].isoformat() if c["last_check"] else None,
        })
    return {"chronic": out, "count": len(out), "threshold_days": threshold_days}
