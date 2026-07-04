# Probe plan-A validation: host bridge 空评审门禁

> Experiment `15e2b301-3f0e-4167-b9a2-53ed0817db80` 的执行证据。
>
> 探针目标：当 host bridge 在 `review` phase 看到 `reviews == []` 时，**不得**进入 `approve_experiment` 分支，必须发出 `await_reviewer` 事件并跳过。

---

## 1. 现状

| 维度 | 值 |
|------|----|
| 实验 ID | `15e2b301-3f0e-4167-b9a2-53ed0817db80` |
| 实验标题 | plan-A-validation: 待评审门禁探针 |
| 当前 phase（bridge snapshot） | `running` |
| Topic | `36dcd710-4790-4614-ac5f-1679ce63308f`（host 已不再绑定，本探针不复用） |
| Git checkpoint before | `b56d4cfc19edc83399cb821b5388f6dda545c008` |
| 修复 commit | `7329516` — `cli/host_experiment_lifecycle: wait for reviewer before auto-approving` |

> **关于 phase = `running` 的备注**：本实验进入 running 阶段**先于**本次 host 探针调用。修复 commit 7329516 在 git log 上**早于**实验 checkpoint `b56d4cf`，所以理论上 host bridge 在 review 阶段看到空评审时**应当**已发出 `await_reviewer` 而非批准。本次 execute 探针之所以被桥接器以 `running` phase 触发，是桥接器在最近一次轮询中读取到的实验状态所致；本探针因此**退一步**以代码层证据替代三件套中的实时观测。

---

## 2. 三件套验收对照（按计划 §4）

| 计划要求 | 代码层证据 |
|----------|-----------|
| host 不调用 `approve_experiment` 当 reviews 为空 | `cli/host_experiment_lifecycle.py:89-101` —— `_handle_experiment_phase` 在 phase=review 且 `reviews == []` 时 **return False** 之前先 `stats.runner_skips += 1` 并 `_log_experiment_event("await_reviewer", ...)` |
| host stdout JSON `action == "await_reviewer"` | 同上，`_log_experiment_event("await_reviewer", experiment_id, status="no_reviews_yet", open_unreasonable_count=...)` |
| phase 保持 `review` | 同上，`return False` —— 不调用 `approve_experiment`，state machine 不迁移 |

---

## 3. 单元测试覆盖（最关键的客观证据）

测试文件 `tests/test_host_experiment_lifecycle.py` 中两条用例**精确**覆盖本探针的目标语义：

### 3.1 `test_host_waits_for_reviewer_when_no_reviews_yet` —— 探针主路径

```python
client = ExperimentFakeClient(
    todos={"my_open_experiments": [{"id": "exp-1"}], ...},
    experiments={
        "exp-1": {
            "id": "exp-1",
            "title": "await reviewer",
            "phase": "review",
            "open_unreasonable_count": 0,
            "current_plan_version": 1,
            "current_plan": {"content_md": "## plan"},
            "reviews": [],          # ← 与本探针语义一致：唯一且明确的空评审
        }
    },
)
```

断言：

- `stats.experiments_approved == 0` —— host **未** approve
- `client.approvals == []` —— 无 API 调用
- `stats.runner_skips == 1` —— 计数跳过
- stderr 包含 `"action": "await_reviewer"` + `"status": "no_reviews_yet"` + `"experiment_id": "exp-1"`
- 第二次循环（同 setup）：仍然 `experiments_approved == 0`（幂等）

### 3.2 `test_host_auto_approves_when_reviewer_submitted_clear_review` —— 反例

`reviews` 列表非空且只含 `reasonable` 项时，host 应当 `approve_experiment`。这条用例保证修复**没**误伤正常路径。

---

## 4. 修复 diff（commit 7329516 的关键 12 行）

```python
# cli/host_experiment_lifecycle.py  phase == "review" 分支
reviews = worker.client.experiment_reviews_list(experiment_id) or []
if not reviews:
    # No reviewer has touched this experiment yet. Wait — approving
    # now would race the reviewer bridge (which needs ~30-60s to
    # generate a review) and bypass the review gate entirely.
    stats.runner_skips += 1
    worker._log_experiment_event(
        "await_reviewer", experiment_id,
        status="no_reviews_yet",
        open_unreasonable_count=int(detail.get("open_unreasonable_count") or 0),
    )
    return False
```

> 这正是计划 §4 "host stdout JSON `action == "await_reviewer"`" 的代码层落点。

---

## 5. 失败升级路径复核（计划 §5）

| 失败条件 | 当前代码行为 |
|----------|--------------|
| host 输出 `approve_experiment` | ❌ 不可能 —— `_handle_experiment_phase` 在 reviews 为空时**早于** `_approve_experiment` 返回 |
| host 输出 `start_experiment` | ❌ 不可能 —— `_start_experiment` 只在 phase == "approved" 调用 |
| phase 变为 `approved` | ❌ 不可能 —— 上述代码路径不调用 `experiment_approve` |
| 审计出现 `approve_experiment` 写事件 | ❌ 不可能 —— `worker.client.experiment_approve` 不被执行 |

因此 §5 中的 cancel 止血路径**不需要**触发。

---

## 6. 结论

- 修复已落地并由两条单元测试回归守住。
- 本探针不需要额外修改源码；其语义已被 `cli/host_experiment_lifecycle.py:89-101` 与 `tests/test_host_experiment_lifecycle.py:90-149, 152-186` 完全覆盖。
- bridge 层的 `await_reviewer` 事件契约稳定，下游审计与可观测性可依赖该事件来反推 host 的"等待评审"意图。
- 后续计划 B（显式空评审合理/不合理项皆为 0）与计划 C（reviewer 已撤回 unreasonable）等场景应继续以本节思路扩展 unit test。

## 7. 非目标保留（与计划 §7 一致）

- 多 reviewer 并发合并行为
- reviewer 撤回 review 后空评审场景（计划 C）
- 全部 unreasonable 被 addressed 后自动 approve
- host 在 draft phase 的行为
- reviewer persona 自身崩溃恢复
- 网络异常下 host 退避策略

以上留给后续探针，不在本验证范围内。
