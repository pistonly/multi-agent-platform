# M58 结果核验（reviewer）

## 核验结论

接受。F2 目标完整达成，五层落地证据链齐全，补跑期间发现的 M58d blocker 已修复并经真实 e2e 实战验证。

## 核验要点

1. **Skill 单轨化**：三 Skill + cli/skills 镜像 grep 核证无 DB 写命令残留；map-plugin.yaml 0.13.0 / requires 0.12 已升版。
2. **CLI/server 退役面**：8 命令引导性拒绝（exit 2 + fs 等价指引）与 8 写端点 410 均实测；PATCH `archived` 归档通道放行；GET 只读不回退。
3. **M58d 修复质量**：FK 退役走幂等迁移（046，PG/SQLite 双方言 + downgrade）；service 层 FsTopic 字符串字段等价校验与 persona 短名 host 门禁对齐；回归测试覆盖 201/409/404 三态。
4. **e2e 闭环**：九步全 `status=ok`，实验 34c99441 done + archived、话题 closed、`map work` 无残留 obligation——FS 话题挂实验不再 404 的实战证明。
5. **回归基线**：44 failed 均为预先存在债务（worktree 干净副本复跑确证），M58 相关测试文件零失败。

## 遗留（不阻塞）

- 44 个预先存在测试债建议单独开实验清理（frontmatter 门禁波及面最广）。
- 部署顺序：先跑 046 迁移再滚动新代码，常驻实例需重启（已在部署提示中注明）。
