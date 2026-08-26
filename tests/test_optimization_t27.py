"""T27：bootstrap / FS 探测收窄 except Exception，YAML 损坏要 debug 留痕。"""

from __future__ import annotations

from pathlib import Path

import pytest

from map_client import bootstrap


def test_surviving_local_tokens_corrupt_yaml_returns_empty_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    local = tmp_path / "agents.local.yaml"
    local.write_text("{]", encoding="utf-8")
    with caplog.at_level("DEBUG", logger="map_client.bootstrap"):
        tokens = bootstrap._surviving_local_tokens(tmp_path)
    assert tokens == []
    assert any("could not read surviving local tokens" in rec.message for rec in caplog.records)


def test_surviving_local_tokens_type_error_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "agents.local.yaml").write_text("personas: {}\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_read_yaml", lambda path: (_ for _ in ()).throw(TypeError("bug")))
    with pytest.raises(TypeError, match="bug"):
        bootstrap._surviving_local_tokens(tmp_path)


def test_public_bootstrap_setup_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bootstrap.httpx,
        "Client",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("bad proxy")),
    )
    assert (
        bootstrap._public_bootstrap(
            "http://127.0.0.1:18400",
            project_key="k",
            project_name="n",
            workspace_path="/tmp/x",
            description=None,
        )
        is None
    )


def test_maybe_auto_sync_type_error_from_resolve_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    from cli.fs_projection import maybe_auto_sync

    monkeypatch.setattr("cli.main._cli_options", {"persona": "host", "dry_run": False})
    monkeypatch.setattr(
        "cli.main.resolve_client",
        lambda **kwargs: (_ for _ in ()).throw(TypeError("unexpected")),
    )
    with pytest.raises(TypeError, match="unexpected"):
        maybe_auto_sync(no_sync=False, workspace=Path("."))
