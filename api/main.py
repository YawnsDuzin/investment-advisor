"""FastAPI 투자 분석 조회 웹서비스"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from shared.config import DatabaseConfig, AuthConfig
from shared.db import init_db
from api.routes import (
    sessions, themes, proposals, chat, admin, admin_systemd,
    auth as auth_routes, user_admin, watchlist, track_record,
    stocks, education, inquiry, marketing, dashboard,
    chat_stream,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 DB 테이블 존재 확인 + 인증 설정 경고"""
    init_db(DatabaseConfig())
    from api.chat_stream_broker import broker as _chat_broker
    _chat_broker.start_cleanup()
    print("[CHAT-STREAM] broker cleanup task 시작 (TTL 600s, hard kill 1500s)")
    auth_cfg = AuthConfig()
    if auth_cfg.enabled:
        if auth_cfg.jwt_secret_key == "INSECURE_DEFAULT_CHANGE_IN_PRODUCTION":
            print("[AUTH] ⚠ 기본 JWT 시크릿 키 사용 중 — 프로덕션에서 반드시 변경하세요!")
        print(f"[AUTH] 인증 활성화 (Access: {auth_cfg.access_token_expire_minutes}분, Refresh: {auth_cfg.refresh_token_expire_days}일)")
    else:
        print("[AUTH] 인증 비활성화 (AUTH_ENABLED=false)")
    yield
    # ── shutdown — active broker 채널을 fail 로 종료 (구독자 hang 방지)
    # uvicorn 종료 시 BG task 가 CancelledError 로 끊기는데, _runner 의
    # except Exception 은 이를 못 잡음 → 채널이 active 로 남아 hard_kill (25분) 까지
    # 구독자가 hang. lifespan shutdown 에서 명시적으로 fail 처리.
    try:
        for (kind, sid), ch in list(_chat_broker._channels.items()):
            if ch.status == "active":
                await _chat_broker.fail(kind, sid, "server shutdown", "shutdown")
        print("[CHAT-STREAM] active 채널 shutdown fail 처리 완료")
    except Exception as e:
        print(f"[CHAT-STREAM] shutdown cleanup 실패: {e}")


app = FastAPI(
    title="AlphaSignal API",
    description="투자 테마 분석 결과 조회 API",
    version="1.0.0",
    lifespan=lifespan,
)

# ── 글로벌 예외 핸들러 (B3) ──────────────────────────
_STATUS_CODE_MAP = {
    400: "bad_request",
    401: "unauthorized",
    402: "payment_required",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_failed",
    429: "rate_limited",
    500: "server_error",
}


def _status_to_code(status: int) -> str:
    return _STATUS_CODE_MAP.get(status, f"http_{status}")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTPException 응답을 {"error": <code>, "detail": <msg>} 포맷으로 통일."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": _status_to_code(exc.status_code), "detail": exc.detail},
        headers=dict(exc.headers) if exc.headers else None,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """FastAPI 기본 422 응답을 {"error": "validation_failed", "detail": [...]} 포맷으로 통일."""
    return JSONResponse(
        status_code=422,
        content={"error": "validation_failed", "detail": exc.errors()},
    )


# 정적 파일 (CSS, JS)
app.mount("/static", StaticFiles(directory="api/static"), name="static")

# 인증 라우트 (/auth/*)
app.include_router(auth_routes.router)

# 도메인 라우터: 각 도메인의 JSON API + HTML 페이지 (B1 콜로케이션)
app.include_router(sessions.router)
app.include_router(sessions.pages_router)
app.include_router(themes.router)
app.include_router(themes.pages_router)
app.include_router(proposals.router)
app.include_router(proposals.api_router)
app.include_router(proposals.pages_router)
app.include_router(chat.router)
app.include_router(chat.pages_router)
app.include_router(chat_stream.router)
app.include_router(admin.router)
app.include_router(admin_systemd.router)
from api.routes import admin_news_feeds as _admin_news_feeds
app.include_router(_admin_news_feeds.router)
app.include_router(user_admin.router)
app.include_router(watchlist.router)
app.include_router(watchlist.pages_router)
app.include_router(track_record.router)
app.include_router(track_record.pages_router)
app.include_router(stocks.router)
app.include_router(stocks.pages_router)
app.include_router(stocks.indices_router)
from api.routes import signals as _signals_routes
app.include_router(_signals_routes.router)
from api.routes import sectors as _sectors_routes
app.include_router(_sectors_routes.router)
from api.routes import screener as _screener_routes
app.include_router(_screener_routes.router)
app.include_router(_screener_routes.pages_router)
app.include_router(education.router)
app.include_router(education.pages_router)
app.include_router(inquiry.router)
app.include_router(inquiry.pages_router)

app.include_router(marketing.pages_router)
app.include_router(dashboard.pages_router)

# 프리마켓 브리핑 (KST 06:30 생성)
from api.routes import briefing as _briefing_routes
app.include_router(_briefing_routes.router)
app.include_router(_briefing_routes.pages_router)

# 자유 질문 채팅 (Ask AI)
from api.routes import general_chat as _general_chat_routes
app.include_router(_general_chat_routes.router)
app.include_router(_general_chat_routes.pages_router)

# 채팅 starter 질문 (Ask AI / Theme Chat / AI Tutor 빈 채팅방 진입 시 동적 예시)
from api.routes import chat_starters as _chat_starters_routes
app.include_router(_chat_starters_routes.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
