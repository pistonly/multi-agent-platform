# 207d7c4b I1 — log 阶段放宽(A1)

commit `4ef1ca2`(平台 log 已于 running 态写,summary:「I1 log 阶段放宽完成:
white-list 加 draft/review/approved(A1);2 单测绿(draft→review→approved...)」)。
本条为划齐本地审计支撑文件,与平台记录一致。

## 实施 log

- `server/services/log_service.py::_validate_log_phase`:白名单从
  `(running, result_review, done)` 放宽到**除 cancelled 终态外的全部阶段**——
  draft/review/approved 均允许追加日志(435 行 open)。
- 理由:日志条目自带实验 phase 快照、只增不改,立项/评审期审计链与 running 期
  同等可信;cancelled 后仍拒绝(白名单保留的唯一边界)。
- 新增 `tests/test_log_phase_whitelist.py`:draft→review→approved 三阶段写日志
  201 且时间线连续;cancelled 后 422 拒绝。
- server daemon 重启加载新 log_service;验证 207d7c4b 本身落在 running 期、
  I1 的执行 log 已直接写平台(不再走 FS 旁路)——自举成功。

## 风险

- 放宽后 review 期日志与 plan revision 不混淆(日志带 phase 快照);早期日志
  噪音问题不预设,由 result_review 裁决口径承接。
- 容器验证需 `docker compose build api`(build-time COPY)——本机 daemon 场景
  以 `map server stop --force` + `MAP_DATABASE_URL` start 重启等价覆盖。

## acceptance

| 验收 | 状态 | 证据 |
|------|------|------|
| A1 draft log 落库 | ✅ | draft 阶段 log 201;timeline 三连 summary 排序正确 |
| A1 cancelled 仍拒 | ✅ | 422(StateTransitionError) |
| 回归 | ✅ | 放宽后厂内相关 log 测试全绿;ruff 通过 |
