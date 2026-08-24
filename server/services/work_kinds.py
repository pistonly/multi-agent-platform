"""Work item kind 分发 registry（实验 d559f431 / work-kind-dispatch-single-source）。

kind → 清理动作 + 归属 Skill + 说明 的 server 侧单一真相源：

- CLI ``map work kinds`` 序列化输出本表（方向 A 过渡，A2）
- wake.md 分发表由本表渲染/校验（CI 标记块内整行 diff，A3/D8）
- 新增 kind 必须同时改本 registry + 渲染 + 测试（A5 checklist 强制项）

字段口径（A1/A6/D8）：``clear_action`` 给动作类别与命令形态；``skill`` 为归属
persona Skill（必需，无它则 agent 知道动作却不知读哪个 Skill，仍然断链）；
``note`` 承载人类注释与主观判断标准（可空，不假装可枚举）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkItemKindSpec:
    """单个 work item kind 的分发规格（四字段，D8）。"""

    kind: str
    clear_action: str
    skill: str
    note: str = ""


WORK_ITEM_KINDS: tuple[WorkItemKindSpec, ...] = (
    WorkItemKindSpec(
        kind="mentions",
        clear_action="map mention dismiss --id <uuid>",
        skill="map-project-collab",
        note="mention 功能保留；实验评论仍产生，话题域来源已随 DB 写路径退役枯竭",
    ),
    WorkItemKindSpec(
        kind="pending_topic_replies",
        clear_action=(
            "写本轮发言文件：map topic comment --id <slug> --file <md>"
            "（即写 map/topics/<slug>/round<N>-<persona>.md，文件存在即消失）；"
            "存量 DB 话题只读，需 host 先 topic migrate"
        ),
        skill="topic-host",
        note="FS 话题 reason=fs_file_missing；participant 视角见 topic-participant",
    ),
    WorkItemKindSpec(
        kind="round_ack",
        clear_action=(
            "参与者交齐文件后 map topic advance-round --id <slug>"
            "（服务端校验写回 index.md；等价 fs advance-round --topic <slug>）"
        ),
        skill="topic-host",
        note="仅 host；FS 话题",
    ),
    WorkItemKindSpec(
        kind="pending_advance_rounds",
        clear_action=(
            "读路径保留；推进已退役——host 先 topic migrate --id <uuid>"
            "迁 FS 后用 topic advance-round --id <slug>"
        ),
        skill="topic-host",
        note="存量 DB 话题",
    ),
    WorkItemKindSpec(
        kind="pending_round_acks",
        clear_action=(
            "FS 话题=写本轮自己的发言文件（发言文件即表态）；"
            "存量 DB 话题=只读（迁移后表态），DB --ack 命令已退役"
        ),
        skill="topic-participant",
        note="reviewer 视角见 experiment-reviewer",
    ),
    WorkItemKindSpec(
        kind="pending_reviews",
        clear_action="完成评审（experiment review add）",
        skill="experiment-reviewer",
        note="",
    ),
    WorkItemKindSpec(
        kind="pending_result_reviews",
        clear_action="accept-result / reject-result",
        skill="experiment-reviewer",
        note="",
    ),
    WorkItemKindSpec(
        kind="pending_replies",
        clear_action="回复",
        skill="experiment-reviewer",
        note="",
    ),
    WorkItemKindSpec(
        kind="my_open_experiments",
        clear_action="实验 phase 推进（complete 等）",
        skill="experiment-host",
        note="",
    ),
    WorkItemKindSpec(
        kind="stale_open_topics",
        clear_action=(
            "复盘推进；FS 话题（仅 creator/host 可见）久未推进→ "
            "map topic close --id <slug> --note 落结论即清理"
            "（dismiss 对 FS 是 no-op）；存量 DB 话题纯等待他人则 "
            "map topic dismiss --id <uuid>"
        ),
        skill="topic-host",
        note="",
    ),
    WorkItemKindSpec(
        kind="my_open_topics",
        clear_action="推进话题或 map topic dismiss --id <uuid>（与 UI ✕ 相同）",
        skill="topic-host",
        note="且无动作时",
    ),
    WorkItemKindSpec(
        kind="action_items",
        clear_action=(
            "完成: map topic action-item complete --topic <slug> --id <n>"
            " --evidence \"<commit/pytest/路径>\"；"
            "放弃: map topic action-item cancel --topic <slug> --id <n> --reason \"...\"。"
            "清零后话题才可 close（closed = 零尾款）"
        ),
        skill="topic-host",
        note="FS 话题，kind 同构于 stale nudge，来源 action-items.yaml；participant 亦可为 owner",
    ),
)


def get_kind_spec(kind: str) -> WorkItemKindSpec | None:
    """按 kind 名取分发规格；未登记返回 None（调用方应视为 drift 信号）。"""
    for spec in WORK_ITEM_KINDS:
        if spec.kind == kind:
            return spec
    return None
