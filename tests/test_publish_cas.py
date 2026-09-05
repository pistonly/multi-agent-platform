"""Publish CAS + tombstone + 幂等重试（实验 M2 A3）单测。

覆盖：

- **CAS 重试**：首发 409 → refetch inventory → 重建 delta → 重试成功
- **CAS 耗尽**：连续 ``MAX_DELTA_RETRIES`` 次 409 → raise RetryableCASConflict
- **非 CAS 409 不重试**：publisher 冲突（error_code="fs_projection_conflict"
  但 status 不一样的，或 detail 不同）→ 直接抛出
- **「缺字段 ≠ 删除」（reviewer 建议）**：FsExperimentRead upsert 缺
  字段（默认值）→ 服务端条目**仍在**，只是字段值被重置为默认；
  删除只能走 experiment_delete tombstone
- **tombstone 重复**：同 slug 连续两次 experiment_delete → 第二次
  抛 fs_projection_delta_invalid（409，因为条目已不存在）

测试服务端 apply_fs_projection_delta 时用 in-memory state mock（与
``test_fs_projection_cli`` 的 Client 风格一致），避免起真实 server。
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field

import pytest
from map_client.exceptions import MAPConflictError, MAPHTTPError
from map_types.schemas.fs import (
    FsExperimentRead,
    FsProjectionChange,
    FsProjectionDeltaRequest,
)
from map_types.schemas.sync_retry import (
    MAX_DELTA_RETRIES,
    RetryableCASConflict,
    apply_delta_with_retry,
)

# ---------------------------------------------------------------------------
# Mock server state
# ---------------------------------------------------------------------------


@dataclass
class _ServerProjection:
    """最小 in-memory 投影状态，模拟 apply_fs_projection_delta 行为。"""

    revision: int = 0
    experiments: dict[str, FsExperimentRead] = field(default_factory=dict)

    def snapshot(self) -> list[FsExperimentRead]:
        return list(self.experiments.values())

    def upsert(self, exp: FsExperimentRead) -> None:
        # 实验 M2 A3：upsert 是整体替换；缺字段 → schema 默认值覆盖既有值
        self.experiments[exp.slug] = exp

    def delete(self, slug: str, expected_hash: str) -> None:
        if slug not in self.experiments:
            raise MAPConflictError(
                status_code=409,
                detail=f"experiment_delete {slug!r} does not exist",
                error_code="fs_projection_delta_invalid",
            )
        # 真实 server 还校验 expected_hash；这里略（与本测试聚焦无关）
        del self.experiments[slug]


@dataclass
class _Inventory:
    projection_revision: int


def _exp(slug: str, **overrides) -> FsExperimentRead:
    defaults = dict(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, f"test.{slug}"),
        slug=slug,
        title=slug,
        description="",
        phase="running",
        creator="host",
        dir_path=f"map/experiments/{slug}",
        executor="",
        topic="",
    )
    defaults.update(overrides)
    return FsExperimentRead(**defaults)


# ---------------------------------------------------------------------------
# apply_delta_with_retry: 重试行为
# ---------------------------------------------------------------------------


class TestApplyDeltaWithRetry:
    def test_first_attempt_succeeds(self):
        """首发成功 → 不重试，inventory 不再被读。"""
        server = _ServerProjection()
        # bootstrap：先 PUT full 让 revision=1（mock 里我们直接 init）
        server.revision = 1
        server.upsert(_exp("a", title="A"))

        apply_calls: list[int] = []

        class Client:
            def fs_projection_inventory(self, pid):
                return _Inventory(projection_revision=server.revision)

            def fs_apply_projection_delta(self, pid, payload):
                apply_calls.append(payload.base_revision)
                # 模拟服务端：revision 推进
                server.revision = payload.base_revision + 1
                return {"projection_revision": server.revision}

        payload = FsProjectionDeltaRequest(
            base_revision=1,
            client_workspace="/ws",
            content_root="map",
            changes=[
                FsProjectionChange(
                    kind="experiment_upsert",
                    slug="b",
                    value=_exp("b", title="B"),
                )
            ],
            result_content_hash="x" * 64,
        )
        result = apply_delta_with_retry(
            Client(),
            uuid.uuid4(),
            lambda base: payload.model_copy(update={"base_revision": base or 1}),
        )
        assert apply_calls == [1]
        assert result["projection_revision"] == 2

    def test_conflict_then_retry_succeeds(self):
        """首发 409 → refetch → 重建 → 第二次成功（A3 幂等重试主路径）。"""
        server = _ServerProjection(revision=1)
        server.upsert(_exp("a"))

        apply_calls: list[int] = []
        inv_calls: list[int] = []

        class Client:
            def fs_projection_inventory(self, pid):
                inv_calls.append(server.revision)
                return _Inventory(projection_revision=server.revision)

            def fs_apply_projection_delta(self, pid, payload):
                apply_calls.append(payload.base_revision)
                if payload.base_revision == 1:
                    # 首发：被抢
                    server.revision = 2  # 对方写完
                    raise MAPConflictError(
                        status_code=409,
                        detail="projection revision conflict: expected 1, got 1",
                        error_code="fs_projection_conflict",
                    )
                # 第二次：用 base=2
                server.revision = payload.base_revision + 1
                return {"projection_revision": server.revision}

        payload = FsProjectionDeltaRequest(
            base_revision=1,
            client_workspace="/ws",
            content_root="map",
            changes=[
                FsProjectionChange(
                    kind="experiment_upsert",
                    slug="b",
                    value=_exp("b"),
                )
            ],
            result_content_hash="x" * 64,
        )
        result = apply_delta_with_retry(
            Client(),
            uuid.uuid4(),
            lambda base: payload.model_copy(update={"base_revision": base or 1}),
        )
        assert apply_calls == [1, 2]
        assert inv_calls == [2]  # 重试前 refetch 一次
        assert result["projection_revision"] == 3

    def test_conflict_persists_raises_retryable(self):
        """连续 MAX_DELTA_RETRIES 次 409 → RetryableCASConflict。"""
        server = _ServerProjection(revision=1)
        apply_calls: list[int] = []

        class Client:
            def fs_projection_inventory(self, pid):
                # 每次 refetch 都看到 revision 推进 → 模拟持续被抢
                return _Inventory(projection_revision=server.revision + 1)

            def fs_apply_projection_delta(self, pid, payload):
                apply_calls.append(payload.base_revision)
                server.revision = payload.base_revision  # 不真推进；下一次 base 仍 stale
                raise MAPConflictError(
                    status_code=409,
                    detail="projection revision conflict",
                    error_code="fs_projection_conflict",
                )

        payload = FsProjectionDeltaRequest(
            base_revision=1,
            client_workspace="/ws",
            content_root="map",
            changes=[
                FsProjectionChange(
                    kind="experiment_upsert",
                    slug="b",
                    value=_exp("b"),
                )
            ],
            result_content_hash="x" * 64,
        )
        with pytest.raises(RetryableCASConflict) as exc_info:
            apply_delta_with_retry(
                Client(),
                uuid.uuid4(),
                lambda base: payload.model_copy(update={"base_revision": base or 1}),
            )
        assert exc_info.value.attempts == MAX_DELTA_RETRIES
        assert exc_info.value.last_base_revision is not None
        assert len(apply_calls) == MAX_DELTA_RETRIES

    def test_non_cas_409_not_retried(self):
        """非 CAS 409（如 publisher 冲突 fs_projection_conflict 但
        detail 不同）→ 直接抛出，不重试。"""

        class Client:
            def fs_projection_inventory(self, pid):
                return _Inventory(projection_revision=1)

            def fs_apply_projection_delta(self, pid, payload):
                raise MAPConflictError(
                    status_code=409,
                    detail="FS projection is bound to another single publisher",
                    error_code="fs_projection_conflict",
                )

        payload = FsProjectionDeltaRequest(
            base_revision=1,
            client_workspace="/ws",
            content_root="map",
            changes=[
                FsProjectionChange(
                    kind="experiment_upsert",
                    slug="b",
                    value=_exp("b"),
                )
            ],
            result_content_hash="x" * 64,
        )
        with pytest.raises(MAPConflictError) as exc_info:
            apply_delta_with_retry(
                Client(),
                uuid.uuid4(),
                lambda base: payload.model_copy(update={"base_revision": base or 1}),
            )
        # detail 不含 "projection revision conflict" → 视为非 CAS 409
        assert "publisher" in exc_info.value.detail

    def test_non_409_error_not_retried(self):
        """非 409（如 422 / 500）→ 直接抛出。"""

        class Client:
            def fs_projection_inventory(self, pid):
                return _Inventory(projection_revision=1)

            def fs_apply_projection_delta(self, pid, payload):
                raise MAPHTTPError(status_code=422, detail="invalid schema")

        payload = FsProjectionDeltaRequest(
            base_revision=1,
            client_workspace="/ws",
            content_root="map",
            changes=[
                FsProjectionChange(
                    kind="experiment_upsert",
                    slug="b",
                    value=_exp("b"),
                )
            ],
            result_content_hash="x" * 64,
        )
        with pytest.raises(MAPHTTPError) as exc_info:
            apply_delta_with_retry(
                Client(),
                uuid.uuid4(),
                lambda base: payload.model_copy(update={"base_revision": base or 1}),
            )
        assert exc_info.value.status_code == 422

    def test_base_unchanged_after_409_gives_up(self):
        """base 在 refetch 后未变（罕见：服务端的 409 不是 CAS 推进导致）
        → 提前放弃，不浪费重试。"""

        class Client:
            def fs_projection_inventory(self, pid):
                return _Inventory(projection_revision=1)  # 永远 1

            def fs_apply_projection_delta(self, pid, payload):
                raise MAPConflictError(
                    status_code=409,
                    detail="projection revision conflict",
                    error_code="fs_projection_conflict",
                )

        payload = FsProjectionDeltaRequest(
            base_revision=1,
            client_workspace="/ws",
            content_root="map",
            changes=[
                FsProjectionChange(
                    kind="experiment_upsert",
                    slug="b",
                    value=_exp("b"),
                )
            ],
            result_content_hash="x" * 64,
        )
        with pytest.raises(RetryableCASConflict):
            apply_delta_with_retry(
                Client(),
                uuid.uuid4(),
                lambda base: payload.model_copy(update={"base_revision": base or 1}),
            )


# ---------------------------------------------------------------------------
# 「缺字段 ≠ 删除」不变量（reviewer 建议）
# ---------------------------------------------------------------------------


class TestUpsertNotImplicitDelete:
    """验证 upsert 整体替换语义：缺字段以 schema 默认值覆盖，**不**触发
    删除（条目仍在）；真正的删除必须走 experiment_delete tombstone。"""

    def test_upsert_with_empty_description_keeps_entry(self):
        """description="" 默认值发 upsert → 条目仍在，仅 description
        被重置；这不是删除。"""
        server = _ServerProjection()
        # 既有 entry（描述完整）
        server.upsert(
            _exp(
                "x",
                title="X",
                description="important description",
                executor="participant",
                topic="design-topic",
            )
        )
        assert "x" in server.experiments

        # 客户端发一个「缺字段」的 upsert（schema 默认 executor="" 等）
        before = copy.deepcopy(server.experiments["x"])
        upsert_value = _exp("x", title="X", description="")  # 只覆盖 description
        server.upsert(upsert_value)

        # 条目仍在
        assert "x" in server.experiments
        # title 没变（被发一样的）
        assert server.experiments["x"].title == before.title
        # description 被重置（这是「缺字段 = 字段值被覆盖」语义；不是删条目）
        assert server.experiments["x"].description == ""
        # executor 被默认覆盖（之前是 participant，现在 ""）
        assert server.experiments["x"].executor == ""
        # topic 被默认覆盖
        assert server.experiments["x"].topic == ""
        # ID 不变（同一 slug）
        assert server.experiments["x"].id == before.id

    def test_upsert_with_all_defaults_still_keeps_entry(self):
        """客户端发一个全 schema 默认的 FsExperimentRead（除 slug 外）→
        条目仍在，字段被全部重置为默认。这是「整体替换」合约的极端用例
        —— 客户端有责任发完整内容，否则字段值会被默认值覆盖。"""
        server = _ServerProjection()
        server.upsert(_exp("y", description="important"))
        before_id = server.experiments["y"].id

        # 客户端发一个几乎全空的 upsert
        upsert_value = _exp("y")  # 全 schema 默认
        server.upsert(upsert_value)

        # 条目**仍在**（upsert 永远不会删条目本身）
        assert "y" in server.experiments
        assert server.experiments["y"].id == before_id  # ID 不变（slug 路由）
        assert server.experiments["y"].description == ""

    def test_only_explicit_delete_removes_entry(self):
        """只有 ``experiment_delete`` tombstone 才能让条目从投影中消失。"""
        server = _ServerProjection()
        server.upsert(_exp("z"))

        # upsert 无数次都不删
        for _ in range(5):
            server.upsert(_exp("z", title="changed"))
            assert "z" in server.experiments

        # tombstone 才删
        server.delete("z", expected_hash="ignored-by-mock")
        assert "z" not in server.experiments


class TestTombstoneIdempotency:
    """Tombstone 行为：第二次 delete 必须显式拒绝（条目已不在投影中），
    不允许静默 noop——防止 caller 误以为成功并继续依赖。"""

    def test_second_delete_raises_conflict(self):
        server = _ServerProjection()
        server.upsert(_exp("z"))

        server.delete("z", expected_hash="x")
        assert "z" not in server.experiments

        with pytest.raises(MAPConflictError) as exc_info:
            server.delete("z", expected_hash="x")
        assert exc_info.value.status_code == 409
        assert exc_info.value.error_code == "fs_projection_delta_invalid"


# ---------------------------------------------------------------------------
# RetryableCASConflict 形态
# ---------------------------------------------------------------------------


class TestRetryableCASConflict:
    def test_inherits_conflict(self):
        """RetryableCASConflict 是 MAPConflictError 子类，兼容 except MAPHTTPError。"""
        exc = RetryableCASConflict(
            "conflict persists",
            attempts=3,
            last_base_revision=5,
            last_detail="expected 5",
        )
        assert isinstance(exc, MAPConflictError)
        assert isinstance(exc, MAPHTTPError)
        assert exc.status_code == 409
        assert exc.error_code == "fs_projection_conflict"
        assert exc.attempts == 3
        assert exc.last_base_revision == 5
        assert exc.last_detail == "expected 5"

    def test_caught_by_map_conflict_error(self):
        """Caller 可写 ``except MAPConflictError`` 同时 catch Retryable + 原生 409。"""
        try:
            raise RetryableCASConflict(
                "x", attempts=1, last_base_revision=1
            )
        except MAPConflictError as exc:
            assert isinstance(exc, RetryableCASConflict)
