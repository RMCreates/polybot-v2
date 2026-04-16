from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "sqlite+aiosqlite:///./polymarket_dev.db"
    polygon_private_key: SecretStr = SecretStr("")
    polygon_wallet_address: str = "0x0000000000000000000000000000000000000000"
    clob_api_key: SecretStr = SecretStr("")
    clob_api_secret: SecretStr = SecretStr("")
    clob_api_passphrase: SecretStr = SecretStr("")

    gamma_api_url: str = "https://gamma-api.polymarket.com"
    clob_api_url: str = "https://clob.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"

    live_trading: bool = False

    market_poll_interval: int = 300
    orderbook_poll_interval: int = 60

    max_single_position_usd: float = 10.0
    max_favorable_position_usd: float = 20.0
    favorable_edge_threshold: float = 0.08
    favorable_confidence_threshold: float = 0.70
    max_total_exposure_usd: float = 100.0
    min_edge_threshold: float = 0.01
    min_liquidity_usd: float = 1000.0
    max_spread_pct: float = 0.05
    min_signal_confidence: float = 0.05

    # Time windows for each session (hours)
    min_hours_to_close: float = 2.0         # minimum time left — don't trade expiring soon
    daily_max_hours: float = 48.0           # daily session: closes within 48h
    weekly_min_hours: float = 48.0          # weekly sessions: at least 2 days out
    weekly_max_hours: float = 168.0         # weekly sessions: max 7 days out
    longterm_min_hours: float = 168.0       # long-term: minimum 7 days out
    longterm_max_hours: float = 8760.0      # long-term: max 1 year

    # Per-session capital
    session_capital_usd: float = 100.0       # per weekly session × 3
    daily_session_capital_usd: float = 25.0  # daily pocket
    longterm_session_capital_usd: float = 100.0  # long-term pocket
    daily_max_trades: int = 3                # max daily pocket trades per calendar day

    signal_provider: str = "combined_signal"

    # External API keys (optional — bot degrades gracefully without them)
    brave_api_key: SecretStr = SecretStr("")
    news_api_key: SecretStr = SecretStr("")
    metaculus_api_key: SecretStr = SecretStr("")

    # Kalshi integration (public API, no auth needed)
    kalshi_api_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    kalshi_cache_ttl_seconds: int = 300
    kalshi_similarity_threshold: float = 0.45
    daily_recommendation_count: int = 3

    # Telegram alerts (optional — disabled if not configured)
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    # Capital management
    starting_capital_usd: float = 100.0  # initial deposit — never changes, not a ceiling

    # Live executor safety
    dry_run_orders: bool = False  # True = sign + log order, but do NOT submit to CLOB

settings = Settings()
