# 执行日志 r1（host）：M60/M61 全部子项实施完成

## 实施内容（plan 实现顺序 1–5 全部完成）

1. **M61-1 扫描函数 + rebuild 命令**：`sdk/python/map_fs/archive.py` 新模块——
   `scan_archive_entries()` 双形态解析（目录形态复用 `parse_topic_dir` 读 frontmatter、
   legacy export 单文件读头部 Title/Status bullet）、`render_archive_index()`、
   `rebuild_archive_index()`（tmp+replace 落盘）；CLI `map fs archive-index --rebuild`
   （`--rebuild` 必传显式标志——生成式投影无其他模式）。
2. **M60-1 archive/undo 薄命令**：`archive_topic()` 前置校验（目录存在含 index.md /
   status: closed / 目标不存在）→ `_move()`（git workspace 下 `git mv` **前置**执行，
   失败即报错不静默 fallback——os.rename 会破坏 renamed: 保真；非 git `os.rename`）；
   `unarchive_topic()` 反向三步；`find_experiment_references()` 弱校验（grep
   map/experiments/**/*.md，警告不阻断）；成功后 CLI 层自动触发 rebuild。
3. **读路径 fallback**：`topic.py` `_archived_slug_hint()`（slug 直查 + uuid5 扫目录反推）
   + `_exit_not_found()` 统一出口（三处 FS 未命中路径接入）；`fs_show` 同步归档指引；
   `test_archived_topics_leave_scan_plane` 扫描面排除回归测试。
4. **引导文案**：`topic.py` `_DB_WRITE_RETIRED['archive'/'archive-undo']` 由手动 `mv`
   提示升级为 `fs archive` 指引。
5. **测试 + PRD**：`tests/test_fs_archive_command.py` 10 用例显式入 `_FAST_GATE_MODULES`
   白名单（R-c-①）；compat 快照与 dry-run 分类登记同步；PRD v0.14 定稿标注回写
   （状态 accepted + 定稿摘要 7 条 + M60/M61 节内已决修正指针）。

## 真机验证证据（evidence_keys 对应）

- **首跑 rebuild（R-b）**：`map fs archive-index --rebuild` → INDEX.md 重建为生成式投影
  格式，2 legacy 条目齐全不重复，`Status: open` 失时（F2）修复为 closed（Notes 保留
  `legacy export form (header said: open)` 痕迹）
- **archive → undo 循环（R-c-②）**：v012-ergonomics-review（closed）归档 →
  `git status` 全部 `R`（renamed:，R5 保真）→ INDEX 条目出现 → undo 还原 →
  INDEX 条目移除（v012 计 0）→ 话题回 map/topics/ 且 `topic show` 正常
- **弱校验**：v012 归档时警告检出 6 个实验文件引用（m54/m55/m56 plan/log），不阻断
- **未 closed 拦截**：v014-fs-archive-design（open）归档 → exit 2 + 「先 close」引导，
  话题未动
- **归档态读路径**：`topic show --id v012-...` 返回「已归档 + --undo 还原指引」非 404
- **fast-gate 全量**：584 passed, 0 failed（新测试 10 用例在白名单内被执行）；
  ruff 全绿；mypy --strict archive.py 绿

## git（窄 commit × 2）

- `feat(fs): map fs archive 薄命令与 archive-index 生成式投影（v0.14 M60/M61）`
  （代码 + 测试 + PRD + INDEX.md）
- `chore(map): v0.14 评审话题 round2 全票收口至 ready 并立 M60/M61 实验`
  （话题 round1/round2 发言 + 实验 plan/log）

## 环境备注（非本实验改动，供审计）

会话早期排查发现 server 进程加载 conda site-packages 旧快照（08-22 17:03 非 editable
安装遗留）导致 persona 修复不生效（FS 话题 advance-round 恒 403）；已改
`pip install -e . --no-deps` 并重启 map-server（PID 2155768，MAP_PORT=18400）。
