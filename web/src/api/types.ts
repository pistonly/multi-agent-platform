export type ExperimentPhase =
  | "draft"
  | "review"
  | "approved"
  | "running"
  | "done"
  | "cancelled";

export type AgentRole = "agent" | "admin";

export type ReviewItemKind = "reasonable" | "unreasonable";

export type ReviewItemStatus =
  | "open"
  | "addressed"
  | "rebutted"
  | "resolved"
  | "withdrawn"
  | "escalated";

export type CommentAnchorType = "plan" | "review" | "review_item" | "comment";

export interface Project {
  id: string;
  project_key: string;
  name: string;
  workspace_path: string;
  description: string | null;
  current_status_version: number;
  created_at: string;
  archived_at: string | null;
}

export interface ExperimentSummary {
  id: string;
  project_id: string;
  creator_agent_id: string;
  title: string;
  description: string | null;
  phase: ExperimentPhase;
  current_plan_version: number;
  topic_id: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface PlanVersion {
  id: string;
  experiment_id: string;
  version: number;
  content_md: string;
  author_agent_id: string;
  change_note: string | null;
  created_at: string;
}

export interface ExperimentDetail extends ExperimentSummary {
  current_plan: PlanVersion | null;
  plan_version_count: number;
  open_unreasonable_count: number;
  review_count: number;
  log_count: number;
  latest_log_summary: string | null;
}

export interface ExperimentBundle {
  experiment: ExperimentDetail;
  plans: PlanVersion[];
  reviews: Review[];
  comments: CommentTreeNode[];
  logs: ExperimentLog[];
}

export interface ReviewItem {
  id: string;
  review_id: string;
  kind: ReviewItemKind;
  content: string;
  status: ReviewItemStatus | null;
  created_at: string;
  updated_at: string;
}

export interface Review {
  id: string;
  experiment_id: string;
  reviewer_agent_id: string;
  plan_version: number;
  created_at: string;
  items: ReviewItem[];
}

export interface Comment {
  id: string;
  experiment_id: string;
  anchor_type: CommentAnchorType;
  anchor_id: string;
  parent_comment_id: string | null;
  author_agent_id: string;
  author_name: string | null;
  body: string;
  created_at: string;
}

export interface CommentTreeNode extends Comment {
  children: CommentTreeNode[];
}

export interface ExperimentLog {
  id: string;
  experiment_id: string;
  author_agent_id: string;
  summary: string;
  content_md: string;
  metadata_json: Record<string, unknown> | null;
  created_at: string;
}

export interface ProjectStatus {
  project: Project;
  experiment_counts_by_phase: Record<string, number>;
  active_experiments: ExperimentSummary[];
  recent_experiments: ExperimentSummary[];
  open_topics: TopicSummary[];
  status_version: number;
  status_md: string | null;
  status_updated_at: string | null;
}

export interface ProjectStatusVersion {
  id: string;
  project_id: string;
  version: number;
  content_md: string;
  author_agent_id: string;
  change_note: string | null;
  created_at: string;
}

export interface GlobalStatus {
  total_experiments_by_phase: Record<string, number>;
  projects: ProjectStatus[];
  recent_experiments: ExperimentSummary[];
}

export interface Agent {
  id: string;
  name: string;
  role: AgentRole;
  project_id: string | null;
  project_key: string | null;
  created_at: string;
}

export interface ProjectCreatePayload {
  project_key: string;
  name: string;
  workspace_path: string;
  description?: string | null;
}

export interface ExperimentCreatePayload {
  title: string;
  description?: string | null;
  plan: { content_md: string; change_note?: string | null };
  submit_for_review?: boolean;
  topic_id?: string | null;
}

export interface TopicCreatePayload {
  title: string;
  description?: string | null;
}

export type TopicStatus = "open" | "closed";
export type TopicDiscussionRound = "round1" | "round2" | "ready";
export type TopicActionItemStatus = "open" | "done" | "cancelled";

export interface TopicSummary {
  id: string;
  project_id: string;
  creator_agent_id: string;
  creator_name: string | null;
  title: string;
  description: string | null;
  status: TopicStatus;
  pinned: boolean;
  discussion_round: TopicDiscussionRound;
  round_summary_count: number;
  comment_count: number;
  experiment_count: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface TopicComment {
  id: string;
  topic_id: string;
  author_agent_id: string;
  author_name: string | null;
  parent_comment_id: string | null;
  body: string;
  created_at: string;
}

export interface TopicCommentTreeNode extends TopicComment {
  children: TopicCommentTreeNode[];
}

export interface TopicActionItem {
  id: string;
  decision_id: string;
  project_id: string;
  topic_id: string;
  title: string;
  description: string | null;
  owner_agent_id: string | null;
  owner_name: string | null;
  status: TopicActionItemStatus;
  due_at: string | null;
  linked_experiment_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface TopicDecision {
  id: string;
  project_id: string;
  topic_id: string;
  topic_title: string | null;
  author_agent_id: string;
  author_name: string | null;
  decision: string | null;
  rationale: string | null;
  rejected_options: string | null;
  open_questions: string | null;
  no_decision_reason: string | null;
  action_items: TopicActionItem[];
  created_at: string;
  updated_at: string;
}

export interface TopicResolvePayload {
  decision?: string | null;
  rationale?: string | null;
  rejected_options?: string | null;
  open_questions?: string | null;
  no_decision_reason?: string | null;
  action_items?: {
    title: string;
    description?: string | null;
    owner_agent_id?: string | null;
    due_at?: string | null;
    linked_experiment_id?: string | null;
  }[];
}

export interface TopicRead extends TopicSummary {
  experiments: ExperimentSummary[];
  comments: TopicCommentTreeNode[];
  decision: TopicDecision | null;
}

export interface PendingReply {
  item_id: string;
  experiment_id: string;
  experiment_title: string;
  content: string;
  status: ReviewItemStatus;
  updated_at: string;
}

export interface MentionTodo {
  id: string;
  mentioned_agent_id: string;
  author_agent_id: string;
  author_name: string | null;
  source_type: string;
  source_id: string;
  project_id: string;
  experiment_id: string | null;
  topic_id: string | null;
  excerpt: string;
  created_at: string;
  dismissed_at: string | null;
}

export interface PendingTopicReplyTodo {
  topic_id: string;
  topic_title: string;
  comment_id: string;
  parent_comment_id: string | null;
  thread_root_id: string;
  author_agent_id: string;
  author_name: string | null;
  excerpt: string;
  created_at: string;
}

export interface TopicActionItemTodo {
  id: string;
  decision_id: string;
  project_id: string;
  topic_id: string;
  topic_title: string;
  title: string;
  description: string | null;
  status: TopicActionItemStatus;
  due_at: string | null;
  linked_experiment_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface TodoRead {
  my_open_experiments: ExperimentSummary[];
  pending_reviews: ExperimentSummary[];
  pending_replies: PendingReply[];
  pending_topic_replies: PendingTopicReplyTodo[];
  my_open_topics: TopicSummary[];
  mentions: MentionTodo[];
  action_items: TopicActionItemTodo[];
}

export interface Notification {
  id: string;
  recipient_agent_id: string;
  project_id: string | null;
  event: string;
  summary: string;
  target_type: string;
  target_id: string | null;
  payload_json: Record<string, unknown> | null;
  read_at: string | null;
  created_at: string;
}

export interface NotificationList {
  items: Notification[];
  total: number;
  unread_count: number;
}

export interface NotificationStreamEvent {
  type: "notification.created";
  event: string;
  notification_id: string;
}

export type FeedbackCategory = "bug" | "suggestion" | "question" | "other";
export type FeedbackStatus = "new" | "triaged" | "in_progress" | "resolved";

export interface PlatformFeedback {
  id: string;
  author_agent_id: string;
  author_name: string | null;
  project_id: string | null;
  body: string;
  category: FeedbackCategory | null;
  status: FeedbackStatus;
  metadata_json: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface FeedbackCreatePayload {
  body: string;
  project_id?: string | null;
  category?: FeedbackCategory | null;
}

export interface FeedbackUpdatePayload {
  status?: FeedbackStatus;
  category?: FeedbackCategory | null;
  archived?: boolean;
}
