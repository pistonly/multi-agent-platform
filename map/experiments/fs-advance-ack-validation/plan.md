---
title: "advance-round ack 合规校验 v1:frontmatter 字段语义 + 名单过滤 + missing 带原因"
acceptance:
  - "A1 手写旁路被拒:无 frontmatter 的 round 文件(纯正文/echo 直写)→ `topic advance-round` 该 persona 进 missing 列表(带原因),轮次不推进(D1 主验收)"
  - "A2 空壳/伪造被拒:空文件、touch 变体、frontmatter 字段与文件名不一致(author 与文件名 persona 不符、round 与文件名轮次不符、posted_at 缺失/不可解析)各进 missing 且原因具体(D2 三条逐项可验证)"
  - "A3 名单外不计入:stray 的名单外 persona round 文件(如 round1-reviewer.md 而 index participants 无 reviewer)不参与 ack 满员判定,单独作为 anomaly 报告(D3)"
  - "A4 正常路径不受扰:CLI `topic comment` 写入的合规 round 文件 ack 判定照常通过(无回归);fast-gate 话题存量手写文件(已推进过轮次)不受追溯影响(flag 不回溯)"
  - "A5 CLI 错误信息:ack missing / 409 输出含文件名 + 具体原因(`round1-participant.md: frontmatter author=host, expected participant`),对齐 M55 actionable error 形态"
  - "A6(可选)preflight 只读命令 `map fs ack-status --topic <slug>` 供 advance 前自查:实验内评估成本后裁决,不阻塞主验收"
  - "测试面:frontmatter 合规检查、字段语义校验、名单过滤的新增单测全绿(接入 fs_source / parser 测试);`ruff check` 通过"
evidence_keys:
  - "pytest_summary:parser frontmatter 合规 / 字段语义 / 名单过滤三组新增单测全绿"
  - "实测输出:手写旁路文件 advance 后 missing 含该 persona + 原因的命令输出(A1);合规 comment 文件照常推进(A4)"
  - "grep 核证:CLI missing/409 文案含文件名与原因;index participants 名单为 ack 统计范围(D3)"
dependencies:
  - "主话题 fs-advance-ack-validation(8bb64d0c-7c53-578f-9bd9-c8904b7c4230)Round 2 Summary 定稿:D1 方向 1 胜出(advance 侧合规校验)/ D2 字段语义校验(participant 细化 1)/ D3 名单过滤(participant 细化 2)/ D4 修复落点(parser 文件存在性推导处,missing 带原因)"
  - "活标本处置:fast-gate-allowlist-inversion/round1-participant.md 手写旁路文件已裁决保留 + 结题注明(不在本实验删除/追溯,对齐 A4)"
  - "与 server fs_source_service 的 stale 排除改动无代码交互(该改动属其他并行工作,不在本实验范围);advance-ack 修的是 sdk/python/map_fs/parser.py 层"
---

# advance-round ack 合规校验 v1:frontmatter 字段语义 + 名单过滤 + missing 带原因

## 背景

`topic comment` 与 `topic advance-round` 对同一物理事实(round 文件已存在)判据矛盾:comment 按 immutable 约定拒绝手写预写文件,advance 却把「文件存在 = 已表态」计为 ack 满员并推进轮次——出现无 audit 的轮次推进(实测于 v0.15 立项现场,已按 FS rollback 回退)。极端变体 `touch round1-participant.md` 空文件同样通过。平台「写路径全审计」承诺被打破。

participant 补充关键视角:触发人群不止 host——「先写好内容再调 CLI」是 participant 的自然冲动(waker-heartbeat 话题有现存活标本 round1-participant.md 手写旁路文件)。

## 定稿决议(Round 2 Summary,D1-D4 + participant 两条细化)

| # | 决议 | 说明 |
|---|------|------|
| D1 | 方向 1 落地:advance 侧 ack 若要满员,round 文件须含规范 frontmatter(author/round/posted_at),无 frontmatter 视为未发言进 missing | 方向 2(comment 侧接管草稿)不作主路径:单一写路径确定性优先,「哪些文件是 CLI 写的」一旦有灰区,审计链信任基础整体退化 |
| D2 | 字段语义校验(participant 细化 1):不只查 `---` 存在——author 与文件名 persona 一致、round 与文件名轮次一致、posted_at 存在且可解析;任一不过进 missing 带具体原因 | 防空壳 frontmatter 伪造、防替他人表态、防旧轮发言挪位充数 |
| D3 | 名单过滤(participant 细化 2):ack 只统计 `index.md` participants 名单内 persona 的 round 文件;名单外 persona 同名文件不计入 ack,单独 anomaly 报告 | 既防无关文件凑数,也暴露「谁没被登记却在发言」 |
| D4 | 修复落点:`sdk/python/map_fs/parser.py` ack 文件存在性推导处加 frontmatter 解析检查;missing 列表输出带原因 | 空文件/touch 变体天然被 D2 覆盖无需单独规则 |

## 调研事实(Round 1/2 已核证)

| 事实 | 位置 |
|------|------|
| ack 即文件存在:`round<N>-<persona>.md` 存在即视为已发言(平台无需单独记录) | `sdk/python/map_fs/parser.py:18` |
| participants 白名单已实现:index frontmatter `participants:` 显式声明 + 发言自动并入 | `sdk/python/map_fs/parser.py:99-112`(`TopicMeta.participants`) |
| missing 计算:host 视角 `[p for p in participants if p != persona and p not in authors]`,round != ready 时记为 round_ack_pending | `sdk/python/map_fs/parser.py:578` |
| comment immutable 拒绝:comment_path.exists() and not overwrite → `comment file already exists (immutable convention)` | `sdk/python/map_fs/parser.py:492` |
| 现场复现:v015 立项 host 手写 round1-host.md,comment 拒 / advance 过 → 已 FS rollback 回退 | 话题 round1-host.md |
| 活标本:fast-gate-allowlist-inversion/round1-participant.md 无 frontmatter 直写正文,index 已在 round2(= 计为已表态推进过轮次) | participant round1 |

## 实施顺序(建议,评审可调)

1. **I1 frontmatter 合规检查(D1+D4)**:parser 文件存在性推导处对每份候选 round 文件先解析 frontmatter;无 frontmatter → 该 persona 未发言进 missing(带原因)
2. **I2 字段语义校验(D2)**:author/round/posted_at 三条逐项核对,原因精确到字段(`author=host, expected participant`)
3. **I3 名单过滤 + anomaly(D3)**:ack 满员只统计 index participants;名单外文件产出 anomaly 报告(单测覆盖)
4. **I4 验证收尾(全部)**:单测 A1-A4 + A5 文案核证;A6 preflight 评估后裁决(成本小则顺手,否则注明 backlog);回归 + ruff check

## 风险与边界

- `comment` 侧 immutable 保持不变(不反向放宽)——本实验只动 advance 侧 ack 判定
- fast-gate 存量手写文件不追溯(A4):只影响**后续** advance 判定,已推进的轮次不回溯、不删除(participant 提议保留该文件)
- 空壳 frontmatter(手写 `---\nauthor: participant\n---` 但无 posted_at)由 D2 的 posted_at 必填/可解析拦截;round 错位由 round 字段与文件名轮次比对拦截
- 修的是 sdk/python/map_fs/parser.py(纯客户端 FS 解析层),不涉 server 状态机;测试接入 fs_source / parser 测试文件
- 工作树存在其他并行未提交改动(server fs_source_service stale 排除等),本实验只 stage 自身文件,不混提交
