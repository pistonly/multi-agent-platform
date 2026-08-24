# project 归档规划（仅规划语义，3b7c2b44 A6 P2-1 不落地代码）

> 本文是 **规划文档**：描述 project 级 archive 应采取什么语义。**本实验不实现
> 任何代码**（P2-1「只做规划语义」），避免与在途 retired-surface 清理实验重叠
> 改动面；落地时以本文为验收基准的语义契约。

## 背景

现有状态：
- `projects.archived_at` 字段已存在（`PATCH /api/v1/projects/{id}` 的
  `archived` 字段可置位），但归档**无副作用**——不改变 workspace/topics 的
  扫描可见性，也不提示受影响范围。
- topic 级归档已落地（v0.14 M60/M61 `map fs archive --topic <slug>`，索引经
  rebuild 达成、可 `--undo` 无损还原）。project 归档应复用同一套「可逆、非
  静默」的设计原则。
- workspace 唯一归属硬约束（3b7c2b44 A5）上线后，「archive 换主」成为存量双
  归属的标准处置路径之一——archive 需真正释放 workspace 扫描键位才有意义。

## 规划语义

### 1. dormant 状态

project `archived_at` 置位 → 进入 **dormant**：
- 不物理删除任何数据（topics / experiments / agents / decisions 全部保留）。
- 该 project 的 workspace 从活跃扫描面退出：`fs status` / waker / 投影扫描
  不再轮询其 workspace_path。
- **联合键语义**：dormant project 的 `workspace_path + content_root` 释放给
  新 project 认领？——**否**。为避免「归档即割让」的静默数据歧义，archive 时
  必须**显式声明**（见 §2 dry-run）当前 workspace 上的受影响 topics；未处置
  完即 archive 应被拒绝或要求追加声明。这样既保留 A5 处置路径，又不静默。

### 2. dry-run 列出受影响 workspace/topics

落地时 `map project archive --id <uuid> --dry-run` 应先于真实 archive：
- 列出该 project 的全部 topics、experiments、agents 数量与 UUID 前缀。
- 列出被其 workspace_path 覆盖的 FS 话题目录（`map/topics/<slug>/`），标注
  uuid5 派生 id——即 archive 后扫描可见性会从哪些目录消失。
- dry-run 只是提示，不改变任何状态（对标 `map fs archive` 的 index rebuild
  预览）。

### 3. unarchive 无损

`archived_at` 复位 → 还原：
- **uuid5 id 稳定**：话题 id 由 slug 派生，不因 archive/unarchive 变化；归档
  前后的 FS 引用（实验 `--topic-id`、决策锚点、导出）全部继续有效。
- **仅切扫描可见性**：dormant 只是「活跃扫描不出来」，不是数据被移走；还原后
  重新纳入扫描，无需索引重建（topic 级归档的 rebuild 不适用——project 没有
  独立索引，可见性由 workspace 扫描门控）。

## 落地步骤（本实验不执行，留给后续）

1. `map project archive` 命令（dry-run 必需 + `--yes` 确认档）。
2. dormant 的 workspace 扫描门控（fs_source_service / waker projection）。
3. A5 文档 `docs/WORKSPACE-UNIQUENESS.md`「archive 换主」路径改为指向真正可
   用的 `map project archive`。
4. 单测锚定：archive 前后 uuid5 id 不变、扫描可见性切换、dry-run 零副作用。

## 明确不做

- 不物理删除 topics/experiments（沉淀结论保留）。
- 不做「archive 自动割让 workspace」——必须显式处置。
- 本实验（3b7c2b44）不落任何 project-archive 代码；仅以上规划 + A5 处置文档。
