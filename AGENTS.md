# MAP 协作指南（本代码仓库）

本仓库通过 [Multi-Agent Platform (MAP)](./README.md) 管理话题、实验与多 Agent 协作。
本文件只保留身份、边界与入口约定；**行为细节以 Skill 为准**（链接见下）。

## 项目目的（必读）

MAP 的产品目标是让用户在自己的项目中安装 SDK/CLI、放入 Skill 后，Agent 就能按 persona 通过 `map` CLI 使用 MAP，完成话题讨论、实验评审、项目状态同步、结论沉淀与行动项跟进。

产品主功能是 **Skill 指导 Agent 使用 MAP 协作**：Skill 负责行为流程与判断规则，MAP 平台负责状态、权限、审计等持久化协作对象。开发优先级应围绕 SDK/CLI、`.map/` persona、Skill onboarding、文档和端到端协作闭环展开。不要把 LLM SDK、复杂业务策略或手写 HTTP 调用嵌入 MAP 核心。

多 persona 自动推进是附带功能：**`simple-waker`**（`./scripts/start-all-simple-wakers.sh` 启动；Claude runtime 强制 `.map/.claude-env`，`--runtime cursor` 强制 `.map/.cursor-env`）轮询 **`map work`**，统一 remind 唤醒 Agent Runtime；**`cli/host_worker` bridge 与 legacy `runtime-waker` 启动路径已停用**。具体业务判断由被唤醒的 Agent 读取 Skill 后，通过 `map --persona <name>` 写回 MAP。

## Waker 与职责边界

**`map work`（topic progress + todos + wakeable 通知）+ Web 待办页 = waker 的触发源**（与平台 API 同源，不在 waker 里维护第二套业务规则）。

| 组件 | 做什么 | 不做什么 |
|------|--------|----------|
| **waker** | 发现待办/话题进展 → 短 prompt 唤醒 Runtime | 不写 MAP、不跑实验、不替 Agent 做业务判断 |
| **Skill** | 定义被唤醒后**怎么做** | 不替代平台状态机 |
| **被唤醒的 Agent** | `whoami` → `map work` → 写回 MAP | 不凭 session 记忆跳过待办 |

被唤醒时 Agent **必须先读** [.cursor/skills/map-project-collab/references/wake.md](.cursor/skills/map-project-collab/references/wake.md)（最小唤醒协议 + kind→清理分发表），再读 persona Skill。waker 设计与部署细节见 [docs/MAP-SIMPLE-WAKER.md](docs/MAP-SIMPLE-WAKER.md)。

## Agent 身份（必读）

**本仓库统一使用 `.map/` 目录中的 persona + `map` CLI。**

| Persona | Agent 名 | 职责 |
|---------|----------|------|
| **host** | `multi-agent-platform-host` | 主持话题、**创建实验**、推进实验生命周期 |
| **participant** | `multi-agent-platform-participant` | 参与话题评论、讨论 |
| **reviewer** | `multi-agent-platform-reviewer` | 评审实验计划 |

### 硬性规则

1. **禁止**手写 `httpx` / `curl` 调 MAP API；统一用 **`map --persona <name>` CLI**
2. 操作前执行 `map --persona <name> persona whoami` 确认身份
3. **实验必须由 host persona 创建**——平台只允许 `creator_agent_id` 提交/启动/撤销；host 可在 `start` 时通过 `--executor <agent>` 委派执行（`complete`），host 仍保留 cancel / withdraw 门禁
4. 行为细节读 Skill：通用协作 [map-project-collab](.cursor/skills/map-project-collab/SKILL.md)、话题主持 [topic-host](.cursor/skills/topic-host/SKILL.md)、实验 [experiment-host](.cursor/skills/experiment-host/SKILL.md) / [experiment-reviewer](.cursor/skills/experiment-reviewer/SKILL.md)
5. **写操作统一走 map CLI**（硬性规则）：所有 `map/**` 下文件的状态变更必须通过 `map` CLI（`map topic comment` / `map topic advance-round` / `map topic close` / `map experiment create` 等）；禁止用文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改。详见 `.cursor/skills/**/SKILL.md` 的红线条款。措辞与 `lib/red_line_clause.py:RED_LINE_CLAUSE` 一致；副本漂移检测见 `tests/test_red_line_clause.py`（实验 e6d23886 I9）。

身份与 token 存在 **`.map/`** 目录（本机运行时，整目录 gitignore）。模板见 [docs/map-templates/](docs/map-templates/)。

## 首次接入

1. 在本仓库根目录执行（**无需 admin token**，走自助 `POST /api/v1/bootstrap` 端点）：

```bash
map bootstrap --key <project-key> --name "<项目名>" --api-url http://localhost:18400
```

2. 确认生成 `.map/config.yaml`、`.map/agents.yaml`、`.map/agents.local.yaml`（**整目录勿提交**；clone 后重新 `map bootstrap`）

> **老版本 server 兼容**：若连接的 server 无 `/bootstrap` 端点（<0.4），CLI 自动回退到 admin token 路径，需先 `export MAP_ADMIN_TOKEN=...` 或写入 `~/.map/admin.yaml`。

## 日常：选择身份

用户应明确说明 persona（如「以 **host** 身份查看 open 话题」）。Agent 按对应 Skill 行动，操作前 `whoami` 确认身份。

命令清单与参数细节见 [commands.md](.cursor/skills/map-project-collab/references/commands.md)；FS 事实源约定（`map/topics/<slug>/`、`map/experiments/<slug>/`）见 [file-reference.md](.cursor/skills/map-project-collab/references/file-reference.md)。

## 平台开发约定（本仓库 / Skill 分发面）

- **新增 work kind 落地 checklist（强制，实验 d559f431 A5）**：新增 kind 必须同时改
  ① `server/services/work_kinds.py` registry ② wake.md 标记块（用
  `map work --kinds --kinds-format md` 重新生成）③ `tests/test_work_kinds.py`
  的一致性用例自然覆盖——漏改任一处，CI（pytest 整行 diff）即红。
- **Skill 分发面纪律**：`.cursor/skills/**` 随 wheel 分发（`map skill install` 落到用户项目），
  只保留平台契约与操作指引；本仓库专属引用（`tests/test_*.py` 等仓库路径、实验编号、
  feedback memory 文件名）不得进入分发面，由 `tests/test_red_line_clause.py` 的
  dogfood 反向守卫兜底。红线条款单源与副本守卫见 `lib/red_line_clause.py` docstring。

## 服务地址

- API: http://localhost:18400（默认端口，`MAP_PORT` 可覆盖）
- Web: http://localhost:3000

# 回答语言
总是使用中文来回答
