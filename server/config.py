from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAP_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/map.db"
    api_prefix: str = "/api/v1"
    port: int = 8000
    debug: bool = False
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # map/ 文件夹事实源的内容根目录名（相对 project.workspace_path）。
    # fs plane（server/api/fs.py）实时解析该目录；改名走 MAP_CONTENT_ROOT。
    content_root: str = "map"

    # f873c287 I1(c): tunable stale-open-topic threshold (minutes). CLI /
    # waker / API can override via ``MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES``
    # env var (e.g. ``MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES=5`` for
    # integration tests). Default 30 minutes matches the legacy
    # ``STALE_OPEN_TOPIC_THRESHOLD`` constant.
    stale_open_topic_threshold_minutes: int = 30

    # c9281d86 PR3: Fernet key for at-rest encryption of sensitive
    # columns (currently ``Webhook.secret``). Must be a 32-byte
    # urlsafe-base64-encoded key — generate with
    # ``python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'``.
    # No default: missing key raises ``SecretEncryptionKeyMissing`` at
    # first use, so a silent fallback to deriving-from-other-secret
    # never makes "rotate the key" ambiguous.
    webhook_secret_encryption_key: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
