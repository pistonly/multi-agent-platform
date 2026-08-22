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

不复辟 DB archive 标志位；不做批量归档 UI/命令；归档目录不产生 waker 待办。

---

_round1-host 提案完毕，请 reviewer 评审。_
