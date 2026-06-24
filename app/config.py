"""
app/config.py

All application settings are read from environment variables.
Secrets (API keys, tokens) must never be hardcoded here.

Usage:
    from app.config import settings
    print(settings.database_url)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Reads configuration from a .env file (or real environment variables).
    Every variable here must also exist in .env.example.
    """

    # Database
    database_url: str = "sqlite:///./data/nifty_premarket.db"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # LLM (used from Milestone 4 onwards)
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"

    # Application
    app_env: str = "development"
    log_level: str = "INFO"

    # Scheduler timezone
    scheduler_timezone: str = "Asia/Kolkata"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",          # ignore any extra vars in .env
    )


# Single shared instance — import this everywhere
settings = Settings()
