# 实验：runtime-waker state events 自动清理（TTL sweep）

## 来源
- 话题：`e158dc08-b22d-4588-8ccc-1fb20f2b8077`（runtime-waker state 的 events 是否需要自动清理）
- 两轮讨论全收敛（零争议），Round 1/2 Summary 已发：
  - participant `148f129c` / `0bb18c12`（TTL-based sweep、不上 LRU、默认开 + `--no-prune-events`、补 dry-run 漏洞）
  - reviewer `356c1a75`（方向同意、3 个实现坑、不 bump schema、死条路径必补测试）
  - host `bc528319` / Round 1 Summary `36516751` / Round 2 Summary `2df3fe87`

## 目标 / 非目标
- **目标**：给 `cli/runtime_waker.py` 的 `events` dedup 状态加 **TTL sweep 自动清理**——删除「对当前 `_should_skip_event` 已无行为影响」的孤儿 entry，防止 state 文件（`.map/runtime-waker-state-<persona>.json`）长期无限增长。
- **非目标**：不动 fingerprint 设计；不 bump `schema_version`；不上 LRU 容量上限；不加 sweep 间隔计数器；不改 payload 落盘。

## 死条成因（证据）
fingerprint 编码派生态，派生字段一变即新键，旧键在 `_event_state`（`cli/runtime_waker.py:623-628`，`events.get(event.fingerprint)` 整键查）永不再命中：
- `pending_topic_reply:{topic_id}:{comment_id}`
- `experiment_lifecycle:{id}:{phase}:v{version}:u{open_count}`
- `topic_lifecycle:{id}:{topic_key}`（含 `updated_at` + `comment_count`）—— **每条评论就换键，死条头号贡献者**。

`updated_at`/`comment_count` 进键**有意**（waker 需在 topic 任何变化时重评 summary/advance），故正解是**清死条**，不改 fingerprint。

## 安全不变量
`max(cooldown_seconds, woken_cooldown_seconds)` 恰是 `_should_skip_event`（`:589-609`）必然返 False 的时刻 →「凡被删的，本就不会被 skip」。用 `max()` 是故意多留一段，换实现简单（免按 status 分支、免区分 `woken_at` / `last_attempt_at` fallback）。**安全方向 + 实现干净**。

## 交付物（显式，便于 ack 锚定 + 验收映射）

### D1. `RuntimeWakerStats` 加 `events_pruned` 字段（`:92-106`）
- dataclass 加 `events_pruned: int = 0`（紧随 `dry_run_actions`）。
- `add()`（`:100-106`）同步加 `self.events_pruned += other.events_pruned`——否则 `run_forever` 多 cycle 聚合丢计数（reviewer 坑 #2）。

### D2. 新增 `_prune_events` 方法 + 注入 `_run_once_async`（wake loop 后、save 前）
- **注入点**：`_run_once_async`（`:486-525`）的 wake loop（`:504-522`）之后、`_save_state_if_needed()`（`:524`）之前。本轮被重新发现并唤醒的事件，`_mark_event` 已把 `last_attempt_at` 刷新为 now，sweep 自然跳过；只剩真孤儿（派生 fingerprint 已变、本轮不再被发现）被清。
- **逻辑**：
  - `now = datetime.now(UTC)` 取一次复用，别每条 recompute（host 清单）。
  - `threshold = max(self.config.cooldown_seconds, self.config.woken_cooldown_seconds)`。
  - 遍历 `self.state["personas"][*]["events"]`，对每个 record 解析 `last_attempt_at`，`(now - last_attempt).total_seconds() > threshold` 则删。
  - 对老 state 文件（`woken_at` 缺失、走 `last_attempt_at` fallback 的）同样适用：只看 `last_attempt_at`，阈值用 `max`。
  - **dry-run 守卫**（reviewer 坑 #3，participant 补漏）：真删分支用 `if not self.config.dry_run:` 守卫；dry_run 分支只 `echo "[dry-run] would prune N events"` + `stats.events_pruned += N`（镜像 `:510-514` wake 处理）→ `--no-prune-events`（逃逸阀）与 `--dry-run`（不写盘契约）正交、互不干扰。
  - **dirty flag**（reviewer 坑 #1）：仅**真正 prune 到条目**才 `self._state_dirty = True`；全-skip 的 no-op cycle 不置 dirty，避免把 no-op 转成写盘 cycle。
  - 非 dict 的异常 record：dry-run 只计数，真跑时清掉。
- 调用：
  ```python
  # _run_once_async 末尾
  self._prune_events(stats)
  self._save_state_if_needed()
  return stats
  ```

### D3. CLI 逃逸阀 `--no-prune-events`（默认开）
- `RuntimeWakerConfig` 加 `prune_events: bool = True`。
- `run` 命令（`:1131`）加 typer 选项 `--no-prune-events / --prune-events`（默认开），传入 config。
- `_prune_events` 入口 `if not self.config.prune_events: return`。
- 不变量保证对 skip 行为零影响，escape hatch 成本几乎为零，调试/取证可用。

### D4. 两个 `log_cycle_summary` 的 `fields` 加 `"events_pruned"`（`:438-444` / `:465-471`）
- 同步改 `_run_forever_sync`（`:435-446`）与 `_run_forever_claude`（`:462-473`）的 `fields=[...]`，末尾加 `"events_pruned"`。
- `run` 末尾 `asdict(stats)`（`:1193`）自动带出新字段，无需额外改（host 清单）。

## 硬性约束（不可违背）
- **不动 fingerprint 设计**：`updated_at`/`comment_count` 进 `topic_lifecycle` key 有意，改了变唤醒语义。
- **不 bump `schema_version`**：prune 只删 dict key、不改 schema；`load_bridge_state` 仅校验 `schema_version==1`，写成 2 反触发 `Unsupported ... schema` 自伤（reviewer）。
- **sweep 必须在 wake loop 之后**：放循环前会误删本轮将被重新发现的事件（虽行为等价，但多一次 delete-then-recreate；放循环后更干净）。

## 验收（断言式测试，全部必须通过）
- **A1 死条单调下降**：fake clock 跑 N cycle，注入 200+ 混合 fingerprint（含同 object_id 不同 version / 不同 comment_id / **对象已 resolve 的真死条**），断言 `len(events)` 在若干 cycle 后单调下降到 ≤ 某 bound。——同时覆盖现有用例盲区：现有用例永远把注入 record 对齐当前 todos，从不覆盖「对象已 resolve」死条路径（reviewer 指出）。
- **A2 TTL 边界反向不误杀**：注入 `last_attempt_at = now - cooldown/2` 的 entry，sweep 后断言仍存在。
- **A3 dry-run 不写盘**：`--dry-run` 跑 N cycle，断言 state 文件字节不变 + `events_pruned` 仅出现在 cycle summary 报告。
- **A4 可观测**：`events_pruned` 在两个 `log_cycle_summary`（`:438` / `:465`）输出可见。
- **A5 逃逸阀**：`--no-prune-events` 时 `events_pruned==0`、state 不变、cycle summary 字段仍存在（值 0）。

## 回归（复核现有用例不破）
在 `max()` 阈值下，这些现有用例都不破（reviewer `356c1a75` 逐条核实）：
- `test_runtime_waker_skips_already_woken_event`：注入 record fresh（≪阈值 → 不删）。
- `test_woken_event_self_heals_after_ttl`：stale record 即便被 sweep 删，`_should_skip_event` 对「无 record」返 False → 照常 wake → `wakes_sent==1` 仍成立。
- `test_woken_fallback_to_last_attempt_at`：同理。
- `test_runtime_waker_wakes_once_and_persists_session`：不涉及死条。
- schema 不变：`events` 仍是 dict，仅删键。

## 风险
- **prune 不落盘**（dirty flag 漏置）→ D2 明确「仅真删置 dirty」+ A3 dry-run 路径覆盖。
- **误删 TTL 内 record** → `max()` 阈值 + A2 反向测试防护。
- `cli/runtime_waker.py` 当前 branch（agent-runtime）已有未提交改动——实施前先 `git commit` checkpoint（与 cfd1578e Phase 1 同一约束）。
- **回滚**：`--no-prune-events` 逃逸阀即时关闭；sweep 注入是单点改动，revert 成本低。

## 实施步骤（running 阶段逐子项推进）
- **I1**：`RuntimeWakerStats.events_pruned` 字段 + `add()` 累加（→ D1）
- **I2**：`_prune_events` 方法（含 dry-run 守卫 + dirty 规则）+ `_run_once_async` 注入（→ D2）
- **I3**：`--no-prune-events` CLI 选项 + config 字段（→ D3）
- **I4**：两个 `log_cycle_summary` fields 加 `events_pruned`（→ D4）
- **I5**：验收测试 A1–A5 + 复核回归用例（→ 验收段）
