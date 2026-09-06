"""Serve the opt-in host application using local Compose storage, without printing secrets."""

import uvicorn
from pydantic import Field, SecretStr

from app.config import Settings
from app.main import create_app


class LocalSettings(Settings):
    rustfs_access_key: SecretStr = Field(validation_alias="RUSTFS_ACCESS_KEY")
    rustfs_secret_key: SecretStr = Field(validation_alias="RUSTFS_SECRET_KEY")


local = LocalSettings()
settings = Settings(
    postgres_host="127.0.0.1",
    storage_enabled=True,
    s3_endpoint="http://127.0.0.1:9000",
    s3_access_key=local.rustfs_access_key,
    s3_secret_key=local.rustfs_secret_key,
    local_training_enabled=True,
    check_dependencies=False,
)
uvicorn.run(create_app(settings), host="127.0.0.1", port=8000)
