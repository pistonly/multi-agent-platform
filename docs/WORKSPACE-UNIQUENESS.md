# workspace 唯一归属约束（workspace_path + content_root 联合键）

local-fs 形态下，project 绑定一个 workspace 目录 + 内容根目录。同一
`workspace_path + content_root` 若被 ≥2 个 project 认领，FS 话题 id（slug 派生
uuid5）与按 project 聚合的操作（导出/归档/waker/权限/实验 `--topic-id`）全部二义。
因此平台对联合键做唯一性约束，并保留存量双归属的标准处置路径。

## 约束规则

- 新建 project（`POST /api/v1/projects`）与 bootstrap 注册（`POST /api/v1/bootstrap`）
  都校验 `workspace_path + content_root` 联合键：命中已有 project 即 **409**，
  detail 指明「已被 project '<name>' (key=<key>) 认领」。
- `PATCH /api/v1/projects/{id}` 改 workspace_path / content_root 同样受约束，
  但排除自身（改到自己的现行组合不报错）。
- **不同 content_root 不误伤**：同一 repo 开多个 project 必须用不同 content_root
  （如 `map` / `docs` / 相对子目录），这是合法多 project 的唯一方式。

## 存量双归属处置

约束只拦截**新建**；已存在的双归属不被自动迁移、也不阻断旧 project 运行，
由 `map doctor --config` 列清单告警（A1 workspace 类别，`--check` 返回 1）。
处置按归属方的性质二选一：

1. **retired-surface 清理**：双归属之一是历史遗留（旧 project 已退役、仅剩绑定）。
   清理该 project 的 workspace 绑定（如将其归档或解除占用），保留主 project
   独占该 workspace。
2. **archive 换主**：双归属之一不再活跃时，走 project 归档语义（dormant +
   dry-run 列出受影响 workspace/topics；unarchive 无损：uuid5 id 稳定、仅切
   扫描可见性），归档后键位释放给当前主 project。

同一 repo 真正要开多 project（非误绑定）时，属合法意图——保持双归属不成立，
改让第二个 project 使用不同 content_root 即可，不需要处置 doc 里的清理路径。

## 验证

- 新建双归属 → 409；不同 content_root 共存 → 201。
- 存量双归属（同 workspace+content_root ≥2 project）→ `map doctor --config`
  输出 workspace 类别并列全认领者；`--check` 退出码 1。
- 修复后 `map doctor --config` 回到 clean（0）。
