"""MAPClient 组合层：HTTP plumbing + 按资源域拆分的 mixin（见 client_mixins/）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from map_types import (
    CommentAnchorType,
    CommentCreate,
    ExperimentComplete,
    ExperimentCreate,
    ExperimentLogCreate,
    ExperimentPhase,
    ExperimentResultDecision,
    PlanRevise,
    ProjectUpdate,
    ReviewCreate,
    ReviewItemStatus,
    TopicCommentCreate,
    TopicCreate,
    TopicStatus,
    TopicUpdate,
)

from map_client.client_mixins import (
    AgentProjectMixin,
    ExperimentMixin,
    FsTopicMixin,
    TodoNotificationMixin,
)
from map_client.config import load_config
from map_client.exceptions import raise_for_status

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _is_local_url(url: str) -> bool:
    """判断 URL 的 host 是否指向本机。

    localhost / 127.0.0.1 / ::1 / 未指定 host 时返回 True。此类场景应
    忽略环境变量代理，避免因 SOCKS 代理初始化失败等问题影响对本地 MAP
    服务的访问。
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _LOCAL_HOSTS

class MAPClient(
    AgentProjectMixin,
    ExperimentMixin,
    FsTopicMixin,
    TodoNotificationMixin,
):
    """HTTP client for the Multi-Agent Platform REST API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        retries: int = 1,
    ) -> None:
        if not token:
            raise ValueError("API token is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._transport = transport
        # 本地地址（localhost / 127.0.0.1 / ::1）忽略环境代理，避免因
        # SOCKS 代理初始化失败等影响本地 MAP 服务访问；其他地址沿用
        # 环境代理配置。调用方显式传 transport 时代理与重试由其自理。
        trust_env = not _is_local_url(self.base_url)
        if transport is None:
            # T28：连接级重试——建连失败（连接拒绝/瞬时网络错误）自动重试
            # 一次，长流程不必在 subprocess 层自建重试；retries=0 可关闭。
            transport = httpx.HTTPTransport(retries=retries, trust_env=trust_env)
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
            trust_env=trust_env,
        )

    @classmethod
    def from_env(cls, *, transport: httpx.BaseTransport | None = None) -> MAPClient:
        cfg = load_config()
        if not cfg.get("token"):
            raise ValueError("MAP_TOKEN not set and no token in ~/.map/config.yaml")
        return cls(cfg["api_url"], cfg["token"], transport=transport)

    @classmethod
    def from_config(
        cls,
        config_path: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> MAPClient:
        cfg = load_config(Path(config_path) if config_path else None)
        if not cfg.get("token"):
            raise ValueError("MAP_TOKEN not set and no token in config")
        return cls(cfg["api_url"], cfg["token"], transport=transport)

    def close(self) -> None:
        if self._transport is None:
            self._http.close()

    def __enter__(self) -> MAPClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            detail = response.text
            error_code: str | None = None
            hint: str | None = None
            retryable: bool | None = None
            if response.content:
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        if "detail" in payload:
                            detail = str(payload["detail"])
                        # I1(d): surface server-side subcodes so callers can
                        # branch on structured failure modes instead of
                        # pattern-matching the human-readable ``detail``.
                        if isinstance(payload.get("error_code"), str):
                            error_code = payload["error_code"]
                        if isinstance(payload.get("hint"), str):
                            hint = payload["hint"]
                        if isinstance(payload.get("retryable"), bool):
                            retryable = payload["retryable"]
                except Exception:
                    detail = response.text
            # P2 #2: 根据 status_code raise 具体子类（NotFound / Conflict 等），
            # 调用方可 catch 子类写语义化处理，不再依赖 if status_code == 404。
            raise_for_status(
                response.status_code,
                detail,
                error_code=error_code,
                hint=hint,
                retryable=retryable,
            )
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        if response.status_code == 204:
            return None
        return response.json()

    @staticmethod
    def _total_count(response: httpx.Response) -> int:
        raw = response.headers.get("X-Total-Count")
        if raw is None:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0


# Re-export types useful for SDK consumers
__all__ = [
    "MAPClient",
    "CommentAnchorType",
    "ExperimentPhase",
    "ReviewItemStatus",
    "ExperimentCreate",
    "ExperimentComplete",
    "ExperimentResultDecision",
    "ExperimentLogCreate",
    "PlanRevise",
    "ReviewCreate",
    "CommentCreate",
    "ProjectUpdate",
    "TopicCreate",
    "TopicUpdate",
    "TopicCommentCreate",
    "TopicStatus",
]
