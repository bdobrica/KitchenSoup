"""Operator configuration for the web application."""

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KITCHENSOUP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="KitchenSoup", min_length=1, max_length=100)
    check_dependencies: bool = False
    postgres_host: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "kitchensoup"
    postgres_user: str = "kitchensoup"
    postgres_password: SecretStr = SecretStr("")
    valkey_url: str = "redis://valkey:6379/0"
    rustfs_health_url: str = "http://rustfs:9000/health/ready"

    @model_validator(mode="after")
    def require_database_password(self) -> "Settings":
        if self.check_dependencies and not self.postgres_password.get_secret_value():
            raise ValueError("A PostgreSQL password is required when dependency checks are enabled")
        return self
