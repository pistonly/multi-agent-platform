# Archive Index

> Generated projection of `map/archive/topics/` — do not edit by hand.
> Rebuilt by `map fs archive-index --rebuild` (auto-invoked after `map fs archive`).
> v0.14 M61: generative projection, not an incrementally-maintained document.

## Topics (3)

| Title | Status | Decision | Notes | File |
|-------|--------|----------|-------|------|
| MAP 平台瘦身：本地 MD 文档为主，平台作为交互索引 | closed | no | legacy export form (header said: open) | [topics/map-平台瘦身本地-md-文档为主平台作为交互索引.md](topics/map-平台瘦身本地-md-文档为主平台作为交互索引.md) |
| 体验优化：waker 存活状态不可见，停机时全部等表态话题静默挂起 | closed | D1 双时间戳+waker特征标记(last_api_seen_at/last_waker_poll_at,告警只看后者); D2 固定绝对阈值15min server Settings项,上报动态算留v2; D3 判定在server+GET /status waker_heartbeats[] per-agent,+map work顶部全persona展示; D4 时间戳持久化到agents表,重启无误报窗口; D5 不用simple-remind:*当心跳; D6 文档补降级路径=host invoke编排+层次/边界说明. 落地 commit 范围 ae588ff..9c64da7 (9 窄 commit, fast-gate 603 绿, 实验 1b605e0b done/final14). 验收 A1 停机检测 WARN / A2 /status 挂点 / A3 map work 顶部展示 / A4 冷启动无误报 / A5 空闲无假阳 / A6 文档三章节 - 全部 passed. 已知边界: live server 重启后经 editable install 加载 /status 才载入 =waker_heartbeat[] (memory: map-server 由 conda 入口解析 site-packages 包, 改代码重装后生效). rationale: 体验问题真实(cold path 信任前提), 成本低, 已完好落地. action_items: [] | experiment_ready | [topics/waker-heartbeat-vis
| 测试 host 编排模式 | closed | no | legacy export form (header said: open) | [topics/测试-host-编排模式.md](topics/测试-host-编排模式.md) |
