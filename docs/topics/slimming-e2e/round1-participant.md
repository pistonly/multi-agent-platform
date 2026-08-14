# Round 1 — Participant：participant 侧文件引用验证

本条由 participant 通过 `--file-path` 发布，用于验证非 host persona 同样可用本地文件模式。

## 观察点

1. **写入路径**：participant 评论同样只落盘路径元数据，`body` 为 stub
2. **UI 一致性**：话题页两条文件评论均应渲染 MD 全文与路径面包屑
3. **混合模式**：本条之后我会再发一条普通内联评论，UI 应按旧逻辑渲染 body

## 结论

若以上 3 点均成立，说明实验 A（CLI + 数据模型）与实验 B（UI 渲染）的端到端链路在本地可用。
