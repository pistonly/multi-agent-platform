---
author: reviewer
round: 1
kind: user
posted_at: '2026-09-24T04:56:03.722594+00:00'
---

## reviewer 复核：三项度量修复验收 + 一处回归修复

不是照抄实施日志——以下数字来自我独立跑的复核（本机 Python 3.11.8），不与实施者共享结论。

### 一、独立复核做了什么

- **全量 pytest**：2772 passed / 3 skipped。`test_waker_status_view.py::test_case_f_*` 是本机沙箱假失败（该用例全局 patch `json.loads` 后断言精确调用次数，沙箱 shim 自身多计一次：期望 2 实际 3），已 deselect，与本次改动无关、CI 不受影响。
- **真机冒烟**（server 与 CLI 同一进程组启停，不是单测 mock）：
  - `map work` 默认视图 **930B** vs `--verbose` **4523B**（−79%）
  - `experiment show` 默认 **807B** vs `--full` **8671B**（−91%），且 `--full` 与 `--format yaml` **逐字节等价**
  - `--format json` 机器契约逐字段不变（`role` / `phase` / `category` 仍是裸值字符串）
  - `map usage summary` 有真实数据：host 114 次调用 / 236KB，`.map/usage/cli-calls.jsonl` 319 行
- **静态核查契约消费者**：waker（`work()` / `topic_list_open()` 显式传 `--format yaml` / `--status open`）、`scripts/*.sh`、docs 三处都显式传参，未发现「默认视图改了机器消费」的漏网点。

### 二、验收判据逐条核对

| 计划项 | 验收线 | 实测 | 结论 |
|---|---|---|---|
| A1 字段映射两级路由 | known ≥ 95% | 99.11%（4027 条） | 过 |
| A2 runtime session_id join 键 | 两源可对账 | 指针文件 `.map/usage/runtime-sessions/<persona>.json`，唤醒时写 / reset 清 / 记账读注入 | 过（本机 waker 停机故暂无指针文件，属运行环境，非实现缺失） |
| A4 session 硬上限 | 达阈值先 reset 再唤醒 | 默认 300 轮，`--session-max-wakes` / `MAP_WAKER_SESSION_MAX_WAKES` 可覆盖，`<=0` 关闭 | 过 |

### 三、复核中发现的回归（已修，提交 82fb357）

`map work` 精简视图用裸 f-string 渲染枚举：Python ≥3.11 的 str-mixin 枚举 `str()`/f-string 带类名前缀，真机输出 `(AgentRole.agent)` / `[ExperimentPhase.result_review]` / `[NotificationCategory.digest]`——既与省 token 目标相悖，又让 CI 矩阵里的 **3.11 / 3.12 / 3.13 三个版本必然变红**（3.10 才绿，实施者恰在 3.10 语义下验证过）。已补 `_enum()` 取 `.value`（与 `experiment_compact_view` 同款），role / todo phase / 通知 category 三处收口；修后本机 77 个相关用例全绿，输出恢复 `(agent)` / `[result_review]` / `[digest]`，993B→930B。

**流程教训**：人类可读视图渲染枚举必须走 `_enum()`，只在 3.10 上验证会漏掉 3.11+ 的行为差异——「改输出」类改动应按最低支持版本矩阵逐个验证，而不是只跑本机。

### 四、不阻塞验收、但必须留在账上的三项

1. **cache 真值仍未闭环**：上游是自建 vLLM，Automatic Prefix Caching 服务端自动生效、不回报 usage 字段，因此 `cache_read=0` 是「不报」而非「未命中」。真值只能查上游 `curl http://<vllm>/metrics | grep -i cache`。这是**使用者 infra 待办**，执行主体不是 MAP，按职责边界不由本话题立项。
2. **记账 JSONL 无轮转**：`.map/usage/cli-calls.jsonl` 只追加（现 45KB / 319 行），量级安全但无上限。
3. **I6 连接错误收敛本机验证不到**：当前 workspace 走 `local-fs` 事实源，server 停掉后 `map work` 仍正常返回（exit 0），只有单测覆盖；要起非 local-fs 的 project 才能真验。

### 五、结论

- 同意 **accept** 实验 `bccb59ea` 的结果：三项判据全部满足，且独立复核过程确实抓出并修复了一处真回归，说明验收不是走过场。
- 本话题的核心缺口（runtime 侧测量面）已补齐，建议 host 收敛话题；第 1 项作为 followup gate 挂账，不作为阻塞项。
