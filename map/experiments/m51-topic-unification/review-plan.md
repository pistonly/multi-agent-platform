# M51 计划评审意见（reviewer）

**结论：批准**

1. **路由层裁剪合理**：服务端 `/fs/topics` 投影实时扫描文件系统（不经过 DB），uuid5→slug 在 CLI 本地反查等效满足「--id <fs-uuid> 可用」，且避免 bootstrap 与验证型写两处同步逻辑的漂移风险。
2. **migrate 次序正确**：FS 完整落盘 → 才调 archive API，失败中止不产生半迁移；复用 archived_at 语义（列表默认隐藏、show 仍可见）零 schema 变更。
3. **风险兜底充分**：uuid 路径行为不变（先 DB 后本地反查）+ CLI 快照回归；`--id` 放宽为 str 由路由层判别格式。

**关注点（执行时自检）**：
- `--storage` 冲突判别需覆盖「DB topic slug 与 FS 同名」场景的探测实现（DB 侧按 slug 查询能力若缺失，文档化为 FS 优先即可，不硬造）。
- comment 路由 FS 后无 API 审计——与现有 `map fs comment` 一致，wake.md 需保留该说明。
