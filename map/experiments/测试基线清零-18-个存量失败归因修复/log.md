# 测试基线清零实验日志

## 分组修复记录（18/18）

| 组 | 失败 | 根因 | 处置 |
|---|------|------|------|
| mcp×4 | Plan frontmatter is missing | 0e0ce9ae plan lint 后夹具裸文本 | make_valid_plan() 工厂 |
| m3×1 | 期望 running 前 log 422，实际 201 | cli-hygiene-batch A1 白名单放宽（除 cancelled 全允许） | 测试改验现行为 + cancelled 拒绝 |
| m2×1 | PATCH resolved 422 | I1(b/d) plan revise auto-archive 冻结 + open→resolved 非法 | 改走 v2 重评审正路（bd9b21f6），断言 422+错误码 |
| cli×1 | ProjectRootNotFoundError exit 1 抢在退役引导前 | 引导在 _run 内、client 构建后 | **产品代码修复**：db_uuid_write_preflight 六命令前置 + 本地反查容错（085dfed） |
| cli×1 | list 默认表格非 yaml | T42 表格默认渲染 | 断言加 --format yaml |
| cli×1 | payload["ok"] KeyError | M54C 有意删除数据级 ok | 断言改 evidence_keys 面 |
| work_deprecation×1 | _FakeClient 缺 get_global_status | work 心跳横幅新消费点 | fake 补最小方法 |
| cross_persona×1 | 建站 params= 422 | httpx params 是 query 不是 body | json= |
| todos×4 | 裸 plan / params= / draft archive 409 | 同上三类 | 工厂替换 / json= / 先 cancel 再 archive |
| 18a64691×3 | 子进程 show 404（话题不在本机 MAP）+ 注释含 obligation | dogfood 环境依赖 + 术语守卫 | 环境自适应 skip + 注释措辞（cb97f60） |

## 验收

- 全量回归（-m "" 含 slow）：**2654 passed / 5 skipped / 6 xfailed / 0 failed**（基线 18 failed / 2662 passed）
- release.sh prepare 复跑全绿（web build + 双包构建 + unpack 校验）；v0.16.0 tag 不受影响
- 5 skipped 均为环境依赖项自适应（18a64691 dogfood 联动×2 + 既有项），非静默跳过

## 结论

13 个 v0.12.0 存量 + 5 个 main 新增失败全部为**断言/夹具未跟上 v0.12 后契约**（plan lint、
表格默认渲染、log 白名单放宽、review item 状态机、archive 门禁、body 校验、M54C ok 删除）
或 **dogfood 环境依赖**，唯一产品代码缺陷是 CLI 退役引导被 root 错误遮蔽（已修）。
测试基线归零，后续回归红即真回归。
