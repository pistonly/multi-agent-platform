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
  created_at: string;
  updated_at: string;
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
