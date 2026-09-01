import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from map_types.enums import (
    AgentRole,
)
from map_types.persona import persona_from_agent_name
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import Base

if TYPE_CHECKING:
    from server.domain.models.experiment import (
        Comment,
        Experiment,
        ExperimentLog,
        PlanVersion,
        Review,
    )
    from server.domain.models.topic import Topic, TopicDecision




class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_root: Mapped[str] = mapped_column(
        String(64), nullable=False, default="map", server_default="map"
    )
    fs_freshness_sla_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_status_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    experiments: Mapped[list["Experiment"]] = relationship(back_populates="project")
    status_versions: Mapped[list["ProjectStatusVersion"]] = relationship(
        back_populates="project", order_by="ProjectStatusVersion.version"
    )
    topics: Mapped[list["Topic"]] = relationship(back_populates="project")
    topic_decisions: Mapped[list["TopicDecision"]] = relationship(back_populates="project")


class ProjectStatusVersion(Base):
    __tablename__ = "project_status_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_project_status_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    author_agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="status_versions")
    author: Mapped["Agent"] = relationship()


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    api_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    api_token_prefix: Mapped[str] = mapped_column(String(8), nullable=False, default="", index=True)
    # T01（2026-08）：sha256(token) 十六进制等值查询列。token 本身是 256-bit
    # 随机数，无暴力破解面，bcrypt 慢哈希属过度防御且让每个请求付出 ~200ms；
    # 此列将认证退化为 O(微秒) 唯一索引查找。NULL = legacy bcrypt-only 行，
    # 由 get_agent_by_token 在首次成功 bcrypt 校验后惰性回填。
    api_token_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    role: Mapped[AgentRole] = mapped_column(Enum(AgentRole), default=AgentRole.agent, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # D1: waker 心跳可见性 — last_api_seen_at 由任意 /me/work 刷新；last_waker_poll_at
    # 仅 waker 特征请求 (client=waker) 刷新；stale 判定只看后者，降级场景人工 work
    # 不污染归属。null → never（该 agent 无 waker 心跳记录），不进 WARN。
    last_api_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_waker_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # I1（实验 b3ec2e4d A1）：waker 长 runtime 会话（claude 子进程）期间
    # busy 状态标记。null → idle（不在 runtime 调用中），按 D1 既有
    # last_waker_poll_at 路径判 stale；非 null → busy，按 status_service
    # 的 busy 容忍阈值判活，不算 stale。
    last_busy_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project | None"] = relationship()

    # ---- Capability model --------------------------------------------------
    # authz experiment (0e6926fa) PR2: capability strings decouple
    # ``system:*`` gating from the AgentRole enum so future capabilities
    # don't churn the schema. Convention: ``<namespace>:<action>``.
    # ``system:*`` is admin-grade (audit / cross-project). Persona-scoped
    # capabilities (e.g. ``host:scan_stalled``) match the agent's trailing
    # name segment (``multi-agent-platform-host`` / ``<project_key>-host``
    # → ``host:*``) — see the ``persona`` property.
    _ADMIN_CAPABILITY_PREFIX = "system:"
    _PERSONA_CAPABILITIES: dict[str, frozenset[str]] = {
        "host": frozenset(
            {
                "system:scan_stalled",
                "system:audit_export",
                "system:cross_persona_call",
            }
        ),
        "reviewer": frozenset({"review:submit", "review:accept_result"}),
        "participant": frozenset({"topic:comment"}),
    }

    @property
    def persona(self) -> str | None:
        """Infer persona from the agent name's trailing ``-<persona>`` segment.

        Delegates to ``map_types.persona.persona_from_agent_name`` so CLI and
        server share one rule: ``{project_key}-host`` and
        ``multi-agent-platform-host`` both resolve to ``host``.
        """
        return persona_from_agent_name(self.name)

    def has_capability(self, capability: str) -> bool:
        """Return True if this agent can perform ``capability``.

        Order:
        1. ``role == admin`` → any ``system:*`` capability passes.
        2. Persona match → look up the persona's capability set.
        3. Otherwise → False.
        """
        if self.role == AgentRole.admin and capability.startswith(self._ADMIN_CAPABILITY_PREFIX):
            return True
        persona = self.persona
        if persona is not None:
            return capability in self._PERSONA_CAPABILITIES[persona]
        return False
    created_experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="creator", foreign_keys="Experiment.creator_agent_id"
    )
    executed_experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="executor", foreign_keys="Experiment.executor_agent_id"
    )
    escalation_target_experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="escalation_target", foreign_keys="Experiment.escalation_target_agent_id"
    )
    plan_versions: Mapped[list["PlanVersion"]] = relationship(back_populates="author")
    reviews: Mapped[list["Review"]] = relationship(back_populates="reviewer")
    comments: Mapped[list["Comment"]] = relationship(back_populates="author")
    logs: Mapped[list["ExperimentLog"]] = relationship(back_populates="author")

