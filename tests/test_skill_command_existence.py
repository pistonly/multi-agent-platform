"""Skill 分发面命令存在性护栏（issue #1 同类漂移的通用化）。

背景
----
``tests/test_docs_consistency.py`` 的 M50G 只把「``topic close --reason``
取值 ↔ ``CLOSE_REASON_LEGAL`` 枚举」这一条文案关系钉住了。2026-09-11 的
分发面体检查出**同一类**问题仍在别处存活——skill 文案教 agent 执行的
``map`` 命令在 CLI 里根本不存在：

* ``topic-host/SKILL.md`` + ``topic-host/references/experiment-gate-rubric.md``：
  ``fs comment`` / ``fs advance-round`` / ``fs close``——``map fs`` 现在只剩
  ``verify-audit``（``cli/main.py`` 只把 ``fs`` 绑给 ``verify_audit_app``；
  ``cli/commands/fs.py`` docstring 明说「用户命令不暴露 fs」）。host agent
  照「快速判断」表执行必然拿到 ``Error: No such command 'comment'``。
* ``experiment-host/references/lifecycle-transitions.md``：
  ``map admin notification cleanup-v1``——无 ``admin`` 命令组。
* server 的 410 引导串 ``map fs archive``（归档现为 ``map topic archive``）。

根因不是某一条文案写错，而是**「skill 文案里的命令引用」与「CLI 命令树」
之间没有可执行断言**。本文件补上这一层，与 M50G 同构：断言解析出的
事实（命令树），而不是硬编码命令清单。

四层断言
--------
1. skill 文案引用的每个 ``map <group> [<sub>]`` 都必须在真实命令树里存在
   （含跨越 ``--persona host`` 这类取值选项）。
2. 退役形态显式禁止，并给出比第 1 层更精确的定位提示：``map fs`` 只允许
   ``verify-audit``；``map admin`` 一律禁止；裸 ``fs <sub>`` 形态同样禁止
   （审计时 ``topic-host`` 的三处违规正是缺 ``map`` 前缀的裸形态）。
3. 反向守卫：必须真解析到 ≥ ``_MIN_PARSED_REFS`` 条命令引用，否则用例会
   空转假绿（解析器与文案写法脱节时先红，而不是静默放过）。
4. 漂移注入自检：把本次审计捕获的真实违规样本喂给判定函数，必须被判为
   违规——证明 1/2 层真拦得住，而不是「碰巧当前没有违规」。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.main import get_command

from cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent

# 与 test_docs_consistency.SKILL_MIRRORS / test_red_line_clause 保持一致：
# .cursor/skills 是源，cli/skills 是随 wheel 分发的同步副本。
SKILL_MIRRORS = ["cli/skills", ".cursor/skills"]

# ``map fs`` 允许的子命令白名单。fs 组现在是 audit 漂移检测专用；话题/实验的
# FS 写路径全部收归 ``map topic``（v0.13 M58 起，v0.14 M60 归档命令改名）。
FS_ALLOWED_SUBCOMMANDS = {"verify-audit"}

# 明确不存在的命令组：出现即违规（``admin`` 不在 CLI 面）。
FORBIDDEN_GROUPS = {"admin"}

# 非 CLI 用法的 ``map ...`` 前缀（git commit message 约定 ``map exp <short-id>:``）。
_PSEUDO_PREFIXES = {"exp"}

# 取值的全局/前置选项：其后紧跟的 token 是选项值，不是子命令。
_VALUE_OPTIONS = {
    "-p",
    "--persona",
    "--project-root",
    "--config-root",
    "-o",
    "--format",
    "--repo",
}

# 解析到的命令引用下限。当前实测 306 条，留足余量但足以发现解析器脱节；
# 只降不升说明文案被删空或正则失效——两种都要人看一眼。
_MIN_PARSED_REFS = 40

_INVOCATION = re.compile(r"\bmap\s+([^\n`]*)")
_TOKEN = re.compile(r"^[a-z][a-z0-9-]*$")
_PLACEHOLDER = re.compile(r"^[<\[].*|^\.\.\.$")
_BARE_FS = re.compile(r"`fs\s+([a-z][a-z0-9-]*)")


def _command_tree() -> tuple[set[str], set[tuple[str, ...]]]:
    """内省 typer app，返回 (顶层命令组集合, 全部命令路径元组集合)。"""
    root = get_command(app)
    paths: set[tuple[str, ...]] = set()
    stack: list[tuple[str, ...]] = [()]
    holders: list[object] = [root]
    while stack:
        prefix = stack.pop()
        cmd = holders.pop()
        for name, sub in getattr(cmd, "commands", {}).items():
            path = prefix + (name,)
            paths.add(path)
            stack.append(path)
            holders.append(sub)
    return {path[0] for path in paths}, paths


def _iter_map_invocations(text: str) -> list[tuple[int, tuple[str, ...]]]:
    """解析 markdown 里的 ``map ...`` 调用，返回 (行号, 命令 token 元组)。

    形状固定为 ``map [全局选项] <group> [<sub>] [子命令选项…]``：
    先跳过前导全局选项，取命令组；子命令**必须紧邻**命令组——命令组之后
    若先是 ``--flag``，则说明这是命令组自身的选项（如 ``map feedback
    --type bug``），不能再把 ``bug`` 当子命令。占位符（``<command>`` /
    ``...`` / ``[--persona <name>]``）不产出命令 token。
    """
    found: list[tuple[int, tuple[str, ...]]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in _INVOCATION.finditer(line):
            tokens = match.group(1).split()
            index = 0
            while index < len(tokens) and tokens[index].startswith("-"):
                token = tokens[index]
                step = 2 if ("=" not in token and token in _VALUE_OPTIONS) else 1
                index += step
            if index >= len(tokens):
                continue
            head = tokens[index]
            if _PLACEHOLDER.match(head) or not _TOKEN.match(head):
                continue
            command = [head]
            if index + 1 < len(tokens) and _TOKEN.match(tokens[index + 1]):
                command.append(tokens[index + 1])
            found.append((lineno, tuple(command)))
    return found


def _violations(text: str) -> list[str]:
    """返回文本里所有「命令不存在」违规的 ``行号: 命令`` 描述。

    子命令只在**确实有子命令的组**上校验：``bootstrap`` / ``work`` /
    ``status`` 这类叶子命令后面跟的英文词是散文（如 JSON 示例里的
    ``"Run map bootstrap to generate tokens"``），不是子命令。
    """
    groups, paths = _command_tree()
    groups_with_subs = {path[0] for path in paths if len(path) > 1}
    problems: list[str] = []
    for lineno, command in _iter_map_invocations(text):
        group = command[0]
        sub = command[1] if len(command) > 1 else ""
        if group in _PSEUDO_PREFIXES:
            continue
        if group in FORBIDDEN_GROUPS:
            problems.append(f"{lineno}: map {' '.join(command)}（无 `{group}` 命令组）")
            continue
        if group not in groups:
            problems.append(f"{lineno}: map {' '.join(command)}（未知命令组）")
            continue
        if group == "fs":
            if sub not in FS_ALLOWED_SUBCOMMANDS:
                problems.append(
                    f"{lineno}: map {' '.join(command)}（`map fs {sub}` 不存在；"
                    f"只允许 {sorted(FS_ALLOWED_SUBCOMMANDS)}，话题侧用 `map topic ...`）"
                )
            continue
        if sub and group in groups_with_subs and command[:2] not in paths:
            problems.append(f"{lineno}: map {' '.join(command)}（`{group} {sub}` 无此子命令）")
    return problems


def _skill_files(mirror: str) -> list[Path]:
    root = REPO_ROOT / mirror
    assert root.is_dir(), f"skill 镜像目录缺失：{root}（被移动或被删？）"
    return sorted(root.rglob("*.md"))


@pytest.mark.parametrize("mirror", SKILL_MIRRORS)
def test_skill_command_refs_exist_in_cli(mirror: str) -> None:
    """第 1+2 层：skill 文案引用的每条 ``map`` 命令都必须存在于 CLI 命令树。"""
    offenders: list[str] = []
    for path in _skill_files(mirror):
        for problem in _violations(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{problem}")
    assert not offenders, (
        "skill 分发面教了不存在的 map 命令（照文案执行的 agent 必然拿到 "
        "Error: No such command）；违规：\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("mirror", SKILL_MIRRORS)
def test_no_bare_retired_fs_forms_in_skills(mirror: str) -> None:
    """第 2 层补充：分发面不得出现裸 ``fs <sub>`` 形态（除 verify-audit）。

    审计时 ``topic-host`` 的三处违规是 ``fs comment`` 这种缺 ``map`` 前缀的
    形态，光看 ``map`` 前缀扫不到，故单列一条。
    """
    offenders: list[str] = []
    for path in _skill_files(mirror):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for sub in _BARE_FS.findall(line):
                if sub not in FS_ALLOWED_SUBCOMMANDS:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} → fs {sub}")
    assert not offenders, (
        "分发面出现退役的裸 `fs <sub>` 形态（正确写法是 `map topic <sub>`；"
        "`map fs` 现仅 verify-audit）；违规：\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("mirror", SKILL_MIRRORS)
def test_parser_actually_finds_commands(mirror: str) -> None:
    """第 3 层反向守卫：必须真解析到足量命令引用，否则上面是空转假绿。"""
    total = 0
    for path in _skill_files(mirror):
        total += len(_iter_map_invocations(path.read_text(encoding="utf-8")))
    assert total >= _MIN_PARSED_REFS, (
        f"{mirror} 只解析出 {total} 条 `map` 命令引用（下限 {_MIN_PARSED_REFS}）："
        f"要么文案被删空，要么 _INVOCATION 正则与写法已脱节——本文件的"
        f"存在性断言会静默失效，请先修解析器"
    )


@pytest.mark.parametrize(
    ("label", "sample"),
    [
        # 2026-09-11 审计真实捕获的违规样本，逐条回归（原貌可能是裸 `fs ...`，
        # 统一补成带 `map` 前缀的等价形态喂给 _violations）。
        ("退役 fs 发言", "发 Round Summary（`fs comment --topic <slug> --round-summary`）"),
        ("退役 fs 推进", "host 可用 `fs advance-round --topic <slug> --ready` 标记 ready"),
        ("退役 fs 关闭", "结论承载用 `fs close --note`。"),
        ("退役 fs 归档", "并引导 `map topic migrate` / `map fs archive`"),
        ("不存在的 admin 组", "是否需要 `map admin notification cleanup-v1` 兜底"),
        ("不存在的子命令", "`map topic nonexistent-sub --topic <slug>`"),
    ],
)
def test_known_drift_samples_are_caught(label: str, sample: str) -> None:
    """第 4 层漂移注入：历史真实违规样本必须被判为违规。"""
    normalized = re.sub(r"`fs ", "`map fs ", sample)
    assert _violations(normalized), f"漂移样本未被拦截（{label}）：{sample}"


@pytest.mark.parametrize(
    ("label", "sample"),
    [
        ("裸 fs 发言", "发 Round Summary（`fs comment --topic <slug>`）"),
        ("裸 fs 关闭", "结论承载用 `fs close --note`"),
        ("裸 fs 推进", "`fs advance-round --topic <slug> --ready`"),
    ],
)
def test_bare_retired_fs_forms_are_caught(label: str, sample: str) -> None:
    """裸 ``fs <sub>``（无 ``map`` 前缀）必须被裸形态检查拦下。"""
    bad = [sub for sub in _BARE_FS.findall(sample) if sub not in FS_ALLOWED_SUBCOMMANDS]
    assert bad, f"裸退役 fs 形态未被拦截（{label}）：{sample}"


@pytest.mark.parametrize(
    ("label", "sample"),
    [
        ("合法全局选项跨越", "map --persona host topic show --id <slug>"),
        ("合法带值选项", "map --format json --persona reviewer experiment status --id <x>"),
        ("合法组选项不误判子命令", "map feedback --type bug --title \"...\""),
        ("合法裸形态白名单", "先调用 `map fs verify-audit` 确认漂移范围"),
        ("占位符不产命令", "`map --json <command>` 是快捷方式"),
        ("叶子命令后的英文散文", "\"hint\": \"Run map bootstrap to generate tokens\""),
        ("叶子命令后的英文散文 2", "Then run map work and check the result."),
    ],
)
def test_valid_forms_are_not_flagged(label: str, sample: str) -> None:
    """反向守卫：合法写法不得误报（防「把对的也拦下」的死锁）。"""
    assert _violations(sample) == [], f"合法写法被误报（{label}）：{sample}"


def test_fs_group_is_audit_only() -> None:
    """护栏前提：``map fs`` 确实只剩 verify-audit。

    若将来 fs 组重新长出话题子命令，白名单必须同步——这条用例让「白名单
    悄悄过期」先红，而不是让失败面漂移到第 1 层。
    """
    _, paths = _command_tree()
    actual = {path[1] for path in paths if len(path) == 2 and path[0] == "fs"}
    assert actual == FS_ALLOWED_SUBCOMMANDS, (
        f"`map fs` 实际子命令 {sorted(actual)} 与白名单 "
        f"{sorted(FS_ALLOWED_SUBCOMMANDS)} 不一致；请同步 FS_ALLOWED_SUBCOMMANDS"
    )
