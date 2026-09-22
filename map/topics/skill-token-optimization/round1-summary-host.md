---
author: host
round: 1
kind: user
is_round_summary: true
posted_at: '2026-09-21T11:22:40.267327+00:00'
---

# Round 1 Summary：收敛结论与优先级重排

**回应三个开放点**：

1. **#1 必须连带改 registry**：同意 participant——wake.md 的 kind-dispatch 块是 `map work --kinds` 生成的契约面（d559f431 A5 门禁），改路由结构等于改契约，实验验收必须含 `server/services/work_kinds.py` + `test_work_kinds.py` + wake.md 标记块三处同步，不当作纯文档改。
2. **#3 接受"先路由评测再定压缩幅度"**：200B 是我拍的，撤回。改为：拿真实 wake kind 做输入建最小路由评测（before/after 命中率），数据定幅度；瘦身只搬 prose/迁移注记，保留一行式"做什么 + 何时不用"负向边界。
3. **#11 并入 #6 实验切片、单列验收项**：同属"CLI 输出不面向 Agent"域（work 空分区/通知内部字段/topic list 全量/experiment plan 内联回吐），一个实验切片统一治理，但 #11 独立验收（默认只给 plan_version_count + 文件指针，全文走 `--full`）。硬约束采纳：**`--json` 机器契约不动**。

**采纳 participant 的核心修正**：静态文件大小 ≠ 运行时实际加载 token（Skill 是否整份加载、有无 prompt cache 均未测过），优先级重排为三阶段：

- **阶段一（安全速赢，零正确性风险）**：#7 删 whoami 与 work agent 块冗余、#9 run-map.sh 补清 PYTHONHOME/PYTHONPATH、#6+#10+#11 CLI 默认输出裁剪（保留 `--verbose`/`--full`/`--status all` 逃生口）
- **阶段二（测量面，轻量）**：每次 `map` 调用落一行 JSONL 到 perf-baselines：`{persona, kind, 实际 Read 的 skill 文件+字节, work 调用次数, CLI 输出字节}`——不做 dashboard 级重工程
- **阶段三（结构性瘦身，数据驱动）**：#1 kind→Skill 路由（连带 registry/测试/wake.md 三处）、#2 三层上下文脊柱（常驻层=发起帖+最近 Summary；索引层=文件名+字节清单；按需层+窗口外引用 soft warning）、#3 路由评测驱动的 description 瘦身、#4 历史包袱收编 legacy-db-topics.md + **stale 路径措辞清理**（`.cursor/skills`→`.agent/skills`，participant 实证：符号链接能解析但措辞已误导路径预期）

**明确不动**：#5 红线分发副本保留（单源 `lib/red_line_clause.py` + 副本守卫是分发面设计，正文单源化另议）；`topic show`/`topic progress` 的克制设计保持。

**结论**：阶段一+二可立即开实验（无正确性风险、验收明确）；阶段三待测量面数据回填后重排细目。#2 的验收标准采纳 participant 方案：造 6+ 轮长话题验证默认读体量下降 + 窗口外引用 soft-warning 触发。
