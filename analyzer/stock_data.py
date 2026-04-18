"""실시간 주가/재무 데이터 조회 모듈 — yfinance + pykrx(한국 주식 크로스체크)"""
from datetime import datetime, timedelta
from shared.logger import get_logger

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from pykrx import stock as pykrx_stock
except ImportError:
    pykrx_stock = None


# ── 한국 시장 판별 ──────────────────────────────────

_KRX_MARKETS = {"KRX", "KOSPI", "KSE", "KOSDAQ", "KQ"}


def _is_korean_market(market: str) -> bool:
    return (market or "").strip().upper() in _KRX_MARKETS


def _normalize_ticker(ticker: str, market: str) -> str:
    """시장 코드에 맞는 yfinance 티커 형식으로 변환

    KRX(코스피) → 005930.KS, KQ(코스닥) → 247540.KQ
    HKEX → 1211.HK, TSE → 6758.T, TWSE → 2330.TW
    미국 시장 → 그대로 사용
    """
    ticker = ticker.strip().upper()
    market = (market or "").strip().upper()

    # 이미 접미사가 붙어 있으면 그대로 반환
    if "." in ticker:
        return ticker

    if market in ("KRX", "KOSPI", "KSE"):
        if ticker.isdigit():
            return f"{ticker}.KS"
        return ticker
    elif market in ("KOSDAQ", "KQ"):
        if ticker.isdigit():
            return f"{ticker}.KQ"
        return ticker
    elif market in ("HKEX", "HKG", "HKSE"):
        if ticker.isdigit():
            return f"{ticker}.HK"
        return ticker
    elif market in ("TSE", "JPX", "TYO"):
        if ticker.isdigit():
            return f"{ticker}.T"
        return ticker
    elif market in ("TWSE", "TPE"):
        if ticker.isdigit():
            return f"{ticker}.TW"
        return ticker
    elif market in ("SSE", "SZSE", "SHA", "SHE"):
        if ticker.isdigit():
            suffix = ".SS" if market in ("SSE", "SHA") else ".SZ"
            return f"{ticker}{suffix}"
        return ticker
    elif market in ("LSE", "LON"):
        return f"{ticker}.L"
    elif market in ("FSE", "FRA", "XETRA"):
        return f"{ticker}.DE"
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


# ── pykrx 헬퍼 ──────────────────────────────────────

def _pykrx_fetch_price(ticker: str) -> dict | None:
    """pykrx로 한국 주식 현재가 + 밸류에이션 조회 (폴백/크로스체크용)"""
    if pykrx_stock is None:
        return None

    raw_ticker = ticker.strip().upper()
    today = datetime.now()
    # 최근 거래일 찾기 (주말/공휴일 대비 5일 범위)
    start = (today - timedelta(days=7)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    try:
        ohlcv = pykrx_stock.get_market_ohlcv(start, end, raw_ticker)
        if ohlcv.empty:
            return None

        last = ohlcv.iloc[-1]
        price = float(last["종가"])
        if price <= 0:
            return None

        # 밸류에이션 조회
        last_date = ohlcv.index[-1].strftime("%Y%m%d")
        fund = pykrx_stock.get_market_fundamental(last_date, last_date, raw_ticker)
        per = pbr = div_yield = None
        if not fund.empty:
            f = fund.iloc[-1]
            per = float(f.get("PER", 0)) or None
            pbr = float(f.get("PBR", 0)) or None
            div_yield = float(f.get("DIV", 0)) or None

        return {
            "price": price,
            "per": per,
            "pbr": pbr,
            "dividend_yield_pct": div_yield,
            "source": "pykrx",
        }
    except Exception as e:
        get_logger("pykrx").warning(f"{raw_ticker} 조회 실패: {e}")
        return None


def _pykrx_fetch_history(ticker: str, days: int = 365) -> "list[tuple[str, float]]":
    """pykrx로 한국 주식 일별 종가 이력 조회

    Returns:
        [(date_str, close_price), ...] 오래된 순 정렬. 실패 시 빈 리스트.
    """
    if pykrx_stock is None:
        return []

    raw_ticker = ticker.strip().upper()
    today = datetime.now()
    start = (today - timedelta(days=days + 10)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    try:
        ohlcv = pykrx_stock.get_market_ohlcv(start, end, raw_ticker)
        if ohlcv.empty:
            return []
        return [
            (idx.strftime("%Y-%m-%d"), float(row["종가"]))
            for idx, row in ohlcv.iterrows()
            if float(row["종가"]) > 0
        ]
    except Exception:
        return []


# ── KRX 티커 검증/교정 ──────────────────────────────

# 캐시: {종목명: 티커코드, ...} — 세션 중 1회 빌드
_krx_name_to_ticker: dict[str, str] | None = None
_krx_ticker_to_name: dict[str, str] | None = None


def _build_krx_lookup() -> None:
    """pykrx에서 KOSPI+KOSDAQ 전 종목의 이름↔티커 매핑 빌드"""
    global _krx_name_to_ticker, _krx_ticker_to_name
    if _krx_name_to_ticker is not None:
        return
    if pykrx_stock is None:
        _krx_name_to_ticker = {}
        _krx_ticker_to_name = {}
        return

    _krx_name_to_ticker = {}
    _krx_ticker_to_name = {}
    today = datetime.now().strftime("%Y%m%d")

    for market in ("KOSPI", "KOSDAQ"):
        try:
            tickers = pykrx_stock.get_market_ticker_list(today, market=market)
            for t in tickers:
                name = pykrx_stock.get_market_ticker_name(t)
                if name:
                    _krx_name_to_ticker[name] = t
                    _krx_ticker_to_name[t] = name
        except Exception as e:
            get_logger("pykrx").warning(f"{market} 종목 목록 조회 실패: {e}")


def validate_krx_tickers(proposals: list[dict]) -> dict:
    """KRX 종목의 티커↔이름 교차 검증 및 교정

    Returns:
        {"corrected": 교정 건수, "invalid": 검증 불가 건수, "details": [...]}
    """
    if pykrx_stock is None:
        return {"corrected": 0, "invalid": 0, "details": []}

    _build_krx_lookup()

    corrected = 0
    invalid = 0
    details = []

    for p in proposals:
        ticker = (p.get("ticker") or "").strip().upper()
        market = (p.get("market") or "").strip().upper()
        asset_name = (p.get("asset_name") or "").strip()

        # KRX 종목만 대상
        if not _is_korean_market(market) or not ticker.isdigit():
            continue

        # 1) 티커로 실제 종목명 조회
        actual_name = _krx_ticker_to_name.get(ticker)

        if actual_name and actual_name == asset_name:
            continue  # 정상

        # 2) 종목명으로 올바른 티커 역조회
        correct_ticker = _krx_name_to_ticker.get(asset_name)

        if correct_ticker and correct_ticker != ticker:
            # 교정 가능: 이름 기반으로 올바른 티커 발견
            old_ticker = ticker
            p["ticker"] = correct_ticker
            corrected += 1
            detail = f"{asset_name}: {old_ticker} → {correct_ticker}"
            if actual_name:
                detail += f" (기존 티커는 '{actual_name}')"
            details.append(detail)
        elif actual_name and actual_name != asset_name:
            # 티커는 유효하지만 이름이 다름 → 이름을 교정
            old_name = asset_name
            p["asset_name"] = actual_name
            corrected += 1
            details.append(f"티커 {ticker}: 이름 '{old_name}' → '{actual_name}'")
        elif not actual_name and not correct_ticker:
            # 티커도 이름도 KRX에서 못 찾음
            invalid += 1
            details.append(f"{asset_name} ({ticker}): KRX 미등록")

    return {"corrected": corrected, "invalid": invalid, "details": details}


# ── 기간별 수익률 계산 ────────────────────────────────

def _calc_period_returns(history: "list[tuple[str, float]]") -> dict:
    """일별 종가 리스트에서 1m/3m/6m/1y 수익률 계산

    Args:
        history: [(date_str, close), ...] 오래된 순 정렬

    Returns:
        {"return_1m_pct": float|None, "return_3m_pct": ..., "return_6m_pct": ..., "return_1y_pct": ...}
    """
    if len(history) < 2:
        return {"return_1m_pct": None, "return_3m_pct": None,
                "return_6m_pct": None, "return_1y_pct": None}

    price_now = history[-1][1]
    # 거래일 기준 근사치: 1m≈22일, 3m≈66일, 6m≈132일, 1y≈전체
    periods = {
        "return_1m_pct": 22,
        "return_3m_pct": 66,
        "return_6m_pct": 132,
        "return_1y_pct": len(history) - 1,
    }

    result = {}
    for key, offset in periods.items():
        idx = max(0, len(history) - 1 - offset)
        price_past = history[idx][1]
        if price_past > 0:
            result[key] = round((price_now - price_past) / price_past * 100, 2)
        else:
            result[key] = None

    return result


def _momentum_tag_from_returns(returns: dict) -> str:
    """기간별 수익률에서 모멘텀 태그 결정"""
    r1m = returns.get("return_1m_pct")
    r3m = returns.get("return_3m_pct")

    if r1m is None:
        return "unknown"

    if r1m >= 20 or (r3m is not None and r3m >= 40):
        return "already_run"
    elif r1m <= -10:
        return "undervalued"
    else:
        return "fair_priced"


# ── 종목 데이터 조회 ─────────────────────────────────

def fetch_stock_data(ticker: str, market: str) -> dict | None:
    """단일 종목의 주가/재무 데이터 조회 (한국 주식은 pykrx 크로스체크)

    Returns:
        dict with keys: ticker, price, change_pct, high_52w, low_52w,
        volume_avg, market_cap, per, pbr, eps, dividend_yield, currency,
        + price_source
        실패 시 None
    """
    is_krx = _is_korean_market(market)
    result = None

    # 1차: yfinance 시도
    if yf is not None:
        result = _fetch_stock_data_yfinance(ticker, market)

    # 2차: 한국 주식이면 pykrx 크로스체크/폴백
    if is_krx:
        pykrx_data = _pykrx_fetch_price(ticker)
        if pykrx_data:
            if result is None:
                # yfinance 실패 → pykrx 폴백
                get_logger("주가").info(f"{ticker} yfinance 실패 → pykrx 폴백")
                result = {
                    "ticker": ticker,
                    "yf_ticker": _normalize_ticker(ticker, market),
                    "price": pykrx_data["price"],
                    "change_pct": None,
                    "high_52w": None, "low_52w": None,
                    "volume_avg": None, "market_cap": None,
                    "per": pykrx_data.get("per"),
                    "pbr": pykrx_data.get("pbr"),
                    "eps": None,
                    "dividend_yield": None,
                    "currency": "KRW",
                    "sector": "", "industry": "",
                    "short_name": ticker,
                    "price_source": "pykrx",
                }
            elif result["price"] and pykrx_data["price"]:
                # 크로스체크: 3% 이상 괴리 시 pykrx 가격 우선
                diff_pct = abs(result["price"] - pykrx_data["price"]) / pykrx_data["price"] * 100
                if diff_pct > 3:
                    get_logger("주가").warning(f"{ticker} 가격 괴리 {diff_pct:.1f}%: "
                          f"yfinance={result['price']:,.0f} vs pykrx={pykrx_data['price']:,.0f} → pykrx 채택")
                    result["price"] = pykrx_data["price"]
                    result["price_source"] = "pykrx_crosscheck"
                # PER/PBR 보완 (yfinance에서 누락된 경우)
                if not result.get("per") and pykrx_data.get("per"):
                    result["per"] = pykrx_data["per"]
                if not result.get("pbr") and pykrx_data.get("pbr"):
                    result["pbr"] = pykrx_data["pbr"]

    return result


def _fetch_stock_data_yfinance(ticker: str, market: str) -> dict | None:
    """yfinance로 단일 종목 데이터 조회 (내부 함수)"""
    yf_ticker = _normalize_ticker(ticker, market)
    try:
        stock = yf.Ticker(yf_ticker)
        info = stock.info

        if not info or info.get("regularMarketPrice") is None:
            get_logger("주가").warning(f"{yf_ticker} 데이터 없음")
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
            "dividend_yield": info.get("dividendRate"),
            "currency": info.get("currency", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "short_name": info.get("shortName", ""),
            "price_source": "yfinance",
        }

    except Exception as e:
        get_logger("주가").warning(f"{yf_ticker} 조회 실패: {e}")
        return None


# ── 모멘텀 체크 (기간별 수익률 포함) ──────────────────

def fetch_momentum_check(ticker: str, market: str) -> dict | None:
    """종목의 기간별 수익률 조회 — 1m/3m/6m/1y + 모멘텀 태깅

    한국 주식: pykrx 우선, 실패 시 yfinance 폴백
    해외 주식: yfinance 사용

    Returns:
        {"ticker", "current_price", "momentum_tag",
         "return_1m_pct", "return_3m_pct", "return_6m_pct", "return_1y_pct"}
        실패 시 None
    """
    is_krx = _is_korean_market(market)
    history = []

    # 한국 주식: pykrx 우선
    if is_krx:
        history = _pykrx_fetch_history(ticker, days=400)
        if history:
            returns = _calc_period_returns(history)
            return {
                "ticker": ticker,
                "current_price": round(history[-1][1], 2),
                "momentum_tag": _momentum_tag_from_returns(returns),
                "price_source": "pykrx",
                **returns,
            }

    # 해외 주식 또는 pykrx 실패 → yfinance
    if yf is None:
        return None

    yf_ticker = _normalize_ticker(ticker, market)
    try:
        stock = yf.Ticker(yf_ticker)
        hist = stock.history(period="1y")
        if hist.empty or len(hist) < 2:
            return None

        # yfinance DataFrame → [(date_str, close)] 리스트 변환
        history = [
            (idx.strftime("%Y-%m-%d"), float(row["Close"]))
            for idx, row in hist.iterrows()
            if float(row["Close"]) > 0
        ]
        if len(history) < 2:
            return None

        returns = _calc_period_returns(history)
        return {
            "ticker": ticker,
            "current_price": round(history[-1][1], 2),
            "momentum_tag": _momentum_tag_from_returns(returns),
            "price_source": "yfinance_close",
            **returns,
        }
    except Exception:
        return None


def fetch_momentum_batch(stocks: list[dict]) -> dict[str, dict]:
    """복수 종목 기간별 수익률 병렬 조회

    Args:
        stocks: [{"ticker": "NVDA", "market": "NASDAQ"}, ...]

    Returns:
        {ticker: {"current_price", "momentum_tag", "return_1m_pct", ...}}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    unique_stocks = []
    seen = set()
    for stock in stocks:
        ticker = stock.get("ticker", "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        unique_stocks.append((ticker, stock.get("market", "")))

    if not unique_stocks:
        return {}

    results = {}
    with ThreadPoolExecutor(max_workers=min(len(unique_stocks), 8)) as pool:
        futures = {
            pool.submit(fetch_momentum_check, ticker, market): ticker
            for ticker, market in unique_stocks
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                data = future.result()
                if data:
                    results[ticker] = data
            except Exception:
                pass

    return results


def fetch_multiple_stocks(stocks: list[dict]) -> dict[str, dict]:
    """복수 종목 병렬 조회 (ThreadPoolExecutor)

    Args:
        stocks: [{"ticker": "NVDA", "market": "NASDAQ"}, ...] 형태 리스트

    Returns:
        {ticker: stock_data_dict} 매핑. 조회 실패 종목은 제외.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 중복 제거
    unique_stocks = []
    seen = set()
    for stock in stocks:
        ticker = stock.get("ticker", "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        unique_stocks.append((ticker, stock.get("market", "")))

    if not unique_stocks:
        return {}

    results = {}
    log = get_logger("주가")
    log.info(f"{len(unique_stocks)}종목 병렬 조회 시작...")

    with ThreadPoolExecutor(max_workers=min(len(unique_stocks), 8)) as pool:
        futures = {
            pool.submit(fetch_stock_data, ticker, market): ticker
            for ticker, market in unique_stocks
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                data = future.result()
                if data:
                    results[ticker] = data
                    price_str = f"{data.get('currency', '')}{data['price']:,.2f}" if data.get('price') else "N/A"
                    log.info(f"{ticker} → {price_str}")
            except Exception as e:
                log.warning(f"{ticker} 조회 오류: {e}")

    return results


def format_stock_data_text(data: dict) -> str:
    """주가 데이터를 프롬프트 삽입용 텍스트로 포맷팅"""
    if not data:
        return ""

    currency = data.get("currency", "")
    currency_symbols = {"KRW": "₩", "USD": "$", "JPY": "¥", "EUR": "€",
                        "GBP": "£", "CNY": "¥", "HKD": "HK$", "TWD": "NT$"}
    c = currency_symbols.get(currency, f"{currency} " if currency else "")

    lines = [f"### {data.get('short_name', data['ticker'])} ({data['ticker']})"]

    # 현재가 + 등락률
    price = data.get("price")
    if price:
        change = data.get("change_pct")
        change_str = f" (전일 대비: {'+' if change > 0 else ''}{change}%)" if change is not None else ""
        lines.append(f"- 현재가: {c}{price:,.2f}{change_str}")

    # 기간별 수익률
    returns = []
    for key, label in [("return_1m_pct", "1개월"), ("return_3m_pct", "3개월"),
                       ("return_6m_pct", "6개월"), ("return_1y_pct", "1년")]:
        val = data.get(key)
        if val is not None:
            sign = "+" if val > 0 else ""
            returns.append(f"{label} {sign}{val:.1f}%")
    if returns:
        lines.append(f"- 기간 수익률: {' / '.join(returns)}")

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

    # 배당 (dividendRate / price로 직접 계산 — dividendYield는 시장별 단위 불일치)
    div_rate = data.get("dividend_yield")  # dividendRate 저장값
    price = data.get("price")
    if div_rate and div_rate > 0 and price and price > 0:
        div_pct = div_rate / price * 100
        lines.append(f"- 배당수익률: {div_pct:.2f}%")

    # 섹터/업종
    sector = data.get("sector")
    industry = data.get("industry")
    if sector or industry:
        lines.append(f"- 업종: {sector or ''} / {industry or ''}")

    return "\n".join(lines)
