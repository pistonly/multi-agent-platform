"""map_fs — map/ 文件夹即事实源的共享解析层（server 与 cli 复用）。

约定（零 API、零 DB，纯文件系统）::

    map/
      topics/<slug>/
        index.md                  # front-matter: title/status/round/creator/...
        round<N>-<persona>.md     # 每轮每人一个普通发言
        round<N>-summary-<persona>.md  # 独立 Round Summary
      experiments/<slug>/
        index.md                  # front-matter: title/phase/creator/...
        plan.md / log.md / review.md

核心性质：

- **实时解析**：每次调用重新扫描目录，无缓存、无状态，DB 不存内容。
- **确定性身份**：topic id / comment id 由 uuid5 从 slug / 相对路径派生，
  跨解析稳定，UI 与 API 可直接当作主键使用。
- **ack 即文件存在**：参与者在本轮有自己的普通发言文件
  即视为已发言（ack）；creator 的 Summary 独立存储，不覆盖原发言。
- **excerpt 自动生成**：取正文首个一级标题（或首个非空行），截断 200 字符。

front-matter 为 YAML（``---`` 围栏），缺失时按文件名/正文兜底推导。
本包只依赖标准库 + pyyaml，供 server 与 cli 共同使用。
"""

from map_fs.action_items import (
    parse_action_items_file,
    read_action_items,
    write_action_items,
)
from map_fs.archive import (
    ArchiveEntry,
    ArchiveStateError,
    archive_topic,
    find_experiment_references,
    rebuild_archive_index,
    scan_archive_entries,
    unarchive_topic,
)
from map_fs.frontmatter import make_excerpt, parse_front_matter, slugify
from map_fs.index_io import (
    commit_experiment_index_write,
    experiment_index_path,
    update_experiment_index,
    update_topic_index,
    validate_experiment_index_file,
    validate_experiment_index_meta,
    validate_experiment_phase_transition,
    write_experiment_index,
    write_experiment_review_yaml,
    write_round_comment,
    write_topic_index,
)
from map_fs.model import (
    DEFAULT_CONTENT_ROOT,
    EXPERIMENT_INDEX_REQUIRED,
    EXPERIMENT_PHASES,
    ExperimentIndexError,
    FsActionItem,
    FsComment,
    FsExperiment,
    FsPlane,
    FsTopic,
    FsWorkItem,
    comment_id_for_path,
    experiment_id_for_slug,
    parse_round_filename,
    topic_id_for_slug,
)
from map_fs.topic_parser import (
    derive_work,
    parse_experiment_dir,
    parse_topic_dir,
    scan_plane,
)
from map_fs.validation import (
    CLOSE_NOTE_LEGAL_FIELDS,
    CLOSE_NOTE_OPTIONAL_FIELDS,
    CLOSE_NOTE_REQUIRED_FIELDS,
    CLOSE_REASON_LEGAL,
    AckPendingError,
    InvalidCloseNoteError,
    InvalidCloseReasonError,
    OpenActionItemsError,
    OpenExperimentError,
    TopicOwnerError,
    TopicStateError,
    validate_advance_round,
    validate_close,
)

__all__ = [
    "AckPendingError",
    "ArchiveEntry",
    "ArchiveStateError",
    "CLOSE_NOTE_LEGAL_FIELDS",
    "CLOSE_NOTE_OPTIONAL_FIELDS",
    "CLOSE_NOTE_REQUIRED_FIELDS",
    "CLOSE_REASON_LEGAL",
    "DEFAULT_CONTENT_ROOT",
    "EXPERIMENT_INDEX_REQUIRED",
    "EXPERIMENT_PHASES",
    "ExperimentIndexError",
    "FsActionItem",
    "FsComment",
    "FsExperiment",
    "FsPlane",
    "FsTopic",
    "FsWorkItem",
    "InvalidCloseNoteError",
    "InvalidCloseReasonError",
    "OpenActionItemsError",
    "OpenExperimentError",
    "TopicOwnerError",
    "TopicStateError",
    "comment_id_for_path",
    "commit_experiment_index_write",
    "derive_work",
    "experiment_id_for_slug",
    "experiment_index_path",
    "make_excerpt",
    "archive_topic",
    "find_experiment_references",
    "rebuild_archive_index",
    "scan_archive_entries",
    "unarchive_topic",
    "parse_action_items_file",
    "parse_experiment_dir",
    "parse_front_matter",
    "parse_round_filename",
    "parse_topic_dir",
    "read_action_items",
    "scan_plane",
    "slugify",
    "topic_id_for_slug",
    "update_experiment_index",
    "update_topic_index",
    "validate_advance_round",
    "validate_close",
    "validate_experiment_index_file",
    "validate_experiment_index_meta",
    "validate_experiment_phase_transition",
    "write_action_items",
    "write_experiment_index",
    "write_experiment_review_yaml",
    "write_round_comment",
    "write_topic_index",
]
