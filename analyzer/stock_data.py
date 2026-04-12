"""실시간 주가/재무 데이터 조회 모듈 — yfinance 기반"""
import yfinance as yf


def _normalize_ticker(ticker: str, market: str) -> str:
    """시장 코드에 맞는 yfinance 티커 형식으로 변환

    KRX(코스피) → 005930.KS, KQ(코스닥) → 247540.KQ
    미국 시장 → 그대로 사용
    """
    ticker = ticker.strip().upper()
    market = (market or "").strip().upper()

    if market in ("KRX", "KOSPI", "KSE"):
        # 한국 코스피: 숫자 6자리.KS
        digits = ticker.lstrip("0") if not ticker.startswith("0") else ticker
        if ticker.replace(".", "").isdigit():
            return f"{ticker}.KS"
        return ticker
    elif market in ("KOSDAQ", "KQ"):
        if ticker.replace(".", "").isdigit():
            return f"{ticker}.KQ"
        return ticker
    # 미국/기타: 그대로
    return ticker


def _format_number(value, currency: str = "") -> str:
    """숫자를 읽기 쉬운 형식으로 변환"""
    if value is None:
        return "N/A"
    if abs(value) >= 1_000_000_000_000:
        return f"{currency}{value / 1_000_000_000_000:.1f}조"
    if abs(value) >= 100_000_000:
        return f"{currency}{value / 100_000_000:.0f}억"
    if abs(value) >= 1_000_000:
        return f"{currency}{value / 1_000_000:.1f}M"
    return f"{currency}{value:,.0f}"


def fetch_stock_data(ticker: str, market: str) -> dict | None:
    """단일 종목의 주가/재무 데이터 조회

    Returns:
        dict with keys: ticker, price, change_pct, high_52w, low_52w,
        volume_avg, market_cap, per, pbr, eps, dividend_yield, currency
        실패 시 None
    """
    yf_ticker = _normalize_ticker(ticker, market)
    try:
        stock = yf.Ticker(yf_ticker)
        info = stock.info

        # yfinance가 유효한 데이터를 반환했는지 확인
        if not info or info.get("regularMarketPrice") is None:
            print(f"  [주가] {yf_ticker} 데이터 없음")
            return None

        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
        change_pct = None
        if price and prev_close and prev_close > 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        return {
            "ticker": ticker,
            "yf_ticker": yf_ticker,
            "price": price,
            "change_pct": change_pct,
            "high_52w": info.get("fiftyTwoWeekHigh"),
            "low_52w": info.get("fiftyTwoWeekLow"),
            "volume_avg": info.get("averageDailyVolume10Day") or info.get("averageVolume"),
            "market_cap": info.get("marketCap"),
            "per": info.get("trailingPE") or info.get("forwardPE"),
            "pbr": info.get("priceToBook"),
            "eps": info.get("trailingEps"),
            "dividend_yield": info.get("dividendYield"),
            "currency": info.get("currency", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "short_name": info.get("shortName", ""),
        }

    except Exception as e:
        print(f"  [주가] {yf_ticker} 조회 실패: {e}")
        return None


def fetch_multiple_stocks(stocks: list[dict]) -> dict[str, dict]:
    """복수 종목 일괄 조회

    Args:
        stocks: [{"ticker": "NVDA", "market": "NASDAQ"}, ...] 형태 리스트

    Returns:
        {ticker: stock_data_dict} 매핑. 조회 실패 종목은 제외.
    """
    results = {}
    seen = set()

    for stock in stocks:
        ticker = stock.get("ticker", "").strip().upper()
        market = stock.get("market", "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)

        print(f"  [주가] {ticker} ({market}) 조회 중...")
        data = fetch_stock_data(ticker, market)
        if data:
            results[ticker] = data
            price_str = f"{data['currency']}{data['price']:,.2f}" if data['price'] else "N/A"
            print(f"  [주가] {ticker} → {price_str}")

    return results


def format_stock_data_text(data: dict) -> str:
    """주가 데이터를 프롬프트 삽입용 텍스트로 포맷팅"""
    if not data:
        return ""

    currency = data.get("currency", "")
    c = "₩" if currency == "KRW" else f"${'' if currency == 'USD' else currency + ' '}" if currency else ""

    lines = [f"### {data.get('short_name', data['ticker'])} ({data['ticker']})"]

    # 현재가 + 등락률
    price = data.get("price")
    if price:
        change = data.get("change_pct")
        change_str = f" (전일 대비: {'+' if change > 0 else ''}{change}%)" if change is not None else ""
        lines.append(f"- 현재가: {c}{price:,.2f}{change_str}")

    # 52주 고저
    high = data.get("high_52w")
    low = data.get("low_52w")
    if high and low:
        lines.append(f"- 52주 고가/저가: {c}{high:,.2f} / {c}{low:,.2f}")
        if price and high > 0:
            from_high = round((price - high) / high * 100, 1)
            from_low = round((price - low) / low * 100, 1) if low > 0 else 0
            lines.append(f"  (고점 대비 {from_high}%, 저점 대비 +{from_low}%)")

    # 시총
    mcap = data.get("market_cap")
    if mcap:
        lines.append(f"- 시가총액: {_format_number(mcap, c)}")

    # 밸류에이션
    per = data.get("per")
    pbr = data.get("pbr")
    eps = data.get("eps")
    vals = []
    if per:
        vals.append(f"PER {per:.1f}")
    if pbr:
        vals.append(f"PBR {pbr:.2f}")
    if eps:
        vals.append(f"EPS {c}{eps:,.2f}")
    if vals:
        lines.append(f"- 밸류에이션: {' / '.join(vals)}")

    # 거래량
    vol = data.get("volume_avg")
    if vol:
        lines.append(f"- 평균 거래량(10일): {vol:,.0f}주")

    # 배당
    div = data.get("dividend_yield")
    if div and div > 0:
        lines.append(f"- 배당수익률: {div * 100:.2f}%")

    # 섹터/업종
    sector = data.get("sector")
    industry = data.get("industry")
    if sector or industry:
        lines.append(f"- 업종: {sector or ''} / {industry or ''}")

    return "\n".join(lines)
