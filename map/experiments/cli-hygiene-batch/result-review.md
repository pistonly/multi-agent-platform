# experiment 域 CLI 卫生三合一 — 结果审批（accept）

## 结论

**通过**。A1–A6 与测试面逐条满足，I1–I4 代码 commit（`4ef1ca2`/`f4c0316`/`325cce5`/`fce538d`）+ 事实源（`a82274a`/`1ebd65b`）全部在 HEAD；v1 评审针对 A3 的修正点已落实，无阻塞项。

## 验收逐条核验

| 验收 | 判定 | 证据 |
|------|------|------|
| A1 log 阶段放宽 | ✅ | HEAD `log_service._validate_log_phase`：白名单放宽为**除 cancelled 终态外全部阶段**（draft/review/approved 加入；超集于 plan「加三态」，贴合定稿 T1-P1「log 幂等追加无阶段排他性」论证，cancelled 保留唯一拒绝边界）。单测：draft→review→approved 三阶段 201 连续时间线 + cancelled 422；本实验自 I1 起执行日志直接写平台（自举验证放宽生效） |
| A2 log CLI 一行式报错 | ✅ | HEAD `experiment.py:599`：`Error: --summary requires --file or --log-file-path`，exit 2，前置校验零请求、无 pydantic 堆栈；单测 |
| A3 补偿流程退役（**v1 评审修正点**） | ✅ | HEAD SKILL.md:48 日志纪律第 8 条：FS 旁路后半句已删、前半「失败重试后补 experiment log」保留，原括号改注「白名单已放宽、阶段拒绝路径已删」；`git grep -E "log-rN|log-r0|落 FS|补记" HEAD -- .cursor/skills/` **零命中** |
| A4 --topic-id 双路由 | ✅ | HEAD `experiment.py:92` 声明 `str \| None`；`topic_id_for_slug`（179-181 行，确定性 uuid5 本地纯函数零请求）；uuid 直传不回归；单测 2（slug→uuid5 / uuid 透传） |
| A5 参数收敛 v1 | ✅ | HEAD `fs.py` 5 命令（179/250/525/558/589）双轨 `--topic, --id` 别名；`subcommand_format.py` did-you-mean 四形态（Missing parameter / extra argument / no such option difflib 近邻 / 兜底无 hint 零误伤）；三命令 help/error 核对表日志持证 |
| A6 收尾动线 | ✅ | HEAD `experiment.py:362` pre-complete 回显可粘贴 complete 命令行；complete 缺 metadata 错误前置 accepted keys + 示例 JSON；`--file`/`--log-file-path` help 各补一行场景说明 |
| 测试面 | ✅ | 实测复跑相关回归 **68 passed**（test_experiment_cli_hygiene / log_phase_whitelist / cli_subcommand_format / required_option_guard 等 21 + 剩余 5 文件 47，含 A4/A5/A6/did-you-mean 新增单测）；log 声称 86 含 accept_result_verdict；ruff 全改文件绿 |
| 文档核正（T2-P3） | ✅ | host-checklist §3 create → close 顺序修正；commands.md `--topic-id <topic-ref>` 双路由注释 + fs comment 双轨说明 |

## 关键核验点

1. **commit 收口完整**：I1–I4 四个代码 commit + 事实源归档 `a82274a` + acceptance.md `1ebd65b` 均 HEAD 祖先，窄提交与 I 粒度一一对应。
2. **A3 是我 v1 评审的 unreasonable 项对应实现**：删除边界严格落在「第 8 条后半句」、前半保留、grep 零残留三证齐备——评审修正完全落地。
3. **A1 实现为「除 cancelled 全放」而非字面「加三态」**：与定稿 T1-P1 的核心论证（日志自带 phase 快照、只增不改、幂等追加无阶段排他性）一致，cancelled 是状态机唯一终态拒绝边界，属 plan 精神的忠实实现，不构成范围越界。
4. **单测真实复跑全绿**：68 passed 为本次审批实际运行结果，非仅信 log 声称。

## 遗留（非阻塞）

- `test_cli_error_envelope` 5 挂经 git stash 验证为既有债务（stash 掉 I2 改动复现同 5 挂），与本实验无关。
- `--topic-id` slug 无存在性校验（乱 slug 生成幽灵 uuid5）——FS 确定性映射语义与 waker-heartbeat 实证路径一致，plan 仅要求「直接成功」，篡改可归 backlog。
- did-you-mean 提取依赖 vendored click 消息措辞，未知形态兜底无 hint（零误伤），已按实测两点适配。
