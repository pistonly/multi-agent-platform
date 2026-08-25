# 退役面物理收口（124e9a00）— 结果审批（accept）

## 结论

**通过**。收口 commit `62626c0`（I1-I4，8 文件）+ 日志归档 `b3d176e` 均已入 HEAD，工作区干净；plan v2 全部验收项（A1-A5 + 测试面）核证成立，其中 A3 两类回归注入由 reviewer **独立复测**均按预期 fail。

## 验收逐条核验（对照 git HEAD）

| 验收 | 判定 | 证据 |
|------|------|------|
| A1 legacy 组 stub 化 | ✅ | 4 个 bridge 脚本（participant/reviewer × 普通/claude）实测逐个执行 → **4/4 exit=1** + stderr 功能等价指引（单 persona → `start-simple-waker.sh --persona <p>`，三 persona → `start-all-wakers.sh`）；模板统一（DEPRECATED 注释 + echo >&2 + exit 1）；`start-host-bridge*.sh` 未枚举（v0.10 已删） |
| A2 两阶段边界 | ✅ | 零物理删除：4 文件全部保留为 stub，无文件删除记录 |
| A3 CI 双规则防回潮 | ✅ | `check-deprecated.sh` Rule1（deprecated 必须保持 stub）+ Rule2（声明处引用路径必须矩阵登记）；**reviewer 独立注入复测**：注入1（stub 改回真脚本）→ fail；注入2（CLAUDE.md 加未登记 `start-zombie-new.sh`）→ fail，且 AGENTS.md 同步副本一并检出；还原后 pass。CI 接线确认：ci.yml backend job checkout 后第一步执行 |
| A4 矩阵登记更新 | ✅ | `LEGACY-ENTRY-MATRIX.md` scripts 节 4 行更新 stub 现状；manifest 段声明「登记单一真相 + CI 消费契约，改格式先过 CI」——A3 规则(2) 基准即此矩阵，无第二清单 |
| A5 删除纪律文档 | ✅ | `docs/MAP-SIMPLE-WAKER.md:212-221`：第二阶段物理删除须显式 diff 评审、host 之外至少一人看过清单表态；观察期隐藏引用先登记再裁决 |
| 测试面 | ✅ | 回归注入实测全绿（见 A3）；本实验改动面为 sh/md/yml（无 .py），不新增任何 ruff 报告；全仓 4 个 ruff 红均为预存且早于本实验（见遗留） |

## 独立复测记录（reviewer）

- stub 执行矩阵：4/4 `exit=1`，stderr 均含替代命令（participant/reviewer 各自正确指 `--persona` 值）
- 注入1/注入2 复测均 fail、还原后 `check-deprecated: all manifest entries present; stub + registration rules pass`（exit=0）
- 复测中确认 Rule2 对 CLAUDE.md 与 AGENTS.md 双副本生效（注入行被两处检出）

## 遗留（非阻塞）

- 全仓 `ruff check` 4 红均为预存：`cli/session_wake_log.py:104` UP038（log 已声明）+ 3 个 I001（`tests/test_049_project_fs_content_config.py` / `test_review_archived_metadata.py` / `test_review_item_state_closed_migration.py`，最后修改分别为 6889869 / e23b1e5，均早于本实验）——log 仅声明 UP038、未列 I001×3，登记不全但与本实验零相关；建议 test-baseline-green-evidence-gate 实验把 ruff 基线与 26 红一并纳入清点。
- 第二阶段（物理删除评估）按 A2 留待观察周期后裁决，不在本实验范围。
