---
title: "advance-round ack 合规校验 v1:frontmatter 字段语义 + 名单过滤 + missing 带原因"
acceptance:
  - "A1 手写旁路被拒(双界面同源):无 frontmatter 的 round 文件(纯正文/echo 直写)在**两处一致地**列为未发言——(i) `map topic advance-round` 报错内联列出 missing 该 persona(带文件名+原因),不推进轮次;(ii) host `map work` 的 round_ack_pending 视图同口径列出待发言(带原因)。两者共用同一 `effective ack authors` 计算(见下 D4),不在 advance / work 之间制造新的判据分叉(review fa3b838b,work-vs-advance 镜像)"
  - "A2 空壳/伪造被拒:空文件、touch 变体、frontmatter 字段与文件名不一致(author 与文件名 persona 不符、round 与文件名轮次不符、posted_at 缺失/不可解析)各进 missing 且原因具体(D2 三条逐项可验证)"
  - "A3 名单外不计入:stray 的名单外 persona round 文件(如 round1-reviewer.md 而 index participants 无 reviewer)不参与 ack 满员判定,单独作为 anomaly 报告(D3)"
  - "A4 正常路径不受扰:CLI `topic comment` 写入的合规 round 文件 ack 判定照常通过(无回归,合规文件天然满足 D2 三字段与文件名一致);fast-gate 话题存量手写文件(已推进过轮次)不受追溯影响(flag 不回溯)"
  - "A5 CLI 错误信息:ack missing / 409 输出含文件名 + 具体原因(`round1-participant.md: frontmatter author=host, expected participant`),对齐 M55 actionable error 形态;work round_ack_pending detail 同格式(author/round/posted_at 逐条指认)"
  - "A6(可选)preflight 只读命令 `map fs ack-status --topic <slug>` 供 advance 前自查:实验内评估成本后裁决,不阻塞主验收"
  - "测试面:frontmatter 合规检查、字段语义校验、名单过滤、work/advance **同源一致性**(同一手写文件在 round_ack_pending 与 advance missing 中口径一致)的新增单测全绿(接入 fs_source / parser 测试);`ruff check` 通过"
evidence_keys:
  - "pytest_summary:parser frontmatter 合规 / 字段语义 / 名单过滤 / work-advance 同源四组新增单测全绿"
  - "实测输出:手写旁路文件在 `map work` round_ack_pending 与 `advance-round` 报错中一致列为未发言(A1 双界面同源);合规 comment 文件照常推进(A4)"
  - "grep 核证:CLI missing/409 文案含文件名与原因;index participants 名单为 ack 统计范围(D3);effective ack authors 单点实现被 work 与 advance 两端引用(D4)"
dependencies:
  - "主话题 fs-advance-ack-validation(8bb64d0c-7c53-578f-9bd9-c8904b7c4230)Round 2 Summary 定稿:D1 方向 1 胜出(advance 侧合规校验)/ D2 字段语义校验(participant 细化 1)/ D3 名单过滤(participant 细化 2)/ D4 修复落点(parser 层,missing 带原因)"
  - "reviewer 评审 fa3b838b(open unreasonable):A1 界面归属 + effective ack authors 同源计算——本 revision 补 D4 共享计算设计、A1 双界面明确、A5 覆盖 work 视图文案(见下方 revision 记录)"
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
| D4 | 共享计算(review fa3b838b 采纳):在 **parser 解析层**对新一轮 round 文件计算「合规亚型」——D2 三条逐项判定(author 与文件名一致 / round 与文件名轮次一致 / posted_at 显式可解析),结果为每份文件标注合规标记;`authors_in_round` 改为只统计**合规标记**文件的 author(**effective ack authors**)。`derive_work`(round_ack_pending)与 `validate_fs_advance_round`(advance 门槛)两端**共用同一标记实体**,天然同源,不存在「work 显示交齐、advance 却拒绝」的镜像分叉。hand-write/空壳/错位文件统一进 effective missing 并带原因 | 空文件/touch 变体天然被只查合规标记的判定覆盖;合规文件 = CLI comment 写出(frontmatter 有 author=文件名 persona/round=写入轮次/posted_at=UTC iso),D2 三条自然通过,零误伤 |

## 调研事实(Round 1/2 + review 核证)

| 事实 | 位置 |
|------|------|
| ack 即文件存在:`round<N>-<persona>.md` 存在即视为已发言(平台无需单独记录) | `sdk/python/map_fs/parser.py:18` |
| **解析层全程宽容**(review fa3b838b 关键核证):无 frontmatter 时 fallback——`author = c_meta.get("author") or persona`、`round = c_meta.get("round") or 文件名轮次`、`posted_at = _parse_dt(...) or mtime`——手写文件在解析层即「看起来合规」,advance/work 的 authors_in_round 天然被其撑满 | `sdk/python/map_fs/parser.py:290/293/298`(parse_topic_dir) |
| `authors_in_round` 宽容口径:`{c.author for c in comments if c.round == round_number}`(author 已是 fallback 后值) | `sdk/python/map_fs/parser.py:118-119`;server `_TopicView.authors_in_round` `server/services/fs_source_service.py:276-277` |
| participants 白名单已实现:index frontmatter `participants:` 显式声明 + 发言自动并入 | `sdk/python/map_fs/parser.py:99-116`(`TopicMeta.participants`) |
| derive_work 的 missing:`[p for p in participants if p != persona and p not in authors]`,round != ready 时记为 round_ack_pending | `sdk/python/map_fs/parser.py:577-588` |
| advance 门槛:server `validate_fs_advance_round` 用 `view.authors_in_round(view.round_number)` 算 missing → `FsAckPendingError(missing)` | `server/services/fs_source_service.py:1427-1431` |
| comment immutable 拒绝:comment_path.exists() and not overwrite → `comment file already exists (immutable convention)` | `sdk/python/map_fs/parser.py:492-494` |
| 活标本:fast-gate-allowlist-inversion/round1-participant.md 无 frontmatter 直写正文,index 已在 round2(= 计为已表态推进过轮次) | participant round1 |

## 实施顺序(建议,评审可调)

1. **I1 共享合规标记(D1+D2+D4 核心)**:parser 解析层对每份 round 文件独立判定 author/round/posted_at 三条,产出合规标记并入 FsComment(新字段或等价暴露);`authors_in_round` 改为只统计合规 author → effective ack authors;derive_work 与 server advance 校验因共用解析结果而同源(单测覆盖:同一手写文件 round_ack_pending 与 advance missing 口径一致)
2. **I2 missing 带原因 + 文案(A2+A5)**:missing/409 与 work round_ack_pending detail 逐条指认字段(`author=host, expected participant` / `round=2, expected 1` / `posted_at missing or unparseable`)
3. **I3 名单过滤 + anomaly(D3)**:ack 满员只统计 index participants;名单外文件产出 anomaly 报告(单测覆盖)
4. **I4 验证收尾(全部)**:单测 A1-A4 + A5 文案核证;A6 preflight 评估后裁决(成本小则顺手,否则注明 backlog);回归 + ruff check

## 修订记录(vplan2)

- **fa3b838b(author/round/posted_at 宽容性 + 分叉镜像)**:采纳 reviewer 全部整改点——a) A1 明确「missing 列表」指 advance 报错**与** host `map work` round_ack_pending **两处同源**(非二选一);b) D4 改为共享 effective ack authors 计算:合规判定下沉到解析层,derive_work 与 advance 共用同一标记,从机制上杜绝「work 显示交齐、advance 拒绝」的镜像分叉;c) A5 覆盖 work 视图文案格式。不采用「声明差异为已知口径」的退让路线——本话题主题就是消除判据分叉,退让等于把 comment-vs-advance 换成 work-vs-advance。

## 风险与边界

- `comment` 侧 immutable 保持不变(不反向放宽)——本实验只动 advance 侧 ack 判定
- fast-gate 存量手写文件不追溯(A4):只影响**后续** advance 判定,已推进的轮次不回溯、不删除(participant 提议保留该文件)
- 空壳 frontmatter(手写 `---\nauthor: participant\n---` 但无 posted_at)由 D2 的 posted_at 必填/可解析拦截;round 错位由 round 字段与文件名轮次比对拦截
- 修的是 sdk/python/map_fs/*(客户端 FS 解析层,parser.py 为核心),server 侧只复用解析结果(validate_fs_advance_round 的 missing 计算自动同源,无需改 server 逻辑);测试接入 fs_source / parser 测试文件
- 工作树存在其他并行未提交改动(server fs_source_service stale 排除等),本实验只 stage 自身文件,不混提交
