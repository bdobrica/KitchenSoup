"""Run an opt-in host training process using local Compose infrastructure."""

import argparse

import uvicorn
from pydantic import Field, SecretStr

from app.background import main as background_main
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
    kubernetes_execution_enabled=local.kubernetes_execution_enabled,
    kubernetes_secret_directory=local.kubernetes_secret_directory,
    kubernetes_env_names=local.kubernetes_env_names,
    kubernetes_staging_workspace=local.kubernetes_staging_workspace,
    ssh_execution_enabled=local.ssh_execution_enabled,
    ssh_known_hosts=local.ssh_known_hosts,
    ssh_secret_directory=local.ssh_secret_directory,
    ssh_env_names=local.ssh_env_names,
    ssh_staging_workspace=local.ssh_staging_workspace,
    local_deployment_enabled=local.local_deployment_enabled,
    local_transformations_enabled=local.local_transformations_enabled,
    check_dependencies=False,
    valkey_url=(
        "redis://127.0.0.1:6379/0"
        if local.valkey_url == "redis://valkey:6379/0"
        else local.valkey_url
    ),
)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--role", choices=("web", "worker", "reconciler"), default="web")
args = parser.parse_args()
if args.role == "web":
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8000)
else:
    background_main(settings, args.role)
