"""
Centralized configuration loaded from environment variables.

All settings are validated by Pydantic at startup — if a required value is
missing or malformed, the app refuses to start with a clear error message.

Usage:
    from app.config import settings
    settings.postgres_dsn  # → "postgresql+asyncpg://sparki:..."
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All Sparki webapp settings, loaded from env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",                  # optional fallback for local runs
        env_file_encoding="utf-8",
        case_sensitive=False,             # POSTGRES_USER == postgres_user
        extra="ignore",                   # ignore vars we don't declare
    )

    # ─── General ─────────────────────────────────────────────────────
    environment: Literal["development", "staging", "production"] = "development"
    tz: str = "Europe/Brussels"

    # ─── PostgreSQL ──────────────────────────────────────────────────
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "sparki"
    postgres_user: str = "sparki"
    postgres_password: SecretStr

    # ─── InfluxDB ────────────────────────────────────────────────────
    influxdb_host: str = "influxdb"
    influxdb_port: int = 8086
    influxdb_org: str = "sparki"
    influxdb_bucket: str = "energy"
    influxdb_token: SecretStr

    # ─── Keycloak ────────────────────────────────────────────────────
    keycloak_internal_url: str = "http://keycloak:8080"
    keycloak_public_url: str = "http://localhost:8080"
    keycloak_realm: str = "sparki"
    keycloak_client_id: str = "webapp"
    keycloak_client_secret: SecretStr

    # ─── Webapp ──────────────────────────────────────────────────────
    webapp_host: str = "0.0.0.0"  # noqa: S104  # binding to all interfaces in container is fine
    webapp_port: int = 8000
    webapp_secret_key: SecretStr = Field(
        ..., min_length=32,
        description="Used for signing session cookies. Must be ≥32 chars.",
    )
    fernet_key: SecretStr = Field(
        ...,
        description="Symmetric key for encrypting Sigencloud credentials at-rest.",
    )

    # ─── Sigencloud (set per organization in DB, but base URL is global) ──
    sigencloud_base_url: str = "https://api-eu.sigencloud.com"
    sigencloud_poll_interval_seconds: int = 300  # 5 min — Sigencloud rate-limit

    # ─── ENTSO-E ─────────────────────────────────────────────────────
    entsoe_api_token: SecretStr = SecretStr("")
    entsoe_bidding_zone: str = "10YBE----------2"

    # ─── Computed properties ─────────────────────────────────────────
    @computed_field  # type: ignore[prop-decorator]
    @property
    def postgres_dsn(self) -> str:
        """Async DSN for SQLAlchemy + asyncpg."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}"
            f":{self.postgres_password.get_secret_value()}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def postgres_dsn_sync(self) -> str:
        """Sync DSN for Alembic migrations."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}"
            f":{self.postgres_password.get_secret_value()}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def influxdb_url(self) -> str:
        return f"http://{self.influxdb_host}:{self.influxdb_port}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance.

    Cached so we don't re-parse env vars on every import.
    Use this function (not the global below) in production code.
    """
    return Settings()  # type: ignore[call-arg]


# Convenience: a module-level instance for simple imports.
# Trade-off: instantiation happens at import time, so unit tests must
# patch env vars before importing app.config.
settings = get_settings()
