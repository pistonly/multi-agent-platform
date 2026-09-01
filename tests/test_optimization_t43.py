"""T43 专项测试：agent_client rc 凭证解析一次性缓存。

原实现 ``_resolve_env_key`` 按 key×文件逐次读盘（每次查找重读全部
候选 rc 文件）；T43 改为每文件 ``parse_export_env_file`` 解析一次、
实例内缓存。本文件不依赖 ``claude_agent_sdk``（模块级为懒加载），
可始终运行。
"""

from __future__ import annotations

from pathlib import Path

from cli import agent_client as agent_client_module
from cli.agent_client import PersonaAgentClient


def _make_agent(project_root: Path) -> PersonaAgentClient:
    return PersonaAgentClient(
        persona="host",
        state={},
        save_state_fn=lambda: None,
        project_root=project_root,
        extra_env={},
        model="fixed-model",
        _client_factory=lambda options: None,
    )


def test_rc_files_parsed_once_and_cached(tmp_path: Path, monkeypatch) -> None:
    hi = tmp_path / "hi.env"
    lo = tmp_path / "lo.env"
    hi.write_text('export ANTHROPIC_AUTH_TOKEN="from-hi"\n', encoding="utf-8")
    lo.write_text(
        "export ANTHROPIC_AUTH_TOKEN=from-lo\n"
        "export ANTHROPIC_BASE_URL=https://lo.example  # inline comment\n",
        encoding="utf-8",
    )
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(key, raising=False)

    calls: list[Path] = []
    real_parse = agent_client_module.parse_export_env_file

    def counting_parse(path: Path) -> dict[str, str]:
        calls.append(path)
        return real_parse(path)

    monkeypatch.setattr(agent_client_module, "parse_export_env_file", counting_parse)

    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, "_candidate_rc_files", lambda: [hi, lo])

    env = agent._resolve_env()

    # 3 个 credential key 查找 → 每个文件只解析一次（原实现为 3×2=6 次读盘）
    assert calls == [hi, lo]
    # 高优先级文件优先；引号剥离语义不变
    assert env["ANTHROPIC_AUTH_TOKEN"] == "from-hi"
    # 低优先级文件补充缺失 key；行内注释剥离语义不变
    assert agent._resolve_env_key("ANTHROPIC_BASE_URL") == "https://lo.example"
    # 缓存生效：后续查找不再触发解析
    assert calls == [hi, lo]


def test_rc_env_key_prefers_process_env_over_rc_files(tmp_path: Path, monkeypatch) -> None:
    rc = tmp_path / "rc.env"
    rc.write_text("export ANTHROPIC_AUTH_TOKEN=from-rc\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "from-env")

    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, "_candidate_rc_files", lambda: [rc])

    assert agent._resolve_env_key("ANTHROPIC_AUTH_TOKEN") == "from-env"


def test_missing_rc_files_parse_to_empty(tmp_path: Path, monkeypatch) -> None:
    agent = _make_agent(tmp_path)
    monkeypatch.setattr(
        agent, "_candidate_rc_files", lambda: [tmp_path / "nope.env", tmp_path / "gone.env"]
    )

    assert agent._resolve_env_key("ANTHROPIC_API_KEY") is None
    assert agent._rc_env_values() == {tmp_path / "nope.env": {}, tmp_path / "gone.env": {}}
