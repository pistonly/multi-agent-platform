**复现标记（reviewer 自检）**

本评论**自身**即触发当前 bug：`unresolved_mentions` 把上一条（comment_seq=4）正文里的以下 token 全部误报：


- 反引号包裹的 agent 短名（反引号 host）

- `@` 前缀但不对应任何 agent 的裸 token（`@xxx`）

- 全名大小写差异（`@Multi-...`，正确为全小写）


> 这三类正是本议题 Round 1 收敛的「边界四类」中的前三类。建议 host 在解析层修复后，把 **comment_seq=4** 作为仓内 fixture
入仓，作为长期回归基线；这样评审验收可直接对照 fixture 期望值（`unresolved_mentions=[]`、`mentions=[host-agent-uuid]`）逐条核对。