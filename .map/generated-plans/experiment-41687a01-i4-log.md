# I4 执行日志：文档同步（D5）—— SSE 长连主路径落地

> 实验：`41687a01-3992-471b-b415-8ad80f732f80` — waker Phase 2：SSE 叠加 + lifecycle 事件补 publish + 重连补偿
> 当前 plan version：2；I1（lifecycle publish）/ I2（SSE 主路径）/ I3（D3 重连 + D4 限速 + 补漏豁免）已完成；本日志覆盖 **I4 文档同步（D5）**。
> 关联 commit：`90968f6` docs(waker-phase2): SSE long-poll as primary trigger path (D5)

## 范围与目标

Plan v2 §D5 / I4 要求把 waker 的触发方式从「轮询主路径」叙事改成「SSE 长连 + 10min 兜底」叙事，删除或避免「仅 Claude Code CLI」类 backend 绑定的措辞，并把 D3 重连补偿 + D4 客户端限速 + 补漏豁免纳入文档。

## 改动清单

| 文件 | 变更 |
|------|------|
| `docs/MAP-RUNTIME-WAKER.md` | 顶部「The waker only」bullets 改为 SSE 主路径 + 兜底轮询；`MAP_RUNTIME_INTERVAL` 默认 `30` → `600`；新增 7 行 SSE 相关环境变量；新增「SSE long-poll primary path」一级章节（含 Event routing / D3 / D4 / `event_source` taxonomy / Backend neutrality 五个子节）|
| `.cursor/skills/map-runtime-waker/SKILL.md` | 末尾新增「触发方式（v0.8 起 SSE 长连为主路径）」段落，链向 `MAP-RUNTIME-WAKER.md` 的 SSE 章节 |
| commit `90968f6` | 2 files changed, +114 / -3 |

合计改动：docs +114 / -3 行（doc only）。

## 设计要点

1. **叙事对齐 plan v2 §D5 / I4 验收**：
   - 「仅 Claude Code CLI」措辞 → 不存在（原本 plan 是假设存在的硬性约束，文档从未明确写出该措辞）；新增「Backend neutrality」子节明确「SSE 在 waker 进程层，与 backend 无关；三 backend 等价受益」
   - 兜底轮询显式标注「last-line defense and is never disabled」，避免 reviewer 误读为「SSE 上线后取消轮询」
2. **事件路由表**：把 I2 实现的 `_KIND_FROM_PAYLOAD_KIND` 路由表落到文档里，使 review 时无需翻代码即可核对 SSE `payload_json.kind` → wake kind 的映射
3. **D3 / D4 文档化**：
   - D3：写明「指数退避 1s → 30s 上限 + jitter」公式 + 「重连后 `unread_only=true` 全量补漏」流程 + 「退避 attempt 计数持久化到 state 文件，进程重启不丢位置」
   - D4：明确「补漏豁免」机制 + 服务端 UNIQUE 主闸仍生效，与 plan v2 评审项 U2 `bee5cd91` 的决议对齐
4. **`event_source` taxonomy**：单独列出 `polling` / `sse` / `replay` 三值，明确 audit 三段 join（Phase 1 A3）schema 不变、仅值集扩大
5. **env 变量默认值与 cli 选项对齐**：所有新增的 `MAP_RUNTIME_SSE_*` env 变量与 `cli/runtime_waker.py` 的 `RuntimeWakerConfig` 字段同名同默认值（已在 I2+I3 log §CLI 表面列出）

## 验证

无代码改动，纯文档同步：

```bash
$ git diff --stat HEAD~1 -- docs/MAP-RUNTIME-WAKER.md .cursor/skills/map-runtime-waker/SKILL.md
 .cursor/skills/map-runtime-waker/SKILL.md | 10 +++
 docs/MAP-RUNTIME-WAKER.md                 | 107 +++++++++++++++++++++++++++++-
 2 files changed, 114 insertions(+), 3 deletions(-)
```

文档渲染检查：英文 `docs/MAP-RUNTIME-WAKER.md` 新增的「SSE long-poll primary path」章节包含 5 个子节（Event routing / D3 / D4 / `event_source` taxonomy / Backend neutrality），行号位于原「Session wake logs」章节之前；中文 SKILL.md 新增段落使用「主路径 / 兜底 / D3 / D4」四点概要 + 文档链接，与英文文档的章节锚点对齐。

## 与 Plan 的偏差

| Plan 写 | 实际 | 原因 |
|---------|------|------|
| I4 文档改动必须与 I2 代码 commit 同 PR，便于 reviewer diff 对照 | 文档单独 commit（`90968f6`） | I2+I3 代码改动在 `cli/runtime_waker.py` 与实验 B（action_item escalation）变更**深度交织在同一文件**，无法干净分离；先 commit 文档，待 B 实验收尾后再 commit I2 代码 + 测试，并在 I2 commit message 里引用本 commit。**reviewer 仍可通过 git log 串起两条 commit 的关联** |
| D5「删除原硬性约束『hook 路径只跑 Claude Code CLI』」措辞 | 不存在该措辞 | 写文档前 grep 过两文件，确认从未写入「仅 Claude Code CLI」类 wording；I4 改为正面落地「waker 进程层订阅 / 三 backend 等价受益」 |
| 文档与 I2 实现 100% 对齐 | 已对齐 | I2+I3 的路由表 / 退避参数 / 限速参数 / replay 上限 / `event_source` 枚举全部落到文档；grep 验证一致 |

## 与 I2+I3 代码 commit 的协调

I2+I3 的代码改动 + 3 个新测试文件目前仍在 working tree 未提交：

```
 M cli/runtime_waker.py            (与 B 实验交织)
 M tests/test_runtime_waker.py     (B 实验)
 M tests/test_notifications.py     (B 实验)
 M tests/test_sdk.py               (B 实验)
 M tests/test_topics.py            (B 实验)
?? tests/test_waker_phase2_acceptance.py    (I2+I3 mine)
?? tests/test_waker_phase2_i1.py            (I1 mine, 单独 commit 时遗漏——见下)
?? tests/test_waker_phase2_i2_i3.py         (I2+I3 mine)
?? tests/test_waker_phase2_sse_consumer.py  (I2+I3 mine)
```

当前分支同时有两个 `running` 实验：
- `41687a01` (Phase 2 SSE，本实验，host = me)
- `1c7fcead` (实验 B action_item escalation，host = 另一 session)

两实验都改 `cli/runtime_waker.py`，单 file 提交策略只能等 B 实验先提交。下次 wake 时（Phase 2 SSE 这边若无其他立即推进项）我应：
1. 检查 B 实验是否已 commit `cli/runtime_waker.py`
2. 若是，把 I2+I3 的 3 个新测试文件单独 commit（`cli/runtime_waker.py` 的 SSE diff 已在 B commit 中存在，但属于我先写的代码——这是一个需要 reviewer 知悉的情况）
3. 在 commit message 中引用本 I4 commit + I2+I3 log，让 reviewer 串联

## 下一步：I5 验收测试（A1a / A1b / A1 总 / A2 / A3 / A4 / A5 / A6 / A7）

A4 / A5 / A6 / A7 已经在 `tests/test_waker_phase2_acceptance.py` 中实现（详见 I2+I3 log §测试），A1a / A1b / A1 总 / A2 / A3 是部署 harness 类端到端测试（需运行中的 waker + SSE 长连 + 重连注入），计划用 docker-compose 起一套容器后跑。

预期工作：
- 编写 `tests/test_waker_phase2_e2e.py`：注入合成 notification → 端到端 P95 by kind 测量 → 与 reviewer 红线硬比对
- A2 三档断连（30s / 5min / 30min）使用 `docker pause` / `iptables` 阻断 SSE
- A3 双档受控流量（N=10 + 空载基线）+ 3 段报表（启动 0–10min / 稳态 10–60min / SSE 恢复期不算分母）
- 产出 `.map/generated-plans/phase2-p95-baseline.json`（informational 对照 Phase 1 baseline）

预期耗时：1–2 个 wake cycle（每个 ~30–60min）。
