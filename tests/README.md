# tests/README.md — 测试分层与 fast gate

## Marker 语义

| Marker | 含义 | 默认 `pytest` |
|--------|------|----------------|
| *(无)* | 单元测试：in-memory sqlite + TestClient + mock | **包含** |
| `slow` | 单测耗时 >1s，或多线程/SSE/file-backed sqlite | **排除** |
| `integration` | 多进程、真 httpx、systemd、压力测试 | **排除** |
| `claude_cli` | 真调用 `claude --print` 子进程 | **排除** |

配置见 `pyproject.toml` `[tool.pytest.ini_options]`。

**约定**：新增测试默认 unit；单用例稳定 >1s 须标 `@pytest.mark.slow`。Marker 是默认 gate 的筛选器，**不是**禁止本地 `pytest tests/foo::bar` 跑慢用例。

## 推荐命令

```bash
# 本地 PR fast gate（默认）
./scripts/test-fast.sh

# 等价
python3 -m pytest -m "not slow and not integration and not claude_cli"

# slow 套件（nightly / 合并前）
python3 -m pytest -m slow

# 集成
python3 -m pytest -m integration

# 全量
python3 -m pytest -m ""   # 或 pytest --marker-all（若配置）
```

## CI 矩阵建议

| 阶段 | 命令 | 目标 |
|------|------|------|
| PR gate | `./scripts/test-fast.sh` | <90s（PR3 机读 gate 落地后强制执行） |
| Nightly | `pytest -m slow` | 补标审计、waker 长用例 |
| Pre-release | `pytest -m integration` | 端到端 |
| 可选 | `pytest -m claude_cli` | 需 Claude CLI 登录态 |

## Baseline

耗时基线见 [`PERF.md`](./PERF.md)。PR3 将基于 duration 报告补 `@pytest.mark.slow` 并收敛 fast gate 至 <90s。

## 相关脚本

- `./scripts/test-fast.sh` — 默认 fast gate 薄包装
- `./scripts/check-deprecated.sh` — legacy 入口 manifest 校验（非测试分层）
