# MAP Evidence Metadata 结构化校验契约（8ac93d4e I1 设计）

**实验 ID**: `8ac93d4e-9325-4bd5-aebb-79551e762fd5` (evidence_metadata 结构化校验 P2)
**子项**: I1 设计契约（占位文档，落地时同步更新）
**日期**: 2026-07-08
**Wake**: W40

## 目标

让 `map experiment log` 接收结构化 `metadata_json`（已经支持），并在 CLI / SDK / 服务端三层落地 **plan evidence_keys 软校验**：对照 plan markdown frontmatter `evidence_keys` 字段与 log metadata 字段，缺失时通过 warnings 数组 + stderr warn 行（与 fb8b0e2a stdout/stderr 分离契约一致）提示 host；**不阻断 log 保存**。

## 接口契约

### 1. SDK：`map_client.plan_evidence`（新模块）

```python
# sdk/python/map_client/plan_evidence.py

class PlanFrontmatterParseError(Exception):
    """Raised when plan frontmatter YAML cannot be parsed.

    Carries the raw frontmatter block + underlying YAML error so the caller
    can render a friendly stderr warning without losing diagnostic detail.
    """

@dataclass(frozen=True)
class PlanEvidenceKeys:
    keys: tuple[str, ...]            # deduped, ordered
    has_frontmatter: bool             # False when plan_md has no frontmatter
    parse_error: str | None = None    # set when frontmatter existed but failed YAML parse

    def __bool__(self) -> bool:
        return bool(self.keys)


def parse_plan_evidence_keys(plan_md: str) -> PlanEvidenceKeys:
    """Extract ``evidence_keys`` from YAML frontmatter in plan markdown.

    Frontmatter format::

        ---
        evidence_keys:
          - pytest_summary
          - alembic_current
          - api_health
        ---

    Behavior:
    * No frontmatter → ``PlanEvidenceKeys(keys=(), has_frontmatter=False)``.
    * Frontmatter with ``evidence_keys: <list>`` → returns deduped ordered keys.
    * Frontmatter malformed YAML → raises ``PlanFrontmatterParseError``.

    Notes:
    * Frontmatter detection = ``content_md.startswith("---\\n")`` and second
      ``\\n---\\n`` or end-of-string terminator.
    * If frontmatter has no ``evidence_keys`` key, returns empty ``keys``
      tuple with ``has_frontmatter=True`` (caller decides no-op vs warning).
    """
```

### 2. 服务端：`server.services.evidence_service`（新模块）

```python
# server/services/evidence_service.py

@dataclass(frozen=True)
class EvidenceWarning:
    code: Literal["MISSING_EVIDENCE_KEY"]
    missing_key: str
    plan_required: bool = True
    log_provided: bool = False


@dataclass(frozen=True)
class EvidenceValidationResult:
    warnings: list[EvidenceWarning]
    parse_error: str | None = None      # set when frontmatter parse failed

    @property
    def valid(self) -> bool:
        """Always True — soft validation never blocks log save."""
        return True


def validate_log_evidence(
    *,
    plan_md: str | None,
    metadata: dict | None,
) -> EvidenceValidationResult:
    """Soft-validate ``metadata`` against plan ``evidence_keys``.

    Cases:
    * plan_md is None or no frontmatter → empty warnings (no validation).
    * plan_md frontmatter exists but parse fails → ``parse_error`` set, no
      warnings (CLI renders stderr warn line; log saves with no warnings).
    * plan_md frontmatter parses + has evidence_keys → return
      ``MISSING_EVIDENCE_KEY`` for each plan key not present in ``metadata``.
    * plan_md evidence_keys empty → no warnings.
    """
```

### 3. 服务端 API：`create_log` 响应结构变更

```python
# server/api/experiments.py create_log

class LogCreateResponse(BaseModel):
    """Response for POST /experiments/{id}/logs (8ac93d4e I1).

    Wraps the persisted log with the validation result so the CLI can
    surface warnings to the host without breaking the v1 ExperimentLogRead
    contract for existing consumers.
    """
    log: ExperimentLogRead
    validation: EvidenceValidationResult       # warnings + parse_error
```

**向后兼容**：新增字段不影响既有 `ExperimentLogRead` 读取者；新增响应 model 是 wrapper，旧 client 反序列化会因字段缺失报错——CLI 端 SDK 方法需同步升级（`MAPClient.create_log` 返回 `LogCreateResponse` 替代 `ExperimentLogRead`）。

### 4. SDK：`MAPClient.create_log` 返回新结构

```python
# sdk/python/map_client/client.py

class MAPClient:
    def create_log(
        self,
        experiment_id: uuid.UUID,
        payload: ExperimentLogCreate,
    ) -> LogCreateResponse:
        """Returns LogCreateResponse {log, validation}."""
```

### 5. CLI：`map experiment log` 输出契约

**stdout**（永远是机器可解析 JSON 顶层）：
```json
{
  "log": {
    "id": "...",
    "experiment_id": "...",
    "summary": "...",
    "content_md": "...",
    "metadata_json": {"pytest_summary": "..."},
    "created_at": "..."
  },
  "validation": {
    "warnings": [
      {"code": "MISSING_EVIDENCE_KEY", "missing_key": "alembic_current", "plan_required": true, "log_provided": false}
    ],
    "parse_error": null,
    "valid": true
  }
}
```

**stderr**（仅在 frontmatter 解析失败 fallback）：
```
[WARN] plan evidence_keys 解析失败: <parse_error message>
```

**两条路径的语义**：
- `parse_error` 不为空 → CLI 同时输出 stdout JSON（含 `warnings: []` 因为无法知道 plan 要什么）和 stderr warn 行（提示 user plan frontmatter 写错了）
- `parse_error` 为空 + `warnings` 非空 → 仅 stdout JSON，stderr 静默
- 两者都为空 → 完全静默（plan 无 evidence_keys 字段 或 全部声明齐全）

### 6. 行为契约总结

| 场景 | stdout warnings | stderr | 是否阻断 log 保存 |
|------|-----------------|--------|-------------------|
| 全部声明齐全 | `[]` | 静默 | 否 |
| 部分声明 | `[MISSING_EVIDENCE_KEY x N]` | 静默 | 否 |
| metadata=None 但 plan 有 evidence_keys | `[MISSING_EVIDENCE_KEY x N]` | 静默 | 否 |
| frontmatter 解析失败 | `[]` | `[WARN] plan evidence_keys 解析失败: ...` | 否 |
| plan_md 无 frontmatter | `[]` | 静默 | 否 |

## acceptance (a) 4 case 单测覆盖

| Case | input | expected stdout.warnings | expected stderr |
|------|-------|--------------------------|-----------------|
| 1 | metadata 含全部 evidence_keys | `[]` | 静默 |
| 2 | metadata 含部分 | `[MISSING_EVIDENCE_KEY x missing_count]` | 静默 |
| 3 | metadata=None，plan 有 evidence_keys | `[MISSING_EVIDENCE_KEY x plan_keys_count]` | 静默 |
| 4 | plan frontmatter 解析失败 | `[]` | `[WARN] plan evidence_keys 解析失败: ...` |

## I1 拆分

| Wake | Scope | 验证 |
|------|-------|------|
| **W40** | 本设计契约 + checkpoint log | 设计文档落地 |
| W41 | SDK `parse_plan_evidence_keys` + 服务端 `validate_log_evidence` 单元测试 | 单元测试通过 |
| W42 | API `LogCreateResponse` wrapper + SDK `MAPClient.create_log` 返回新结构 + `tests/test_log_create_response.py` | E2E 路径通过 |
| W43 | CLI `experiment log` stdout/stderr 契约落地 + acceptance (a) 4 case E2E 单测 + CLI 帮助文本 | 4 case 单测通过 |
| W44 | 收口 → result_review | pre-complete + complete |

## 后续子项（不在 W40 scope）

- **(b)** reviewer 评审界面 plan-required 字段对照表 → web/ 子项目
- **(d)** 历史 log `metadata_json=null` pre-schema 标签 + `map experiment logs --validate`

## 风险与依赖

- **wrapper API breaking change**：旧 SDK consumer 反序列化 `LogCreateResponse` 会因 schema mismatch 报错。**缓解**：`MAPClient.create_log` 同步升级 + 发版说明；服务端 API 加 `Accept: application/vnd.map.v2+json` 头做版本协商（如有必要）。
- **frontmatter 格式约束**：当前 plan markdown 没有强制 frontmatter；plan (a) 落地时增加 lint 工具（7c9a2746 backlog）保证新 plan 都有 frontmatter。
- **acceptance (b) reviewer UI**：本实验 CLI 端只负责 plan evidence_keys 解析与日志保存；reviewer UI 是 web/ 子项目，与本实验并行。
