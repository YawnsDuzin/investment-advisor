"""섹터 분류 정규화 마스터 — KRX 업종(pykrx) ↔ GICS(yfinance) ↔ sector_norm.

`stock_universe.sector_norm`은 KOR/US 양 시장 공통 키로, Top Picks 다양성 제약
(`max_per_sector`)과 스크리너 스펙의 `sector_norm` 필터에 사용된다.

매핑 누락 시 `SECTOR_OTHER`로 fallback + 경고 로그.

**유지보수 가이드**
- 새로운 KRX 업종이 등장하면 `_KRX_TO_NORM`에 추가
- yfinance가 새 GICS sector를 노출하면 `_GICS_TO_NORM`에 추가
- 세분화 키(semiconductors, it_software 등)는 industry 단계 매핑(`_INDUSTRY_OVERRIDES`)으로 보강
- 한국 종목은 `industry` 컬럼이 비어있는 경우가 많아 `_KR_TICKER_OVERRIDES` / `_KR_NAME_KEYWORDS` 로 보강
- 변경 시 회귀 테스트 (오픈 이슈 §10.1) 추가 필요

**2026-04-24 개편 (P0-A/P1/P2)**
- 반도체 복구: 한국 종목 industry 부재로 `_INDUSTRY_OVERRIDES`가 발동 안 되던 문제를
  `_KR_TICKER_OVERRIDES`(화이트리스트) + `_KR_NAME_KEYWORDS`(종목명 키워드)로 우회.
- Finance 3분할: banks / insurance / capital_markets (+ 기타금융은 holding_co로 분리).
- Healthcare 2분할: biotech / pharma_medtech.
- Materials 4분할: chemicals / steel_metals / nonmetallic / paper_wood.
- 기존 `finance`/`healthcare`/`materials` 상수는 하위호환 fallback으로만 유지.
"""
from __future__ import annotations

from shared.logger import get_logger


# ── 표준 sector_norm 키 ─────────────────────────────
# 다양성 제약·스크리너 필터에 사용되는 정규화 키.

# IT 3분할 (기존 유지)
SECTOR_SEMICONDUCTORS = "semiconductors"
SECTOR_IT_HARDWARE = "it_hardware"
SECTOR_IT_SOFTWARE = "it_software"
SECTOR_COMMUNICATION = "communication"

# Finance 3분할 (P2)
SECTOR_BANKS = "banks"
SECTOR_INSURANCE = "insurance"
SECTOR_CAPITAL_MARKETS = "capital_markets"
SECTOR_FINANCE = "finance"  # DEPRECATED — fallback only (기타 금융 잔여분)

# Healthcare 2분할 (P2)
SECTOR_BIOTECH = "biotech"
SECTOR_PHARMA_MEDTECH = "pharma_medtech"
SECTOR_HEALTHCARE = "healthcare"  # DEPRECATED — fallback only

# Consumer
SECTOR_CONSUMER_DISCRETIONARY = "consumer_discretionary"
SECTOR_CONSUMER_STAPLES = "consumer_staples"

# Energy / Materials 4분할 (P1)
SECTOR_ENERGY = "energy"
SECTOR_CHEMICALS = "chemicals"
SECTOR_BATTERY_MATERIALS = "battery_materials"   # P1-ext: 2차전지 소재 분리
SECTOR_STEEL_METALS = "steel_metals"
SECTOR_NONMETALLIC = "nonmetallic"
SECTOR_PAPER_WOOD = "paper_wood"
SECTOR_MATERIALS = "materials"  # DEPRECATED — fallback only

# Industrials 세분화 (P1-ext / P1-ext2)
SECTOR_INDUSTRIALS = "industrials"
SECTOR_AUTOS = "autos"                           # 자동차·부품
SECTOR_AEROSPACE_DEFENSE = "aerospace_defense"   # 항공·방위
SECTOR_TRANSPORT_LOGISTICS = "transport_logistics"  # 운송·물류
SECTOR_SHIPBUILDING = "shipbuilding"             # 조선·해양플랜트 (P1-ext2)
SECTOR_CONSTRUCTION = "construction"             # 건설·EPC·엔지니어링 (P1-ext2)

# Consumer Discretionary 세분화 (P1-ext)
SECTOR_MEDIA_ENTERTAINMENT = "media_entertainment"  # 미디어·엔터

# 기타
SECTOR_UTILITIES = "utilities"
SECTOR_REAL_ESTATE = "real_estate"
SECTOR_HOLDING_CO = "holding_co"  # 지주사 분리 버킷 (P0.5)
SECTOR_OTHER = "other"

ALL_SECTOR_NORMS: tuple[str, ...] = (
    SECTOR_SEMICONDUCTORS,
    SECTOR_IT_HARDWARE,
    SECTOR_IT_SOFTWARE,
    SECTOR_COMMUNICATION,
    SECTOR_BANKS,
    SECTOR_INSURANCE,
    SECTOR_CAPITAL_MARKETS,
    SECTOR_FINANCE,  # deprecated fallback
    SECTOR_BIOTECH,
    SECTOR_PHARMA_MEDTECH,
    SECTOR_HEALTHCARE,  # deprecated fallback
    SECTOR_CONSUMER_DISCRETIONARY,
    SECTOR_CONSUMER_STAPLES,
    SECTOR_ENERGY,
    SECTOR_CHEMICALS,
    SECTOR_BATTERY_MATERIALS,
    SECTOR_STEEL_METALS,
    SECTOR_NONMETALLIC,
    SECTOR_PAPER_WOOD,
    SECTOR_MATERIALS,  # deprecated fallback
    SECTOR_INDUSTRIALS,
    SECTOR_AUTOS,
    SECTOR_AEROSPACE_DEFENSE,
    SECTOR_TRANSPORT_LOGISTICS,
    SECTOR_SHIPBUILDING,
    SECTOR_CONSTRUCTION,
    SECTOR_MEDIA_ENTERTAINMENT,
    SECTOR_UTILITIES,
    SECTOR_REAL_ESTATE,
    SECTOR_HOLDING_CO,
    SECTOR_OTHER,
)

# deprecated 상수 참조 금지 세트 (lint/회귀 검사용)
DEPRECATED_SECTOR_NORMS: frozenset[str] = frozenset({
    SECTOR_FINANCE, SECTOR_HEALTHCARE, SECTOR_MATERIALS,
})


# ── KRX 업종 매핑 (pykrx KOSPI/KOSDAQ 업종명 기준) ───────────
# pykrx.stock.get_market_ticker_industry / 종목 상세에서 노출되는 한글 업종명.
# 공백·중점·괄호 등은 _normalize_key()로 정규화 후 비교.
_KRX_TO_NORM: dict[str, str] = {
    # ── KOSPI 대분류 ──
    "음식료품": SECTOR_CONSUMER_STAPLES,
    "섬유의복": SECTOR_CONSUMER_DISCRETIONARY,
    "종이목재": SECTOR_PAPER_WOOD,                 # P1
    "화학": SECTOR_CHEMICALS,                      # P1
    "의약품": SECTOR_PHARMA_MEDTECH,               # P2 (바이오는 _NAME_KEYWORDS에서 세분)
    "비금속광물": SECTOR_NONMETALLIC,              # P1
    "철강금속": SECTOR_STEEL_METALS,               # P1
    "기계": SECTOR_INDUSTRIALS,
    "전기전자": SECTOR_IT_HARDWARE,
    "의료정밀": SECTOR_PHARMA_MEDTECH,             # P2
    "운수장비": SECTOR_INDUSTRIALS,
    "유통업": SECTOR_CONSUMER_DISCRETIONARY,
    "전기가스업": SECTOR_UTILITIES,
    "건설업": SECTOR_CONSTRUCTION,                 # P1-ext2
    "운수창고업": SECTOR_TRANSPORT_LOGISTICS,   # P1-ext
    "운수창고": SECTOR_TRANSPORT_LOGISTICS,
    "통신업": SECTOR_COMMUNICATION,
    "금융업": SECTOR_BANKS,                        # P2 (대부분 은행·금융지주)
    "은행": SECTOR_BANKS,                          # P2
    "증권": SECTOR_CAPITAL_MARKETS,                # P2
    "보험": SECTOR_INSURANCE,                      # P2
    "서비스업": SECTOR_CONSUMER_DISCRETIONARY,
    "제조업": SECTOR_INDUSTRIALS,

    # ── KOSDAQ 대분류 ──
    "음식료담배": SECTOR_CONSUMER_STAPLES,
    "음식료": SECTOR_CONSUMER_STAPLES,
    "섬유의류": SECTOR_CONSUMER_DISCRETIONARY,
    "제약": SECTOR_PHARMA_MEDTECH,                 # P2 (바이오는 _NAME_KEYWORDS)
    "비금속": SECTOR_NONMETALLIC,                  # P1
    "금속": SECTOR_STEEL_METALS,                   # P1
    "기계장비": SECTOR_INDUSTRIALS,
    "일반전기전자": SECTOR_IT_HARDWARE,
    "의료정밀기기": SECTOR_PHARMA_MEDTECH,         # P2
    "운송장비부품": SECTOR_AUTOS,                  # P1-ext: 대부분 자동차 부품. 조선/항공은 티커 override
    "유통": SECTOR_CONSUMER_DISCRETIONARY,
    "숙박음식": SECTOR_CONSUMER_DISCRETIONARY,
    "운송": SECTOR_TRANSPORT_LOGISTICS,            # P1-ext
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
    "금융": SECTOR_BANKS,                          # P2
    "오락문화": SECTOR_CONSUMER_DISCRETIONARY,
    "교육서비스": SECTOR_CONSUMER_DISCRETIONARY,

    # ── 추가 변형 / 흔한 표기 ──
    # "기타금융"은 지주사 오염원 — SECTOR_HOLDING_CO 로 분리 (P0.5)
    "기타금융": SECTOR_HOLDING_CO,
    "기타제조": SECTOR_INDUSTRIALS,
    "건설": SECTOR_CONSTRUCTION,                   # P1-ext2
    "철강": SECTOR_STEEL_METALS,                   # P1

    # ── KRX 표준업종 분류(2026 현재) — 실데이터 기반 보강 ──
    "IT서비스": SECTOR_IT_SOFTWARE,
    "IT 서비스": SECTOR_IT_SOFTWARE,
    "일반서비스": SECTOR_CONSUMER_DISCRETIONARY,
    "운송창고": SECTOR_TRANSPORT_LOGISTICS,        # P1-ext
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
    "materials": SECTOR_CHEMICALS,                 # P1 — 기본은 chemicals, industry로 세분
    "basic materials": SECTOR_CHEMICALS,
    "industrials": SECTOR_INDUSTRIALS,
    "consumer discretionary": SECTOR_CONSUMER_DISCRETIONARY,
    "consumer cyclical": SECTOR_CONSUMER_DISCRETIONARY,
    "consumer staples": SECTOR_CONSUMER_STAPLES,
    "consumer defensive": SECTOR_CONSUMER_STAPLES,
    "health care": SECTOR_PHARMA_MEDTECH,           # P2 — 기본 pharma, biotech는 industry/name에서
    "healthcare": SECTOR_PHARMA_MEDTECH,
    "financials": SECTOR_BANKS,                     # P2 — 기본 banks, industry로 insurance/capital 세분
    "financial services": SECTOR_BANKS,
    "finance": SECTOR_BANKS,                        # AI 자유 텍스트 대응
    "financial": SECTOR_BANKS,
    "information technology": SECTOR_IT_HARDWARE,
    "technology": SECTOR_IT_HARDWARE,
    "communication services": SECTOR_COMMUNICATION,
    "communications": SECTOR_COMMUNICATION,
    "utilities": SECTOR_UTILITIES,
    "real estate": SECTOR_REAL_ESTATE,
}


# ── industry 보강 매핑 (GICS Sector만으로는 부족할 때) ──────
# yfinance의 industry 필드(또는 KRX의 세부 업종)에 특정 키워드가 있으면 우선 적용.
# 우선순위: 아래 리스트의 앞쪽이 먼저 매칭됨 → 구체적 키워드를 위에 배치.
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
    # 헬스케어 세분화 (P2)
    ("biotech", SECTOR_BIOTECH),
    ("biotechnology", SECTOR_BIOTECH),
    ("gene", SECTOR_BIOTECH),
    ("바이오", SECTOR_BIOTECH),  # 단 "바이오로직스"는 CMO — _KR_NAME_KEYWORDS에서 예외 처리
    ("pharmaceutical", SECTOR_PHARMA_MEDTECH),
    ("drug manufacturers", SECTOR_PHARMA_MEDTECH),
    ("medical device", SECTOR_PHARMA_MEDTECH),
    ("medical instruments", SECTOR_PHARMA_MEDTECH),
    ("healthcare plans", SECTOR_PHARMA_MEDTECH),
    ("medical", SECTOR_PHARMA_MEDTECH),
    ("제약", SECTOR_PHARMA_MEDTECH),
    ("의료", SECTOR_PHARMA_MEDTECH),
    # Financials 세분화 (P2)
    ("insurance", SECTOR_INSURANCE),
    ("life insurance", SECTOR_INSURANCE),
    ("property & casualty", SECTOR_INSURANCE),
    ("reinsurance", SECTOR_INSURANCE),
    ("보험", SECTOR_INSURANCE),
    ("asset management", SECTOR_CAPITAL_MARKETS),
    ("capital markets", SECTOR_CAPITAL_MARKETS),
    ("investment banking", SECTOR_CAPITAL_MARKETS),
    ("brokerage", SECTOR_CAPITAL_MARKETS),
    ("financial exchanges", SECTOR_CAPITAL_MARKETS),
    ("financial data", SECTOR_CAPITAL_MARKETS),
    ("증권", SECTOR_CAPITAL_MARKETS),
    ("자산운용", SECTOR_CAPITAL_MARKETS),
    ("banks", SECTOR_BANKS),
    ("banks—", SECTOR_BANKS),
    ("regional banks", SECTOR_BANKS),
    ("diversified banks", SECTOR_BANKS),
    ("bank", SECTOR_BANKS),
    ("은행", SECTOR_BANKS),
    # Materials 세분화 (P1)
    ("specialty chemicals", SECTOR_CHEMICALS),
    ("chemicals", SECTOR_CHEMICALS),
    ("agricultural inputs", SECTOR_CHEMICALS),
    ("화학", SECTOR_CHEMICALS),
    ("steel", SECTOR_STEEL_METALS),
    ("iron", SECTOR_STEEL_METALS),
    ("copper", SECTOR_STEEL_METALS),
    ("aluminum", SECTOR_STEEL_METALS),
    ("gold", SECTOR_STEEL_METALS),
    ("silver", SECTOR_STEEL_METALS),
    ("precious metals", SECTOR_STEEL_METALS),
    ("industrial metals", SECTOR_STEEL_METALS),
    ("철강", SECTOR_STEEL_METALS),
    ("금속", SECTOR_STEEL_METALS),
    ("building materials", SECTOR_NONMETALLIC),
    ("cement", SECTOR_NONMETALLIC),
    ("glass", SECTOR_NONMETALLIC),
    ("비금속", SECTOR_NONMETALLIC),
    ("paper", SECTOR_PAPER_WOOD),
    ("lumber", SECTOR_PAPER_WOOD),
    ("forest", SECTOR_PAPER_WOOD),
    ("종이", SECTOR_PAPER_WOOD),
    ("목재", SECTOR_PAPER_WOOD),
    # Energy 미국 섹터 보강 (SK이노베이션 같은 국내 혼선은 ticker override)
    ("oil & gas", SECTOR_ENERGY),
    ("integrated oil", SECTOR_ENERGY),
    ("refining", SECTOR_ENERGY),
    # Autos (P1-ext)
    ("auto manufacturers", SECTOR_AUTOS),
    ("auto parts", SECTOR_AUTOS),
    ("auto & truck", SECTOR_AUTOS),
    ("automobile", SECTOR_AUTOS),
    ("tires & rubber", SECTOR_AUTOS),
    # Shipbuilding (P1-ext2) — 조선을 aerospace/defense보다 먼저 매칭
    ("shipbuilding", SECTOR_SHIPBUILDING),
    ("boats & ships", SECTOR_SHIPBUILDING),
    ("marine shipbuilding", SECTOR_SHIPBUILDING),
    # Aerospace / Defense (P1-ext)
    ("aerospace", SECTOR_AEROSPACE_DEFENSE),
    ("defense", SECTOR_AEROSPACE_DEFENSE),
    ("weapons", SECTOR_AEROSPACE_DEFENSE),
    # Construction & Engineering (P1-ext2) — "heavy construction"은 CAT의 "Heavy Construction Machinery"와 오탐 → 제외
    ("construction & engineering", SECTOR_CONSTRUCTION),
    ("engineering & construction", SECTOR_CONSTRUCTION),
    ("construction services", SECTOR_CONSTRUCTION),
    ("general contractors", SECTOR_CONSTRUCTION),
    ("general building contractors", SECTOR_CONSTRUCTION),
    # Transport & Logistics (P1-ext)
    ("airlines", SECTOR_TRANSPORT_LOGISTICS),
    ("airline", SECTOR_TRANSPORT_LOGISTICS),
    ("marine shipping", SECTOR_TRANSPORT_LOGISTICS),
    ("trucking", SECTOR_TRANSPORT_LOGISTICS),
    ("railroads", SECTOR_TRANSPORT_LOGISTICS),
    ("integrated freight", SECTOR_TRANSPORT_LOGISTICS),
    ("integrated shipping", SECTOR_TRANSPORT_LOGISTICS),
    # Media / Entertainment (P1-ext, 단 게임은 it_software 유지)
    ("entertainment", SECTOR_MEDIA_ENTERTAINMENT),
    ("broadcasting", SECTOR_MEDIA_ENTERTAINMENT),
    ("publishing", SECTOR_MEDIA_ENTERTAINMENT),
    ("advertising agencies", SECTOR_MEDIA_ENTERTAINMENT),
    # REITs
    ("reit", SECTOR_REAL_ESTATE),
    ("리츠", SECTOR_REAL_ESTATE),
)


# ── 한국 종목 보정 1: 티커 화이트리스트 (P0-A) ──
# 한국은 industry 컬럼이 비어있는 구조적 한계 탓에 industry override가 발동하지 않는다.
# 주요 반도체·지주사·오분류 종목을 티커 단위로 강제 매핑.
# 유지보수: 신규 상장 시 추가. 커버리지는 "상위 시총 순 + 주요 테마주" 위주.
_KR_TICKER_OVERRIDES: dict[str, str] = {
    # ── 반도체 (semiconductors) — P0 핵심 ──
    # 종목명에 "반도체"가 없는 메이저·장비·소재 위주로 수록. 이름에 "반도체"가 있는 종목은
    # _KR_NAME_KEYWORDS 의 "반도체" 매칭으로 자동 커버되므로 중복 등재하지 않음.
    "000660": SECTOR_SEMICONDUCTORS,  # SK하이닉스
    "000990": SECTOR_SEMICONDUCTORS,  # DB하이텍
    "005290": SECTOR_SEMICONDUCTORS,  # 동진쎄미켐
    "036830": SECTOR_SEMICONDUCTORS,  # 솔브레인홀딩스
    "039030": SECTOR_SEMICONDUCTORS,  # 이오테크닉스
    "058470": SECTOR_SEMICONDUCTORS,  # 리노공업
    "064760": SECTOR_SEMICONDUCTORS,  # 티씨케이
    "067310": SECTOR_SEMICONDUCTORS,  # 하나마이크론
    "074600": SECTOR_SEMICONDUCTORS,  # 원익QnC
    "084370": SECTOR_SEMICONDUCTORS,  # 유진테크
    "089030": SECTOR_SEMICONDUCTORS,  # 테크윙
    "095340": SECTOR_SEMICONDUCTORS,  # ISC
    "108320": SECTOR_SEMICONDUCTORS,  # LX세미콘
    "061970": SECTOR_SEMICONDUCTORS,  # 엘비세미콘 (correct ticker, was 119830=아이텍 bug)
    "064290": SECTOR_SEMICONDUCTORS,  # 인텍플러스 (correct ticker, was 150840 미상장 bug)
    "140860": SECTOR_SEMICONDUCTORS,  # 파크시스템스
    "161580": SECTOR_SEMICONDUCTORS,  # 필옵틱스
    "171090": SECTOR_SEMICONDUCTORS,  # 선익시스템
    "178320": SECTOR_SEMICONDUCTORS,  # 서진시스템
    "183300": SECTOR_SEMICONDUCTORS,  # 코미코
    "189300": SECTOR_SEMICONDUCTORS,  # 인텔리안테크
    "213420": SECTOR_SEMICONDUCTORS,  # 덕산네오룩스
    "348210": SECTOR_SEMICONDUCTORS,  # 넥스틴 (correct ticker, was 217270=넵튠 bug)
    "222080": SECTOR_SEMICONDUCTORS,  # 씨아이에스
    "240810": SECTOR_SEMICONDUCTORS,  # 원익IPS
    "241790": SECTOR_SEMICONDUCTORS,  # 티이엠씨씨엔에스 (반도체용 고순도 TEOS 가스 소재)
    "394280": SECTOR_SEMICONDUCTORS,  # 오픈엣지테크놀로지 (correct ticker)
    "319660": SECTOR_SEMICONDUCTORS,  # 피에스케이
    "031980": SECTOR_SEMICONDUCTORS,  # 피에스케이홀딩스
    "357780": SECTOR_SEMICONDUCTORS,  # 솔브레인
    "403870": SECTOR_SEMICONDUCTORS,  # HPSP

    # ── 삼성전자: 반도체 비중 크지만 복합 — IT_HARDWARE 유지 ──
    "005930": SECTOR_IT_HARDWARE,     # 삼성전자

    # ── 지주사 (holding_co) — P0.5 핵심 ──
    # 지주업을 본업으로 하는 종목만. 자회사 지분법 이익 의존도 높음.
    "034730": SECTOR_HOLDING_CO,      # SK (그룹지주)
    "402340": SECTOR_HOLDING_CO,      # SK스퀘어
    "006120": SECTOR_HOLDING_CO,      # SK디스커버리
    "003550": SECTOR_HOLDING_CO,      # LG (지주)
    "000880": SECTOR_HOLDING_CO,      # 한화
    "267250": SECTOR_HOLDING_CO,      # HD현대
    "004800": SECTOR_HOLDING_CO,      # 효성
    "001040": SECTOR_HOLDING_CO,      # CJ
    "078930": SECTOR_HOLDING_CO,      # GS

    # ── 핀테크·인프라펀드·신탁 오분류 교정 (KRX "기타금융" → holding_co 잘못 매핑되는 예외) ──
    "377300": SECTOR_CAPITAL_MARKETS, # 카카오페이 (결제·증권 플랫폼)
    "088980": SECTOR_REAL_ESTATE,     # 맥쿼리인프라 (상장 인프라 펀드)
    "415640": SECTOR_REAL_ESTATE,     # KB발해인프라 (인프라 투자신탁)
    "094800": SECTOR_REAL_ESTATE,     # 맵스리얼티
    "034830": SECTOR_REAL_ESTATE,     # 한국토지신탁
    "123890": SECTOR_REAL_ESTATE,     # 한국자산신탁
    "026890": SECTOR_CAPITAL_MARKETS, # 스틱인베스트먼트 (PE 투자운용)
    "244920": SECTOR_INSURANCE,       # 에이플러스에셋 (보험대리점)
    "229640": SECTOR_INDUSTRIALS,     # LS에코에너지 (전력 기자재)
    "007540": SECTOR_CONSUMER_STAPLES, # 샘표 (식품 지주)
    "013570": SECTOR_INDUSTRIALS,     # 디와이 (기계 지주)
    "044820": SECTOR_CONSUMER_STAPLES, # 코스맥스비티아이 (코스메틱 지주)

    # ── pharma_medtech 오분류 교정 (KRX "의료·정밀기기"에 반도체/광학/방위 혼입) ──
    "025560": SECTOR_SEMICONDUCTORS,  # 미래산업 (반도체 테스트 핸들러)
    "003160": SECTOR_SEMICONDUCTORS,  # 디아이 (반도체 번인 테스트)
    "424960": SECTOR_IT_HARDWARE,     # 스마트레이더시스템
    "053450": SECTOR_IT_HARDWARE,     # 세코닉스 (광학렌즈)
    "065450": SECTOR_AEROSPACE_DEFENSE,  # 빅텍 (방위 전자) — P1-ext

    # ── yfinance industry 부정확성 교정 (P1-ext) ──
    "012700": SECTOR_CONSUMER_DISCRETIONARY,  # 리드코프 (대부업, yfinance가 Oil & Gas로 잘못 반환)
    "023410": SECTOR_NONMETALLIC,             # 유진기업 (레미콘, yfinance가 Capital Markets로 잘못 반환)

    # ── KOSDAQ 미커버 대형주 수작업 태깅 (시총 상위 중 industry NULL 잔존) ──
    # 반도체 장비·소재 추가
    "036930": SECTOR_SEMICONDUCTORS,  # 주성엔지니어링
    "083450": SECTOR_SEMICONDUCTORS,  # GST
    "101490": SECTOR_SEMICONDUCTORS,  # 에스앤에스텍 (블랭크마스크)
    "078600": SECTOR_SEMICONDUCTORS,  # 대주전자재료 (반도체·디스플레이 소재)
    "077360": SECTOR_SEMICONDUCTORS,  # 덕산하이메탈 (OLED/반도체 소재)
    "102710": SECTOR_SEMICONDUCTORS,  # 이엔에프테크놀로지 (반도체 공정 화학)
    "131970": SECTOR_SEMICONDUCTORS,  # 두산테스나 (반도체 테스트)
    "131290": SECTOR_SEMICONDUCTORS,  # 티에스이 (반도체 테스트 소켓)
    # 통신 추가
    "050890": SECTOR_COMMUNICATION,   # 쏠리드 (통신장비)
    "218410": SECTOR_COMMUNICATION,   # RFHIC (RF 통신장비)
    # 바이오 추가
    "358570": SECTOR_BIOTECH,         # 지아이이노베이션 (바이오 신약)
    "052020": SECTOR_BIOTECH,         # 에스티큐브 (바이오 신약)
    "082270": SECTOR_BIOTECH,         # 젬백스 (바이오 신약)
    "007390": SECTOR_BIOTECH,         # 네이처셀 (줄기세포)
    "078160": SECTOR_BIOTECH,         # 메디포스트 (줄기세포)
    "214370": SECTOR_BIOTECH,         # 케어젠
    "087010": SECTOR_BIOTECH,         # 펩트론
    # pharma 추가
    "140410": SECTOR_PHARMA_MEDTECH,  # 메지온
    "237690": SECTOR_PHARMA_MEDTECH,  # 에스티팜 (합성 원료 CMO)
    # VC/PE → capital_markets
    "100790": SECTOR_CAPITAL_MARKETS, # 미래에셋벤처투자
    "027360": SECTOR_CAPITAL_MARKETS, # 아주IB투자

    # ── Autos (P1-ext) ──
    "005380": SECTOR_AUTOS,           # 현대차
    "000270": SECTOR_AUTOS,           # 기아
    "012330": SECTOR_AUTOS,           # 현대모비스
    "011210": SECTOR_AUTOS,           # 현대위아
    "204320": SECTOR_AUTOS,           # HL만도
    "018880": SECTOR_AUTOS,           # 한온시스템
    "161390": SECTOR_AUTOS,           # 한국타이어앤테크놀로지
    "073240": SECTOR_AUTOS,           # 금호타이어
    "002350": SECTOR_AUTOS,           # 넥센타이어
    "013520": SECTOR_AUTOS,           # 화승알앤에이
    "120115": SECTOR_AUTOS,           # 지누스 제외 — 재정의 방지
    "003620": SECTOR_AUTOS,           # KG모빌리티
    "192650": SECTOR_AUTOS,           # 드림텍 제외
    "008770": SECTOR_AUTOS,           # 호텔신라 제외 — 재정의
    # 지누스/드림텍/호텔신라 재정의 (위 줄은 의도적 덮어쓰기 방지 목적 주석이었으나 dict 중복키는 마지막이 이김)
    "120115": SECTOR_CONSUMER_DISCRETIONARY,  # 지누스 (매트리스)
    "192650": SECTOR_IT_HARDWARE,             # 드림텍
    "008770": SECTOR_CONSUMER_DISCRETIONARY,  # 호텔신라

    # ── Battery Materials (P1-ext) ──
    "247540": SECTOR_BATTERY_MATERIALS,  # 에코프로비엠 (양극재)
    "086520": SECTOR_BATTERY_MATERIALS,  # 에코프로 (양극재 전구체·지주성 복합 — 배터리 우선)
    "003670": SECTOR_BATTERY_MATERIALS,  # 포스코퓨처엠 (양극재)
    "066970": SECTOR_BATTERY_MATERIALS,  # 엘앤에프 (양극재)
    "121600": SECTOR_BATTERY_MATERIALS,  # 나노신소재
    "373220": SECTOR_BATTERY_MATERIALS,  # LG에너지솔루션 (배터리셀 — 소재로 분류)
    "006400": SECTOR_BATTERY_MATERIALS,  # 삼성SDI (배터리셀)
    "096700": SECTOR_BATTERY_MATERIALS,  # SDN 제외 — 재정의
    # SDN 복원
    "096700": SECTOR_UTILITIES,           # SDN (태양광·전력) 복원
    "028050": SECTOR_BATTERY_MATERIALS,   # 삼성엔지니어링 제외 — 재정의
    "028050": SECTOR_INDUSTRIALS,         # 삼성엔지니어링 복원 (EPC)

    # ── Aerospace / Defense (P1-ext) ──
    "079550": SECTOR_AEROSPACE_DEFENSE,  # LIG넥스원
    "012450": SECTOR_AEROSPACE_DEFENSE,  # 한화에어로스페이스
    "047810": SECTOR_AEROSPACE_DEFENSE,  # 한국항공우주(KAI)
    "064350": SECTOR_AEROSPACE_DEFENSE,  # 현대로템
    "099320": SECTOR_AEROSPACE_DEFENSE,  # 쎄트렉아이 (위성)
    "272210": SECTOR_AEROSPACE_DEFENSE,  # 한화시스템
    "042660": SECTOR_AEROSPACE_DEFENSE,  # 한화오션 (옛 대우조선 방산)

    # ── Transport / Logistics (P1-ext) ──
    "003490": SECTOR_TRANSPORT_LOGISTICS,  # 대한항공
    "011200": SECTOR_TRANSPORT_LOGISTICS,  # HMM
    "028670": SECTOR_TRANSPORT_LOGISTICS,  # 팬오션
    "000120": SECTOR_TRANSPORT_LOGISTICS,  # CJ대한통운
    "044450": SECTOR_TRANSPORT_LOGISTICS,  # KSS해운
    "005880": SECTOR_TRANSPORT_LOGISTICS,  # 대한해운

    # ── Media / Entertainment (P1-ext) ──
    "253450": SECTOR_MEDIA_ENTERTAINMENT,  # 스튜디오드래곤
    "035900": SECTOR_MEDIA_ENTERTAINMENT,  # JYP Ent.
    "041510": SECTOR_MEDIA_ENTERTAINMENT,  # 에스엠
    "122870": SECTOR_MEDIA_ENTERTAINMENT,  # 와이지엔터테인먼트
    "035760": SECTOR_MEDIA_ENTERTAINMENT,  # CJ ENM
    "352820": SECTOR_MEDIA_ENTERTAINMENT,  # 하이브
    "036420": SECTOR_MEDIA_ENTERTAINMENT,  # 제이콘텐트리(콘텐트리중앙)
    "034120": SECTOR_MEDIA_ENTERTAINMENT,  # SBS
    "066570": SECTOR_IT_HARDWARE,          # LG전자 (가전)

    # ── P1-ext 4차 재정규화 후 발견된 yfinance 버그/오분류 ──
    "002230": SECTOR_IT_HARDWARE,          # 피에스텍 (yfinance가 Auto Parts로 잘못 반환 — 실제 측정기기)
    "000240": SECTOR_HOLDING_CO,           # 한국앤컴퍼니 (한국타이어 지주)
    "208860": SECTOR_COMMUNICATION,        # 다산디엠씨 (통신장비)
    "005720": SECTOR_CHEMICALS,            # 넥센 (종합화학 지주)
    "126600": SECTOR_CHEMICALS,            # BGF에코머티리얼즈 (컴파운드 플라스틱)
    "101360": SECTOR_BATTERY_MATERIALS,    # 에코앤드림 (2차전지 전구체)
    "360070": SECTOR_BATTERY_MATERIALS,    # 탑머티리얼 (2차전지 소재)

    # ── Shipbuilding (P1-ext2) — 조선 대장주 aerospace_defense에서 분리 ──
    "329180": SECTOR_SHIPBUILDING,         # HD현대중공업 (70조)
    "009540": SECTOR_SHIPBUILDING,         # HD한국조선해양 (34조)
    "010140": SECTOR_SHIPBUILDING,         # 삼성중공업 (30조)
    "042660": SECTOR_SHIPBUILDING,         # 한화오션 (옛 대우조선해양)
    "443060": SECTOR_SHIPBUILDING,         # HD현대마린솔루션 (선박 AS·서비스)
    "071970": SECTOR_SHIPBUILDING,         # HD현대마린엔진
    "439260": SECTOR_SHIPBUILDING,         # 대한조선
    "075580": SECTOR_SHIPBUILDING,         # 세진중공업 (해양플랜트)
    "097230": SECTOR_SHIPBUILDING,         # HJ중공업 (조선+건설 혼합, 조선 우위)

    # ── Construction 추가 티커 (KRX "일반서비스" 소속인 EPC/엔지니어링) ──
    # KRX "건설업"/"건설" 대분류는 자동 흡수됨. 아래는 대분류가 다른 EPC·엔지니어링.
    "028050": SECTOR_CONSTRUCTION,         # 삼성E&A (구 삼성엔지니어링)
    "052690": SECTOR_CONSTRUCTION,         # 한전기술 (원전 설계)
    "002150": SECTOR_CONSTRUCTION,         # 도화엔지니어링
    "053690": SECTOR_CONSTRUCTION,         # 한미글로벌 (CM)
    "079900": SECTOR_CONSTRUCTION,         # 전진건설로봇 (건설 특화 로봇)
    "060370": SECTOR_CONSTRUCTION,         # LS마린솔루션 (해저 케이블 EPC)
    "012630": SECTOR_HOLDING_CO,           # HDC (건설 지주) — 지주 유지

    # ── KRX 분류 명백 오류 개별 교정 ──
    "088390": SECTOR_IT_HARDWARE,          # 이녹스 (반도체·디스플레이 소재)
    "298040": SECTOR_INDUSTRIALS,          # 효성중공업 (중전기 설비, it_hardware 오분류 교정)
    "032820": SECTOR_INDUSTRIALS,          # 우리기술 (발전 제어)
    "095610": SECTOR_SEMICONDUCTORS,       # 테스 (반도체 증착 장비)
    "036630": SECTOR_COMMUNICATION,        # 세종텔레콤 (KRX "건설" 오분류)

    # ── 에너지 오분류 교정 (KRX "화학"에 들어가는 정유·배터리) ──
    "096770": SECTOR_ENERGY,          # SK이노베이션 (정유+배터리)
    "010950": SECTOR_ENERGY,          # S-Oil

    # ── 유틸리티 오분류 교정 ──
    "018670": SECTOR_UTILITIES,       # SK가스 (LPG, KRX "유통"으로 들어감)
    "036460": SECTOR_UTILITIES,       # 한국가스공사
    "015760": SECTOR_UTILITIES,       # 한국전력

    # ── 바이오 (biotech) — 신약개발·플랫폼·백신 등 고위험군 ──
    # 참고: 091990 셀트리온헬스케어는 2024년 셀트리온 합병으로 상폐 — 제거
    "068270": SECTOR_BIOTECH,         # 셀트리온
    "326030": SECTOR_BIOTECH,         # SK바이오팜 (신약)
    "298380": SECTOR_BIOTECH,         # 에이비엘바이오
    "950160": SECTOR_BIOTECH,         # 코오롱티슈진
    "196170": SECTOR_BIOTECH,         # 알테오젠
    "302440": SECTOR_BIOTECH,         # SK바이오사이언스 (백신)
    "207940": SECTOR_BIOTECH,         # 삼성바이오로직스 (CMO이지만 플랫폼성)

    # ── 제약·의료기기 (pharma_medtech) ──
    "214150": SECTOR_PHARMA_MEDTECH,  # 클래시스 (의료기기)
    "000100": SECTOR_PHARMA_MEDTECH,  # 유한양행 (제약)
    "185750": SECTOR_PHARMA_MEDTECH,  # 종근당
    "128940": SECTOR_PHARMA_MEDTECH,  # 한미약품
    "086900": SECTOR_PHARMA_MEDTECH,  # 메디톡스
    "145020": SECTOR_PHARMA_MEDTECH,  # 휴젤 (보툴리눔톡신)
    "009420": SECTOR_PHARMA_MEDTECH,  # 한올바이오파마

    # ── 은행·금융지주 (banks) ──
    "105560": SECTOR_BANKS,           # KB금융
    "055550": SECTOR_BANKS,           # 신한지주
    "086790": SECTOR_BANKS,           # 하나금융지주
    "316140": SECTOR_BANKS,           # 우리금융지주
    "024110": SECTOR_BANKS,           # 기업은행
    "138930": SECTOR_BANKS,           # BNK금융지주
    "175330": SECTOR_BANKS,           # JB금융지주

    # ── 증권·자산운용 (capital_markets) ──
    "006800": SECTOR_CAPITAL_MARKETS, # 미래에셋증권
    "039490": SECTOR_CAPITAL_MARKETS, # 키움증권
    "016360": SECTOR_CAPITAL_MARKETS, # 삼성증권
    "030610": SECTOR_CAPITAL_MARKETS, # 교보증권
    "003540": SECTOR_CAPITAL_MARKETS, # 대신증권
    "001510": SECTOR_CAPITAL_MARKETS, # SK증권

    # ── 보험 (insurance) ──
    "005830": SECTOR_INSURANCE,       # DB손해보험
    "000810": SECTOR_INSURANCE,       # 삼성화재
    "032830": SECTOR_INSURANCE,       # 삼성생명
    "088350": SECTOR_INSURANCE,       # 한화생명
    "082640": SECTOR_INSURANCE,       # 동양생명

    # ── biotech 이름 오분류 교정 (이름만 "바이오"인 non-biotech) ──
    "054050": SECTOR_CONSUMER_STAPLES,  # 농우바이오 (종자)
    "038460": SECTOR_IT_HARDWARE,       # 바이오스마트 (스마트카드 IC)
    "059210": SECTOR_PHARMA_MEDTECH,    # 메타바이오메드 (치과 의료기기)
    "092190": SECTOR_IT_HARDWARE,       # 서울바이오시스 (UV LED)
    "188040": SECTOR_CONSUMER_STAPLES,  # 바이오포트 (해조류)
    "082850": SECTOR_CONSUMER_STAPLES,  # 우리바이오 (건강기능식품)
    "353810": SECTOR_CONSUMER_STAPLES,  # 이지바이오 (사료)
    "317870": SECTOR_CHEMICALS,         # 엔바이오니아 (나노소재)
    "086060": SECTOR_CONSUMER_STAPLES,  # 진바이오텍 (동물사료)

    # ── REITs ──
    "395400": SECTOR_REAL_ESTATE,     # SK리츠
    "357430": SECTOR_REAL_ESTATE,     # 마스턴프리미어리츠
    "448730": SECTOR_REAL_ESTATE,     # 삼성FN리츠
}


# ── 한국 종목 보정 2: 종목명 키워드 (P0-A) ──
# 티커 화이트리스트 다음 우선순위. asset_name에 키워드가 포함되면 해당 sector로 매핑.
# 주의: 짧고 흔한 키워드는 오탐 위험. 명확한 경우만.
# 검사 순서: 아래 리스트의 앞쪽이 먼저 매칭됨 → 구체적 키워드를 위에.
_KR_NAME_KEYWORDS: tuple[tuple[str, str], ...] = (
    # ── SPV / 리츠 먼저 ──
    # 스팩(SPAC)은 실질 사업 없는 Special Purpose Vehicle. KRX "금융" 매핑을 그대로 두면
    # banks 버킷 오염원이 됨. other로 격리. (우선순위상 다른 키워드보다 먼저.)
    ("스팩", SECTOR_OTHER),
    ("SPAC", SECTOR_OTHER),
    ("리츠", SECTOR_REAL_ESTATE),
    ("REIT", SECTOR_REAL_ESTATE),

    # 반도체
    ("반도체", SECTOR_SEMICONDUCTORS),  # 서울/제주/SFA/아이티엠반도체 등

    # 바이오 vs 제약·CMO 세분화
    # "바이오로직스"·"바이오제약"은 CMO·제약 성격 — pharma로 분기 (순서상 "바이오"보다 앞)
    ("바이오로직스", SECTOR_PHARMA_MEDTECH),
    ("바이오제약", SECTOR_PHARMA_MEDTECH),   # 동구바이오제약 등
    ("바이오", SECTOR_BIOTECH),              # SK바이오사이언스/팜, 셀트리온, 삼성바이오 등
    ("제약", SECTOR_PHARMA_MEDTECH),
    ("파마", SECTOR_PHARMA_MEDTECH),

    # 금융 세분화
    ("증권", SECTOR_CAPITAL_MARKETS),
    ("자산운용", SECTOR_CAPITAL_MARKETS),
    ("투자증권", SECTOR_CAPITAL_MARKETS),
    ("은행", SECTOR_BANKS),
    ("금융지주", SECTOR_BANKS),
    ("생명보험", SECTOR_INSURANCE),
    ("손해보험", SECTOR_INSURANCE),
    ("화재", SECTOR_INSURANCE),          # 삼성화재·DB화재 — 단 "화재보험"만 의도
    ("생명", SECTOR_INSURANCE),          # 삼성생명·한화생명
    ("카드", SECTOR_CAPITAL_MARKETS),    # 삼성카드·현대카드
    ("캐피탈", SECTOR_CAPITAL_MARKETS),

    # 지주사
    ("홀딩스", SECTOR_HOLDING_CO),
    ("지주", SECTOR_HOLDING_CO),
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


def _kr_ticker_override(ticker: str | None) -> str | None:
    """한국 종목 티커 화이트리스트 매칭."""
    if not ticker:
        return None
    return _KR_TICKER_OVERRIDES.get(ticker.strip())


def _kr_name_override(asset_name: str | None) -> str | None:
    """한국 종목명 키워드 매칭."""
    if not asset_name:
        return None
    name = asset_name.strip()
    if not name:
        return None
    for needle, sector in _KR_NAME_KEYWORDS:
        if needle in name:
            return sector
    return None


def _is_krx_market(market: str | None) -> bool:
    """market 코드가 한국 시장인지."""
    if not market:
        return False
    return market.upper() in ("KOSPI", "KOSDAQ", "KONEX", "KRX")


def normalize_sector(
    *,
    ticker: str | None = None,
    asset_name: str | None = None,
    market: str | None = None,
    sector_krx: str | None = None,
    sector_gics: str | None = None,
    industry: str | None = None,
    warn_on_miss: bool = True,
) -> str:
    """모든 입력 신호를 종합해 sector_norm 결정.

    우선순위 (높음→낮음):
        1. KR 티커 화이트리스트 (_KR_TICKER_OVERRIDES)
        2. KR 종목명 키워드 (_KR_NAME_KEYWORDS, market이 KRX일 때만)
        3. industry 키워드 (영문 위주 — US 종목 주효)
        4. KRX 업종 대분류
        5. GICS sector
        6. SECTOR_OTHER (+ 경고)

    한국 종목의 경우 industry 컬럼이 대부분 NULL이라 `_INDUSTRY_OVERRIDES`가
    구조적으로 발동하지 않는 문제를 1·2 단계로 우회한다.

    Args:
        ticker: 종목 코드 (6자리 숫자 또는 US ticker)
        asset_name: 종목명 (한글/영문)
        market: 'KOSPI'/'KOSDAQ'/'NASDAQ'/'NYSE' 등. KR override 한정 조건.
        sector_krx: pykrx 업종명 (한글)
        sector_gics: yfinance sector (영문)
        industry: yfinance industry 또는 KRX 세부업종 (세분화 보강용)
        warn_on_miss: 매핑 실패 시 logger.warning 발생 여부

    Returns:
        sector_norm 문자열 (항상 ALL_SECTOR_NORMS 중 하나).
    """
    # 1) KR 티커 화이트리스트 — 가장 강한 신호
    tk_over = _kr_ticker_override(ticker)
    if tk_over:
        return tk_over

    # 2) KR 종목명 키워드 — market이 KRX일 때 or market 모름 + 한글 포함
    is_krx = _is_krx_market(market)
    if is_krx or (market is None and asset_name and any("가" <= c <= "힣" for c in asset_name)):
        name_over = _kr_name_override(asset_name)
        if name_over:
            return name_over

    # 3) industry 키워드 매칭 (반도체/소프트웨어/biotech/banks/chemicals 등)
    override = _industry_override(industry)
    if override:
        return override

    # 4) KRX 업종 우선 (한국 시장 비중이 큼)
    krx = normalize_krx_sector(sector_krx)
    if krx:
        return krx

    # 5) GICS sector
    gics = normalize_gics_sector(sector_gics)
    if gics:
        return gics

    # 6) fallback
    if warn_on_miss and (sector_krx or sector_gics or industry):
        get_logger("sector_mapping").warning(
            "섹터 매핑 누락 → other "
            f"(ticker={ticker!r}, name={asset_name!r}, "
            f"krx={sector_krx!r}, gics={sector_gics!r}, industry={industry!r})"
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
