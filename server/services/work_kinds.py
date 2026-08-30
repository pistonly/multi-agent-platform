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
    """单个 work item kind 的分发规格（六字段，D8 + 实验 8b1d20a1 I1）。

    实验 8b1d20a1 引入 ``required_role`` 与 ``obligation_whitelist_exempt``：

    - ``required_role``: 该 kind 的可清理角色集合（``host`` / ``participant`` /
      ``reviewer`` / ``all``）。filter 路径对 contextual kind 按此角色 + 话题白名单
      （creator ∪ declared ∪ speakers_current_round ∪ speakers_prev_round）做收件人
      判定；``all`` 表示任何角色都收，不走白名单。
    - ``obligation_whitelist_exempt``: 仅对 obligation kind 生效——为 True 时，
      filter 强制豁免白名单过滤，全角色 fan-out。reviewer round2 硬边界：
      pending_reviews / pending_result_reviews / pending_replies 必须豁免，
      保证 reviewer 在未参与任何 topic 的情况下也能收 experiment.phase_changed
      （phase ∈ {review, result_review}）/ experiment.lifecycle.cancelled /
      experiment.lifecycle.withdrawn 等 obligation-wakeable。
    """

    kind: str
    clear_action: str
    skill: str
    note: str = ""
    required_role: str = "all"
    obligation_whitelist_exempt: bool = False


WORK_ITEM_KINDS: tuple[WorkItemKindSpec, ...] = (
    WorkItemKindSpec(
        kind="mentions",
        clear_action="map mention dismiss --id <uuid>",
        skill="map-project-collab",
        note="mention 功能保留；实验评论仍产生，话题域来源已随 DB 写路径退役枯竭",
        required_role="all",
        obligation_whitelist_exempt=False,
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
        required_role="participant",
        obligation_whitelist_exempt=False,
    ),
    WorkItemKindSpec(
        kind="round_ack",
        clear_action=(
            "参与者交齐文件后 map topic advance-round --id <slug>"
            "（服务端校验写回 index.md；等价 topic advance-round --topic <slug>）"
        ),
        skill="topic-host",
        note="仅 host；FS 话题",
        required_role="host",
        obligation_whitelist_exempt=False,
    ),
    WorkItemKindSpec(
        kind="pending_advance_rounds",
        clear_action=(
            "读路径保留；推进已退役——host 先 topic migrate --id <uuid>"
            "迁 FS 后用 topic advance-round --id <slug>"
        ),
        skill="topic-host",
        note="存量 DB 话题",
        required_role="host",
        obligation_whitelist_exempt=False,
    ),
    WorkItemKindSpec(
        kind="pending_round_acks",
        clear_action=(
            "FS 话题=写本轮自己的发言文件（发言文件即表态）；"
            "存量 DB 话题=只读（迁移后表态），DB --ack 命令已退役"
        ),
        skill="topic-participant",
        note="reviewer 视角见 experiment-reviewer",
        required_role="participant",
        obligation_whitelist_exempt=False,
    ),
    WorkItemKindSpec(
        kind="pending_reviews",
        clear_action="完成评审（experiment review add）",
        skill="experiment-reviewer",
        note="",
        required_role="reviewer",
        obligation_whitelist_exempt=True,
    ),
    WorkItemKindSpec(
        kind="pending_result_reviews",
        clear_action="accept-result / reject-result",
        skill="experiment-reviewer",
        note="",
        required_role="reviewer",
        obligation_whitelist_exempt=True,
    ),
    WorkItemKindSpec(
        kind="pending_replies",
        clear_action="回复",
        skill="experiment-reviewer",
        note="",
        required_role="reviewer",
        obligation_whitelist_exempt=True,
    ),
    WorkItemKindSpec(
        kind="my_open_experiments",
        clear_action="实验 phase 推进（complete 等）",
        skill="experiment-host",
        note="",
        required_role="host",
        obligation_whitelist_exempt=False,
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
        required_role="host",
        obligation_whitelist_exempt=False,
    ),
    WorkItemKindSpec(
        kind="my_open_topics",
        clear_action="推进话题或 map topic dismiss --id <uuid>（与 UI ✕ 相同）",
        skill="topic-host",
        note="且无动作时",
        required_role="host",
        obligation_whitelist_exempt=False,
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
        required_role="host",
        obligation_whitelist_exempt=False,
    ),
    WorkItemKindSpec(
        kind="unread_change",
        clear_action=(
            "读最新发言并接棒：map topic comments --id <slug> 查看后，"
            "写本轮发言文件 map topic comment --id <slug> --file <md>（写完自己成为最新发言者即消失）"
        ),
        skill="map-project-collab",
        note=(
            "contextual 交接信号：对方是最新发言者时出现，我自己发言后消失；"
            "仅白名单（creator ∪ declared ∪ speakers）可见，reviewer 等旁观者不可见；"
            "simple-waker 签名唤醒的交接信号源，不再依赖 stale_open_topics 心跳"
        ),
        required_role="all",
        obligation_whitelist_exempt=False,
    ),
)


def get_kind_spec(kind: str) -> WorkItemKindSpec | None:
    """按 kind 名取分发规格；未登记返回 None（调用方应视为 drift 信号）。"""
    for spec in WORK_ITEM_KINDS:
        if spec.kind == kind:
            return spec
    return None


def resolve_kind(name: str) -> str:
    """Canonical kind string with a fail-fast registry guard.

    实验 d559f431 R1: 产 kind 的 service 一律经此取复数码——名字未在
    ``WORK_ITEM_KINDS`` 登记即抛 KeyError（service 产未登记/改名 kind 立即
    红，配合 tests/test_work_kind_registry_consistency.py 静态收集测试构成
    service→registry 方向机器防线；A3 的 registry↔wake.md 整行 diff 只防
    registry→渲染方向）。
    """
    if get_kind_spec(name) is None:
        raise KeyError(
            f"work item kind {name!r} not registered in WORK_ITEM_KINDS; "
            "register it in server/services/work_kinds.py (kind/clear_action/"
            "skill/note/required_role/obligation_whitelist_exempt 六字段) "
            "and sync the wake.md kind-dispatch block (checklist 强制项)"
        )
    return name


def render_kinds_md() -> str:
    """渲染 wake.md 分发表标记块内容（A3/D8 唯一渲染形态，实验 8b1d20a1 I1 扩列）。

    消费方：CLI ``map work --kinds --kinds-format md`` 输出、tests 对
    wake.md ``BEGIN/END:kind-dispatch`` 标记块的整行 diff——同一函数保证
    三处逐字符一致。

    实验 8b1d20a1 新增两列：
    - ``required_role``: 可清理角色（host/participant/reviewer/all）
    - ``obligation_whitelist_exempt``: 是否豁免白名单过滤（仅 obligation kind 生效）
    """
    lines = [
        "| kind | 清理动作 | 下一步 Skill | 说明 | required_role | obligation_whitelist_exempt |",
        "|------|----------|--------------|------|---------------|----------------------------|",
    ]
    for spec in WORK_ITEM_KINDS:
        action = spec.clear_action.replace("|", "\\|")
        note = spec.note.replace("|", "\\|")
        lines.append(
            f"| `{spec.kind}` | {action} | {spec.skill} | {note} "
            f"| {spec.required_role} | {spec.obligation_whitelist_exempt} |"
        )
    return "\n".join(lines)
