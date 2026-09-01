---
author: host
round: 1
kind: user
posted_at: '2026-08-30T22:51:04.211119+00:00'
---

# T4：skill 分发热自检——运行中源漂移的检测与重同步

## 现象（实测 + 源码考证）

waker 的 runtime home（`.map/claude-runtime-home-<persona>/.claude/skills/`）持有 `.cursor/skills/` 的分发副本，被唤醒的 agent 读的是副本。考证现状：

- `sync_runtime_skills`（cli/wake_backend.py:155）做全量镜像同步（rmtree + copytree + 清孤儿），机制本身彻底；
- **但只在 waker 启动时调用一次**（cli/simple_waker.py:1276），运行期间源 skills 更新不会热同步——本战役 T1 实验改了 `.cursor/skills/`，运行中的 waker 全程持有旧副本，靠监督者验收后人工重启才生效；
- 同步动作无任何日志/审计留痕，漂移发生过也无法事后察觉。

## 任务

1. **运行中漂移自检**：waker 循环内以廉价方式周期校验源 `.cursor/skills/` 与 runtime home 副本的差异（如 mtime+大小快检，不一致再 hash），发现漂移则自动重同步（复用 sync_runtime_skills）并在 waker 日志留审计条目（含漂移 skill 名单）；重同步失败则告警不阻断轮询。
2. **启动同步留痕**：启动时的同步结果（同步/跳过原因、skill 数）写入 waker 日志。
3. 窄提交白名单：`^cli/`、`^tests/`。

## 机器可判验收要求

```bash
# 1. 新增回归测试（必须）：
#    - 运行中改动源 skill 文件 → 下一周期（或可配置周期）检测漂移并重同步，日志有审计条目
#    - 副本与源一致 → 零开销快检通过，无多余 copytree
#    - 重同步失败（如目标目录只读）→ 告警日志 + 轮询不中断
# 2. 全量测试绿（基线 1782 passed / 2 skipped / 359 deselected，只增不减，0 failed）
.venv/bin/python3 -m pytest tests/ -q
# 3. tests/test_simple_waker.py 既有 busy/签名去重测试不回归
# 4. git diff --name-only 白名单：^cli/、^tests/
```

## 边界

- 不改 sync_runtime_skills 的全量镜像语义与孤儿清理行为，只加触发时机与留痕。
- 自检周期可配置（env），默认不得高频到影响轮询（建议 ≥ 5 分钟或每 N cycle）。
- 不引入新的 server 端点/DB 字段；纯 waker 本地行为。
- 涉及 cli/ 改动，验收通过后由监督者重启 server 与 waker 生效。
