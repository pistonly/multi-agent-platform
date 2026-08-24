# 实验日志：retired-surface-physical-cleanup (124e9a00)

## 2026-08-24 15:16 · approve 完成，执行安排

- plan v2 评审通过（review b80f0f92，无 open unreasonable 项）→ host approve，phase=approved
- start + I1 执行留待下轮唤醒：执行锁单 running 约束（与 bd9b21f6 同批 approved，逐个 start 执行）；approved 为稳定相位无空转
- v2 修订内容见 plan change_note：legacy 清单以 LEGACY-ENTRY-MATRIX 为准（D7）+ 登记基准单一真相化（D8）

## 2026-08-24 15:25 · I1-I4 完成（第一阶段执行）

- I1（A1）：4 个 bridge 脚本 stub 化，实测逐个执行 → exit 1 + stderr 功能等价替代指引（participant/reviewer 分别指 --persona 参数；三 persona 指 start-all-wakers.sh）
- I2（A3）：check-deprecated.sh 增两条规则——Rule1 deprecated 脚本必须保持 stub（缺退役标记/exit 1 → fail）；Rule2 退役声明处（CLAUDE.md/AGENTS.md/wake.md）引用的 scripts/*.sh 必须在矩阵登记。回归注入实测：注入1（stub 改回真脚本）fail；注入2（CLAUDE.md 加未登记 start-zombie-new.sh）fail（AGENTS.md 同步副本一并检出）；还原后 pass
- I3（A4）：矩阵 scripts 节 4 行更新 stub 现状；manifest 段声明「登记单一真相 + CI 消费契约，改格式先过 CI」
- I4（A5）：SIMPLE-WAKER.md 落第二阶段删除纪律（显式 diff 评审，host 之外至少一人看过清单）
- CI 接线：ci.yml backend job checkout 后第一步跑 check-deprecated.sh
- commit: 本批 8 文件（4 stub + check-deprecated.sh + 2 docs + ci.yml）
- 已知无关红：ruff UP038 @ cli/session_wake_log.py:104 为预存问题（非本实验文件，测试债实验范围），未越界修
- 剩余：I5 收尾（complete 前 A2 两阶段边界核对 + evidence 汇总）留下轮
