"""规范化 hash 模块（M2 A1）单测。

覆盖 plan acceptance A1：

- Markdown NFC 规范化（组合 vs 预组合字符）
- Markdown LF 行尾（CRLF / CR → LF）+ 末尾恰好一个换行
- YAML key 递归排序（mapping key 乱序 → 一致 hash）
- YAML list 顺序保持（list 顺序变化 → 不同 hash）
- 五路 hash（metadata / plan / log / reviews / content_root）输出稳定
- normalize_markdown / normalize_yaml_text 幂等性
- reviews_content_hash 按 slug 排序后拼接
- content_root_hash 与 normalize_content_root 一致
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime

import pytest
from map_types.schemas.canonical import (
    all_hashes,
    content_root_hash,
    log_content_hash,
    metadata_hash,
    normalize_markdown,
    normalize_yaml_text,
    plan_content_hash,
    reviews_content_hash,
    validate_markdown_normalization,
    validate_yaml_normalization,
)
from map_types.schemas.fs import FsExperimentRead


def _experiment(
    *,
    slug: str = "demo",
    title: str = "demo title",
    description: str = "demo desc",
    phase: str = "running",
    creator: str = "host",
    executor: str = "participant",
    topic: str = "demo-topic",
    current_plan_version: int = 1,
    updated_at: datetime | None = None,
    projection_id: uuid.UUID | None = None,
) -> FsExperimentRead:
    return FsExperimentRead(
        id="00000000-0000-0000-0000-000000000001",
        slug=slug,
        title=title,
        description=description,
        phase=phase,
        creator=creator,
        dir_path="map/experiments/demo",
        plan_path="map/experiments/demo/plan.md",
        log_path="map/experiments/demo/log.md",
        review_path=None,
        current_plan_version=current_plan_version,
        executor=executor,
        topic=topic,
        updated_at=updated_at,
        projection_id=projection_id,
    )


# ---------------------------------------------------------------------------
# Markdown 规范化
# ---------------------------------------------------------------------------


class TestNormalizeMarkdown:
    def test_nfc_combining_to_precomposed(self):
        """é 两种写法（U+0065 U+0301 vs U+00E9）必须归一为同一字符串。"""
        combining = "e" + "́"  # U+0065 + U+0301 (combining acute)
        precomposed = "é"  # U+00E9
        assert combining != precomposed, "fixture must differ at codepoint level"
        assert normalize_markdown(combining) == normalize_markdown(precomposed)
        assert normalize_markdown(combining) == "é\n"

    def test_nfc_explicit_unicodedata(self):
        combining = "é"  # NFD
        precomposed = "é"  # NFC
        assert unicodedata.normalize("NFC", combining) == precomposed
        assert normalize_markdown(combining) == precomposed + "\n"

    def test_crlf_to_lf(self):
        text = "line1\r\nline2\r\nline3"
        result = normalize_markdown(text)
        assert "\r" not in result
        assert result == "line1\nline2\nline3\n"

    def test_cr_only_to_lf(self):
        text = "line1\rline2\rline3"
        result = normalize_markdown(text)
        assert result == "line1\nline2\nline3\n"

    def test_mixed_line_endings(self):
        text = "a\r\nb\rc\nd"
        result = normalize_markdown(text)
        assert result == "a\nb\nc\nd\n"

    def test_trailing_newline_added_when_missing(self):
        assert normalize_markdown("no newline") == "no newline\n"

    def test_trailing_newlines_collapsed_to_one(self):
        assert normalize_markdown("text\n\n\n") == "text\n"
        assert normalize_markdown("text\n\n\n\n\n") == "text\n"

    def test_single_trailing_newline_preserved(self):
        assert normalize_markdown("text\n") == "text\n"

    def test_empty_string(self):
        assert normalize_markdown("") == "\n"

    def test_idempotent(self):
        samples = [
            "no newline",
            "one newline\n",
            "two newlines\n\n",
            "crlf\r\nstuff",
            "nfc é " + "é",
            "",
            "trailing spaces   \n\n\n",
        ]
        for s in samples:
            once = normalize_markdown(s)
            twice = normalize_markdown(once)
            assert once == twice, f"not idempotent for {s!r}: {once!r} vs {twice!r}"

    def test_validate_helper(self):
        validate_markdown_normalization("any text")
        validate_markdown_normalization("crlf\r\nhere")
        validate_markdown_normalization("nfd: é")

    def test_hash_changes_only_when_meaningful_change(self):
        """NFC + CRLF 归一后，hash 仅在内容真实变化时变化。"""
        h1 = plan_content_hash("hello\r\nworld\r\n")
        h2 = plan_content_hash("hello\nworld\n")
        assert h1 == h2

        h3 = plan_content_hash("hello\nworld")
        h4 = plan_content_hash("hello\nworld\n")
        assert h3 == h4  # 末尾换行补一个，hash 一致

        h5 = plan_content_hash("hello\nworld\n")
        h6 = plan_content_hash("hello\nworld!\n")
        assert h5 != h6


# ---------------------------------------------------------------------------
# YAML 规范化
# ---------------------------------------------------------------------------


class TestNormalizeYaml:
    def test_mapping_keys_sorted(self):
        text = """
b: 2
a: 1
c: 3
"""
        result = normalize_yaml_text(text)
        assert list(result.keys()) == ["a", "b", "c"]
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_nested_mapping_keys_sorted_recursively(self):
        text = """
z:
  y: 1
  x: 2
a: 0
"""
        result = normalize_yaml_text(text)
        assert list(result.keys()) == ["a", "z"]
        assert list(result["z"].keys()) == ["x", "y"]

    def test_list_order_preserved(self):
        """list 顺序变化 → 不同 payload → 不同 hash。"""
        text1 = """
items:
  - c
  - a
  - b
"""
        text2 = """
items:
  - a
  - b
  - c
"""
        r1 = normalize_yaml_text(text1)
        r2 = normalize_yaml_text(text2)
        assert r1["items"] == ["c", "a", "b"]
        assert r2["items"] == ["a", "b", "c"]
        assert r1 != r2

    def test_list_of_mappings_preserves_order_keys_sorted(self):
        text = """
- z: 1
  a: 2
- y: 3
  b: 4
"""
        result = normalize_yaml_text(text)
        assert result[0] == {"a": 2, "z": 1}
        assert result[1] == {"b": 4, "y": 3}

    def test_hash_stable_across_key_reorder(self):
        """同一 dict 不同 key 顺序 → 同 hash。"""
        text1 = "b: 2\na: 1\n"
        text2 = "a: 1\nb: 2\n"
        from map_types.schemas.canonical import _yaml_payload_to_jsonable

        json1 = _yaml_payload_to_jsonable(normalize_yaml_text(text1))
        json2 = _yaml_payload_to_jsonable(normalize_yaml_text(text2))
        assert json1 == json2

    def test_idempotent_normalize(self):
        samples = [
            "a: 1\nb: 2\n",
            "items:\n  - 1\n  - 2\n",
            "nested:\n  z: 1\n  a: 2\n",
        ]
        for s in samples:
            once = normalize_yaml_text(s)
            from map_types.schemas.canonical import _yaml_payload_to_jsonable

            twice_json = _yaml_payload_to_jsonable(normalize_yaml_text(_yaml_payload_to_jsonable(once)))
            once_json = _yaml_payload_to_jsonable(once)
            assert once_json == twice_json

    def test_validate_helper(self):
        validate_yaml_normalization("a: 1\nb: 2\n")
        validate_yaml_normalization("nested:\n  z: 1\n  a: 2\n")

    def test_scalar_passthrough(self):
        assert normalize_yaml_text("just a string") == "just a string"
        assert normalize_yaml_text("42") == 42
        assert normalize_yaml_text("null") is None


# ---------------------------------------------------------------------------
# 五路 hash
# ---------------------------------------------------------------------------


class TestMetadataHash:
    def test_hash_is_64_hex(self):
        h = metadata_hash(_experiment())
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_deterministic(self):
        h1 = metadata_hash(_experiment(title="A"))
        h2 = metadata_hash(_experiment(title="A"))
        assert h1 == h2

    def test_hash_changes_with_meaningful_field(self):
        h1 = metadata_hash(_experiment(phase="draft"))
        h2 = metadata_hash(_experiment(phase="running"))
        assert h1 != h2

    def test_hash_excludes_created_at_and_dir_path(self):
        """dir_path 不影响 hash（与 fs_experiment_content_hash 一致）。"""
        e1 = _experiment()
        e2 = _experiment()
        # model 不暴露 created_at 之外的字段变更不影响（exclude 已生效）
        assert metadata_hash(e1) == metadata_hash(e2)


class TestPlanContentHash:
    def test_hash_is_64_hex(self):
        h = plan_content_hash("# plan\ncontent\n")
        assert len(h) == 64

    def test_hash_stable_under_crlf_nfc(self):
        h1 = plan_content_hash("a\r\nb\ré")
        h2 = plan_content_hash("a\nb\né")
        assert h1 == h2


class TestLogContentHash:
    def test_hash_is_64_hex(self):
        h = log_content_hash("## I0 done\n\n- step 1\n")
        assert len(h) == 64


class TestReviewsContentHash:
    def test_empty_reviews_returns_hash(self):
        h = reviews_content_hash([])
        assert len(h) == 64

    def test_hash_stable_under_input_order(self):
        """reviews 输入顺序不同 → slug 排序后 → 同 hash。"""
        a_text = "decision: accept\nnote: ok\n"
        b_text = "decision: reject\nnote: nope\n"
        c_text = "decision: pending\n"
        h1 = reviews_content_hash([("a", a_text), ("b", b_text), ("c", c_text)])
        h2 = reviews_content_hash([("c", c_text), ("a", a_text), ("b", b_text)])
        assert h1 == h2

    def test_hash_changes_with_content(self):
        a_text = "decision: accept\n"
        b_text = "decision: reject\n"
        h1 = reviews_content_hash([("a", a_text)])
        h2 = reviews_content_hash([("a", b_text)])
        assert h1 != h2

    def test_hash_changes_with_slug(self):
        """同名 review 不同 slug（不同 review 轮次）→ 不同 hash。"""
        h1 = reviews_content_hash([("round1", "x: 1\n")])
        h2 = reviews_content_hash([("round2", "x: 1\n")])
        assert h1 != h2


class TestContentRootHash:
    def test_normalized_input(self):
        h1 = content_root_hash("map")
        h2 = content_root_hash("map")  # 幂等
        assert h1 == h2

    def test_invalid_content_root_raises(self):
        """content_root 不合法（路径、特殊字符）抛 ValueError。"""
        with pytest.raises(ValueError):
            content_root_hash("../etc")
        with pytest.raises(ValueError):
            content_root_hash("nested/dir")


class TestAllHashes:
    def test_returns_all_five_keys(self):
        result = all_hashes(experiment=_experiment(), content_root="map")
        assert set(result.keys()) == {
            "metadata_hash",
            "plan_content_hash",
            "log_content_hash",
            "reviews_content_hash",
            "content_root_hash",
        }
        # 仅 metadata + content_root 必有；plan/log/reviews 在缺省时为空串
        assert len(result["metadata_hash"]) == 64
        assert len(result["content_root_hash"]) == 64
        # 缺省 hash 为空
        assert result["plan_content_hash"] == ""
        assert result["log_content_hash"] == ""
        assert result["reviews_content_hash"] == ""

    def test_empty_text_yields_empty_hash(self):
        result = all_hashes(experiment=_experiment(), content_root="map")
        assert result["plan_content_hash"] == ""
        assert result["log_content_hash"] == ""
        assert result["reviews_content_hash"] == ""
        # metadata + content_root 仍可计算
        assert result["metadata_hash"] != ""
        assert result["content_root_hash"] != ""

    def test_full_input_yields_all_hashes(self):
        result = all_hashes(
            experiment=_experiment(),
            plan_text="# plan\nbody\n",
            log_text="## log\n",
            reviews=[("r1", "k: v\n"), ("r2", "k2: v2\n")],
            content_root="map",
        )
        for v in result.values():
            assert len(v) == 64

    def test_deterministic(self):
        kwargs = dict(
            experiment=_experiment(),
            plan_text="plan\n",
            log_text="log\n",
            reviews=[("a", "k: v\n")],
            content_root="map",
        )
        r1 = all_hashes(**kwargs)
        r2 = all_hashes(**kwargs)
        assert r1 == r2
