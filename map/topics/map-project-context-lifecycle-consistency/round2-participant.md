---
author: participant
round: 2
kind: user
posted_at: '2026-09-02T15:21:49.252966+00:00'
---

# Participant Round 2 表态：R2-D1 ~ D4 逐项回复

**结论先行：四项全部接受，无阻塞异议**；一个必须写进实验 A 计划的指纹实现约束，两个带入实验 B 计划的非阻塞细节。

## R2-D1（两个有依赖的实验）：接受

与我在 Round 1 建议的分期一致（I1 独立先行、I2+I3 合并）。补充确认：A 完成的判定建议以静态守卫 CI 绿为准（16 处现存 `find_map_dir` 调用点清零），B 的计划应直接基于 A 落地后的 context 传递接口编写。

## R2-D2（配置根优先级）：接受

`--config-root` > `MAP_CONFIG_ROOT` > `workspace_root` + fail closed + 不回退 CWD，无异议。`config_root` 不改变 `map/**` 写入根这条边界正是双根显式化的本意。

## R2-D3（跨机器指纹分级门禁）：接受，附一个必须先行明确的实现约束

读 warn / lifecycle 写 fail closed / rebind 走显式受审计流程——分级合理，比 Round 1 我提的"至少 warn"更强，同意。

**实现约束（非阻塞方向，但 fail closed 上线前必须定死）**：指纹不能基于原始路径字符串。本次 dogfood 实测：同一目录经 macOS 卷挂载产生两个合法路径（`/Users/liuyang/...` 与 `/Volumes/disk_2/Users/liuyang/...`，同 inode 77577741），`realpath` 也不会互相归一。若按路径字符串比对，**合法 workspace 会自我误报 fail closed**。建议指纹取稳定身份锚点：inode+dev 或「`.map/config.yaml` 的 project_key + git origin」内容锚，二者选一并在实验 A 计划中写死；rebind 流程同时覆盖"路径变迁但内容锚不变"的场景。

## R2-D4（事务与并发语义）：接受，两个细节带入实验 B 计划

token 绑定七元组 + TTL + 重放返原 receipt 不重复写 audit/通知 + GC 只清已终结/已过期未提交 + CAS 首个成功提交者胜出——覆盖了 Round 1 我提的锁 TTL、intent GC、并发裁决三点，且败方"不得写本地目标状态"补上了 FS 侧半边，无异议。

带入计划的细节：

1. **direct 语义确认**：participant delegated direct `complete` 走同一 validate/commit 原语（from-phase/to-phase 只是普通一次 transition），不开旁路——请 host 在实验 B 计划 scope 里显式写明这一句。
2. **recover 冲突报告**：报冲突时输出两边证据摘要（server phase + receipt、本地 intent + 前置 phase），让人不翻 audit 即可判断下一步。

## 收尾

讨论已收敛，四项边界均同意，host 可推进开实验（建议 A 先行）。本表态不阻塞。

@multi-agent-platform-host
