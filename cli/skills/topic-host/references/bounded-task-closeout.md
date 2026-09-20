# 小任务执行、审阅与有限返修

适用于范围已明确的文档修订、代码修复等交付任务。研究实验继续使用原有实验生命周期和评审门禁；这里不产生科学 PASS，也不替代实验结果审批。

## 一次给清楚任务

host 在现有话题中发布一份任务说明，包含：目标、允许修改的文件/目录、禁止扩大的范围、交付文件、验收命令、executor/reviewer、最多返修次数（默认 2）。用户已授权调用配置好的 Agent 时沿用方式 C，不重复确认工作方式。

只为独立交付物创建行动项，沿用真实 owner；审阅发现的 R1/R2/R3 是这份交付物的问题编号，不为每个措辞修正再建话题、实验或行动项。未验收的交付物保持 open，完成证据由 owner 通过 CLI 提交。

## 方式 C 的连续执行顺序

1. 首次调用前运行 `map runtime check --persona participant` 与 `map runtime check --persona reviewer`。这是本地配置检查，不证明网关可用。需要共用已有配置时，两条命令以及后续 invoke 都带同一个 `--env-file <path>`，或在调用进程设置 `MAP_CLAUDE_ENV_FILE`；不要复制/打印 token，也不要临时重写 Agent 启动器。
2. host 调用 executor：prompt 首段放 `topic=<slug>; task=<任务文件>; skill=topic-participant`，正文要求完成任务、运行适用检查、写交付说明，并只回报交付路径、验证结果和阻塞项。已有任务说明直接引用，不逐次复制长背景。
3. executor 返回后，host 核对退出码、JSON `status` 和交付物存在，再调用独立 reviewer。首次审阅可用 `--new-session` 隔离旧任务；后续返修复审续用该 reviewer 会话。reviewer 直接检查文件/差异/测试证据，写任务约定的审阅文件，结论为 `PASS / NEEDS_CHANGES / BLOCKED`，问题用稳定编号并给具体位置和验收条件。
4. `NEEDS_CHANGES` 且问题仍在原范围内：host 把审阅文件路径交给原 executor，再让原 reviewer 核对修正；按相同顺序继续，最多 2 次返修。无需再让用户逐项同意已授权的小修正。host 不代写 reviewer 的结论。
5. reviewer `PASS` 后，host 核实实际交付、必要检查和 MAP 行动项状态，给最终验收；由各 owner 提交完成证据，再发布 Round Summary 并按既有门禁关闭话题。Agent 进程成功退出仅表示调用完成，不表示任务验收通过。
6. 调用失败、证据缺失、范围冲突或返修到限：停止自动转交，保留未完成项，由 host 判断缺口。不要盲重试、改成 PASS、取消仍需完成的要求或把失败藏在总结里。

长 prompt 用文件，避免 shell 转义；调用形式：

```bash
map --persona host host invoke --persona participant --prompt-file ./task-prompt.md --json --follow
map --persona host host invoke --persona reviewer --prompt-file ./review-prompt.md --json --follow
```

`--json` 和文本模式均只在 `status=ok` 时返回 0；`error/no_response/timeout` 返回非零。配置错误先查 `runtime check` 的 source、effort 和 issues；修正配置后再决定是否重试，保留既有模型选择。

## 小修正的证据如何留下

- 任务开始时约定工作区中的交付说明和审阅文件（位于 `map/**` 之外），每次修订保留原问题编号、旧结论和复审结果，记录被审文件版本/差异或哈希。它们是交付证据，MAP 状态仍以 CLI 查询结果为准。
- 连续 invoke 的中间结果先写这些证据文件并回报 host；最终由各 persona 用 `map topic comment --topic <slug> --file <md>` 一次发布本轮结论。不要为了“收到”或“已修改两个词”消耗一个不可覆盖的发言槽。
- 普通 `round<N>-<persona>.md` 发言不可覆盖；`--force` 只支持 Round Summary。若已发布的发言确需正式修正，host 按现有门禁推进下一轮，不能手改/删除旧评论绕过审计。
- waker 模式仍按 wake 协议及时清理本轮义务；不要把方式 C 的连续返修约定用于无限推迟 `pending_round_acks`。

这套推进由 host Agent 遵循 Skill 执行；单次 `host invoke` 不会自行调度下一位 Agent。没有运行中的 host/waker 时，不宣称后台会自动接续。
