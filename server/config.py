from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAP_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/map.db"
    api_prefix: str = "/api/v1"
    port: int = 8000
    debug: bool = False
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Default content_root for *new* projects when the client omits it.
    # Existing projects store content_root on the Project row (P1); scan
    # paths never fall back to this env var per project.
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

    # FS 验证型写（validate → 本地写回 → commit）的 HMAC 签名密钥。
    # 解析顺序：本配置 > webhook_secret_encryption_key > 进程级随机密钥。
    # 单进程部署（uvicorn 默认 / SQLite）validate 与 commit 同进程，随机
    # 回退即可工作；多 worker（Postgres + --workers N）必须显式设置，
    # 否则跨进程签发的 commit token 无法验证。
    fs_write_token_secret: str | None = None

    # Serve the bundled React SPA from map-server (same origin as /api).
    # Disable with MAP_SERVE_WEB=false when another process (Vite / nginx)
    # already serves the board. MAP_WEB_DIST overrides the packaged
    # server/web_dist directory.
    serve_web: bool = True
    web_dist: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
