"""설정 모듈 — .env 파일에서 DB 접속 정보를 읽어옴"""
import os
from dataclasses import dataclass, field
from pathlib import Path

# .env 파일 로드 (프로젝트 루트 기준)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


@dataclass
class DatabaseConfig:
    host: str = field(default_factory=lambda: os.getenv("DB_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("DB_PORT", "5432")))
    dbname: str = field(default_factory=lambda: os.getenv("DB_NAME", "investment_advisor"))
    user: str = field(default_factory=lambda: os.getenv("DB_USER", "postgres"))
    password: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", "postgres"))

    @property
    def dsn(self) -> str:
        return f"host={self.host} port={self.port} dbname={self.dbname} user={self.user} password={self.password}"


@dataclass
class NewsConfig:
    """RSS 피드 소스 설정"""
    feeds: dict[str, list[str]] = field(default_factory=lambda: {
        # 글로벌 종합 뉴스
        "global": [
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://feeds.reuters.com/reuters/worldNews",
        ],
        # 경제·금융·시장
        "finance": [
            "https://feeds.reuters.com/reuters/businessNews",
            "https://feeds.bloomberg.com/markets/news.rss",
            "https://www.cnbc.com/id/10001147/device/rss/rss.html",
        ],
        # 기술·AI·반도체
        "technology": [
            "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
            "https://feeds.arstechnica.com/arstechnica/technology-lab",
        ],
        # 에너지·원자재
        "commodities": [
            "https://oilprice.com/rss/main",
        ],
        # 한국 뉴스
        "korea": [
            "https://www.hankyung.com/feed/economy",
            "https://www.hankyung.com/feed/stock",
        ],
        # 선행 지표 — 산업 전문·규제·공급망 얼리 시그널
        "early_signals": [
            "https://www.federalregister.gov/documents/search.atom?conditions%5Btype%5D=RULE",  # 미국 연방관보 (규제 선행)
            "https://www.digitimes.com/rss/daily_news.xml",  # 아시아 IT 공급망 선행 지표
        ],
        # 한국 산업·M&A·자본시장 선행
        "korea_early": [
            "https://www.etnews.com/rss/Section901.xml",  # 전자신문 (산업 기술)
            "https://www.thebell.co.kr/rss/rss_news_all.xml",  # 더벨 (M&A/자본시장)
        ],
    })
    max_articles_per_feed: int = field(default_factory=lambda: int(os.getenv("MAX_ARTICLES_PER_FEED", "5")))


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass
class AnalyzerConfig:
    """멀티스테이지 분석 파이프라인 설정"""
    max_turns: int = field(default_factory=lambda: int(os.getenv("MAX_TURNS", "1")))
    top_themes: int = field(default_factory=lambda: int(os.getenv("TOP_THEMES", "2")))
    top_stocks_per_theme: int = field(default_factory=lambda: int(os.getenv("TOP_STOCKS_PER_THEME", "2")))
    enable_stock_analysis: bool = field(default_factory=lambda: _env_bool("ENABLE_STOCK_ANALYSIS", True))
    enable_stock_data: bool = field(default_factory=lambda: _env_bool("ENABLE_STOCK_DATA", True))
    # 모델 설정 — 용도별 분리 (비용 최적화)
    model_analysis: str = field(default_factory=lambda: os.getenv("MODEL_ANALYSIS", "claude-sonnet-4-6"))
    model_translate: str = field(default_factory=lambda: os.getenv("MODEL_TRANSLATE", "claude-haiku-4-5-20251001"))
    # SDK 쿼리 타임아웃 (초) — 서버 부하 시 첫 토큰 지연이 길어질 수 있음
    query_timeout: int = field(default_factory=lambda: int(os.getenv("QUERY_TIMEOUT", "900")))
    # 재분석 임계값 — 신규 뉴스가 이 수 미만이면 분석 스킵
    min_new_news: int = field(default_factory=lambda: int(os.getenv("MIN_NEW_NEWS", "5")))
    # SDK 동시 실행 수 제한 (Stage 1-B/Stage 2 병렬) — 기본 2
    sdk_concurrency: int = field(default_factory=lambda: int(os.getenv("SDK_CONCURRENCY", "2")))


@dataclass
class RecommendationConfig:
    """대시보드 Top Picks 추천 엔진 설정

    가중치 기반 스코어링으로 투자 제안을 순위화한다.
    모든 가중치는 환경변수로 오버라이드 가능.
    """
    # 가중치 (점수)
    w_conviction_high: int = field(default_factory=lambda: int(os.getenv("REC_W_CONVICTION_HIGH", "30")))
    w_stage2_done: int = field(default_factory=lambda: int(os.getenv("REC_W_STAGE2_DONE", "20")))
    w_discovery_early: int = field(default_factory=lambda: int(os.getenv("REC_W_DISCOVERY_EARLY", "15")))
    w_action_buy: int = field(default_factory=lambda: int(os.getenv("REC_W_ACTION_BUY", "10")))
    w_upside_high: int = field(default_factory=lambda: int(os.getenv("REC_W_UPSIDE_HIGH", "10")))
    w_upside_mid: int = field(default_factory=lambda: int(os.getenv("REC_W_UPSIDE_MID", "5")))
    w_theme_confidence_mult: int = field(default_factory=lambda: int(os.getenv("REC_W_THEME_CONF_MULT", "10")))
    w_streak_bonus: int = field(default_factory=lambda: int(os.getenv("REC_W_STREAK_BONUS", "5")))
    # 감점
    w_already_priced_penalty: int = field(default_factory=lambda: int(os.getenv("REC_W_PRICED_PENALTY", "15")))
    w_no_price_penalty: int = field(default_factory=lambda: int(os.getenv("REC_W_NOPRICE_PENALTY", "10")))
    # 임계값
    upside_high_threshold: float = field(default_factory=lambda: float(os.getenv("REC_UPSIDE_HIGH", "20.0")))
    upside_mid_threshold: float = field(default_factory=lambda: float(os.getenv("REC_UPSIDE_MID", "10.0")))
    momentum_overheated_pct: float = field(default_factory=lambda: float(os.getenv("REC_MOMENTUM_OVERHEAT", "20.0")))
    streak_days_threshold: int = field(default_factory=lambda: int(os.getenv("REC_STREAK_THRESHOLD", "3")))
    # 다양성 제약
    max_candidates: int = field(default_factory=lambda: int(os.getenv("REC_MAX_CANDIDATES", "15")))
    max_per_theme: int = field(default_factory=lambda: int(os.getenv("REC_MAX_PER_THEME", "2")))
    max_per_sector: int = field(default_factory=lambda: int(os.getenv("REC_MAX_PER_SECTOR", "3")))
    top_n_display: int = field(default_factory=lambda: int(os.getenv("REC_TOP_N_DISPLAY", "10")))
    # AI 재정렬 (Stage 3)
    enable_ai_rerank: bool = field(default_factory=lambda: _env_bool("REC_ENABLE_AI_RERANK", False))
    ai_rerank_top_n: int = field(default_factory=lambda: int(os.getenv("REC_AI_RERANK_TOP_N", "10")))
    ai_rerank_max_turns: int = field(default_factory=lambda: int(os.getenv("REC_AI_RERANK_MAX_TURNS", "2")))


@dataclass
class UniverseConfig:
    """Stock Universe 동기화 설정 (Phase 1a — recommendation-engine-redesign).

    스크리너가 LLM hallucination을 차단하기 위해 참조하는 검증된 종목 마스터.
    """
    krx_enabled: bool = field(default_factory=lambda: _env_bool("UNIVERSE_KRX_ENABLED", True))
    us_enabled: bool = field(default_factory=lambda: _env_bool("UNIVERSE_US_ENABLED", False))
    # 동기화 주기 (스케줄러/CLI에서 참조하는 힌트값. 실제 트리거는 systemd/cron이 담당)
    sync_price_schedule: str = field(default_factory=lambda: os.getenv("UNIVERSE_SYNC_PRICE_SCHEDULE", "daily"))
    sync_meta_schedule: str = field(default_factory=lambda: os.getenv("UNIVERSE_SYNC_META_SCHEDULE", "weekly"))
    # auto 모드에서 meta가 stale로 판단되는 경과 일수
    meta_stale_days: int = field(default_factory=lambda: int(os.getenv("UNIVERSE_META_STALE_DAYS", "7")))


@dataclass
class ScreenerConfig:
    """Universe-First Stage 1-B 분해 설정 (Phase 2 — recommendation-engine-redesign).

    enable_universe_first_b=False(기본) 시 기존 Stage 1-B(LLM이 ticker 자유 생성) 동작.
    True 시 Stage 1-B1(스펙 생성) → 1-B2(결정적 스크리너) → 1-B3(배치 분석) 분해 동작.
    """
    enable_universe_first_b: bool = field(
        default_factory=lambda: _env_bool("ENABLE_UNIVERSE_FIRST_B", False)
    )
    # Spec 매칭 0건/과소 시 fallback 재시도 횟수
    spec_screener_max_retries: int = field(
        default_factory=lambda: int(os.getenv("SPEC_SCREENER_MAX_RETRIES", "3"))
    )
    # 0건 매칭 시 market_cap_range 확장 비율 (%)
    spec_screener_fallback_expand_pct: int = field(
        default_factory=lambda: int(os.getenv("SPEC_SCREENER_FALLBACK_EXPAND_PCT", "50"))
    )
    # Stage 1-B1 스펙당 후보 최대 수 (기본 20)
    candidates_max: int = field(
        default_factory=lambda: int(os.getenv("SPEC_SCREENER_CANDIDATES_MAX", "20"))
    )
    # Stage 1-B3 배치 분석에 넘길 후보 수 (스크리너 결과 상위 N)
    stage1b3_top_n: int = field(
        default_factory=lambda: int(os.getenv("STAGE1B3_TOP_N", "20"))
    )


@dataclass
class AuthConfig:
    """JWT 인증 설정"""
    enabled: bool = field(default_factory=lambda: _env_bool("AUTH_ENABLED", False))
    jwt_secret_key: str = field(default_factory=lambda: os.getenv("JWT_SECRET_KEY", "INSECURE_DEFAULT_CHANGE_IN_PRODUCTION"))
    jwt_algorithm: str = field(default_factory=lambda: os.getenv("JWT_ALGORITHM", "HS256"))
    access_token_expire_minutes: int = field(default_factory=lambda: int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")))
    refresh_token_expire_days: int = field(default_factory=lambda: int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30")))
    admin_email: str = field(default_factory=lambda: os.getenv("ADMIN_EMAIL", "admin@example.com"))
    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", "changeme123"))
    cookie_secure: bool = field(default_factory=lambda: _env_bool("COOKIE_SECURE", False))


@dataclass
class AppConfig:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    recommendation: RecommendationConfig = field(default_factory=RecommendationConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    screener: ScreenerConfig = field(default_factory=ScreenerConfig)
    max_turns: int = field(default_factory=lambda: int(os.getenv("MAX_TURNS", "1")))  # 하위호환
