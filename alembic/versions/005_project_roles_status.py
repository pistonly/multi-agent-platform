"""project roles and status documents

Revision ID: 005
Revises: 004
Create Date: 2026-06-24
"""

from __future__ import annotations

import re
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slugify(name: str) -> str:
    key = re.sub(r"[^a-z0-9-_]+", "-", name.lower()).strip("-")
    if not key or not key[0].isalnum():
        key = f"p-{key}" if key else "project"
    return key[:64]


def upgrade() -> None:
    op.create_table(
        "project_status_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("author_agent_id", sa.Uuid(), nullable=False),
        sa.Column("change_note", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["author_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version", name="uq_project_status_version"),
    )
    op.create_index(op.f("ix_project_status_versions_project_id"), "project_status_versions", ["project_id"], unique=False)

    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("project_key", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("current_status_version", sa.Integer(), server_default="0", nullable=False))

    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key("fk_agents_project_id", "projects", ["project_id"], ["id"])
        batch_op.create_index("ix_agents_project_id", ["project_id"], unique=False)

    bind = op.get_bind()
    projects = bind.execute(sa.text("SELECT id, name, created_at FROM projects")).fetchall()
    used_keys: set[str] = set()
    admin_id = bind.execute(
        sa.text("SELECT id FROM agents WHERE role = 'admin' ORDER BY created_at ASC LIMIT 1")
    ).scalar()
    if admin_id is None:
        admin_id = bind.execute(sa.text("SELECT id FROM agents ORDER BY created_at ASC LIMIT 1")).scalar()

    for project_id, name, created_at in projects:
        base_key = _slugify(name)
        key = base_key
        suffix = 2
        while key in used_keys:
            key = f"{base_key[:58]}-{suffix}"
            suffix += 1
        used_keys.add(key)
        bind.execute(
            sa.text("UPDATE projects SET project_key = :key WHERE id = :id"),
            {"key": key, "id": project_id},
        )
        if admin_id is not None:
            status_id = str(uuid.uuid4())
            ts = created_at if isinstance(created_at, str) else (
                created_at.isoformat() if created_at is not None else ""
            )
            content = f"""# Current Status — {key}

## 当前目标

- （待填写）

## 进行中的实验

- （暂无）

## 阻塞 / 风险

- 无

## 下一步

- （待填写）

---
_最后更新：{ts} · 版本 v1_
"""
            bind.execute(
                sa.text(
                    """
                    INSERT INTO project_status_versions
                    (id, project_id, version, content_md, author_agent_id, change_note, created_at)
                    VALUES (:id, :project_id, 1, :content_md, :author_id, '初始版本', CURRENT_TIMESTAMP)
                    """
                ),
                {
                    "id": status_id,
                    "project_id": project_id,
                    "content_md": content,
                    "author_id": admin_id,
                },
            )
            bind.execute(
                sa.text("UPDATE projects SET current_status_version = 1 WHERE id = :id"),
                {"id": project_id},
            )

    if projects:
        first_project_id = projects[0][0]
        bind.execute(
            sa.text(
                """
                UPDATE agents
                SET project_id = :project_id
                WHERE role = 'agent' AND project_id IS NULL
                """
            ),
            {"project_id": first_project_id},
        )

    with op.batch_alter_table("projects") as batch_op:
        batch_op.alter_column("project_key", nullable=False)
        batch_op.create_unique_constraint("uq_projects_project_key", ["project_key"])
        batch_op.create_index("ix_projects_project_key", ["project_key"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_index("ix_agents_project_id")
        batch_op.drop_constraint("fk_agents_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")

    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_index("ix_projects_project_key")
        batch_op.drop_constraint("uq_projects_project_key", type_="unique")
        batch_op.drop_column("current_status_version")
        batch_op.drop_column("project_key")

    op.drop_index(op.f("ix_project_status_versions_project_id"), table_name="project_status_versions")
    op.drop_table("project_status_versions")
