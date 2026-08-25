# M1 执行日志

## summary

host 亲自执行 M1（用户纠正：不要 invoke participant 执行；评审才 invoke reviewer）。停掉误 invoke 的 participant 后 acquire lock，落地 index.md 契约、验证型写回、slug/uuid5/DB uuid 路由。DB 实验行本切片仍作投影（锁/通知/review item/token），退役条件写在 index.md。

## 实施 log

- 停 participant invoke；`executor_agent_id` 为空，host 可 complete
- I1 `write_experiment_index` / `commit_experiment_index_write` / `index-validate`
- I2 CLI `approve|start|complete|accept-result|plan revise|review add` 走 `_run_lifecycle`
- I3 `show`/`list` overlay FS；`--id` 接受 slug / uuid5 / DB uuid
- I4 锁仍走 DB `experiment lock`；index 写不碰 lock 文件
- commit: [ab64a68](https://github.com/quantaeye/multi-agent-platform/commit/ab64a68)
- 实测手改 `phase: done` 再 `index-validate --expected-from running` → `Error: experiment index.md phase is 'done', expected 'running' (hand-edited phase is rejected; use map experiment CLI)`
- `map experiment show --id experiment-lifecycle-fs-m1` 与 uuid5 读到同一投影 id `34840a7a-...`

## 风险

- A3 第一刀仍 INSERT 投影行：`show/list` 以 index.md 为准；M2 `sync --check` 后再停 INSERT
- A7 原计划 `--executor participant`，用户改为 host 自执行；本实验 executor 记 `host`
- complete 写回发生在 API 成功之后：若写回失败会 exit 1，DB 已迁相位、FS 可能落后，需人工 `index-validate` 对齐

## acceptance

- A1 index.md 含 phase / current_plan_version / creator / executor / topic / updated_at；reviews/ 不进 index
- A2 手改 phase 被独立 `index-validate` 拒绝；合法 complete 回写 phase + reviews/
- A3 投影行语义与退役条件已写入本实验 index.md
- A4 complete 后 git 可见 index.md phase 与 reviews/complete.yaml
- A5 lock acquire 仍走 DB；TTL 心跳与 index.md 无关
- A6 slug / uuid5 / DB uuid 三态路由实测同一实验
- 测试：`tests/test_fs_experiment_index.py` + `tests/test_experiment_id_routing.py` + 相关 CLI/lock/fs 共 62 passed
- ruff：改动文件 `ruff check` 全绿
