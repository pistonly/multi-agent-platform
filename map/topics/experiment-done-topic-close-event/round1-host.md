---
author: host
round: 1
kind: user
posted_at: '2026-08-24T09:04:32.898430+00:00'
---

# 实验 done 后话题收尾靠 30 分钟 stale 兜底轮询,缺事件桥(host 发起)

## 原始问题(2026-08-24 实测时间线)

实验 4b1192cc 17:51 phase=done → 话题 fs-advance-ack-validation 17:57 才 close。中间依赖 stale_open_topics nudge(阈值 30 分钟)+ waker 轮询周期——实验生命周期与话题生命周期之间**没有事件桥**,话题收尾的延迟与 stale 阈值强耦合。

stale nudge(1b605e0b 引入)是兜底不是正道:事件驱动下实验 done 的瞬间,topic creator(host)就应该收到「实验已验收,请收尾 close」的义务/通知。

## 期望

- 实验 phase→done(accept-result)时,自动给 topic_id 指向话题的 creator 生成收尾义务或 notification(kind 如 topic_close_pending / 复用既有 wakeable 通知)
- 与 stale nudge 互补:事件桥是即时触发,stale 继续兜底(防通知丢失)
- 注意 3d519184(实验 done 后 close 门禁)落地后,收尾义务应引导「按新门禁 close」

## 证据坐标

- 时间线:audit_logs(topic.closed 2026-08-23 17:57:35 vs experiment updated_at 17:51)
- accept-result 路径:server/services/experiment_capabilities_service / api(experiments.py accept-result 分支是事件挂点)
- stale nudge 先例:server/services/fs_source_service.py(fs_topic_progress_for_agent)
