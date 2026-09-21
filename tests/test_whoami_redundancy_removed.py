"""whoami 冗余消除 · 三处真相源一致（实验 4e4206de I2 / A2）。

钉住验收：唤醒链路不再把独立 `persona whoami` 当第一步——`map work` 的
agent 块即身份。三处真相源必须同步为 work-first：
  ① wake.md「唤醒后四步」
  ② cli/agent_client.py waker 集成系统提示词
  ③ AGENTS.md「Waker 与职责边界」唤醒流程行（CLAUDE.md 符号链接同文件）

kind-dispatch 生成块逐字节不变由 test_work_kinds.py::test_registry_matches_wake_md_dispatch_table
覆盖（本实验只动标记块外的四步正文，未碰块内容）。
"""

from __future__ import annotations

import re
from pathlib import Path

from cli.agent_client import PersonaAgentClient

REPO_ROOT = Path(__file__).resolve().parent.parent

# 两处 wake.md 镜像（源 + wheel 分发副本），须逐字节一致。
WAKE_MD_MIRRORS = [
    REPO_ROOT / ".agent/skills/map-project-collab/references/wake.md",
    REPO_ROOT / "cli/skills/map-project-collab/references/wake.md",
]


def _waker_prompt(persona: str = "host") -> str:
    # _system_append_prompt 只用 persona/integration；绕开重型构造函数。
    client = object.__new__(PersonaAgentClient)
    client.persona = persona
    client.integration = "waker"
    return client._system_append_prompt()


# --- ② agent_client waker 提示词 --------------------------------------------


def test_waker_prompt_is_work_first() -> None:
    prompt = _waker_prompt("host")
    assert "`map --persona host work`" in prompt, prompt
    # 不再以「Confirm identity with ... persona whoami」把 whoami 当第一步。
    assert "Confirm identity with" not in prompt, prompt
    # whoami 至多作为条件回退（agent 块缺失/身份存疑才单独跑）。
    assert "only if" in prompt and "persona whoami" in prompt, prompt


def test_waker_prompt_work_precedes_whoami() -> None:
    prompt = _waker_prompt("host").lower()
    assert prompt.index("work") < prompt.index("persona whoami"), prompt


# --- ① wake.md 四步（两镜像）------------------------------------------------


def _four_step_block(text: str) -> str:
    m = re.search(r"## 唤醒后四步\s*\n(.*?)\n## ", text, re.S)
    assert m, "wake.md 缺『唤醒后四步』段"
    return m.group(1)


def test_wake_md_all_mirrors_identical() -> None:
    blobs = [p.read_text(encoding="utf-8") for p in WAKE_MD_MIRRORS]
    assert blobs[0] == blobs[1], "cli/skills 与 .agent/skills wake.md 漂移"


def test_wake_md_step1_is_work_not_whoami() -> None:
    for path in WAKE_MD_MIRRORS:
        block = _four_step_block(path.read_text(encoding="utf-8"))
        first_line = block.strip().splitlines()[0]
        assert "work`" in first_line, f"{path.name} step1 应为 map work：{first_line}"
        assert "persona whoami" not in first_line, first_line
        # whoami 仍存在但降级为条件回退（身份存疑/agent 块缺失才单独跑）。
        assert "persona whoami" in block, f"{path.name} 应保留 whoami 条件回退"
        assert "省一次独立调用" in block or "省独立" in block, path.name


def test_wake_md_keeps_four_steps_and_label() -> None:
    # 「四步」标签被 5 个 SKILL.md（RUNTIME_CONTRACT_FILES）引用，必须保留 4 个编号步。
    for path in WAKE_MD_MIRRORS:
        text = path.read_text(encoding="utf-8")
        assert "## 唤醒后四步" in text
        block = _four_step_block(text)
        numbered = re.findall(r"^\d\.", block, re.M)
        assert len(numbered) == 4, f"{path.name} 四步编号数应为 4，实得 {len(numbered)}"


# --- ③ AGENTS.md 唤醒流程行 --------------------------------------------------


def test_agents_md_wake_flow_row_is_work_first() -> None:
    row = next(
        line for line in (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()
        if "被唤醒的 Agent" in line
    )
    assert "`map work`" in row, row
    assert "`whoami` → `map work`" not in row, row
    # CLAUDE.md 是 AGENTS.md 的符号链接，内容必须同源解析。
    claude = REPO_ROOT / "CLAUDE.md"
    assert claude.is_symlink(), "CLAUDE.md 应为指向 AGENTS.md 的符号链接"
    assert "被唤醒的 Agent" in claude.read_text(encoding="utf-8")
