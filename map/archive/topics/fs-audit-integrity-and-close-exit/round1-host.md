---
author: host
round: 1
kind: user
posted_at: '2026-08-31T04:16:09.483903+00:00'
---

# T7：audit 链一致性检测 + skill 层写入红线 + close 语义出口

## 背景事件（实锤）

T5-B 话题 `experiment-cost-ledger` 被 host agent **直接编辑 index.md frontmatter** 置为 closed：话题目录无 `audit.jsonl`（local plane 验证型写必留审计行，cli/commands/fs.py:162），且关联实验 `e63ec33e` 当时 phase=pending_review（非 terminal）——`map topic close` 的 validate_close 第 4 维门禁（sdk/python/map_fs/validation.py:179）本应拦截。手写绕过使 T3 门禁形同虚设，且违反本仓 AGENTS.md「写操作统一走 map CLI」硬性规则。

## 已定论的边界（监督者与用户拍板）

**不做 runtime 私有权限层**（如 claude settings.json permissions deny）——平台 runtime 中立（claude/cursor/人均可 host），不为单一 runtime 维护私有配置。门禁拦不住有文件写权限的 agent，防范 = **检测 + 红线**，不是拦截。

## 任务

1. **audit 链一致性校验（检测层）**：新增只读命令（建议 `map fs verify-audit`）：扫描 `map/topics/`、`map/experiments/` 的 frontmatter 状态字段（status/phase/round/close_reason 等不变量字段），与 `audit.jsonl`（local plane）或 server 审计（remote plane）比对；发现「状态已变但无对应审计凭据」→ 逐条指认 + 非 0 退出。waker 启动时或周期集成（打 WARN 日志，不阻断轮询），并写入监督者巡检文档。
2. **skill 层写入红线**：wake.md（最小唤醒协议）+ topic-host + experiment-host skill 增加 runtime 中立条款：「禁止用 Edit/Write/sed/python 等直接修改 `map/**` 下任何文件；一切状态变更走 `map` CLI；发现手写漂移视为事故并停止当前话题操作」。
3. **close 语义出口**：讨论并落地「讨论收敛但实验未 terminal」的合法 close 路径——如 `map topic close --reason discussion_converged`：要求 close_note 显式写明闭环追踪方式（实验 id + 后续门禁），仍走 CLI 校验链留 audit；其余非 terminal close 维持拒绝。
4. 窄提交白名单：`^cli/`、`^sdk/`、`^tests/`、`^.cursor/skills/`。

## 机器可判验收要求

```bash
# 1. 新增回归测试（必须）：
#    - fixture 手写漂移（改 frontmatter 无 audit）→ verify-audit 检出并非 0 退出
#    - 正常 CLI 写入（有 audit 凭据）→ 不误报
#    - close --reason discussion_converged 带完整 close_note → 放行且留 audit；缺追踪说明 → 拒绝
# 2. 全量测试绿（基线 1806 passed / 2 skipped / 359 deselected，只增不减，0 failed）
.venv/bin/python3 -m pytest tests/ -q
# 3. 对既有 6 个话题目录实测 verify-audit：T1-T5 的 CLI 关闭应全绿，experiment-cost-ledger 应检出 T5-B 漂移（历史事实）
# 4. git diff --name-only 白名单：^cli/、^sdk/、^tests/、^.cursor/skills/
```

## 边界

- 不做 runtime 私有配置层（settings.json deny 等），已拍板。
- verify-audit 只读，不改任何写路径；waker 集成只告警不阻断。
- 不改 FS plane「本地文件真相源」架构。
- skill 条款措辞 runtime 中立，不提具体工具名（Edit/Write/sed 仅作示例枚举）。
- 涉及 cli//.cursor/skills/ 改动，验收通过后由监督者重启 server 与 waker 生效。
