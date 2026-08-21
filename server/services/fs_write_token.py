"""FS 验证型写的短时 HMAC 凭证（validate → 本地写回 → commit）。

职责边界：

- **签发**（validate 端点）：校验通过后把 ``action / project / slug / fields``
  连同 actor / projection revision / nonce / 证据摘要与过期时间打成
  canonical JSON，HMAC-SHA256 签名。
- **验证**（commit 端点）：验签 + 过期 + 归属（project/slug/action 与请求
  一致），防伪造与跨话题重放。

安全模型说明：token 防**伪造 verdict**，不防恶意客户端——workspace 文件
主权本就在 Agent 侧，能写 round 文件的人本来就能改 index.md。验证型写的
价值是对诚实 Agent 的流程门禁（ack 满员、状态机合法）。

密钥解析顺序（见 Settings.fs_write_token_secret 文档）：
``MAP_FS_WRITE_TOKEN_SECRET`` > ``MAP_WEBHOOK_SECRET_ENCRYPTION_KEY`` >
进程级随机密钥。多 worker 部署必须显式设置，否则跨进程签发的 token 无法
验证（validate 与 commit 同进程内完成时无影响）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from server.config import get_settings

_TOKEN_TTL_SECONDS = 600
_VERSION = "v1"

# 进程级回退密钥：未配置任何密钥时的最后手段（单进程部署下 validate 与
# commit 同进程完成，可正常验签；跨进程则要求显式配置）。
_PROCESS_SECRET = secrets.token_bytes(32)


class FsWriteTokenError(Exception):
    """token 验证失败（伪造 / 过期 / 归属不符）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(f"fs write token invalid: {reason}")
        self.reason = reason


def _secret() -> bytes:
    configured = get_settings().fs_write_token_secret
    if configured:
        return configured.encode("utf-8")
    fernet = get_settings().webhook_secret_encryption_key
    if fernet:
        return fernet.encode("utf-8")
    return _PROCESS_SECRET


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def evidence_digest(evidence_json: str | None) -> str:
    """validate 请求携带的客户端证据摘要（无证据时空串占位）。"""
    if not evidence_json:
        return ""
    return hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()


def sign_write_token(
    *,
    action: str,
    project_id: str,
    slug: str,
    fields: dict[str, str],
    agent_id: str = "",
    base_revision: int = 0,
    nonce: str | None = None,
    evidence_sha256: str = "",
    ttl_seconds: int = _TOKEN_TTL_SECONDS,
) -> tuple[str, datetime]:
    """签发写回凭证，返回 ``(token, expires_at)``。"""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    payload: dict[str, Any] = {
        "v": _VERSION,
        "action": action,
        "project_id": str(project_id),
        "slug": slug,
        "fields": dict(fields),
        "agent_id": str(agent_id),
        "base_revision": int(base_revision),
        "nonce": nonce or uuid.uuid4().hex,
        "ev": evidence_sha256,
        "exp": int(expires_at.timestamp()),
    }
    body = _b64encode(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    sig = _b64encode(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{_VERSION}.{body}.{sig}", expires_at


def verify_write_token(
    token: str,
    *,
    action: str,
    project_id: str,
    slug: str,
    agent_id: str = "",
) -> dict[str, Any]:
    """验签并校验归属与过期；通过则返回 token payload（含 fields/ev/exp）。"""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _VERSION:
        raise FsWriteTokenError("malformed token")
    body, sig = parts[1], parts[2]
    expected = _b64encode(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise FsWriteTokenError("bad signature")
    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        raise FsWriteTokenError("undecodable payload") from err
    if payload.get("action") != action or payload.get("slug") != slug:
        raise FsWriteTokenError("token bound to another action/topic")
    if str(payload.get("project_id")) != str(project_id):
        raise FsWriteTokenError("token bound to another project")
    if str(payload.get("agent_id") or "") != str(agent_id):
        raise FsWriteTokenError("token bound to another actor")
    if not str(payload.get("nonce") or ""):
        raise FsWriteTokenError("token has no one-time nonce")
    exp = int(payload.get("exp") or 0)
    if datetime.now(timezone.utc).timestamp() >= exp:
        raise FsWriteTokenError("token expired")
    return cast(dict[str, Any], payload)
