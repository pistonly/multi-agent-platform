# log-r1.md（FS fallback — review 阶段不能写 log，进入 running 后立即补记到 log.md）

## 2026-08-17T07:15:36Z — create retry 记录

### 失败原文
```
$ map --persona host experiment create \
    --title "E2E smoke: persona lifecycle 24-cell rubric" \
    --plan-file /Volumes/disk_2/Users/liuyang/Documents/quantaeye/multi-agent-platform/.map/e2e-logs/20260817T071215Z/plan.md \
    --topic-id 5ceb40d8-0878-51c7-b504-57cf6bff3b24 \
    --submit-for-review
Exit code 2
Error: plan frontmatter lint failed (acceptance, dependencies, evidence_keys, title).
```

### hint 解析
PLAN_MARKER_MISSING_FIELD: 缺 `title` / `acceptance` / `evidence_keys` / `dependencies` 四个必填 frontmatter 字段。

### 修复动作
按 M57 plan frontmatter 约定补齐四个字段（acceptance 列验收条目 / evidence_keys 列可观察证据 / dependencies 列前置条件 / title 与 --title 对齐），重跑通过：

```
$ map experiment create ... --submit-for-review
{
  "id": "c839507f-19ae-43d0-982b-26155b85322f",
  "phase": "review",
  "current_plan_version": 1,
  "topic_id": "5ceb40d8-0878-51c7-b504-57cf6bff3b24",
  "warnings": ["topic_not_ready_for_experiment"]
}
```

### 旁注：topic_not_ready_for_experiment warning
phase=review，但 source topic metadata 仍标 round1（未 advance-round / 未 mark ready）。host override 通过。后续若 reviewer 追问 topic 不 ready 的原因，host 引用本轮 R2-host.md §6「host 端视为收敛」+ participant R7 §6「不再追加」作为 host-side mark-ready 的等效语义。

> 进入 running 后立即把本节 append 到 map/experiments/e2e-lifecycle-smoke/log.md 顶部，并删本 FS fallback 文件。
