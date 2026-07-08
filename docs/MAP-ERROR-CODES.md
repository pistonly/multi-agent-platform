# MAP Error Codes

MAP 服务端错误码 baseline。机器可读源见
[`docs/error-codes/index.json`](error-codes/index.json)；CLI 端查询
`map docs error-codes` / `map docs error-codes --search <keyword>`
（多关键词 AND）。

本仓库当前已落地 **3** 个 error_code 默认 / 子码：

| code | category | http | owner |
|------|----------|------|-------|
| `state_machine_error` | state_machine | 409 | 156172e9 |
| `REVIEW_ALREADY_ARCHIVED` | review | 422 | 18f1d8f6 |
| `REVIEW_REJECT_RESULT_MISUSE` | review | 409 | 18f1d8f6 |

## state_machine_error

- **category**: `state_machine`
- **http_status**: `409 Conflict`
- **title**: State machine transition rejected
- **description**: Default `error_code` emitted by `StateMachineError` when no more specific subcode applies. The current phase does not accept the requested transition.
- **hint**: Inspect the experiment phase via `map experiment status --id <uuid>` and follow the `recovery_hint` from the `STATE_MACHINE.*` subcode or the SDK `recovery_hint()` helper.
- **retryable**: false
- **since_version**: `0.7`

## REVIEW_ALREADY_ARCHIVED

- **category**: `review`
- **http_status**: `422 Unprocessable Entity`
- **title**: Review is archived
- **description**: Attempted to mutate a review that has been archived (e.g. by `plan_revise` finalize, manual archive, or the W11 auto-archive flow). Resolve / withdraw / update-item flows are not allowed on archived reviews.
- **hint**: If you need to change your verdict after a plan revision, create a new review on the latest `plan_version` via `map --persona reviewer experiment review add --id <exp> --review ./review.yaml`.
- **retryable**: false
- **since_version**: `0.9`

## REVIEW_REJECT_RESULT_MISUSE

- **category**: `review`
- **http_status**: `409 Conflict`
- **title**: reject-result called by non-reviewer or on running experiment
- **description**: Subcode of `state_machine_error`. `reject-result` is restricted to reviewer persona on experiments in `result_review` phase. Calling it as host/creator or on running/draft/approved phases raises this subcode.
- **hint**: Use `map experiment complete` (host) to submit a result for review, or `map --persona reviewer experiment reject-result` only when the experiment is in `result_review`. Do not use `reject-result` as a substitute for `plan_revise`.
- **retryable**: false
- **since_version**: `0.9`

## 关联实验

| 实验 | 职责 |
|------|------|
| `156172e9` | state_machine_error 默认错误码落地 |
| `18f1d8f6` | review 子码（REVIEW_ALREADY_ARCHIVED / REVIEW_REJECT_RESULT_MISUSE） |
| `8a8822b5` | (d) 错误码 baseline 文档化 + `map docs error-codes` CLI |
