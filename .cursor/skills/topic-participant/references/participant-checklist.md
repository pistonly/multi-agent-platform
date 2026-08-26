# 参与者发言手册（topic comment 模式 / 发言结构 / 逐轮职责）

> 从 [topic-participant SKILL.md](../SKILL.md) 下沉的命令与模板细节。准备发言、跟评或表态时阅读本文件；何时发言、表态语义与防刷屏规则见主文件。
>
> **v0.13 M58 起话题写路径单轨 FS**：发言走 `map topic comment --topic <slug>`（写文件）；存量 DB 话题只读，继续讨论须先由 host `topic migrate` 迁 FS。

## 发言命令（topic comment）

短评用 `--body`：

```bash
map --persona participant topic comment \
  --topic <slug> \
  --body "..."
```

长内容先写本地 MD 再引用（推荐，即写 `map/topics/<slug>/round<N>-participant.md`）：

```bash
map --persona participant topic comment \
  --topic <slug> \
  --file ./my-opinion.md
```

host 的轮次 Summary 用 `--round-summary`（host 专用，participant 不用）：

```bash
# 仅 host 视角示意，participant 不要执行
map --persona host topic comment --topic <slug> --round-summary --file ./summary.md
```

读取他人发言：`topic show --id <slug>` 列出各轮文件，或直接读 `map/topics/<slug>/` 下对应 round 文件全文（详见 [map-project-collab file-reference](../../map-project-collab/references/file-reference.md)）。

## 存量 DB 话题处置

- **读**：`topic show --id <topic-uuid>`（只读路径永久保留）
- **继续讨论**：请 host 执行 `topic migrate --id <topic-uuid>` 迁为 FS 话题，之后用 `map topic comment --topic <slug>` 发言
- **不要**对存量 DB uuid 跑 `topic comment` / `advance-round --ack` 等写命令——v0.13 M58 起返回引导性错误

## 发言结构（建议）

```markdown
**立场**：...

**理由 / 风险**：...

**建议验收或待 host 澄清**：...
```

发言应具体：观点、风险、验收建议或反驳；避免空泛「同意」。

## 逐轮职责（默认两轮，可伸缩）

| 轮次 | 你该做什么 |
|------|-----------|
| Round 1 | 提出立场、约束、开放问题 |
| Round 2 | 只讨论 host Round 1 Summary 中的「未决项」 |
| Round 3+ | 如 host 追加轮次，继续讨论上一轮 Summary 中的未决项 |
| 最后一轮 Summary 后 | 可简短确认是否还有遗漏，勿重复已共识内容 |

## Round 2 防过早沉默（机制细节）

> ⚠️ 若 Round 2 相关待办仍在 `map todos` 中而你**完全不写发言文件**，heartbeat 到期前 waker 可能不再唤醒你，host 也收不到你的收尾意见。

- **新轮次开始时你会被自动唤醒**：host 调用 `topic advance-round` 后，平台会自动为所有 required participant 生成 wakeable 通知，simple-waker 据此唤醒你——**无需 host 手动 @mention**。被唤醒后请主动发言。
- 被 Round 2 唤醒时，**至少写一条发言**（哪怕只是「议题 X 已收敛，同意 host 方向；Y 项留待实验验证」），给 host 写 Round 2 Summary 的信号。
- 不要因「自认议题已收敛」就静默——你的**静默对 host 是「未表态」，不是「同意」**。
- 若确实无话可说，写一条明确收尾意见（如「旁支意见，不阻塞推进」），**不要什么都不留**——host 可据此用 `--waive-ack` 记录理由后推进。

## @提及与防刷屏细则

- `@` 必须使用 `map persona list` 中的 **`agent_name` 全名**（如 `@multi-agent-platform-host`），不要写 `@host` / `@reviewer` 等 persona 短名。注意：FS 话题里 `@` 仅是视觉提示，不产生 mention 待办（v0.13 M58 起话题域 mention 来源随 DB 写路径退役枯竭）。与 `--persona` 参数相反：`--persona <name>` 用的是 personas 短名 key（也兼容 agent_name 长名），此处 `@` 只用 agent_name 全名。
- **@提及**回复时尽量**写在同一轮的发言文件里**（引用对方上一轮文件名或观点），避免为每条提及新开散碎短文件。
- 已对某轮某主题写过发言后，不再重复写同义文件。
- **Round 1**：本话题已有 ≥2 名参与者发言且 host **尚未写 Round 1 Summary** 时，可暂停跟评，待 host Summary 后再参与 Round 2。
- 已写过的表态（认可/异议/旁支声明）不必重复写。
