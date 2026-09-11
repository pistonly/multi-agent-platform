"""FS 投影缓存存储（T06 从 fs_source_service 拆出）。

职责边界：``FsProjection`` 行的读写与一致性协议——push 上行全量
（``upsert_fs_projection``）、增量 delta（``apply_fs_projection_delta``）、
验证型写回后的字段修补（``apply_fields_to_projection``）、投影行读取与
payload 校验降级（``get_fs_projection`` / ``projection_payload_*``）、
超限防线（``_payload_size_ok`` + ``FsProjectionTooLargeError``）与
发布者权限（``_ensure_projection_publisher_allowed`` 一族）。

这一簇只被远程 / 容器部署（workspace 不可达）走 ``map sync publish`` 上行时
使用；同机部署的实时解析读路径仍在 ``fs_source_service``。拆出后
``fs_source_service`` 通过顶层 re-export 维持
``fs_svc.upsert_fs_projection`` 等既有导入路径不变。

依赖方向：本模块顶层只 import models / enums / schemas / errors；对
``fs_source_service`` 的 ``content_root_name`` / ``workspace_fs_available`` /
``content_source_meta`` 三个 scan 侧 helper 采用函数内 lazy import——
反方向（fs_source_service re-export 本模块）为顶层 import，避免导入环
（与 notification_stalled 同一模式）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from map_types.schemas.fs import (
    FsExperimentRead,
    FsProjectionDeltaRequest,
    FsProjectionDeltaResult,
    FsProjectionInventoryRead,
    FsProjectionMetaRead,
    FsProjectionObjectHash,
    FsProjectionPushRequest,
    FsTopicDetailRead,
    fs_experiment_content_hash,
    fs_projection_content_hash,
    fs_topic_content_hash,
)
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, FsProjection, Project
from server.services.errors import ConflictError, ForbiddenError

logger = logging.getLogger(__name__)

# 投影缓存上限：超限 413（payload 是 JSON 快照，超过该量级说明 push 用法
# 变形——应当按项目拆分或改走 Git，而不是把 server 当内容仓库）。
_PROJECTION_MAX_TOPICS = 2000
_PROJECTION_MAX_BYTES = 8 * 1024 * 1024
_PROJECTION_MAX_OBJECT_BYTES = 1024 * 1024


class FsProjectionTooLargeError(Exception):
    """投影快照超限（把它当内容仓库用了）。"""


def get_fs_projection(db: Session, project: Project) -> FsProjection | None:
    return db.scalar(
        select(FsProjection).where(FsProjection.project_id == project.id)
    )


def projection_payload_topics(row: FsProjection | None) -> list[FsTopicDetailRead]:
    if row is None:
        return []
    payload = row.payload_json or {}
    try:
        return [FsTopicDetailRead.model_validate(t) for t in payload.get("topics", [])]
    except Exception:
        logger.warning(
            "fs projection topics 快照校验失败，按空处理（project=%s revision=%s）",
            row.project_id,
            getattr(row, "revision", None),
            exc_info=True,
        )
        return []  # 脏快照按空处理；下一次 push 覆盖修复


def projection_payload_experiments(row: FsProjection | None) -> list[FsExperimentRead]:
    if row is None:
        return []
    payload = row.payload_json or {}
    try:
        return [FsExperimentRead.model_validate(e) for e in payload.get("experiments", [])]
    except Exception:
        logger.warning(
            "fs projection experiments 快照校验失败，按空处理（project=%s revision=%s）",
            row.project_id,
            getattr(row, "revision", None),
            exc_info=True,
        )
        return []


def _ensure_content_root_matches(project: Project, client_root: str | None) -> None:
    from map_types.schemas.project import normalize_content_root

    from server.services.fs_source_service import content_root_name

    expected = content_root_name(project)
    if client_root is None:
        return
    got = normalize_content_root(client_root)
    if got != expected:
        raise ConflictError(
            f"content_root mismatch: project has {expected!r}, client sent {got!r}",
            error="content_root_mismatch",
        )


def _is_project_host(agent: Agent, project: Project) -> bool:
    # 统一走 Agent.persona（尾段 -host），canonical 与 <project_key>-host 同判。
    _ = project
    return agent.persona == "host"


def _is_projection_sync_agent(agent: Agent) -> bool:
    return agent.name.endswith("-sync")


def _project_host_agent(db: Session, project: Project) -> Agent | None:
    agents = list(db.scalars(select(Agent).where(Agent.project_id == project.id)))
    return next((agent for agent in agents if _is_project_host(agent, project)), None)


def _ensure_projection_publisher_allowed(agent: Agent, project: Project) -> None:
    if (
        agent.role == AgentRole.admin
        or _is_project_host(agent, project)
        or _is_projection_sync_agent(agent)
    ):
        return
    raise ForbiddenError(
        "FS projection push is restricted to the project host, admin, or an "
        "explicit *-sync agent"
    )


def _projection_owner_for_first_push(
    db: Session, project: Project, agent: Agent
) -> Agent:
    if _is_project_host(agent, project):
        return agent
    host = _project_host_agent(db, project)
    if host is not None:
        return host
    if agent.role == AgentRole.admin:
        return agent
    raise ForbiddenError(
        "A project host agent must exist before a *-sync agent can publish the FS projection"
    )


def upsert_fs_projection(
    db: Session, project: Project, agent: Agent, payload: FsProjectionPushRequest
) -> FsProjectionMetaRead:
    _ensure_projection_publisher_allowed(agent, project)
    _ensure_content_root_matches(project, payload.content_root)
    _payload_size_ok(payload.topics, payload.experiments)
    computed_hash = fs_projection_content_hash(payload.topics, payload.experiments)
    if payload.content_hash is not None and payload.content_hash != computed_hash:
        raise ConflictError(
            "projection content_hash does not match the canonical topics/experiments payload"
        )
    body = payload.model_dump(mode="json")
    body["content_hash"] = computed_hash
    if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > _PROJECTION_MAX_BYTES:
        raise FsProjectionTooLargeError(
            f"projection too large: payload exceeds {_PROJECTION_MAX_BYTES} bytes"
        )

    row = get_fs_projection(db, project)
    if row is None:
        if payload.base_revision is not None:
            raise ConflictError("first projection push must not set base_revision")
        owner = _projection_owner_for_first_push(db, project, agent)
        row = FsProjection(
            project_id=project.id,
            client_workspace=payload.client_workspace,
            publisher_agent_id=agent.id,
            owner_agent_id=owner.id,
            revision=1,
        )
        db.add(row)
        row.pushed_by_agent_id = agent.id
        row.payload_json = body
        row.content_hash = computed_hash
        row.pushed_at = datetime.now(timezone.utc)
        db.flush()
        return _projection_meta(row)
    else:
        publisher_agent_id = row.publisher_agent_id
        owner_agent_id = row.owner_agent_id
        if publisher_agent_id is None:
            # migration 048 leaves legacy rows unbound; the first eligible P0
            # publisher adopts the row in place.  Adoption never requires
            # base_revision and keeps the current revision: it initialises the
            # unbound snapshot and persists the owner/publisher binding (which is what
            # unblocks the owner gate, ensure_fs_topic_owner).  CAS-guarded so
            # a concurrent adopter wins cleanly.  Doing this idempotently (rather
            # than falling into the retry short-circuit) is what makes a content-
            # identical repush of a legacy row succeed on its first try.
            result = db.execute(
                update(FsProjection)
                .where(
                    FsProjection.id == row.id,
                    FsProjection.publisher_agent_id.is_(None),
                )
                .values(
                    pushed_by_agent_id=agent.id,
                    publisher_agent_id=agent.id,
                    owner_agent_id=_projection_owner_for_first_push(db, project, agent).id,
                    client_workspace=payload.client_workspace,
                    payload_json=body,
                    content_hash=computed_hash,
                    revision=row.revision,
                )
            )
            if result.rowcount == 1:
                db.expire(row)
                db.refresh(row)
            return _projection_meta(row)
        elif agent.id != row.publisher_agent_id and agent.role != AgentRole.admin:
            raise ConflictError(
                "FS projection is bound to another single publisher; use that publisher "
                "or an admin recovery path"
            )

        # Identical retries are idempotent even if the caller only learned the
        # successful revision after a transport failure.  They still (a) persist
        # the adoption binding for pre-P0 legacy rows — otherwise the remote
        # owner gate stays 403-locked forever because the same-content retry is
        # short-circuited before the binding is written — and (b) refresh
        # pushed_at as a publisher heartbeat so the freshness SLA can recover.
        if row.content_hash == computed_hash:
            if row.publisher_agent_id is None:
                adopted = db.execute(
                    update(FsProjection)
                    .where(
                        FsProjection.id == row.id,
                        FsProjection.publisher_agent_id.is_(None),
                    )
                    .values(
                        publisher_agent_id=publisher_agent_id,
                        owner_agent_id=owner_agent_id,
                        pushed_by_agent_id=agent.id,
                        pushed_at=datetime.now(timezone.utc),
                    )
                )
                db.expire(row)
                db.refresh(row)
                if adopted.rowcount != 1 and (
                    row.publisher_agent_id != agent.id and agent.role != AgentRole.admin
                ):
                    raise ConflictError(
                        "FS projection is bound to another single publisher; use that "
                        "publisher or an admin recovery path"
                    )
            else:
                db.execute(
                    update(FsProjection)
                    .where(
                        FsProjection.id == row.id,
                        FsProjection.revision == row.revision,
                        FsProjection.content_hash == computed_hash,
                    )
                    .values(
                        pushed_by_agent_id=agent.id,
                        pushed_at=datetime.now(timezone.utc),
                    )
                )
                db.expire(row)
                db.refresh(row)
            return _projection_meta(row)
        if payload.base_revision is None:
            raise ConflictError(
                f"projection base_revision is required; current revision is {row.revision}"
            )
        if payload.base_revision != row.revision:
            raise ConflictError(
                f"projection revision conflict: expected {row.revision}, "
                f"got {payload.base_revision}; pull status/diff before retrying"
            )
        pushed_at = datetime.now(timezone.utc)
        result = db.execute(
            update(FsProjection)
            .where(
                FsProjection.id == row.id,
                FsProjection.revision == payload.base_revision,
            )
            .values(
                pushed_by_agent_id=agent.id,
                publisher_agent_id=publisher_agent_id,
                owner_agent_id=owner_agent_id,
                client_workspace=payload.client_workspace,
                payload_json=body,
                content_hash=computed_hash,
                revision=payload.base_revision + 1,
                pushed_at=pushed_at,
            )
        )
        if result.rowcount != 1:
            db.expire(row)
            db.refresh(row)
            raise ConflictError(
                f"projection revision conflict: expected {row.revision}; "
                "another writer committed first"
            )
        db.expire(row)
        db.refresh(row)
    return _projection_meta(row)


def _projection_meta(row: FsProjection, *, project: Project | None = None) -> FsProjectionMetaRead:
    from server.services.fs_source_service import content_root_name

    payload = row.payload_json or {}
    return FsProjectionMetaRead(
        pushed_at=row.pushed_at,
        pushed_by_agent_id=row.pushed_by_agent_id,
        publisher_agent_id=row.publisher_agent_id,
        owner_agent_id=row.owner_agent_id,
        client_workspace=row.client_workspace,
        topic_count=len(payload.get("topics", [])),
        experiment_count=len(payload.get("experiments", [])),
        projection_revision=row.revision,
        content_hash=row.content_hash,
        content_root=content_root_name(project) if project is not None else payload.get("content_root"),
    )


def projection_meta(db: Session, project: Project) -> FsProjectionMetaRead | None:
    row = get_fs_projection(db, project)
    return _projection_meta(row, project=project) if row is not None else None


def projection_inventory(db: Session, project: Project) -> FsProjectionInventoryRead | None:
    from server.services.fs_source_service import content_root_name, content_source_meta

    row = get_fs_projection(db, project)
    if row is None:
        return None
    topics = projection_payload_topics(row)
    experiments = projection_payload_experiments(row)
    objects = [
        FsProjectionObjectHash(
            kind="topic", slug=topic.slug, content_hash=fs_topic_content_hash(topic)
        )
        for topic in topics
    ]
    objects.extend(
        FsProjectionObjectHash(
            kind="experiment",
            slug=experiment.slug,
            content_hash=fs_experiment_content_hash(experiment),
        )
        for experiment in experiments
    )
    objects.sort(key=lambda item: (item.kind, item.slug))
    return FsProjectionInventoryRead(
        projection_revision=row.revision,
        content_hash=row.content_hash or fs_projection_content_hash(topics, experiments),
        content_root=content_root_name(project),
        publisher_agent_id=row.publisher_agent_id,
        pushed_at=row.pushed_at,
        objects=objects,
        source=content_source_meta(db, project),
    )


def _object_payload_bytes(item: FsTopicDetailRead | FsExperimentRead) -> int:
    return len(item.model_dump_json().encode("utf-8"))


def _payload_size_ok(topics: list[FsTopicDetailRead], experiments: list[FsExperimentRead]) -> None:
    if len(topics) > _PROJECTION_MAX_TOPICS:
        raise FsProjectionTooLargeError(
            f"projection too large: {len(topics)} topics (max {_PROJECTION_MAX_TOPICS})"
        )
    if len(experiments) > _PROJECTION_MAX_TOPICS:
        raise FsProjectionTooLargeError(
            f"projection too large: {len(experiments)} experiments (max {_PROJECTION_MAX_TOPICS})"
        )
    for topic in topics:
        size = _object_payload_bytes(topic)
        if size > _PROJECTION_MAX_OBJECT_BYTES:
            raise FsProjectionTooLargeError(
                f"topic {topic.slug!r} exceeds {_PROJECTION_MAX_OBJECT_BYTES} bytes"
            )
    for experiment in experiments:
        size = _object_payload_bytes(experiment)
        if size > _PROJECTION_MAX_OBJECT_BYTES:
            raise FsProjectionTooLargeError(
                f"experiment {experiment.slug!r} exceeds {_PROJECTION_MAX_OBJECT_BYTES} bytes"
            )
    body = {
        "topics": [t.model_dump(mode="json") for t in topics],
        "experiments": [e.model_dump(mode="json") for e in experiments],
    }
    if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > _PROJECTION_MAX_BYTES:
        raise FsProjectionTooLargeError(
            f"projection too large: payload exceeds {_PROJECTION_MAX_BYTES} bytes"
        )


def apply_fs_projection_delta(
    db: Session,
    project: Project,
    agent: Agent,
    payload: FsProjectionDeltaRequest,
) -> FsProjectionDeltaResult:
    from server.services.fs_source_service import content_root_name

    _ensure_projection_publisher_allowed(agent, project)
    _ensure_content_root_matches(project, payload.content_root)
    row = get_fs_projection(db, project)
    if row is None:
        raise ConflictError(
            "no projection exists; run a full `map sync publish --full` to bootstrap",
            error="fs_projection_missing",
        )
    if agent.id != row.publisher_agent_id and agent.role != AgentRole.admin:
        raise ConflictError(
            "FS projection is bound to another single publisher; use that publisher "
            "or an admin recovery path",
            error="fs_projection_conflict",
        )
    topics = {item.slug: item for item in projection_payload_topics(row)}
    experiments = {item.slug: item for item in projection_payload_experiments(row)}
    tombstones = 0
    applied = 0
    for change in payload.changes:
        kind = change.kind
        slug = change.slug
        if kind == "topic_upsert":
            if not isinstance(change.value, FsTopicDetailRead):
                raise ConflictError(
                    f"topic_upsert {slug!r} requires a topic value",
                    error="fs_projection_delta_invalid",
                )
            topics[slug] = change.value
            applied += 1
        elif kind == "topic_delete":
            existing = topics.get(slug)
            if existing is None:
                raise ConflictError(
                    f"topic_delete {slug!r} does not exist on the current projection",
                    error="fs_projection_delta_invalid",
                )
            if not change.expected_hash:
                raise ConflictError(
                    f"topic_delete {slug!r} requires expected_hash",
                    error="fs_projection_delta_invalid",
                )
            current_hash = fs_topic_content_hash(existing)
            if change.expected_hash != current_hash:
                raise ConflictError(
                    f"topic_delete {slug!r} hash mismatch",
                    error="fs_projection_delta_invalid",
                )
            del topics[slug]
            tombstones += 1
            applied += 1
        elif kind == "experiment_upsert":
            if not isinstance(change.value, FsExperimentRead):
                raise ConflictError(
                    f"experiment_upsert {slug!r} requires an experiment value",
                    error="fs_projection_delta_invalid",
                )
            experiments[slug] = change.value
            applied += 1
        elif kind == "experiment_delete":
            existing_exp = experiments.get(slug)
            if existing_exp is None:
                raise ConflictError(
                    f"experiment_delete {slug!r} does not exist on the current projection",
                    error="fs_projection_delta_invalid",
                )
            if not change.expected_hash:
                raise ConflictError(
                    f"experiment_delete {slug!r} requires expected_hash",
                    error="fs_projection_delta_invalid",
                )
            current_hash = fs_experiment_content_hash(existing_exp)
            if change.expected_hash != current_hash:
                raise ConflictError(
                    f"experiment_delete {slug!r} hash mismatch",
                    error="fs_projection_delta_invalid",
                )
            del experiments[slug]
            tombstones += 1
            applied += 1
        else:
            raise ConflictError(
                f"unknown delta change kind: {kind}",
                error="fs_projection_delta_invalid",
            )

    topic_list = list(topics.values())
    experiment_list = list(experiments.values())
    _payload_size_ok(topic_list, experiment_list)
    result_hash = fs_projection_content_hash(topic_list, experiment_list)
    if result_hash != payload.result_content_hash:
        raise ConflictError(
            "result_content_hash does not match the reconstructed snapshot",
            error="fs_projection_hash_mismatch",
        )
    if not payload.changes or result_hash == row.content_hash:
        # noop delta = publisher heartbeat：刷新 pushed_at 让 freshness SLA
        # 可自愈（同内容定期 sync 证明发布端仍活跃），不 bump revision。
        db.execute(
            update(FsProjection)
            .where(
                FsProjection.id == row.id,
                FsProjection.revision == row.revision,
                FsProjection.content_hash == row.content_hash,
            )
            .values(
                pushed_by_agent_id=agent.id,
                client_workspace=payload.client_workspace,
                pushed_at=datetime.now(timezone.utc),
            )
        )
        db.expire(row)
        db.refresh(row)
        meta = _projection_meta(row, project=project)
        return FsProjectionDeltaResult(**meta.model_dump(), applied_changes=0, tombstones=0, noop=True)
    if payload.base_revision != row.revision:
        raise ConflictError(
            f"projection revision conflict: expected {row.revision}, "
            f"got {payload.base_revision}; pull status/diff before retrying",
            error="fs_projection_conflict",
        )
    body = {
        "client_workspace": payload.client_workspace,
        "content_root": content_root_name(project),
        "topics": [item.model_dump(mode="json") for item in topic_list],
        "experiments": [item.model_dump(mode="json") for item in experiment_list],
        "content_hash": result_hash,
    }
    pushed_at = datetime.now(timezone.utc)
    result = db.execute(
        update(FsProjection)
        .where(
            FsProjection.id == row.id,
            FsProjection.revision == payload.base_revision,
        )
        .values(
            pushed_by_agent_id=agent.id,
            client_workspace=payload.client_workspace,
            payload_json=body,
            content_hash=result_hash,
            revision=payload.base_revision + 1,
            pushed_at=pushed_at,
        )
    )
    if result.rowcount != 1:
        db.expire(row)
        db.refresh(row)
        raise ConflictError(
            f"projection revision conflict: expected {row.revision}; "
            "another writer committed first",
            error="fs_projection_conflict",
        )
    db.expire(row)
    db.refresh(row)
    meta = _projection_meta(row, project=project)
    return FsProjectionDeltaResult(
        **meta.model_dump(),
        applied_changes=applied,
        tombstones=tombstones,
        noop=False,
    )


def apply_fields_to_projection(
    db: Session,
    project: Project,
    slug: str,
    fields: dict[str, str],
    *,
    base_revision: int,
) -> int | None:
    """commit 后把写回字段应用到投影快照（远程模式下保持读视图新鲜）。

    本地可达时投影不是读源，跳过即可；快照无该话题也静默跳过（下次
    push 全量修复）。
    """
    from server.services.fs_source_service import workspace_fs_available

    if workspace_fs_available(project):
        return None
    row = get_fs_projection(db, project)
    if row is None:
        raise ConflictError("FS projection missing; run `map sync publish --full` before committing")
    if row.revision != base_revision:
        raise ConflictError(
            f"projection revision conflict: validated at {base_revision}, "
            f"current revision is {row.revision}; local write was not committed"
        )
    # 独立副本：不能就地改 ORM 缓存的 dict——否则新旧值内容相等，
    # SQLAlchemy 会把变更历史剪空，UPDATE 根本不会发出。
    payload = json.loads(json.dumps(row.payload_json or {}, ensure_ascii=False))
    changed = False
    for topic in payload.get("topics", []):
        if topic.get("slug") != slug:
            continue
        if "round" in fields:
            topic["discussion_round"] = fields["round"]
        if "status" in fields:
            topic["status"] = fields["status"]
        if "close_reason" in fields:
            topic["close_reason"] = fields["close_reason"]
        if "waive_reason" in fields:
            topic["waive_reason"] = fields["waive_reason"]
        changed = True
        break
    if changed:
        try:
            topics = [
                FsTopicDetailRead.model_validate(item)
                for item in payload.get("topics", [])
            ]
        except Exception:
            logger.warning(
                "apply_fields 后投影 topics 重新校验失败，content_hash 将按空列表计算"
                "（project=%s slug=%s revision=%s）",
                project.id,
                slug,
                base_revision,
                exc_info=True,
            )
            topics = []
        experiments: list[FsExperimentRead] = []
        try:
            experiments = [
                FsExperimentRead.model_validate(item)
                for item in payload.get("experiments", [])
            ]
        except Exception:
            logger.warning(
                "apply_fields 后投影 experiments 重新校验失败，content_hash 将不含实验"
                "（project=%s slug=%s revision=%s）",
                project.id,
                slug,
                base_revision,
                exc_info=True,
            )
            experiments = []
        content_hash = fs_projection_content_hash(topics, experiments)
        payload["content_hash"] = content_hash
        result = db.execute(
            update(FsProjection)
            .where(FsProjection.id == row.id, FsProjection.revision == base_revision)
            .values(
                payload_json=payload,
                content_hash=content_hash,
                revision=base_revision + 1,
                pushed_at=datetime.now(timezone.utc),
            )
        )
        if result.rowcount != 1:
            raise ConflictError(
                "projection revision conflict: another writer committed before this token"
            )
        db.expire(row)
        db.refresh(row)
        return row.revision
    raise ConflictError(
        f"projection topic '{slug}' is missing; push the canonical FS tree before retrying"
    )
