"""实验生命周期 validate/commit CAS 原语（实验B 24f3e565 / B1~B8）。

把实验 phase transition 从「单体 POST 直接调 ``phase_service.<action>``」
升级为与 topic 域 ``validated_write_flow`` 同思想的两跳可恢复协议：

1. **validate**：状态机预检 + 角色 gate 预检 + 指纹 fail closed 门禁（B8）
   + revision 快照，签发绑定七元组（project / experiment / actor /
   from-phase / to-phase / base-revision / workspace-fingerprint）与有限
   TTL 的 HMAC token（B1）。
2. **commit**：验签 → 重放命中 receipt 直接返回原回执（B2 幂等，不重复
   audit / 通知）→ CAS（receipt 计数即 revision，首个成功提交者胜出，
   B5）→ 指纹 re-stat 复核 → ``phase_service.<action>(commit=False)`` 与
   receipt 插入**同事务**原子提交。

audit / notification 扇出留在 API 层（``server/api/experiment_transition.py``
的 ``emit_transition_effects``），仅在 ``replayed=False`` 时执行一次——
direct 与 standard、两跳与单体包装由此产生同构的 receipt / audit（B7）。

Revision 语义：``base_revision`` = 该实验提交时刻已 committed 的 receipt
计数（单调递增）。**不改 experiments 表**——``init_db`` 的 create_all
不给既有表补列（``server/db/session.py``），receipt 计数即版本；历史
transition 无 receipt 行（计数从 0 起），from-phase 绑定独立兜底。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.domain.models import ExperimentTransitionReceipt
from server.domain.state_machine import validate_phase_transition
from server.services import phase_service
from server.services.errors import ConflictError, StateTransitionError
from server.services.experiment_transition_token import (
    ExperimentTransitionTokenError,
    sign_transition_token,
    token_digest,
    verify_transition_token,
)
from server.services.project_service import get_experiment

# action → 允许的 to_phase 集合（与单体端点的 phase_service 调用一一对应；
# complete 的终态由 mode 决定：direct→done，standard→result_review，
# 状态机在 validate 与 commit 双侧兜底）。
_ACTION_TO_PHASES: dict[str, frozenset[str]] = {
    "submit-review": frozenset({"review"}),
    "approve": frozenset({"approved"}),
    "withdraw": frozenset({"draft"}),
    "cancel": frozenset({"cancelled"}),
    "start": frozenset({"running"}),
    "complete": frozenset({"done", "result_review"}),
    "accept-result": frozenset({"done"}),
    "reject-result": frozenset({"running"}),
}

_REBIND_GUIDANCE = (
    "lifecycle 写在指纹不一致时 fail closed（实验B B8）；请走受审计的显式 "
    "rebind/bootstrap 流程（`map bootstrap --heal --key <project-key>`）核对 "
    "workspace_path 后重试。不做隐式回退。"
)


# ---------------------------------------------------------------------------
# 指纹（B8）——格式与 sdk map_client.project_context.workspace_fingerprint
# 逐字符一致（dev=<st_dev>:ino=<st_ino>）；server 侧不 import client SDK，
# 这里镜像实现并以测试锚定两端口径一致。
# ---------------------------------------------------------------------------


def workspace_fingerprint_of(path_str: str | None) -> str | None:
    """对路径现场 ``stat`` 出指纹；不可 stat（跨机/已删除）返回 None。"""
    if not path_str:
        return None
    try:
        st = os.stat(path_str)
    except OSError:
        return None
    return f"dev={st.st_dev}:ino={st.st_ino}"


def _fingerprint_gate(
    project,
    client_fingerprint: str | None,
) -> str | None:
    """validate 侧指纹门禁，返回应绑定进 token 的指纹。

    - 显式客户端指纹（CLI 两跳路径）：与 server 记录的 workspace_path 现场
      stat 严格比对；不可 stat 或不一致 → fail closed（409 + rebind 指引）。
    - None（server 同请求两跳包装路径）：绑定 server 自算指纹
      （server-authoritative，Web 兼容；CLI 两跳路径必须显式携带）。
    """
    server_fp = workspace_fingerprint_of(getattr(project, "workspace_path", None))
    if client_fingerprint is None:
        return server_fp
    if server_fp is None:
        raise ConflictError(
            "server 记录的 workspace_path 无法在本机 stat（跨机部署或目录已移除）；"
            + _REBIND_GUIDANCE
        )
    if server_fp != client_fingerprint:
        raise ConflictError(
            f"workspace fingerprint mismatch: local {client_fingerprint} vs server "
            f"{server_fp}（疑似同一项目的两个 clone / 双挂载漂移，split-brain 风险）；"
            + _REBIND_GUIDANCE
        )
    return client_fingerprint


# ---------------------------------------------------------------------------
# revision（receipt 计数）
# ---------------------------------------------------------------------------


def committed_revision(db: Session, experiment_id: uuid.UUID) -> int:
    """该实验已 committed 的 transition receipt 计数（单调乐观锁基）。"""
    return (
        db.query(ExperimentTransitionReceipt)
        .filter(ExperimentTransitionReceipt.experiment_id == experiment_id)
        .count()
    )


def latest_receipt(
    db: Session, experiment_id: uuid.UUID
) -> ExperimentTransitionReceipt | None:
    return (
        db.query(ExperimentTransitionReceipt)
        .filter(ExperimentTransitionReceipt.experiment_id == experiment_id)
        .order_by(ExperimentTransitionReceipt.base_revision.desc())
        .first()
    )


def get_receipt(
    db: Session, experiment_id: uuid.UUID, nonce: str
) -> ExperimentTransitionReceipt | None:
    receipt = db.get(ExperimentTransitionReceipt, nonce)
    if receipt is not None and receipt.experiment_id != experiment_id:
        return None
    return receipt


def list_receipts(
    db: Session, experiment_id: uuid.UUID, limit: int = 20
) -> list[ExperimentTransitionReceipt]:
    return (
        db.query(ExperimentTransitionReceipt)
        .filter(ExperimentTransitionReceipt.experiment_id == experiment_id)
        .order_by(ExperimentTransitionReceipt.base_revision.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _derive_to_phase(action: str, experiment, to_phase: str | None) -> str:
    allowed = _ACTION_TO_PHASES.get(action)
    if allowed is None:
        raise StateTransitionError(
            f"unknown transition action: {action} "
            f"(expected one of {sorted(_ACTION_TO_PHASES)})"
        )
    if to_phase is None:
        if len(allowed) == 1:
            return next(iter(allowed))
        # complete：按 mode 定终态（direct→done / standard→result_review）
        return "done" if experiment.mode == "direct" else "result_review"
    if to_phase not in allowed:
        raise StateTransitionError(
            f"action {action} cannot target phase {to_phase} "
            f"(allowed: {sorted(allowed)})"
        )
    return to_phase


def _preflight_role_gate(experiment, actor, action: str) -> None:
    """validate 侧快速失败的角色预检（authoritative 门禁仍在 service 内）。

    只覆盖 creator 族 gate 与 complete 的 executor gate；accept-result /
    reject-result 的 reviewer 语义由 ``phase_completion`` 的 gate 在 commit
    时权威裁决，这里不预检（避免把合法 reviewer 拦在 validate）。
    """
    from server.services.phase_service import _ensure_can_complete, _ensure_creator_or_admin

    if action == "complete":
        _ensure_can_complete(experiment, actor)
        return
    if action in ("accept-result", "reject-result"):
        return
    _ensure_creator_or_admin(experiment, actor)


def validate_transition(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    actor,
    action: str,
    to_phase: str | None = None,
    base_revision: int | None = None,
    workspace_fingerprint: str | None = None,
    expires_in_seconds: int = 600,
):
    """状态机 + 角色 + 指纹 + revision 预检，签发七元组绑定 token。

    返回 ``(verdict_schema, token)``；不落地任何状态。
    """
    from map_types.enums import ExperimentPhase
    from map_types.schemas.experiment import ExperimentTransitionVerdict

    experiment = get_experiment(db, experiment_id)
    _preflight_role_gate(experiment, actor, action)
    target = _derive_to_phase(action, experiment, to_phase)
    try:
        target_enum = ExperimentPhase(target)
    except ValueError as err:
        raise StateTransitionError(f"unknown target phase: {target}") from err
    validate_phase_transition(experiment.phase, target_enum, mode=experiment.mode)

    project = _project_of(db, experiment)
    bound_fp = _fingerprint_gate(project, workspace_fingerprint)

    # 实验 M2 I4（A4）：fs_stop_duplicate_insert flag 触发 active transition
    # fail closed。检查在状态机与角色 gate 之后——状态机本身就拒绝
    # 非法转移，先把非法流量过滤掉再查 flag 避免无谓 IO。
    _fail_closed_if_flag_on(
        db,
        project_id=experiment.project_id,
        experiment_id=experiment_id,
        target_phase=target_enum,
    )

    revision = committed_revision(db, experiment_id)
    if base_revision is not None and base_revision != revision:
        raise ConflictError(_cas_lost_message(experiment, base_revision, revision, db))

    token, expires_at, nonce = sign_transition_token(
        action=action,
        project_id=str(experiment.project_id),
        experiment_id=str(experiment_id),
        agent_id=str(actor.id),
        from_phase=experiment.phase.value,
        to_phase=target,
        base_revision=revision,
        fingerprint=bound_fp,
        ttl_seconds=expires_in_seconds,
    )
    verdict = ExperimentTransitionVerdict(
        token=token,
        nonce=nonce,
        action=action,
        experiment_id=experiment_id,
        project_id=experiment.project_id,
        from_phase=experiment.phase.value,
        to_phase=target,
        base_revision=revision,
        expires_at=expires_at,
    )
    return verdict, token


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


def _cas_lost_message(experiment, base_revision: int, current: int, db: Session) -> str:
    winner = latest_receipt(db, experiment.id)
    winner_desc = ""
    if winner is not None:
        winner_desc = (
            f"; 胜出 receipt: action={winner.action} actor={winner.actor_id} "
            f"at {winner.committed_at} (token_digest={winner.token_digest[:12]}…)"
        )
    return (
        f"transition CAS lost: token base_revision=r{base_revision}, "
        f"server 已推进到 r{current}, phase={experiment.phase.value}"
        f"{winner_desc}。本地不得落地目标状态投影；"
        f"用 `map experiment recover --id <exp>` 对照双方证据。"
    )


def _project_of(db: Session, experiment):
    from server.domain.models import Project

    project = db.get(Project, experiment.project_id)
    if project is None:
        raise ConflictError("experiment has no project record")
    return project


# ---------------------------------------------------------------------------
# 实验 M2 I4（A4）fail-closed gate
# ---------------------------------------------------------------------------

# 触发 fail closed 的目标 phase 集合（active = 非终态；对照
# sync_check.TERMINAL_PHASES = {done, cancelled}）。
_FAIL_CLOSED_ACTIVE_PHASES: frozenset[str] = frozenset(
    {"draft", "review", "approved", "running", "result_review"}
)


def _fail_closed_if_flag_on(
    db: Session,
    *,
    project_id: uuid.UUID,
    experiment_id: uuid.UUID,
    target_phase,
) -> None:
    """fs_stop_duplicate_insert=on 且 active transition 缺 projection
    主行时 fail closed（实验 M2 A4）。

    设计约束：

    - **OFF（默认）** 直接 return——M1 行为不变。
    - **target 不在 active 集合** 直接 return——terminal transition
      （→ done / cancelled）不受 flag 限制；A4 lazy materialization
      只允许 terminal FS-only 展示（sync_check.TERMINAL_PHASES 同源
      定义）。
    - **target ∈ active + flag=ON + project 无 fs_projections 行** →
      raise ConflictError，错误文案带 flag 状态 + 触发条件 + 推荐
      kill switch 路径（让 caller 一眼看到「我下一步该 flip off」）。

    「projection 主行」的具体定义：本实现选 ``fs_projections``（项目级
    FS 投影快照表）作为 projection 主行的代理——它存在的条件是
    ``map sync publish --full`` 至少跑过一次。local-fs 模式 / 远端
    未推送的项目天然无行 → 任何 active transition 在 flag=ON 时
    fail closed。这与「lazy materialization 不放行」的语义一致：远端
    读路径在 flag=ON + 缺投影时应 fail closed，而不是悄悄走 DB 旧列。
    """
    from server.services.feature_flag_service import is_fs_stop_duplicate_insert_on

    target_value = getattr(target_phase, "value", target_phase)
    if target_value not in _FAIL_CLOSED_ACTIVE_PHASES:
        return
    if not is_fs_stop_duplicate_insert_on(db, project_id):
        return
    # FsProjection 的 PK 是 ``id``，``project_id`` 是 unique index——
    # 不能直接 db.get(FsProjection, project_id)，否则等价于查 ``id =
    # project_id`` 永远 None（除了极小概率碰撞）。改成 select + first()
    # 走 unique index。
    from sqlalchemy import select

    from server.domain.models import FsProjection

    projection_row = db.scalar(
        select(FsProjection).where(FsProjection.project_id == project_id)
    )
    if projection_row is not None:
        return
    raise ConflictError(
        f"fs_stop_duplicate_insert is ON and FS projection main row is "
        f"missing for project {project_id} (experiment {experiment_id} → "
        f"{target_value}); active transition fail closed (实验 M2 A4). "
        f"修复路径: 1) `map sync publish --full` 先建立 projection 主行; "
        f"或 2) kill switch 触发回退: `map project config flag set "
        f"--key fs_stop_duplicate_insert --value off --reason '<reason>'` "
        f"（仅 host creator / admin 可执行）。"
    )


def _apply_transition(
    db: Session,
    *,
    action: str,
    experiment_id: uuid.UUID,
    actor,
    start=None,
    complete=None,
    decision=None,
) -> None:
    """按 action 分发到 phase_service（commit=False，与 receipt 同事务）。"""
    if action == "submit-review":
        phase_service.submit_for_review(db, experiment_id, actor, commit=False)
    elif action == "approve":
        phase_service.approve_experiment(db, experiment_id, actor, commit=False)
    elif action == "withdraw":
        phase_service.withdraw_from_review(db, experiment_id, actor, commit=False)
    elif action == "cancel":
        phase_service.cancel_experiment(db, experiment_id, actor, commit=False)
    elif action == "start":
        executor_agent_id = start.executor_agent_id if start is not None else None
        phase_service.start_experiment(
            db, experiment_id, actor, executor_agent_id, commit=False
        )
    elif action == "complete":
        if complete is None:
            raise StateTransitionError("complete transition requires the complete payload")
        phase_service.complete_experiment(
            db, experiment_id, actor, complete, commit=False
        )
    elif action == "accept-result":
        if decision is None:
            raise StateTransitionError("accept-result transition requires the decision payload")
        phase_service.accept_result(db, experiment_id, actor, decision, commit=False)
    elif action == "reject-result":
        if decision is None:
            raise StateTransitionError("reject-result transition requires the decision payload")
        phase_service.reject_result(db, experiment_id, actor, decision, commit=False)
    else:
        raise StateTransitionError(f"unknown transition action: {action}")


def commit_transition(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    actor,
    token: str,
    start=None,
    complete=None,
    decision=None,
) -> tuple[ExperimentTransitionReceipt, dict[str, Any], bool]:
    """CAS 提交：重放返回原 receipt；胜者落地状态+回执；败方 409 带证据。

    返回 ``(receipt, response_snapshot, replayed)``。audit / notification
    由 API 层在 ``replayed=False`` 时执行一次（``emit_transition_effects``）。
    """
    experiment = get_experiment(db, experiment_id)
    try:
        payload = verify_transition_token(
            token,
            project_id=str(experiment.project_id),
            experiment_id=str(experiment_id),
            agent_id=str(actor.id),
        )
    except ExperimentTransitionTokenError as exc:
        raise ConflictError(
            f"transition token invalid ({exc.reason}): 未落地任何状态；"
            f"重新 validate 后再 commit（token 七元组任一不匹配或过期都会拒绝，B1）。"
        ) from exc

    nonce = str(payload["nonce"])
    existing = get_receipt(db, experiment_id, nonce)
    if existing is not None:
        # B2 重放幂等：返回原 receipt，不重复写 audit / 发通知 / 变更状态。
        return existing, dict(existing.response_snapshot), True

    action = str(payload["action"])
    token_base_revision = int(payload["base_revision"])
    token_fp = payload.get("fp")

    # B8 commit 侧：对 server 记录的 workspace_path 现场 re-stat 复核
    # （validate 与 commit 之间 workspace 被调包也能拦下）。
    project = _project_of(db, experiment)
    server_fp = workspace_fingerprint_of(getattr(project, "workspace_path", None))
    if token_fp is not None and (server_fp is None or server_fp != token_fp):
        raise ConflictError(
            "workspace fingerprint mismatch at commit（validate 后 workspace 已变化"
            f"或跨机不可 stat）：token fp={token_fp}, server fp={server_fp}；"
            + _REBIND_GUIDANCE
        )

    # B5 CAS：首个成功提交者胜出。
    current_revision = committed_revision(db, experiment_id)
    if current_revision != token_base_revision:
        raise ConflictError(
            _cas_lost_message(experiment, token_base_revision, current_revision, db)
        )
    if experiment.phase.value != str(payload["from_phase"]):
        raise ConflictError(
            f"transition from-phase mismatch: token from={payload['from_phase']}, "
            f"server phase={experiment.phase.value}（状态已被并发 transition 改变）；"
            f"用 `map experiment recover --id <exp>` 对照双方证据。"
        )

    _apply_transition(
        db,
        action=action,
        experiment_id=experiment_id,
        actor=actor,
        start=start,
        complete=complete,
        decision=decision,
    )

    snapshot: dict[str, Any] = {
        "experiment_id": str(experiment_id),
        "phase": experiment.phase.value,
        "title": experiment.title,
        "mode": experiment.mode,
        "executor_agent_id": (
            str(experiment.executor_agent_id) if experiment.executor_agent_id else None
        ),
    }
    receipt = ExperimentTransitionReceipt(
        nonce=nonce,
        experiment_id=experiment_id,
        project_id=experiment.project_id,
        action=action,
        from_phase=str(payload["from_phase"]),
        to_phase=str(payload["to_phase"]),
        actor_id=actor.id,
        base_revision=token_base_revision,
        fingerprint=token_fp,
        token_digest=token_digest(token),
        response_snapshot=snapshot,
    )
    db.add(receipt)
    try:
        db.commit()
    except IntegrityError:
        # 并发同 nonce 双提交（nonce 主键冲突）→ 另一事务已胜出，按重放返回。
        db.rollback()
        winner = get_receipt(db, experiment_id, nonce)
        if winner is not None:
            return winner, dict(winner.response_snapshot), True
        raise
    return receipt, snapshot, False


def run_transition(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    actor,
    action: str,
    start=None,
    complete=None,
    decision=None,
) -> tuple[ExperimentTransitionReceipt, dict[str, Any], bool]:
    """单体端点包装路径：同请求内 validate（server-authoritative 指纹）+ commit。

    Web / 旧 SDK 单跳调用语义不变（B10 不做 Web 改动），但状态变更与
    receipt 与两跳路径同构（B7：无绕过 validate 的旁路）。
    """
    verdict, token = validate_transition(
        db,
        experiment_id=experiment_id,
        actor=actor,
        action=action,
        to_phase=None,
        workspace_fingerprint=None,
    )
    return commit_transition(
        db,
        experiment_id=experiment_id,
        actor=actor,
        token=token,
        start=start,
        complete=complete,
        decision=decision,
    )
