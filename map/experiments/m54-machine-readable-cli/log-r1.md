# M54 log

## Round 1：M54A 子命令级 `--format` 实现完成（2026-08-15）

### 交付内容

- 新增 `cli/subcommand_format.py`：根 `typer.Typer` 挂自定义 `TyperGroup` 子类（`cls=make_group_cls(hook)`），click 解析时经 `Group.get_command` 拦截，对**每个叶子命令**注入长格式 `--format` 选项并包装 callback；对嵌套子组（`experiment` → `review`/`plan`/`lock`）递归实例级补丁。全部幂等（命令对象打标），改动集中一处、不触碰 16 个命令模块
- `cli/main.py`：`_apply_sub_format` 解析钩子——复用全局路径的校验 / legacy 别名告警 / N=2 硬切换（`_apply_n2_hard_cutover`），在叶子 callback 内、全局回调之后执行，**子命令级显式值直接胜出**；同时盖写全局 `--format` / `--json` / `MAP_CLI_FORMAT` 时向 stderr 输出 override 告警
- 仅注入长格式 `--format`；全局 `-o` 短写法保持根级独占，避免与叶子命令未来短旗标冲突（PRD 风险表既定口径）

### 验收证据（对齐 PRD M54 验收条目）

1. **E1 原始复现命令转绿**：`map --persona host experiment review list --format json --id f4ef8cb2-…` 由 `No such option: --format` 变为正常输出 JSON 信封（`ok:true`）——正是 Round 0 取数踩坑的那条命令，嵌套子组位生效
2. **两处写法逐字节一致**：`map experiment show --format json --id X` 与 `map --format json experiment show --id X` 的 stdout/stderr `diff` 为空
3. **help 可见**：任意叶子命令 `--help`（含嵌套 `experiment review list --help`）列出注入的 `--format` 及帮助文案
4. **优先级**：子命令级 > 全局 `--format` / `--json` > `MAP_CLI_FORMAT` > 默认 yaml；冲突时 stderr 告警（单测锁定，`format_source` 记为 `explicit --format (subcommand)`）
5. **门禁**：默认 pytest gate 415 passed / 2 skipped（新增 `tests/test_cli_subcommand_format.py` 9 例，已入 fast-gate 白名单）；`ruff check server cli sdk scripts tests` 清零
6. **兼容回归**：`tests/test_cli_format_priority.py`（13 例）与 `tests/cli/test_compat.py`（含 main.py 尺寸护栏，现 ~1.5k 行 < 1600 上限）全绿

### 设计取舍记录

- 选择「构建期命令树注入」而非逐命令加参数：改动集中、未来新增命令自动获得，且 CliRunner / 真实入口两条路径（`app()` 与 `get_command(app)`）行为一致
- 防御性跳过已自带 `--format` 的命令（click 会因重名在解析期报错），为将来某命令需要语义不同的 `--format` 留出口

### 状态

- M54A ✅ 完成；M54B（短 id DB 前缀解析）、M54C（JSON 输出结构对齐 + schema 文档）待做
