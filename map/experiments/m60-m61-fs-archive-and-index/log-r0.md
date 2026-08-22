# 立项日志（host）

实验从话题 v014-fs-archive-design 立项：round1 三方意见 → host 汇总修订方案 → round2 participant/reviewer 全票同意（零阻塞）→ 话题标 ready → 本实验创建。

plan 关键点：

- 含**评审定稿 vs PRD 原文差异表**（M61 生成式投影 / M60 git mv 前置 / undo 硬约束 / M61 入口独立 rebuild + archive 自动调用 / R-a R-b R-c 验收替换）
- 调研核证：`topic.py:410-417` 手动 mv 引导文案、`map/archive/INDEX.md` 2026-08-14 快照 2 条 Status: open 失时、archive/topics/ 2 个旧形态标题命名单文件、`tests/conftest.py:99/260` FAST_GATE 白名单机制
- 已 submit-review 进入 review 阶段，等 reviewer 评审

踩坑记录（日志纪律）：首次 `experiment log` 仅传 `--summary` 触发 pydantic 校验错误 `Either content_md or file_path must be provided`（slim 模式下 summary 不承载内容）；修正为 `--file` 传内容文件。
