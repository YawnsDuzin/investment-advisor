"""FastAPI 투자 분석 조회 웹서비스"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from shared.config import DatabaseConfig, AuthConfig
from shared.db import init_db
from api.routes import (
    sessions, themes, proposals, pages, chat, admin,
    auth as auth_routes, user_admin, watchlist, track_record,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 DB 테이블 존재 확인 + 인증 설정 경고"""
    init_db(DatabaseConfig())
    auth_cfg = AuthConfig()
    if auth_cfg.enabled:
        if auth_cfg.jwt_secret_key == "INSECURE_DEFAULT_CHANGE_IN_PRODUCTION":
            print("[AUTH] ⚠ 기본 JWT 시크릿 키 사용 중 — 프로덕션에서 반드시 변경하세요!")
        print(f"[AUTH] 인증 활성화 (Access: {auth_cfg.access_token_expire_minutes}분, Refresh: {auth_cfg.refresh_token_expire_days}일)")
    else:
        print("[AUTH] 인증 비활성화 (AUTH_ENABLED=false)")
    yield


app = FastAPI(
    title="AlphaScope API",
    description="투자 테마 분석 결과 조회 API",
    version="1.0.0",
    lifespan=lifespan,
)

# 정적 파일 (CSS, JS)
app.mount("/static", StaticFiles(directory="api/static"), name="static")

# 인증 라우트 (/auth/*)
app.include_router(auth_routes.router)

# JSON API 라우트 (/sessions, /themes, /proposals, /chat)
app.include_router(sessions.router)
app.include_router(themes.router)
app.include_router(proposals.router)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(user_admin.router)
app.include_router(watchlist.router)
app.include_router(track_record.router)

# HTML 페이지 라우트 (/, /pages/*)
app.include_router(pages.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
