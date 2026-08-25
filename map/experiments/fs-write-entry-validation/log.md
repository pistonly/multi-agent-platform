# 27f961d1 执行日志（fs-write-entry-validation）

## I1 parser anomaly 层（R1+R2）
- `sdk/python/map_fs/parser.py`：新增 `FsAnomaly` dataclass（file/reason/level）+ `FsTopic.anomalies` 字段（默认空，消费方无破坏）；`_anomaly_level_of` 按 close_note D4 分级（posted_at 有值不可解析/字段不符=invalid；缺失含整块缺失=lite）；parse 循环复用 `_ack_error_of` 收集（不另写第二套判定）。
- 读取行为不变：posted_at fallback mtime、正文完整、文件字节不动（R2）。

## I2 写路径前置校验（W1）
- `_reject_embedded_frontmatter`：body 以 `---` 围栏开头且解析出 author/round/posted_at 任一机器字段 → ValueError（文案带正确示例：正文直接以标题/文字开头）。纯分隔线（无闭合围栏/无机器键）放行。
- `write_round_comment` 入口调用；CLI（fs comment / topic comment）捕获输出 exit 2；`--force` help 注明不豁免。

## I3 可见性双出口（V1）
- `map fs show`：topics 头部后输出 anomalies 段（表：File/Level/Reason）。
- `map fs anomalies`：全工作区扫描，--format table|yaml|json；实测输出 6 条存量 anomaly（含 E1 锚点 invalid + 5 条 lite）。

## I4 测试与并发事件
- 测试面：tests/test_fs_write_entry_validation.py（SDK：W1 参数化 5 变体 + overwrite 负向 + 分隔线/无机器键放行 + R1 四场景 + E1 真实 fixture）+ tests/cli/test_fs_anomalies.py（CLI：双出口 + 拒绝文案 + force 不豁免）+ test_compat 快照 + dry-run 登记。文件级 91 passed。
- **并发事件**：执行期发现被唤醒的后台 host runtime（pid 3484434）在同时推进本实验——它补写了 SDK 测试文件与 dry-run 登记（内容与主实现一致、覆盖更全，审查后合入），但构成双 host 竞态；已 kill 该 runtime（waker 此前已停）。教训：锁只互斥实验执行动作，不互斥文件写入——手动执行期间应同时停 waker 与 runtime。
- commit：见 git log（map exp 27f961d1: FS 写入口双端校验…）。

## 验证
- ruff check 全绿
- fast suite 全量：见 evidence（后台重跑中，结果补 pre-complete）
