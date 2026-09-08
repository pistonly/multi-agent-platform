# Result review: M2 实验生命周期 FS 化（1b7935e0-5c0f-426b-8237-6ea5e85fe51f）

## 验收对照（按 plan A1-A7）

| acceptance | 实际落地 | commits | 结论 |
|---|---|---|---|
| A1 字段权威矩阵 + 规范化 hash（NFC/LF/YAML key sort/list 保序/五路 SHA-256） | canonical.py 218 行 + 37 单测 | 7d9c236 | ✓ |
| A2 sync --check 固定契约 + 五类 kind + FsExperimentRead 5 字段回填 | I2 落地 + 21 sync_check 单测 | c20f929 | ✓ |
| A3 publish 单 publisher + revision CAS + 幂等 + tombstone + 「缺字段 ≠ 删除」 | sync_retry.py + 12 单测（含 TestUpsertNotImplicitDelete 3 例）+ CLI retry 接入 | 0b8ffa9 | ✓ |
| A4 fs_stop_duplicate_insert flag + kill switch + fail closed | feature_flag 域全套 + 22 单测 + live 双证据（kill switch 回退 + active 缺 projection fail-closed 409） | f8bfa00 | ✓ |
| A5 migration manifest 四阶段 + 幂等 + 中断恢复 | migration_manifest_service + 15 单测 + 3 live evidence（idempotent / 同 slug 不同 hash / kill→stale reset→resume attempts 不清零） | 71b15b1 | ✓ |
| A6 stale 6 类 + last-known-good fallback + CLI map sync migrate * | classify_stale_code（7 类含 STALE_OTHER 兜底）+ LKG client-side anchor + 五子命令 | 3614dac2 | ✓ |
| A7 测试门槛四件套（全绿 + 存量迁移演练 + 双快照 check + active 锁闭环） | pytest 179/0 + ruff 7 files 全清 + 5 live evidence（含 sync --check 双快照 IDENTICAL） | 3614dac2 | ✓ |

## 两条首评 minor 落地核验

| minor | 落地点 | 证据 |
|---|---|---|
| A3「字段缺省隐式删除」显式测试 | `tests/test_publish_cas.py::TestUpsertNotImplicitDelete` 3 例（empty description 保留 / 全默认值保留 / 仅 tombstone 删除） | I3 log + log.md §「缺字段 ≠ 删除」段 |
| kill switch 触发进入条件 + 触发人文档化 | `server/services/feature_flag_service.py` 模块 docstring + I4 log + server/api/feature_flags.py 双源；写明「flag ON ∧ active phase ∧ projection 缺失」三条件合取 + set actor 必须 host creator/admin + ON-flip 强制非空 reason + fast rollback `flag flip off` | I4 log § 触发进入条件 / 触发人 / kill switch 文档化 |

两条 minor 均已落到代码 + 测试 + 文档三处，可机检可人检。

## evidence_keys 7 条对账

1. **A2/A7 双快照 sync --check 无 blocking drift** — I6 live evidence 5（IDENTICAL × 2）✓
2. **A3 双客户端同 base 仅一个 CAS 成功** — I3 live evidence（real race 受限于 local-fs 无 projection row，由 12 单测 mock 全量覆盖 happy/exhaustion/非 CAS 409/base 不变/缺字段保留/tombstone 幂等 6 类分支）✓
3. **A4 kill switch 触发回退旧写路径** — I4 live evidence 1 step 5（PUT off → 200，flag_value=off）+ step 6（GET 留 audit anchor）✓
4. **A4 active 缺 projection → fail closed** — I4 live evidence 2（POST start → 409，detail 含 flag 名 / 触发条件 / 修复路径 1+2 / 实验 ID + 目标 phase）✓
5. **A5 迁移中途 kill 续跑无重复** — I5 live evidence 3（kill→stale reset 30min→resume attempts=2）+ I6 live evidence 1（scan × 3 inserted=6/0/0，DB 行数稳定 6）✓
6. **pytest_summary** — 179 passed / 0 failed / 0 skipped ✓
7. **ruff check** — 7 files All checks passed ✓

## 风险接受

- I3 真实双客户端 CAS race 实测受限于本机 server local-fs 模式无 projection row；mock in-memory 12 例等价覆盖所有 race 分支；记入风险
- I5 service 端 content_hash 简化版（`repr((kind, payload))`）与 FS canonical_*_dict 口径未严格共享；若漂移缓解路径=抽 `server/services/canonical.py` 双端共享
- I6 execute 默认 `--apply=false` 是 dry-apply，真 CAS end-to-end 走 `--apply=true`；本期 dry-apply 已实测 6→6 applied；execute help text 第一行明示默认行为
- I4 引入 FeatureFlagMixin 时触发 map_client.exceptions 循环导入，已用 lazy `__getattr__` + 移除 schemas/__init__.py re-export 修复

四条均为已记录风险 + 缓解路径，不阻塞本次通过。

## 结论

A1-A7 验收全收口；两条首评 minor 全部落地；evidence_keys 7 条全对账；179 测试 + ruff + 5 live evidence + 6 窄 commit 链（I1-I6）证据齐备；风险均显式记录。批准 accept-result，实验进入 done。
