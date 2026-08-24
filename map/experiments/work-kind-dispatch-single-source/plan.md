---
title: "kind 分发单一真相化：server KINDS registry → 渲染 wake.md 分发表 → CI 逐行一致校验；方向 A（map work kinds）作过渡"
acceptance:
  - "A1 server KINDS registry：kind 名 + 清理动作 + **归属 Skill** + **note（人类注释/主观标准）** 四字段（Skill 列为必需——没有它 agent 知道动作却不知道读哪个 persona Skill，仍然断链；note 字段承载现 wake.md 表行内的人类注释，如 mentions 功能保留说明、FS/DB 分支说明——注释不留在 md 手写，全部迁入 registry 单一真相；可空）；registry 定义放 server 侧、与产 kind 的代码同一处，CLI/md 全部消费之——杜绝在 cli/ 另放一份的次生人工同步"
  - "A2 方向 A 过渡：`map work kinds`（或 `--explain <kind>`）运行时序列化输出 registry，立即可用；wake.md 对应行先静态引用该命令输出"
  - "A3 CI 逐行一致校验（v2 定稿比对口径）：wake.md 分发表整块由生成标记包裹（`<!-- BEGIN:kind-dispatch (generated: map work kinds --format md) -->` … `<!-- END:kind-dispatch -->`），块内表行目标形态 = registry 四字段渲染的四列表（| kind | 清理动作 | 下一步 Skill | 说明 |）；CI 比对口径 = **标记块内整行 diff**（`map work kinds --format md` 输出 vs 块内内容逐字符一致），块外人类补充不参与比对。participant round2 补充采纳——**方向 A 过渡期的 manual 同步窗口即纳入 CI diff**，不必等 B 渲染管道完全就绪才生效"
  - "A4 漂移封口实测：新加一个 kind（测试桩）→ 不改 wake.md 时 CI fail；补改后 CI pass"
  - "A5 新 kind 落地 checklist：进 experiment-host/handler 文档且为强制项——「新增 kind 必须同时改 registry + 渲染 + 测试」，CI 封住「只改 server 不改表」"
  - "A6 不过度机械化：清理动作里「按内容选」等需 agent 判断的条目，registry 只给「动作类别 + note 主观标准」，不假装可枚举（registry 设计约束，非验收脚本项；note 字段即 D6 的承载位）"
  - "A7 端到端冒烟（v2 定稿等价验收路径，不受 obligation 时序卡死）：首选真实动线——实验执行期自然出现 obligation 时按表清理并记录；**等价替代（规范路径，无自然 obligation 时必走）**：host 主动构造一条 mentions obligation（如 topic comment @ 自己产 mention 通知）→ 按 wake.md 表行选清理动作 `map mention dismiss` → 验证该项从 `map work` 消失 → 实验日志记录完整动线（kind → 表行 → 清理动作 → 消失证据）；不等待、不空转"
  - "测试面：registry 渲染 / CI diff 逻辑（标记块定位与整行比对）/ `map work kinds` 输出的新增单测全绿；`ruff check` 通过"
evidence_keys:
  - "diff 输出：`map work kinds --format md` 输出与 wake.md 标记块内逐行一致（CI 内 diff 通过记录）（A3）"
  - "两次 CI 运行记录：测试桩 kind 不改 wake.md → fail；补改 → pass（A4）"
  - "grep 核证：wake.md 分发表含「归属 Skill」与「说明」列且在生成标记块内；checklist 强制项落位 experiment-host/handler 文档（A1+A5）"
  - "实验日志：端到端冒烟动线（kind → 表 → 清理动作完成 → work 列表消失）（A7，真实或构造路径均需记录动线与消失证据）"
dependencies:
  - "话题 work-kind-dispatch-single-source（6a2647de-d74f-52c9-8d84-26e823807f17）close_note 口径：方向 B 为主 + A 过渡、registry 与产 kind 代码同源（server 侧）、保留 Skill 列、checklist 强制项、不过度机械化——participant 两轮表态无异议，附补充（A 过渡期 manual 窗口纳入 CI diff）已吸收为 A3"
  - "触发背景：stale_open_topics 增加 FS 语义时（1b605e0b）必须记得手动同步 wake.md 分发表，忘了则 agent 对着表找不到清理动作，义务变死循环；action_items 断链（3d519184 修复中）同根——Skill 约定与 server 能力的 drift 没有机器防线"
  - "既有手维护表：.cursor/skills/map-project-collab/references/wake.md:12-28；人工同步先例 commit c381784；UI 标签第二处 cli/wake_backend.py TODO_BUCKET_UI_LABELS——三处一致性纳入 registry 消费面评估（实现时核对，不强行并入验收）"
  - "与本批其他实验无代码冲突（server registry + cli work 命令 + wake.md + CI 配置）"
---

# kind 分发单一真相化：server KINDS registry → 渲染 wake.md 分发表 → CI 逐行一致校验

## 背景

话题 `work-kind-dispatch-single-source`：wake.md 的 kind→清理分发表是手维护的第二真相源——server 加 kind 要人肉同步文档，忘了就 drift。participant 强相关体感：每次被唤醒第一动作就是读这份表选清理动作，表与 server 实际产出 drift 时，「就把 obligation 当成功做了或当死循环跳过」（e2e-ai-gate 失效快照上已踩过）。分发表、TODO_BUCKET_UI_LABELS（cli/wake_backend.py）、server 产 kind 三处之间没有任何一致性校验。

## 定稿决议（close_note + Round 2 双方表态）

| # | 决议 | 来源 |
|---|------|------|
| D1 | 方向 B 为最终态：server 维护 `KINDS` registry（kind 名 + 清理动作 + 归属 Skill）→ 渲染 wake.md 分发表 → CI 校验「分发表行 == registry 渲染结果」。漂移从运行时静默失败变成 CI 期错误 | 双方一致（participant round1 立场采纳） |
| D2 | registry 与产 kind 代码同源：定义放 server 侧同一处，CLI/md 全部消费——单真相定义一次，杜绝二次手写（避免 cli/ 另放一份的次生同步债） | participant round1 边界 1 采纳 |
| D3 | 方向 A 作 B 的低成本过渡：`map work kinds` / `--explain <kind>` 运行时输出立即可用，wake.md 对应行先静态引用；B 管道就绪后切换，两者不冲突 | 双方一致 |
| D4 | 保留「下一步 Skill」列为 registry 必需字段 | participant 口径 3 采纳（必需） |
| D5 | 新 kind 落地 checklist 进 handler 文档且为强制项：新增必须同时改 registry + 渲染 + 测试 | participant 口径 4 采纳 |
| D6 | 不过度机械化：主观判断条目只给动作类别 + 主观标准 | participant 边界 2 采纳 |
| D7 | A 过渡期的 manual 同步窗口即纳入 CI diff，作验收第一步，不等 B 管道就绪 | participant round2 补充采纳 |
| D8 | 比对口径定稿：registry 四字段（kind/action/skill/note）——现表行内人类注释（mentions 功能保留说明、round_ack/pending_round_acks 的 FS/DB 分支说明等）与主观标准**全部迁入 note 字段**，md 内不留手写注释；wake.md 表整块放生成标记（BEGIN/END:kind-dispatch）内，CI 对**块内整行**做逐字符 diff（四列渲染：kind/清理动作/Skill/说明），块外内容不参与比对——注释不丢、比对可机械判定 | 评审 item 9d0bf5d8（v1 计划评审）修订定稿 |
| D9 | A7 冒烟等价路径定稿：无自然 obligation 时主动构造（topic comment @ 自己产 mention → 按表 dismiss → work 列表消失），构造动线与真实动线同为合格 evidence——验收不被外部时序卡死 | 评审 item 8d5c1ab2（v1 计划评审）修订定稿 |

## 实施顺序（建议，评审可调）

1. **I1 server KINDS registry**（A1）：四字段结构 + 与产 kind 代码同源落位 + server 单测
2. **I2 方向 A 输出**（A2）：`map work kinds` / `--explain <kind>` CLI 序列化（含 `--format md` 渲染形态）
3. **I3 CI 逐行一致校验**（A3+D7+D8）：标记块整行 diff 进 CI；测试桩 kind 双向验证（A4）
4. **I4 wake.md 切换引用/渲染**（B 管道）：分发表由渲染生成，保留 Skill 列，现有行内注释迁入 registry note
5. **I5 checklist 文档**（A5）+ 端到端冒烟记录（A7，真实或构造路径）

## 风险与边界

- registry 落位若发现「产 kind 的代码」分散多处（server 多 service 产 kind），先收敛枚举点再放 registry——若收敛成本超一个实验，registry 先覆盖 wake.md 表所引 kind 子集，其余记日志后续补（评审可调）
- TODO_BUCKET_UI_LABELS 第三处一致性：本实验以 registry 消费面覆盖评估，不强行并入验收（见 dependencies）
- wake.md 是 Agent 运行时依赖文档：渲染/引用切换需保持表的可读性（Agent 消费），不为机器校验牺牲人/LLM 可读——note 列保留人类可读措辞，不压缩成编码串
- 生成标记块方案的风险：块边界被人为挪动/删除 → CI 找不到块即 fail（缺块视为漂移，fail-safe 而非静默跳过）

## v2 修订说明（回应评审 457f5aa8）

- item 9d0bf5d8：A3 定稿比对口径（标记块内整行 diff、四列表形态），A1 registry 增 note 字段承载人类注释与主观标准，D8 定稿，I2/I3/I4 与 evidence_keys/测试面同步
- item 8d5c1ab2：A7 定稿等价验收路径（构造 mention → 按表 dismiss → work 消失，动线+消失证据进日志），D9 定稿，不依赖自然 obligation 时序
