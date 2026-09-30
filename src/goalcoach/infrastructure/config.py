import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Disable third-party telemetry globally for clean offline and test execution
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


class Settings(BaseSettings):
    """Configuration for hosted/local model switching and optional infrastructure."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GOALCOACH_", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./goalcoach.db"
    content_database_url: str = "sqlite:///./data/database1/goalcoach_hsk1_learning.db"
    content_database_path: str = "./data/database1/goalcoach_hsk1_learning.db"
    learner_database_path: str = "./goalcoach.db"
    planning_item_minutes: int = Field(default=5, gt=0, le=120)
    enable_prerequisites: bool = False

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=30, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_cost_usd_per_week: float = Field(default=50, gt=0)
    offline_llm_fallback: bool = True
    fallback_llm_base_url: str = "http://localhost:11434/v1"
    fallback_llm_model: str | None = None
    enable_ollama_fallback: bool = False

    auth_secret_key: str = "goalcoach-dev-insecure-secret"
    auth_token_expiry_minutes: int = Field(default=60 * 24 * 7, ge=5, le=60 * 24 * 30)
    auth_require_token: bool = False

    log_level: str = "INFO"
    log_format: str = "auto"
    log_to_file: bool = True
    log_file_path: str = "./logs/goalcoach.log"
    log_slow_query_threshold_ms: float = 25.0
