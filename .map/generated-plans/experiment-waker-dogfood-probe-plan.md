# v0.8 waker dogfood 探针

## 目标

验证 **runtime-waker + map CLI** 路径可完成实验全生命周期，不依赖 host bridge。

## 范围

1. host 创建并 `submit-review`
2. reviewer 提交评审（无 open unreasonable）
3. host `approve` → `start` → `log` → `complete`

## 验收

- 各 phase 转换成功
- 全程未启动 `start-host-bridge*.sh`
- waker 日志无 `wake_errors`
