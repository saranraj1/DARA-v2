"""
DARA — Application Settings
Reads all configuration from environment variables / .env file.
Uses Pydantic v2 BaseSettings for type-safe, validated config.
"""
from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Primary
    groq_api_key: str = Field(..., description="Groq API key (primary LLM)")
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq model name",
    )
    # Fallback
    google_api_key: str = Field(..., description="Google Gemini API key (fallback)")
    gemini_flash_model: str = Field(default="gemini-1.5-flash")
    gemini_pro_model: str = Field(default="gemini-1.5-pro")
    # Local
    ollama_base_url: AnyHttpUrl = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="deepseek-coder-v2:16b")
    llm_mode: Literal["groq", "local", "gemini"] = Field(default="groq")
    llm_model_name: str = Field(default="llama-3.3-70b-versatile")
    # HuggingFace
    huggingface_api_token: str = Field(default="")
    embedding_model: str = Field(default="all-mpnet-base-v2")


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_url: str = Field(
        default="postgresql+asyncpg://dara:dara_dev@localhost:5432/dara"
    )
    redis_url: str = Field(default="redis://:dara_dev@localhost:6379/0")
    qdrant_url: str = Field(default="http://localhost:6333")
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="dara_dev")
    elasticsearch_url: str = Field(default="http://localhost:9200")
    minio_url: str = Field(default="http://localhost:9000")
    minio_root_user: str = Field(default="dara")
    minio_root_password: str = Field(default="dara_dev_secret")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_postgres_url(self) -> str:
        """Synchronous PostgreSQL URL for Alembic migrations."""
        return self.postgres_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )


class GitHubSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    github_app_id: str = Field(..., description="GitHub App ID")
    github_private_key_path: str = Field(
        default="",
        description="Path to GitHub App private key (.pem). Auto-detected if empty.",
    )
    github_webhook_secret: str = Field(..., description="GitHub webhook HMAC secret")
    github_installation_id: str = Field(..., description="GitHub App installation ID")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_pem_path(self) -> str:
        """Resolve the .pem path: explicit env var > auto-detect in config/ > fallback."""
        import pathlib
        if self.github_private_key_path and pathlib.Path(self.github_private_key_path).exists():
            return self.github_private_key_path
        # Auto-detect: find first .pem in config/
        config_dir = pathlib.Path("config")
        pems = sorted(config_dir.glob("*.pem"))
        if pems:
            return str(pems[0])
        return "config/github_app.pem"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def github_private_key(self) -> str:
        """Load the private key from file at runtime."""
        try:
            with open(self.resolved_pem_path) as f:
                return f.read()
        except FileNotFoundError:
            return ""


class SlackSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    slack_bot_token: str = Field(..., description="Slack Bot OAuth token (xoxb-)")
    slack_signing_secret: str = Field(..., description="Slack signing secret")
    slack_client_secret: str = Field(default="")
    slack_alert_channel: str = Field(
        default="#dara-alerts", description="Default Slack channel for alerts"
    )

    @field_validator("slack_bot_token")
    @classmethod
    def validate_bot_token(cls, v: str) -> str:
        if v and not v.startswith("xoxb-"):
            raise ValueError("SLACK_BOT_TOKEN must start with 'xoxb-'")
        return v


class NgrokSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ngrok_domain: str = Field(default="")
    webhook_base_url: str = Field(default="http://localhost:8000")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def github_webhook_url(self) -> str:
        return f"{self.webhook_base_url}/api/v1/webhooks/github"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def slack_events_url(self) -> str:
        return f"{self.webhook_base_url}/api/v1/slack/events"


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auto_merge_confidence_threshold: float = Field(
        default=0.88,
        ge=0.0,
        le=1.0,
        description="Minimum confidence to be eligible for auto-merge",
    )
    max_context_tokens: int = Field(default=8000, ge=1000, le=32000)
    max_pipeline_retries: int = Field(default=3, ge=1, le=10)
    sandbox_timeout_seconds: int = Field(default=120, ge=10, le=600)
    max_files_for_auto_merge: int = Field(default=1)
    max_lines_for_auto_merge: int = Field(default=15)
    llm_cache_ttl_seconds: int = Field(default=3600)
    embedding_batch_size: int = Field(default=32)
    context_recency_window_hours: int = Field(default=72)
    git_commit_history_days: int = Field(default=30)


class Settings(
    LLMSettings,
    DatabaseSettings,
    GitHubSettings,
    SlackSettings,
    NgrokSettings,
    PipelineSettings,
):
    """
    Master settings class — combines all setting groups.
    All values read from environment or .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App metadata
    app_name: str = Field(default="DARA")
    app_version: str = Field(default="0.1.0")
    environment: Literal["development", "staging", "production"] = Field(
        default="development"
    )
    debug: bool = Field(default=False)
    api_secret_key: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        description="Secret key for API token signing",
    )
    admin_api_key: str = Field(
        default="dara-admin-secret",
        description="X-Admin-Token header value for admin API protection",
    )
    rate_limit_per_minute: int = Field(
        default=60,
        description="Max requests per minute per API key (sliding window)",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    cors_origins: list[str] = Field(default=["http://localhost:3000"])



    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached Settings instance.
    Call get_settings.cache_clear() in tests to reset.
    """
    return Settings()
