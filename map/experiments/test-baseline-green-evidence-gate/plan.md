---
title: "测试债清偿：主干 26 红清零 + complete 的 pytest_summary 机器校验（failed>0 拒绝 / --known-failures 豁免）+ fast-gate 存量打标"
acceptance:
  - "A1 预存红清零（v2 定稿验收基准=机器清单）：执行第一步先跑 pytest 全量收集失败基线，原始失败输出（完整用例清单）落盘 evidence——**清零验收以该机器清单为准（多退少补）**。plan 编写时手工盘点 25 项，与话题口径「26 红」差 1 属人工盘点误差，不作为验收基数：CLI format/envelope 族（9 红，同根因 JSONDecodeError Extra data：test_cli_error_envelope ×5、test_cli_format_priority ×3、test_cli_json_schema ×1）→ map_sdk_skeleton 误报（2 红，grep 误判）→ 零散三组（test_error_codes_cli ×5 / test_error_envelope_and_file_options ×5 同族、test_experiment_lock_notifications ×1、test_reject_result_misuse ×1 + test_review_list_archived_filter ×2）。清零判定：基线清单用例全绿（pytest <机器清单文件集> -q 与 CI 对照）且基线收集时未列入已知豁免的失败为零"
  - "A2 防新漂移纪律（执行约束）：修测试断言优先修「过严的断言」而非「放宽被测逻辑」——每个修复 commit message 或实验日志注明该用例属哪类"
  - "A3 evidence 机器校验：complete 提交时校验 `evidence_metadata.pytest_summary`——`failed>0 → 拒绝 complete 并提示修复`；`total 与 CI 不符 → warning`（避免单机/CI 环境差异误杀）。复用已在建的 evidence_metadata 结构，不另起炉灶。实测：造 failed>0 的 evidence completion → complete 被拒；带 `--known-failures <ref>`（引用已登记债条目）→ 放行"
  - "A4 accept-result 侧可视化：reviewer 视角对 pytest_summary 校验结果可见——红灯直接可见（complete/accept 路径展示校验结果）"
  - "A5 fast-gate 存量打标：存量真 slow/integration 用例显式打标（与 fast-gate 实验 eb291c4b 的 A4 验收口径对齐）；fast-gate 正常集仍绿"
  - "A6 probe 分口径（participant round2 补充）：`test_zz_fastgate_probe.py` 是验证探针而非被测债，与 26 红清单**分开标注**——绿的标准明确为「26 红清零 + fast-gate 正常集」，probe 只作附注，不混入验收口径"
  - "A7 顺带小洞（管道审计指派并入）：`action-item add` 对 closed 话题加校验——closed=零尾款 invariant 下拒绝或显著警告；本批 4 个 closed 话题被成功写入 open 项即为实证；补单测覆盖"
  - "测试面：evidence 校验、action-item closed 校验的新增单测全绿；`ruff check` **全绿（v3 定稿基线=当前 4 预存红一并清零：UP038 @ cli/session_wake_log.py:104 + I001×3 @ tests/test_049_project_fs_content_config.py / test_review_archived_metadata.py / test_review_item_state_closed_migration.py；I001 可 ruff --fix 自动修）**——不留执行期自由裁量，4 红进 I0 基线，清零进本实验验收"
evidence_keys:
  - "实测输出：执行首日 pytest 失败基线**原始输出落盘**（机器生成完整清单=清零对照基准；若与手工盘点 25 有出入以实测为准并注明差项）（A1）"
  - "pytest 全绿输出：机器清单用例全绿 + fast-gate 正常集（与 CI 对照），probe 仅附注（A1+A5+A6）"
  - "实测输出：failed>0 evidence 被 complete 拒绝的报错；`--known-failures` 豁免放行记录；reviewer 侧红灯可见（A3+A4）"
  - "实测输出：对 closed 话题 `action-item add` 被拒/警告（A7）+ 对应单测绿"
  - "实验日志：逐用例修复记录（所属族 + 修断言还是修源码 + 为何），防新漂移自查（A2）"
dependencies:
  - "话题 test-baseline-green-evidence-gate（ad089fdc-4f92-5dd8-8bca-b79706d21b98）close_note 口径：清零优先级、校验形态（failed>0 拒 + total 不符 warning + --known-failures 豁免）、fast-gate 顺带打标、probe 分口径（participant 补充）全部为决议"
  - "发起背景：2026-08-24 merge 6aaca4c 验收实录——验收者被迫临时 worktree 逐批对照跑（/tmp/pb*.log），这笔对照考古每次验收都要付；fast-gate 实验 eb291c4b 刚 done 而 26 红仍在，覆盖范围与修复动线有缝隙"
  - "与实验 207d7c4b（cli-hygiene-batch）衔接：其 A 系列建了 evidence 结构但未建机器校验（发起帖点名），本实验补校验侧；CLI format/envelope 族清零与其 A2（CLI 报错友好化）同域不同层，互不阻塞"
  - "与实验 plan-revision-review-gate（本批同开）在 complete 门禁路径同族触碰：本实验管 pytest_summary 校验，彼管 plan 版本核对红旗——先后落地，后者注意 rebase"
---

# 测试债清偿：主干 26 红清零 + complete 的 pytest_summary 机器校验 + fast-gate 存量打标

## 背景

话题 `test-baseline-green-evidence-gate`（2026-08-24 merge 6aaca4c 验收实录）两个叠加问题：

1. **主干 26 个预存红**——为证明 merge 零新增失败，验收者被迫建临时 worktree 在 pre-merge commit 上逐批对照跑，这笔「考古税」每次验收都要付；主干不绿使一切基于 `pytest` 结论的判断悬空（participant：我做任何一轮表态/验收都默认「主干是绿的基线」）
2. **实验 complete 的 pytest_summary 是自报的**——「测试全绿」是四门验收的暗门禁，但 evidence metadata 无机器校验；f4c0316 一边自认 5 挂一边实验照常 done

fast-gate 实验（eb291c4b）刚 done 而 26 红仍在：26 红是真实失败非 fast-gate 排除项，修白名单没用，得修源码（participant 口径 3，host 定稿 5 采纳）。

## 定稿决议（close_note + Round 2 双方表态）

| # | 决议 | 来源 |
|---|------|------|
| D1 | 26 红 P0 先清零，按族优先级：CLI format/envelope 族（9 红一个根因，直指「CLI 报错 30 行堆栈」体验债同族）→ map_sdk_skeleton 误报（2 红 grep 误判）→ 零散三组 → 两只碎红；前两组低风险理性债先行 | 双方一致（participant 口径 1 采纳） |
| D2 | evidence 校验实质化：complete 时校验 `pytest_summary`——failed>0 拒绝、total 与 CI 不符 warning（拒绝/告警分离避免环境差异误杀）；复用 evidence_metadata 不另起炉灶 | 双方一致（participant 口径 2、4） |
| D3 | `--known-failures <ref>` 显式豁免：引用已登记债条目——诚实记录而非死板门禁 | participant 边界采纳 |
| D4 | accept-result 侧 reviewer 可视化（红灯直接可见），验收闭环 | host 定稿 4 |
| D5 | fast-gate 范围：26 红是真实失败得修源码；顺带存量真 slow/integration 打标（与 eb291c4b A4 对齐） | 双方一致 |
| D6 | 防新漂移：修断言优先修「过严断言」而非「放宽被测逻辑」 | participant 边界采纳 |
| D7 | probe（test_zz_fastgate_probe.py）与 26 红分开标注，验收口径=26 红清零+fast-gate 正常集，probe 仅附注 | participant round2 补充 |
| D8 | （任务单并入）action-item add 对 closed 话题校验——管道审计实证：本批 4 个 closed 话题被成功写入 open 项，「closed=零尾款」invariant 被破 | 管道审计指派 |

## 实施顺序（建议，评审可调）

1. **I0 基线收集**（A1 验收基准）：pytest 全量实跑收集失败基线 + `ruff check` 失败基线（4 预存红）双收集，原始输出落 evidence——机器清单即清零对照基准（与手工盘点 25 的差项在此步显形并注明；ruff 4 红同入清零范围，I001×3 自动修 + UP038 一行改写）
2. **I1 CLI format/envelope 族清零**（9 红，同根因 JSONDecodeError Extra data——输出在 JSON 后多段内容）
3. **I2 map_sdk_skeleton 误报清零**（2 红：evidence.py 注释含 "from server" 被 grep 误报；_map_sdk_version 已不在 cli.main——修 grep 或修测试锚点）
4. **I3 零散三组 + 两只碎红清零**（error_codes_cli / error_envelope_and_file_options 同族 10 红、lock_notifications、reject_result_misuse、review_list_archived_filter）
5. **I4 evidence 校验**（A3+A4）：complete 拒绝路径 + warning + `--known-failures` 豁免 + reviewer 可视化
6. **I5 fast-gate 存量打标**（A5）+ probe 分口径附注（A6）
7. **I6 action-item closed 校验**（A7）
8. **I7 验证收尾**：全量清单复跑 + CI 对照 + 新增单测全绿 + ruff check

## 风险与边界

- 26 红修复面横跨 CLI/server/测试三层：每族窄 commit，commit message 注明族与修复类型（D6 自查载体）
- evidence 校验是 server 侧 complete 门禁改动：容器验证需 `docker compose build api`；与 plan-revision-review-gate 的 complete 拦截同路径，落地顺序见 dependencies
- `--known-failures` 的 ref 形态（issue/债条目引用）在实现时定最小形态：字符串 ref + 登记位置可查即可，不过度设计
- warning 不阻断 complete（total 与 CI 不符仅提示），避免单机收集数差异误杀（D2 定稿语义）

## v2 修订说明（回应评审 a3354e7b）

- 计数口径定稿：A1 验收基准从「发起帖 26 个」改为「执行首日 pytest 实跑机器清单（原始输出落 evidence，多退少补）」；手工枚举 25 项保留为编写时盘点并标注与话题口径 26 的差 1 属人工盘点误差；新增 I0 基线收集步骤与 evidence_keys 首条原始输出要求；后续步骤编号顺延


## v3 修订说明（回应评审 1e06bb6a）

- ruff 基线口径定稿（择一写死，不留自由裁量）：本实验测试面的 ruff check 全绿=当前 4 预存红一并清零（UP038 cli/session_wake_log.py:104 + I001×3 三个 tests 文件）——I001 走 ruff --fix 自动修，UP038 单行改写；4 红并入 I0 基线收集与清零验收，不做豁免登记。理由：契合「测试债清偿」主旨，124e9a00 结果审批亦建议本实验一并清点
