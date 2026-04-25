"""프리마켓 브리핑 조회 API + 페이지 라우트.

`pre_market_briefings` 테이블(v34) 콘텐츠를 JSON/HTML로 노출한다.
배치(`analyzer.briefing_main`)가 매일 KST 06:30 생성한 결과물을 표시.

엔드포인트:
  - GET /api/briefing/today                  최신 브리핑 1건
  - GET /api/briefing/{briefing_date}        특정 날짜
  - GET /pages/briefing                      최신 브리핑 HTML 페이지
  - GET /pages/briefing/{briefing_date}      특정 날짜 HTML
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse
from psycopg2.extras import RealDictCursor

from api.deps import get_db_conn, make_page_ctx
from api.serialization import serialize_row as _serialize_row
from api.templates_provider import templates


router = APIRouter(prefix="/api/briefing", tags=["프리마켓 브리핑"])
pages_router = APIRouter(prefix="/pages/briefing", tags=["프리마켓 브리핑 페이지"])


def _fetch_briefing(conn, briefing_date=None) -> dict | None:
    """briefing_date 지정 없으면 최신 1건. status != 'failed' 만 노출."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if briefing_date is None:
            cur.execute(
                """
                SELECT *
                FROM pre_market_briefings
                WHERE status IN ('success', 'partial')
                ORDER BY briefing_date DESC
                LIMIT 1
                """
            )
        else:
            cur.execute(
                "SELECT * FROM pre_market_briefings WHERE briefing_date = %s",
                (briefing_date,),
            )
        return cur.fetchone()


def _serialize_briefing(row: dict) -> dict:
    """DB row → API 응답 dict. JSONB는 그대로 통과."""
    if not row:
        return {}
    out = _serialize_row(dict(row))
    # 핵심 필드 단순 분해
    return {
        "briefing_date": out.get("briefing_date"),
        "source_trade_date": out.get("source_trade_date"),
        "status": out.get("status"),
        "us_summary": row.get("us_summary"),
        "briefing_data": row.get("briefing_data"),
        "regime_snapshot": row.get("regime_snapshot"),
        "error_message": row.get("error_message"),
        "generated_at": out.get("generated_at"),
        "updated_at": out.get("updated_at"),
    }


@router.get("/today")
def get_today_briefing(conn = Depends(get_db_conn)):
    """가장 최근 브리핑 1건. 없으면 404."""
    row = _fetch_briefing(conn)
    if not row:
        raise HTTPException(status_code=404, detail="브리핑이 아직 생성되지 않았습니다")
    return _serialize_briefing(row)


@router.get("/{briefing_date}")
def get_briefing_by_date(briefing_date: date, conn = Depends(get_db_conn)):
    row = _fetch_briefing(conn, briefing_date)
    if not row:
        raise HTTPException(status_code=404, detail=f"{briefing_date} 브리핑 없음")
    return _serialize_briefing(row)


@router.get("")
def list_briefings(conn = Depends(get_db_conn), limit: int = 14):
    """최근 브리핑 목록 (헤더 + 메타만, 본문 제외)."""
    limit = max(1, min(int(limit), 60))
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT briefing_date, source_trade_date, status,
                   generated_at, updated_at,
                   (briefing_data->>'morning_brief') AS morning_brief
            FROM pre_market_briefings
            ORDER BY briefing_date DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return {"count": len(rows), "items": [_serialize_row(r) for r in rows]}


@pages_router.get("", response_class=HTMLResponse)
def page_today_briefing(
    request: Request,
    ctx: dict = Depends(make_page_ctx("briefing")),
):
    """최신 브리핑 페이지."""
    conn = ctx["_conn"]
    row = _fetch_briefing(conn)
    return templates.TemplateResponse(
        "briefing.html",
        {**ctx, "briefing": _serialize_briefing(row) if row else None,
         "is_today": True},
    )


@pages_router.get("/{briefing_date}", response_class=HTMLResponse)
def page_briefing_by_date(
    request: Request,
    briefing_date: date,
    ctx: dict = Depends(make_page_ctx("briefing")),
):
    conn = ctx["_conn"]
    row = _fetch_briefing(conn, briefing_date)
    return templates.TemplateResponse(
        "briefing.html",
        {**ctx, "briefing": _serialize_briefing(row) if row else None,
         "is_today": False, "requested_date": str(briefing_date)},
    )
