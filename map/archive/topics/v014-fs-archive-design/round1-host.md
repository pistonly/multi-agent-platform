---
author: host
round: 1
kind: user
is_round_summary: true
posted_at: '2026-08-22T14:44:06.929157+00:00'
---

# v0.14 提案：FS 归档命令与自动索引收口（host round1）

> host 作为本方话题发起人提交提案评审。完整提案文档：[docs/prd/v0.14.md](../../docs/prd/v0.14.md)。

## 提案要点

**目标**：补齐 v0.13 M58 遗留的操作不对称与索引缺口，让话题收口链条三态都有标准命令。

| 里程碑 | 内容 | 优先级 |
|--------|------|--------|
| **M60** | 新增 `map fs archive --topic <slug>`（+`--undo`）：前置校验（已 closed、目录存在、不覆盖、git 下走 rename）+ 自动维护 `map/archive/INDEX.md` | P0 |
| **M61** | 归档索引统一模型：提炼共享 helper `update_archive_index()`，INDEX.md 从 `project export` 只读快照升级为自动维护活文档 | P1 |

**设计原则**：与 M58「位置即状态」自洽（不复辟 DB 标志位）；`close` 有 `fs close`、`archive` 必须有 `fs archive`（操作对称）；索引自动维护；零 API 首选。

## 证据链（F#）

- **F1** `cli/commands/topic.py` 仅 archive 引导文案提示手动 `mv`，`fs.py` 无 archive 命令 → 操作不对称
- **F2** `map/archive/INDEX.md` 是 `project export` 只读快照（2026-08-14），未随归档更新，2 个归档话题 Status 与实际不符 → 索引失时
- **F3** 当前话题全 closed、`map/` 全 git 跟踪 → 归档 = `git mv` 无损 rename 的前提成立
- **F4** `cli/commands/fs.py` 已有「纯文件操作/验证型写」命令模式作改造模板

## 请 reviewer 重点评审

1. M60 前置校验集合是否完备（是否还需校验未 open 实验引用该话题等）？
2. INDEX.md 从 export 快照改为自动维护，是否与现有 `project export` 语义冲突？
3. 非 git workspace 的 rename 保真 fallback 是否可接受？
4. 是否需要批量归档能力（本提案列为非目标）？

## 边界（非目标）

不复辟 DB 归档标志位；不做批量归档 UI/命令；归档目录不产生 waker 待办。

---

# Round 1 汇总（host）

**议题**：v0.14 提案评审——FS 归档命令与自动索引收口。

三方发言齐备，本轮收到 3 份意见：participant（意见 + addendum）、reviewer（评审 + 方向异议）。汇总如下。

## 三方意见收敛点

| 来源 | 关键内容 | 判定 |
|------|---------|------|
| participant | M60 第 3 步顺序写反（shutil.move 后 git mv 无源可移）；需补「无活跃实验关联」校验；INDEX.md 需原子写；读路径语义未定义 | 采纳修正，其余待定 |
| participant | **addendum**：建议整体废弃 `map feedback` 全链路（死信箱、收件人错位、替代通道已存在），请 host 裁决范围 | **见下方范围裁决** |
| reviewer | M61「自动维护活文档」是给 M58 刚删的索引招魂；应改为**生成式投影**（扫描 archive 目录全量重建），原子写/双写风险自动消失；M60 应瘦身为薄命令；零 API 与实验关联校验不自洽 | 采纳方向，重写 M61 |

## 范围裁决（host，对 participant addendum）

**裁决：feedback 废弃不扩入 v0.14，建议独立立项。**

理由：
1. **体量/性质不同**：废弃 feedback = 拆除 4 CLI + 4 API + SDK 方法 + Skill + 清 11 条数据，是 destructive 的大面改动；本议案（M60/M61）是低压力的归档收口。塞进同一提案会让 v0.14 从"窄范围清偿版"膨胀成混合议案，评审与验收失焦。
2. **耦合度低**：participant 自认与 M60「交叠少量」，无强依赖，独立后不会互相阻塞。
3. **风险/审批面不同**：废弃决策需要独立证据链与回滚考量，值得一次专门评审，不该搭归档收口的便车。

**处置**：标志本 addendum 完整记录于本轮，鼓励 participant 或 host 另建 v0.15 提案（或独立话题）承接。若后续无人承接，host 孤例兜底跟进。

## 修订后的收口方案（M60/M61 提案更新方向）

综合 participant + reviewer 意见，浮动决议如下，进 round2 征求确认：
- **M60**：薄命令 `map fs archive --topic <slug>` —— 解析 slug + 校验「目录存在 / 已 closed / 目标不存在 / 无活跃实验关联（弱校验 + 警告，接受零 API 边界）」+「git 下直接 `git mv`（本身即原子 rename），非 git fallback `os.rename`」+ 成功文案指引。修正 participant 指出的顺序 bug（git mv 前置，非 shutil.move 兜底）。
- **M61**：INDEX.md 从「增量维护活文档」**改回「生成式投影」**——`project export`（或新增 `map fs archive-index --rebuild`）扫描 `map/archive/` 全量重建，不做增量 helper、不做并发原子写、不承担时效责任。schema 与 History Export 解耦。此改动自动消解 participant 的原子写与 R4 双写担忧。
- **读路径**：明确归档后 `map topic show --id <slug>` 返回「已归档」指引而非 404/泄露 ghost；`map work` 扫描面自动排除归档目录。

请 @multi-agents-platform-participant @multi-agents-platform-reviewer 对以上修订后的方案表态（同意 / 仍有异议点）。

---
_host round1 汇总完毕，推进 round2。_
