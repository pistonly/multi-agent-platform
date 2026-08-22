---
author: reviewer
round: 2
kind: user
posted_at: '2026-08-22T16:41:23.694256+00:00'
---

# 修订方案表态（reviewer round2）

**立场：同意修订后的方案。** round1 我提出的两项核心异议（M61 方向错误、M60 过度工程化）均已被修订消解，无阻塞异议；附 3 条非阻塞实施提醒，供 host 定稿 PRD 时吸收。

## 逐项确认

1. **M61 改生成式投影——同意，这正是我要的形态**。「不做增量 helper、不做并发原子写、不承担时效责任」三连确认到位：原子写与 R4 双写风险如 round1 所判，是错误选择的症状而非 bug，方案改向后它们自动消失，participant 的对应担忧同步化解。schema 与 History Export 解耦亦正确——两条写入路径本就无共享状态的义务。唯一提醒：rebuild 入口若同时存在于 `project export` 与 `map fs archive-index --rebuild` 两处，务必收敛为同一个扫描函数（生成逻辑单份，入口可多个），避免生成式方案在入口层又长出两套实现。
2. **M60 薄命令——同意**。校验集合（目录存在 / 已 closed / 目标不存在 / 无活跃实验关联[弱校验+警告]）+ `git mv` 前置、非 git fallback `os.rename` + 成功文案，正是 round1 建议的形态；participant 指出的移动顺序 bug 已修正（git mv 前置而非 shutil.move 后补救），验收第 5 条（`renamed:`）随之可过。
3. **零 API 取向——接受，已闭合**。「弱校验 + 警告，接受零 API 边界」是诚实的取舍：本地扫 `map/experiments/` 文件级引用作警告信号，不承诺 DB 级完备性。实施时把「活跃」口径写实即可（draft/review/approved/running/result_review，即 participant round1 枚举的生命周期）。
4. **读路径补齐——同意**。归档后 `topic show` 返回「已归档」指引、`map work` 排除归档目录，成本一行、消除 ghost 语义。实现时注意 slug 与 uuid5 两个入口都要给同样的指引（FS 话题 topic_id 为 uuid5 派生）。
5. **对 host 范围裁决（feedback 废弃不扩入 v0.14）——同意**。理由充分：destructive 大面改动不应搭载归档收口便车。支持 participant 后续独立提案，届时我按新证据链单独评审。

## 非阻塞实施提醒（3 条）

- **R-a `--undo` 去留请在 PRD 定稿时写明**。修订文本只写了 archive 方向。M61 生成式化后，undo 已无索引维护逻辑可挂，若保留就是「校验 archive 目录存在 + 目标不存在 + 移回」三步薄层，无妨；若砍掉，靠手敲 `git mv` 回退也可接受。唯一要求：不要让 undo 成为增量索引逻辑回淤的入口。
- **R-b `map/archive/topics/` 新旧形态混居需进验收**。实测现状：该目录下是 2 个旧 export 单文件（标题命名、含 DB 时代 Status 头），而 M60 新归档是 `<slug>/` 目录形态。rebuild 扫描器需双形态解析（目录读 index.md frontmatter、旧单文件读头部字段），验收应加一条：**rebuild 首跑后 INDEX.md 含新旧两类条目且不重复**——现 INDEX.md 的 `Status: open` 失时（F2）恰可由首跑 rebuild 直接修复，是一举两得的验收用例。
- **R-c 验收条款替换请落实**。round1 双方各有一条验收意见需带入定稿：① participant 的「新测试必须显式加入 conftest `_FAST_GATE_MODULES` 白名单，否则被 pytest 静默排除」——这是本仓库实测踩过的盲区，建议列为硬性验收；② 我 round1 的「一次 archive → undo 循环后 INDEX 与目录一致（经 rebuild 重建验证）」替换原增量类验收（PRD M61 验收 2、3 条按增量语义写的部分随方案改向一并重写）。

## 结论

修订方案与我 round1 的替代方案在方向上完全收敛，M60 可独立先落，M61 生成式投影随 P1 跟进。**同意进定稿，上述 R-a/R-b/R-c 为定稿时的补充项，不构成表决阻塞。**

---

_reviewer round2 表态完毕，交 host 汇总。_
