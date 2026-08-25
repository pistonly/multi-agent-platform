---
author: participant
round: 1
kind: user
posted_at: '2026-08-23T16:20:28.431285+00:00'
---

# Round 1 — participant 表态：merge 方向 + 实证核证三个关键事实 + 未来以 github 为跨源基线

**立场**：问题 1 主张 `git merge github/main` 保留双亲历史、不做 rebase；问题 2 已实证不存在「两侧互补/重复」歧义——fast-gate 实验两侧内容完全一致；问题 3 在途 `fs_source_service.py` 是一组温和修复+测试，可提交；问题 4 建议以 origin 收到 merge 后 push 到 github 的「单向同步」为机制；问题 5 主张纯 git 操作不需开实验。

## 问题 1（方向与方式）：merge，保留双亲历史

- 本地 main 跟踪 `origin/main`，领先 13 无分叉；分歧只在 `github/main`（19 独有）↔ 本地（28 独有），merge-base 实证为 `cef5ed3`。
- **偏向 merge 而非 rebase**：rebase 会重写本地 28 个 commit 的 SHA，与 `map/` 协作资产（大量 topic/experiment 落盘文件提到的 commit hash——如 `4b1192cc`、`207d7c4b`、`a024bbf` 等在 round/close note 中被引用）失去一致对应，审计链断裂。merge 保留双亲，后续 `git log --first-parent` 仍清晰。
- merge 本身**冲突面远小于 host 名单**：我用 `git merge-tree cef5ed3 HEAD github/main` 穷举，真实「changed in both」只有 **9 个文件**：
  - 代码：`cli/main.py`、`sdk/python/map_client/client.py`、`sdk/python/map_types/schemas/__init__.py`、`server/api/agents.py`
  - 协作资产：`map/topics/{cli-param-consistency,complete-metadata-duplication,experiment-log-phase-restriction,fs-advance-ack-validation,host-invoke-async}/index.md` 共 5 个
  - `alembic/` 零差异、`docs/MAP-SIMPLE-WAKER.md` 零差异、fast-gate 目录零差异、web/TopicPage.tsx 不在冲突列（两侧改动在 merge-tree 眼中可自动合并）。
  - 9 个中 5 个是话题 index（两侧各自推进的 FS 元数据，语义上「两侧都写的最新版」，解决时按内容取舍即可，不是真正的代码对抗）；真需手工的只有 4 个代码文件。
- merge 后让 `origin/main` 指向新的 merge commit（本地已 ahead origin 13，push 相对安全，非 force）。

## 问题 2（fast-gate 两侧关系）：实证为「同一实验、两侧已一致」，无优先级问题

- 我核证了 `git diff HEAD github/main -- map/experiments/fast-gate-allowlist-inversion/`：**输出为空**——两侧 baseline.md / log-r0.md / plan.md / review.yaml 内容逐字节一致。这是同一次实验落盘在两边的镜像（已随 release/实验归档各自提交），不是互补推进，也不存在重复内容冲突。
- 结论：合并时 fast-gate 目录不会产生冲突；worker review 文件（`result-review.md`，工作区未跟踪）是本轮评审新增，与合并无关，随在途提交一并带上即可。

## 问题 3（工作区在途修改）：`fs_source_service.py` 建议提交而非 stash

- 在途改动 = `server/services/fs_source_service.py` + `tests/test_fs_source.py` + perf baselines + `.cursor/skills/.../wake.md`。这些是 **fs stale-nudge 修复的未提交部分**（host 明言「已隔离验证」），本质是一组自洽的 bugfix+回归测试。
- 若 stash：merge 后 conflict 增多（fs_source_service 同时是冲突候选），且 stash pop 还要重新验证。若先提交：本地多 1 个 commit，28→29，merge 冲突面反而小（合并不会触碰这段刚提交的代码，因为 github 侧旧版会被新 commit 正确解析）。
- 建议顺序：**先提交 fs 修复（含相关 map/ 未跟踪实验 review 文件按需纳入或留 untracked）→ 再 merge github/main → 最后解决残余冲突**。优先级是「先本地齐整、再跨源对齐」。
- 一个提醒：`map/topics/waker-client-flag-test-debt/` 与 `map/topics/merge-github-main-into-local/` 这类我/刚写的最新 round 文件是 untracked，合并不影响它们，保持 untracked 到话题收敛再随常规 commit 落即可，不必赶在 merge 前提交。

## 问题 4（未来同步策略）：以 github 为跨源发布基线，合并后建立「内网 → 公网」单向推送一次

- 双 remote 并存是常态，问题在**对齐方向**。建议：让 `github/main` 成为跨源真相源（发布/CI/PyPI 依赖它），`origin` 是内网协作暂存。合并完成后 `git push github main`（把含 github 19 commit + 本地 28 commit 的 merge 结果推回去），两者回到同一点。
- 之后**内网为主产地、github 为分叉收敛点**：本地正常在 origin 迭代，仅在需要发布/对外时 `git merge github/main` + push github。不需要也不现实的「持续自动同步」，只需要「每次 merge/publish 前后把两边拉齐」这一个纪律。

## 问题 5（是否开实验）：不开，话题收敛后直接执行

- 四门 Rubric 视角：纯 git 操作=可回退工程动作（merge 不触碰业务逻辑分支，有 merge-base 与 diff 实据可复核）；无行为不确定性；无用户可感知的界面变更；验收=`git log`/`git diff`/test 全绿，均可由 merge 结果本身证伪。不满足「需要实验验证假设」的任何一扇门。
- 建议 host 在话题收敛、round2 Summary 对齐后直接执行；若要更稳可把「merge 后跑一次 `pytest -q` 关键子集 + `git log --first-parent` 复核」写进 topic close note 作为验收产出，而非开实验。

## 待 host 确认

- 代码冲突按我核证的 **9 文件清单**（cli/main.py + sdk×2 + server/api/agents.py + 5× topic index）推进即可，`git merge-tree` 结果可复核（/tmp 已存 the raw output）。若 host 在 round2 前自行合并，建议以该清单为冲突处理底稿。
