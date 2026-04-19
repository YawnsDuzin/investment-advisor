"""KRX 확장 데이터 수집 모듈 — 투자자 수급, 공매도, 국채 금리

pykrx를 통해 투자 의사결정에 필요한 추가 데이터를 수집한다.
stock_data.py의 _check_pykrx()/_safe_pykrx_call() 패턴을 공유하여
로그인 실패 시 자동 비활성화된다.

Phase 2: 투자자별 수급, 공매도, 국채 금리
Phase 3: 시가총액/외인보유율, 지수 편입, ETF 자금흐름
"""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from shared.logger import get_logger
from analyzer.stock_data import _check_pykrx, _safe_pykrx_call, _is_korean_market

try:
    from pykrx import stock as pykrx_stock
except ImportError:
    pykrx_stock = None

try:
    from pykrx import bond as pykrx_bond
except ImportError:
    pykrx_bond = None


# ── Phase 2-1: 투자자별 수급 데이터 ──────────────────

def fetch_investor_trading(ticker: str, days: int = 20) -> dict | None:
    """투자자별 순매수 동향 조회 (최근 N거래일)

    Args:
        ticker: KRX 종목코드 (예: "005930")
        days: 조회 기간 (거래일 기준)

    Returns:
        {"foreign_net_buy_5d": int, "foreign_net_buy_20d": int,
         "inst_net_buy_5d": int, "inst_net_buy_20d": int,
         "foreign_consecutive_days": int,  # 양수=연속순매수, 음수=연속순매도
         "summary": "외국인 5일 연속 순매수 (+1,200억원)"}
    """
    if not _check_pykrx():
        return None

    raw_ticker = ticker.strip().upper()
    if not raw_ticker.isdigit():
        return None  # KRX 종목만 대상

    today = datetime.now()
    start = (today - timedelta(days=days + 10)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    df = _safe_pykrx_call(
        pykrx_stock.get_market_trading_value_by_date, start, end, raw_ticker
    )
    if df is None or df.empty:
        return None

    try:
        # 컬럼명: 기관합계, 기타법인, 개인, 외국인합계 등
        foreign_col = None
        inst_col = None
        for col in df.columns:
            if "외국인" in col:
                foreign_col = col
            elif "기관" in col:
                inst_col = col

        if foreign_col is None:
            return None

        # 최근 5일, 20일 순매수 합계 (단위: 원)
        foreign_5d = int(df[foreign_col].tail(5).sum()) if len(df) >= 5 else int(df[foreign_col].sum())
        foreign_20d = int(df[foreign_col].tail(20).sum())
        inst_5d = int(df[inst_col].tail(5).sum()) if inst_col and len(df) >= 5 else 0
        inst_20d = int(df[inst_col].tail(20).sum()) if inst_col else 0

        # 외국인 연속 순매수/순매도 일수
        consecutive = 0
        for val in reversed(df[foreign_col].values):
            if consecutive == 0:
                consecutive = 1 if val > 0 else -1
            elif (consecutive > 0 and val > 0) or (consecutive < 0 and val < 0):
                consecutive += 1 if consecutive > 0 else -1
            else:
                break

        # 요약 텍스트 생성
        direction = "순매수" if consecutive > 0 else "순매도"
        amount = foreign_5d
        amount_str = f"+{amount / 1e8:,.0f}억" if amount >= 0 else f"{amount / 1e8:,.0f}억"
        summary = f"외국인 {abs(consecutive)}일 연속 {direction} ({amount_str}원/5일)"

        return {
            "foreign_net_buy_5d": foreign_5d,
            "foreign_net_buy_20d": foreign_20d,
            "inst_net_buy_5d": inst_5d,
            "inst_net_buy_20d": inst_20d,
            "foreign_consecutive_days": consecutive,
            "summary": summary,
        }
    except Exception as e:
        get_logger("KRX수급").warning(f"{raw_ticker} 수급 데이터 파싱 실패: {e}")
        return None


def fetch_investor_trading_batch(stocks: list[dict]) -> dict[str, dict]:
    """복수 종목 투자자 수급 병렬 조회

    Args:
        stocks: [{"ticker": "005930", "market": "KRX"}, ...]

    Returns:
        {ticker: investor_trading_dict}
    """
    if not _check_pykrx():
        return {}

    # KRX 종목만 필터
    krx_stocks = [
        s["ticker"].strip().upper()
        for s in stocks
        if _is_korean_market(s.get("market", "")) and s.get("ticker", "").strip().upper().isdigit()
    ]
    krx_stocks = list(set(krx_stocks))  # 중복 제거

    if not krx_stocks:
        return {}

    log = get_logger("KRX수급")
    log.info(f"{len(krx_stocks)}종목 투자자 수급 조회 시작...")
    results = {}

    with ThreadPoolExecutor(max_workers=min(len(krx_stocks), 4)) as pool:
        futures = {
            pool.submit(fetch_investor_trading, ticker): ticker
            for ticker in krx_stocks
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                data = future.result()
                if data:
                    results[ticker] = data
            except Exception:
                pass

    log.info(f"투자자 수급 조회 완료 — {len(results)}/{len(krx_stocks)}종목")
    return results


# ── Phase 2-2: 공매도 데이터 ──────────────────────────

def fetch_short_selling(ticker: str, days: int = 20) -> dict | None:
    """공매도 잔고 및 거래 비중 조회

    Returns:
        {"short_balance_ratio_pct": float, "short_volume_ratio_pct": float,
         "short_balance_change_5d_pct": float,
         "squeeze_risk": "high"|"medium"|"low",
         "summary": "공매도 잔고 3.2% (5일간 -0.5%p 감소)"}
    """
    if not _check_pykrx():
        return None

    raw_ticker = ticker.strip().upper()
    if not raw_ticker.isdigit():
        return None

    today = datetime.now()
    start = (today - timedelta(days=days + 10)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    # 공매도 잔고 조회
    balance_df = _safe_pykrx_call(
        pykrx_stock.get_shorting_balance_by_date, start, end, raw_ticker
    )

    if balance_df is None or balance_df.empty:
        return None

    try:
        # 컬럼: 공매도잔고, 상장주식수, 공매도잔고비중(%) 등
        ratio_col = None
        balance_col = None
        for col in balance_df.columns:
            col_lower = str(col).lower()
            if "비중" in col_lower or "비율" in col_lower:
                ratio_col = col
            elif "잔고" in col_lower and "비" not in col_lower:
                balance_col = col

        if ratio_col is None and balance_col is None:
            return None

        # 최신 잔고 비중
        latest_ratio = float(balance_df[ratio_col].iloc[-1]) if ratio_col else 0
        # 5일 전 대비 변화
        ratio_5d_ago = float(balance_df[ratio_col].iloc[-6]) if ratio_col and len(balance_df) >= 6 else latest_ratio
        change_5d = latest_ratio - ratio_5d_ago

        # 공매도 거래 비중 (당일)
        volume_df = _safe_pykrx_call(
            pykrx_stock.get_shorting_volume_by_date, start, end, raw_ticker
        )
        short_vol_ratio = 0.0
        if volume_df is not None and not volume_df.empty:
            for col in volume_df.columns:
                if "비중" in str(col):
                    short_vol_ratio = float(volume_df[col].iloc[-1])
                    break

        # 숏스퀴즈 위험도 판정
        squeeze_risk = "low"
        if latest_ratio >= 10:
            squeeze_risk = "high"
        elif latest_ratio >= 5:
            squeeze_risk = "medium"
        # 잔고 감소 추세면 위험도 상향
        if change_5d < -0.5 and squeeze_risk == "medium":
            squeeze_risk = "high"

        change_dir = "감소" if change_5d < 0 else "증가"
        summary = f"공매도 잔고 {latest_ratio:.1f}% (5일간 {change_5d:+.1f}%p {change_dir})"

        return {
            "short_balance_ratio_pct": round(latest_ratio, 2),
            "short_volume_ratio_pct": round(short_vol_ratio, 2),
            "short_balance_change_5d_pct": round(change_5d, 2),
            "squeeze_risk": squeeze_risk,
            "summary": summary,
        }
    except Exception as e:
        get_logger("KRX공매도").warning(f"{raw_ticker} 공매도 데이터 파싱 실패: {e}")
        return None


def fetch_short_selling_batch(stocks: list[dict]) -> dict[str, dict]:
    """복수 종목 공매도 병렬 조회"""
    if not _check_pykrx():
        return {}

    krx_stocks = list(set(
        s["ticker"].strip().upper()
        for s in stocks
        if _is_korean_market(s.get("market", "")) and s.get("ticker", "").strip().upper().isdigit()
    ))
    if not krx_stocks:
        return {}

    log = get_logger("KRX공매도")
    log.info(f"{len(krx_stocks)}종목 공매도 조회 시작...")
    results = {}

    with ThreadPoolExecutor(max_workers=min(len(krx_stocks), 4)) as pool:
        futures = {
            pool.submit(fetch_short_selling, ticker): ticker
            for ticker in krx_stocks
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                data = future.result()
                if data:
                    results[ticker] = data
            except Exception:
                pass

    log.info(f"공매도 조회 완료 — {len(results)}/{len(krx_stocks)}종목")
    return results


# ── Phase 2-3: 국채 수익률 ────────────────────────────

def fetch_korea_bond_yields() -> dict | None:
    """한국 국채 수익률 + 장단기 스프레드 조회

    Returns:
        {"kr_1y": float, "kr_2y": float, "kr_3y": float, "kr_5y": float,
         "kr_10y": float, "kr_30y": float,
         "corp_aa": float,  # 회사채 AA-
         "cd_91d": float,   # CD 91일
         "spread_10y_2y": float,
         "yield_curve_status": "normal"|"flat"|"inverted",
         "summary": "국고10Y 3.50%, 스프레드 0.25%p (정상)"}
    """
    if pykrx_bond is None:
        return None

    today = datetime.now().strftime("%Y%m%d")
    log = get_logger("KRX금리")

    # 최근 거래일 수익률 조회 (주말/공휴일 대비 5일 범위 시도)
    yields_data = None
    for offset in range(5):
        check_date = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            df = pykrx_bond.get_otc_treasury_yields(check_date)
            if df is not None and not df.empty:
                yields_data = df
                break
        except Exception as e:
            if "로그인" in str(e) or "Expecting value" in str(e):
                log.warning(f"국채 금리 조회 실패 (인증 문제): {e}")
                return None
            continue

    if yields_data is None:
        log.warning("국채 금리 데이터 조회 실패 (최근 5일 내 데이터 없음)")
        return None

    try:
        # 행 인덱스 또는 컬럼에서 금리 추출
        result = {}

        # A-5: pykrx.bond 반환 DataFrame 구조가 버전에 따라 다름 —
        # 인덱스/컬럼 어느 쪽에 수익률이 있든 숫자를 찾아 매핑한다.
        import re as _re

        def _find_numeric_value(row) -> float | None:
            """row(Series)에서 첫 수치형 값을 반환. dtype 무관."""
            for v in row:
                try:
                    if v is None:
                        continue
                    fv = float(v)
                    # 0이 아닌 합리적 금리 범위 (0.01 ~ 20%)
                    if 0 < abs(fv) < 50:
                        return fv
                except (TypeError, ValueError):
                    continue
            return None

        def _match_bond_key(label: str) -> str | None:
            """라벨에서 금리 키 추출 — 다양한 표기법 수용.
            '국고채 3년', '국고채권(3년)', '국고3Y', 'KR3Y' 등 모두 매칭.
            """
            s = label.replace(" ", "").replace("-", "").upper()
            # 국고/국채 계열
            if "국고" in label or "국채" in label or s.startswith("KR"):
                m = _re.search(r"(\d+)\s*년|(\d+)\s*Y", label, _re.IGNORECASE) \
                    or _re.search(r"(\d+)Y", s)
                if m:
                    year = int(next(g for g in m.groups() if g))
                    key_map = {1: "kr_1y", 2: "kr_2y", 3: "kr_3y", 5: "kr_5y",
                               10: "kr_10y", 20: "kr_20y", 30: "kr_30y"}
                    return key_map.get(year)
            # 회사채 AA-
            if "회사채" in label and ("AA" in label.upper() or "AA-" in label.upper()):
                return "corp_aa"
            # CD 91일
            if ("CD" in label.upper() and "91" in label) or "91일물" in label:
                return "cd_91d"
            return None

        # 인덱스 라벨 기반 1차 파싱
        for idx in yields_data.index:
            idx_str = str(idx)
            key = _match_bond_key(idx_str)
            if not key:
                continue
            val = _find_numeric_value(yields_data.loc[idx])
            if val is not None and result.get(key) is None:
                result[key] = val

        # 2차: 컬럼 기반 파싱 (가로형 DataFrame 대비)
        if not result:
            for col in yields_data.columns:
                col_str = str(col)
                key = _match_bond_key(col_str)
                if not key:
                    continue
                # 컬럼 전체에서 첫 유효 숫자 선택
                val = _find_numeric_value(yields_data[col])
                if val is not None and result.get(key) is None:
                    result[key] = val

        if not result.get("kr_3y"):
            # A-5: 원본 구조를 detail에 포함해 추후 진단 가능하도록
            try:
                cols = list(yields_data.columns)
                idx_sample = [str(i) for i in list(yields_data.index)[:15]]
                preview = yields_data.head(5).to_string()[:1500]
                detail = (
                    f"columns={cols}\n"
                    f"index_sample={idx_sample}\n"
                    f"--- preview ---\n{preview}"
                )
            except Exception as _e:
                detail = f"raw preview 생성 실패: {_e}"
            log.warning(
                "국채 3년 금리 누락 — 데이터 형식 불일치 (pykrx 버전 이슈 가능)",
                extra={"detail": detail, "stage": "KRX금리"},
            )
            # 부분 성공이라도 반환 (타 기간물은 있을 수 있음)
            if not any(k.startswith("kr_") for k in result):
                return None

        # 장단기 스프레드 계산
        kr_10y = result.get("kr_10y", result.get("kr_5y", 0))
        kr_2y = result.get("kr_2y", result.get("kr_3y", 0))
        spread = round(kr_10y - kr_2y, 3) if kr_10y and kr_2y else None
        result["spread_10y_2y"] = spread

        # 수익률 곡선 상태 판정
        if spread is not None:
            if spread > 0.3:
                result["yield_curve_status"] = "normal"
            elif spread > -0.1:
                result["yield_curve_status"] = "flat"
            else:
                result["yield_curve_status"] = "inverted"
        else:
            result["yield_curve_status"] = "unknown"

        # 요약
        kr_10y_str = f"{kr_10y:.2f}%" if kr_10y else "N/A"
        spread_str = f"{spread:+.2f}%p" if spread is not None else "N/A"
        status_kr = {"normal": "정상", "flat": "평탄", "inverted": "역전", "unknown": "불명"}
        result["summary"] = (
            f"국고10Y {kr_10y_str}, 스프레드(10Y-2Y) {spread_str} "
            f"({status_kr.get(result['yield_curve_status'], '')})"
        )

        log.info(f"국채 금리 조회 완료 — {result['summary']}")
        return result
    except Exception as e:
        log.warning(f"국채 금리 파싱 실패: {e}")
        return None


# ── Phase 3-1: 시가총액 + 외인 보유비율 ──────────────

def fetch_market_cap_info(ticker: str) -> dict | None:
    """시총, 외인보유비율, 대/중/소형주 분류

    Returns:
        {"market_cap": int, "shares_outstanding": int,
         "foreign_ownership_pct": float,
         "size_category": "large"|"mid"|"small"}
    """
    if not _check_pykrx():
        return None

    raw_ticker = ticker.strip().upper()
    if not raw_ticker.isdigit():
        return None

    today = datetime.now()
    date_str = today.strftime("%Y%m%d")

    # 시가총액 조회
    cap_df = _safe_pykrx_call(pykrx_stock.get_market_cap, date_str, date_str, raw_ticker)
    if cap_df is None or cap_df.empty:
        # 최근 거래일 재시도
        for offset in range(1, 5):
            date_str = (today - timedelta(days=offset)).strftime("%Y%m%d")
            cap_df = _safe_pykrx_call(pykrx_stock.get_market_cap, date_str, date_str, raw_ticker)
            if cap_df is not None and not cap_df.empty:
                break

    if cap_df is None or cap_df.empty:
        return None

    try:
        last = cap_df.iloc[-1]
        market_cap = int(last.get("시가총액", 0))
        shares = int(last.get("상장주식수", 0))

        # 외인 보유비율
        foreign_pct = None
        foreign_df = _safe_pykrx_call(
            pykrx_stock.get_exhaustion_rates_of_foreign_investment, date_str, date_str, raw_ticker
        )
        if foreign_df is not None and not foreign_df.empty:
            for col in foreign_df.columns:
                if "지분율" in str(col) or "보유비중" in str(col):
                    foreign_pct = float(foreign_df[col].iloc[-1])
                    break

        # 대/중/소형주 분류 (시총 기준)
        if market_cap >= 10_000_000_000_000:  # 10조 이상
            size = "large"
        elif market_cap >= 1_000_000_000_000:  # 1조 이상
            size = "mid"
        else:
            size = "small"

        return {
            "market_cap": market_cap,
            "shares_outstanding": shares,
            "foreign_ownership_pct": round(foreign_pct, 2) if foreign_pct else None,
            "size_category": size,
        }
    except Exception as e:
        get_logger("KRX시총").warning(f"{raw_ticker} 시총 조회 실패: {e}")
        return None


# ── Phase 3-2: 지수 편입 여부 ─────────────────────────

# 주요 지수 코드 (pykrx 기준)
_MAJOR_INDICES = {
    "1028": "KOSPI200",
    "1034": "KRX300",
}
_index_cache: dict[str, set[str]] | None = None


def _build_index_cache() -> dict[str, set[str]]:
    """주요 지수 구성종목 캐시 빌드 (세션당 1회)"""
    global _index_cache
    if _index_cache is not None:
        return _index_cache

    _index_cache = {}
    if not _check_pykrx():
        return _index_cache

    log = get_logger("KRX지수")
    today = datetime.now().strftime("%Y%m%d")

    for idx_code, idx_name in _MAJOR_INDICES.items():
        tickers = _safe_pykrx_call(
            pykrx_stock.get_index_portfolio_deposit_file, today, idx_code
        )
        if tickers is not None:
            _index_cache[idx_name] = set(tickers)
            log.info(f"{idx_name} 구성종목 {len(tickers)}건 로드")
        else:
            _index_cache[idx_name] = set()

    return _index_cache


def check_index_membership(ticker: str) -> list[str]:
    """종목이 편입된 주요 지수 목록 반환

    Returns:
        ["KOSPI200", "KRX300"] 등. 편입 안 되면 빈 리스트.
    """
    cache = _build_index_cache()
    raw_ticker = ticker.strip().upper()
    return [
        idx_name for idx_name, members in cache.items()
        if raw_ticker in members
    ]


# ── Phase 3-3: 테마 ETF 자금 흐름 ─────────────────────

# 테마 키워드 → ETF 티커 매핑 (주요 테마만)
_THEME_ETF_MAP = {
    "2차전지": ["305720", "364690"],     # TIGER 2차전지테마, KODEX 2차전지산업
    "반도체": ["091160", "091170"],       # KODEX 반도체, TIGER 반도체
    "AI": ["456250"],                     # TIGER AI반도체핵심공정
    "바이오": ["244580", "227540"],       # KODEX 바이오, TIGER 바이오TOP10
    "자동차": ["091180"],                 # TIGER 자동차
    "에너지": ["117460"],                 # KODEX 에너지화학
    "금융": ["091170"],                   # 금융 관련 ETF
    "방산": ["464520"],                   # TIGER 방산
}


def fetch_theme_etf_flows(theme_keywords: list[str], days: int = 5) -> dict[str, dict]:
    """테마 관련 ETF의 자금 유입/유출 분석

    Args:
        theme_keywords: 테마 키워드 목록 (예: ["2차전지", "반도체"])
        days: 조회 기간

    Returns:
        {"2차전지": {"etf_name": "TIGER 2차전지테마", "net_flow_5d": +500억, ...}}
    """
    if not _check_pykrx():
        return {}

    log = get_logger("KRXETF")
    results = {}

    today = datetime.now()
    start = (today - timedelta(days=days + 5)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    for keyword in theme_keywords:
        matched_etfs = _THEME_ETF_MAP.get(keyword)
        if not matched_etfs:
            continue

        for etf_ticker in matched_etfs:
            try:
                # ETF OHLCV로 거래대금 추이 확인
                etf_df = _safe_pykrx_call(
                    pykrx_stock.get_market_ohlcv, start, end, etf_ticker
                )
                if etf_df is None or etf_df.empty:
                    continue

                # ETF 이름
                etf_name = _safe_pykrx_call(
                    pykrx_stock.get_market_ticker_name, etf_ticker
                ) or etf_ticker

                # 최근 N일 거래대금 합계 (순유입 근사치)
                recent = etf_df.tail(days)
                total_volume = int(recent["거래량"].sum()) if "거래량" in recent.columns else 0
                total_value = int(recent["거래대금"].sum()) if "거래대금" in recent.columns else 0

                # 가격 변화
                if len(recent) >= 2:
                    price_change_pct = round(
                        (float(recent["종가"].iloc[-1]) - float(recent["종가"].iloc[0]))
                        / float(recent["종가"].iloc[0]) * 100, 2
                    )
                else:
                    price_change_pct = 0

                results[keyword] = {
                    "etf_ticker": etf_ticker,
                    "etf_name": etf_name,
                    "volume_5d": total_volume,
                    "value_5d": total_value,
                    "price_change_5d_pct": price_change_pct,
                }
                break  # 첫 번째 매칭 ETF만 사용

            except Exception as e:
                log.warning(f"ETF {etf_ticker} 조회 실패: {e}")

    if results:
        log.info(f"테마 ETF 흐름 조회 완료 — {len(results)}개 테마")
    return results


# ── 프롬프트 포맷팅 함수 ──────────────────────────────

def format_investor_data_text(data: dict) -> str:
    """투자자 수급 데이터를 프롬프트 삽입용 텍스트로 포맷팅"""
    if not data:
        return ""

    lines = ["### 투자자별 수급 동향 (최근 20거래일)"]

    f5d = data.get("foreign_net_buy_5d", 0)
    f20d = data.get("foreign_net_buy_20d", 0)
    i5d = data.get("inst_net_buy_5d", 0)
    consec = data.get("foreign_consecutive_days", 0)

    f5d_str = f"{f5d / 1e8:+,.0f}억원" if f5d else "0"
    f20d_str = f"{f20d / 1e8:+,.0f}억원" if f20d else "0"
    i5d_str = f"{i5d / 1e8:+,.0f}억원" if i5d else "0"

    direction = "순매수" if consec > 0 else "순매도"
    lines.append(f"- 외국인: {abs(consec)}일 연속 {direction}, 5일 {f5d_str}, 20일 {f20d_str}")
    lines.append(f"- 기관: 5일 {i5d_str}")

    return "\n".join(lines)


def format_short_selling_text(data: dict) -> str:
    """공매도 데이터를 프롬프트 삽입용 텍스트로 포맷팅"""
    if not data:
        return ""

    lines = ["### 공매도 현황"]
    ratio = data.get("short_balance_ratio_pct", 0)
    change = data.get("short_balance_change_5d_pct", 0)
    risk = data.get("squeeze_risk", "low")
    risk_kr = {"high": "높음", "medium": "보통", "low": "낮음"}.get(risk, risk)

    lines.append(f"- 공매도 잔고비중: {ratio:.1f}% (5일 변화: {change:+.1f}%p)")
    lines.append(f"- 숏스퀴즈 위험도: {risk_kr}")

    return "\n".join(lines)


def format_bond_yields_text(data: dict) -> str:
    """국채 금리 데이터를 프롬프트 삽입용 텍스트로 포맷팅"""
    if not data:
        return ""

    lines = ["## 한국 금리 환경 (실시간 데이터)"]

    rates = []
    for key, label in [("kr_1y", "1년"), ("kr_3y", "3년"), ("kr_5y", "5년"),
                        ("kr_10y", "10년"), ("kr_30y", "30년")]:
        val = data.get(key)
        if val is not None:
            rates.append(f"{label} {val:.2f}%")
    if rates:
        lines.append(f"- 국고채: {', '.join(rates)}")

    spread = data.get("spread_10y_2y")
    status = data.get("yield_curve_status", "unknown")
    status_kr = {"normal": "정상", "flat": "평탄", "inverted": "역전"}.get(status, "")
    if spread is not None:
        lines.append(f"- 장단기 스프레드(10Y-2Y): {spread:+.2f}%p ({status_kr})")

    corp = data.get("corp_aa")
    if corp is not None:
        credit_spread = round(corp - data.get("kr_3y", 0), 2) if data.get("kr_3y") else None
        cs_str = f" (신용스프레드 {credit_spread:+.2f}%p)" if credit_spread else ""
        lines.append(f"- 회사채 AA-: {corp:.2f}%{cs_str}")

    cd = data.get("cd_91d")
    if cd is not None:
        lines.append(f"- CD 91일: {cd:.2f}%")

    return "\n".join(lines)
