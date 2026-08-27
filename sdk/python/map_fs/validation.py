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

    fields: dict[str, str] = {"status": "closed"}
    if close_reason:
        fields["close_reason"] = close_reason
    if close_note:
        fields["close_note"] = close_note
    return fields
