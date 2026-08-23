# 207d7c4b I4 — 文档(A3+T2-P3)

commit `fce538d`。

## 实施 log

- **A3 Skill 日志纪律后半句删除**(`.cursor/skills/experiment-host/SKILL.md` 第 8 条):
  删除末尾「同轮日志被状态机拒绝时(如 review 阶段不能写 log),把日志文件落 FS
  (`map/experiments/<slug>/log-rN.md`)并在进入下一阶段后立即补记」;前半
  「create / revise / submit 等任何一次失败后重试成功,都必须补一条
  `experiment log` 记录失败原文(422/409 的 error_code 与 hint)与修复动作」
  保留。原括号半句改注「log 白名单已放宽到 draft/review/approved 全阶段,阶段
  拒绝路径已随 cli-hygiene-batch A3 删除」。
- **T2-P3 host-checklist §3 顺序修正**(`.cursor/skills/topic-host/references/
  host-checklist.md`):was「先 `topic close` 再 `experiment create`」——与 409
  门禁(Cannot create experiment on a closed topic)相反;now「先 `experiment
  create --topic-id <topic-ref>` 再 `topic close --note ...; 实验 <exp-id>」。
  `--topic-id` 示例同时更新为 `<topic-ref>`(uuid | FS slug,A4 双路由)。
- **T2-P3 commands.md 核正**(`.cursor/skills/map-project-collab/references/
  commands.md`):`experiment create --topic-id` 示例加注释「接受 DB uuid 或 FS 话题
  slug(T2-P1 双路由,slug → 确定性 uuid5)」,占位从 `<topic-uuid>` 改
  `<topic-ref>`;`fs comment --topic` 行补「`--topic` 与 `--id` 双轨别名均可用」。

## 风险

- grep 核证:`.cursor/skills` 下 `log-rN` / `log-r0` / `落 FS` / `补记` 零残留
  (含本实验新增注释也未自引这些字样);SKILL 前半「补一条 `experiment log`」
  保留一次。A3 验收的 grep 核证点达标。

## acceptance

| 验收 | 状态 | 证据 |
|------|------|------|
| A3 后半句删除 | ✅ | SKILL.md:48 末尾无 FS 旁路段;grep 零残留(上面) |
| A3 前半保留 | ✅ | grep -c「补一条 \`experiment log\`」= 1 |
| T2-P3 host-checklist 顺序 | ✅ | §3 代码块 create → close(bash 顺序) |
| T2-P3 commands.md 核正 | ✅ | create --topic-id 双路由注释 + fs comment 双轨说明 |
