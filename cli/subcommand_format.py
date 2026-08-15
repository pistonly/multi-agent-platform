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

* **leaf command** — append a ``--format`` click Option and wrap the
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

Only the long form ``--format`` is injected. The global ``-o`` shorthand
stays root-only to avoid future short-flag collisions on leaf commands.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click
from typer.core import TyperGroup

#: Hook that receives the raw ``--format`` string (``None`` when absent).
#: ``cli.main._apply_sub_format`` implements resolution / validation.
ApplyHook = Callable[[str | None], None]

_PATCH_FLAG = "_map_sub_format_patched"


def _sub_format_option() -> click.Option:
    """The click Option appended to every leaf command."""
    return click.Option(
        ["--format"],
        default=None,
        help=(
            "Output format for this command: 'table', 'yaml', or 'json'. "
            "Overrides the global --format flag and the MAP_CLI_FORMAT env "
            "var (v0.12 M54A)."
        ),
    )


def _patch_leaf(cmd: click.Command, apply: ApplyHook) -> click.Command:
    """Inject ``--format`` into a leaf command and route it to ``apply``."""
    if getattr(cmd, _PATCH_FLAG, False):
        return cmd
    # Defensive: if a future command declares its own --format, leave it be
    # (click would reject duplicate option names at parse time otherwise).
    if any(param.name == "format" for param in cmd.params):
        return cmd
    setattr(cmd, _PATCH_FLAG, True)
    cmd.params.append(_sub_format_option())
    original = cmd.callback

    def callback(**kwargs: Any) -> Any:
        apply(kwargs.pop("format", None))
        if original is None:
            return None
        return original(**kwargs)

    cmd.callback = callback
    return cmd


def _patch_group(group: click.Group, apply: ApplyHook) -> click.Group:
    """Instance-patch a (possibly nested) group so its children get patched."""
    if getattr(group, _PATCH_FLAG, False):
        return group
    setattr(group, _PATCH_FLAG, True)
    original_get = group.get_command

    def get_command(
        ctx: click.Context, cmd_name: str
    ) -> click.Command | None:
        sub = original_get(ctx, cmd_name)
        return _patch_any(sub, apply)

    group.get_command = get_command  # type: ignore[method-assign]
    return group


def _patch_any(
    cmd: click.Command | None, apply: ApplyHook
) -> click.Command | None:
    if cmd is None:
        return None
    if isinstance(cmd, click.Group):
        return _patch_group(cmd, apply)
    return _patch_leaf(cmd, apply)


def make_group_cls(apply: ApplyHook) -> type[TyperGroup]:
    """Build the root group class used by ``typer.Typer(cls=...)``.

    ``apply`` is invoked lazily (inside command callbacks), so callers may
    pass a lambda resolving a function defined later in their module.
    """

    class _SubFormatGroup(TyperGroup):
        def get_command(
            self, ctx: click.Context, cmd_name: str
        ) -> click.Command | None:
            sub = super().get_command(ctx, cmd_name)
            return _patch_any(sub, apply)

    return _SubFormatGroup
