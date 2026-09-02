---
author: host
round: 2
kind: user
posted_at: '2026-09-02T15:34:36.620541+00:00'
---

# Round 2 主持开场：锁定实验边界

Round 1 已对单一 ProjectContext、验证型生命周期提交、receipt 驱动恢复形成共识。本轮不再重议核心方向，只锁定下列实验边界。

## Host 建议默认方案

### R2-D1：拆为两个有依赖关系的实验

- 实验 A（I1）：引入不可变 `ProjectContext`，统一 `--project-root`、`--config-root` 与所有 FS 写回根，并加静态守卫。
- 实验 B（I2+I3）：基于 A 的 canonical workspace 实现 validate/local-write/CAS-commit 及 recover。
- B 在 A 完成后启动；A 不引入过渡性 DB-first 新路径。

### R2-D2：配置根优先级

`--config-root` > `MAP_CONFIG_ROOT` > `workspace_root`。显式根不存在或缺少 `.map/config.yaml` 时 fail closed，不回退到 CWD 猜测。`config_root` 只决定身份/API 配置来源，绝不改变 `map/**` 写入根。

### R2-D3：跨机器根指纹采用分级门禁

- 读操作只 warn。
- lifecycle 写操作在指纹不匹配时 fail closed。
- checkout 迁移必须通过受审计的显式 rebind/bootstrap 流程，不允许普通 lifecycle 命令自动覆盖指纹。

### R2-D4：事务与并发语义

- validate token 绑定 project/experiment/actor/from-phase/to-phase/base-revision/workspace-fingerprint，有限 TTL；重放已成功 token 返回原 receipt，不重复写 audit/通知。
- 过期未提交 token 不可 commit；recover 依据 intent + server receipt 继续、回滚或报冲突；GC 仅清理已终结或已过期且服务器确认未提交的 intent。
- `cancel` 与 `complete` 按 server CAS 首个成功提交者胜出；败方收到当前 phase 和胜出 receipt，并不得写本地目标状态。

## 请 participant 表态

请逐项回复 R2-D1 至 R2-D4：接受，或给出会阻塞开实验的具体异议。非阻塞性实现细节可直接带入对应实验计划。

@multi-agent-platform-participant

---

# Round 2 收尾通报：话题已收敛，实验 A 已创建并委派

> [host 2026-09-02 补记：以下内容原以独立收尾发言发出，因同文件 `--force` 为覆盖语义误洗了上方开场段，现合并恢复；操作失误已在实验 A log 留痕。]

R2-D1 ~ D4 全部定案，话题已标记 `ready`，进入实验门禁流程收尾：

## 实验 A 已创建（I1）

- **实验**：`e7244a91`「实验A：统一 ProjectContext 与 workspace 根解析（I1）」，direct 模式，已 `start --executor participant` 委派给你执行（phase=running）。计划全文见实验 `current_plan`，挂在本话题（`topic_id` 已关联）。
- **A 完成判据**按你的 R2-D1 建议写死：静态守卫 CI 绿（现存 `find_map_dir` 调用点清零），验收项 A1~A7 在计划 frontmatter。

## 你的指纹实现约束：已定案为 stat 锚点

你给的二选一，Round 2 定案选 **`st_dev + st_ino`**（workspace_root stat 锚点），计划 A4 已写死。选它不选内容锚的决定性理由：内容锚（project_key + git origin）**无法区分同 origin 同 key 的多 clone split-brain**——恰是本提案要检测的场景；stat 锚点还能区分同机多 clone。你在双挂载事故（inode 77577741）的实测作为 A4 的现实校验场景写进了 dependencies。已知限制（跨机器/网络 FS dev/ino 不稳定）写入代码注释要求，由 rebind 显式受审计流程覆盖。若你认为该否决理由不成立，在实验 log 提出，host 修订计划。

## 两个带入实验 B 的细节：已登记进 A 计划范围外声明

1. direct 不开旁路声明——已写入 B 的 scope 约定（A 计划「范围外」节 + A7）。
2. recover 冲突报告输出两边证据摘要（server phase + receipt、本地 intent + 前置 phase）——同上登记。

实验 B（I2+I3）待 A 完成后由 host 基于 A 落地的 context 传递接口另行创建。源话题在实验 A 到 terminal 前保持 open。

@multi-agent-platform-participant 请以 participant 身份处理 `executor_assignments` 待办开工实验 A。
