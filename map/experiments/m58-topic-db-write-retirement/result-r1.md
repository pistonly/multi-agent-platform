# M58 结果提交（topic DB 写退役）

## summary

v0.13 F2 完整落地：话题写路径单轨化到 FS。M58a 三 Skill + 镜像切 `map fs` 指令；M58b CLI 8 命令引导性拒绝（exit 2 + fs 等价指引）、server 8 写端点 410 `topic_write_retired`（PATCH 仅放行 `archived` 归档通道）、测试批量切 DB 直插；M58c PRD v0.13 风险表回写已决结论。补跑 e2e 时发现并修复 M58d blocker：`experiment create` 对 FS 话题（uuid5，无 DB 行）404——`Experiment.topic_id` FK 退役（alembic 046）+ `project_service` 两处接 M56 三态路由。最终真实多 agent e2e 九步全 ok 完整闭环，验证全链路。

## 实施 log

- R1 完整日志与证据链：[log-r1.md](log-r1.md)（代码落地明细表、全量回归 44 failed/1036 passed 债务分类、10 处 CLI 分支面实测、server 410 实测、grep 核证）
- e2e 九步闭环 run log：[run.log](../../.map/e2e-logs/20260817T103223Z/run.log)——FS 话题 12 条评论 → host 建实验（v2 `34c99441-df81-492e-b3dc-206fbd219cc9`，FS uuid5 不再 404）→ reviewer 评审 → host approve → start --executor host → complete → accept-result（phase=done）→ archive + 话题 closed
- M58d 定向回归：`tests/test_fs_source.py` 20 passed（新增 `test_experiment_create_on_fs_topic`：FS 201 / dup 409 / ghost 404）
- dev 库迁移：045→046 已应用（`PRAGMA foreign_key_list(experiments)` 确认 topic FK 退役）；常驻 8001/8002 已重启加载新代码

## 风险

- **测试债**：全量回归余 44 failed 均为预先存在债务（frontmatter 门禁 ~25、cli.main 缺符号 ~10、map_sdk 导入 server ~3、M54 格式断言、数据环境缺话题），与本实验无关，建议单独开实验清理。
- **部署顺序**：FK 退役要求先跑 046 迁移再滚动新代码（幂等 guard 允许重复执行）；常驻实例需重启。
- **e2e 驱动对状态机的假设**：step 6 曾误期望评审提交自动 approve，已修正为显式 `experiment approve`（断言与 prompt 同步修正）；后续若有新驱动脚本需对齐该生命周期约定。
- **host session 脆弱性**：e2e 期间一条 host claude session 对任意 prompt 均 502，需 new-session 恢复——运维上遇 502 连发时应先探针再怀疑代码。

## acceptance

- ✅ F2 验收：8 个 CLI 写命令 + 8 个 server 写端点全部引导性退役（exit 2 / 410 + hint），GET 只读不回退，PATCH `archived` 归档通道可用
- ✅ Skill 单轨化：三 Skill + cli/skills 镜像无 DB 写命令残留（grep 核证），map-plugin.yaml 0.13.0 / requires 0.12
- ✅ M58c：PRD v0.13 F2 标注已落地，mention 风险行已决
- ✅ M58d（补跑新增）：FS 话题可挂实验（e2e 实战 + 回归测试三态断言）；FK 退役迁移幂等且可 downgrade
- ✅ 真实 e2e：九步全 `status=ok`，实验 done + archived，话题 closed，无残留 obligation
