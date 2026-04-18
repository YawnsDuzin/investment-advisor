"""종목 기초정보 조회 API"""
from fastapi import APIRouter, HTTPException, Query
from analyzer.stock_data import fetch_fundamentals

router = APIRouter(prefix="/api/stocks", tags=["종목 기초정보"])


@router.get("/{ticker}/fundamentals")
def get_fundamentals(
    ticker: str,
    market: str = Query(default="", description="시장 코드 (KRX, NASDAQ 등)"),
):
    """종목 기초정보 온디맨드 조회 (yfinance 기반, 1시간 캐싱)"""
    data = fetch_fundamentals(ticker, market)
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"종목 '{ticker}' 데이터를 조회할 수 없습니다",
        )
    return data
