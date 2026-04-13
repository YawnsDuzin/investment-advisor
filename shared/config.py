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
    max_turns: int = field(default_factory=lambda: int(os.getenv("MAX_TURNS", "2")))
    top_themes: int = field(default_factory=lambda: int(os.getenv("TOP_THEMES", "2")))
    top_stocks_per_theme: int = field(default_factory=lambda: int(os.getenv("TOP_STOCKS_PER_THEME", "2")))
    enable_stock_analysis: bool = field(default_factory=lambda: _env_bool("ENABLE_STOCK_ANALYSIS", True))
    enable_stock_data: bool = field(default_factory=lambda: _env_bool("ENABLE_STOCK_DATA", True))


@dataclass
class AppConfig:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    max_turns: int = field(default_factory=lambda: int(os.getenv("MAX_TURNS", "2")))  # 하위호환
