"""T7-b skill 写入红线条款常量（实验 e6d23886 I4）。

设计约束:
- 单一真相源: 4 个 SKILL.md 嵌入一致副本, 文本与本模块常量字面相等
- 副本漂移检测: tests/test_red_line_clause.py (I9) 读 4 个 SKILL.md,
  断言 RED_LINE_CLAUSE / INCIDENT_TRIGGER 字符串出现且完全一致
- runtime 中立措辞: 枚举是示例 (Edit/Write/sed/python/heredoc 等),
  非穷尽白名单; 真正的判定标准 = "绕过 map CLI 直接改 map/** 下文件"
- 措辞同时落 A5 (视为事故触发条件) + A6 (三 persona 细分), 但
  PERSONA_INCIDENT_RESPONSE 仅作 persona-specific skill 内嵌副本,
  不强制每个 skill 嵌入
"""

RED_LINE_CLAUSE: str = (
    "**红线条款**（runtime 中立）：禁止用任何文本编辑器或脚本"
    "（Edit/Write/sed/python/heredoc 等）直接修改 `map/**` 下任何文件；"
    "一切状态变更走 `map` CLI；如需 Read 类工具（cat/head/tail/grep）"
    "做诊断允许。"
)

INCIDENT_TRIGGER: str = (
    "**视为事故触发条件**：发现 audit 链漂移（不论 verify-audit 检测"
    "还是 agent 自己注意到，含 server 侧门禁失效导致的非手写场景）"
    "→ 停止当前话题状态变更 → 报告 → 等 host/supervisor 决定。"
)

# A6 三 persona 细分: 各 persona-specific skill 嵌入对应一行
PERSONA_INCIDENT_RESPONSE: dict[str, str] = {
    "participant": (
        "**participant 响应**：停止写新发言文件 (`map topic comment`)，"
        "改发诊断评论或退回待 host 决定。"
    ),
    "host": (
        "**host 响应**：停止 advance-round / close / 创建实验等状态变更，"
        "先调用 `map fs verify-audit` 确认漂移范围并写诊断评论。"
    ),
    "reviewer": (
        "**reviewer 响应**：停止评审提交（不调 `experiment review add`），"
        "先在评审草稿里标注『发现 audit 链漂移，待 host 修复』。"
    ),
}


def red_line_section() -> str:
    """返回用于 4 个 SKILL.md 的『写入红线』整段 markdown。"""
    return (
        "## 写入红线（runtime 中立）\n\n"
        f"{RED_LINE_CLAUSE}\n\n"
        f"{INCIDENT_TRIGGER}\n"
    )
