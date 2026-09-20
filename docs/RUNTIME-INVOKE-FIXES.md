# MAP 调用与小任务收尾改进

日期：2026-09-20。

## 问题与改动

实际使用中，跨项目调用需要临时启动器补入 Claude 配置，Agent 运行错误在 `host invoke --json` 下仍可能返回退出码 0，小型文档任务也需要 host 多次转述问题、补行动项与轮次。

本次修复：

1. `host invoke` 支持 `--env-file` 和 `--effort`；所有 Claude SDK 入口支持 `MAP_CLAUDE_ENV_FILE`，未显式选择时读取项目 `.map/.claude-env`。选中的文件对凭据、端点和模型别名为权威，缺少的键不会混入 shell 的旧值；显式文件不可读时失败。没有配置文件时保留原有进程环境和 shell rc 兼容路径。
2. 配置文件中的 `MAP_RUNTIME_EFFORT` / `CLAUDE_CODE_EFFORT_LEVEL` 生效；显式调用参数、进程变量可以覆盖，默认 `medium`。模型仍使用既有配置。
3. `map runtime check` 显示配置来源、模型、effort、凭据键名、SDK 是否安装及修复提示，不打印 token 或端点值，不调用模型、不创建会话。`configured` 只证明本地配置存在。
4. `host invoke` 的文本、局部 `--json` 和全局 `--json` 均只在 `status=ok` 时退出 0；`error/no_response/timeout` 均非零。SDK 错误详情传到 JSON 的 `error` 字段，连接建立也受调用超时限制。waker 的无效共享配置路径返回清晰启动错误。
5. 分发 Skill 增加 executor → 独立 reviewer → 最多两次返修 → host 验收的连续执行约定。复用原任务说明、行动项和问题编号，中间修正记录在任务约定的证据文件，最终发布本轮结论。普通发言仍不可覆盖，owner 仍须亲自完成行动项，实验门禁保留。

配置优先级详见 [运行配置说明](MAP-SIMPLE-WAKER.md#claude-sdk-credentials-for-resumed-agents-mapclaude-env)。小任务流程见 [收尾约定](../.cursor/skills/topic-host/references/bounded-task-closeout.md)。源 Skill 与 `cli/skills/` 分发副本已同步。

## 使用

```bash
# 共享已有文件：路径由调用方明确给出，不复制凭据
map runtime check --persona participant --env-file /path/to/shared/.claude-env
map --persona host host invoke --persona participant \
  --env-file /path/to/shared/.claude-env --prompt-file ./task-prompt.md --json

# 同一调用环境持续使用时，可设置 MAP_CLAUDE_ENV_FILE
# 有网关兼容性要求时，可显式加 --effort medium
```

host 按 Skill 处理审阅与返修；单次 invoke 不会自行调度下一位 Agent。已有项目的 Skill 可通过 `map skill install` 更新。调用成功也不等于交付验收通过，须核对实际文件、验证结果和 MAP 状态。

## 验证

- 默认单元套件：`conda run -n llm-pipe-hub python -m pytest -q -n 4`，**2682 passed, 2 skipped**。遵循仓库默认 marker，未运行 slow/integration/claude_cli 套件。
- 随后补充 waker 配置错误提示，相关配置、waker、编排、Skill 和 CLI 命令分类回归：**157 passed**。
- 修改的 Python 文件通过 Ruff，`git diff --check` 通过；Skill 副本和命令引用守卫通过。
- 使用配置好的 MAP reviewer，经修改后的原生入口和原有模型做了一次真实只读源码审阅，调用 `status=ok`、退出 0，审阅结论 **PASS**。其后补充的 waker 提示由上述定向测试验证。
- reviewer 提醒空值覆盖与 unset 语义不同；SDK 实际按 `{**os.environ, **options.env}` 合并环境。保留空值覆盖以抑制父进程残留，不能简单省略未定义键，否则会重新继承旧凭据。实际网关调用通过；本次未另做缓存 OAuth 登录兼容测试。

运行环境还发现版本混用：共享 `llm-pipe-hub` 环境原来安装 `0.16.3`，本地源码为 `0.17.0`，导致直接 `map` 仍加载旧代码，测试中的 SDK schema 也陈旧。已用 `pip install --no-deps -e <MAP源码目录>` 将该环境接到本地源码；从研究项目目录验证 CLI 和 SDK 均加载源码路径及 `0.17.0`。首轮测试中的版本/schema 失败随之消失，新增命令的只读分类遗漏也已补齐。该环境另安装了项目 dev 依赖 `pytest-xdist` 以运行并行测试。

本次未修改研究代码、论文结论或科学实验，未重启平台服务，未发布软件包，未提交或推送 Git。这里的 PASS 仅指软件改动审阅与上述验证范围。
