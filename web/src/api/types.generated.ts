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
export type AgentRole1 = "agent" | "admin";
export type ExperimentPhase = "draft" | "review" | "approved" | "running" | "result_review" | "done" | "cancelled";
/**
 * Experiment lifecycle mode (v0.10).
 *
 * ``standard`` — full lifecycle with reviewer gates (draft → review →
 * approved → running → result_review → done). Default for backward compat.
 *
 * ``direct`` — fast path without reviewer gates (draft → running → done).
 * Host creates the plan and delegates execution to a participant via
 * ``--executor``; no review or result_review phase is entered.
 */
export type ExperimentMode = "standard" | "direct";
/**
 * Decision-owner role for each ``ExperimentPhase`` (experiment f873c287 I1(b)).
 *
 * Maps which persona holds the decision authority to *advance* a given
 * phase — used by ``informational_only`` auto-classification
 * (I1(a): ``actions=[] AND blocked_on AND phase_owner != host``) and by
 * the UI "host blocked, waiting on {phase_owner}" copy (I1(d)).
 *
 * Semantics — "decision owner", not "executor":
 * - ``draft``      → host    (creator drafts the plan)
 * - ``review``     → reviewer (non-creator reviews & submits verdict)
 * - ``revise``     → host    (revising is a host decision during review)
 * - ``approved``   → host    (host decides to start)
 * - ``running``    → host    (host owns execution)
 * - ``result_review`` → reviewer (non-creator reviews result)
 * - ``done``       → host    (host owns archival / follow-ups)
 * - ``cancelled``  → host    (host decides to cancel; admin can override)
 */
export type PhaseOwner = "host" | "reviewer" | "participant" | "admin";
export type ReviewItemStatus = "open" | "addressed" | "rebutted" | "resolved" | "withdrawn" | "escalated" | "closed";
export type TopicStatus = "open" | "closed";
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
/**
 * Experiment lifecycle mode (v0.10).
 *
 * ``standard`` — full lifecycle with reviewer gates (draft → review →
 * approved → running → result_review → done). Default for backward compat.
 *
 * ``direct`` — fast path without reviewer gates (draft → running → done).
 * Host creates the plan and delegates execution to a participant via
 * ``--executor``; no review or result_review phase is entered.
 */
export type ExperimentMode1 = "standard" | "direct";
/**
 * Decision-owner role for each ``ExperimentPhase`` (experiment f873c287 I1(b)).
 *
 * Maps which persona holds the decision authority to *advance* a given
 * phase — used by ``informational_only`` auto-classification
 * (I1(a): ``actions=[] AND blocked_on AND phase_owner != host``) and by
 * the UI "host blocked, waiting on {phase_owner}" copy (I1(d)).
 *
 * Semantics — "decision owner", not "executor":
 * - ``draft``      → host    (creator drafts the plan)
 * - ``review``     → reviewer (non-creator reviews & submits verdict)
 * - ``revise``     → host    (revising is a host decision during review)
 * - ``approved``   → host    (host decides to start)
 * - ``running``    → host    (host owns execution)
 * - ``result_review`` → reviewer (non-creator reviews result)
 * - ``done``       → host    (host owns archival / follow-ups)
 * - ``cancelled``  → host    (host decides to cancel; admin can override)
 */
export type PhaseOwner1 = "host" | "reviewer" | "participant" | "admin";
export type ReviewSubstituteKind = "none" | "admin_for_others" | "admin_self_substitute";
/**
 * Reason a ``Review`` row was archived.
 *
 * ``auto`` — archived automatically by ``plan_revise`` because the host
 * bumped ``current_plan_version`` and this row is no longer canonical.
 * Also used as the historical backfill marker for rows that predate
 * the archive feature (the UI renders those with a
 * ``(pre-archive, all reviews shown)`` hint).
 * ``manual`` — archived explicitly by an admin or host (e.g. duplicate
 * review row, withdrawn reviewer).
 * ``superseded`` — the review's plan_version was explicitly superseded
 * by a later authoritative review at the same plan_version (rare;
 * reserved for the future review-amendment flow).
 */
export type ReviewArchivedReason = "auto" | "manual" | "superseded";
export type ReviewItemKind = "reasonable" | "unreasonable";
export type ResolutionReason = "resolved" | "rebutted" | "superseded";
/**
 * Experiment lifecycle mode (v0.10).
 *
 * ``standard`` — full lifecycle with reviewer gates (draft → review →
 * approved → running → result_review → done). Default for backward compat.
 *
 * ``direct`` — fast path without reviewer gates (draft → running → done).
 * Host creates the plan and delegates execution to a participant via
 * ``--executor``; no review or result_review phase is entered.
 */
export type ExperimentMode2 = "standard" | "direct";
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

export interface A2ATaskListRead {
  tasks?: A2ATaskRead[];
  total?: number;
  protocol: string;
}
/**
 * A2A Task 投影条目：MAP 话题轮次 / 实验生命周期 → Task。
 */
export interface A2ATaskRead {
  id: string;
  kind: string;
  name: string;
  status: string;
  map_ref: string;
  updated_at?: string | null;
}
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
export interface AgentCardListRead {
  items?: AgentCardRead[];
  total?: number;
  protocol: string;
}
export interface AgentCardRead {
  id: string;
  name: string;
  description: string;
  url: string;
  skills?: AgentCardSkillRead[];
  capabilities?: string[];
  protocol: string;
  project_id?: string | null;
}
/**
 * 卡片 skills 条目：id + name 最小对（评审建议 2）。
 */
export interface AgentCardSkillRead {
  id: string;
  name: string;
}
/**
 * Body for ``POST /api/v1/agents`` (cleanup experiment f12a5638 P2 #5).
 *
 * Replaces the legacy query-param creation contract so the endpoint
 * follows the same body-driven pattern as every other write endpoint
 * in the API. ``project_id`` and ``project_key`` are both accepted for
 * caller convenience — at most one must resolve to a project for
 * ``role=agent`` (admin-only). When both are omitted the service layer
 * raises ``ValueError`` which maps to 400.
 */
export interface AgentCreate {
  name: string;
  role?: AgentRole;
  project_id?: string | null;
  project_key?: string | null;
}
export interface AgentCreateResponse {
  id: string;
  name: string;
  role: AgentRole1;
  project_id: string | null;
  project_key?: string | null;
  created_at: string;
  api_token: string;
}
export interface AgentRead {
  id: string;
  name: string;
  role: AgentRole1;
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
  discussion_round: string;
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
  executor_agent_id?: string | null;
  title: string;
  description: string | null;
  phase: ExperimentPhase;
  mode?: ExperimentMode;
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
  phase_owner?: PhaseOwner;
  informational_only?: boolean;
  hidden_for_current_persona?: boolean;
  template_validation?: TemplateValidationSchema | null;
  plan_file_path?: string | null;
  log_file_path?: string | null;
}
/**
 * Soft validation result for a result submission (b72d0542 I1.b).
 *
 * Mirrors :class:`EvidenceValidationSchema` (8ac93d4e I1.c): ``valid``
 * is always True (soft validation never blocks ``experiment complete``).
 * ``warnings`` lists missing 4-段 sections or malformed markdown links
 * detected in the ``## 实施 log`` section body.
 */
export interface TemplateValidationSchema {
  warnings?: TemplateWarningSchema[];
  sections_present?: string[];
  log_link_count?: number;
  valid?: boolean;
}
export interface TemplateWarningSchema {
  code: "MISSING_TEMPLATE_SECTION" | "NO_LINK_IN_LOG_SECTION" | "MALFORMED_MARKDOWN_LINK";
  section?: string | null;
  detail?: string | null;
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
  discussion_round: string;
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
  discussion_round: string;
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
  discussion_round: string;
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
  slug?: string | null;
  status: TopicStatus;
  pinned?: boolean;
  discussion_round?: string;
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
  close_reason?: string | null;
  close_note?: string | null;
  content_source?: string;
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
  experiments_needing_attention_by_owner?: {
    [k: string]: number;
  };
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
export interface BootstrapAgentResult {
  persona: string;
  agent_id: string;
  agent_name: string;
  api_token: string;
}
/**
 * Body for ``POST /api/v1/bootstrap`` — self-service project + persona agents.
 *
 * Lets a new user create a project and the 3 default persona agents
 * (host/participant/reviewer) in a single atomic call without an admin
 * token. Returns the plaintext API tokens (shown once).
 */
export interface BootstrapRequest {
  project_key: string;
  project_name: string;
  workspace_path: string;
  description?: string | null;
}
export interface BootstrapResponse {
  project: ProjectRead;
  agents: BootstrapAgentResult[];
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
export interface CrossPersonaCallRecord {
  visibility_diff?: {
    [k: string]: unknown;
  };
  result_partition_count?: number;
  diff_size?: number;
}
export interface DismissAllMentionsResultRead {
  dismissed: number;
}
export interface DismissMentionResultRead {
  id: string;
  dismissed_at: string;
}
/**
 * Resolution of a STATE_MACHINE.* error's escalation contact.
 *
 * Returned by ``GET /api/v1/agents/me/escalation-target?experiment_id=...``.
 * The CLI uses this on ``MAPHTTPError`` with a STATE_MACHINE.* error_code
 * so the user knows who to ping about a state-machine refusal.
 */
export interface EscalationTargetRead {
  experiment_id: string | null;
  escalation_target_id: string | null;
  escalation_label: string;
  tier: string;
}
/**
 * Soft validation result for an ``ExperimentLogCreate`` payload.
 *
 * The validator never blocks the log save — ``valid`` is always True.
 * ``warnings`` lists evidence_keys declared in plan frontmatter that are
 * missing from the supplied metadata; ``parse_error`` is set when the
 * plan frontmatter existed but its YAML failed to parse.
 */
export interface EvidenceValidationSchema {
  warnings?: EvidenceWarningSchema[];
  parse_error?: string | null;
  plan_keys?: string[];
  valid?: boolean;
}
export interface EvidenceWarningSchema {
  code: "MISSING_EVIDENCE_KEY";
  missing_key: string;
  plan_required?: boolean;
  log_provided?: boolean;
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
  executor_agent_id?: string | null;
  title: string;
  description: string | null;
  phase: ExperimentPhase;
  mode?: ExperimentMode1;
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
  phase_owner?: PhaseOwner1;
  informational_only?: boolean;
  hidden_for_current_persona?: boolean;
  template_validation?: TemplateValidationSchema | null;
  plan_file_path?: string | null;
  log_file_path?: string | null;
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
  last_resolution_reason?: ResolutionReason | null;
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
  file_path?: string | null;
  metadata_json: {
    [k: string]: unknown;
  } | null;
  created_at: string;
}
export interface ExperimentComplete {
  summary: string;
  content_md?: string | null;
  metadata?: {
    [k: string]: unknown;
  } | null;
  log_file_path?: string | null;
}
export interface ExperimentCreate {
  title: string;
  description?: string | null;
  plan: PlanInput;
  submit_for_review?: boolean;
  topic_id?: string | null;
  mode?: ExperimentMode2;
  plan_file_path?: string | null;
}
export interface PlanInput {
  content_md?: string | null;
  change_note?: string | null;
  file_path?: string | null;
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
  content_md?: string | null;
  file_path?: string | null;
  metadata?: {
    [k: string]: unknown;
  } | null;
  force_skip_similarity?: boolean;
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
/**
 * Optional body for ``POST /experiments/{id}/start`` (migration 042).
 *
 * When omitted (or ``executor_agent_id`` is null) the host self-executes
 * and the server populates ``experiments.executor_agent_id`` with the
 * caller's id. When set, that agent becomes the sole non-admin caller
 * allowed to ``complete`` the experiment.
 */
export interface ExperimentStart {
  executor_agent_id?: string | null;
}
export interface ExperimentUpdate {
  title?: string | null;
  description?: string | null;
  archived?: boolean | null;
}
/**
 * 验证型写：推进轮次（服务端校验 ack 后写回 index.md）。
 *
 * 远程/容器部署（server 看不到 workspace）时携带 ``evidence``——客户端
 * 本地解析的话题快照，server 据此校验 ack 完整性并签发写回 verdict。
 */
export interface FsAdvanceRoundRequest {
  waive_ack?: boolean;
  waive_reason?: string | null;
  mark_ready?: boolean;
  base_revision?: number | null;
  evidence?: FsTopicDetailRead | null;
}
export interface FsTopicDetailRead {
  id: string;
  slug: string;
  title: string;
  description?: string;
  status?: string;
  discussion_round?: string;
  creator: string;
  comment_count?: number;
  participants?: string[];
  created_at?: string | null;
  updated_at?: string | null;
  dir_path: string;
  comments?: FsCommentRead[];
}
/**
 * 一条评论 = map/topics/<slug>/round<N>-<persona>.md。
 */
export interface FsCommentRead {
  id: string;
  topic_slug: string;
  round: number;
  author: string;
  kind?: string;
  is_round_summary?: boolean;
  excerpt?: string;
  content?: string;
  file_path: string;
  posted_at?: string | null;
  comment_seq: number;
}
/**
 * 验证型写：关闭话题（写回 index.md 的 status/close_reason）。
 */
export interface FsCloseRequest {
  close_reason?: string | null;
  close_note?: string | null;
  base_revision?: number | null;
  evidence?: FsTopicDetailRead | null;
}
/**
 * 一个实验内容包 = map/experiments/<slug>/ 文件夹。
 */
export interface FsExperimentRead {
  id: string;
  slug: string;
  title: string;
  description?: string;
  phase?: string;
  creator: string;
  created_at?: string | null;
  dir_path: string;
  plan_path?: string | null;
  log_path?: string | null;
  review_path?: string | null;
}
/**
 * server 视角的 FS plane 可达状态（部署矩阵探测握手）。
 *
 * - ``local-fs``：server 能直接读 ``<workspace>/<content_root>/``（同机部署
 *   或容器内同路径挂载），实时解析 + 服务端写回均可用。
 * - ``projection-cache``：workspace 不可达，但存在 ``map fs push`` 上行的
 *   投影缓存——读路径回退到缓存，验证型写走 validate → 本地写回 → commit。
 * - ``detached``：两者皆无，FS plane 对 server 不可见（读写链路均断，
 *   ``hint`` 给出修复指引）。
 */
export interface FsPlaneStatusRead {
  workspace_path: string;
  content_root: string;
  workspace_exists: boolean;
  content_root_exists: boolean;
  mode: string;
  projection_pushed_at?: string | null;
  projection_revision?: number | null;
  publisher_agent_id?: string | null;
  consistency_model?: string | null;
  hint?: string;
}
/**
 * 投影缓存元信息（不含正文，供 status / UI 展示）。
 */
export interface FsProjectionMetaRead {
  pushed_at: string;
  pushed_by_agent_id?: string | null;
  publisher_agent_id?: string | null;
  owner_agent_id?: string | null;
  client_workspace: string;
  topic_count: number;
  experiment_count: number;
  projection_revision?: number;
  content_hash?: string | null;
  consistency_model?: string;
}
/**
 * ``map fs push`` 上行的 FS plane 投影（远程/容器部署的读侧回退源）。
 *
 * 内容主权仍在本地文件：这里只是 server 侧的只读投影缓存，push 幂等
 * 覆盖。``topics`` 携带评论元数据与正文（供 Web UI / work 投影离线渲染）。
 */
export interface FsProjectionPushRequest {
  /**
   * 推送端本地 workspace 绝对路径（审计用）
   */
  client_workspace: string;
  /**
   * CAS 基线；首次 push 为空，已有投影时必须等于当前 revision
   */
  base_revision?: number | null;
  /**
   * topics/experiments 规范化内容的 SHA-256；server 会复核
   */
  content_hash?: string | null;
  topics?: FsTopicDetailRead[];
  experiments?: FsExperimentRead[];
  /**
   * 缺省由 server 盖当前时间戳
   */
  pushed_at?: string | null;
}
/**
 * 一个话题 = map/topics/<slug>/ 文件夹（index.md + 评论文件）。
 */
export interface FsTopicSummaryRead {
  id: string;
  slug: string;
  title: string;
  description?: string;
  status?: string;
  discussion_round?: string;
  creator: string;
  comment_count?: number;
  participants?: string[];
  created_at?: string | null;
  updated_at?: string | null;
  dir_path: string;
}
/**
 * 从文件推导的协作待办（waker / CLI 共用的纯函数产物）。
 */
export interface FsWorkItemRead {
  kind: string;
  topic_slug: string;
  title: string;
  round: number;
  detail: string;
}
/**
 * 本地写回完成后的 commit 请求（凭 validate 签发的 token）。
 */
export interface FsWriteCommitRequest {
  token: string;
  slug: string;
  /**
   * advance-round | close（与 validate 的 action 一致）
   */
  action: string;
  /**
   * 客户端实际写回的字段（与 verdict.fields 比对，不一致时 409）
   */
  applied_fields?: {
    [k: string]: string;
  };
}
export interface FsWriteCommitResponse {
  accepted?: boolean;
  action: string;
  slug: string;
  projection_revision?: number | null;
}
/**
 * 验证型写（validate 阶段）判定结果。
 *
 * ``allowed=True`` 时 ``fields`` 是 CLI 应本地写回 index.md 的 front-matter
 * 字段；``token`` 是 server 签发的短时 HMAC 凭证，本地写回完成后凭它
 * commit（审计 + 通知 + 投影缓存刷新）。防伪造 verdict，不防恶意客户端
 * （本地文件主权本就在 Agent 侧）。
 */
export interface FsWriteVerdictRead {
  action: string;
  allowed?: boolean;
  slug: string;
  fields?: {
    [k: string]: string;
  };
  token: string;
  expires_at: string;
  base_revision?: number;
  topic: FsTopicSummaryRead;
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
/**
 * Wrapper returned by ``POST /experiments/{id}/logs`` (8ac93d4e I1.c).
 *
 * The persisted ``log`` is unchanged from ``ExperimentLogRead`` so v1
 * consumers can still parse the response by reaching into ``log``. The
 * new ``validation`` field surfaces soft evidence-key warnings without
 * breaking v1 schema.
 *
 * b72d0542 I1.b(2)(e): ``similarity_warning`` carries the soft
 * content-similarity check result (always None on endpoints other than
 * ``POST /experiments/{id}/logs``). When the warning fires AND
 * ``force_skip_similarity=True`` was supplied, the warning is suppressed
 * in this response and a ``log.force_skip`` audit row is written
 * instead. ``force_skip`` field echoes whether the caller opted in
 * (False on responses without a similarity check).
 */
export interface LogCreateResponse {
  log: ExperimentLogRead;
  validation: EvidenceValidationSchema;
  similarity_warning?: SimilarityWarningSchema | null;
  force_skip?: boolean;
  similarity_skipped?: string | null;
  summary_repeat_hint?: string | null;
}
/**
 * Soft warning fired when the new log body is too similar to a
 * previous log on the same experiment (b72d0542 I1.b(2)(b)).
 *
 * ``score`` is the cosine similarity in ``[0.0, 1.0]`` between the new
 * log's content embedding and the most-similar previous log on the
 * same experiment. ``threshold`` is the warn threshold (plan default
 * 0.7). ``ref_log_id`` points to the previous log the score was
 * computed against. ``model`` is the embedding model id used (e.g.
 * ``sentence-transformers/all-MiniLM-L6-v2``).
 *
 * The warning is non-blocking; callers may pass
 * ``force_skip_similarity=True`` to suppress it AND write a
 * ``log.force_skip`` audit row instead.
 */
export interface SimilarityWarningSchema {
  code: "HIGH_CONTENT_SIMILARITY";
  score: number;
  threshold: number;
  ref_log_id: string;
  model: string;
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
/**
 * Body for ``POST /api/v1/bootstrap/reissue`` — reissue one agent token.
 *
 * Trust model mirrors ``POST /bootstrap``: the ``project_key`` acts as
 * the self-service proof of project ownership (it is committed in
 * ``.map/config.yaml``). Reissuing immediately revokes the previous
 * token, so a lost ``.map/agents.local.yaml`` is recoverable.
 */
export interface TokenReissueRequest {
  project_key: string;
  agent_name: string;
}
export interface TokenReissueResponse {
  agent_id: string;
  agent_name: string;
  project_key: string;
  api_token: string;
  previous_token_revoked?: boolean;
  reissued_at: string;
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
  mark_ready?: boolean;
  waive_ack?: boolean;
  waive_reason?: string | null;
}
/**
 * Optional body for ``POST /topics/{id}/close`` — records *why* the topic
 * is being closed so the team can distinguish "discussed, no experiment
 * needed" from a plain closure.
 */
export interface TopicCloseRequest {
  close_reason?: string | null;
  close_note?: string | null;
}
export interface TopicCommentCreate {
  body?: string | null;
  parent_id?: string | null;
  is_round_summary?: boolean;
  file_path?: string | null;
  excerpt?: string | null;
}
export interface TopicCommentRead {
  id: string;
  topic_id: string;
  author_agent_id: string;
  author_name?: string | null;
  parent_comment_id: string | null;
  body: string;
  kind?: TopicCommentKind;
  is_round_summary?: boolean;
  comment_seq: number;
  created_at: string;
  unresolved_mentions?: string[];
  file_path?: string | null;
  excerpt?: string | null;
}
export interface TopicCommentTreeNode {
  id: string;
  topic_id: string;
  author_agent_id: string;
  author_name?: string | null;
  parent_comment_id: string | null;
  body: string;
  kind?: TopicCommentKind1;
  is_round_summary?: boolean;
  comment_seq: number;
  created_at: string;
  unresolved_mentions?: string[];
  file_path?: string | null;
  excerpt?: string | null;
  children?: TopicCommentTreeNode[];
}
export interface TopicCreate {
  title: string;
  description?: string | null;
  slug?: string | null;
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
  slug?: string | null;
  status: TopicStatus;
  pinned?: boolean;
  discussion_round?: string;
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
  close_reason?: string | null;
  close_note?: string | null;
  content_source?: string;
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
