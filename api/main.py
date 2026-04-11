"""FastAPI 투자 분석 조회 웹서비스"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from shared.config import DatabaseConfig
from shared.db import init_db
from api.routes import sessions, themes, proposals, pages


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 DB 테이블 존재 확인"""
    init_db(DatabaseConfig())
    yield


app = FastAPI(
    title="Investment Advisor API",
    description="투자 테마 분석 결과 조회 API",
    version="1.0.0",
    lifespan=lifespan,
)

# 정적 파일 (CSS, JS)
app.mount("/static", StaticFiles(directory="api/static"), name="static")

# JSON API 라우트 (/sessions, /themes, /proposals)
app.include_router(sessions.router)
app.include_router(themes.router)
app.include_router(proposals.router)

# HTML 페이지 라우트 (/, /pages/*)
app.include_router(pages.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=True)
