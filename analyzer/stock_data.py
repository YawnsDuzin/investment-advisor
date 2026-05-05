"""실시간 주가/재무 데이터 조회 모듈 — yfinance + pykrx(한국 주식 크로스체크)"""
import os
import threading
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


# ── pykrx 사용 가능 여부 캐싱 ──────────────────────
# pykrx 로그인 실패 시 세션 내 모든 후속 호출을 건너뛰어
# 불필요한 로그인 시도 반복을 방지한다.
_pykrx_available = True
_pykrx_lock = threading.Lock()  # ThreadPoolExecutor race condition 방지

# 로그인 실패 감지 패턴 (pykrx 1.2.7 auth.py 에러 메시지)
_PYKRX_LOGIN_FAIL_PATTERNS = ("로그인", "자격 증명", "Expecting value", "login")


def _check_pykrx() -> bool:
    """pykrx 사용 가능 여부 반환 (로그인 실패 시 비활성화)"""
    return pykrx_stock is not None and _pykrx_available


def _disable_pykrx(reason: str) -> None:
    """pykrx를 세션 내 비활성화 (thread-safe)"""
    global _pykrx_available
    with _pykrx_lock:
        if _pykrx_available:
            _pykrx_available = False
            get_logger("pykrx").warning(f"pykrx 비활성화 (세션 내): {reason}")


def _is_login_failure(error: Exception) -> bool:
    """pykrx 로그인 실패 여부 판별"""
    err_msg = str(error).lower()
    return any(p in err_msg for p in _PYKRX_LOGIN_FAIL_PATTERNS)


def _safe_pykrx_call(func, *args, **kwargs):
    """pykrx 함수 안전 호출 래퍼 — 로그인 실패 시 자동 비활성화

    Returns:
        함수 반환값. 실패 시 None.
    """
    if not _check_pykrx():
        return None
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if _is_login_failure(e):
            _disable_pykrx(f"인증 오류: {str(e)[:100]}")
        else:
            get_logger("pykrx").warning(f"{func.__name__} 실패: {e}")
        return None


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
    if not _check_pykrx():
        return None

    raw_ticker = ticker.strip().upper()
    today = datetime.now()
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
        if _is_login_failure(e):
            _disable_pykrx(f"가격 조회 중 인증 오류: {str(e)[:100]}")
        else:
            get_logger("pykrx").warning(f"{raw_ticker} 조회 실패: {e}")
        return None


def _pykrx_fetch_history(ticker: str, days: int = 365) -> "list[tuple[str, float]]":
    """pykrx로 한국 주식 일별 종가 이력 조회

    Returns:
        [(date_str, close_price), ...] 오래된 순 정렬. 실패 시 빈 리스트.
    """
    if not _check_pykrx():
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
    except Exception as e:
        if _is_login_failure(e):
            _disable_pykrx(f"이력 조회 중 인증 오류: {str(e)[:100]}")
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
    if not _check_pykrx():
        _krx_name_to_ticker = {}
        _krx_ticker_to_name = {}
        return

    _krx_name_to_ticker = {}
    _krx_ticker_to_name = {}
    today = datetime.now().strftime("%Y%m%d")

    for market in ("KOSPI", "KOSDAQ"):
        tickers = _safe_pykrx_call(pykrx_stock.get_market_ticker_list, today, market=market)
        if tickers is None:
            continue
        for t in tickers:
            name = _safe_pykrx_call(pykrx_stock.get_market_ticker_name, t)
            if name:
                _krx_name_to_ticker[name] = t
                _krx_ticker_to_name[t] = name


# ── US 화이트리스트 검증 (stock_universe DB 기반) ──

_US_MARKETS = {"NYSE", "NASDAQ", "AMEX"}

# {ticker_upper: asset_name} — 세션 중 1회 빌드. (ticker, market) 페어 분리는 ticker 가 시장 간 unique 가정.
_us_ticker_to_name: dict[str, str] | None = None
_us_lookup_lock = threading.Lock()


def _is_us_market(market: str) -> bool:
    return (market or "").strip().upper() in _US_MARKETS


def _build_us_lookup(db_cfg) -> None:
    """stock_universe 에서 US 시장 종목 일괄 조회 후 (ticker → name) 캐시.

    DB 호출 1회, 이후 in-memory 매칭. KRX 와 달리 외부 API 비의존.
    db_cfg 미지정 시 빈 딕셔너리로 빌드 (= 검증 비활성화).
    """
    global _us_ticker_to_name
    if _us_ticker_to_name is not None:
        return

    with _us_lookup_lock:
        if _us_ticker_to_name is not None:
            return

        if db_cfg is None:
            _us_ticker_to_name = {}
            return

        try:
            from shared.db import get_connection
            conn = get_connection(db_cfg)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT UPPER(ticker), COALESCE(asset_name_en, asset_name)
                           FROM stock_universe
                           WHERE UPPER(market) = ANY(%s) AND listed = TRUE""",
                        (list(_US_MARKETS),),
                    )
                    _us_ticker_to_name = {tk: name for tk, name in cur.fetchall() if tk}
            finally:
                conn.close()
        except Exception as e:
            get_logger("us_validator").warning(f"stock_universe US 조회 실패: {e}")
            _us_ticker_to_name = {}


def _reset_us_lookup() -> None:
    """테스트 격리용 — module-level 캐시 초기화."""
    global _us_ticker_to_name
    with _us_lookup_lock:
        _us_ticker_to_name = None


def validate_us_tickers(proposals: list[dict], db_cfg=None) -> dict:
    """US 종목 (NYSE/NASDAQ/AMEX) 의 티커 화이트리스트 검증.

    KRX 와 달리 영문 이름 매칭이 어려운 경우(약어·분기·티커 변경) 가 많아 자동 교정은
    하지 않고, **stock_universe 에 등록되지 않은 ticker 만 invalid 로 표기**한다.

    Args:
        proposals: in-place 수정 가능한 proposal 리스트
        db_cfg: stock_universe 조회용 DB cfg. None 이면 검증 스킵.

    Returns:
        {"corrected": 0 (US는 자동 교정 없음), "invalid": int, "details": [str, ...]}
    """
    if db_cfg is None:
        return {"corrected": 0, "invalid": 0, "details": []}

    _build_us_lookup(db_cfg)
    if not _us_ticker_to_name:
        # universe 조회 실패 → 검증 비활성화 (KRX 와 같은 정책)
        return {"corrected": 0, "invalid": 0, "details": []}

    invalid = 0
    details: list[str] = []

    for p in proposals:
        ticker = (p.get("ticker") or "").strip().upper()
        market = (p.get("market") or "").strip().upper()
        asset_name = (p.get("asset_name") or "").strip()

        if not _is_us_market(market) or not ticker:
            continue

        if ticker in _us_ticker_to_name:
            continue  # 정상

        invalid += 1
        details.append(f"{asset_name} ({ticker} @ {market}): stock_universe 미등록")

    return {"corrected": 0, "invalid": invalid, "details": details}


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
        + price_source, price_anomaly(선택)
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

    # A-2: 가격 sanity check — 페니스톡·200일MA 대비 대괴리 감지
    if result and result.get("price"):
        anomalies = _detect_price_anomalies(result)
        if anomalies:
            result["price_anomaly"] = anomalies
            currency = result.get("currency", "")
            get_logger("주가").warning(
                f"{ticker} 가격 이상 감지: {', '.join(anomalies)} "
                f"({currency}{result['price']:,.4f}) — 상장폐지·분할·심볼오류 의심"
            )

    return result


# ── 가격 이상 감지 (A-2) ──────────────────────────

def _detect_price_anomalies(data: dict) -> list[str]:
    """주가 이상징후 감지 — 의심 종목 사전 경고용.

    감지 항목:
    - penny_stock: USD/EUR/GBP $1 미만, KRW 100원 미만, JPY/HKD/TWD 10 미만
    - extreme_drawdown_from_52w_high: 52주 고가 대비 -80% 이하 (상장폐지 위기 수준)
    - price_vs_52w_low_anomaly: 52주 저가 아래에서 거래 중 (데이터 이상 가능)
    - market_cap_penny: 시가총액 미화 5천만달러 미만 (micro-cap 리스크)
    - too_cheap_for_market: 시장별 통화 단위 대비 비정상 저가
    """
    flags: list[str] = []
    price = data.get("price")
    if not price or price <= 0:
        return flags

    currency = (data.get("currency") or "").upper()

    # 통화별 penny stock 임계값
    penny_thresholds = {
        "USD": 1.0, "EUR": 1.0, "GBP": 0.5, "CAD": 1.0, "AUD": 1.0,
        "KRW": 100.0, "JPY": 10.0, "HKD": 1.0, "TWD": 10.0, "CNY": 1.0,
    }
    threshold = penny_thresholds.get(currency)
    if threshold is not None and price < threshold:
        flags.append(f"penny_stock(<{threshold}{currency})")

    # 52주 고가 대비 급락률
    high_52w = data.get("high_52w")
    if high_52w and high_52w > 0:
        drawdown_pct = (price - high_52w) / high_52w * 100
        if drawdown_pct <= -80:
            flags.append(f"52w_high_drawdown({drawdown_pct:.0f}%)")

    # 52주 저가 아래 거래 (데이터 이상)
    low_52w = data.get("low_52w")
    if low_52w and low_52w > 0 and price < low_52w * 0.95:
        flags.append("below_52w_low")

    # 시가총액 극소형주
    mcap = data.get("market_cap")
    if mcap and currency == "USD" and mcap < 50_000_000:
        flags.append(f"micro_cap(${mcap/1e6:.0f}M)")

    return flags


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


def _fetch_ohlcv_history_batch(
    stocks: list[tuple[str, str]],
    db_cfg,
    days: int = 400,
) -> "dict[str, list[tuple[str, float]]]":
    """stock_universe_ohlcv에서 (ticker, market) 배치에 대해 최근 N일 종가 이력을 단일 SQL로 조회.

    Args:
        stocks: [(ticker_upper, market), ...] 유니크 목록
        db_cfg: DatabaseConfig
        days: 오늘로부터 조회할 일수 (거래일 아닌 달력일 기준)

    Returns:
        {ticker_upper: [(trade_date_str, close_float), ...] 오래된 순}
        OHLCV 결측 종목은 딕셔너리에서 제외.
    """
    if not stocks:
        return {}

    # 지연 import — stock_data는 DB 없이도 돌아가야 하므로
    try:
        from shared.db.connection import get_connection
    except Exception as e:
        get_logger("모멘텀DB").warning(f"DB 모듈 로드 실패 → OHLCV 조회 불가: {e}")
        return {}

    # (ticker, market) 정리. market=''이면 market 무시 매칭
    pairs = [(tk.strip().upper(), (mk or "").strip()) for tk, mk in stocks]
    placeholders = ",".join(["(%s, %s)"] * len(pairs))
    flat_args: list = []
    for tk, mk in pairs:
        flat_args.extend([tk, mk])
    flat_args.append(days)

    sql = f"""
    WITH targets (ticker, market) AS (
        VALUES {placeholders}
    )
    SELECT UPPER(o.ticker) AS ticker, o.trade_date::text, o.close::float
    FROM stock_universe_ohlcv o
    JOIN targets t
      ON UPPER(o.ticker) = t.ticker
     AND (t.market = '' OR UPPER(o.market) = UPPER(t.market))
    WHERE o.trade_date >= CURRENT_DATE - (%s::int)
    ORDER BY ticker, o.trade_date
    """

    history_map: dict[str, list[tuple[str, float]]] = {}
    conn = get_connection(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, flat_args)
            rows = cur.fetchall()
    except Exception as e:
        get_logger("모멘텀DB").warning(f"OHLCV 배치 조회 실패: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return {}
    finally:
        conn.close()

    for ticker, date_str, close in rows:
        history_map.setdefault(ticker, []).append((date_str, float(close)))
    return history_map


def fetch_momentum_from_db(
    stocks: list[dict],
    db_cfg,
    *,
    days: int = 400,
) -> dict[str, dict]:
    """OHLCV 이력 테이블 기반 배치 모멘텀 조회 (외부 API 호출 없음).

    Args:
        stocks: [{"ticker": "NVDA", "market": "NASDAQ"}, ...]
        db_cfg: DatabaseConfig
        days: 조회 범위 (기본 400일 — 1y 수익률 여유 확보)

    Returns:
        {ticker: {"current_price", "momentum_tag", "return_1m_pct", ...,
                  "price_source": "ohlcv_db"}}
        OHLCV에 충분한 이력이 없는 종목은 결과에서 제외 (호출자가 live 폴백).
    """
    unique: list[tuple[str, str]] = []
    seen = set()
    for stock in stocks:
        ticker = stock.get("ticker", "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        unique.append((ticker, stock.get("market", "")))

    if not unique:
        return {}

    history_map = _fetch_ohlcv_history_batch(unique, db_cfg, days=days)
    if not history_map:
        return {}

    results: dict[str, dict] = {}
    for ticker, _market in unique:
        history = history_map.get(ticker)
        if not history or len(history) < 2:
            continue
        returns = _calc_period_returns(history)
        results[ticker] = {
            "ticker": ticker,
            "current_price": round(history[-1][1], 2),
            "momentum_tag": _momentum_tag_from_returns(returns),
            "price_source": "ohlcv_db",
            **returns,
        }
    return results


def fetch_momentum_batch(stocks: list[dict], db_cfg=None) -> dict[str, dict]:
    """복수 종목 기간별 수익률 배치 조회.

    소스 우선순위 (`MOMENTUM_SOURCE` 환경변수):
      - "db" (기본): stock_universe_ohlcv 이력 → 결측 종목만 live(yfinance/pykrx) 폴백
      - "live": 기존 동작 (live만)
      - "db_only": DB만, live 폴백 없음 (디버깅용)
    db_cfg가 None이면 자동으로 live 모드.

    Args:
        stocks: [{"ticker": "NVDA", "market": "NASDAQ"}, ...]
        db_cfg: DatabaseConfig (선택). None이면 live 강제.

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

    source = (os.getenv("MOMENTUM_SOURCE") or "db").strip().lower()
    log = get_logger("모멘텀")

    results: dict[str, dict] = {}
    missing: list[tuple[str, str]] = list(unique_stocks)

    # 1차: DB 우선
    if db_cfg is not None and source in ("db", "db_only"):
        try:
            db_results = fetch_momentum_from_db(
                [{"ticker": tk, "market": mk} for tk, mk in unique_stocks],
                db_cfg,
            )
            if db_results:
                results.update(db_results)
                missing = [(tk, mk) for tk, mk in unique_stocks if tk not in db_results]
                log.info(
                    f"[모멘텀] DB 조회 {len(db_results)}/{len(unique_stocks)}종목 성공, "
                    f"결측 {len(missing)}종목은 live 폴백 시도"
                )
        except Exception as e:
            log.warning(f"[모멘텀] DB 조회 전체 실패 → live 폴백: {e}")
            missing = list(unique_stocks)

    # 2차: 결측 종목 live 폴백 (db_only 모드가 아닐 때만)
    if missing and source != "db_only":
        if db_cfg is None and source == "db":
            log.info(f"[모멘텀] db_cfg 미지정 → live 모드로 {len(missing)}종목 조회")
        with ThreadPoolExecutor(max_workers=min(len(missing), 8)) as pool:
            futures = {
                pool.submit(fetch_momentum_check, ticker, market): ticker
                for ticker, market in missing
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


# ── 메모리 캐시 (종목 기초정보) ──────────────────────
_fundamentals_cache: dict[str, tuple[float, dict]] = {}
_FUNDAMENTALS_TTL = 3600  # 1시간


def fetch_fundamentals(ticker: str, market: str = "") -> dict | None:
    """종목 기초정보 온디맨드 조회 (yfinance .info 기반, 1시간 캐싱)

    밸류에이션·수익성·재무건전성·성장성·현금흐름·기술지표를 한 번에 반환.
    """
    import time

    cache_key = _normalize_ticker(ticker, market)

    # 캐시 히트
    cached = _fundamentals_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _FUNDAMENTALS_TTL:
        return cached[1]

    is_krx = _is_korean_market(market)

    # yfinance 조회
    if yf is None:
        return None

    try:
        stock = yf.Ticker(cache_key)
        info = stock.info or {}
    except Exception as e:
        get_logger("기초정보").warning(f"{cache_key} yfinance 조회 실패: {e}")
        return None

    price = info.get("regularMarketPrice") or info.get("currentPrice")
    if not price:
        return None

    prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
    change_pct = None
    if price and prev_close and prev_close > 0:
        change_pct = round((price - prev_close) / prev_close * 100, 2)

    currency = info.get("currency", "")

    def _pct(v):
        """소수(0.15) → 퍼센트(15.0) 변환, None 안전"""
        if v is None:
            return None
        return round(v * 100, 2)

    def _round2(v):
        if v is None:
            return None
        return round(v, 2)

    result = {
        # 기본 정보
        "ticker": ticker,
        "yf_ticker": cache_key,
        "name": info.get("shortName") or info.get("longName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": currency,
        "exchange": info.get("exchange"),
        "market": market,

        # 가격
        "price": price,
        "change_pct": change_pct,
        "high_52w": info.get("fiftyTwoWeekHigh"),
        "low_52w": info.get("fiftyTwoWeekLow"),
        "market_cap": info.get("marketCap"),
        "volume_avg": info.get("averageDailyVolume10Day") or info.get("averageVolume"),

        # 밸류에이션
        "valuation": {
            "trailing_pe": _round2(info.get("trailingPE")),
            "forward_pe": _round2(info.get("forwardPE")),
            "peg_ratio": _round2(info.get("pegRatio")),
            "pb_ratio": _round2(info.get("priceToBook")),
            "ps_ratio": _round2(info.get("priceToSalesTrailing12Months")),
            "ev_ebitda": _round2(info.get("enterpriseToEbitda")),
            "ev_revenue": _round2(info.get("enterpriseToRevenue")),
            "eps_trailing": _round2(info.get("trailingEps")),
            "eps_forward": _round2(info.get("forwardEps")),
        },

        # 수익성
        "profitability": {
            "roe": _pct(info.get("returnOnEquity")),
            "roa": _pct(info.get("returnOnAssets")),
            "gross_margin": _pct(info.get("grossMargins")),
            "operating_margin": _pct(info.get("operatingMargins")),
            "net_margin": _pct(info.get("profitMargins")),
            "ebitda": info.get("ebitda"),
        },

        # 재무건전성
        "health": {
            "debt_to_equity": _round2(info.get("debtToEquity")),
            "current_ratio": _round2(info.get("currentRatio")),
            "quick_ratio": _round2(info.get("quickRatio")),
            "total_debt": info.get("totalDebt"),
            "total_cash": info.get("totalCash"),
        },

        # 성장성
        "growth": {
            "revenue_growth": _pct(info.get("revenueGrowth")),
            "earnings_growth": _pct(info.get("earningsGrowth")),
            "earnings_quarterly_growth": _pct(info.get("earningsQuarterlyGrowth")),
        },

        # 현금흐름
        "cashflow": {
            "operating_cashflow": info.get("operatingCashflow"),
            "free_cashflow": info.get("freeCashflow"),
        },

        # 배당
        "dividend": {
            "dividend_rate": info.get("dividendRate"),
            "dividend_yield": _pct(info.get("dividendYield")),
            "payout_ratio": _pct(info.get("payoutRatio")),
            "ex_dividend_date": info.get("exDividendDate"),
        },

        # 기술 지표
        "technical": {
            "beta": _round2(info.get("beta")),
            "fifty_day_avg": _round2(info.get("fiftyDayAverage")),
            "two_hundred_day_avg": _round2(info.get("twoHundredDayAverage")),
        },

        # 애널리스트
        "analyst": {
            "target_mean": _round2(info.get("targetMeanPrice")),
            "target_low": _round2(info.get("targetLowPrice")),
            "target_high": _round2(info.get("targetHighPrice")),
            "recommendation": info.get("recommendationKey"),
            "num_analysts": info.get("numberOfAnalystOpinions"),
        },
    }

    # pykrx 크로스체크 (한국 주식)
    if is_krx:
        raw_ticker = ticker.replace(".KS", "").replace(".KQ", "").strip().upper()
        pykrx_data = _pykrx_fetch_price(raw_ticker)
        if pykrx_data:
            if pykrx_data["price"] and result["price"]:
                diff_pct = abs(result["price"] - pykrx_data["price"]) / pykrx_data["price"] * 100
                if diff_pct > 3:
                    result["price"] = pykrx_data["price"]
                    result["price_source"] = "pykrx_crosscheck"
            if not result["valuation"]["trailing_pe"] and pykrx_data.get("per"):
                result["valuation"]["trailing_pe"] = pykrx_data["per"]
            if not result["valuation"]["pb_ratio"] and pykrx_data.get("pbr"):
                result["valuation"]["pb_ratio"] = pykrx_data["pbr"]

    # 캐시 저장
    _fundamentals_cache[cache_key] = (time.time(), result)
    return result


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
