---
experiment: fs-participant-whitelist
status: done
completed_at: 2026-08-15
summary: "参与人白名单三源合并（creator ∪ declared ∪ speakers）落地 derive_work 单一过滤点，白名单外 persona 零 FS 待办"
evidence:
  - "tests/test_fs_source.py 20 passed（含 6 个新增白名单用例）"
  - "map/ 话题 index.md 已携带 participants front-matter 并随发言自动并入"
---

# 实验日志：fs-participant-whitelist

## 实施内容

### 1. parser 层（sdk/python/map_fs/parser.py）

- `FsTopic.declared_participants` 字段：解析 index.md front-matter `participants:`，
  容错处理（非列表 / 含空串按忽略；标量单值按单元素列表，落实 review 建议 1）
- `FsTopic.participants` property：三源合并 `creator ∪ declared ∪ speakers`，
  展示顺序稳定（creator 在前、declared 次之、speakers 按发言顺序追加，去重）
- `write_topic_index(participants=[...])`：声明列表写入 front-matter
- `write_round_comment` 新作者自动并入：`_merge_participant` 把首次发言的 persona
  追加进 index.md participants（index 缺失的手建文件夹容错跳过，落实「发言即参与」）
- `derive_work` 白名单过滤：`pending_topic_reply` 仅对 persona ∈ participants 生成；
  `round_ack_pending` 的 missing 计算仍用全量 participants（语义不收缩）

### 2. CLI 层（cli/commands/fs.py）

- `fs topic-create --participants a,b`：逗号分隔声明白名单
- `fs comment --force` 覆盖已有文件前输出显式警告（不可变约定的例外需留痕）

### 3. 测试（tests/test_fs_source.py）

新增 6 个用例：

- 白名单外 persona（reviewer / 陌生 outsider）对 open 话题零待办
- 声明 participants 后：成员有待办、非成员无待办
- 新发言人自动并入 index.md（发言即参与，持久化在 front-matter）
- 存量话题（无 participants front-matter）行为与改造前一致
- front-matter 标量容错（`participants: host` 单值写法）

## 验收对照

| 验收标准 | 结果 |
|----------|------|
| reviewer 对未声明参与的 open FS 话题零 FS 待办 | ✅ `test_whitelist_blocks_non_participant_pending` |
| `write_topic_index(participants=[...])` 写入 front-matter | ✅ `test_declared_participants_receives_work` |
| 新作者发言自动并入 participants | ✅ `test_comment_auto_merges_new_participant` |
| `fs topic-create --participants a,b` 可用 | ✅ CLI 参数 + 测试覆盖 |
| 存量话题行为与改造前一致 | ✅ `test_legacy_topic_without_front_matter` |
| advance-round ack missing 仍覆盖全量 participants | ✅ derive_work host 分支未改语义 |
| pytest tests/test_fs_source.py 全绿 | ✅ 20 passed |

## 非阻塞建议落实（review.yaml）

- 建议 1（front-matter 容错：标量按单元素）：已落实 + 测试钉住
- 建议 2（合并顺序稳定：declared 在前按并入时间追加）：已落实（creator → declared → speakers）

## 风险观察项

- 并发写 index.md 竞争窗口：维持「观察项」定级（话题决策 #6），当前粒度下冲突面极小
- 不改 service 层：`fs_work_items` / `fs_topic_progress_for_agent` 均走 `derive_work`
  单一 choke point，parser 层过滤即全链路生效（waker 路径自动受益）
