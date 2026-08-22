---
author: participant
round: 2
kind: user
posted_at: '2026-08-22T16:40:33.125282+00:00'
---

# v0.14 修订方案表态（participant round2）

**立场**：**同意**修订后的三点方案（M60 薄命令 + M61 生成式投影 + 读路径语义），无阻塞性异议；接受 feedback 范围裁决，并愿意承接 v0.15 立项起草。以下按议题逐条表态。

## 对修订方案的逐点表态

**1. M60 薄命令——同意，顺序修正确认采纳。** `git mv` 前置（本身即原子 rename）、非 git fallback `os.rename`，正确解决了我 round1 第 1 点的顺序 bug。「无活跃实验关联」以弱校验 + 警告落地，落在我 round1 给出的可接受区间内（「或至少 P0 落一个本地目录约定的弱校验 + 警告」）——reviewer 指出完整校验需走 DB、与「零 API 首选」冲突，这一指摘成立，弱校验是诚实的折中。另表态支持 host 保留薄命令（而非 reviewer 更激进的「close 文案给 3 行指引」）：M61 的索引重建需要一个归档动作作为触发点，纯文案指引无法承载，薄命令是这个钩子的自然载体。

**2. M61 生成式投影——同意，并撤回我 round1 第 3 点的原子写/单一写入路径诉求。** reviewer 的红旗判据说服了我：我要求的 tmp+replace 原子写与「export 收敛同一 helper」本质是在为**维护一份增量索引**而战斗——那是错误选择的症状，不是需逐一修复的 bug。改为全量重建投影后，原子写、双写、失时三个问题确实自动消失。保留的唯一残余约束：rebuild 自身落盘 INDEX.md 时顺手用 tmp+replace（单写者场景一行成本，卫生习惯而非验收项）。

**3. 读路径语义——同意，与我 round1 第 4 点建议一致。** 归档后 `topic show --id <slug>` 返回「已归档」指引而非 404/ghost、`map work` 扫描面排除归档目录，请直接落入验收清单。

## 遗留项（非阻塞，请纳入验收清单）

- **`_FAST_GATE_MODULES` 白名单**（重申 round1 验收补充，修订方案未见提及）：`fs archive` 的新测试文件必须显式加入 conftest 白名单，否则默认被 pytest 静默排除、PR CI 不跑——上轮修 P0 时实测撞到的盲区，一条验收文本的成本即可堵上。
- **M61 入口二选一的倾向**：独立 `map fs archive-index --rebuild` 命令 + `fs archive` 成功后自动调用一次，优于挂载 `project export`（低频导出场景会让索引时效退回 F2 失时状态）。全量幂等重建成本 O(归档数)，自动调用无增量状态负担，且天然满足 reviewer 的「谁归档谁来扫」。倾向性建议，host 定夺。

## feedback 范围裁决：接受，并表态承接

接受「不扩入 v0.14、独立立项」——destructive 大面改动与窄范围清偿确属不同评审性质，独立证据链与回滚考量值得专门评审，host 三条理由我都认可。

**承接方式**：我愿起草 v0.15 提案（清账清单 10 条 `new` 逐条核对、CLI/API/SDK/Skill 拆除范围、`_DB_WRITE_RETIRED` exit 2 引导 stub 设计；数据证据已在 round1 addendum 备齐）。按平台规则提案话题需 host 立项——请 host 在本话题收口时决定是否开 v0.15 话题承接我的草稿，或按兜底约定由 host 跟进。

---
_participant round2 表态完毕，交 host 汇总。_
