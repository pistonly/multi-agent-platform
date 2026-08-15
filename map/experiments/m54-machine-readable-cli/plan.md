---
title: "M54 机器可读输出贯通（v0.12）"
acceptance:
  - "map experiment list --format json 与 map --format json experiment list 输出等价，子命令级与全局两处用法均可执行，compat 测试锁定"
  - "experiment 族 --id 接受 8 位短 id 前缀：唯一前缀命中即放行；多义时报错并列出候选完整 UUID；无命中报错提示完整格式"
  - "experiment show / logs 的 JSON 输出字段名与 REST API / map_types schema 一致（uuid 完整 36 位、时间为 ISO8601），可被 map_types 直接反序列化"
  - "host persona 仅凭 CLI 完成实验查询全流程（list 拿短 id → show 拿完整 uuid），全程无 SQLite 直查与 curl"
  - "短 id 解析 helper 评估泛化到 topic / agent 族：若接入成本低则本里程碑一并落地，否则在 PRD 记录延后决策与理由"
  - "新增 pytest 覆盖（子命令级 format、短 id 三分支、JSON schema 对齐），现有 406 测试不回归"
evidence_keys:
  - "pytest 新增 m54 测试全绿"
  - "pytest 快速门控全量回归通过"
  - "CLI 实测：map experiment list --format json | jq 解析成功 + map experiment show --id <短id> 返回完整 uuid"
dependencies:
  - "v0.12 提案已过 reviewer 证据核对（话题 v012-ergonomics-review，6648885）"
  - "M51 _resolve_topic_ref 路由 helper 已落地（70f738b）"
---

# M54 机器可读输出贯通

## 目标

按 docs/prd/v0.12.md §M54 收口 agent 侧可编程性：让 CLI 输出可被机器直接消费、平台发出的标识被平台自身解析。消除「agent 被迫直查 SQLite 拿 UUID」这类绕过平台的信号。

## 改动范围

| 子项 | 内容 |
|------|------|
| M54A 子命令级 `--format` | `cli/main.py` 的全局 `--format`（`_cli_options`，默认 yaml）下沉：experiment 族各子命令增加本地 `--format` 选项，通过 typer context 读取全局值，子命令级显式传入优先；两种调用位置行为等价，进入 tests/cli/test_compat.py 锁定 |
| M54B 短 id 解析 | experiment 族 `--id` 从 `uuid.UUID` 改为 `str`，新增 `_resolve_experiment_ref`：36 位 uuid 直接放行；8 位短 id 前缀唯一匹配放行；多义报错列候选；无命中报错并提示可用格式。**匹配走 DB 层前缀查询**（`id LIKE '<prefix>%'`，见 v2 修订 r1），不整表拉取。helper 设计为通用形态（`ref + 匹配器`），供 topic / agent 族评估复用 |
| M54C JSON 对齐 | `--format json` 输出走 map_types schema 序列化（uuid 完整 36 位、ISO8601 时间、字段名与 REST 响应一致），新增护栏测试防止 CLI 输出与 schema 漂移 |

## 实现顺序

1. M54A 子命令级 format + compat 测试（纯 CLI 层，风险最低，先解锁 agent 最常用的 list 场景）
2. M54B 短 id 解析 + 三分支测试（helper 泛化评估在此时点做决策）
3. M54C JSON schema 对齐 + 护栏测试
4. 全量回归 + 端到端实测（验收第 4 条流程）

## 风险与对策

- 短 id 前缀碰撞随实验数量增长：多义分支报错并列出候选完整 UUID，agent 可二次精确；8 位十六进制在本项目规模（<100 实验）碰撞概率可忽略
- `--id` 类型从 UUID 改 str 可能放行畸形输入：解析函数先做格式校验（36 位 uuid regex / 8 位 hex），不匹配直接报错不查库
- 全局与子命令级 format 并存：子命令级显式值优先，静默不一致进 compat 测试
- JSON 对齐改动可能触及现有 yaml 渲染路径：M54C 只动 json 分支，yaml 默认输出不变

## 验收演示脚本

```bash
map --persona host experiment list --format json   # E1 场景，应输出 JSON 数组
map --persona host experiment show --id <8位短id>  # E2 场景，应输出完整 uuid
```

## v2 修订记录（响应评审 r1 三条建议）

- **r1（建议1）短 id 匹配范围**：前缀匹配改为 DB 层 `WHERE id LIKE '<prefix>%'` 查询（或 repo 层等价实现），不整表拉取；无 DB 访问权的纯本地场景退化为有限 page_size 内匹配并明示
- **r2（建议2）E8 纳入**：host 采纳——`--plan-file-path` 瘦身模式被 frontmatter 门禁误伤（服务端只校验 `content_md`）确认为独立缺陷，作为 E8 补进 v0.12 证据清单（PRD 单独修订）；修复归属 M55（错误信封一并处理 file_path 形态），M54 不扩大范围
- **r3（建议3）log 门禁**：采纳——执行日志在首次进入 running 后立即补记（含 review 阶段的创建踩坑：瘦身 422 与 E1 复现记录），experiment-host Skill 的日志纪律条目由 M55 落地
