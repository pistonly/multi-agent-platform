"""M54A: subcommand-level ``--format`` option injection.

Historically ``--format`` was only registered on the root ``@app.callback()``,
so ``map experiment list --format json`` failed with ``No such option
'--format'`` — the flag had to come *before* the subcommand name (E1).
This module makes the long form ``--format`` available on **every leaf
command** of the CLI tree, regardless of nesting depth, without touching
each command module by hand.

Mechanism
---------
The root ``typer.Typer`` is created with ``cls=make_group_cls(apply_hook)``
(see ``cli/main.py``). Click resolves subcommands lazily at parse time via
``Group.get_command``; our subclass intercepts every lookup and patches the
returned command:

* **leaf command** — append a ``--format`` option and wrap the
  callback so the parsed value is handed to ``apply_hook`` *before* the
  command body runs. The hook (``_apply_sub_format`` in ``cli/main.py``)
  resolves/validates the value and writes it into ``_cli_options``, so an
  explicit subcommand value wins over the global flag / ``MAP_CLI_FORMAT``
  (the global callback already ran by then).
* **nested group** — instance-patch its ``get_command`` with the same
  wrapper, recursively. This covers sub-apps created with the default
  ``TyperGroup`` (e.g. ``experiment`` → ``review`` / ``plan`` / ``lock``).

All patches are idempotent (flagged on the command object) and add no
import-time cost beyond a tiny closure per group; the tree is only touched
when a command is actually resolved.

Required-option enforcement (typer×click compat)
-------------------------------------------------
typer <0.26 builds ``TyperOption`` / ``TyperArgument`` with an explicit
``default=None``. Click 8.2 redefined ``default=None`` as a *valid* default
("no default" became the ``UNSET`` sentinel), so a missing required option
resolves to a legitimate-looking default and click skips its
``MissingParameter`` check — every ``typer.Option(...)`` required flag in the
CLI silently passes ``None`` into the callback (reproduced with typer 0.16.1
+ click 8.3.2 / 8.4.2). ``_restore_required_check`` normalizes required
params back to ``UNSET`` at patch time, restoring native click validation
(usage error, exit 2) without constraining the published dependency range.

Only the long form ``--format`` is injected. The global ``-o`` shorthand
stays root-only to avoid future short-flag collisions on leaf commands.

Typer >=0.26 vendors Click (no third-party ``click`` package). Option
construction and group detection therefore branch on ``typer._click``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from difflib import get_close_matches
from typing import Any

import typer
from typer.core import TyperGroup

#: Hook that receives the raw ``--format`` string (``None`` when absent).
#: ``cli.main._apply_sub_format`` implements resolution / validation.
ApplyHook = Callable[[str | None], None]

_PATCH_FLAG = "_map_sub_format_patched"
_FORMAT_HELP = (
    "Output format for this command: 'table', 'yaml', or 'json'. "
    "Overrides the global --format flag and the MAP_CLI_FORMAT env "
    "var (v0.12 M54A)."
)


def _vendored_click() -> bool:
    """True when Typer ships Click internally (0.26+) instead of depending on it."""
    return hasattr(typer, "_click")


def _sub_format_option() -> Any:
    """The ``--format`` option appended to every leaf command."""
    if _vendored_click():
        from typer.core import TyperOption

        return TyperOption(param_decls=["--format"], default=None, help=_FORMAT_HELP)
    import click

    return click.Option(["--format"], default=None, help=_FORMAT_HELP)


def _unset_sentinel() -> Any:
    """click>=8.2 的 ``UNSET`` sentinel（「无默认值」的显式表达）。

    vendored click（typer>=0.26，实测必填校验原生生效、无 UNSET 概念）与
    click<8.2（``value is None`` 即 missing）都返回 None——这两种组合
    无需归一化。
    """
    if _vendored_click():
        return None
    try:
        from click.core import UNSET
    except ImportError:
        return None
    return UNSET


def _restore_required_check(cmd: Any) -> None:
    """typer(<0.26) × click(>=8.2) 必填校验失效修复。

    根因：``TyperOption`` / ``TyperArgument`` 构造时对「无默认值」显式传
    ``default=None``；click 8.2 起 ``default=None`` 是合法默认值，「无默认」
    改由 ``UNSET`` sentinel 表达。于是 required 参数缺失时 ``get_default()``
    返回 None 被当作有效默认值，``Parameter.process_value`` 跳过
    ``MissingParameter`` 校验，回调以 ``None`` 直跑（实测 typer 0.16.1 +
    click 8.3.2 / 8.4.2 下全部 ``typer.Option(...)`` 必填项失效）。

    修复：把 ``required=True 且 default is None`` 的参数 default 归一为
    ``UNSET``，恢复 click 原生校验（缺参 → usage error，exit 2）。
    版本无关且幂等：UNSET 不存在（click<8.2 / vendored click）时不动；
    default 已是 UNSET 时条件不命中。回归测试见
    ``tests/test_required_option_guard.py``。
    """
    unset = _unset_sentinel()
    if unset is None:
        return
    for param in getattr(cmd, "params", None) or []:
        if getattr(param, "required", False) is True and param.default is None:
            param.default = unset


def _is_group(cmd: Any) -> bool:
    """TyperGroup in both click-backed and vendored-Click Typer; plus classic Group."""
    if cmd is None:
        return False
    if isinstance(cmd, TyperGroup):
        return True
    return hasattr(cmd, "get_command") and hasattr(cmd, "list_commands")


def _patch_leaf(cmd: Any, apply: ApplyHook) -> Any:
    """Patch a leaf command: restore required-option checks + inject ``--format``."""
    if getattr(cmd, _PATCH_FLAG, False):
        return cmd
    setattr(cmd, _PATCH_FLAG, True)
    # typer×click 必填校验修复对所有 leaf 生效（与是否注入 --format 无关）。
    _restore_required_check(cmd)
    _patch_did_you_mean(cmd)
    # Defensive: if a future command declares its own --format, leave it be
    # (click would reject duplicate option names at parse time otherwise).
    params = getattr(cmd, "params", None) or []
    if any(getattr(param, "name", None) == "format" for param in params):
        return cmd
    cmd.params.append(_sub_format_option())
    original = cmd.callback

    def callback(**kwargs: Any) -> Any:
        apply(kwargs.pop("format", None))
        if original is None:
            return None
        return original(**kwargs)

    cmd.callback = callback
    return cmd


def _patch_group(group: Any, apply: ApplyHook) -> Any:
    """Instance-patch a (possibly nested) group so its children get patched."""
    if getattr(group, _PATCH_FLAG, False):
        return group
    setattr(group, _PATCH_FLAG, True)
    original_get = group.get_command

    def get_command(ctx: Any, cmd_name: str) -> Any:
        sub = original_get(ctx, cmd_name)
        return _patch_any(sub, apply)

    group.get_command = get_command  # type: ignore[method-assign]
    return group


def _patch_any(cmd: Any, apply: ApplyHook) -> Any:
    if cmd is None:
        return None
    if _is_group(cmd):
        return _patch_group(cmd, apply)
    return _patch_leaf(cmd, apply)


def _did_you_mean_hint(
    message: str,
    known_opts: list[str],
    args: list[str],
    flags_by_name: dict[str, list[str]],
    ctx_args: list[str],
) -> str | None:
    """从 click UsageError 消息提取可落地的「你是不是想用」建议。

    四种形态(cli-hygiene-batch / A5,T2-P2):
    - missing option:``map experiment status myslug`` —— required ``--id`` 缺失
      (vendored click 报 ``Missing parameter: experiment_id``,param 名非 flag,
      故经 flags_by_name 反查)→ 建议 ``--id <slug>``(验收形态)。
    - extra argument:命令已给 id 又裸传 slug(``experiment status --id x myslug``)
      → 建议 ``--id <token>``。
    - no such option:flag 拼错(如 ``--topc``)→ 对已知 option 名做近邻匹配。
    """
    match = re.search(r"missing parameter:\s*(\S+)", message, re.IGNORECASE)
    if match:
        pname = match.group(1)
        flags = flags_by_name.get(pname) or []
        target = next((f for f in ("--id", "--topic") if f in flags), None)
        if target is None:
            target = "--id" if "--id" in known_opts else ("--topic" if "--topic" in known_opts else None)
            if target is None:
                return None
        token = next((a for a in ctx_args if not a.startswith("-")), None)
        return f"你是不是想用 {target} {token if token else '<slug>'}"
    match = re.search(r"extra argument\(s\)? \(([^)]+)\)", message, re.IGNORECASE)
    if match:
        target = "--id" if "--id" in known_opts else "--topic"
        return f"你是不是想用 {target} {match.group(1)}"
    # click >=8.0 原生 no-such-option 消息带引号无冒号
    # (``No such option '--sumary'. Did you mean '--summary'?``)，旧形态
    # ``No such option: --sumary`` 亦需兼容——两种都归一到 token 提取。
    match = re.search(r"no such option\s*[:']?\s*'?(\S+)", message, re.IGNORECASE)
    if match:
        bad = match.group(1)
        close = get_close_matches(bad, known_opts, n=1, cutoff=0.5)
        if close:
            return f"你是不是想用 {close[0]}"
        target = "--id" if "--id" in known_opts else "--topic"
        return f"你是不是想用 {target}"
    return None


def _patch_did_you_mean(cmd: Any) -> None:
    """T2-P2 (A5): id/topic 域 leaf 的参数解析 did-you-mean。

    只作用于带 ``--id`` 或 ``--topic`` option 的命令(experiment / fs / topic
    的 id 域高频命令);不改 exit code 与消息主体,只在 UsageError 上追加一行
    ``Hint: ...``。click 错误类名在 vendored click 与独立 click 间不定,故
    以 ``message`` 关键字匹配,不依赖类类型——同时写 ``message`` 与 ``args[0]``
    双通道保证任一渲染路径都带 hint。
    """
    opts = [
        option
        for param in getattr(cmd, "params", None) or []
        for option in getattr(param, "opts", None) or []
    ]
    if "--id" not in opts and "--topic" not in opts:
        return
    flags_by_name = {
        param.name: list(getattr(param, "opts", None) or ())
        for param in getattr(cmd, "params", None) or []
        if getattr(param, "name", None)
    }
    orig_parse_args = cmd.parse_args

    def parse_args(ctx: Any, args: Any) -> Any:
        try:
            return orig_parse_args(ctx, args)
        except Exception as exc:
            hint = _did_you_mean_hint(
                str(exc),
                opts,
                list(args),
                flags_by_name,
                list(getattr(ctx, "args", None) or ()),
            )
            if hint and hasattr(exc, "message"):
                amended = f"{exc.message}  Hint: {hint}"
                exc.message = amended
                exc.args = (amended,) + tuple(exc.args[1:])
            raise

    cmd.parse_args = parse_args  # type: ignore[method-assign]


def make_group_cls(apply: ApplyHook) -> type[TyperGroup]:
    """Build the root group class used by ``typer.Typer(cls=...)``.

    ``apply`` is invoked lazily (inside command callbacks), so callers may
    pass a lambda resolving a function defined later in their module.
    """

    class _SubFormatGroup(TyperGroup):
        def get_command(self, ctx: Any, cmd_name: str) -> Any:
            sub = super().get_command(ctx, cmd_name)
            return _patch_any(sub, apply)

    return _SubFormatGroup
