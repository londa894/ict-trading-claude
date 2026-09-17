"""Server-side settings. Provider secrets live only here (env / secret manager) and are never serialised."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.enums import Timeframe


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "postgresql+psycopg://fmcc:fmcc@localhost:5432/fmcc"
    redis_url: str = "redis://localhost:6379/0"
    market_data_provider: str = "unconfigured"
    market_data_api_key: SecretStr | None = None
    execution_timeframe: Timeframe = Timeframe.M5
    cors_origins: str = "http://localhost:3000"
    # Manual account profile (balance, limits, day state, user contract specs). Server-side, never committed.
    risk_profile_path: str = ""
    # Background alert monitor (runs inside the API process while it is up).
    alert_monitor_enabled: bool = True
    # AI assistant: deterministic explainer by default; an external model only with a server-side key.
    ai_provider: Literal["deterministic", "anthropic"] = "deterministic"
    anthropic_api_key: SecretStr | None = None
    ai_model: str = "claude-sonnet-5"
    ai_timeout_seconds: float = 30.0
    ai_share_account_data: bool = False
    # Economic calendar (news gate): unconfigured blocks every decision with NEWS_DATA_UNAVAILABLE.
    calendar_provider: Literal["unconfigured", "fixture", "file"] = "unconfigured"
    calendar_file_path: str = ""
    # Basic macro (DXY, yields, VIX): unconfigured leaves MACRO NOT_EVALUATED. Context only: it never blocks.
    macro_provider: Literal["unconfigured", "fixture", "file"] = "unconfigured"
    macro_file_path: str = ""
    # Journal (private manual records): unconfigured saves nothing; database uses DATABASE_URL (Postgres, or
    # sqlite:///path for a local file) after `alembic upgrade head`.
    journal_store: Literal["unconfigured", "database"] = "unconfigured"
    # Paper trading (broker-free simulation): unconfigured simulates nothing; database uses DATABASE_URL.
    paper_store: Literal["unconfigured", "database"] = "unconfigured"
    paper_monitor_enabled: bool = True
    # Backtesting (research replays): unconfigured runs nothing; database uses DATABASE_URL.
    backtest_store: Literal["unconfigured", "database"] = "unconfigured"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
