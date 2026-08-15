"""A2A 互操作映射常量（M53，实验性）。

单一事实源：本模块定义 (1) persona → Agent Card 内容注册表、
(2) MAP 对象 → A2A Task 状态映射。``docs/A2A-MAPPING.md`` 与
``GET /agents/{id}/tasks`` 投影端点都必须从这里取值——
``tests/test_a2a.py`` 的护栏测试会校验文档与常量不漂移。

协议基线：A2A Agent Card / Task 语义按 2025-03 草案（JSON
结构可能演进），因此卡片只输出稳定核心字段，不做兼容承诺。
"""

from __future__ import annotations

from map_types.enums import ExperimentPhase, TopicStatus

A2A_PROTOCOL_BASELINE = "A2A draft 2025-03 (experimental projection)"

# ---------------------------------------------------------------------------
# Agent Card 注册表（persona → 卡片内容）
# ---------------------------------------------------------------------------

CARD_SKILLS_HOST = [{"id": "topic-host", "name": "MAP Topic Host"}]
CARD_SKILLS_PARTICIPANT = [{"id": "topic-participant", "name": "MAP Topic Participant"}]
CARD_SKILLS_REVIEWER = [{"id": "experiment-reviewer", "name": "MAP Experiment Reviewer"}]
_COMMON_SKILL = {"id": "map-project-collab", "name": "MAP Project Collaboration"}

PERSONA_CARDS: dict[str, dict] = {
    "host": {
        "description": "主持话题讨论、创建并推进实验生命周期",
        "skills": CARD_SKILLS_HOST + [_COMMON_SKILL],
        "capabilities": [
            "topic.hosting",
            "experiment.create",
            "experiment.gatekeeping",
        ],
    },
    "participant": {
        "description": "参与话题评论与讨论、受委派执行实验",
        "skills": CARD_SKILLS_PARTICIPANT + [_COMMON_SKILL],
        "capabilities": [
            "topic.comment",
            "experiment.execute",
        ],
    },
    "reviewer": {
        "description": "评审实验计划与结果",
        "skills": CARD_SKILLS_REVIEWER + [_COMMON_SKILL],
        "capabilities": [
            "experiment.review",
        ],
    },
}


# ---------------------------------------------------------------------------
# Task 状态映射（MAP 对象 → A2A Task state）
# ---------------------------------------------------------------------------

# experiment phase → A2A task state（PRD v0.11 §7.3 映射表）
EXPERIMENT_TASK_STATE: dict[ExperimentPhase, str] = {
    ExperimentPhase.draft: "submitted",
    ExperimentPhase.review: "working",
    ExperimentPhase.approved: "working",
    ExperimentPhase.running: "working",
    ExperimentPhase.result_review: "working",
    ExperimentPhase.done: "completed",
    ExperimentPhase.cancelled: "failed",
}

# topic 状态 → A2A task state（open 即轮次进行中 = working；closed = completed）
TOPIC_TASK_STATE: dict[TopicStatus, str] = {
    TopicStatus.open: "working",
    TopicStatus.closed: "completed",
}

# A2A Task 投影允许出现的状态集合（护栏测试用）
A2A_TASK_STATES = frozenset(
    {v for v in EXPERIMENT_TASK_STATE.values()} | set(TOPIC_TASK_STATE.values())
)
