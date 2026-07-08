/* tslint:disable */
/* eslint-disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

export type AcceptanceType = "migration" | "smoke" | "unit_test" | "integration" | "manual";
/**
 * Drives the cancel-reason minimum-length threshold (see ActionItemCancel).
 */
export type ActionItemCategory = "implementation" | "decision" | "unspecified";
export type AgentRole = "agent" | "admin";
export type TopicDiscussionRound = "round1" | "round2" | "ready";
export type ExperimentPhase = "draft" | "review" | "approved" | "running" | "result_review" | "done" | "cancelled";
export type ReviewItemStatus = "open" | "addressed" | "rebutted" | "resolved" | "withdrawn" | "escalated";
export type TopicStatus = "open" | "closed";
export type TopicDiscussionRound1 = "round1" | "round2" | "ready";
export type TopicActionItemStatus = "open" | "done" | "cancelled";
export type NotificationCategory = "wakeable" | "digest";
/**
 * Marker for which fingerprint dialect a Notification row carries.
 *
 * v0.9 (this version) marks every freshly-written notification with ``v2`` so
 * the runtime-waker can reject legacy ``v1`` fingerprints (``inbound:<event_id>``)
 * at the ingest gate and record ``rejection_count`` instead of resuming. The
 * version lives on the Notification row itself — classification does not look
 * at it, but the audit / three-layer join does (see PRD §5.1 + topic
 * d0df651c Round 1 Summary).
 */
export type NotificationFingerprintVersion = "v1" | "v2";
export type CommentAnchorType = "plan" | "review" | "review_item" | "comment";
export type ReviewSubstituteKind = "none" | "admin_for_others" | "admin_self_substitute";
export type ReviewArchivedReason = "auto" | "manual" | "superseded";
export type ReviewItemKind = "reasonable" | "unreasonable";
/**
 * Reviewer's per-item verdict on experiment acceptance.
 *
 * Drives ``experiment.result_review`` structured verdict files and the
 * R6 verdict breakdown in ``experiment_logs.metadata_json``. Parallel
 * pattern to ``31793f90`` ``pre_schema_log``.
 */
export type ReviewVerdict = "passed" | "failed" | "waived";
/**
 * Origin channel through which the runtime-waker received a notification.
 *
 * Phase 1 only writes ``polling``. ``sse`` and ``replay`` are reserved for
 * Phase 2 (SSE overlay + reconnect compensation) so the enum is forward-compat
 * and DB migrations don't need to widen the column later.
 */
export type InboundEventSource = "polling" | "sse" | "replay";
/**
 * Origin channel through which the runtime-waker received a notification.
 *
 * Phase 1 only writes ``polling``. ``sse`` and ``replay`` are reserved for
 * Phase 2 (SSE overlay + reconnect compensation) so the enum is forward-compat
 * and DB migrations don't need to widen the column later.
 */
export type InboundEventSource1 = "polling" | "sse" | "replay";
export type FeedbackCategory = "bug" | "suggestion" | "question" | "other";
export type FeedbackStatus = "new" | "triaged" | "in_progress" | "resolved";
export type TopicCommentKind = "user" | "system";
export type TopicCommentKind1 = "user" | "system";
export type TopicDiscussionRound2 = "round1" | "round2" | "ready";

export interface AcceptanceStatusRead {
  id: string;
  description: string;
  acceptance_type: AcceptanceType;
  evidence_provided?: boolean;
  reviewer_verdict?: string | null;
}
/**
 * Body of ``POST /api/v1/action-items/{id}/cancel`` and ``map action cancel``.
 */
export interface ActionItemCancel {
  reason: string;
  category?: ActionItemCategory | null;
}
/**
 * Payload of ``action_item.stale`` audit event.
 *
 * Fired after the 4th unanswered wake at the next 7d boundary. Pairs with admin
 * notification + creator audit-only mark; the runtime-waker stops waking the
 * assignee once ``stale_at`` is set. The event itself is independent of any
 * state transition (unlike ``action_item.completed`` / ``action_item.cancelled``):
 * it is a diagnostic signal that the open item has not progressed.
 */
export interface ActionItemStalePayload {
  action_item_id: string;
  owner_agent_id: string;
  topic_id: string;
  decision_id?: string | null;
  linked_experiment_id?: string | null;
  last_woken_at: string | null;
  wake_count: number;
  stale_after_attempt: number;
  admin_notified: boolean;
  creator_audit_only: boolean;
}
/**
 * Payload of ``action_item.wake_sent`` audit event.
 *
 * Fired by ``runtime-waker.scan_pending_action_items`` whenever ``should_wake_action_item``
 * decides to wake the assignee (T+24h / T+72h / every 7d up to 4 times). Pairs with the
 * ``action_item.stale`` event but is a distinct, lower-severity audit signal.
 */
export interface ActionItemWakeSentPayload {
  action_item_id: string;
  owner_agent_id: string;
  topic_id: string;
  decision_id?: string | null;
  linked_experiment_id?: string | null;
  wake_count: number;
  last_woken_at: string;
  first_open_at: string;
  elapsed_since_first_open_seconds: number;
  triggered_by?: string;
}
export interface AgentCreateResponse {
  id: string;
  name: string;
  role: AgentRole;
  project_id: string | null;
  project_key?: string | null;
  created_at: string;
  api_token: string;
}
export interface AgentRead {
  id: string;
  name: string;
  role: AgentRole;
  project_id: string | null;
  project_key?: string | null;
  created_at: string;
}
/**
 * Unified work snapshot for Web 待办, waker, and CLI ``map work``.
 */
export interface AgentWorkRead {
  agent: AgentRead;
  topic_progress: TopicProgressListRead;
  todos: TodoRead;
  notifications: NotificationListRead;
}
export interface TopicProgressListRead {
  items?: TopicProgressItemRead[];
  total?: number;
}
export interface TopicProgressItemRead {
  topic_id: string;
  topic_title: string;
  discussion_round: TopicDiscussionRound;
  last_comment_author_agent_id?: string | null;
  last_comment_author_name?: string | null;
  my_last_comment_id?: string | null;
  new_comments?: TopicProgressCommentRead[];
  new_comment_count?: number;
  work_items?: TopicWorkItemRead[];
}
export interface TopicProgressCommentRead {
  id: string;
  author_agent_id: string;
  author_name?: string | null;
  parent_comment_id: string | null;
  body: string;
  excerpt: string;
  created_at: string;
}
export interface TopicWorkItemRead {
  kind: string;
  priority: string;
  topic_id: string;
  topic_title: string;
  source_comment_id?: string | null;
  thread_root_id?: string | null;
  required_agent_id: string;
  reason: string;
  idempotency_key: string;
  clear_action: string;
  excerpt: string;
  created_at: string;
  discussion_round?: string | null;
  stale_since?: string | null;
  /**
   * DEPRECATED alias for stale_since.
   */
  advance_round_pending_since: string | null;
}
export interface TodoRead {
  my_open_experiments?: ExperimentSummaryRead[];
  pending_reviews?: ExperimentSummaryRead[];
  pending_result_reviews?: ExperimentSummaryRead[];
  experiment_review_informational?: ExperimentReviewInformationalRead[];
  pending_replies?: PendingReplyRead[];
  pending_plan_revisions?: PendingPlanRevisionRead[];
  pending_topic_replies?: PendingTopicReplyTodoRead[];
  pending_round_acks?: PendingRoundAckTodoRead[];
  pending_advance_rounds?: PendingAdvanceRoundTodoRead[];
  stale_open_topics?: StaleOpenTopicTodoRead[];
  my_open_topics?: TopicSummaryRead[];
  mentions?: MentionTodoRead[];
  action_items?: TopicActionItemTodoRead[];
}
export interface ExperimentSummaryRead {
  id: string;
  project_id: string;
  creator_agent_id: string;
  title: string;
  description: string | null;
  phase: ExperimentPhase;
  current_plan_version: number;
  topic_id?: string | null;
  warnings?: string[];
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  lock_holder_experiment_id?: string | null;
  lock_acquired_at?: string | null;
  lock_ttl_seconds?: number | null;
  next_attempt_at?: string | null;
  lock_skip_count?: number;
  open_unreasonable_count?: number;
  log_count?: number;
  latest_log_summary?: string | null;
  actions?: string[];
  blocked_on?: string | null;
  legacy_self_review?: boolean;
}
/**
 * Read-only experiment review snapshot for non-reviewer personas.
 */
export interface ExperimentReviewInformationalRead {
  experiment_title: string;
  phase: ExperimentPhase;
  updated_at: string;
  review_progress: string;
}
export interface PendingReplyRead {
  item_id: string;
  experiment_id: string;
  experiment_title: string;
  content: string;
  status: ReviewItemStatus;
  updated_at: string;
}
export interface PendingPlanRevisionRead {
  experiment_id: string;
  experiment_title: string;
  current_plan_version: number;
  open_unreasonable_count: number;
  blocked_on?: string;
  actions?: string[];
  updated_at: string;
}
export interface PendingTopicReplyTodoRead {
  topic_id: string;
  topic_title: string;
  comment_id: string;
  parent_comment_id?: string | null;
  thread_root_id: string;
  author_agent_id: string;
  author_name?: string | null;
  excerpt: string;
  created_at: string;
}
export interface PendingRoundAckTodoRead {
  topic_id: string;
  topic_title: string;
  discussion_round: TopicDiscussionRound;
  round_summary_count?: number;
  summary_comment_id?: string | null;
  summary_excerpt?: string | null;
  stale_since?: string | null;
  updated_at: string;
  /**
   * DEPRECATED alias for stale_since.
   */
  advance_round_pending_since: string | null;
}
/**
 * Host-owned topics where participant acks are complete and advance-round is due.
 */
export interface PendingAdvanceRoundTodoRead {
  topic_id: string;
  topic_title: string;
  discussion_round: TopicDiscussionRound;
  round_summary_count?: number;
  stale_since?: string | null;
  updated_at: string;
  /**
   * DEPRECATED alias for stale_since.
   */
  advance_round_pending_since: string | null;
}
/**
 * Host-owned open topic that has had no activity for the stale threshold.
 */
export interface StaleOpenTopicTodoRead {
  topic_id: string;
  topic_title: string;
  discussion_round: TopicDiscussionRound;
  round_summary_count?: number;
  stale_since: string;
  updated_at: string;
}
export interface TopicSummaryRead {
  id: string;
  project_id: string;
  creator_agent_id: string;
  creator_name?: string | null;
  title: string;
  description: string | null;
  status: TopicStatus;
  pinned?: boolean;
  discussion_round?: TopicDiscussionRound1;
  round_summary_count?: number;
  comment_count?: number;
  experiment_count?: number;
  last_comment_id?: string | null;
  last_comment_author_agent_id?: string | null;
  last_comment_author_name?: string | null;
  last_comment_excerpt?: string | null;
  my_comment_count?: number | null;
  dismissed_at?: string | null;
  /**
   * When this topic's pending action started waiting (round ack, reply, etc.). Renamed from advance_round_pending_since in N=2; old name remains readable until N=2.
   */
  stale_since?: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  /**
   * DEPRECATED alias for stale_since — kept readable for clients still using the old name.
   */
  advance_round_pending_since: string | null;
}
export interface MentionTodoRead {
  id: string;
  mentioned_agent_id: string;
  author_agent_id: string;
  author_name?: string | null;
  source_type: string;
  source_id: string;
  project_id: string;
  experiment_id: string | null;
  topic_id: string | null;
  excerpt: string;
  created_at: string;
  dismissed_at?: string | null;
}
export interface TopicActionItemTodoRead {
  id: string;
  decision_id: string;
  project_id: string;
  topic_id: string;
  topic_title: string;
  title: string;
  description?: string | null;
  status: TopicActionItemStatus;
  due_at?: string | null;
  linked_experiment_id?: string | null;
  linked_experiment_phase?: ExperimentPhase | null;
  wake_count?: number;
  first_open_at?: string | null;
  last_woken_at?: string | null;
  stale_at?: string | null;
  created_at: string;
  updated_at: string;
}
export interface NotificationListRead {
  items: NotificationRead[];
  total: number;
  unread_count: number;
}
export interface NotificationRead {
  id: string;
  recipient_agent_id: string;
  project_id: string | null;
  event: string;
  summary: string;
  target_type: string;
  target_id: string | null;
  payload_json: {
    [k: string]: unknown;
  } | null;
  category?: NotificationCategory;
  group_key?: string | null;
  wake_version?: number;
  fingerprint_version?: NotificationFingerprintVersion;
  event_count?: number;
  first_event_at?: string | null;
  last_event_at?: string | null;
  read_at: string | null;
  created_at: string;
  updated_at?: string | null;
}
/**
 * 6-bucket by_kind summary returned by ``map work --summary``.
 *
 * Compact view intended for waker quick-scans and the Web /work top card.
 * Full per-partition detail stays in ``AgentWorkRead``.
 */
export interface AgentWorkSummaryRead {
  agent: AgentRead;
  buckets?: SummaryBucket[];
  topics_needing_attention?: number;
  experiments_needing_attention?: number;
  /**
   * f873c287 I1(e): per-phase_owner breakdown of the experiment attention
   * counter. Keys are PhaseOwner.value strings ("host" / "reviewer" /
   * "participant" / "admin"). Visibility-filtered: under
   * `visibility_filter_applied: true` the `host` key is dropped because the
   * underlying bucket is host_only.
   */
  experiments_needing_attention_by_owner?: { [key: string]: number };
  topics_truncated?: number;
  experiments_truncated?: number;
  visibility_filter_applied?: boolean;
  topics_limit?: number;
  experiments_limit?: number;
}
/**
 * One of the 6 summary buckets exposed by ``map work --summary``.
 *
 * A bucket is a kind-based aggregation of action items / context items so the
 * UI can render a top-of-page card without scanning the full partition
 * list.
 */
export interface SummaryBucket {
  kind: "mention" | "round_ack" | "pending_reply" | "explicit_only" | "informational_only" | "action_items";
  count: number;
  visibility?: "all" | "host_only" | "reviewer_only" | "participant_only";
  items?: SummaryBucketItem[];
  top_excerpt?: string | null;
  /**
   * DEPRECATED alias for visibility — kept readable for clients still using the old name.
   */
  partition_visibility: "all" | "host_only" | "reviewer_only" | "participant_only";
}
/**
 * A short summary of one item inside a bucket. The full item is in
 * ``map work``'s ``todos`` and ``topic_progress``; this is the slice
 * rendered on the /work summary card.
 */
export interface SummaryBucketItem {
  kind: "mention" | "round_ack" | "pending_reply" | "explicit_only" | "informational_only" | "action_items";
  topic_id?: string | null;
  topic_title?: string | null;
  excerpt?: string | null;
  updated_at?: string | null;
}
export interface AuditLogRead {
  id: string;
  agent_id: string | null;
  project_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  summary: string | null;
  payload_json: {
    [k: string]: unknown;
  } | null;
  created_at: string;
}
export interface CommentCreate {
  anchor_type: CommentAnchorType;
  anchor_id: string;
  parent_id?: string | null;
  body: string;
}
export interface CommentRead {
  id: string;
  experiment_id: string;
  anchor_type: CommentAnchorType;
  anchor_id: string;
  parent_comment_id: string | null;
  author_agent_id: string;
  author_name?: string | null;
  body: string;
  created_at: string;
  unresolved_mentions?: string[];
}
export interface CommentTreeNode {
  id: string;
  experiment_id: string;
  anchor_type: CommentAnchorType;
  anchor_id: string;
  parent_comment_id: string | null;
  author_agent_id: string;
  author_name?: string | null;
  body: string;
  created_at: string;
  unresolved_mentions?: string[];
  children?: CommentTreeNode[];
}
export interface DismissAllMentionsResultRead {
  dismissed: number;
}
export interface DismissMentionResultRead {
  id: string;
  dismissed_at: string;
}
/**
 * Aggregated experiment page payload (detail + plans + reviews + comment tree + logs).
 */
export interface ExperimentBundleRead {
  experiment: ExperimentDetailRead;
  plans?: PlanVersionRead[];
  reviews?: ReviewRead[];
  comments?: CommentTreeNode[];
  logs?: ExperimentLogRead[];
}
export interface ExperimentDetailRead {
  id: string;
  project_id: string;
  creator_agent_id: string;
  title: string;
  description: string | null;
  phase: ExperimentPhase;
  current_plan_version: number;
  topic_id?: string | null;
  warnings?: string[];
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  lock_holder_experiment_id?: string | null;
  lock_acquired_at?: string | null;
  lock_ttl_seconds?: number | null;
  next_attempt_at?: string | null;
  lock_skip_count?: number;
  open_unreasonable_count?: number;
  log_count?: number;
  latest_log_summary?: string | null;
  actions?: string[];
  blocked_on?: string | null;
  legacy_self_review?: boolean;
  current_plan?: PlanVersionRead | null;
  plan_version_count?: number;
  review_count?: number;
  acceptance_status?: AcceptanceStatusRead[];
}
export interface PlanVersionRead {
  id: string;
  experiment_id: string;
  version: number;
  content_md: string;
  author_agent_id: string;
  change_note: string | null;
  created_at: string;
}
export interface ReviewRead {
  id: string;
  experiment_id: string;
  reviewer_agent_id: string;
  plan_version: number;
  substitute_kind?: ReviewSubstituteKind;
  created_at: string;
  archived_at?: string | null;
  archived_reason?: ReviewArchivedReason | null;
  items?: ReviewItemRead[];
}
export interface ReviewItemRead {
  id: string;
  review_id: string;
  kind: ReviewItemKind;
  content: string;
  status: ReviewItemStatus | null;
  created_at: string;
  updated_at: string;
  /**
   * Populated server-side from the latest accept-result verdict_file where verdict='waived' for this item. Read-only convenience field for participant-facing UIs; the source of truth is experiment_logs.metadata_json.verdict_file.
   */
  waived_reason?: string | null;
}
export interface ExperimentLogRead {
  id: string;
  experiment_id: string;
  author_agent_id: string;
  summary: string;
  content_md: string;
  metadata_json: {
    [k: string]: unknown;
  } | null;
  created_at: string;
}
export interface ExperimentComplete {
  summary: string;
  content_md: string;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface ExperimentCreate {
  title: string;
  description?: string | null;
  plan: PlanInput;
  submit_for_review?: boolean;
  topic_id?: string | null;
}
export interface PlanInput {
  content_md: string;
  change_note?: string | null;
}
/**
 * Per-project execution-lock snapshot (CP-3).
 *
 * 定义在 ``map_types.schemas`` 以便 server API 与 SDK 共享同一响应模型；
 * server 端用 ``ExperimentLockRead.model_validate(result)`` 从 lock_service
 * 的结果对象构造（``ORMModel`` 已开启 ``from_attributes``）。
 */
export interface ExperimentLockRead {
  experiment_id: string;
  project_id: string;
  holder?: string | null;
  acquired_at?: string | null;
  ttl_seconds?: number | null;
  next_attempt_at?: string | null;
  skip_count?: number;
}
export interface ExperimentLockStalledScanRead {
  notification_ids: string[];
  emitted_count: number;
}
export interface ExperimentLogCreate {
  summary: string;
  content_md: string;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface ExperimentResultDecision {
  summary: string;
  content_md: string;
  metadata?: {
    [k: string]: unknown;
  } | null;
  /**
   * Optional structured verdict file (CLI: --review-verdict-file). When provided, server validates item_id.review_id ownership and records pre_schema_accept_result='false' + verdict breakdown in log metadata. Omit (legacy) → pre_schema_accept_result='true' with an info-level warning.
   */
  verdict_file?: ReviewVerdictFile | null;
}
export interface ReviewVerdictFile {
  review_id: string;
  verdicts?: ReviewVerdictItem[];
  invariants?: ReviewInvariantCheck[];
}
export interface ReviewVerdictItem {
  item_id: string;
  verdict: ReviewVerdict;
  /**
   * Required when verdict == waived; otherwise optional.
   */
  reason?: string | null;
}
export interface ReviewInvariantCheck {
  item_id: string;
  verified: boolean;
  note?: string | null;
}
export interface ExperimentUpdate {
  title?: string | null;
  description?: string | null;
  archived?: boolean | null;
}
export interface GlobalStatusRead {
  total_experiments_by_phase: {
    [k: string]: number;
  };
  projects: ProjectStatusRead[];
  recent_experiments: ExperimentSummaryRead[];
}
export interface ProjectStatusRead {
  project: ProjectRead;
  experiment_counts_by_phase: {
    [k: string]: number;
  };
  active_experiments?: ExperimentSummaryRead[];
  recent_experiments: ExperimentSummaryRead[];
  open_topics?: TopicSummaryRead[];
  status_version?: number;
  status_md?: string | null;
  status_updated_at?: string | null;
}
export interface ProjectRead {
  id: string;
  project_key: string;
  name: string;
  workspace_path: string;
  description: string | null;
  current_status_version: number;
  created_at: string;
  archived_at: string | null;
}
/**
 * Waker-side record of a notification it intends to act on.
 *
 * ``fingerprint`` is the dedup key — server enforces ``UNIQUE(fingerprint)``
 * and returns 409 Conflict on replay. ``event_id`` should match the upstream
 * ``notification.id`` so the three audit layers stay joinable.
 */
export interface InboundEventCreate {
  event_id: string;
  event_type: string;
  source?: InboundEventSource;
  fingerprint: string;
  payload?: {
    [k: string]: unknown;
  } | null;
}
export interface InboundEventRead {
  id: string;
  agent_id: string;
  event_id: string;
  event_type: string;
  source: InboundEventSource1;
  fingerprint: string;
  payload: {
    [k: string]: unknown;
  } | null;
  received_at: string;
  acked_at: string | null;
  rejection_count?: number;
}
/**
 * Return shape for ``POST /me/inbound-events``.
 *
 * ``status="recorded"`` → first time, waker may proceed.
 * ``status="duplicate"`` → fingerprint already existed; treat as already woken
 * (Phase 1 server gate; see plan D6).
 * ``status="rejected_v1"`` → legacy v1 fingerprint (``inbound:<event_id>``);
 * inbound_event row is persisted (or upserted) with ``rejection_count`` bumped
 * so the audit table still records the sighting, but the waker MUST NOT
 * resume — see plan I2 / M30A acceptance #4.
 */
export interface InboundEventRecordResult {
  status: "recorded" | "duplicate" | "rejected_v1";
  event: InboundEventRead;
}
export interface ORMModel {}
export interface PlanRevise {
  content_md: string;
  change_note?: string | null;
  addressed_item_ids?: string[];
}
/**
 * A free-text feedback entry any authenticated agent may submit.
 *
 * `project_id` is an optional source-context hint (the project the agent was
 * working in); it is NOT an access boundary — feedback is platform-wide.
 */
export interface PlatformFeedbackCreate {
  body: string;
  project_id?: string | null;
  category?: FeedbackCategory | null;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface PlatformFeedbackRead {
  id: string;
  author_agent_id: string;
  author_name?: string | null;
  project_id: string | null;
  body: string;
  category: FeedbackCategory | null;
  status: FeedbackStatus;
  metadata_json: {
    [k: string]: unknown;
  } | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}
/**
 * Admin-only triage fields.
 */
export interface PlatformFeedbackUpdate {
  status?: FeedbackStatus | null;
  category?: FeedbackCategory | null;
  archived?: boolean | null;
}
export interface ProjectCreate {
  project_key: string;
  name: string;
  workspace_path: string;
  description?: string | null;
}
export interface ProjectStatusRevise {
  content_md: string;
  change_note?: string | null;
}
export interface ProjectStatusVersionRead {
  id: string;
  project_id: string;
  version: number;
  content_md: string;
  author_agent_id: string;
  change_note: string | null;
  created_at: string;
}
export interface ProjectUpdate {
  name?: string | null;
  workspace_path?: string | null;
  description?: string | null;
  archived?: boolean | null;
}
export interface ReviewCreate {
  reasonable_items?: string[];
  unreasonable_items?: string[];
  /**
   * Required when admin submits a substitute review (admin_for_others).
   */
  substitute_reason?: string | null;
}
export interface ReviewItemUpdate {
  status: ReviewItemStatus;
}
export interface TopicActionItemCreate {
  title: string;
  description?: string | null;
  owner_agent_id?: string | null;
  due_at?: string | null;
  linked_experiment_id?: string | null;
  category?: ActionItemCategory | null;
  id?: string | null;
}
export interface TopicActionItemRead {
  id: string;
  decision_id: string;
  project_id: string;
  topic_id: string;
  title: string;
  description?: string | null;
  owner_agent_id?: string | null;
  owner_name?: string | null;
  status: TopicActionItemStatus;
  due_at?: string | null;
  linked_experiment_id?: string | null;
  linked_experiment_phase?: ExperimentPhase | null;
  category?: ActionItemCategory | null;
  cancel_reason?: string | null;
  suggested_linked_experiment_id?: string | null;
  suggested_linked_experiment_title?: string | null;
  wake_count?: number;
  first_open_at?: string | null;
  last_woken_at?: string | null;
  stale_at?: string | null;
  created_at: string;
  updated_at: string;
}
export interface TopicAdvanceRound {
  increment_summary?: boolean;
  acknowledged_by?: string[];
  ack?: ("accept" | "reject" | "dismiss") | null;
}
export interface TopicCommentCreate {
  body: string;
  parent_id?: string | null;
}
export interface TopicCommentRead {
  id: string;
  topic_id: string;
  author_agent_id: string;
  author_name?: string | null;
  parent_comment_id: string | null;
  body: string;
  kind?: TopicCommentKind;
  comment_seq: number;
  created_at: string;
  unresolved_mentions?: string[];
}
export interface TopicCommentTreeNode {
  id: string;
  topic_id: string;
  author_agent_id: string;
  author_name?: string | null;
  parent_comment_id: string | null;
  body: string;
  kind?: TopicCommentKind1;
  comment_seq: number;
  created_at: string;
  unresolved_mentions?: string[];
  children?: TopicCommentTreeNode[];
}
export interface TopicCreate {
  title: string;
  description?: string | null;
}
export interface TopicDecisionRead {
  id: string;
  project_id: string;
  topic_id: string;
  topic_title?: string | null;
  author_agent_id: string;
  author_name?: string | null;
  decision?: string | null;
  rationale?: string | null;
  rejected_options?: string | null;
  open_questions?: string | null;
  no_decision_reason?: string | null;
  action_items?: TopicActionItemRead[];
  created_at: string;
  updated_at: string;
}
export interface TopicRead {
  id: string;
  project_id: string;
  creator_agent_id: string;
  creator_name?: string | null;
  title: string;
  description: string | null;
  status: TopicStatus;
  pinned?: boolean;
  discussion_round?: TopicDiscussionRound2;
  round_summary_count?: number;
  comment_count?: number;
  experiment_count?: number;
  last_comment_id?: string | null;
  last_comment_author_agent_id?: string | null;
  last_comment_author_name?: string | null;
  last_comment_excerpt?: string | null;
  my_comment_count?: number | null;
  dismissed_at?: string | null;
  /**
   * When this topic's pending action started waiting (round ack, reply, etc.). Renamed from advance_round_pending_since in N=2; old name remains readable until N=2.
   */
  stale_since?: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  experiments?: ExperimentSummaryRead[];
  comments?: TopicCommentTreeNode[];
  decision?: TopicDecisionRead | null;
  /**
   * DEPRECATED alias for stale_since — kept readable for clients still using the old name.
   */
  advance_round_pending_since: string | null;
}
export interface TopicReadCursorRead {
  topic_id: string;
  agent_id: string;
  last_read_comment_seq: number;
  updated_at: string;
}
export interface TopicResolve {
  decision?: string | null;
  rationale?: string | null;
  rejected_options?: string | null;
  open_questions?: string | null;
  no_decision_reason?: string | null;
  action_items?: TopicActionItemCreate[];
}
export interface TopicUpdate {
  title?: string | null;
  description?: string | null;
  pinned?: boolean | null;
  archived?: boolean | null;
}
export interface WebhookCreate {
  url: string;
  events?: string[];
  project_id?: string | null;
}
export interface WebhookCreateResponse {
  id: string;
  project_id: string | null;
  url: string;
  events: string[];
  active: boolean;
  created_at: string;
  secret: string;
}
export interface WebhookDeliveryRead {
  id: string;
  webhook_id: string;
  event: string;
  payload: {
    [k: string]: unknown;
  };
  status_code: number | null;
  attempts: number;
  success: boolean;
  last_attempt_at: string | null;
  last_error: string | null;
  created_at: string;
}
export interface WebhookRead {
  id: string;
  project_id: string | null;
  url: string;
  events: string[];
  active: boolean;
  created_at: string;
}
export interface WebhookUpdate {
  url?: string | null;
  events?: string[] | null;
  active?: boolean | null;
}
