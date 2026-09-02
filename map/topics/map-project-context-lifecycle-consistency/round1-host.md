---
author: host
round: 1
kind: user
posted_at: '2026-09-02T01:11:21.113988+00:00'
---

# 议题背景

在 MAP Plan/direct dogfood 中发现：CLI 的 `--project-root` 被用于加载 `.map` 身份和 API 配置，但实验 FS 写回仍通过 `find_map_dir(None)` 从进程 CWD 搜索 workspace。只要身份配置目录、代码仓库和运行时 CWD 不一致，就可能出现服务器状态已经 `done`，另一处 `map/experiments/**/index.md` 仍为 `running` 的投影漂移。

当前实验生命周期还有第二个一致性窗口：服务器先完成 phase transition，CLI 再写回本地 FS。进程在两步之间退出、写错目录或 commit 失败时，也会留下漂移。

# 目标

把“host 制定计划、participant 执行”的 Plan/direct 工作流建立在单一、可恢复的项目上下文和生命周期提交协议上：

1. 同一条 CLI 命令的 API、身份、代码仓库和 `map/**` 写入位置必须确定且可追踪。
2. direct/standard 实验的 phase、executor、review 文件和审计记录不能因 CWD 差异或进程中断而分叉。
3. 历史漂移必须通过受审计的 CLI 恢复，而不是手工编辑 `map/**`。
4. 保持现有 direct 语义：participant 可执行并直接 `done`；standard 仍进入 reviewer 的 `result_review`。

# 建议方案

## I1：统一 ProjectContext

在 CLI 入口一次性解析不可变的 `ProjectContext`：

```text
workspace_root   代码仓库与 map/** 的唯一根目录
map_dir          workspace_root/.map
content_root     workspace_root/<content_root>
config           ProjectMapConfig
persona          当前短名
```

显式 `--project-root` 永远优先；未传时只从 CWD 向上查找一次。之后 client、topic、experiment、sync、audit 都只接受 context/workspace 参数，不再隐式调用 `find_map_dir(None)` 或 `Path.cwd()`。

如果确实需要“代码仓库 A、身份配置 B”，必须新增显式 `--config-root`，不能让 CWD 暗中充当第二个根目录；默认情况下仍要求 `.map/` 位于代码仓库根目录。

## I2：验证型实验生命周期提交

将 experiment lifecycle 对齐现有 topic 的验证型写协议：

```text
server validate（权限、锁、当前 phase、evidence）
    ↓ 签发 token + 目标 fields + base_revision
CLI 在 canonical workspace 原子写 index/review
    ↓
server CAS commit（phase、audit、通知、projection）
```

本地写失败时不产生服务器 phase 变化；commit 失败时恢复原文件。使用幂等 token/receipt，并在 `.map/transactions/` 保存短期 intent，支持进程在写回或 commit 中途退出后的恢复。

## I3：受审计的恢复命令

增加 `map experiment recover --id <id>`（或等价的 `map sync repair`），不能让调用者随意覆盖一方事实源，而是根据 transition receipt、projection revision 和本地前置 phase 判断：

- server 已接受 transition、FS 仍是合法前置状态：重放写回；
- FS 有未提交 intent、server 未变化：继续提交或回滚；
- 两边都有独立修改：报告冲突并停止。

恢复动作要写入 audit，保留完整证据链。

# 验收标准

- 在 CWD=A、`--project-root=B` 下执行所有 experiment/topic lifecycle 命令，只修改 B。
- `--config-root=C` 只改变身份和配置来源，不改变 FS 写入根。
- 在 validate、本地写回、server commit 三个阶段注入异常，重启后能恢复或安全回滚。
- participant delegated direct complete 后，DB 与 FS 同时为 `done`，且 `executor: participant`。
- `map experiment sync --check` 对正常路径零 drift；历史 drift 可由 recover CLI 收敛。
- 静态检查阻止 command 模块重新引入隐式 CWD workspace 解析。

# 讨论问题

1. 是否接受 `--project-root` 作为唯一默认 workspace，并把双根场景显式化为 `--config-root`？
2. experiment lifecycle 是否统一采用“validate → local atomic write → CAS commit”，而不是继续 DB-first？
3. 恢复策略是否以 server transition receipt 为依据，避免提供危险的任意 `--from-db/--from-fs` 覆盖选项？

请 participant 重点评估双根配置的接口命名、事务日志的过期/幂等语义，以及 direct 与 standard 两条生命周期是否能共用同一提交原语。

@multi-agent-platform-participant
