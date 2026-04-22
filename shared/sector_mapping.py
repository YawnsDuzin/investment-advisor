"""섹터 분류 정규화 마스터 — KRX 업종(pykrx) ↔ GICS(yfinance) ↔ sector_norm.

`stock_universe.sector_norm`은 KOR/US 양 시장 공통 키로, Top Picks 다양성 제약
(`max_per_sector`)과 스크리너 스펙의 `sector_norm` 필터에 사용된다.

매핑 누락 시 `SECTOR_OTHER`로 fallback + 경고 로그.

**유지보수 가이드**
- 새로운 KRX 업종이 등장하면 `_KRX_TO_NORM`에 추가
- yfinance가 새 GICS sector를 노출하면 `_GICS_TO_NORM`에 추가
- 세분화 키(semiconductors, it_software 등)는 industry 단계 매핑(`_INDUSTRY_OVERRIDES`)으로 보강
- 변경 시 회귀 테스트 (오픈 이슈 §10.1) 추가 필요
"""
from __future__ import annotations

from shared.logger import get_logger


# ── 표준 sector_norm 키 ─────────────────────────────
# 다양성 제약·스크리너 필터에 사용되는 정규화 키. 13개 + other.
SECTOR_SEMICONDUCTORS = "semiconductors"
SECTOR_IT_HARDWARE = "it_hardware"
SECTOR_IT_SOFTWARE = "it_software"
SECTOR_COMMUNICATION = "communication"
SECTOR_FINANCE = "finance"
SECTOR_HEALTHCARE = "healthcare"
SECTOR_CONSUMER_DISCRETIONARY = "consumer_discretionary"
SECTOR_CONSUMER_STAPLES = "consumer_staples"
SECTOR_ENERGY = "energy"
SECTOR_MATERIALS = "materials"
SECTOR_INDUSTRIALS = "industrials"
SECTOR_UTILITIES = "utilities"
SECTOR_REAL_ESTATE = "real_estate"
SECTOR_OTHER = "other"

ALL_SECTOR_NORMS: tuple[str, ...] = (
    SECTOR_SEMICONDUCTORS,
    SECTOR_IT_HARDWARE,
    SECTOR_IT_SOFTWARE,
    SECTOR_COMMUNICATION,
    SECTOR_FINANCE,
    SECTOR_HEALTHCARE,
    SECTOR_CONSUMER_DISCRETIONARY,
    SECTOR_CONSUMER_STAPLES,
    SECTOR_ENERGY,
    SECTOR_MATERIALS,
    SECTOR_INDUSTRIALS,
    SECTOR_UTILITIES,
    SECTOR_REAL_ESTATE,
    SECTOR_OTHER,
)


# ── KRX 업종 매핑 (pykrx KOSPI/KOSDAQ 업종명 기준) ───────────
# pykrx.stock.get_market_ticker_industry / 종목 상세에서 노출되는 한글 업종명.
# 공백·중점·괄호 등은 _normalize_key()로 정규화 후 비교.
_KRX_TO_NORM: dict[str, str] = {
    # KOSPI 업종
    "음식료품": SECTOR_CONSUMER_STAPLES,
    "섬유의복": SECTOR_CONSUMER_DISCRETIONARY,
    "종이목재": SECTOR_MATERIALS,
    "화학": SECTOR_MATERIALS,
    "의약품": SECTOR_HEALTHCARE,
    "비금속광물": SECTOR_MATERIALS,
    "철강금속": SECTOR_MATERIALS,
    "기계": SECTOR_INDUSTRIALS,
    "전기전자": SECTOR_IT_HARDWARE,
    "의료정밀": SECTOR_HEALTHCARE,
    "운수장비": SECTOR_INDUSTRIALS,
    "유통업": SECTOR_CONSUMER_DISCRETIONARY,
    "전기가스업": SECTOR_UTILITIES,
    "건설업": SECTOR_INDUSTRIALS,
    "운수창고업": SECTOR_INDUSTRIALS,
    "운수창고": SECTOR_INDUSTRIALS,
    "통신업": SECTOR_COMMUNICATION,
    "금융업": SECTOR_FINANCE,
    "은행": SECTOR_FINANCE,
    "증권": SECTOR_FINANCE,
    "보험": SECTOR_FINANCE,
    "서비스업": SECTOR_CONSUMER_DISCRETIONARY,
    "제조업": SECTOR_INDUSTRIALS,
    # KOSDAQ 업종
    "음식료담배": SECTOR_CONSUMER_STAPLES,
    "음식료": SECTOR_CONSUMER_STAPLES,
    "섬유의류": SECTOR_CONSUMER_DISCRETIONARY,
    "제약": SECTOR_HEALTHCARE,
    "비금속": SECTOR_MATERIALS,
    "금속": SECTOR_MATERIALS,
    "기계장비": SECTOR_INDUSTRIALS,
    "일반전기전자": SECTOR_IT_HARDWARE,
    "의료정밀기기": SECTOR_HEALTHCARE,
    "운송장비부품": SECTOR_INDUSTRIALS,
    "유통": SECTOR_CONSUMER_DISCRETIONARY,
    "숙박음식": SECTOR_CONSUMER_DISCRETIONARY,
    "운송": SECTOR_INDUSTRIALS,
    "정보기기": SECTOR_IT_HARDWARE,
    "반도체": SECTOR_SEMICONDUCTORS,
    "통신장비": SECTOR_COMMUNICATION,
    "디지털컨텐츠": SECTOR_IT_SOFTWARE,
    "디지털콘텐츠": SECTOR_IT_SOFTWARE,
    "통신서비스": SECTOR_COMMUNICATION,
    "방송서비스": SECTOR_COMMUNICATION,
    "인터넷": SECTOR_IT_SOFTWARE,
    "소프트웨어": SECTOR_IT_SOFTWARE,
    "IT부품": SECTOR_IT_HARDWARE,
    "통신방송서비스": SECTOR_COMMUNICATION,
    "금융": SECTOR_FINANCE,
    "오락문화": SECTOR_CONSUMER_DISCRETIONARY,
    "교육서비스": SECTOR_CONSUMER_DISCRETIONARY,
    # 추가 변형 / 흔한 표기
    "기타금융": SECTOR_FINANCE,
    "기타제조": SECTOR_INDUSTRIALS,
    "건설": SECTOR_INDUSTRIALS,
    "철강": SECTOR_MATERIALS,
    # KRX 표준업종 분류(2026 현재) — 실데이터 기반 보강
    "IT서비스": SECTOR_IT_SOFTWARE,
    "IT 서비스": SECTOR_IT_SOFTWARE,
    "일반서비스": SECTOR_CONSUMER_DISCRETIONARY,
    "운송창고": SECTOR_INDUSTRIALS,
    "운송": SECTOR_INDUSTRIALS,
    "부동산": SECTOR_REAL_ESTATE,
    "통신": SECTOR_COMMUNICATION,
    "전기가스": SECTOR_UTILITIES,
    "전기가스수도": SECTOR_UTILITIES,
    "농업임업및어업": SECTOR_CONSUMER_STAPLES,
    "농림어업": SECTOR_CONSUMER_STAPLES,
    "출판매체복제": SECTOR_COMMUNICATION,
    "미디어엔터테인먼트": SECTOR_COMMUNICATION,
    "방송": SECTOR_COMMUNICATION,
}


# ── GICS 매핑 (yfinance Ticker.info["sector"] 기준) ─────────
_GICS_TO_NORM: dict[str, str] = {
    "energy": SECTOR_ENERGY,
    "materials": SECTOR_MATERIALS,
    "basic materials": SECTOR_MATERIALS,
    "industrials": SECTOR_INDUSTRIALS,
    "consumer discretionary": SECTOR_CONSUMER_DISCRETIONARY,
    "consumer cyclical": SECTOR_CONSUMER_DISCRETIONARY,
    "consumer staples": SECTOR_CONSUMER_STAPLES,
    "consumer defensive": SECTOR_CONSUMER_STAPLES,
    "health care": SECTOR_HEALTHCARE,
    "healthcare": SECTOR_HEALTHCARE,
    "financials": SECTOR_FINANCE,
    "financial services": SECTOR_FINANCE,
    "information technology": SECTOR_IT_HARDWARE,
    "technology": SECTOR_IT_HARDWARE,
    "communication services": SECTOR_COMMUNICATION,
    "communications": SECTOR_COMMUNICATION,
    "utilities": SECTOR_UTILITIES,
    "real estate": SECTOR_REAL_ESTATE,
}


# ── industry 보강 매핑 (GICS sector만으로는 부족할 때) ──────
# yfinance의 industry 필드(또는 KRX의 세부 업종)에 특정 키워드가 있으면 우선 적용.
_INDUSTRY_OVERRIDES: tuple[tuple[str, str], ...] = (
    # IT 세분화
    ("semiconductor", SECTOR_SEMICONDUCTORS),
    ("반도체", SECTOR_SEMICONDUCTORS),
    ("software", SECTOR_IT_SOFTWARE),
    ("internet", SECTOR_IT_SOFTWARE),
    ("interactive media", SECTOR_IT_SOFTWARE),
    ("소프트웨어", SECTOR_IT_SOFTWARE),
    ("인터넷", SECTOR_IT_SOFTWARE),
    # 통신
    ("telecom", SECTOR_COMMUNICATION),
    ("communication", SECTOR_COMMUNICATION),
    # 헬스케어
    ("biotech", SECTOR_HEALTHCARE),
    ("pharmaceutical", SECTOR_HEALTHCARE),
    ("medical", SECTOR_HEALTHCARE),
    ("바이오", SECTOR_HEALTHCARE),
    ("제약", SECTOR_HEALTHCARE),
    ("의료", SECTOR_HEALTHCARE),
)


def _normalize_key(s: str | None) -> str:
    """매핑 키 정규화: 소문자 + 공백/중점/하이픈 제거"""
    if not s:
        return ""
    out = s.strip().lower()
    for ch in (" ", "·", "-", "_", "/", ".", "(", ")", "&"):
        out = out.replace(ch, "")
    return out


# 정규화된 키로 다시 빌드 (런타임 비교 시 매번 normalize 하기 위함)
_KRX_TO_NORM_NORMALIZED: dict[str, str] = {
    _normalize_key(k): v for k, v in _KRX_TO_NORM.items()
}
_GICS_TO_NORM_NORMALIZED: dict[str, str] = {
    _normalize_key(k): v for k, v in _GICS_TO_NORM.items()
}


def normalize_krx_sector(sector_krx: str | None) -> str | None:
    """KRX 업종명 → sector_norm. 매핑 없으면 None (caller가 fallback 결정)."""
    if not sector_krx:
        return None
    return _KRX_TO_NORM_NORMALIZED.get(_normalize_key(sector_krx))


def normalize_gics_sector(sector_gics: str | None) -> str | None:
    """GICS sector → sector_norm. 매핑 없으면 None."""
    if not sector_gics:
        return None
    return _GICS_TO_NORM_NORMALIZED.get(_normalize_key(sector_gics))


def _industry_override(industry: str | None) -> str | None:
    """industry 문자열에 키워드가 있으면 sector_norm 강제 반환."""
    if not industry:
        return None
    norm = industry.strip().lower()
    for needle, sector in _INDUSTRY_OVERRIDES:
        if needle in norm:
            return sector
    return None


def normalize_sector(
    *,
    sector_krx: str | None = None,
    sector_gics: str | None = None,
    industry: str | None = None,
    warn_on_miss: bool = True,
) -> str:
    """모든 입력 신호를 종합해 sector_norm 결정.

    우선순위: industry override > KRX 업종 > GICS sector > "other".
    매핑 누락 시 SECTOR_OTHER 반환 + 경고 로그(중복 누락은 호출부에서 dedup 권장).

    Args:
        sector_krx: pykrx 업종명 (한글)
        sector_gics: yfinance sector (영문)
        industry: yfinance industry 또는 KRX 세부업종 (세분화 보강용)
        warn_on_miss: 매핑 실패 시 logger.warning 발생 여부

    Returns:
        sector_norm 문자열 (항상 ALL_SECTOR_NORMS 중 하나).
    """
    # 1) industry 키워드 매칭이 가장 강함 (반도체/소프트웨어 등 세분화)
    override = _industry_override(industry)
    if override:
        return override

    # 2) KRX 업종 우선 (한국 시장 비중이 큼)
    krx = normalize_krx_sector(sector_krx)
    if krx:
        return krx

    # 3) GICS sector
    gics = normalize_gics_sector(sector_gics)
    if gics:
        return gics

    # 4) fallback
    if warn_on_miss and (sector_krx or sector_gics or industry):
        get_logger("sector_mapping").warning(
            "섹터 매핑 누락 → other "
            f"(krx={sector_krx!r}, gics={sector_gics!r}, industry={industry!r})"
        )
    return SECTOR_OTHER


# ── 시총 버킷 ─────────────────────────────────────
# 양 시장 통합 기준 (KRW 환산). 환율은 USD=1400원 가정 (월 1회 갱신 예정).
# 향후 별도 fx_rate 테이블로 분리 가능하나 Phase 1a에서는 상수 사용.
_BUCKET_THRESHOLDS_KRW = (
    ("mega", 30_000_000_000_000),    # 30조원 이상
    ("large", 10_000_000_000_000),   # 10조원 이상
    ("mid", 1_000_000_000_000),      # 1조원 이상
    ("small", 0),                    # 그 이하
)


def market_cap_bucket(market_cap_krw: int | float | None) -> str | None:
    """시가총액(KRW) → small/mid/large/mega 버킷."""
    if market_cap_krw is None or market_cap_krw <= 0:
        return None
    for label, threshold in _BUCKET_THRESHOLDS_KRW:
        if market_cap_krw >= threshold:
            return label
    return "small"
