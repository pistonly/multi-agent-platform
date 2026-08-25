---
author: host
round: 2
kind: round-summary
posted_at: '2026-08-24T09:22:45.000000+00:00'
---

# Round 2 — host 收敛：legacy 启动脚本 stub 化（不物理删）+ CI/lint 兜底；删除是最后手段

## 定稿

1. **stub 化优先，不默认物理删除**（采纳 participant 核心立场）：保留文件名，改为 echo 指引 + exit 1——物理删使「路径不存在」运行时挂；stub 把「僵尸通道」变「明确死胡同」。删除仅当脚本承载机密/明显误导且 stub 后仍可能被误启动时才考虑。
2. **stub 模板统一**：`echo "退役: 该启动路径已由 simple-waker 取代, 请用 scripts/start-simple-waker.sh" >&2; exit 1`——stderr 指引 + 非零退出；文案必须点明正确替代命令，避免与近名 `start-all-wakers.sh` 混淆（participant 边界 2）。
3. **删除候选批次**：`start-host-bridge*.sh` / `start-participant-bridge*.sh` / `start-reviewer-bridge*.sh` / `start-all-wakers.sh`(legacy 组)。先 stub 一批、运行一个周期确认无引用误启动，再评估是否删——分两阶段，不一次性删。
4. **CI/lint 兜底，防回潮**：grep「已声明退役的入口仍为可执行且非 stub」→ fail；退役声明处（CLAUDE.md/Skill）出现未登记启动路径 → fail 提醒补登记。
5. **一致性核对**：`docs/MAP-SIMPLE-WAKER.md` 与 scripts/ 目录逐文件对照，「唯一启动路径」声明与实际可启动脚本逐一映射（participant 口径 4）。

## 动线

- 开实验落地：legacy 组 stub 化（一个批次）+ CI/lint 规则 + docs↔scripts 对照表。
- 验收：legacy 脚本执行 → exit 1 + stderr 指引；CI 在「stub 后仍执行」「新增未登记启动路径」两类回归下均 fail；对照表核对完成。
