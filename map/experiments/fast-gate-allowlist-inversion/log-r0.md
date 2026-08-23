# log-r0 立项记录（draft/review 阶段旁路落盘，approve 后经 CLI 补记）

> 状态机限制：review 阶段禁写 `experiment log`（server/services/log_service.py:33 白名单）——本文件即话题 experiment-log-phase-restriction 所述补偿流程的活例，approve/start 后第一时间补记。

## 时间线（2026-08-23 UTC）

- 06:45 Round 2 Summary 落盘（round2-host.md，is_round_summary），吸收 participant 两修正为 D2/D3
- 06:47:53 participant round2 表态：无异议，同意推进 ready；补充 A1 基线测量顺带输出 per-module 耗时 top-N（一次测量喂 A1+A4，已纳入 plan）
- 06:48 四门核对通过 → `topic advance-round --ready`（committed）
- 06:49 **踩坑**：先 `topic close` 后 `experiment create` → `409: Cannot create experiment on a closed topic`。host-checklist §3 的 close→create 顺序描述与 server 409 门禁相反（实验须建在话题关闭前）；按 FS reopen 约定改回 index status 并注明原因
- 06:50:53 `experiment create` 成功（eb291c4b，draft，plan_file_path 瘦身模式）
- 06:51:05 `experiment submit-review` 成功（phase=review）
- 06:51 `topic close` 以正确顺序重做（close_note 含 linked_experiment: eb291c4b）

## 待办（approve 后）

1. 补记本日志到 `experiment log`
2. 审阅中发现 host-checklist §3 顺序描述错误已记入 cli-param-consistency 话题范围（P3 文档核正）
