from map_client.project_config import (
    find_map_dir,
    load_project_map_config,
    resolve_client,
)
from map_client.testing import MAPTestClientTransport


def test_find_map_dir(tmp_path):
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text("project_key: demo\napi_url: http://test\n", encoding="utf-8")
    sub = tmp_path / "src" / "nested"
    sub.mkdir(parents=True)
    assert find_map_dir(sub) == map_dir


def test_load_project_map_config(tmp_path):
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "project_key: demo\napi_url: http://test\nproject_id: abc\ndefault_persona: participant\n",
        encoding="utf-8",
    )
    (map_dir / "agents.yaml").write_text(
        "personas:\n  host:\n    agent_name: demo-host\n    description: host role\n",
        encoding="utf-8",
    )
    (map_dir / "agents.local.yaml").write_text(
        "personas:\n  host:\n    token: secret-token\n",
        encoding="utf-8",
    )
    cfg = load_project_map_config(map_dir=map_dir)
    assert cfg.project_key == "demo"
    assert cfg.default_persona == "participant"
    assert cfg.token_for("host") == "secret-token"
    assert cfg.personas["host"].agent_name == "demo-host"


def test_token_for_accepts_agent_name_long_name(tmp_path):
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text("project_key: demo\napi_url: http://test\n", encoding="utf-8")
    (map_dir / "agents.yaml").write_text(
        "personas:\n  host:\n    agent_name: demo-host\n  reviewer:\n    agent_name: demo-reviewer\n",
        encoding="utf-8",
    )
    (map_dir / "agents.local.yaml").write_text(
        "personas:\n  host:\n    token: host-tok\n  reviewer:\n    token: rev-tok\n",
        encoding="utf-8",
    )
    cfg = load_project_map_config(map_dir=map_dir)
    assert cfg.resolve_persona("reviewer") == "reviewer"
    assert cfg.resolve_persona("demo-reviewer") == "reviewer"
    assert cfg.token_for("demo-reviewer") == "rev-tok"


def test_resolve_client_uses_persona(tmp_path):
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text("project_key: demo\napi_url: http://test\n", encoding="utf-8")
    (map_dir / "agents.yaml").write_text(
        "personas:\n  host:\n    agent_name: demo-host\n",
        encoding="utf-8",
    )
    (map_dir / "agents.local.yaml").write_text(
        "personas:\n  host:\n    token: tok123\n",
        encoding="utf-8",
    )
    client = resolve_client(persona="host", project_root=tmp_path, transport=MAPTestClientTransport(None))
    try:
        assert client.token == "tok123"
        assert client.base_url == "http://test"
    finally:
        client.close()


def test_bootstrap_writes_map_dir(client, admin_token, tmp_path, monkeypatch):
    from map_client.bootstrap import bootstrap_project_map

    _, admin_token_value = admin_token
    monkeypatch.setenv("MAP_ADMIN_TOKEN", admin_token_value)
    monkeypatch.setenv("MAP_API_URL", "http://test")

    transport = MAPTestClientTransport(client)
    result = bootstrap_project_map(
        project_key=f"boot-{tmp_path.name[:8]}",
        project_name="Bootstrap Test",
        workspace_path=str(tmp_path),
        project_root=tmp_path,
        api_url="http://test",
        transport=transport,
    )
    assert (tmp_path / ".map" / "config.yaml").is_file()
    assert (tmp_path / ".map" / "agents.local.yaml").is_file()
    assert "host" in result.config.tokens
    assert result.config.token_for("host")

    host_client = result.config.client_for("host", transport=transport)
    me = host_client.get_me()
    assert me.project_id is not None
    host_client.close()
