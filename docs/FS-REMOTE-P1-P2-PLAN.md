# 远程 FS P1 / P2 实施计划

> 状态：P1 已实施；P2 未启动（见第 5 节门禁）  
> 基线：`e16b5d9 feat(fs): harden remote projection P0`  
> 适用范围：server 无法直接读取项目 workspace 的远程 / 容器部署  
> 总目标：本地继续 FS-first；远程逐步转为 Git / connector-backed content plane；P0 的 `projection-cache` 保留为兼容与离线缓存层。

## 1. 背景与基线

P0 已把原来的任意客户端全量覆盖收紧为可信单发布者模型，完成了：

- publisher / owner 服务端绑定；
- 数据库级原子 revision CAS；
- host、admin、`*-sync` 发布权限；
- validate 不再信任客户端 evidence 中的 owner / ack；
- write token 绑定 actor、base revision、nonce，并通过 receipt 防重放；
- CLI 远程 lifecycle 自动 CAS push，commit 冲突时恢复本地 `index.md`；
- Web 对 `fs-projection` 明确只读。

P0 的定位是安全兼容层，不是多 clone 同步协议。后续工作不能重新引入 last-writer-wins，也不应把 MAP 核心演化为通用文件同步或 Git 托管服务。

## 2. 目标状态

```text
本地开发
Agent -> 本地 map/ working tree -> local-fs 实时解析

远程协作（目标）
Agent -> Git commit/push -> GitTreeSource -> MAP 只读投影
                     \-> webhook / poll -> 重建索引

无 Git remote 的受控场景
Agent -> EdgeConnectorSource -> delta/CAS -> MAP 只读投影

MAP server
状态机 + 权限 + 审计 + 通知 + 内容索引
不承担通用文件编辑、Git merge 或任意多写者文件同步
```

优先级原则：

1. P1 先把现有兼容层做成可观测、可诊断、可增量刷新的可靠单发布者通道。
2. P2 再抽象内容源并引入 Git-backed source；P0/P1 projection 退居兼容层。
3. 多 clone 的冲突最终由 Git commit/ref/merge 语义处理，不在投影 API 内发明另一套通用合并算法。

---

## 3. P1：可靠增量同步与可观测性

### 3.1 P1 交付目标

在不改变“单发布者、最终一致”边界的前提下：

- 避免每次上传整个项目快照；
- 让客户端和用户能判断服务端内容来自哪个 revision、是否陈旧；
- 提供明确的 `status / diff / sync` 操作闭环；
- 普通本地写操作可自动或显式刷新远程投影；
- 将 `content_root` 从 server 全局配置收敛为项目级内容源配置。

P1 完成后仍不宣称支持任意多 clone 并发写。

### 3.2 数据与协议契约

#### 3.2.1 项目级 FS 配置

为 Project 增加或等价表达以下配置：

```yaml
content_source_type: local-fs | projection-cache
content_root: map
```

要求：

- `content_root` 默认 `map`，迁移后现有项目行为不变；
- server 的扫描、状态、投影校验全部读取项目级配置；
- CLI push 时携带的 `content_root` 必须与项目配置一致，不一致返回结构化 409；
- `.map/config.yaml` 仍是客户端工作区配置，但远程服务端不再从全局环境变量推断该项目的内容根。

#### 3.2.2 增量变更集

新增 delta 请求，而不是直接删除 P0 全量 push：

```json
{
  "base_revision": 12,
  "client_workspace": "/local/audit/path",
  "content_root": "map",
  "changes": [
    {"kind": "topic_upsert", "slug": "remote-fs", "value": {}},
    {"kind": "topic_delete", "slug": "old-topic", "expected_hash": "..."},
    {"kind": "experiment_upsert", "slug": "m60", "value": {}},
    {"kind": "experiment_delete", "slug": "old-exp", "expected_hash": "..."}
  ],
  "result_content_hash": "..."
}
```

约束：

- 整个变更集在单事务中按 `base_revision` CAS；
- 成功后 revision 只增加一次；
- 删除必须使用 tombstone / delete change，禁止通过“本次 payload 没出现”隐式删除；
- upsert 和 delete 均以 topic / experiment 为最小同步单元；评论随所属 topic 更新，P1 不拆成独立评论 CRDT；
- server 重建规范化结果并复核 `result_content_hash`；
- 非绑定 publisher 的 delta 与全量 push 一样拒绝；
- 空 delta 幂等，不增加 revision；
- P0 全量 push 保留为 bootstrap / repair 路径，并继续要求 CAS。

建议新增端点：

- `POST /api/v1/projects/{project_id}/fs/projection/delta`
- 保留 `PUT /api/v1/projects/{project_id}/fs/projection` 作为全量 repair

#### 3.2.3 投影新鲜度元数据

所有由 FS 内容派生的远程读响应应能暴露一致的来源元数据，至少包括：

```json
{
  "content_source": "fs-projection",
  "source_revision": 13,
  "source_content_hash": "...",
  "source_updated_at": "...",
  "stale": false,
  "stale_reason": null
}
```

实施时不要在各 schema 中零散复制字段。优先定义统一的 `ContentSourceMeta`，由 topic detail/list、experiment read、work snapshot 和 FS status 复用；若列表逐项重复数据过重，可在响应 envelope 放一次。

stale 判定建议：

- server 不凭“本地是否还有未 push 文件”猜测 stale；
- `stale=true` 仅基于可验证信号，例如超过项目配置的 freshness SLA、connector 心跳失联、Git ref 已知领先于已解析 commit；
- 默认阈值应可按项目配置，并允许关闭时间型 stale；
- UI 必须显示 revision、更新时间及 stale 原因，不能只用颜色表达。

### 3.3 CLI 计划

将当前 `map fs push` 演化为以下用户面：

#### `map fs status`

新增展示：

- 本地内容 hash；
- server revision / content hash / publisher；
- `in-sync | local-ahead | divergent | detached | stale`；
- 下一步可执行命令。

#### `map fs diff`

目标：只显示结构化摘要，不下载或打印全部正文。

- 新增 / 修改 / 删除的 topic 与 experiment；
- 本地 hash 与 server hash；
- base revision；
- publisher 不匹配、content_root 不匹配等阻塞项；
- `--json` 输出稳定 schema，供 Skill 和自动化消费。

server 无投影时，diff 应解释为首次 bootstrap，而不是错误。

#### `map fs sync`

建议语义：

1. 读取 server meta；
2. 比较本地清单与服务端对象 hash；
3. 生成 delta；
4. 预览删除 tombstone；
5. CAS 提交；
6. 输出新 revision 和同步摘要。

建议参数：

- `--dry-run`：只显示 diff；
- `--full`：强制走全量 repair；
- `--yes`：允许非交互确认 tombstone；
- `--json`：机器可读结果。

删除安全门禁：交互模式必须确认；Agent / 非交互模式缺少 `--yes` 时返回可操作错误，不能静默删除远端对象。

`map fs push` 在 P1 期间保留为兼容别名，输出 deprecation 提示并等价于 `map fs sync --full`，实际移除另行立项。

#### 本地写后的自动同步

以下成功写操作在检测到 server 为 `projection-cache` 时默认执行增量 sync：

- `map topic comment` 的 FS 路径；
- `map fs comment`；
- topic create；
- lifecycle validate / commit 完成后的关联 topic 刷新。

保留明确逃生口 `--no-sync`，用于离线工作。自动同步失败时：

- 本地文件写入不回滚（comment/create 本来就是本地事实）；
- 命令必须非零退出或明确输出 `local write succeeded, remote sync failed`；
- 给出 `map fs diff` / `map fs sync` 修复命令；
- lifecycle commit 的原子恢复逻辑维持 P0 契约，不与普通本地写混用。

### 3.4 服务端与 Web 计划

服务端：

- 提供对象级 hash 清单，供 CLI diff 计算，避免为 diff 下载全部正文；
- delta 应用必须使用数据库事务和 revision 条件更新；
- audit log 记录 base/new revision、change counts、tombstones、result hash、actor；
- 投影大小限制改为同时检查单对象与应用 delta 后总快照；
- 记录最后成功同步与最后失败心跳（若引入 connector heartbeat）。

Web：

- 远程 FS 继续只读；
- 页面显示 content source、revision、更新时间、stale 警告；
- stale 时不得把 lifecycle 状态展示成实时事实，应显示“最后同步于……”；
- 不在 P1 增加服务端直接改文件按钮。

### 3.5 P1 实施切片

建议按以下顺序拆成窄提交：

1. **P1-A：项目级 `content_root`**  
   migration + Project schema/API + server scan 路由 + 默认值兼容测试。
2. **P1-B：来源元数据与 stale**  
   统一 schema、read envelope、Web badge、status 输出。
3. **P1-C：对象 hash 清单与 `map fs diff`**  
   只读能力先落地，固定 delta 输入契约。
4. **P1-D：delta + tombstone CAS**  
   服务端事务、SDK、CLI `sync --dry-run/--yes/--full`。
5. **P1-E：写后自动同步**  
   comment/create/lifecycle 路径、错误恢复与 Skill 文档。

每个切片应独立可回滚；不要把 Project migration、delta 协议、Web UI 和自动同步压成一个大提交。

### 3.6 P1 验收门禁

必须全部满足才算 P1 PASS：

- [x] 现有 P0 旧 clone CAS、owner 伪造、跨 actor commit、token replay 测试继续通过；
- [x] delta 并发请求只有一个能从相同 base revision 成功；
- [x] 删除只能由显式 tombstone 发生，全量 repair 不因遗漏对象静默删除；
- [x] 空 delta 幂等且 revision 不增长；
- [x] `map fs diff --json` 能稳定区分 in-sync、local-ahead、divergent、detached；
- [x] comment/create 自动同步失败时本地文件保留，CLI 明确报“本地成功、远端失败”；
- [x] lifecycle commit 冲突仍恢复本地 `index.md`；
- [x] topic、experiment、work、Web 展示同一个 source revision；
- [x] 不同项目可以使用不同 `content_root`，默认项目行为不变；
- [x] payload 限制、权限、审计字段均有 API 测试；
- [x] migration 完成 `upgrade head -> downgrade 前一版 -> upgrade head` 往返；
- [x] `scripts/test-fast.sh` 中 P0/P1 FS 相关模块、Web Vitest/`tsc -b`、generated types、`git diff --check`、Skill 镜像全绿。仓库 HEAD 上已有与本切片无关的 `test_required_option_guard` Rich ANSI 断言失败，以及 `cli/session_wake_log.py` UP038 / 部分 review migration 测试 I001；P1 未扩大这些失败。
- [x] `.cursor/skills` 与 `cli/skills` 镜像一致。

### 3.7 P1 非目标

- 不支持两个普通 clone 直接向同一 projection 做自动三方合并；
- 不实现评论 CRDT；
- 不允许 Web 直接写远程 FS；
- 不把服务端投影当永久内容仓库；
- 不在 server 中执行任意 Git 凭据或 shell 命令；
- 不移除 P0 全量 repair 路径。

---

## 4. P2：统一 ContentSource 与 Git / Edge Connector

### 4.1 P2 交付目标

把“内容从哪里来”从 `fs_source_service.py` 的部署分支提升为显式领域边界：

- `LocalFsSource`：同机 / 同路径挂载，实时读本地内容树；
- `GitTreeSource`：远程生产推荐路径，解析固定 repo/ref/commit；
- `EdgeConnectorSource`：无可用 Git remote 时，由受控单发布者 connector 上送内容；
- P0/P1 的 projection table 作为物化索引和兼容缓存，不再被描述为事实源本身。

目标事实源定义：**版本化内容树是事实源，本地 FS 是 working copy，MAP 保存可验证来源的只读索引。**

### 4.2 `ContentSource` 接口边界

建议先定义窄接口，不做通用插件框架：

```python
class ContentSource(Protocol):
    def status(project) -> ContentSourceStatus: ...
    def revision(project) -> SourceRevision: ...
    def list_topics(project) -> list[FsTopicDetailRead]: ...
    def list_experiments(project) -> list[FsExperimentRead]: ...
    def refresh(project, expected_revision=None) -> RefreshResult: ...
```

要求：

- `plane_views`、topic detail/list、experiment read、work projection 只依赖该接口；
- 权限、lifecycle 状态机、审计、通知仍留在现有 MAP service，不下沉到 source；
- source adapter 不直接决定 persona 行为；
- source revision 使用带类型的结构，不能继续用一个含义混杂的整数：

```json
{
  "kind": "local-scan | projection-revision | git-commit | connector-revision",
  "value": "<revision or sha>",
  "observed_at": "..."
}
```

### 4.3 `GitTreeSource` 计划

#### 项目配置

建议字段：

```yaml
content_source_type: git
repository_id: <server-side credential binding id>
repository_url: <sanitized display URL>
ref: refs/heads/main
content_root: map
webhook_secret_id: <optional secret binding>
```

安全要求：

- API 不返回明文 Git token、deploy key 或 webhook secret；
- credential 按项目 / repository binding 隔离；
- 禁止客户端提交任意本地路径让 server 读取；
- checkout 必须限制大小、深度、文件数量、单文件大小和解析时间；
- 拒绝 symlink 逃逸、submodule 自动执行、Git hooks 和任意 shell hook；
- 日志与错误中清洗带凭据 URL。

#### 刷新模型

推荐顺序：

1. webhook 通知 ref 变化；
2. server / 独立 connector 解析指定 commit 的 `content_root`；
3. 生成规范化 topic / experiment 索引；
4. 事务性发布 `source_revision=commit_sha`；
5. 触发 work / Web / waker 使用新索引。

必须保留轮询修复路径，以覆盖 webhook 丢失；相同 commit 重复刷新应幂等。

#### Git 一致性合同

- 每次投影都绑定不可变 `commit_sha`；
- branch/ref 只是发现入口，不作为已解析内容版本；
- ref 回退（force-push）不得静默接受：需要项目策略 `reject | allow-with-audit`，默认 reject；
- refresh 失败继续服务 last-known-good commit，并标 stale/error；
- 不完整或不合法内容树不能替换 last-known-good；
- Git 合并冲突由 Agent 在 repo 中解决，MAP 不合并文件正文。

### 4.4 `EdgeConnectorSource` 计划

适用于私有网络、无 Git remote 或 server 无法访问仓库的场景。

connector 职责：

- 在单个受控 workspace 扫描内容树；
- 按 P1 delta/CAS 协议同步；
- 上报稳定 `connector_id`、heartbeat、workspace fingerprint 和 source revision；
- 断线重连先 diff，再决定 delta 或 full repair；
- 不替 Agent 做 topic / experiment 业务判断。

建议新增独立命令或守护进程：

```bash
map fs connector run --project-key <key>
map fs connector status --project-key <key>
```

不要把 connector 放进 simple-waker。waker 只发现 `map work` 并唤醒 Agent；connector 只负责内容源刷新，二者职责必须保持分离。

### 4.5 远程写入模型

P2 默认仍保持 Web 只读。若业务确实需要从 Web 发起写操作，采用“意图”而非 server 直接修改 Git：

```text
Web/API 创建 WriteIntent
  -> 授权 Agent/connector 拉取意图
  -> 在 working tree 应用
  -> Agent 创建 commit/push
  -> GitTreeSource 观察新 commit
  -> MAP 将 intent 标记为 applied
```

WriteIntent 必须绑定：project、actor、action、目标 slug、base commit/revision、期望字段、过期时间和一次性 nonce。P2 首轮可只定义接口与只读状态，不必同时实现 Web 编辑 UI。

### 4.6 P2 迁移与兼容策略

项目迁移应显式选择，不自动猜测：

1. 现有 `local-fs` 项目保持不变；
2. 现有 `projection-cache` 项目默认映射为 `EdgeConnectorSource` 兼容模式；
3. 管理员配置 repository binding 后，执行一次 Git source probe；
4. 比较当前 projection hash 与目标 commit 解析 hash；
5. 一致才允许无缝切换；不一致输出 diff 并停止；
6. 切换后保留 last-known-good 与审计记录，不立即删除旧 projection；
7. 回滚只切换 source binding，不改写 Git 或本地文件。

### 4.7 P2 实施切片

建议顺序：

1. **P2-A：ContentSource interface**  
   用 adapter 包裹现有 local/projection 行为，要求外部响应零语义变化。
2. **P2-B：Git source 只读 probe**  
   repository binding、credential boundary、commit 解析、last-known-good；暂不切默认路径。
3. **P2-C：webhook + poll refresh**  
   幂等刷新、force-push 策略、失败保留旧版本、stale 状态。
4. **P2-D：Edge connector**  
   daemon/CLI、heartbeat、P1 delta 复用、部署文档。
5. **P2-E：项目迁移工具与 source selector**  
   hash 对比、显式切换、回滚、审计。
6. **P2-F：WriteIntent 设计门禁（可选）**  
   只有出现明确 Web 远程写需求时立项；否则维持只读。

### 4.8 P2 端到端验证矩阵

至少增加两个独立 clone 的真实 Git 流程测试：

| 场景 | 期望结果 |
|------|----------|
| clone A、B 基于同一 commit 各写不同评论并通过 Git 合并 | MAP 最终解析合并后的 commit，两条评论均存在 |
| clone B 从旧 commit 直接刷新 projection | Git source 不受旧 clone 全量覆盖影响 |
| 两个 webhook 对同一 commit 重复到达 | 幂等，只产生一个有效 source revision |
| webhook 乱序到达 old/new commit | 不回退 last-known-good；旧事件被忽略并审计 |
| force-push 回退 ref | 默认拒绝，显示 stale/error；显式策略才可接受 |
| 新 commit 内容树解析失败 | 继续服务上一 commit，不发布半成品 |
| 非 host 伪造 topic creator | lifecycle 权限仍以服务端 owner/binding 为准 |
| token replay / 跨 actor commit | P0 安全门禁继续生效 |
| connector 与 Git source 同时尝试发布 | source binding 决定唯一有效来源，另一方被拒绝 |
| Web 访问远程 topic | 显示 commit/revision 与 stale，默认只读 |

### 4.9 P2 验收门禁

必须全部满足才算 P2 PASS：

- [ ] LocalFsSource 与当前同机行为无回归；
- [ ] Projection/Edge 模式继续满足 P0/P1 CAS、权限与防重放合同；
- [ ] Git 投影明确绑定 commit SHA，所有 topic/work/Web 读面版本一致；
- [ ] webhook 丢失可由 poll 修复，重复/乱序 webhook 幂等；
- [ ] 失败 refresh 不替换 last-known-good；
- [ ] force-push 策略有显式配置、默认拒绝和审计；
- [ ] 两独立 clone 并发评论的最终结果由 Git merge 后完整呈现；
- [ ] credential、symlink、submodule、hook、资源上限有安全测试；
- [ ] source 切换前进行 hash/diff 门禁，支持只切 binding 的回滚；
- [ ] simple-waker 不承担 connector 或 Git 同步职责；
- [ ] SDK、CLI、Web、README、ARCHITECTURE、QUICKSTART、两份 Skill 镜像同步；
- [ ] 全仓快速门控、定向集成测试、Web build、迁移往返、Ruff 与 diff check 全绿。

### 4.10 P2 非目标

- 不建设 Git 托管平台；
- 不实现通用 merge engine 或 CRDT；
- 不让 MAP 核心调用 LLM 决定冲突；
- 不自动执行仓库代码、hooks、submodules 或构建脚本；
- 不把 simple-waker 改造成内容同步守护进程；
- 不在没有明确需求和安全设计时开放 Web 直接写 Git。

---

## 5. 决策门禁与停止条件

### P1 -> P2 启动门禁

只有以下条件满足才进入 Git / connector 实施：

- P1 delta/CAS 和来源元数据稳定；
- 至少一个真实远程项目证明全量 projection 的传输或诊断成本值得升级；
- repository credential 的部署与轮换方式已经确定；
- 明确首个 Git provider / transport 范围，避免一开始支持所有托管平台。

### 停止或降级条件

- 若实际项目全部是单 workspace + 低频同步，P1 可作为长期终点，P2 不应为抽象而抽象；
- 若 Git credential 无法满足隔离和轮换要求，先交付 EdgeConnectorSource，不让 server 直接拉私有仓库；
- 若 Web 远程写没有明确消费者，WriteIntent 停留在设计稿，不进入实现；
- 若 delta 的复杂度高于全量快照收益，保留 `diff + full CAS repair`，不要引入对象级协议但缺乏实际收益。

## 6. 建议的最终交付物

P1：

- migration 与项目级 content source 配置；
- delta/tombstone API、SDK、CLI；
- `map fs status/diff/sync`；
- source meta / stale 的 CLI、API、Web 展示；
- 自动同步与恢复说明；
- P1 协议、权限、并发、迁移测试。

P2：

- `ContentSource` interface 与三类 adapter；
- repository credential binding 与 GitTreeSource；
- webhook + poll refresh、last-known-good；
- Edge connector 命令、部署和心跳；
- source 迁移 / 回滚工具；
- 双 clone、乱序 webhook、安全边界端到端测试。

## 7. 完成判定

- **P1 PASS**：现有单发布者远程通道具备增量同步、显式 diff、统一版本/新鲜度展示和项目级配置，且 P0 安全合同无回归。
- **P2 PASS**：远程生产路径可从不可变 Git commit 或受控 Edge connector 获得内容，所有读面绑定可验证 source revision，多 clone 冲突由 Git 工作流解决，projection-cache 只承担物化索引与兼容缓存职责。

