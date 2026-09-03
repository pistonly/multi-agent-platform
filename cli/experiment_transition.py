"""实验生命周期两跳协议的 CLI 本地平面（实验B 24f3e565，B3~B6/B8）。

validate 成功后、commit 之前，CLI 把 token 与七元组落到
``<workspace>/.map/intents/<eid8>-<to_phase>.json``（本地 intent）——
进程在 commit 前崩溃时，``map experiment recover --id`` 依 intent +
server receipt 做三分支对账（继续 / 回滚 / 报冲突），不再靠猜。

约定：

- intent 是 CLI 自己的恢复凭证，不是 MAP 状态——server 唯一事实源仍是
  receipt；intent 永远不会让本地「看起来已提交」。
- 删除时机：commit 成功（含重放命中，同 receipt）后立即删除；GC 见
  ``gc_intents``（B6：只清「实验已终结」或「token 过期且 server 确认
  未提交」，自动 GC 不做——recover 默认只读，``--gc`` 显式删）。
- 写指纹 fail closed（B8）：CLI 显式携带
  ``ProjectContext.workspace_fingerprint()``；本地解析不出 workspace 时
  降级为不携带并 stderr warn（server 侧按 server-authoritative 门禁）。
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError, MAPNotFoundError
from map_types.schemas import (
    ExperimentComplete,
    ExperimentDetailRead,
    ExperimentResultDecision,
    ExperimentStart,
    ExperimentTransitionCommitResponse,
    ExperimentTransitionVerdict,
)

INTENTS_DIR_NAME = ".map/intents"
_INTENT_VERSION = 1

# 实验 B 的明确 GC 定案（B6）：recover --gc 只清这两类 intent。
_TERMINAL_PHASES = frozenset({"done", "cancelled"})


class ExperimentIntent:
    """一条本地 intent（intent 文件 JSON 的内存形态）。"""

    def __init__(self, data: dict[str, Any], path: Path) -> None:
        self.data = data
        self.path = path

    @property
    def token(self) -> str:
        return str(self.data["token"])

    @property
    def nonce(self) -> str:
        return str(self.data["nonce"])

    @property
    def action(self) -> str:
        return str(self.data["action"])

    @property
    def experiment_id(self) -> uuid.UUID:
        return uuid.UUID(str(self.data["experiment_id"]))

    @property
    def from_phase(self) -> str:
        return str(self.data["from_phase"])

    @property
    def to_phase(self) -> str:
        return str(self.data["to_phase"])

    @property
    def expires_at(self) -> datetime | None:
        raw = self.data.get("expires_at")
        if not raw:
            return None
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))

    def expired(self, *, now: datetime | None = None) -> bool:
        expires_at = self.expires_at
        if expires_at is None:
            return False
        return (now or datetime.now(timezone.utc)) >= expires_at


def intents_dir(workspace_root: Path) -> Path:
    return workspace_root / INTENTS_DIR_NAME


def intent_path(workspace_root: Path, experiment_id: uuid.UUID, to_phase: str) -> Path:
    return intents_dir(workspace_root) / f"{str(experiment_id)[:8]}-{to_phase}.json"


def save_intent(
    workspace_root: Path,
    verdict: ExperimentTransitionVerdict,
    *,
    before: ExperimentDetailRead,
    action: str,
    fingerprint: str | None = None,
) -> Path:
    """validate 成功后落本地 intent（崩溃恢复凭证）。

    ``local_snapshot`` 是提交前本地已知快照摘要（phase / plan 版本 / 标题
    / 指纹）——recover 报告用它对照「本地提交前视角 vs server 事实」。
    """
    directory = intents_dir(workspace_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = intent_path(workspace_root, verdict.experiment_id, verdict.to_phase)
    payload = {
        "version": _INTENT_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "action": action,
        # 七元组 + token（B1 绑定，B3 落地）
        "token": verdict.token,
        "nonce": verdict.nonce,
        "experiment_id": str(verdict.experiment_id),
        "project_id": str(verdict.project_id),
        "from_phase": verdict.from_phase,
        "to_phase": verdict.to_phase,
        "base_revision": verdict.base_revision,
        "workspace_fingerprint": fingerprint,
        "expires_at": verdict.expires_at.isoformat(),
        # 本地快照摘要（提交前视角）
        "local_snapshot": {
            "phase": _phase_value(before),
            "current_plan_version": before.current_plan_version,
            "title": before.title,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_intents(workspace_root: Path, experiment_id: uuid.UUID | None = None) -> list[ExperimentIntent]:
    """读取本地 intent（可按实验过滤）；损坏文件跳过并 stderr 提示。"""
    directory = intents_dir(workspace_root)
    if not directory.is_dir():
        return []
    found: list[ExperimentIntent] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            typer.echo(f"[WARN] intent 文件损坏，跳过 {path.name}: {exc}", err=True)
            continue
        intent = ExperimentIntent(data, path)
        if experiment_id is not None and intent.experiment_id != experiment_id:
            continue
        found.append(intent)
    return found


def delete_intent(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _phase_value(obj: Any) -> str:
    value = getattr(obj, "phase", obj)
    return getattr(value, "value", value)


def _workspace_fingerprint_for_write() -> str | None:
    """CLI 写路径指纹（B8）：解析不出本地 workspace 时降级 + warn。

    正常 dogfood 场景（workspace 内有 ``.map/``）一定解析得出；None 只
    出现在 projection/remote 边缘场景，此时写门禁按 server 自算视角执行
    （与单体包装路径同安全级别），stderr 显式提示不静默。
    """
    from cli.project_context import optional_context

    context = optional_context()
    if context is None:
        return None
    try:
        return context.workspace_fingerprint()
    except OSError as exc:
        typer.echo(
            f"[WARN] 本地 workspace 指纹不可 stat（{exc}）；本次 lifecycle 写"
            "不带指纹，门禁按 server 视角执行。",
            err=True,
        )
        return None


def two_hop_transition(
    client: MAPClient,
    experiment_id: uuid.UUID,
    before: ExperimentDetailRead,
    *,
    action: str,
    start: ExperimentStart | None = None,
    complete: ExperimentComplete | None = None,
    decision: ExperimentResultDecision | None = None,
    workspace_root: Path | None = None,
) -> ExperimentTransitionCommitResponse:
    """validate → intent → commit → 删 intent 的 CLI 两跳路径。

    失败时 intent 留在本地并给出 ``map experiment recover`` 指引（B3）；
    CAS 落败（409）在 intent 留存的同时原样透出 server 的败方证据
    （当前 phase + 胜出 receipt，B4/B5）——本地不落地任何目标状态。
    """
    root = workspace_root
    if root is None:
        from cli.project_context import optional_context

        context = optional_context()
        root = context.workspace_root if context is not None else None

    fingerprint = _workspace_fingerprint_for_write() if root is not None else None
    verdict = client.transition_validate(
        experiment_id,
        action,
        workspace_fingerprint=fingerprint,
    )

    intent_file: Path | None = None
    if root is not None:
        try:
            intent_file = save_intent(
                root, verdict, before=before, action=action, fingerprint=fingerprint
            )
        except OSError as exc:
            # intent 落盘失败不阻断提交（server 侧 receipt 仍是事实源），
            # 但要显式提示崩溃恢复窗口已关闭。
            typer.echo(
                f"[WARN] 本地 intent 落盘失败（{exc}）；本次提交在 commit 前"
                "崩溃时将无法本地恢复，仅能以 server receipt 对账。",
                err=True,
            )

    try:
        response = client.transition_commit(
            experiment_id,
            verdict.token,
            start=start,
            complete=complete,
            decision=decision,
        )
    except MAPHTTPError as exc:
        _report_commit_failure(exc, intent_file)
        raise
    # 成功（含重放命中同 receipt）：intent 的使命完成，立即删除。
    if intent_file is not None:
        delete_intent(intent_file)
    return response


def _report_commit_failure(exc: MAPHTTPError, intent_file: Path | None) -> None:
    """commit 失败的统一 stderr 报告 + recover 指引（intent 留存）。"""
    experiment_hint = ""
    if intent_file is not None:
        experiment_hint = (
            f"\n  本地 intent 已保留: {intent_file}"
            "\n  恢复: map experiment recover --id <experiment-id>"
        )
    typer.echo(
        f"Error: transition commit 被拒绝: {exc}\n"
        "  未落地任何本地状态；server phase 以 `map experiment show` 为准。"
        + experiment_hint,
        err=True,
    )


# ---------------------------------------------------------------------------
# recover（B3 三分支 + B4 冲突报告 + B6 显式 GC）
# ---------------------------------------------------------------------------


def recover_intents(
    client: MAPClient,
    workspace_root: Path,
    experiment_id: uuid.UUID,
    *,
    gc: bool = False,
) -> dict[str, Any]:
    """``map experiment recover --id`` 的执行体：对账并返回结构化结果。

    三分支（B3，每条 intent 独立裁决）：

    - **committed**：nonce 在 server 有 receipt → server 已落地。若本地
      FS 投影 phase 落后，则按 receipt 修正本地投影（继续分支）。
    - **not_committed 且 token 未过期**：什么都没发生 → 提示可重新
      validate 提交（回滚分支的温和形态：默认不动 server、只报告）。
    - **conflict**：nonce 无 receipt 但 server phase ≠ intent.from_phase，
      或 CAS 败因 receipt 存在 → 输出双方证据（B4）。

    ``--gc``（B6）：三分支之外，只清「实验已终结」或「token 过期且
    server 确认未提交」的 intent 文件。
    """
    intents = load_intents(workspace_root, experiment_id)
    detail = client.get_experiment(experiment_id)
    server_phase = _phase_value(detail)
    terminal = server_phase in _TERMINAL_PHASES

    results: list[dict[str, Any]] = []
    removed: list[str] = []
    for intent in intents:
        try:
            receipt = client.transition_receipt(experiment_id, intent.nonce)
        except MAPNotFoundError:
            receipt = None

        if receipt is not None:
            branch = "committed"
            note = (
                f"server 已提交该 transition（{receipt.action}: "
                f"{receipt.from_phase} → {receipt.to_phase}，"
                f"committed_at={receipt.committed_at.isoformat()}）；"
                "本地以 server 为准"
            )
        elif terminal or intent.expired():
            branch = "stale"
            reason = (
                f"实验已终结（server phase={server_phase}）"
                if terminal
                else f"token 已过期且 server 确认未提交（phase={server_phase}）"
            )
            note = f"{reason}；该 intent 不可再提交"
        elif server_phase != intent.from_phase:
            branch = "conflict"
            winner_lines = [
                f"    - action={r.action} actor={r.actor_id} at {r.committed_at.isoformat()} "
                f"digest={r.token_digest[:12]}…"
                for r in client.transition_receipts(experiment_id, limit=5)
            ]
            note = (
                "server 状态已被其它 transition 推进：\n"
                f"    本地 intent : {intent.action} {intent.from_phase} → "
                f"{intent.to_phase} (base_revision=r{intent.data.get('base_revision')})\n"
                f"    server 事实 : phase={server_phase}\n"
                f"    最近 receipt:\n" + ("\n".join(winner_lines) if winner_lines else "    -（无）")
            )
        else:
            branch = "pending"
            note = (
                "token 未过期且 server 未提交、phase 未变——可重新执行原命令"
                "（会重新 validate 换新 token）或 `recover --gc` 清掉本 intent"
            )

        results.append(
            {
                "file": str(intent.path),
                "action": intent.action,
                "from_phase": intent.from_phase,
                "to_phase": intent.to_phase,
                "nonce": intent.nonce,
                "expired": intent.expired(),
                "branch": branch,
                "note": note,
            }
        )
        if gc and branch in ("committed", "stale"):
            delete_intent(intent.path)
            removed.append(str(intent.path))

    return {
        "experiment_id": str(experiment_id),
        "server_phase": server_phase,
        "intents": results,
        "removed": removed,
        "gc": gc,
    }


def render_recover_report(report: dict[str, Any]) -> str:
    """人类可读的 recover 报告（``--format yaml/json`` 走通用信封）。"""
    lines = [
        f"experiment {report['experiment_id'][:8]}… server phase={report['server_phase']}",
    ]
    if not report["intents"]:
        lines.append("  本地无待恢复 intent")
    for item in report["intents"]:
        marker = {
            "committed": "[committed]",
            "stale": "[stale]",
            "conflict": "[conflict]",
            "pending": "[pending]",
        }[item["branch"]]
        lines.append(f"  {marker} {item['action']} {item['from_phase']} → {item['to_phase']}")
        for row in str(item["note"]).splitlines():
            lines.append(f"      {row}")
    if report["removed"]:
        lines.append("  GC 已删除:")
        for path in report["removed"]:
            lines.append(f"    - {path}")
    return "\n".join(lines)
