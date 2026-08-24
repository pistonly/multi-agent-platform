---
title: "config 生命周期修复：project_id 对账/修复路径 + local-fs workspace 唯一归属约束（topic platform-config-lifecycle-feedback 决议 D1 + D2/D3 顺带）"
acceptance:
  - "A1 doctor 主动对账（元建议落地）：新增 `map doctor --config`（或对等 validator）扫描 `.map/` 与服务端权威对比，把分叉项列清单并逐项给修复命令；config 的 project_id 陈旧时 `persona whoami` / `fs status` 输出告警，且带 `--check`/可区分 exit code 供 CI 机器判定"
  - "A2 非破坏性修复（机器断言）：`map bootstrap --heal`（key 已存在 → 不 create，把 config 的 project_id/agent 字段回写权威值、**不碰 token**）——断言 `--heal` 前后 `agents.local.yaml` 与 server 端 token 均不变（participant round2 补充，非文档承诺）"
  - "A3 bootstrap existing-key 分流：对已存在 key 提供 `--heal` 非 create 路径；409 文案按用户意图分流（丢 token→reissue；config 陈旧→heal；想整体重做→archive+bootstrap）"
  - "A4 reissue 纠错边界：`map auth reissue` 可选 `--rewrite-config`（仅用户明确要求时一并修正 config；默认保持 token 丢失恢复语义，避免误用吊销 waker 缓存 token）"
  - "A5 P1-1 workspace 唯一归属（联合键硬约束 + 兜底告警）：local-fs project create/register 对 `workspace_path + content_root` 联合键做唯一性校验，命中即 409 并指明已属哪个 project；同一 repo 以不同 content_root 开多 project 不被误伤；校验上线前 `map doctor --config`（A1）对 workspace 双归属先行输出告警兜底，杜绝静默分裂窗口"
  - "A6 顺带决议（topic D2/D3，低优先随手修）：P4-1 `topic create --help` 泄漏占位符 → help 渲染 snapshot 测试；P3-1 CLI/skills 版本错位 → `map version --json` + 关键命令集（fs/topic/bootstrap/review）对照范围明确，不建第二张全量漂移表；P2-1 project archive 只做规划语义（dormant + dry-run 列出受影响 workspace/topics + unarchive 无损：uuid5 id 稳定、仅切扫描可见性）不落地代码"
  - "测试面：上述各项新增单测/门禁测试全绿；`ruff check` 整仓全绿（以实际复跑为准）"
evidence_keys:
  - "实测输出：config project_id 陈旧时 `map doctor --config` / `whoami` / `fs status` 告警与 `--check` exit code（A1）"
  - "实测输出：`--heal` 前后 agents.local.yaml 与服务端 token 不变断言（A2）"
  - "实测输出：bootstrap 对已存在 key 的分流行为 + 409 文案（A3）"
  - "实测输出：reissue `--rewrite-config` 行为与默认不修 config 的回归（A4）"
  - "实测输出：重复 workspace_path+content_root 的 project create 被 409 拒绝、不同 content_root 合法共存（A5）+ 双归属告警兜底"
  - "实测输出：P4-1 snapshot 测试、P3-1 版本对照范围、P2-1 规划 dry-run 语义（A6）"
dependencies:
  - "话题 platform-config-lifecycle-feedback（21a45e2a-21e2-5f4f-ba26-f2f1096d8c9c）round3 决议 D1（合并 P0-1/2/3 + P1-1）为 acceptance 主口径，participant round2 补充（token 不变机器断言、告警 CI 锚点、联合键语义）整体进 A2/A1/A5"
  - "来源：外部项目 noise-solver-expert 排查 .map/config.yaml project_id 分叉的实测反馈（P0-P4），全部已复现/已读源码证实"
  - "与在途实验 50cddb7e（test-baseline-green-evidence-gate）衔接：本实验新增单测并入同一测试面，改动后需整仓 ruff + fast suite 复跑，注意 rebase 顺序"
---

# config 生命周期修复：project_id 对账/修复 + workspace 唯一归属

## 背景

CLI v0.9.0 local-fs 形态下，`.map/config.yaml` 的 `project_id` 是平台缓存副本，但平台从不主动对账、也没有合法修复命令：`bootstrap` 对已存在 key 必 409、`auth reissue` 不修 config 且会吊销旧 token、没有 project 级 archive。用户唯一出路是手改 config。更深一层：local-fs 允许两个 project 指向同一 workspace，FS 话题 id 为 slug 派生 uuid5，无字段区分归属，按 project 聚合的操作（导出/归档/waker/权限/实验 `--topic-id`）全部二义。

平台目前是**被动校验**（只在显式 bootstrap/审批时报错）；本轮一行陈旧 id 引发完整排查，说明需要**主动对账诊断**（发起帖元建议：`map doctor --config`）。

## 定稿决议引用

| # | 决议 | 来源 |
|---|------|------|
| D1 | 合并 P0-1/P0-2/P0-3/P1-1 为一个 config 生命周期修复实验 | topic round3 Round Summary；participant round2 无反对 |
| D2 | P2-1 archive 仅规划（dry-run + unarchive 无损） | 同上 |
| D3 | P3-1/P4-1 低优先随手修（对照范围明确） | 同上 |

## 实施步骤

1. **I0 现状盘点**：核实 server 侧 project create/register 与 fs_source_service 当前是否已有 workspace_path+content_root 唯一性校验；盘点 `map bootstrap` / `map auth reissue` / `persona whoami` / `fs status` 现状行为；收集基线（快照 config 形态 + 相关测试面）。若有已实现项则标注为「已存在，补测试锚点」，避免重复造轮。
2. **I1（A1）**：实现 `map doctor --config` validator——读 `.map/config.yaml` 与 server 权威对比，输出分叉清单 + 修复命令；`persona whoami` / `fs status` 在 project_id 陈旧（或字段对不上权威）时输出告警行；`--check` 返回可区分 exit code 供 CI。
3. **I2（A2）**：实现 `bootstrap --heal`——key 已存在时跳过 create，把 config 的 project_id/agent 字段回写权威值、不碰 token；加「token 不变」机器断言测试。
4. **I3（A3+A4）**：bootstrap 对已存在 key 的 409 文案按意图分流；`reissue --rewrite-config` 可选纠错并保持默认语义，补回归测试（默认不修 config、不误用场景）。
5. **I4（A5）**：project create/register 增加 workspace_path+content_root 联合键唯一性校验（409 + 指明归属）；补「不同 content_root 合法共存」与「双归属被拒」两端测试；A1 中接入双归属兜底告警。
6. **I5（A6 + 收尾）**：P4-1 help 占位符 snapshot 测试；P3-1 `map version --json` + 关键命令集对照；P2-1 archive 规划 dry-run 语义文档化；整仓 ruff + fast suite 复跑，0 errors / 0 failed 落执行日志（以实际复跑为准）。

## 风险

- A5 唯一性硬约束可能误伤「同一 repo 多 project」——以联合键（workspace_path + content_root）缓解，不误伤不同 content_root。
- A4 `--rewrite-config` 若默认开启会继续复现「误用 reissue 吊销 token」陷阱——默认关闭，仅显式开启。
- P2-1 只规划不落地，避免与在途 retired-surface 清理产生重叠改动面。
