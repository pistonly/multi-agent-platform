---
title: "FS 写入口双端校验：写路径前置拒收 body 自带 frontmatter + parser 读路径 anomaly 报告（不阻断读不改写）"
acceptance:
  - "W1 写路径前置校验：`map fs comment` / `map topic comment`（同源 _write_fs_comment）在写入前检测 body 自带 frontmatter（以 `---` 开头且可解析为 YAML mapping）→ **拒绝写入**（exit code 非 0），错误文案含原因 + 正确示例（正文无需自带 frontmatter，author/round/posted_at 由 CLI 生成并给一行示例）；`--force` 只豁免 immutable 约定、**不**绕过本校验"
  - "R1 读路径 anomaly 收集：`parse_topic_dir` 对每个 round 文件复用 4b1192cc 的 `_ack_error_of` 三条判定（author/round/posted_at 与文件名及语义一致），FsTopic 新增 `anomalies` 字段（文件名 + 原因 + 级别）；分级口径按 close_note——**字段存在但非法**（author 与文件名不符、round 与文件名不符、posted_at 有值但不可解析）= `invalid`；**frontmatter 整块缺失或 posted_at 缺失** = `lite`（仅提示，不与 invalid 混级）"
  - "R2 不阻断不改写：anomaly 文件的读取行为不变（现有 posted_at→mtime、round→文件名轮次的 fallback 语义保留），文件内容不被修改——脏 fixture 保留历史；anomaly 只报告不修正不静默"
  - "V1 可见性双出口：`map fs show --topic <slug>` 在输出顶部追加 anomalies 段（若非空）；新增 `map fs anomalies`（扫全工作区话题，表格列 slug/文件/原因/级别，支持 --format table|yaml|json）"
  - "E1 回归锚点：单测以 `map/topics/fs-close-action-items-lifecycle/round1-host.md`（posted_at='$ts' 实测脏值）断言——被报告为 invalid anomaly 且原因含 unparseable、读取不阻断（正文完整、posted_at fallback mtime）"
  - "测试面：写路径拒绝（body 带 frontmatter / 带非法 posted_at）与放行（正常 body）、读路径 anomaly 分级（invalid/lite 边界）、fs show 段落与 fs anomalies 输出的新增单测全绿；`ruff check` 通过"
evidence_keys:
  - "pytest_summary:写路径拒绝/放行、anomaly 分级、双出口输出、脏 fixture 回归锚点全绿(pytest_summary)"
  - "实测输出:`map fs anomalies` 列出 fs-close-action-items-lifecycle/round1-host.md(posted_at unparseable)且 `map fs show --topic fs-close-action-items-lifecycle` 不阻断、正文完整(V1+R2+E1)"
  - "grep 核证:CLI 拒绝文案含正确示例一行;--force help 文本注明不豁免 frontmatter 校验(W1)"
dependencies:
  - "话题 fs-write-entry-validation（9183e90a-e7a3-5e55-acbd-7c170a6b83b2）close_note 口径：双端校验（写路径前置 + parser 读路径兜底）、复用 4b1192cc A 系列规则、拒绝形态不静默不修正、posted_at 存在但非法才拒绝/缺失走 anomaly-lite、存量非法不改写、error 文案带正确示例"
  - "4b1192cc（fs-advance-ack-validation v3）已落 `_ack_error_of`（parser 三条判定，D2）与 ack_error/ack_valid 字段——本实验复用同一判定函数做 anomaly 源，不另写第二套规则（避免判据分叉，同 D4 精神）"
  - "实测脏 fixture：map/topics/fs-close-action-items-lifecycle/round1-host.md 的 posted_at: '$ts'（3d519184 执行期脚本旁路写入、模板未渲染）——作 E1 回归锚点，保留不修"
  - "写路径现状：sdk/python/map_fs/parser.py `write_round_comment` 生成合规 frontmatter 后将 body 原样拼接（_render_file）——body 自带 frontmatter 时产生双 frontmatter 文件，当前照收（本实验 W1 封口）"
  - "与本批 experiment-done-topic-close-event（server 通知）、ops-visibility-batch（CLI audit）无代码冲突（sdk/map_fs + cli/commands/fs.py|topic.py 为主）"
---

# FS 写入口双端校验：写路径前置拒收 + 读路径 anomaly 报告

## 背景

话题 `fs-write-entry-validation`：`write_round_comment` 只做 immutable 校验，frontmatter 由 CLI 生成——但 body（`--file`/`--body` 入参）自带 frontmatter 时照收，产生双 frontmatter 文件；手写/脚本旁路文件（实测脏 fixture：`round1-host.md` 的 `posted_at: '$ts'`）在读路径被**静默修正**——`posted_at = _parse_dt(...) or _mtime_utc(entry)`、`round = meta or 文件名轮次`，非法值无人报告，`map fs show`/`map topic show` 看起来一切正常。4b1192cc 已在 **ack 判定语境**拦住不合规文件（advance-round 报错 + round_ack_pending），但日常读取完全不感知。

## 定稿决议（close_note 口径）

| # | 决议 | 来源 |
|---|------|------|
| D1 | 双端校验：写路径前置校验 + parser 读路径兜底 | close_note decision |
| D2 | 复用 4b1192cc A 系列规则（author/round/posted_at 三机器字段判定），不另写第二套 | close_note decision |
| D3 | 拒绝形态不静默不修正：写路径拒绝带原因+正确示例；读路径 anomaly 只报告 | close_note decision |
| D4 | posted_at 存在但非法 = invalid；缺失（含 frontmatter 整块缺失）= lite | close_note decision |
| D5 | 存量非法 frontmatter 不阻断读、不改写文件（脏 fixture 保留历史），$ts 值作回归锚点 | close_note decision |
| D6 | error 文案带正确示例 | close_note rationale 附补充 |

## 实施顺序

1. **I1 parser anomaly 层**（R1+R2）：FsTopic.anomalies + `_ack_error_of` 复用 + invalid/lite 分级 + 单测（含 E1 锚点）
2. **I2 写路径前置校验**（W1）：`write_round_comment` 入口检测 body frontmatter → 拒绝；CLI 层友好报错（M55 actionable 形态）+ 单测
3. **I3 可见性双出口**（V1）：`fs show` anomalies 段 + `map fs anomalies` 命令 + 单测
4. **I4 实测 evidence 收集**：真实工作区跑 `fs anomalies` / `fs show` 落档

## 风险与边界

- body 以 `---` 开头但**不是** YAML mapping（如 Markdown 分隔线/thematic break）会误伤 → 判定必须"以 `---` 围栏包裹且可解析为非空 mapping 且含 author/round/posted_at 任一键"才算自带 frontmatter，纯分隔线放行；边界单测覆盖
- anomalies 字段新增进 FsTopic dataclass：消费方（e2e_collab/project_export 等遍历 FsTopic 的代码）不感知新字段（默认空列表），无破坏性
- `map fs anomalies` 是离线命令（同 fs list，无网络）；不含 index.md/action-items.yaml 的校验（那属于 close 门禁 3d519184 范围，不扩界）
