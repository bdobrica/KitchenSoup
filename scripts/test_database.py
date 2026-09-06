"""Run integration tests in disposable, loopback-only PostgreSQL/RustFS containers."""

import os
import secrets
import subprocess
import sys
import time
from urllib.request import urlopen
from uuid import uuid4


def main() -> int:
    name = f"kitchensoup-db-test-{uuid4().hex[:12]}"
    storage_name = f"{name}-s3"
    environment = os.environ.copy()
    environment["POSTGRES_PASSWORD"] = secrets.token_hex(24)
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--env",
                "POSTGRES_PASSWORD",
                "--env",
                "POSTGRES_DB=kitchensoup_test",
                "--publish",
                "127.0.0.1::5432",
                "--tmpfs",
                "/var/lib/postgresql",
                "postgres:18.6-bookworm",
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        for _ in range(60):
            status = subprocess.run(
                [
                    "docker",
                    "exec",
                    name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                    "-d",
                    "kitchensoup_test",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if status.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable PostgreSQL did not become ready")
        port = (
            subprocess.check_output(["docker", "port", name, "5432"], text=True)
            .strip()
            .rsplit(":", 1)[1]
        )
        environment.update(
            {
                "KITCHENSOUP_POSTGRES_HOST": "127.0.0.1",
                "KITCHENSOUP_POSTGRES_PORT": port,
                "KITCHENSOUP_POSTGRES_USER": "postgres",
                "KITCHENSOUP_POSTGRES_DB": "kitchensoup_test",
                "KITCHENSOUP_POSTGRES_PASSWORD": environment.pop("POSTGRES_PASSWORD"),
                "KITCHENSOUP_DB_TEST": "1",
            }
        )
        environment["RUSTFS_ACCESS_KEY"] = secrets.token_hex(16)
        environment["RUSTFS_SECRET_KEY"] = secrets.token_hex(24)
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                storage_name,
                "--env",
                "RUSTFS_ACCESS_KEY",
                "--env",
                "RUSTFS_SECRET_KEY",
                "--env",
                "RUSTFS_VOLUMES=/data",
                "--env",
                "RUSTFS_CONSOLE_ENABLE=false",
                "--publish",
                "127.0.0.1::9000",
                "--tmpfs",
                "/data:uid=10001,gid=10001",
                "--tmpfs",
                "/logs:uid=10001,gid=10001",
                "rustfs/rustfs:1.0.0-beta.12",
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        storage_port = (
            subprocess.check_output(["docker", "port", storage_name, "9000"], text=True)
            .strip()
            .rsplit(":", 1)[1]
        )
        endpoint = f"http://127.0.0.1:{storage_port}"
        for _ in range(60):
            try:
                with urlopen(endpoint + "/health/ready", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Disposable RustFS did not become ready")
        environment.update(
            {
                "KITCHENSOUP_S3_ENDPOINT": endpoint,
                "KITCHENSOUP_S3_PUBLIC_ENDPOINT": endpoint,
                "KITCHENSOUP_S3_ACCESS_KEY": environment.pop("RUSTFS_ACCESS_KEY"),
                "KITCHENSOUP_S3_SECRET_KEY": environment.pop("RUSTFS_SECRET_KEY"),
                "KITCHENSOUP_S3_BUCKET": "kitchensoup-test",
            }
        )
        return subprocess.call(
            [sys.executable, "-m", "pytest", "tests/integration"], env=environment
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", storage_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["docker", "rm", "--force", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


if __name__ == "__main__":
    sys.exit(main())
