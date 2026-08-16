# M56 实验结果（topic 命令路由统一，v0.12 收官）

## Summary

M56 全部五个子项（A 六命令接入三态路由 / B fs 目标降级二分 / C help 文本统一 / D cancel CLI 封装 / E 路由回归测试）按 plan v2 交付。`map topic` 命令族 13 个带 `--id` 的命令中，10 个为三态路由（文案逐字一致），migrate / archive 两个 DB-only 命令在 help 明确标注，E7「两套 id 规则」收敛完成。顺带交付 M55 移交项 `map experiment cancel`。wire 验证阶段发现并根因修复一个存量 server 缺陷（裸 StateMachineError 无异常映射 → 非法状态转换全线 500），补 API 级回归锁。v0.12 三里程碑（M54/M55/M56）至此全部完成。

## 实施 log

见 log-r1.md（交付清单逐子项、wire 实测记录表、门禁证据、工程纪律记录）。

## 风险与遗留

- 六命令 `--id` 类型 `uuid.UUID → str` 为 breaking change：非法 uuid 字符串不再被 typer 拦截而进路由层，slug 分支报 not found 且含修复引导（plan 风险节既定行为，测试锁定）。
- 8001 线上进程为 M55 提交前旧代码：E8 slim create 与本实验的双重 cancel 422 均需 server 例行重启生效（CLI 侧改动即时生效，fs 路由纯本地零依赖）。
- 延后 v0.13（开放问题 2 既定）：`experiment log --log-file-path` 瘦身形态（需 server schema + DB 列 + 校验三处改动）。
- 命令分类守卫（dry-run 写命令白名单）已同步登记 `experiment cancel`。

## Acceptance 逐条核验

1. **六命令三形态路由** ✅ — `--id` 收 DB UUID / FS uuid5 / slug + `--storage` 覆盖；复用 `_resolve_topic_ref` 零新解析逻辑（TestSixCommandDbBranch 6 用例 + 8001 实弹 read/dismiss）。
2. **fs 降级分类** ✅ — 通知投影类 no-op exit 0、状态变迁类 exit 2 + 分别指向 close（close_reason）/ round 文件 / index.md（TestSixCommandFsDegradation 7 用例 + 5 条实弹）。
3. **migrate / archive DB-only 标注** ✅ — archive help 补标注，migrate 原有；help 一致性断言例外清单为「除 migrate / archive」（TestIdHelpConsistency）。
4. **cancel 封装** ✅ — `--id` 支持短 id 前缀（`_rid`）；状态机拒绝透传不吞（stub 信封断言 + wire：已 cancelled 422 terminal phase / 未知 404）；写命令白名单已登记。
5. **help 一致 + pytest + fast-gate** ✅ — 10 命令文案逐字一致 + 分组闭合守卫；test_topic_routing 33 passed（18 新）；fast-gate 473 passed / 2 skipped；slow 实验域 36 passed；ruff 全绿。
