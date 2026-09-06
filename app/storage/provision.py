"""Local Compose bucket provisioning; never prints configuration or signed URLs."""

from app.config import Settings
from app.storage.s3 import S3ArtifactStore


def main() -> None:
    settings = Settings()
    store = S3ArtifactStore(settings)
    try:
        store.provision(
            [origin.strip() for origin in settings.s3_allowed_origins.split(",") if origin.strip()]
        )
    finally:
        store.close()
    print("Artifact bucket and browser CORS configured")


if __name__ == "__main__":
    main()
