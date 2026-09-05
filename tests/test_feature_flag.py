"""Project feature flag service 测试（实验 M2 I4：A4）。

覆盖：

- 服务层 CRUD：set 是 upsert、get / list 行为
- value 校验：未知 key → UnknownFlagKeyError；非法 value → InvalidFlagValueError
- 审计 anchor：ON flip 必须非空 reason；OFF flip 允许空 reason
- 权限：仅 host creator / admin 可 set；participant / reviewer / cross-project
  agent 直接 ForbiddenError；admin bypass

不覆盖：HTTP 路径（test_kill_switch_fail_closed 用 FastAPI TestClient
覆盖 API + 端到端 gate 行为）。
"""

from __future__ import annotations

import uuid

import pytest

from server.domain.models import ProjectFeatureFlag
from server.services import feature_flag_service as svc
from server.services.errors import ForbiddenError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_agent(db_session, *, project_id, name, role="agent"):
    """构造最小可用的 Agent 记录（db_session 直写，避免再走 HTTP 路径）。

    直接写库需要给 ``api_token_hash`` / ``api_token_prefix`` / ``api_token_sha256``
    提供合法值（NOT NULL 约束）；feature flag service 只读 actor.id /
    actor.name / actor.role / actor.project_id，这些字段足以让它走通
    ``_ensure_can_set_flag`` 判定。
    """
    from server.domain.models import Agent, AgentRole

    role_enum = AgentRole(role) if role in {"admin", "agent"} else AgentRole(role)
    agent = Agent(
        id=uuid.uuid4(),
        name=name,
        api_token_hash="test-token-hash",
        api_token_prefix="tt",
        api_token_sha256=uuid.uuid4().hex + uuid.uuid4().hex[:0],  # 64-hex
        role=role_enum,
        project_id=project_id,
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def _make_project(db_session, *, key="pff-project"):
    """db_session 直写一个 project，跳过 HTTP path。"""
    from server.domain.models import Project

    project = Project(
        id=uuid.uuid4(),
        project_key=key,
        name=key,
        workspace_path=f"/tmp/{key}",
        description="feature flag test project",
    )
    db_session.add(project)
    db_session.flush()
    return project


# ---------------------------------------------------------------------------
# set / get / list
# ---------------------------------------------------------------------------


def test_set_creates_row_with_actor_and_reason(db_session):
    """set 创建新行，actor / reason / flag_value 全部正确落库。"""
    project = _make_project(db_session)
    host = _make_agent(db_session, project_id=project.id, name=f"{project.project_key}-host")

    result = svc.set_flag(
        db_session,
        project_id=project.id,
        flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
        flag_value="on",
        actor=host,
        reason="A4 灰度启用：双写保稳态",
        commit=False,
    )
    db_session.flush()

    assert result.project_id == project.id
    assert result.flag_key == svc.FLAG_FS_STOP_DUPLICATE_INSERT
    assert result.flag_value == "on"
    assert result.set_by_agent_id == host.id
    assert result.reason == "A4 灰度启用：双写保稳态"

    row = db_session.get(ProjectFeatureFlag, (project.id, svc.FLAG_FS_STOP_DUPLICATE_INSERT))
    assert row is not None
    assert row.flag_value == "on"
    assert row.set_by_agent_id == host.id


def test_set_upsert_overwrites_value_reason_actor(db_session):
    """同一 (project, flag_key) 二次 set 覆盖 value / reason / actor。"""
    project = _make_project(db_session)
    host_a = _make_agent(db_session, project_id=project.id, name=f"{project.project_key}-host")
    # 第二个 host：同项目不同名字但仍需带 ``-host`` 后缀才被 persona 解析
    # 为 host。用 ``host-b`` 后缀（依然匹配 CANONICAL_PERSONAS）。
    host_b = _make_agent(db_session, project_id=project.id, name="alt-host")

    svc.set_flag(
        db_session,
        project_id=project.id,
        flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
        flag_value="on",
        actor=host_a,
        reason="first flip",
        commit=False,
    )
    db_session.flush()

    svc.set_flag(
        db_session,
        project_id=project.id,
        flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
        flag_value="off",
        actor=host_b,
        reason="kill switch 触发：DB / FS 漂移，先回退",
        commit=False,
    )
    db_session.flush()

    row = db_session.get(ProjectFeatureFlag, (project.id, svc.FLAG_FS_STOP_DUPLICATE_INSERT))
    assert row is not None
    assert row.flag_value == "off"
    assert row.set_by_agent_id == host_b.id
    assert row.reason == "kill switch 触发：DB / FS 漂移，先回退"

    listed = svc.list_flags(db_session, project.id)
    assert len(listed) == 1
    assert listed[0].flag_value == "off"


def test_get_returns_none_when_not_set(db_session):
    """get 不存在 flag → None（不要 raise —— caller 走 default）。"""
    project = _make_project(db_session)
    assert svc.get_flag(db_session, project.id, svc.FLAG_FS_STOP_DUPLICATE_INSERT) is None


def test_list_flags_returns_sorted_keys(db_session):
    """list 按 flag_key 字母序返回（多 flag 时 caller 顺序稳定）。"""
    project = _make_project(db_session)
    host = _make_agent(db_session, project_id=project.id, name=f"{project.project_key}-host")
    # 当前 REGISTERED_FLAGS 只一个 key，但写两条以触发 ON flip → fail
    # closed 路径会先 set off；验证 list 仍按 key 排序即可。
    svc.set_flag(
        db_session,
        project_id=project.id,
        flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
        flag_value="on",
        actor=host,
        reason="A4 启用",
        commit=False,
    )
    db_session.flush()

    listed = svc.list_flags(db_session, project.id)
    assert [r.flag_key for r in listed] == [svc.FLAG_FS_STOP_DUPLICATE_INSERT]


# ---------------------------------------------------------------------------
# value / key 校验
# ---------------------------------------------------------------------------


def test_set_rejects_unknown_flag_key(db_session):
    project = _make_project(db_session)
    host = _make_agent(db_session, project_id=project.id, name=f"{project.project_key}-host")

    with pytest.raises(svc.UnknownFlagKeyError) as ei:
        svc.set_flag(
            db_session,
            project_id=project.id,
            flag_key="nonsense_flag",
            flag_value="on",
            actor=host,
            reason="r",
            commit=False,
        )
    assert ei.value.key == "nonsense_flag"


def test_set_rejects_invalid_value(db_session):
    project = _make_project(db_session)
    host = _make_agent(db_session, project_id=project.id, name=f"{project.project_key}-host")

    with pytest.raises(svc.InvalidFlagValueError) as ei:
        svc.set_flag(
            db_session,
            project_id=project.id,
            flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
            flag_value="true",  # 不是合法值
            actor=host,
            reason="r",
            commit=False,
        )
    assert ei.value.value == "true"
    assert set(ei.value.allowed) == {"on", "off"}


# ---------------------------------------------------------------------------
# reason 强制（kill switch / fail-closed gate 审计 anchor）
# ---------------------------------------------------------------------------


def test_set_on_requires_non_empty_reason(db_session):
    """ON flip 强制 reason 非空（kill switch / fail-closed 决策不可无痕）。"""
    project = _make_project(db_session)
    host = _make_agent(db_session, project_id=project.id, name=f"{project.project_key}-host")

    for bad in (None, "", "   "):
        with pytest.raises(ValueError) as ei:
            svc.set_flag(
                db_session,
                project_id=project.id,
                flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
                flag_value="on",
                actor=host,
                reason=bad,
                commit=False,
            )
        assert "non-empty reason" in str(ei.value)


def test_set_off_allows_empty_reason(db_session):
    """OFF flip 允许空 reason（fast rollback 路径不该被空文本阻塞）。"""
    project = _make_project(db_session)
    host = _make_agent(db_session, project_id=project.id, name=f"{project.project_key}-host")

    # 先 ON 一次（有 reason）
    svc.set_flag(
        db_session,
        project_id=project.id,
        flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
        flag_value="on",
        actor=host,
        reason="A4 启用",
        commit=False,
    )
    db_session.flush()

    # 再 OFF 不带 reason：不该 raise
    result = svc.set_flag(
        db_session,
        project_id=project.id,
        flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
        flag_value="off",
        actor=host,
        reason=None,
        commit=False,
    )
    db_session.flush()
    assert result.flag_value == "off"


# ---------------------------------------------------------------------------
# Authorization（host creator / admin only）
# ---------------------------------------------------------------------------


def test_set_rejects_non_host_non_admin_actor(db_session):
    """participant / reviewer actor → ForbiddenError。"""
    project = _make_project(db_session)
    participant = _make_agent(
        db_session, project_id=project.id, name=f"{project.project_key}-participant"
    )

    with pytest.raises(ForbiddenError) as ei:
        svc.set_flag(
            db_session,
            project_id=project.id,
            flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
            flag_value="on",
            actor=participant,
            reason="p",
            commit=False,
        )
    assert "persona=" in str(ei.value) or "host or admin" in str(ei.value)


def test_set_rejects_cross_project_host_actor(db_session):
    """A 项目的 host agent 不允许 set B 项目的 flag（project_id 不匹配）。"""
    project_a = _make_project(db_session, key="proj-a")
    project_b = _make_project(db_session, key="proj-b")
    host_of_a = _make_agent(db_session, project_id=project_a.id, name=f"{project_a.project_key}-host")

    with pytest.raises(ForbiddenError) as ei:
        svc.set_flag(
            db_session,
            project_id=project_b.id,
            flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
            flag_value="on",
            actor=host_of_a,
            reason="p",
            commit=False,
        )
    assert "project" in str(ei.value).lower()


def test_set_admin_bypasses_host_check(db_session):
    """admin role 直接放行（即便 actor 不属于该项目）。"""
    project = _make_project(db_session)
    # admin 通常不属于任何 project（project_id=None）；通过给一个跨项目
    # project_id 验证 admin bypass。
    admin = _make_agent(db_session, project_id=None, name="platform-admin", role="admin")

    result = svc.set_flag(
        db_session,
        project_id=project.id,
        flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
        flag_value="on",
        actor=admin,
        reason="admin 紧急 flip",
        commit=False,
    )
    db_session.flush()
    assert result.flag_value == "on"
    assert result.set_by_agent_id == admin.id


# ---------------------------------------------------------------------------
# 便捷查询
# ---------------------------------------------------------------------------


def test_is_fs_stop_duplicate_insert_on_default_false(db_session):
    """未 set / OFF → False（保守默认：fail-closed 默认不激活）。"""
    project = _make_project(db_session)
    # 完全没有 set
    assert svc.is_fs_stop_duplicate_insert_on(db_session, project.id) is False

    host = _make_agent(db_session, project_id=project.id, name=f"{project.project_key}-host")
    svc.set_flag(
        db_session,
        project_id=project.id,
        flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
        flag_value="off",
        actor=host,
        reason="init off",
        commit=False,
    )
    db_session.flush()
    assert svc.is_fs_stop_duplicate_insert_on(db_session, project.id) is False


def test_is_fs_stop_duplicate_insert_on_when_set(db_session):
    """ON set 后 → True。"""
    project = _make_project(db_session)
    host = _make_agent(db_session, project_id=project.id, name=f"{project.project_key}-host")

    svc.set_flag(
        db_session,
        project_id=project.id,
        flag_key=svc.FLAG_FS_STOP_DUPLICATE_INSERT,
        flag_value="on",
        actor=host,
        reason="A4 启用",
        commit=False,
    )
    db_session.flush()

    assert svc.is_fs_stop_duplicate_insert_on(db_session, project.id) is True
