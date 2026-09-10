"""mypy 门禁清单漂移守卫：pyproject overrides ↔ CI 命令行。

背景（本次修复的 bug 类）：``[tool.mypy]`` 配了 ``follow_imports = "silent"``，
mypy 因此只报**命令行显式传入**的文件；被 import 跟随进来的存量模块仍参与
类型解析，但报错不阻塞门禁（否则「按目录渐进 strict」会被 sdk/ 等存量模块的
类型错误牵连，实测牵出 28 个报错）。

这个设计让「已纳入门禁的严格模块」出现两份清单：

1. ``pyproject.toml`` 的 ``[[tool.mypy.overrides]]``：声明哪些模块是 strict 的
   （mypy 的 per-module 配置面）
2. ``.github/workflows/ci.yml`` 的 Mypy 步骤 ``run:``：实际传给 mypy 的文件

两份清单漂移 = 隐蔽的假绿：模块被声明为 strict，却没列进 CI 命令行，
它的类型错误会被 ``follow_imports = "silent"`` 静默吞掉。本测试断言两者
一一对应，任一处漂移即红。

注意：``[[tool.mypy.overrides]]`` 里还有另外两个 section（``server.domain.schemas``
/ ``server.domain.models`` 的 attr-defined 关闭、``alembic.*`` 的
ignore_missing_imports），它们不是 strict 门禁清单，按「是否带
``disallow_any_generics``」筛出目标 section，避免误判。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# 一个 [[tool.mypy.overrides]] section 的正文（到下一个 section 头为止）。
_OVERRIDE_RE = re.compile(
    r"^\[\[tool\.mypy\.overrides\]\]\s*$(?P<body>.*?)(?=^\[|\Z)",
    re.MULTILINE | re.DOTALL,
)
# section 正文里的字符串字面量（module 列表项）。
_STRING_RE = re.compile(r'"([^"]+)"')

# 把 strict 门禁 section 和其他 overrides 区分开的标志 flag。
_STRICT_MARKER = "disallow_any_generics"


def _promoted_modules() -> list[str]:
    """pyproject 里声明为 strict 的模块清单（受保护面 / 单一真相源）。"""
    text = PYPROJECT.read_text(encoding="utf-8")
    found: list[str] = []
    for match in _OVERRIDE_RE.finditer(text):
        body = match.group("body")
        if _STRICT_MARKER not in body:
            continue
        module_block = re.search(r"^module\s*=\s*\[(?P<items>.*?)\]", body, re.MULTILINE | re.DOTALL)
        assert module_block is not None, "strict override 缺少 module 列表"
        found.extend(_STRING_RE.findall(module_block.group("items")))
    # 通配 pattern（alembic.* 之类）不属于门禁清单。
    modules = [m for m in found if "*" not in m]
    assert modules, "pyproject 未声明任何 strict 门禁模块"
    return sorted(modules)


def _ci_mypy_targets() -> list[str]:
    """CI Mypy 步骤实际传给 mypy 的模块清单。"""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    runs: list[str] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            name = str(step.get("name", ""))
            if name.lower().startswith("mypy"):
                runs.append(str(step["run"]))
    assert len(runs) == 1, f"期望恰好一个 Mypy 步骤，实际 {len(runs)} 个"
    tokens = runs[0].split()
    assert tokens and tokens[0] == "mypy", f"Mypy 步骤应以 `mypy` 开头：{runs[0]!r}"
    targets = []
    for token in tokens[1:]:
        assert token.endswith(".py"), f"非文件参数：{token!r}"
        targets.append(token[: -len(".py")].replace("/", "."))
    return sorted(targets)


def test_ci_mypy_targets_match_pyproject_promoted_modules() -> None:
    promoted = _promoted_modules()
    ci_targets = _ci_mypy_targets()

    missing = sorted(set(promoted) - set(ci_targets))
    extra = sorted(set(ci_targets) - set(promoted))
    assert not missing, (
        "以下模块在 pyproject 被声明为 strict，却没列进 CI 的 mypy 命令行——"
        f"因为 follow_imports=silent，它们的报错会被静默吞掉：{missing}"
    )
    assert not extra, f"以下模块在 CI 命令行里，但 pyproject 未声明为 strict：{extra}"
    assert ci_targets == promoted


def test_promoted_modules_exist_on_disk() -> None:
    for module in _promoted_modules():
        path = REPO_ROOT / (module.replace(".", "/") + ".py")
        assert path.is_file(), f"pyproject 声明的 strict 模块不存在：{module} → {path}"
