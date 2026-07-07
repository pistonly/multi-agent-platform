#!/usr/bin/env python
"""从 Pydantic schema 生成 TypeScript 类型（``web/src/api/types.generated.ts``）。

事实源：``sdk/python/map_types/schemas.py``（Pydantic 模型，前后端共享）。
工具链：``pydantic-to-typescript``（``pyproject.toml`` 的 dev 依赖）+ ``json2ts``
（``web`` 的 devDependency ``json-schema-to-typescript``）。

改了 schema 后重新生成并提交：

    python scripts/gen_types.py

CI（``gen-types-diff`` job）会重新跑本脚本并 ``git diff`` 校验
``types.generated.ts`` 与提交一致——不一致就 fail，防止 schema 改了却忘了 regen。
"""

from __future__ import annotations

from pathlib import Path

from pydantic2ts import generate_typescript_defs

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "web" / "src" / "api" / "types.generated.ts"
JSON2TS = ROOT / "web" / "node_modules" / ".bin" / "json2ts"


def main() -> None:
    if not JSON2TS.exists():
        raise SystemExit(
            f"json2ts not found at {JSON2TS}. "
            "Run `npm install` in web/ first (installs json-schema-to-typescript)."
        )
    generate_typescript_defs(
        module="map_types.schemas",
        output=str(OUTPUT),
        json2ts_cmd=str(JSON2TS),
    )
    print(f"generated {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
