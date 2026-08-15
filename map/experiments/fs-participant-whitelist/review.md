# 评审意见：fs-participant-whitelist 实验计划

## 总体评价

计划合理，缺陷定性准确（reviewer 被无差别投影已在 slimming-e2e 实证），方案与话题
fs-refactor-review round2 决策一致。三源合并语义（declared ∪ speakers ∪ creator）在
向后兼容与防噪音之间取得了正确平衡。**建议接受**。

## 合理性确认

1. 过滤点选在 `derive_work()`（parser 层）而非 service 层投影——单一 choke point，
   `fs_work_items` / `fs_topic_progress_for_agent` / CLI `fs work` 三条路径同时生效，正确
2. 发言自动并入（speech = factual participation）避免了「被邀请后忘记声明」的死角
3. ack missing 计算不收缩：advance-round 仍要求全量 participants 交文件，防止白名单
   过滤反向削弱推进门禁——这条边界抓得准

## 非阻塞建议（实施时落实）

1. **front-matter 容错**：participants 为字符串（非列表）时按单元素处理比忽略更友好
   （手写 YAML 常见 `participants: host` 单值写法），但忽略也可接受——二选一，写测试钉住
2. **dedup 顺序**：participants 合并时建议 declared 在前、按并入时间追加，`fs show`
   展示稳定；用列表去重即可，无需排序
3. **index.md 缺失场景**：write_round_comment 自动并入时若 index.md 不存在（手建文件夹），
   静默跳过即可（计划已写「容错跳过」），但建议打一条 stderr 提示，便于排查

## 结论

accept —— 无阻塞问题，按计划实施，非阻塞建议 3 条在实施阶段落实。
