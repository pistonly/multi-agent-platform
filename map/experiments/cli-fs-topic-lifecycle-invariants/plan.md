---
title: "CLI/FS 不变量包：topic create 防 slug 冲突 + close 查实验 terminal"
acceptance:
  - "A1 create 不变量：`sdk/python/map_fs/parser.write_topic_index` 新增 `overwrite=False` 默认参数；目标 `map/topics/<slug>/index.md` 已存在 → 抛 `FileExistsError(\"fs topic '<slug>' already exists\")`；CLI `map topic create` 捕获并 exit != 0 + stderr 含 \"already exists\""
  - "A2 create --force 覆盖语义：传 `overwrite=True` 时保留既有评论文件（`round<N>-*.md` 全保留）+ `created_at` 不变（即使 `--force` 也禁止覆盖——`created_at` 是建话题时间戳，不可变）+ `action-items.yaml` / `comments/*.json` 保留；只更新 front-matter 可变字段（title / status / round / creator / description / participants）；`experiments` 关联列表按 append 语义合并（不重置）"
  - "A3 created_at 硬约束：构造 `map topic create --slug <已存在> --force --created-at <新时间>` 偷渡 created_at → exit != 0 + stderr 含 \"created_at is immutable\"；必须新增独立 `map topic amend --created-at <ts>` 命令才能改 — 防止未来 CLI 实现回归把 created_at 写进 --force 更新路径"
  - "A4 close 实验 terminal 校验：`sdk/python/map_fs/validation.validate_close` 新增 `experiments` 维度硬拒绝：关联实验存在 + 任一 non-terminal（phase != done/cancelled/withdrawn）→ 抛 `OpenExperimentError(\"experiment '<id>' non-terminal (phase=<phase>)\")`；无关联实验（`experiments=[]` 或字段缺失）或全部 terminal → 现状放行；不提供 `--force` 绕过（与既有 close creator/action-items 门禁对称）"
  - "A5 close 不接受 --force（与 §2 硬拒绝一致）：`map topic close` 不新增 `--force` 标志；CLI 层不引入告警 + force 放行路径——避免「话题已关 + 实验悬挂」的状态机不一致"
  - "A6 回归测试：双层（CLI subprocess + 直接调 validation 函数）覆盖 §a-§f 六个 case：(a) CLI 拒绝 无 --force / (b) CLI 覆盖 有 --force 保留语义 / (c) CLI 关闭拒绝 non-terminal 实验 / (d) CLI 关闭放行 无实验 / (e) CLI 关闭放行 全 terminal / (f) created_at 偷渡 — 全部新增到 `tests/test_topic_routing.py` 或新建 `tests/test_topic_lifecycle_invariants.py`；`tests/test_fs_validation.py`（如缺新建）覆盖 validation 层；既有 lifecycle / migration / routing 测试零回归"
  - "A7 验收：`ruff check` 通过；`pytest tests/ -q` 全绿（基线以开实验时 HEAD 为准，只增不减，0 failed）；`git diff --name-only` 落在窄白名单 `^cli/`、`^sdk/python/map_fs/`、`^server/`（如 server 侧 fs_validate_close 需同步校验）、`^tests/`"
  - "A8 边界：comment immutable 约定不动（parser.py L782 FileExistsError 路径）；close creator + action-items 既有门禁语义不动，只加 experiments 维度；DB plane 已 retire 的写路径不碰；不引入新的 wake signature / work_items kind；不影响 review accept-result 的 close_reason: experiment_done 路径（实验 b3ec2e4d 已落地）"
evidence_keys:
  - "pytest_summary:新 6 个 CLI subprocess case + 6 个 validation 直调 case，全量 pytest -q 0 failed"
  - "实测输出:(a) map topic create --slug 已存在 → exit 1 stderr 'already exists'；(b) map topic create --slug 已存在 --force → 覆盖，评论保留，created_at 不变；(c) map topic close 含 non-terminal 实验 → exit 1 stderr 'experiment <id> non-terminal'；(d)/(e) 放行场景 exit 0；(f) created_at 偷渡 stderr 'created_at is immutable'"
  - "grep 核证:sdk/python/map_fs/parser.py write_topic_index 含 overwrite 参数 + created_at 不可变分支；sdk/python/map_fs/validation.py validate_close 含 experiments terminal 校验"
dependencies:
  - "话题 cli-fs-invariants-topic-lifecycle（9100434b-498a-5c95-98c8-9d4f716d739f）Round 1+2 共识收口"
  - "既有 parser.write_topic_index 创建/覆盖合并语义（parser.py:702）；comment immutable FileExistsError（parser.py:782）作为对照参照"
  - "既有 validate_close 三类门禁（owner / status=closed / open action-items，validation.py:122-154）；本实验仅新增第 4 维度 experiments terminal，不改既有"
  - "既有 map topic create 调用链（cli/commands/topic.py:492 → cli/commands/fs.py write_new_fs_topic → write_topic_index）；保留调用点，扩展参数透传"
  - "既有 FsTopicDetailRead.experiments 字段（fs.py:74）；FsExperimentRead 已含 phase 字段（server 端 phase 实情），CLI 可读"
  - "既有 map topic close local plane 路径（topic.py:1086 → fs_validation.validate_close）；CLI 层调用点不动，只扩展 validate_close 校验"
  - "实验 b3ec2e4d 已落地的 close_reason: experiment_done 路径不动；本次新校验为前置门禁，与 close_reason 写入解耦"
  - "本次 cli/sdk/server 改动：验收通过后由监督者重启 server 与 waker 生效（无 docker 镜像 build，仅 daemon restart）"
---

# CLI/FS 不变量包：topic create 防 slug 冲突 + close 查实验 terminal

## 背景

平台仓流水线首日踩到两个状态机缺口（实测，详见话题 9100434b Round 1）：

1. **`map topic create` 静默覆盖既有话题**：`write_topic_index`（sdk/python/map_fs/parser.py:702）docstring 自述「创建（或覆盖）话题文件夹与 index.md」。监督者复用 8/29 既有话题 `reviewer-waker-notification-loop` 时，`topic create --slug <已存在>` 直接重置 front-matter（title/status/round/creator），无任何告警——评论文件还在，但话题元数据被洗。若误操作撞 slug，破坏无提示。
2. **`topic close` 不查关联实验状态**：`validate_close`（sdk/python/map_fs/validation.py:122）只拦三类——非 creator、已 closed、有 open action-items。close_reason 是自由文本。话题关联实验还在 running 时 close 也放行 → 会出现「话题已关、实验悬挂」的不一致态，本轮 T1/T2 的 `close_reason: experiment_done` 全靠 host 自律。

对照：comment 已有 immutable 防线（parser.py:782 文件已存在且 overwrite=False 抛 FileExistsError，--force 才放行）——create/close 缺同级防护。

## 任务

1. **create 不变量**：slug 已存在 → 报错拒绝（非 0 退出 + stderr 含 "already exists"）；显式 `--force` 才覆盖，覆盖时按保留语义表（见 §A2）处理
2. **close 不变量**：关联实验存在 + 任一 non-terminal → 拒绝 close；全部 terminal 或无关联实验 → 现状放行
3. **created_at 硬约束**：禁止借 `--force` 偷渡改 created_at；必须独立 `map topic amend --created-at` 命令
4. **回归测试**：CLI subprocess + validation 双层各跑一次，覆盖 6 个验收 case（§a-§f）
5. **窄提交白名单**：`^cli/`、`^sdk/python/map_fs/`、`^server/`（如 server 侧 fs_validate_close 需同步校验）、`^tests/`

## 实施步骤

### I1 write_topic_index 加 overwrite + slug 冲突检测（parser.py）

- `sdk/python/map_fs/parser.py:write_topic_index` 新增 `overwrite: bool = False` 默认参数
- `topic_dir / "index.md"` 已存在 + `overwrite=False` → 抛 `FileExistsError("fs topic '<slug>' already exists")`
- 已存在 + `overwrite=True`：进入现有保留逻辑（评论保留 / created_at 保留 / experiments 追加合并）
- `created_at` 字段在 overwrite 路径下禁止覆盖：若 CLI 调用方传入 `created_at` 参数（新增 `created_at_at: str | None = None`）且与旧值不同 → 抛 `ValueError("created_at is immutable, use `map topic amend --created-at`")`
- 不动 `write_round_comment` 既有 FileExistsError 路径

### I2 write_new_fs_topic 透传 overwrite + CLI `topic create` 加 --force

- `cli/commands/fs.py:write_new_fs_topic` 新增 `force: bool = False` 参数，透传到 `write_topic_index(overwrite=force)`
- `cli/commands/topic.py:topic_create` 新增 `--force` 标志；捕获 `FileExistsError` → exit 1 + stderr 含 "already exists"
- `write_new_fs_topic` 也接受 `created_at: str | None = None`（仅用于 amend 路径，不暴露给 topic create 普通用户）

### I3 validate_close 加 experiments terminal 校验（validation.py）

- `sdk/python/map_fs/validation.py:validate_close` 新增 `experiments` 维度校验：
  - `topic.experiments` 非空 → 检查每项 phase
  - 任一 phase 不在 `{done, cancelled, withdrawn}` → 抛 `OpenExperimentError("experiment '<id>' non-terminal (phase=<phase>)")`
  - 空列表 / 字段缺失 → 放行
- 新增 `OpenExperimentError` 异常类（在 `map_fs/exceptions.py`）
- 不提供 `--force` 绕过：CLI `map topic close` 不新增 `--force` 标志
- 不动既有 owner / status / action-items 三类门禁

### I4 CLI close 错误捕获

- `cli/commands/topic.py:topic_close`（local plane 路径）捕获 `OpenExperimentError` → exit 1 + stderr 含 "experiment <id> non-terminal"
- Server plane `FsCloseRequest` schema 同步加 experiments 字段（如 server 侧需校验）
- 不影响 review accept-result 的 `close_reason: experiment_done` 写入路径

### I5 回归测试（tests/）

- 新建 `tests/test_topic_lifecycle_invariants.py`（或追加到 `tests/test_topic_routing.py`）：
  - CLI subprocess 层 6 case：
    - (a) `map topic create --slug <已存在>` → exit != 0 + stderr "already exists"
    - (b) `map topic create --slug <已存在> --force` → exit 0 + 既有 round1-host.md 保留 + created_at 不变
    - (c) `map topic close` 含 non-terminal 实验 → exit != 0 + stderr "experiment <id> non-terminal"
    - (d) `map topic close` 无实验 → exit 0
    - (e) `map topic close` 全 terminal 实验 → exit 0
    - (f) `map topic create --slug <已存在> --force --created-at <新时间>` → exit != 0 + stderr "created_at is immutable"
- 新建 `tests/test_fs_validation_close.py`（或追加到现有）：
  - validation 层 6 case 直调 `validate_close`：
    - 重复上述 (a)-(f) 的 validation 层等价（构造 FsTopic fixture）
    - 验证 `OpenExperimentError.detail` 包含 experiment id + phase
- 既有 `tests/test_topic_routing.py` / `tests/test_fs_validation.py` / `tests/test_fs_migration*.py` 零回归

### I6 commit + log + release

- 窄 commit 白名单校验：`git diff --name-only` 落在 `^cli/`、`^sdk/python/map_fs/`、`^server/`、`^tests/`
- `ruff check` 通过；`pytest -q` 全量绿（基线 1773 passed + 新增，只增不减）
- complete log → reviewer → done
- cli/server 改动：监督者重启 server_daemon + waker 生效

## 风险与边界

- 不动 comment immutable 约定（parser.py:782 FileExistsError 路径）
- 不改 close creator + action-items 既有门禁语义（validation.py:133-147），只加 experiments 维度
- DB plane 已 retire 的写路径不碰（CLI 不引入 DB plane 改动）
- 不引入新的 wake signature / work_items kind（不动 wake.md 分发表）
- 不影响 review accept-result 的 close_reason 写入路径（实验 b3ec2e4d 已落地）
- `created_at` 硬约束需新增独立 `map topic amend --created-at` 命令（仅 spec，不在本实验实施范围内，留作 follow-up）
- `experiments` 关联列表追加合并时，需读既有 list + append 新条目 + 去重（按 id）；不重置既有 status

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
