"""优化任务 T09 / T16 的回归测试（见 docs/OPTIMIZATION-TASKS.md）。

T09：topics 列表 FS 合并路径——DB 段改 SQL 侧分页（slug 反查排除 +
偏移续页），不再 page_size=None 全量拉取内存合并。

T16：build_projects_status 的 recent 每项目 5 条改窗口函数在 SQL 侧
限量，第 6 名起不离开数据库。
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path

from map_fs import write_topic_index
from map_types.enums import ExperimentPhase, TopicStatus
from sqlalchemy import select

from server.domain.models import Agent, AgentRole, Experiment, Project, Topic
from server.services.auth import create_agent
from server.services.project_service import build_projects_status
from tests.test_perf_count_sql_fixture import count_sql_calls

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# T16: recent 窗口函数
# ---------------------------------------------------------------------------


def _seed_experiments(db_session, *, count: int = 8):
    project = Project(
        project_key=f"t16-{uuid_mod.uuid4().hex[:8]}",
        name="T16 Tests",
        workspace_path="/tmp/t16",
    )
    db_session.add(project)
    db_session.flush()
    creator, _ = create_agent(db_session, "t16-creator", AgentRole.agent, project_id=project.id)
    titles: list[str] = []
    for i in range(count):
        stamp = NOW - timedelta(hours=count - i)
        title = f"exp-{i:02d}"
        titles.append(title)
        db_session.add(
            Experiment(
                project_id=project.id,
                creator_agent_id=creator.id,
                title=title,
                phase=ExperimentPhase.done,
                current_plan_version=1,
                created_at=stamp,
                updated_at=stamp,
            )
        )
    # 已删/已归档行不得进入 recent。
    for phase_flag, title in ((True, "exp-deleted"), (False, "exp-archived")):
        stamp = NOW + timedelta(minutes=5)
        db_session.add(
            Experiment(
                project_id=project.id,
                creator_agent_id=creator.id,
                title=title,
                phase=ExperimentPhase.done,
                current_plan_version=1,
                created_at=stamp,
                updated_at=stamp,
                deleted_at=stamp if phase_flag else None,
                archived_at=None if phase_flag else stamp,
            )
        )
    db_session.flush()
    return project, titles


def test_recent_limited_to_five_latest_per_project(db_session):
    project, titles = _seed_experiments(db_session, count=8)

    statuses = build_projects_status(db_session, [project])

    assert len(statuses) == 1
    recent = statuses[0].recent_experiments
    assert [e.title for e in recent] == titles[-5:][::-1]
    assert all(e.title not in {"exp-deleted", "exp-archived"} for e in recent)


def test_recent_uses_window_function_in_sql(db_session, engine):
    """T16 守卫：recent 查询走 ROW_NUMBER 窗口函数，行数在 SQL 侧限量。"""
    project, _titles = _seed_experiments(db_session, count=8)

    with count_sql_calls(engine, label="t16.recent") as counter:
        build_projects_status(db_session, [project])

    window_stmts = [
        s for s, _ in counter.statements if "ROW_NUMBER" in s.upper() and "experiments" in s
    ]
    assert len(window_stmts) == 1, counter.summary()
    # 窗口语句必须带 rn 过滤（限量发生在数据库内，而非 Python 截断）。
    assert "rn" in window_stmts[0]
    # 不得再出现无 LIMIT 的全量 experiments 行查询（GROUP BY 计数除外）。
    unbounded = [
        s
        for s, _ in counter.statements
        if "FROM experiments" in s
        and "COUNT" not in s.upper()
        and "ROW_NUMBER" not in s.upper()
        and "LIMIT" not in s.upper()
    ]
    assert unbounded == [], counter.summary()


# ---------------------------------------------------------------------------
# T09: topics 列表 FS 合并分页
# ---------------------------------------------------------------------------


def test_fs_merge_db_side_pagination(client, admin_headers, db_session, tmp_path: Path, engine) -> None:
    """T09：合并分页语义不变（FS 前置 + DB 续页 + total 一致），
    且 DB topics 查询必须带 LIMIT（不再全量拉取）。"""
    project = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": f"t09-{uuid_mod.uuid4().hex[:8]}",
            "name": "T09 Tests",
            "workspace_path": str(tmp_path),
        },
    ).json()
    pid = project["id"]
    db_project = db_session.get(Project, uuid_mod.UUID(pid))
    admin = db_session.scalar(select(Agent).where(Agent.role == "admin"))
    for i in range(5):
        db_session.add(
            Topic(
                project_id=db_project.id,
                creator_agent_id=admin.id,
                title=f"DB Topic {i}",
                slug=f"db-topic-{i}",
                status=TopicStatus.open,
                created_at=NOW - timedelta(hours=5 - i),
                updated_at=NOW - timedelta(hours=5 - i),
            )
        )
    db_session.commit()
    write_topic_index(tmp_path, "fs-page", title="FS Page Topic", creator="host")

    pages = []
    with count_sql_calls(engine, label="t09.merge") as counter:
        for page_no in (1, 2, 3):
            resp = client.get(
                f"/api/v1/projects/{pid}/topics",
                headers=admin_headers,
                params={"page": page_no, "page_size": 3},
            )
            assert resp.status_code == 200
            assert resp.headers["X-Total-Count"] == "6"
            pages.append([t["slug"] for t in resp.json()])

    # 语义与旧实现一致：FS 前置、无重复、覆盖全部 6 条。
    assert pages[0][0] == "fs-page"
    assert len(pages[0]) == 3 and len(pages[1]) == 3
    assert set(pages[0]) & set(pages[1]) == set()
    assert set(pages[0]) | set(pages[1]) | set(pages[2]) == {
        "fs-page",
        "db-topic-0",
        "db-topic-1",
        "db-topic-2",
        "db-topic-3",
        "db-topic-4",
    }

    # SQL 守卫：凡取行的 topics 查询必须 LIMIT；count 子查询除外。
    unbounded = [
        s
        for s, _ in counter.statements
        if "FROM topics" in s and "count(" not in s.lower() and "LIMIT" not in s.upper()
    ]
    assert unbounded == [], counter.summary()
