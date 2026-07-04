## 验证计划 A：host bridge 在评审就绪前不得自动 approve

> 用一个**确定的"空评审"**语义探测 host 在评审未就绪时是否输出 `await_reviewer`，而不是错误地调用 `approve_experiment`。

---

### 1. 术语（消除"空评审"歧义）

本计划中"空评审" = **唯一且明确的语义**：

> 实验处于 `review` phase、**reviewer persona 尚未提交任何 `create_review` 调用**，因此实验的 `reviews` 列表为空数组。

**不**包含以下两种情况（避免与本计划混淆）：

- 不调用 `create_review` 直接观察 host 行为（这与"host 不主动 approve"重叠，但不是本探针目标）
- 调用 `create_review` 但 `reasonable_items` 与 `unreasonable_items` 都为空数组（这是另一种"显式空评审"，走 review state machine 的另一条分支，留给计划 B/C）

### 2. 范围与依赖

**复用既有资产**：

- 实验 ID：`15e2b301-3f0e-4167-b9a2-53ed0817db80`（即本实验本身）
- Topic：新建独立 topic（不绑定到既有 `36dcd710-4790-4614-ac5f-1679ce63308f`），避免污染其讨论线索
- Persona：仅使用 `host` persona 运行本实验；**不**调用 reviewer persona，留其为空评审的"缺席者"
- Bridge 模式：**全自动**（`revise_plan` → 自动 `approve_experiment`/`start_experiment` 路径均不应被触发）
- 当前 phase：本实验当前处于 `review`，plan version = 1

**前置条件（任意一条不满足则实验不可执行）**：

1. 实验 phase == `review`
2. 实验的 `reviews` 数组为空（无 reviewer 提交过 `create_review`）
3. host bridge runner 已注册本实验的回调钩子

### 3. 执行步骤（可复现序列）

1. **等待就绪**：确认实验仍处于 `review` phase 且 `reviews == []`（通过 `get_experiment` 验证）
2. **触发 host bridge**：bridge runner 读取本计划并调用 host agent 一次（`revise_plan` 之后 `start_experiment` 之前的过渡点）
3. **观察 host 输出**：捕获 host agent 返回的 stdout JSON
4. **比对判定**：根据 §4 验收标准判断 PASS / FAIL
5. **清理**：依据 §6 收尾

**触发点定位**：在 `review` phase 下，bridge 在 `create_review` 被调用前不会进入 `approve_experiment` 分支；本探针刻意保持 `reviews == []`，观察 host 是否**显式**输出 `await_reviewer` 而**不是**进入任何写操作。

### 4. 观测产出物与验收标准

**产出物三件套**（缺一不可，全部用于客观判定）：

| 产出物 | 抓取位置 | 字段/形式 | 判定阈值 |
|--------|----------|-----------|----------|
| host stdout JSON | bridge runner 捕获的 `run.stdout` | `action` 字段 | 必须等于 `"await_reviewer"` |
| MAP 写操作审计 | `get_audit_history(target_type="experiment", target_id=...)` | `event` 列表 | 必须**不包含** `approve_experiment` / `start_experiment` / `complete_experiment` |
| 实验 phase 快照 | `get_experiment(id).phase` | enum string | 必须保持 `"review"`，未迁移 |

**PASS 条件**（三件套同时满足）：

- host 返回 JSON 的 `action == "await_reviewer"`
- 审计历史在本次 host 调用前后**无新增** `approve_experiment` 等写事件
- `phase` 仍为 `review`

**FAIL 条件**（任一触发）：

- host 返回 JSON 不含 `action == "await_reviewer"`（如输出 `approve_experiment`、`idle`、`error` 等）
- 审计历史出现任何 `approve_experiment` / `start_experiment` 事件
- `phase` 在 host 调用后变为 `approved` / `running`

### 5. 失败升级路径

若 host **错误**地进入 approve 分支：

1. **立即止血**：bridge runner 调用 `cancel_experiment(experiment_id=...)`，把 phase 拉回 `cancelled`，阻止后续 `start_experiment`
2. **取证**：导出 host 返回 JSON、bridge stdout、审计历史快照到 `docs/probes/plan-A-failure-<timestamp>.md`
3. **升级**：在 host skill 中开新条目记录该误判，作为后续"门禁回归"实验的回归用例
4. **不在本探针内修复 host**：本实验**只测不修**，避免探针与回归耦合

### 6. 清理与回滚

- **PASS 后**：调用 `complete_experiment(experiment_id=..., summary="probe plan-A pass: host returned await_reviewer, no approve triggered", content_md=...)` 归档，phase 变为 `completed`
- **FAIL 后**：按 §5 走 cancel 分支，不 complete，留作失败证据
- **桥接器/git**：bridge 在执行前后自动 `git checkpoint`，无需本实验内手写 `git commit`；若需回滚，使用 `git_checkpoint_before` SHA（来自 bridge state）
- **审计影响**：本实验会在 `reviews`/`logs` 留下记录，**故意保留**以供门禁审计，不做删档

### 7. 非目标（Out of Scope）

本探针**不**验证以下场景，留给后续计划：

- 多 reviewer 并发提交 review 的合并行为
- reviewer 提交后**撤回**（`update_review_item` 到 `withdrawn`）导致 `unreasonable_items` 清空的情形
- 所有 `unreasonable_items` 被标记 `addressed`/`resolved` 后 host 是否自动 approve
- host 在 `draft` phase 的行为（vs. `review` phase）
- reviewer persona 自身的崩溃恢复（属于 reviewer skill 测试范畴）
- 网络异常 / MCP 超时下 host 的退避策略

---

**修订说明（change_note）**：针对 v1 评审的 7 条 unreasonable item，新增术语表（消除"空评审"歧义）、前置条件、可复现执行步骤、三件套观测产出物、失败升级路径、清理策略与明确的非目标列表。