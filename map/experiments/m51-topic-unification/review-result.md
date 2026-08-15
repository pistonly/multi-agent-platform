# 结果评审：M51 话题事实源收敛

## 总体评价

验收证据完整：三种 `--id` 形式（slug / FS uuid5 / DB uuid）路由实测通过，migrate dry-run 输出与 plan 设计一致，395 passed 快速门控全量回归无破坏。**建议接受**。

## 验收核对（对照 plan acceptance）

1. **slug / fs-uuid 路由** ✅ — `map topic show --id fs-refactor-review` 与 `--id 1ba6ab09-...` 解析到同一 FS 话题；uuid5 反查在 CLI 本地完成（零 API），plan 的裁剪说明（不做服务端注册表）论证成立
2. **advance-round / close 同路由** ✅ — `_resolve_topic_ref()` 四命令共用，uuid 仍走 DB 行为不变
3. **wake.md 收敛** ✅ — 「先判别话题类型」步骤与 uuid5 红线已删除，pending_topic_replies 分发表合并单行
4. **commands.md 收敛** ✅ — 双列合单列 + `--storage` 说明，`map fs` 标注 advanced
5. **migrate 单向迁移** ✅ — dry-run 实测：index.md（status/round/participants frontmatter）+ round 分组文件 + archive 计划；FS 先完整落盘后 archive 的原子性设计与测试覆盖一致
6. **存量行为不变** ✅ — DB uuid 话题（19e0d9cc）show 行为不变；test_fs_source / test_fs_persona / test_compat 回归全绿
7. **ARCHITECTURE v2** ✅ — 三层分工图、DB vs FS 实体全景、事实源约定表、M51 路由规则齐备（588 行精简）
8. **测试覆盖** ✅ — test_topic_routing（uuid 优先级 / slug FS 优先 / --storage 裁决 / 404 反查）+ test_topic_migrate（round summary 界轮 / 同人同轮合并 / 决策落档 / 原子性）

## 结论

accept —— 全部验收项通过，无阻塞问题。
