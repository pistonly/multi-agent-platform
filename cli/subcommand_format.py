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
  command body runs. The hook (``_apply_sub_format`` in this module,
  T33) resolves/validates the value and writes it into
  ``cli.main._cli_options``, so an explicit subcommand value wins over
  the global flag / ``MAP_CLI_FORMAT`` (the global callback already ran
  by then).
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

import os
import re
from collections.abc import Callable
from difflib import get_close_matches
from pathlib import Path
from typing import Any

import typer
import yaml
from typer.core import TyperGroup

#: Hook that receives the raw ``--format`` string (``None`` when absent).
#: ``_apply_sub_format`` below implements resolution / validation.
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
    """Patch a leaf command: required-option checks + ``--json`` hint + inject ``--format``."""
    if getattr(cmd, _PATCH_FLAG, False):
        return cmd
    setattr(cmd, _PATCH_FLAG, True)
    # typer×click 必填校验修复对所有 leaf 生效（与是否注入 --format 无关）。
    _restore_required_check(cmd)
    _patch_did_you_mean(cmd)
    # 后置 --json 指引对所有 leaf 生效（A1）；与 did-you-mean 按 token 互斥
    # （--json 归这里的指引，其他拼写错误归 did-you-mean），不会双重 Hint。
    _patch_json_position_hint(cmd)
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
        if bad.strip("'.") == "--json":
            # 后置 --json 的指引由 _patch_json_position_hint 统一负责（所有 leaf，
            # 不只 id 域），这里让路避免双重 Hint。
            return None
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


_JSON_POSITION_HINT = (
    "--json 是全局选项，请置于子命令前，如 `map --json <cmd>`；"
    "或改用该命令的 --format json"
)


def _json_position_hint(message: str) -> str | None:
    """no-such-option 的 token 恰为 ``--json`` 时给出全局选项位置指引（A1）。

    token 用 flag 字符类提取，兼容 ``No such option: --json``（冒号形）与
    ``No such option '--json'.``（引号形）两种 click 消息；其他拼写错误
    （``--topc`` 等）不命中，仍归 did-you-mean。
    """
    match = re.search(r"no such option\s*[:']?\s*'?(--?[A-Za-z0-9_-]+)", message, re.IGNORECASE)
    if match and match.group(1) == "--json":
        return _JSON_POSITION_HINT
    return None


def _patch_json_position_hint(cmd: Any) -> None:
    """A1: 所有 leaf 的后置 ``--json`` 位置指引。

    与 did-you-mean 一样只追加一行 ``Hint: ...``，不改 exit code 与消息主体；
    对 ``--json`` 以外的解析错误完全透明。
    """
    orig_parse_args = cmd.parse_args

    def parse_args(ctx: Any, args: Any) -> Any:
        try:
            return orig_parse_args(ctx, args)
        except Exception as exc:
            hint = _json_position_hint(str(exc))
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


# ---- T33: N=2 release cutoff + subcommand --format resolution --------------
# Moved verbatim from ``cli/main.py`` (8a8822b5). ``MAP_CLI_RELEASE_VERSION``
# controls whether the post-N=2 behavior is active. The build / release
# script is expected to set this env var at install time once N=2 ships;
# for now we default to "0.x" so legacy yaml stays default. The
# hard-cutover behavior is:
#   * if release >= N=2, ``yaml`` / ``legacy`` formats are force-overridden
#     to ``json`` regardless of flag / env, with a one-shot stderr warning;
#   * if ``.map/config.yaml`` declares ``cli.default_format: yaml`` under
#     N=2 release, the same warning fires and the value is ignored.
_N2_RELEASE_MAJOR = 1  # bump here when N=2 ships
_N2_DEFAULT_RELEASE = "0.9"


def _parse_release_version(raw: str | None) -> tuple[int, int]:
    """Parse ``MAJOR.MINOR`` release tuple; return ``(0, 9)`` on any failure.

    The parser is intentionally permissive — anything we cannot interpret
    is treated as pre-N=2 so we never accidentally hard-cutover a script.
    """
    if not raw:
        return (0, 9)
    raw = raw.strip()
    if not raw:
        return (0, 9)
    parts = raw.split(".")
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return (0, 9)
    try:
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return (major, 0)
    return (major, minor)


def _is_n2_released() -> bool:
    """True iff ``MAP_CLI_RELEASE_VERSION`` parses to ``>= (_N2_RELEASE_MAJOR, 0)``."""
    raw = os.environ.get("MAP_CLI_RELEASE_VERSION")
    major, minor = _parse_release_version(raw)
    if major > _N2_RELEASE_MAJOR:
        return True
    if major < _N2_RELEASE_MAJOR:
        return False
    return minor >= 0  # any minor in the N=2 major line is in release


def _project_cli_default_format(project_root: Path | None) -> str | None:
    """Read ``cli.default_format`` from ``.map/config.yaml``.

    Returns ``None`` when the project root is not provided, when
    ``.map/config.yaml`` is missing, or when the key is absent. The check
    is intentionally narrow — we only look at the explicit key the release
    checklist flips; everything else (including unknown keys) is ignored.
    """
    if project_root is None:
        return None
    config_path = project_root / ".map" / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    cli_block = data.get("cli")
    if not isinstance(cli_block, dict):
        return None
    value = cli_block.get("default_format")
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def _apply_n2_hard_cutover(
    *,
    current_format: str,
    current_source: str,
    project_root: Path | None,
) -> tuple[str, str, list[str]]:
    """Apply 8a8822b5 (a) post-N=2 yaml hard-cutover.

    Returns ``(new_format, new_source, warnings)``. Warnings are emitted
    by the caller; this function is pure so it is unit-testable without
    touching ``typer.echo``.
    """
    if not _is_n2_released():
        return current_format, current_source, []

    warnings: list[str] = []
    config_default = _project_cli_default_format(project_root)
    if config_default == "yaml":
        warnings.append(
            "Warning: .map/config.yaml `cli.default_format: yaml` is "
            "deprecated in N=2; CLI is forcing json output."
        )
        return "json", "n2-release-cutover", warnings

    # Only warn when the user EXPLICITLY asked for yaml (flag / env / config).
    # The implicit default is the CLI's own choice — N=2 silently swaps it
    # to json without a deprecation warning, since the user never asked for
    # yaml in the first place.
    if current_format == "yaml" and current_source in {
        "explicit --format",
        "MAP_CLI_FORMAT env",
        "config cli.default_format",
    }:
        warnings.append(
            "Warning: yaml output format is removed in N=2; "
            "CLI is forcing json output."
        )
        return "json", "n2-release-cutover", warnings

    # Silent default-yaml → json transition (no warning).
    if current_format == "yaml" and current_source == "default":
        return "json", "n2-release-cutover", []

    return current_format, current_source, warnings


def _apply_sub_format(raw: str | None) -> None:
    """v0.12 M54A: resolve a subcommand-level ``--format`` value.

    Invoked from the leaf-command callback wrapper (``make_group_cls``),
    i.e. AFTER the global callback already resolved the global flag / env
    var — so an explicit subcommand value simply wins. Mirrors the global
    path (legacy alias handling, validation, N=2 hard cutover) so both
    spellings stay equivalent: ``map experiment list --format json`` and
    ``map --format json experiment list``.

    Runtime state ``cli.main._cli_options`` is resolved through the module
    object at call time (T23 injection-surface pattern) — monkeypatching
    ``cli.main._cli_options`` keeps affecting this hook.
    """
    from cli import main as _main  # runtime state (injection surface)

    _cli_options = _main._cli_options
    if raw is None:
        return
    resolved = raw.strip().lower()
    if resolved == "legacy":
        typer.echo(
            "Warning: --format legacy is deprecated; "
            "use 'yaml' explicitly. The 'legacy' alias will be removed in N=2.",
            err=True,
        )
        resolved = "yaml"
    if resolved not in ("yaml", "json", "table"):
        typer.echo(
            f"Error: unknown --format {resolved!r}; expected 'table', 'yaml', 'json', or 'legacy'.",
            err=True,
        )
        raise typer.Exit(2)
    prev = _cli_options.get("format")
    prev_source = _cli_options.get("format_source", "default")
    if prev is not None and prev != resolved:
        if prev_source == "explicit --format":
            typer.echo(
                f"Warning: subcommand --format={resolved} overrides "
                f"global --format={prev}",
                err=True,
            )
        elif prev_source == "explicit --json":
            typer.echo(
                f"Warning: subcommand --format={resolved} overrides global --json",
                err=True,
            )
        elif prev_source == "MAP_CLI_FORMAT env":
            typer.echo(
                f"Warning: subcommand --format={resolved} overrides "
                f"MAP_CLI_FORMAT={prev}",
                err=True,
            )
    resolved, source, n2_warnings = _apply_n2_hard_cutover(
        current_format=resolved,
        current_source="explicit --format",
        project_root=_cli_options.get("project_root"),
    )
    for warning in n2_warnings:
        typer.echo(warning, err=True)
    _cli_options["format"] = resolved
    _cli_options["format_source"] = f"{source} (subcommand)"
