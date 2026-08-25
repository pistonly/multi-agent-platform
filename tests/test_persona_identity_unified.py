"""统一 persona 判定回归测试（``Agent.persona`` 尾段 ``-<persona>`` 规则）。

背景：persona 身份曾有四套互不一致的规则——notification_service /
agent_work_service 认精确 canonical 名、``Agent.persona`` 认
``multi-agent-platform-`` 前缀、fs_source_service 认 ``endswith("-host")``、
a2a 自行尾段提取。bootstrap 项目（agent 名 ``<project_key>-host``）因此
收不到 wakeable 通知、host 能力门禁恒 False。统一后 ``Agent.persona``
是唯一判定源，本文件锁定各消费点的行为。
"""

from __future__ import annotations

import uuid

from server.domain.models import Agent, AgentRole
from server.services.agent_work_service import _is_host_persona
from server.services.fs_source_service import _is_project_host, persona_short_name
from server.services.notification_service import _resolve_persona_agent_ids
from server.services.todo_persona_filter import agent_sees_review_obligations


def _agent(name: str, role: AgentRole = AgentRole.agent) -> Agent:
    """内存 Agent（不落库）——persona 判定纯看名字，不依赖 DB。"""
    return Agent(name=name, role=role, api_token_hash="x", api_token_prefix="x")


def test_persona_property_naming_matrix():
    assert _agent("multi-agent-platform-host").persona == "host"  # canonical
    assert _agent("my-project-host").persona == "host"  # bootstrap 命名
    assert _agent("demo-participant").persona == "participant"
    assert _agent("sdk-reviewer").persona == "reviewer"
    assert _agent("noise-solver").persona is None  # 自定义 agent
    assert _agent("my-project-sync").persona is None  # 投影 sync agent
    assert _agent("host").persona is None  # 无尾段
    assert _agent("multi-agent-platform-admin").persona is None  # 非法尾段


def test_bootstrap_named_host_has_capabilities():
    """``<key>-host`` 必须拿得到 host 能力（修复前恒 False → 403）。"""
    host = _agent("my-project-host")
    assert host.has_capability("system:cross_persona_call")
    assert host.has_capability("system:scan_stalled")
    assert not host.has_capability("review:accept_result")


def test_is_host_persona_covers_bootstrap_naming():
    assert _is_host_persona(_agent("my-project-host"))
    assert not _is_host_persona(_agent("my-project-participant"))
    assert not _is_host_persona(_agent("custom-agent"))
    assert _is_host_persona(_agent("custom-agent", role=AgentRole.admin))


def test_is_project_host_uses_suffix_rule():
    assert _is_project_host(_agent("my-project-host"), None)
    assert _is_project_host(_agent("multi-agent-platform-host"), None)
    assert not _is_project_host(_agent("my-project-reviewer"), None)


def test_review_obligations_filtered_by_suffix_persona():
    """bootstrap 的 host/participant 不应看到评审义务分区（与 canonical 一致）。"""
    assert not agent_sees_review_obligations(_agent("my-project-host"))
    assert not agent_sees_review_obligations(_agent("my-project-participant"))
    assert agent_sees_review_obligations(_agent("my-project-reviewer"))
    assert agent_sees_review_obligations(_agent("custom-agent"))


def test_persona_from_agent_name_covers_key_and_package_shapes():
    from map_types.persona import persona_from_agent_name, pick_agent_by_persona

    assert persona_from_agent_name("acme-host") == "host"
    assert persona_from_agent_name("multi-agent-platform-host") == "host"
    assert persona_from_agent_name("multi-agents-platform-reviewer") == "reviewer"
    assert persona_from_agent_name("noise-solver") is None
    assert persona_short_name(_agent("multi-agent-platform-host")) == "host"
    assert persona_short_name(_agent("my-project-host")) == "host"
    assert persona_short_name(_agent("noise-solver")) == "noise-solver"

    class _A:
        def __init__(self, name: str, id: str) -> None:
            self.name = name
            self.id = id

    agents = [
        _A("multi-agents-platform-participant", "legacy"),
        _A("acme-participant", "preferred"),
    ]
    picked = pick_agent_by_persona(agents, "participant", project_key="acme")
    assert picked is not None and picked.id == "preferred"
    fallback = pick_agent_by_persona(agents, "participant")
    assert fallback is not None and fallback.id == "legacy"


def test_resolve_persona_agent_ids_bootstrapped_project(db_session, client):
    """bootstrap 出的 ``<key>-host`` 必须能被 wakeable 收件人解析命中（修复前静默空）。"""
    resp = client.post(
        "/api/v1/bootstrap",
        json={
            "project_key": "persona-unify-demo",
            "project_name": "Persona Unify Demo",
            "workspace_path": "/tmp/persona-unify-demo",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    host = next(a for a in body["agents"] if a["persona"] == "host")
    assert host["agent_name"] == "persona-unify-demo-host"

    project_id = uuid.UUID(body["project"]["id"])
    host_ids = _resolve_persona_agent_ids(db_session, project_id, ["host"])
    assert host_ids == [uuid.UUID(host["agent_id"])]

    # 收件人不串 persona：host 与 participant/reviewer 解析结果互斥
    others = _resolve_persona_agent_ids(
        db_session, project_id, ["participant", "reviewer"]
    )
    assert others and set(host_ids).isdisjoint(others)
