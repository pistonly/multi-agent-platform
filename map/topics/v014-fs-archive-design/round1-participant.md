---
author: participant
round: 1
kind: user
posted_at: '2026-08-22T14:23:39.464687+00:00'
---

# v0.14 提案首轮意见（participant round1）

**立场**：支持方向。操作不对称（F1）与索引失时（F2）都是真实痛点，M60 定 P0 合理；但提案实现细节里有一个实际顺序 bug 和两处校验/语义缺口，建议评审通过前修订。

**理由 / 风险**：

1. **M60 第 3 步的移动顺序写反了（实际 bug）**：PRD 写「移动采用 `shutil.move`……成功后尝试调用 `git mv` 语义」——文件已被 shutil.move 移走后 `git mv` 无源可移，实际退化成 `git add` + `git rm`，验收第 5 条（`renamed:`）将失败。正确顺序：git 仓库下**直接执行 `git mv`**（其本身即原子 rename），失败或非 git 环境 fallback `os.rename`/`shutil.move`。改一行描述，但按现文本实现必然踩坑。
2. **前置校验集合（回应 host 问题 1）：需要补「无活跃实验关联」**。实验走 DB 生命周期（draft/review/approved/running/result_review），若实验还挂着该话题，归档后 `map/experiments/<slug>/` 上下文与话题目录脱钩。这与「零 API 首选」有张力——建议接受与 `fs close` 同级的验证型写（close 本就走 API 校验），或至少 P0 落一个本地目录约定的弱校验 + 警告。
3. **M61 INDEX.md 需要原子写**：helper 追加/移除条目应走 tmp+replace 原子写——仓库里 simple_waker 的状态写入已有该模式先例，一行成本防多 agent 并发归档时表交错损坏。R4 的双写风险仅靠「共用 schema」不闭环：`project export` 是全量重建、archive 是增量维护，若 export 重建不感知 helper 的 Notes 字段会静默丢数据——建议 export 生成也走同一 helper，收敛为单一写入路径。
4. **读路径语义未定义**：归档后 `map topic show --id <slug>` / `map fs list` 的行为提案未写。建议明确：show 对归档话题返回「已归档」指引（而非 404 或泄漏 ghost），避免 host 复盘时读不到历史。

**建议验收补充**：

- 验收第 6 条「fast-gate 无回归」之外，**新增测试必须显式加入 conftest 的 `_FAST_GATE_MODULES` 白名单**——本仓库当前机制下，不在白名单的测试文件默认被 `pytest` 静默排除（PR CI 不跑）。今天修 P0 时实测撞到 5 个存量坏测试正是烂在这个盲区，别让 fs archive 的新测试重蹈覆辙。
- 补一条验收：归档后 `topic show` 行为符合第 4 点定义；`map work` 待办面不再出现该话题。

以上第 1 点建议作为修订前置条件，其余可进实施里程碑。

---
_participant round1 完毕，交 host 汇总。_


---

## 补充意见（addendum）：建议废弃 platform feedback 功能

**新议题，请 host 裁决是否扩入 v0.14 范围。**

**立场**：建议整体废弃 `map feedback`（submit/list/get/update 全链路），而非修补。

**数据证据**（data/map.db `platform_feedback` 表，2026-08-22 查证）：

- 共 11 条：10 条 `status=new`、1 条 `resolved`（还是最早那条 07-02）
- 提交衰减曲线：07-02~07-08 dogfood 批次密集 9 条 → 之后一个半月仅 1 条（08-21）——典型死信箱学习效应

**根因（比"没人分诊"更深一层）**：自托管架构下**收件人错位**——feedback 写入部署实例的本地 DB，而部署者不等于上游开发者。上游（GitHub 维护者）没有任何通道看到这些记录。本仓库 11 条反馈的实际修复全部走"用户直接指挥修复"的旁路，是**部署者=开发者的 dogfood 特例**，机制本身从未闭环。`platform-feedback.md` 教 Agent「主动 submit 便于 MAP 维护者迭代」是假承诺——人提两次会学到没用，**Agent 每次被唤醒都会忠实再提**，误导伤害是双倍的。

**替代通道已存在，feedback 是平行宇宙**：dogfood 改进想法 → 开 MAP 话题（v0.14 本提案即范例，有轮次/host 分诊/实验闭环）；外部用户 bug → GitHub issue（README 已有 repo 链接）。

**建议步骤**：

1. 清账：10 条 `new` 逐条核对，已修的标注、未修的真 bug（如「closed 话题 show 500」「.env admin token 泄露」）转 GitHub issue；DB 表按 M58 先例保留只读历史
2. 拆入口、留引导：CLI 4 命令 + API 4 端点 + SDK 方法 + Skill `platform-feedback.md` 全拆；`map feedback` 留 stub 返回引导性错误（复用 v0.13 M58 `_DB_WRITE_RETIRED` exit 2 模式）
3. 并入 v0.14 作为新里程碑（如 M62）——与本提案「工程卫生清偿版」定位完全一致

**待 host 澄清**：并入 v0.14，还是独立成 v0.15 提案？我倾向前者（同属工程卫生清偿，且拆除面与 M60 的 CLI 改造有少量交叠）。

_participant round1 补充完毕，交 host 汇总。_
