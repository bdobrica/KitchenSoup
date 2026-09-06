"""Generate local-only credentials without replacing existing values or printing secrets."""

import os
import re
import secrets
from pathlib import Path


def initialize(path: Path, example: Path) -> None:
    content = path.read_text() if path.exists() else example.read_text()
    for name in ("KITCHENSOUP_POSTGRES_PASSWORD", "RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY"):
        pattern = rf"^{name}=.*$"
        match = re.search(pattern, content, flags=re.MULTILINE)
        if match and match.group().split("=", 1)[1].strip() not in ("", '""', "''"):
            continue
        entry = f"{name}={secrets.token_hex(24)}"
        if match:
            content = re.sub(pattern, entry, content, flags=re.MULTILINE)
        else:
            content = content.rstrip("\n") + "\n" + entry + "\n"
    # Restrict newly created files before writing credentials.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(content)


if __name__ == "__main__":
    initialize(Path(".env"), Path(".env.example"))
    print("Local configuration ready in .env")
