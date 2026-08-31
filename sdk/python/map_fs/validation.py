"""map_fs.validation — 话题轮次/关闭门禁的共享纯校验（server 与 CLI 复用）。

单一真值：advance-round / close 的前置条件与 fields 产出只在这里定义。

- server（``fs_source_service.validate_fs_advance_round`` /
  ``validate_fs_close``）委托本模块，并在外围保留 owner gate / projection
  revision CAS 等服务端专属检查；异常类以模块别名指回这里的类
  （同一类对象，``server/api/fs.py`` 的 isinstance 映射零改动）。
- CLI local plane（``plane: local``）直接调用本模块，零 server。

与 server 逐字对齐的契约：
- 异常消息字符串（``_validate_error_http`` 存在 409 字符串路径）；
- ack 名单 = ``FsTopic.ack_participants()``（creator ∪ declared，不含
  动态 speaker），missing 顺序与 server 的 ``view.ack_participants`` 迭代序一致；
- ``waive_reason`` 仅在 ``waive_ack and waive_reason`` 时进 fields（不新增
  强制校验，保持 remote 行为预期不变）。
"""

from __future__ import annotations

from pathlib import Path

from map_fs.parser import FsActionItem, FsTopic


class AckPendingError(Exception):
    """本轮还有参与者未发言（ack 未满）。

    ``missing`` 保持 persona 列表（向后兼容）；``missing_reasons`` 为
    persona → ``round1-participant.md: 原因`` 的逐条指认（A5）。
    """

    def __init__(
        self, missing: list[str], missing_reasons: dict[str, str] | None = None
    ) -> None:
        super().__init__(f"round ack pending: {', '.join(missing)}")
        self.missing = missing
        self.missing_reasons = missing_reasons or {}


class OpenActionItemsError(Exception):
    """action-items.yaml 存在 open 项或格式错漏，close 被拦（D2 唯一防线）。

    ``items`` 携带未清零条目（id/title/owner）供 409 逐条展示；格式错漏
    场景 items 为空、``detail`` 携解析错误文案（A1 不静默）。
    """

    def __init__(
        self, items: list[FsActionItem] | None = None, *, detail: str = ""
    ) -> None:
        self.items = items or []
        self.detail = detail
        if self.items:
            joined = ", ".join(f"#{i.id} {i.title}" for i in self.items)
            super().__init__(f"action items open: {joined}")
        else:
            super().__init__(f"action items unparseable: {detail}")


class TopicStateError(Exception):
    """话题当前状态不允许该操作（如已关闭再推进）。"""


class TopicOwnerError(Exception):
    """local plane owner gate：只有 topic creator 可执行验证型写。"""


class OpenExperimentError(Exception):
    """close 门禁：关联实验存在 non-terminal phase（D6 实验悬挂防线）。

    ``experiments`` 携带非 terminal 的实验列表（id/title/phase/dir_path），
    供 409 逐条展示。空列表 = 全部 terminal 或无关联实验 = 放行。
    """

    def __init__(self, experiments: list) -> None:
        self.experiments = list(experiments)
        joined = ", ".join(
            f"{e.id} (phase={e.phase})" for e in self.experiments
        )
        super().__init__(f"experiment non-terminal: {joined}")


# 实验 terminal phase 集合（实验 cli-fs-topic-lifecycle-invariants A4）：
# 已落地只有 done / cancelled；FS index.md 合法 phase 见 parser.EXPERIMENT_PHASES。
_EXPERIMENT_TERMINAL_PHASES: frozenset[str] = frozenset({"done", "cancelled"})


# close_reason 合法枚举（T7 I7，与 cli/verify_audit/scanner.py CLOSE_REASON_LEGAL
# 单一真值）：sdk/python/map_fs/validation.py 定义、scanner 引用，避免双源漂移
# （验证型写拒绝 + 审计层检测双层防线，详见 cli/verify_audit/scanner.py:32-39）。
# 4 值含义：
#   experiment_ready       — 收敛后开实验，等实验就绪
#   experiment_done        — 收敛后开实验，实验已 done/cancelled
#   cancelled              — 话题主动取消（不开实验）
#   discussion_converged   — 讨论收敛但不开实验（仅沉淀决策 / 不挂实验链路）
CLOSE_REASON_LEGAL: frozenset[str] = frozenset({
    "experiment_ready",
    "experiment_done",
    "cancelled",
    "discussion_converged",
})


class InvalidCloseReasonError(Exception):
    """close_reason 不在合法枚举 CLOSE_REASON_LEGAL 内（T7 I7 第 4 维校验）。

    ``reason`` 携带非法值；``legal`` 携带合法集合（sorted 字符串列表，便于
    错误消息直接展示）。非法值入参 = 验证型写直接拒绝（写入未发生），
    已存在的 close 字段值不在此范围 → 由 verify_audit D004 检测。
    """

    def __init__(self, reason: str, legal: list[str]) -> None:
        self.reason = reason
        self.legal = list(legal)
        super().__init__(
            f"close_reason='{reason}' 不在合法枚举 {legal} 内; "
            f"请使用 4 值之一: experiment_ready / experiment_done / "
            f"cancelled / discussion_converged"
        )


def _missing_reasons(topic: FsTopic, missing: list[str]) -> dict[str, str]:
    """missing persona 的逐条指认：``round1-participant.md: 原因``（A5）。

    只对"有文件但不合规"的 persona 产原因；纯缺文件的 persona 不进 reasons
    （missing 列表本身已指认其名）。
    """
    reasons: dict[str, str] = {}
    for persona in missing:
        for c in topic.comments:
            if c.round == topic.round_number and c.file_persona == persona and c.ack_error:
                reasons[persona] = f"{Path(c.file_path).name}: {c.ack_error}"
                break
    return reasons


def validate_advance_round(
    topic: FsTopic,
    *,
    actor: str | None = None,
    waive_ack: bool = False,
    waive_reason: str | None = None,
    mark_ready: bool = False,
) -> dict[str, str]:
    """校验推进轮次的前置条件，返回应写回 index.md 的 fields（不写文件）。

    ``actor`` 非 None 时执行 owner gate（creator 才可推进；server 路径的
    owner 检查在服务端外围完成，传 None）。
    """
    if actor is not None and actor != topic.creator:
        raise TopicOwnerError(
            f"only the topic creator '{topic.creator}' can advance-round; "
            f"current persona is '{actor}'"
        )
    if topic.status != "open":
        raise TopicStateError(
            f"fs topic '{topic.slug}' is {topic.status}; only open topics advance"
        )

    if not waive_ack:
        authors = topic.authors_in_round(topic.round_number)
        missing = [
            p for p in topic.ack_participants() if p != topic.creator and p not in authors
        ]
        if missing:
            raise AckPendingError(missing, _missing_reasons(topic, missing))

    fields: dict[str, str] = {
        "round": "ready" if mark_ready else f"round{topic.round_number + 1}"
    }
    if waive_ack and waive_reason:
        fields["waive_reason"] = waive_reason
    return fields


def validate_close(
    topic: FsTopic,
    *,
    actor: str | None = None,
    close_reason: str | None = None,
    close_note: str | None = None,
) -> dict[str, str]:
    """校验关闭话题的前置条件，返回应写回的 fields（不写文件）。

    ``actor`` 语义同 :func:`validate_advance_round`。

    关闭门禁（顺序固定，全部满足才放行）：
    1. owner：actor 非 None 时必须 = topic.creator（local plane 校验）
    2. status：当前话题必须 open（closed → 409 TopicStateError）
    3. action-items.yaml：D2 唯一防线——存在 open 项或格式错漏 → 拦
    4. experiments（D6 实验悬挂防线，实验 cli-fs-topic-lifecycle-invariants A4）：
       关联实验非空 + 任一 phase ∉ {done, cancelled} → 抛 OpenExperimentError；
       空 / 缺失 → 放行；不提供 --force 绕过
    """
    if actor is not None and actor != topic.creator:
        raise TopicOwnerError(
            f"only the topic creator '{topic.creator}' can close; "
            f"current persona is '{actor}'"
        )
    if topic.status == "closed":
        raise TopicStateError(f"fs topic '{topic.slug}' is already closed")

    # D2 唯一防线（plan v3）:closed = 零尾款。action-items.yaml 存在 open 项
    # （或格式错漏无法判定）→ 拦 close;全 done/cancelled 或无 yaml 话题放行。
    if topic.action_items_error is not None:
        raise OpenActionItemsError(detail=topic.action_items_error)
    open_items = [item for item in topic.action_items if item.status == "open"]
    if open_items:
        raise OpenActionItemsError(open_items)

    # D6 实验 terminal 校验：避免「话题已关 + 实验悬挂」状态机不一致
    non_terminal = [
        exp for exp in topic.experiments
        if exp.phase not in _EXPERIMENT_TERMINAL_PHASES
    ]
    if non_terminal:
        raise OpenExperimentError(non_terminal)

    # 第 4 维（T7 I7）：close_reason 枚举校验。close_reason 显式传入且不在
    # CLOSE_REASON_LEGAL 内 → 拒绝写入；None（未指定）放行以兼容历史 close。
    # 与 cli/verify_audit/scanner.py CLOSE_REASON_LEGAL 单一真值对齐。
    if close_reason is not None and close_reason not in CLOSE_REASON_LEGAL:
        raise InvalidCloseReasonError(
            close_reason, sorted(CLOSE_REASON_LEGAL)
        )

    fields: dict[str, str] = {"status": "closed"}
    if close_reason:
        fields["close_reason"] = close_reason
    if close_note:
        fields["close_note"] = close_note
    return fields
