"""FS 验证型写原语 — T45 拆分自 cli/commands/fs.py。

local plane（``local_validated_write_flow``：本地校验 → 写回 → 审计）与
remote plane（``validated_write_flow``：server validate → 本地写回 → CAS
commit）共用的工作流。workspace/解析 helpers（``_workspace`` /
``_require_local_topic`` / ``_content_root_name`` 等）留守宿主
``cli.commands.fs``（测试 monkeypatch 面），函数体 call-time 导入保持
patch 可见性。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError

from cli import runner  # module ref: test monkeypatch surface (T23)


def _append_local_audit(
    workspace: Path, topic: str, action_name: str, actor: str, fields: dict[str, str]
) -> None:
    """local plane 生命周期审计：``<topic>/audit.jsonl`` 追加一行。

    不匹配 ``_ROUND_FILE_RE``，对 scan_plane / derive_work / anomaly 扫描不可见。
    """
    from datetime import datetime, timezone

    from cli.commands.fs import _content_root_name

    audit_path = workspace / _content_root_name(workspace) / "topics" / topic / "audit.jsonl"
    line = json.dumps(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action_name,
            "actor_persona": actor,
            "fields": fields,
            "source": "local-plane",
        },
        ensure_ascii=False,
    )
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _render_local_validate_error(exc: Exception) -> None:
    """local plane 校验失败 → 逐行列出可操作依据（与 remote 409 文案同源）。"""
    from map_fs import AckPendingError, OpenActionItemsError, OpenExperimentError

    if isinstance(exc, AckPendingError):
        typer.echo("Error: round ack pending — 本轮仍有缺/无效表态（含原因）", err=True)
        for persona in exc.missing:
            reason = exc.missing_reasons.get(persona) or "缺文件（未发言）"
            typer.echo(f"  - {persona}: {reason}", err=True)
        return
    if isinstance(exc, OpenActionItemsError):
        if exc.items:
            typer.echo("Error: action items 未清零 — 无法关闭（closed = 零尾款）", err=True)
            for item in exc.items:
                typer.echo(
                    f"  - #{item.id} {item.title} (owner: {item.owner}) — "
                    "用 `map topic action-item complete/cancel` 清零后再 close",
                    err=True,
                )
        else:
            typer.echo(f"Error: action-items.yaml 无法解析 — {exc.detail or '未知原因'}", err=True)
        return
    if isinstance(exc, OpenExperimentError):
        typer.echo(
            "Error: experiment non-terminal — 关联实验未达 terminal，无法关闭话题",
            err=True,
        )
        for exp in exc.experiments:
            typer.echo(
                f"  - experiment {exp.id} (phase={exp.phase}) non-terminal — "
                "等实验 done/cancelled 后再 close",
                err=True,
            )
        return
    typer.echo(f"Error: {exc}", err=True)


def local_validated_write_flow(
    *,
    action_name: str,
    topic: str,
    actor_persona: str,
    validate_call,
) -> dict:
    """local plane 验证型写：本地校验 → 本地写回 → 本地审计（零客户端/零网络）。

    语义与 :func:`validated_write_flow`（remote）对齐；门禁复用
    ``map_fs.validation``（与 server 单一真值同源）。``validate_call`` 为
    ``(FsTopic) -> dict[str, str]`` 的共享校验函数调用。
    """
    from map_fs import (
        AckPendingError,
        OpenActionItemsError,
        OpenExperimentError,
        TopicOwnerError,
        TopicStateError,
        update_topic_index,
    )

    from cli.commands.fs import (
        _content_root_name,
        _require_local_topic,
        _workspace,
        fs_topic_to_detail_read,
    )

    workspace = _workspace()
    parsed = _require_local_topic(workspace, topic)
    try:
        fields = validate_call(parsed)
    except (
        AckPendingError,
        OpenActionItemsError,
        OpenExperimentError,
        TopicStateError,
        TopicOwnerError,
    ) as exc:
        _render_local_validate_error(exc)
        raise typer.Exit(1) from exc
    update_topic_index(workspace, topic, content_root=_content_root_name(workspace), **fields)
    _append_local_audit(workspace, topic, action_name, actor_persona, fields)
    refreshed = _require_local_topic(workspace, topic)
    topic_payload = fs_topic_to_detail_read(refreshed).model_dump(
        mode="json", exclude={"comments", "action_items", "action_items_error"}
    )
    typer.echo(f"local-plane {action_name} committed: {topic} {fields}")
    return {
        "action": action_name,
        "slug": topic,
        "fields": fields,
        "committed": True,
        "source": "local-plane",
        "topic": topic_payload,
    }



def validated_write_flow(
    c: MAPClient,
    *,
    pid: uuid.UUID,
    action_name: str,
    topic: str,
    validate_call,
) -> dict:
    """验证型写核心流程（复用调用方 client）：validate → 本地写回 → commit。

    - server 校验权限与 ack 完整性（远程模式凭本地解析的 evidence）；
    - 写回永远发生在 CLI 本地（内容主权在文件系统）；
    - commit 凭 HMAC token 完成审计 + 通知 + 投影缓存刷新。
    """
    from map_fs import update_topic_index
    from map_types.schemas.fs import FsWriteCommitRequest

    from cli.commands.fs import (
        _content_root_name,
        _render_validate_error,
        _require_local_topic,
        _workspace,
        fs_topic_to_detail_read,
    )

    workspace = _workspace()
    # evidence：本地解析快照（旧客户端兼容字段，远程校验只信已 CAS 发布的
    # 投影；validate 前的增量 sync 会先把本地变更发布上去并取回 revision）。
    parsed = _require_local_topic(workspace, topic)
    evidence = fs_topic_to_detail_read(parsed)
    index_path = (
        workspace / _content_root_name(workspace) / "topics" / topic / "index.md"
    )
    original_index = index_path.read_bytes()

    plane_status = c.fs_plane_status(pid)
    base_revision: int | None = None
    if plane_status.mode != "local-fs":
        # 增量同步（禁删）：远端独有对象不会被验证型写隐式清掉——全量 PUT
        # 会静默删除投影中本地缺失的对象，绕过 map sync publish 的 tombstone 门禁。
        from cli.fs_projection import sync_projection

        result = sync_projection(c, pid=pid, workspace=workspace, allow_deletes=False)
        if result.get("sync_state") == "skipped-deletes":
            typer.echo(
                "Warning: projection sync before validate skipped because remote "
                "objects would be deleted. Preview with `map sync diff`, then "
                "`map sync publish --yes`.",
                err=True,
            )
        base_revision = int(
            result.get("projection_revision") or result["base_revision"]
        )

    try:
        verdict = validate_call(c, pid, evidence, base_revision)
    except MAPHTTPError as exc:
        _render_validate_error(exc)
        raise
    update_topic_index(
        workspace, topic, content_root=_content_root_name(workspace), **verdict.fields
    )
    try:
        commit = c.fs_write_commit(
            pid,
            FsWriteCommitRequest(
                token=verdict.token,
                slug=topic,
                action=verdict.action,
                applied_fields=verdict.fields,
            ),
        )
    except Exception:
        # validate 成功但 CAS commit 失败时，不能留下未审计的本地状态。
        index_path.write_bytes(original_index)
        raise
    # validate verdict 携带的是写回前快照。commit 成功后必须从本地
    # FS 事实源重新解析，避免 fields.round=round2 但 topic 仍显示
    # round1 的自相矛盾成功响应。
    refreshed = _require_local_topic(workspace, topic)
    topic_payload = fs_topic_to_detail_read(refreshed).model_dump(
        mode="json", exclude={"comments", "action_items", "action_items_error"}
    )
    return {
        "action": commit.action,
        "slug": commit.slug,
        "fields": verdict.fields,
        "committed": commit.accepted,
        "projection_revision": commit.projection_revision,
        "flow": f"{action_name}: validate → local write-back → commit",
        "topic": topic_payload,
    }


def _run_validated_write(
    *,
    action_name: str,
    topic: str,
    project: uuid.UUID | None,
    project_key: str | None,
    validate_call,
) -> None:
    """``map topic advance-round|close`` 入口：包一层 client 构造与输出渲染。"""

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        return validated_write_flow(
            c, pid=pid, action_name=action_name, topic=topic, validate_call=validate_call
        )

    runner._run(action)
