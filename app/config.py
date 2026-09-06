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
    soup_ingestion_url: str = ""
    storage_enabled: bool = False
    s3_endpoint: str = "http://rustfs:9000"
    s3_public_endpoint: str = "http://127.0.0.1:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "kitchensoup"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    upload_max_bytes: int = Field(default=1024 * 1024 * 1024, ge=1, le=5 * 1024**3)
    storage_url_ttl: int = Field(default=300, ge=1, le=3600)

    @model_validator(mode="after")
    def require_database_password(self) -> "Settings":
        if self.check_dependencies and not self.postgres_password.get_secret_value():
            raise ValueError("A PostgreSQL password is required when dependency checks are enabled")
        if self.storage_enabled and not (
            self.s3_access_key.get_secret_value() and self.s3_secret_key.get_secret_value()
        ):
            raise ValueError("Storage credentials are required when storage is enabled")
        return self
