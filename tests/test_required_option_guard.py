"""typer(<0.26) × click(>=8.2) 必填校验失效的解析期修复回归。

根因与机制见 ``cli/subcommand_format.py``：typer 构造 TyperOption 时对
「无默认值」显式传 ``default=None``，click 8.2 起将其视为合法默认值，
required 参数缺失不再抛 ``MissingParameter``，回调以 ``None`` 直跑
（曾导致 ``map fs topic-create --title X`` 缺 ``--slug`` 直接崩
``TypeError: PosixPath / NoneType``）。

``_patch_leaf`` 在解析期把 ``required=True 且 default=None`` 的参数归一为
``UNSET``，恢复 click 原生 usage error（exit 2）。覆盖两层：合成 app
（机制级，与 cli/main.py 同款 root group class）+ 真实 root CLI 路径
（惰性 get_command patch 与生产一致）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from cli.subcommand_format import make_group_cls

runner = CliRunner()


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    map_dir = tmp_path / ".map"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "api_url": "http://localhost:8001",
                "project_key": "t",
                "default_persona": "host",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _synthetic_app() -> typer.Typer:
    """make_group_cls 最小复现：与 cli/main.py 的 root group class 同款。"""
    app = typer.Typer(cls=make_group_cls(lambda raw: None))
    sub = typer.Typer()

    @sub.command("go")
    def go(
        name: str = typer.Option(..., "--name"),
        opt: str = typer.Option(None, "--opt"),
    ) -> None:
        typer.echo(f"ok name={name} opt={opt}")

    app.add_typer(sub, name="sub")
    return app


class TestSyntheticRequiredGuard:
    def test_missing_required_is_usage_error(self) -> None:
        result = runner.invoke(_synthetic_app(), ["sub", "go"])
        assert result.exit_code == 2, result.output
        assert "--name" in result.output

    def test_missing_required_not_silently_none(self) -> None:
        # 修复前：exit 0 + 回调收到 name=None（必填校验被跳过）
        result = runner.invoke(_synthetic_app(), ["sub", "go"])
        assert result.exit_code == 2
        assert "ok name=None" not in result.output

    def test_provided_required_still_works(self) -> None:
        result = runner.invoke(_synthetic_app(), ["sub", "go", "--name", "x"])
        assert result.exit_code == 0, result.output
        # 可选项未传仍回退 None（原有语义不变）
        assert "ok name=x opt=None" in result.output


class TestRootCliRequiredGuard:
    """真实 map CLI：经 root group 惰性 patch（与生产 ``map ...`` 路径一致）。"""

    def test_fs_topic_create_missing_title(self, workspace: Path) -> None:
        from cli.main import app

        result = runner.invoke(app, ["fs", "topic-create"])
        assert result.exit_code == 2, result.output
        assert "--title" in result.output

    def test_topic_comment_missing_id(self, workspace: Path) -> None:
        from cli.main import app

        result = runner.invoke(app, ["topic", "comment", "--body", "x"])
        assert result.exit_code == 2, result.output
        assert "--id" in result.output

    def test_happy_path_unaffected(self, workspace: Path) -> None:
        from cli.main import app

        result = runner.invoke(app, ["fs", "topic-create", "--title", "Guard Ok"])
        assert result.exit_code == 0, result.output
        assert (workspace / "map" / "topics" / "guard-ok" / "index.md").is_file()
