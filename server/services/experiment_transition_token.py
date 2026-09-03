"""实验 lifecycle transition 的短时 HMAC 凭证（实验B 24f3e565 / B1）。

与 topic 域 ``fs_write_token`` 同构：validate 端点把七元组
（project / experiment / actor / from-phase / to-phase / base-revision /
workspace-fingerprint）连同 nonce 与过期时间打成 canonical JSON 后
HMAC-SHA256 签名；commit 端点验签 + 过期 + 七元组归属校验，任一不匹配
即拒绝。安全模型亦同——token 防伪造 verdict，不防恶意客户端（能调
transition API 的 agent 本就能直接调单体端点；价值是对诚实 Agent 的
流程门禁与可恢复性）。

密钥解析复用 ``fs_write_token._secret``（同一配置链），避免两套密钥源
漂移；多 worker 部署要求与 fs 域一致（显式设置 secret）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from server.services.fs_write_token import _b64decode, _b64encode, _secret

_TOKEN_TTL_SECONDS = 600
_VERSION = "v1"


class ExperimentTransitionTokenError(Exception):
    """transition token 验证失败（伪造 / 过期 / 七元组不符）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(f"experiment transition token invalid: {reason}")
        self.reason = reason


def sign_transition_token(
    *,
    action: str,
    project_id: str,
    experiment_id: str,
    agent_id: str,
    from_phase: str,
    to_phase: str,
    base_revision: int,
    fingerprint: str | None,
    nonce: str | None = None,
    ttl_seconds: int = _TOKEN_TTL_SECONDS,
) -> tuple[str, datetime, str]:
    """签发七元组绑定 token，返回 ``(token, expires_at, nonce)``。"""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    payload: dict[str, Any] = {
        "v": _VERSION,
        "action": action,
        "project_id": str(project_id),
        "experiment_id": str(experiment_id),
        "agent_id": str(agent_id),
        "from_phase": str(from_phase),
        "to_phase": str(to_phase),
        "base_revision": int(base_revision),
        "fp": fingerprint,
        "nonce": nonce or uuid.uuid4().hex,
        "exp": int(expires_at.timestamp()),
    }
    body = _b64encode(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    sig = _b64encode(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{_VERSION}.{body}.{sig}", expires_at, str(payload["nonce"])


def verify_transition_token(
    token: str,
    *,
    project_id: str,
    experiment_id: str,
    agent_id: str,
) -> dict[str, Any]:
    """验签并校验归属与过期；通过则返回 token payload（含 action 七元组）。

    ``action`` 由（已验签的）payload 自带并在 commit 原语里按其分发——
    commit 请求不携带意图字段，token 即意图，不存在「请求 action 与
    token action」需要比对的第二来源。``base_revision`` / ``from_phase`` /
    ``fp`` 与 server 当前状态相关，由 commit 原语对 DB 现场 CAS（见
    ``experiment_transition_service.commit_transition``）。
    """
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _VERSION:
        raise ExperimentTransitionTokenError("malformed token")
    body, sig = parts[1], parts[2]
    expected = _b64encode(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise ExperimentTransitionTokenError("bad signature")
    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        raise ExperimentTransitionTokenError("undecodable payload") from err
    if str(payload.get("experiment_id")) != str(experiment_id):
        raise ExperimentTransitionTokenError("token bound to another experiment")
    if str(payload.get("project_id")) != str(project_id):
        raise ExperimentTransitionTokenError("token bound to another project")
    if str(payload.get("agent_id") or "") != str(agent_id):
        raise ExperimentTransitionTokenError("token bound to another actor")
    if not str(payload.get("action") or ""):
        raise ExperimentTransitionTokenError("token has no action binding")
    if not str(payload.get("nonce") or ""):
        raise ExperimentTransitionTokenError("token has no one-time nonce")
    exp = int(payload.get("exp") or 0)
    if datetime.now(timezone.utc).timestamp() >= exp:
        raise ExperimentTransitionTokenError("token expired")
    return cast(dict[str, Any], payload)


def token_digest(token: str) -> str:
    """token 摘要（receipt 存摘要不存原文；冲突报告可引用）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
