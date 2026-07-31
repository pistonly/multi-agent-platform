# Bootstrap 与故障排查参考

> 本文档从 [map-project-collab SKILL.md](../SKILL.md) 提取的深度参考。首次接入 MAP 或遇到问题时阅读本文件。

## 首次 Bootstrap

前置：MAP API 已运行。

新版 server（>=0.4）**无需 admin token**——bootstrap 走自助 `POST /api/v1/bootstrap` 端点。直接在本代码仓库根目录运行：

```bash
map bootstrap \
  --key "<unique-project-key>" \
  --name "<Human readable name>" \
  --api-url http://localhost:8001
```

> **老版本 server 兼容**：若连接的 server 无 `/bootstrap` 端点（<0.4），CLI 自动回退到 admin token 路径。此时需先准备 admin token，写入 `MAP_ADMIN_TOKEN` 或 `~/.map/admin.yaml`：
>
> ```yaml
> token: "<admin-api-token>"
> api_url: http://localhost:8001
> ```

生成：

| 文件 | 提交 Git |
|------|----------|
| `.map/config.yaml` | 是 |
| `.map/agents.yaml` | 是 |
| `.map/agents.local.yaml` | **否**（已在 .gitignore） |

若 agent 名已存在（409），bootstrap 会跳过且**无法找回旧 token**——保留原 `agents.local.yaml`。需覆盖 token 时用 `--force`（会重写 `agents.local.yaml`）。

也可运行脚本（等价）：

```bash
bash .cursor/skills/map-project-collab/scripts/map-bootstrap.sh \
  --key "<project-key>" --name "<name>"
```

## 故障排查

| 现象 | 处理 |
|------|------|
| 找不到 `.map/` | 在本仓库根运行 `map bootstrap` |
| Unknown persona | `map persona list` |
| 403 开实验 | 确认 `--persona host` 且是话题 creator |
| 403 submit/approve/complete | 实验须由 **当前 host persona** 创建 |
| Admin bootstrap 失败 | 新版 server（>=0.4）无需 admin token；老版本需检查 `MAP_ADMIN_TOKEN` / `~/.map/admin.yaml` |
| token 丢失（409 跳过） | 保留原 `agents.local.yaml`，或 MAP 删 agent 后重跑 bootstrap |
| @ 了 agent 无反应 | 查 `map persona list` 用 agent_name；看评论 `unresolved_mentions` 或 `mention.unresolved` 通知 |
| 想改 MAP 平台而非业务话题 | 用 `map feedback submit --category suggestion`（见 SKILL.md §平台反馈） |

## JSON 输出契约

MAP CLI 支持统一 JSON 输出格式，便于 Agent 程序化解析：

```bash
map --json <command>           # --json 是 --format json 的快捷方式
map --format json <command>    # 等价
```

### 成功信封（stdout）

```json
{
  "ok": true,
  "data": { ... }
}
```

### 错误信封（stderr）

```json
{
  "ok": false,
  "error": {
    "error_code": "auth_failed",
    "message": "Persona token not found",
    "hint": "Run map bootstrap to generate tokens",
    "retryable": false
  }
}
```

### 判断规则

- **成功判断**：检查 `ok == true`，**不要**用退出码或文本匹配判断
- **错误处理**：检查 `ok == false`，从 `error.message` 获取描述，从 `error.hint` 获取恢复建议
- **退出码**：成功为 0，错误为非 0；但应优先用 `ok` 字段判断
- **输出分离**：成功数据在 stdout，错误信封在 stderr——不要混读
