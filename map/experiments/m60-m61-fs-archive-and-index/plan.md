---
title: "M60/M61 FS 归档命令与自动索引收口（v0.14）"
acceptance:
  - "M60 薄命令：`map fs archive --topic <slug>` 落地——前置校验（目录存在且含 index.md / frontmatter status: closed / 目标 `map/archive/topics/<slug>/` 不存在 / 无活跃实验关联弱校验+警告）任一不满足 exit 2 给引导不静默成功；移动在 git workspace 下直接 `git mv`（前置执行，本身即原子 rename），非 git fallback `os.rename`；`--undo` 为反向三步薄层（archive 目录存在 & 目标不存在 & 归档 index 非 open），**硬约束：archive/undo 不承载任何索引维护逻辑**（索引一致性一律经 M61 rebuild 达成）；成功文案输出指引并自动触发一次 M61 rebuild"
  - "M60 实验关联弱校验口径写实：本地 grep `map/experiments/**/*.md` 中对该 slug 的引用，命中输出警告（列出引用文件）但不阻断；明确不承诺 DB 级完备性（实验 phase 在 DB，零 API 边界下不可见），帮助文案说明完整校验需人工核对"
  - "M61 生成式投影：`map fs archive-index --rebuild` 命令扫描 `map/archive/topics/` 全量重建 `map/archive/INDEX.md` Topics 表；双形态解析（新形态 `<slug>/` 目录读 index.md frontmatter、旧形态 export 标题命名单文件读原头部字段）；**单一扫描函数**（rebuild 命令与 fs archive 成功后的自动调用共用同一实现，入口多个、生成逻辑一份）；不做增量 helper、不做并发原子写（rebuild 落盘顺手 tmp+replace 作卫生习惯，非验收项）；schema 与 `project export` History Export 解耦（export 行为不动）"
  - "M61 首跑验收（R-b）：rebuild 首跑后 INDEX.md 含新旧两类条目且不重复、表结构完好；现状 F2 失时（2 条 `Status: open`）被修复为实际状态——首跑即修复索引失时，一举两得"
  - "M61 一致性验收（R-c-②，替换 PRD 原增量语义验收条目）：一次 archive → undo 循环后经 rebuild 重建验证 INDEX 与目录状态一致；多次 rebuild 幂等（重复跑不产生重复行/不破坏表头）"
  - "读路径语义：归档后 `map topic show --id <slug>` 与 uuid5 入口均返回「已归档」指引（含 `fs archive --undo` 还原路径）而非 404/ghost——CLI 侧本地 fallback（FS 解析失败后查 `map/archive/topics/<slug>/` 存在性），server 端点零改动（零 API 边界）；`map work` / `fs list` / `topic list` 扫描面不出现已归档话题（plane 扫描天然排除，加回归测试防回退）"
  - "测试门禁（R-c-①，硬性验收）：新增测试文件**显式加入 `tests/conftest.py` `_FAST_GATE_MODULES` 白名单**（conftest.py:99/260 机制：不在白名单的测试文件默认被 pytest 静默排除、PR CI 不跑）；全量 fast-gate 回归通过"
  - "git rename 保真：git workspace 下 archive/undo 操作后 `git status` 显示 `renamed:` 而非 `deleted:+untracked`"
  - "`cli/commands/topic.py` `_DB_WRITE_RETIRED['archive'/'archive-undo']` 引导文案（:410-417）由手动 `mv` 提示更新为 `map fs archive --topic <slug>` 指引；`map fs --help` 命令族说明同步"
  - "PRD v0.14 回写定稿标注：M61 从「自动维护活文档」改为「生成式投影」、M60 移动顺序修正（git mv 前置）、undo 硬约束、M61 入口定稿（独立 rebuild 命令 + archive 自动调用）、评审话题 round2 定稿裁决 1–5 逐条对应里程碑条目带已决标注"
evidence_keys:
  - "CLI 实测：对已 closed 话题 archive 成功（目录移动 + git renamed:）；未 closed 拦截 exit 2；--undo 还原；实验引用弱校验警告输出"
  - "rebuild 首跑输出：INDEX.md 含旧 export 单文件 2 条 + 新目录形态条目、无重复；原 `Status: open` 失时条目已修复"
  - "archive → undo → rebuild 循环后 INDEX 与目录一致；rebuild 重复执行幂等"
  - "归档话题 show（slug 与 uuid 入口）返回已归档指引的输出记录；map work / fs list 无归档话题"
  - "新测试文件在 _FAST_GATE_MODULES 白名单的 diff + pytest fast-gate 全量通过输出"
  - "PRD v0.14 定稿标注 diff；topic.py 引导文案更新 diff"
dependencies:
  - "v0.14 提案评审通过（话题 v014-fs-archive-design round2 双方全盘同意、零阻塞，host 已标 ready；定稿裁决 1–5 见 round2-host.md）"
  - "M58 先例：位置即状态（归档=目录移动）、话题域 DB 写路径已退役（引导错误模式 `_DB_WRITE_RETIRED` 可复用）"
  - "现状核证（2026-08-23）：`map/archive/INDEX.md` 为 2026-08-14 export 快照、2 条 Status: open 失时；`map/archive/topics/` 下 2 个旧形态标题命名单文件；`cli/commands/fs.py` 已有纯文件命令（list/show/work）与验证型写框架（validated_write_flow:418）两类模式"
---

# M60/M61 FS 归档命令与自动索引收口（v0.14）

## 目标

按 docs/prd/v0.14.md 及评审定稿（话题 v014-fs-archive-design round2）落地：**M60** `map fs archive` 薄命令（消除与 `fs close` 的操作不对称，F1）；**M61** 归档索引改为**生成式投影**（`archive-index --rebuild` 全量重建，F2）；补齐归档后读路径语义。工程卫生清偿定位，零 API、不复辟 DB 标志位。

## 评审定稿 vs PRD 原文（关键差异，评审已决）

| 议题 | PRD 原文（proposal） | 定稿（round2 全票） | 依据 |
|------|---------------------|---------------------|------|
| M61 索引模型 | 自动维护活文档（增量 helper + 原子写 + export 共用 schema） | **生成式投影**：全量重建，不做增量/并发原子写/时效承诺 | reviewer round1 核心异议（红旗判据），participant round2 撤回原子写诉求 |
| M60 移动顺序 | `shutil.move` 后尝试 `git mv` 语义（顺序 bug） | **git mv 前置**（本身即原子 rename），非 git fallback `os.rename` | participant round1 第 1 点（实际 bug） |
| M60 体量 | 含索引维护、独立 --undo 增量逻辑 | **薄命令**；undo=反向校验+移回三步，禁承载索引逻辑 | reviewer round1 + host round2 裁决 R-a |
| M61 入口 | 挂 `project export` 共用 helper | **独立 `map fs archive-index --rebuild`** + archive 成功后自动调用；单一扫描函数多入口共享 | participant round2 倾向 + reviewer R-a/R-c；host 裁决采纳 |
| 验收基线 | 增量语义（不破坏表/多写不冲突） | 首跑双形态一致（R-b）+ archive→undo 循环 rebuild 一致 + FAST_GATE 白名单硬验收（R-c） | reviewer R-b/R-c、participant round1 验收补充 |

## 改动范围

| 子项 | 内容 | 落点 |
|------|------|------|
| M60-1 | `fs archive --topic <slug> [--undo]` 命令：校验集 + git mv 前置 / os.rename fallback + 弱校验警告 + 成功文案与 rebuild 自动触发 | `cli/commands/fs.py`（纯文件命令模式；复用 `_workspace()` / `_persona()` / `_require_local_topic()`） |
| M60-2 | `_DB_WRITE_RETIRED` archive / archive-undo 引导文案更新 | `cli/commands/topic.py:410-417` |
| M61-1 | 归档扫描函数（双形态解析）+ `fs archive-index --rebuild` 命令 | `cli/commands/fs.py` 或新 `cli/fs_archive.py`（保持单一实现） |
| M61-2 | `map/archive/INDEX.md` 重建 schema（Topics 表：Title/Status/Decision/Notes/File；目录形态 `<slug>/`） | `map/archive/INDEX.md`（产物） |
| 读路径 | show 的归档 fallback 指引（slug + uuid5 两入口）；list/work 排除归档回归测试 | `cli/commands/topic.py`（show 路径）、`cli/commands/fs.py`（fs_show） |
| 测试 | 新测试文件（archive 命令行为 / rebuild 幂等与双形态 / 读路径 fallback / rename 保真），**全部显式入 `_FAST_GATE_MODULES`** | `tests/test_fs_archive_command.py`（新建）、`tests/conftest.py` |
| 文档 | PRD v0.14 定稿标注回写 | `docs/prd/v0.14.md` |

## 实现顺序

1. M61-1 扫描函数 + rebuild 命令（先可独立验证：首跑直接修复 F2 失时，即 R-b 验收）
2. M60-1 archive/undo 薄命令（依赖 M61 扫描函数做成功后自动 rebuild）
3. 读路径 fallback + 扫描面回归测试
4. 引导文案 + PRD 回写
5. 测试全量入白名单 + fast-gate 回归

## 风险与边界

- **非 git workspace**：`os.rename` fallback（R3 维持 PRD 判定，功能不阻断）
- **旧形态单文件解析**：export 标题命名文件无 frontmatter，读原头部字段映射到新表；解析失败的单文件保留原行不丢弃（防数据丢失），带 Notes 标记
- **不做批量归档**（非目标维持）；**归档目录不产生 waker 待办**（退出扫描面即终态）
- v0.15 feedback 废弃提案不在本实验范围（host round1 裁决独立立项，见话题收口时 action_item）
