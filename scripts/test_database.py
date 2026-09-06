"""Run the PostgreSQL suite in a disposable, loopback-only container."""

import os
import secrets
import subprocess
import sys
import time
from uuid import uuid4


def main() -> int:
    name = f"kitchensoup-db-test-{uuid4().hex[:12]}"
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
        return subprocess.call(
            [sys.executable, "-m", "pytest", "tests/integration"], env=environment
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


if __name__ == "__main__":
    sys.exit(main())
