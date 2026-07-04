# Experiment 3d46e2bb — I1 执行日志

## 范围

I1：Migration 补丁 + InboundEvent 字段（host）

## 改动

### 1. alembic/versions/023_notification_category_aggregation.py

增补 `fingerprint_version` 列：
- 新增 `NotificationFingerprintVersion` enum (`v1`, `v2`)，server_default=`v2`
- 增补 `_NOTIFICATION_FINGERPRINT_VERSION_ENUM_NAME = "notificationfingerprintversion"` 常量
- `upgrade()` 在已有 7 列（`category`/`group_key`/`wake_version`/`event_count`/`first_event_at`/`last_event_at`/`updated_at`）之后追加：
  - `_has_column` 守卫 → `notificationfingerprintversion` enum 创建（postgresql `checkfirst=True`）
  - `add_column` + `server_default='v2'`
  - `ix_notifications_fingerprint_version` 索引
- `downgrade()`：
  - 索引 drop 列表前置（含 fingerprint_version 索引）
  - 列 drop 列表前置 `fingerprint_version`
  - 末尾 `DROP TYPE IF EXISTS` 两个 enum

backfill：在原有 3 条 `UPDATE` 后追加 `UPDATE notifications SET fingerprint_version='v2' WHERE fingerprint_version IS NULL`（防御 server_default 兜底）。

### 2. alembic/versions/024_inbound_event_rejection_count.py（新文件）

新增 `inbound_events.rejection_count` 列：
- revision=024, down_revision=023（线性 chain）
- `_has_column` 守卫 + `add_column(Integer, nullable=False, server_default=text("0"))`
- `downgrade()` 反向 drop

### 3. server/domain/models.py::InboundEvent

在 `acked_at` 后追加字段：

```python
rejection_count: Mapped[int] = mapped_column(
    Integer, nullable=False, default=0, server_default=text("0")
)
```

## 验证

### AC1 — `notifications` 8 字段 ✅

`sqlite3 PRAGMA table_info(notifications)` 全部 8 字段就位（`category` / `group_key` / `wake_version` / `event_count` / `first_event_at` / `last_event_at` / `updated_at` / `fingerprint_version`）。

### AC2 — `inbound_events.rejection_count` ✅

`INTEGER NOT NULL default=0` 字段就位。

### AC10 — backfill 默认值 ✅

- `category` server_default='digest'
- `fingerprint_version` server_default='v2'
- `first_event_at` / `last_event_at` / `updated_at` 通过 UPDATE backfill = created_at

迁移后 `SELECT count(*) FROM notifications WHERE category IS NULL` = 0；`fingerprint_version IS NULL` = 0。

### 链式迁移 ✅

`MAP_DATABASE_URL=sqlite:////tmp/sqlite_test/db.sqlite alembic upgrade head` 跑通 001 → 024 全 24 个迁移，无错。最终 `alembic_version` = `024`。

### ruff ✅

`ruff check alembic/versions/023_*.py alembic/versions/024_*.py server/domain/models.py` → All checks passed!

### 通知测试 ✅

```
pytest tests/test_notification_service_v09.py tests/test_notification_stream.py \
       tests/test_notifications.py tests/test_topic_notifications.py
20 passed in 35.26s
```

未发现 I1 改动引发的回归。

## 风险

- migration 023 `fingerprint_version` 列在已有 DB 上的 backfill：UPDATE WHERE IS NULL 在 SQLite / PostgreSQL 上语义一致；server_default=v2 保证新行不缺。
- `rejection_count` 列在 021 之前的 DB 上不存在：023 migration 不会触碰它，024 是链式下游，独立 add_column 幂等。

## 后续

I2：inbound_event_service 扩展 + runtime_waker v1 拒绝路径。
