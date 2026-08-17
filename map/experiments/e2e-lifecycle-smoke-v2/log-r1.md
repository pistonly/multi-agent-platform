# log-r1.md（FS fallback — review 阶段不能写 log，进入 running 后立即补记到 log.md）

## 2026-08-17T10:34:31Z — v2 create retry 记录

### 上下文
- v1 实验 c839507f-19ae-43d0-982b-26155b85322f 在 phase=review，无 reviewer 动作（actions=[] after v1 cancel 之前为 ['approve', 'withdraw']）
- orchestrator 启动 v2 计划（post-R8/R9 ack refresh 视角），plan 路径：`.map/e2e-logs/20260817T103223Z/plan.md`

### 失败原文（v2 create 首次）
```
$ map --persona host experiment create \
    --title "E2E smoke v2: ..." \
    --plan-file .../plan.md \
    --topic-id 5ceb40d8-0878-51c7-b504-57cf6bff3b24 \
    --submit-for-review
Exit code 1
Error 409: Topic already has an active experiment (c839507f-19ae-43d0-982b-26155b85322f); complete or cancel it first
```

### hint 解析
服务端正强制「topic 同一时刻一个 active experiment」约束（review/draft/running 都算 active）；v1 在 phase=review 阻塞 v2 创建。

### 修复动作
1. host 用 creator-only cancel 命令撤回 v1（v1 无 reviewer 动作、log_count=0，未污染状态）：
   ```
   $ map --persona host experiment cancel --id c839507f-19ae-43d0-982b-26155b85322f
   → phase=cancelled, actions=[]
   ```
2. 重跑 v2 create → 200：
   ```
   $ map --persona host experiment create ... --submit-for-review
   {
     "id": "34c99441-df81-492e-b3dc-206fbd219cc9",
     "phase": "review",
     "warnings": ["topic_not_ready_for_experiment"],
     "blocked_on": "awaiting_non_creator_review"
   }
   ```

### v1 / v2 关系记录
| 项 | v1 (c839507f) | v2 (34c99441) |
|----|---------------|---------------|
| plan 路径 | .map/e2e-logs/20260817T071215Z/plan.md | .map/e2e-logs/20260817T103223Z/plan.md |
| plan slug | e2e-lifecycle-smoke | e2e-lifecycle-smoke-v2 |
| 提交时刻 | 07:15Z（R8/R9 之前） | 10:34Z（R8/R9 之后） |
| 最终 phase | cancelled（被 v2 替换让位） | review（待 reviewer） |
| 关系 | v1 → v2 是 post-R8/R9 ack refresh 升级；v1 cancel 是为满足「topic 单 active experiment」约束的必要动作，不是 v1 失败的产物 | — |

### 旁注：topic_not_ready_for_experiment warning（v2 再次出现）
source topic 仍 round1 未 advance-round / 未 mark ready；host override 通过。语义同 v1：host R2/R3 收拢 + participant R7-R9 立场硬终止 = host-side mark-ready 等效语义。

> 进入 running 后立即把本节 append 到 map/experiments/e2e-lifecycle-smoke-v2/log.md 顶部，并删本 FS fallback 文件。
