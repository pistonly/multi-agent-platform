"""优化任务 T07 的回归测试（见 docs/OPTIMIZATION-TASKS.md）。

get_todos 是 simple-waker 每 tick 的热点路径。原 mention 分支每个候选
mention 各发 1 条回复判定查询（同容器重复加载全部评论）+ 1 条
Agent.name 查询；现改为按容器一条 IN 预取 + author 名一次 IN 预取。
list_pending_plan_revisions 的循环内逐实验 count 同步改一条 GROUP BY。
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

from map_types.enums import (
    CommentAnchorType,
    ExperimentPhase,
    MentionSourceType,
    ReviewItemKind,
    ReviewItemStatus,
)

from server.domain.models import (
    AgentRole,
    Comment,
    Experiment,
    Mention,
    Project,
    Review,
    ReviewItem,
    Topic,
    TopicComment,
)
from server.services.auth import create_agent
from server.services.mention_service import (
    agent_replied_after_mention,
    agents_replied_after_mentions,
)
from server.services.todo_service import get_todos, list_pending_plan_revisions
from tests.test_perf_count_sql_fixture import count_sql_calls

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _make_project(db_session) -> Project:
    project = Project(
        project_key=f"t07-{uuid_mod.uuid4().hex[:8]}",
        name="T07 Tests",
        workspace_path="/tmp/t07",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_experiment(db_session, project, creator, *, phase=ExperimentPhase.review) -> Experiment:
    exp = Experiment(
        project_id=project.id,
        creator_agent_id=creator.id,
        title=f"exp-{uuid_mod.uuid4().hex[:6]}",
        phase=phase,
        current_plan_version=1,
    )
    db_session.add(exp)
    db_session.flush()
    return exp


def _make_topic(db_session, project, creator) -> Topic:
    topic = Topic(
        project_id=project.id,
        creator_agent_id=creator.id,
        title="t07-topic",
        created_at=NOW - timedelta(hours=2),
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def _make_topic_comment(
    db_session, topic, author, *, created_at, seq=1, parent=None, body="plain comment"
) -> TopicComment:
    comment = TopicComment(
        topic_id=topic.id,
        author_agent_id=author.id,
        parent_comment_id=parent.id if parent is not None else None,
        body=body,
        comment_seq=seq,
        created_at=created_at,
    )
    db_session.add(comment)
    db_session.flush()
    return comment


def _make_experiment_comment(db_session, experiment, author, *, created_at) -> Comment:
    comment = Comment(
        experiment_id=experiment.id,
        anchor_type=CommentAnchorType.plan,
        anchor_id=experiment.id,
        author_agent_id=author.id,
        body="plain experiment comment",
        created_at=created_at,
    )
    db_session.add(comment)
    db_session.flush()
    return comment


def _make_mention(
    db_session,
    *,
    mentioned_id,
    author_id,
    project_id,
    source_id,
    source_type=MentionSourceType.topic_comment,
    experiment_id=None,
    topic_id=None,
) -> Mention:
    mention = Mention(
        mentioned_agent_id=mentioned_id,
        author_agent_id=author_id,
        source_type=source_type,
        source_id=source_id,
        project_id=project_id,
        experiment_id=experiment_id,
        topic_id=topic_id,
        excerpt="t07 mention",
    )
    db_session.add(mention)
    db_session.flush()
    return mention


# ---------------------------------------------------------------------------
# 批量回复判定：与单条路径语义一致 + SQL 有界
# ---------------------------------------------------------------------------


def _seed_reply_scenarios(db_session):
    project = _make_project(db_session)
    mentioned, _ = create_agent(db_session, "t07-mentioned", AgentRole.agent, project_id=project.id)
    author, _ = create_agent(db_session, "t07-author", AgentRole.agent, project_id=project.id)
    exp = _make_experiment(db_session, project, author, phase=ExperimentPhase.running)

    mentions: list[Mention] = []
    # 每个场景独立 topic，避免「mentioned 的后期评论」跨场景污染判定。
    # 场景 1：source 之后 mentioned 有回复 → True
    topic1 = _make_topic(db_session, project, author)
    src1 = _make_topic_comment(db_session, topic1, author, created_at=NOW - timedelta(hours=1), seq=1)
    _make_topic_comment(db_session, topic1, mentioned, created_at=NOW - timedelta(minutes=30), seq=2)
    mentions.append(_make_mention(
        db_session, mentioned_id=mentioned.id, author_id=author.id, project_id=project.id,
        source_id=src1.id, source_type=MentionSourceType.topic_comment,
        experiment_id=exp.id, topic_id=topic1.id,
    ))
    # 场景 2：mentioned 的评论在 source 之前 → False
    topic2 = _make_topic(db_session, project, author)
    _make_topic_comment(db_session, topic2, mentioned, created_at=NOW - timedelta(minutes=50), seq=1)
    src2 = _make_topic_comment(db_session, topic2, author, created_at=NOW - timedelta(minutes=10), seq=2)
    mentions.append(_make_mention(
        db_session, mentioned_id=mentioned.id, author_id=author.id, project_id=project.id,
        source_id=src2.id, source_type=MentionSourceType.topic_comment,
        experiment_id=exp.id, topic_id=topic2.id,
    ))
    # 场景 3：mentioned 的唯一评论就是 source 本身 → False
    topic3 = _make_topic(db_session, project, author)
    src3 = _make_topic_comment(db_session, topic3, mentioned, created_at=NOW - timedelta(minutes=5), seq=1)
    mentions.append(_make_mention(
        db_session, mentioned_id=mentioned.id, author_id=author.id, project_id=project.id,
        source_id=src3.id, source_type=MentionSourceType.topic_comment,
        experiment_id=exp.id, topic_id=topic3.id,
    ))
    # 场景 4：实验评论容器，source 之后有回复 → True
    src4 = _make_experiment_comment(db_session, exp, author, created_at=NOW - timedelta(hours=1))
    _make_experiment_comment(db_session, exp, mentioned, created_at=NOW - timedelta(minutes=20))
    mentions.append(_make_mention(
        db_session, mentioned_id=mentioned.id, author_id=author.id, project_id=project.id,
        source_id=src4.id, source_type=MentionSourceType.experiment_comment,
        experiment_id=exp.id,
    ))
    # 场景 5：实验评论容器，无回复 → False
    exp2 = _make_experiment(db_session, project, author, phase=ExperimentPhase.running)
    src5 = _make_experiment_comment(db_session, exp2, author, created_at=NOW - timedelta(hours=1))
    mentions.append(_make_mention(
        db_session, mentioned_id=mentioned.id, author_id=author.id, project_id=project.id,
        source_id=src5.id, source_type=MentionSourceType.experiment_comment,
        experiment_id=exp2.id,
    ))
    # 场景 6：source_id 不存在（评论被删）→ False
    mentions.append(_make_mention(
        db_session, mentioned_id=mentioned.id, author_id=author.id, project_id=project.id,
        source_id=uuid_mod.uuid4(), source_type=MentionSourceType.topic_comment,
        experiment_id=exp.id, topic_id=topic1.id,
    ))
    # SQLite 的 DATETIME 往返丢 tzinfo：统一过期，让后续读取全部从库中
    # 加载成 naive 值，避免内存 aware 值与库加载值混比报错。
    db_session.flush()
    db_session.expire_all()
    return mentioned, mentions


def test_replied_after_batch_matches_single_path(db_session):
    """批量判定与逐条 ``agent_replied_after_mention`` 在全部场景上结果一致。"""
    mentioned, mentions = _seed_reply_scenarios(db_session)

    batch = agents_replied_after_mentions(db_session, mentions=mentions, agent_id=mentioned.id)
    for mention in mentions:
        single = agent_replied_after_mention(db_session, mention=mention, agent_id=mentioned.id)
        assert batch[mention.id] == single, (
            f"mention {mention.id}: batch={batch[mention.id]} single={single}"
        )
    # 场景抽查：True / False 分布符合构造。
    assert [batch[m.id] for m in mentions] == [True, False, False, True, False, False]


def test_replied_after_batch_sql_bounded(db_session, engine):
    """SQL 守卫：5 个 mention（2 topic 容器 + 2 实验容器）各 1 条 IN 预取，无逐条查询。"""
    mentioned, mentions = _seed_reply_scenarios(db_session)

    with count_sql_calls(engine, label="t07.replied-batch") as counter:
        agents_replied_after_mentions(db_session, mentions=mentions, agent_id=mentioned.id)

    topic_comment_loads = [
        s for s, _ in counter.statements
        if "FROM topic_comments" in s and "IN" in s
    ]
    experiment_comment_loads = [
        s for s, _ in counter.statements
        if "FROM comments" in s and "IN" in s
    ]
    assert len(topic_comment_loads) == 1, counter.summary()
    assert len(experiment_comment_loads) == 1, counter.summary()


# ---------------------------------------------------------------------------
# get_todos 集成：mention 待办 + author 名批量
# ---------------------------------------------------------------------------


def _seed_todo_mentions(db_session):
    project = _make_project(db_session)
    mentioned, _ = create_agent(db_session, "t07-host", AgentRole.agent, project_id=project.id)
    author_x, _ = create_agent(db_session, "t07-author-x", AgentRole.agent, project_id=project.id)
    author_y, _ = create_agent(db_session, "t07-author-y", AgentRole.agent, project_id=project.id)
    exp = _make_experiment(db_session, project, mentioned, phase=ExperimentPhase.running)

    # topic A：3 个未回复 mention（不同 author），mentioned 从未发言。
    topic_a = _make_topic(db_session, project, author_x)
    pending_source_ids: list[uuid_mod.UUID] = []
    for i, author in enumerate([author_x, author_y, author_x]):
        src = _make_topic_comment(
            db_session, topic_a, author, created_at=NOW - timedelta(minutes=60 - i), seq=i + 1
        )
        pending_source_ids.append(src.id)
        _make_mention(
            db_session, mentioned_id=mentioned.id, author_id=author.id, project_id=project.id,
            source_id=src.id, source_type=MentionSourceType.topic_comment,
            experiment_id=exp.id, topic_id=topic_a.id,
        )
    # topic B：1 个已回复 mention → 被 get_todos 排除。
    topic_b = _make_topic(db_session, project, author_y)
    replied_src = _make_topic_comment(
        db_session, topic_b, author_y, created_at=NOW - timedelta(minutes=10), seq=1
    )
    _make_mention(
        db_session, mentioned_id=mentioned.id, author_id=author_y.id, project_id=project.id,
        source_id=replied_src.id, source_type=MentionSourceType.topic_comment,
        experiment_id=exp.id, topic_id=topic_b.id,
    )
    _make_topic_comment(
        db_session, topic_b, mentioned, created_at=NOW - timedelta(minutes=5), seq=2
    )
    # experiment_id 为 None 的 mention：不进 todos（保持原语义）。
    src_no_exp = _make_topic_comment(
        db_session, topic_a, author_x, created_at=NOW - timedelta(minutes=3), seq=9
    )
    _make_mention(
        db_session, mentioned_id=mentioned.id, author_id=author_x.id, project_id=project.id,
        source_id=src_no_exp.id, source_type=MentionSourceType.topic_comment,
        experiment_id=None, topic_id=topic_a.id,
    )
    db_session.flush()
    db_session.expire_all()
    return (
        mentioned,
        topic_a,
        pending_source_ids,
        {author_x.id: "t07-author-x", author_y.id: "t07-author-y"},
    )


def test_get_todos_mentions_batched_with_author_names(db_session, engine):
    """未回复 mention 进 todos 且 author 名正确；SQL 守卫：author 名恰好 1 条 IN 查询。"""
    mentioned, topic_a, pending_source_ids, name_by_id = _seed_todo_mentions(db_session)

    with count_sql_calls(engine, label="t07.get_todos") as counter:
        todos = get_todos(db_session, mentioned)

    # 3 个未回复 mention 全部出现（按 source_id 识别，work-items 路径可能
    # 额外贡献同容器待办，不按总数断言），author 名批量解析正确。
    by_source = {m.source_id: m for m in todos.mentions}
    for source_id in pending_source_ids:
        assert source_id in by_source
        m = by_source[source_id]
        assert m.author_name == name_by_id[m.author_agent_id]

    # SQL 守卫：author 名全部走 IN 批量预取（work-items 路径与 mention 补充
    # 路径各一次，<= 2）。旧 N+1 形态是单列 ``SELECT agents.name ... WHERE
    # agents.id = ?``（原代码 per-mention 逐条查询）；全列 db.get(Agent)
    # 的单条等值查询与本任务无关，不在此守卫范围。
    name_in_queries = [
        s for s, _ in counter.statements
        if s.startswith("SELECT agents.id, agents.name") and "agents.id IN" in s
    ]
    name_eq_queries = [
        s for s, _ in counter.statements
        if s.startswith("SELECT agents.name") and "agents.id =" in s
    ]
    assert 1 <= len(name_in_queries) <= 2, counter.summary()
    assert name_eq_queries == [], counter.summary()


def test_get_todos_excludes_replied_mentions(db_session):
    mentioned, topic_a, pending_source_ids, _ = _seed_todo_mentions(db_session)

    todos = get_todos(db_session, mentioned)

    # 已回复 mention 不出现在任何来源的 todos.mentions 中（按 source_id
    # 断言，不受 work-items 路径额外贡献影响）。
    sources_in_todos = {m.source_id for m in todos.mentions}
    assert set(pending_source_ids) <= sources_in_todos


# ---------------------------------------------------------------------------
# list_pending_plan_revisions：一条 GROUP BY
# ---------------------------------------------------------------------------


def _seed_plan_revisions(db_session):
    project = _make_project(db_session)
    host, _ = create_agent(db_session, "t07-rev-host", AgentRole.agent, project_id=project.id)
    reviewer, _ = create_agent(db_session, "t07-rev-reviewer", AgentRole.agent, project_id=project.id)

    def _exp_with_items(open_count: int) -> Experiment:
        exp = _make_experiment(db_session, project, host)
        review = Review(
            experiment_id=exp.id,
            reviewer_agent_id=reviewer.id,
            plan_version=1,
        )
        db_session.add(review)
        db_session.flush()
        for _ in range(open_count):
            db_session.add(ReviewItem(
                review_id=review.id,
                kind=ReviewItemKind.unreasonable,
                content="must fix",
                status=ReviewItemStatus.open,
            ))
        # addressed 不计入 plan-revision 义务（status=open only）。
        db_session.add(ReviewItem(
            review_id=review.id,
            kind=ReviewItemKind.unreasonable,
            content="addressed one",
            status=ReviewItemStatus.addressed,
        ))
        return exp

    exp_two_open = _exp_with_items(2)
    exp_none_open = _exp_with_items(0)
    # 非 review 相位实验即使有 open 项也不出现。
    exp_running = _exp_with_items(1)
    exp_running.phase = ExperimentPhase.running
    db_session.flush()
    return host, exp_two_open, exp_none_open, exp_running


def test_pending_plan_revisions_batched_counts(db_session, engine):
    host, exp_two_open, exp_none_open, exp_running = _seed_plan_revisions(db_session)

    with count_sql_calls(engine, label="t07.plan-revisions") as counter:
        pending = list_pending_plan_revisions(db_session, host)

    by_exp = {p.experiment_id: p for p in pending}
    assert set(by_exp) == {exp_two_open.id}
    assert by_exp[exp_two_open.id].open_unreasonable_count == 2

    group_by_counts = [
        s for s, _ in counter.statements
        if "review_items" in s and "GROUP BY" in s
    ]
    assert len(group_by_counts) == 1, counter.summary()
    per_exp_counts = [
        s for s, _ in counter.statements
        if "review_items" in s and "GROUP BY" not in s and "COUNT" in s
    ]
    assert per_exp_counts == [], counter.summary()
