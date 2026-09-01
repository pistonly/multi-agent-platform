import axios, { type AxiosError } from "axios";
import type {
  Agent,
  AgentRole,
  AgentWorkRead,
  AgentWorkSummary,
  CommentTreeNode,
  ExperimentBundle,
  ExperimentCreatePayload,
  ExperimentDetail,
  ExperimentLog,
  ExperimentPhase,
  ExperimentSummary,
  GlobalStatus,
  PlanVersion,
  Project,
  ProjectCreatePayload,
  ProjectStatus,
  ProjectStatusVersion,
  Review,
  TopicActionItem,
  TopicActionItemStatus,
  TopicCreatePayload,
  TopicDecision,
  TodoRead,
  TopicRead,
  TopicResolvePayload,
  TopicStatus,
  TopicSummary,
} from "./types";

export interface PaginatedResult<T> {
  items: T[];
  total: number;
}

export function parseTotalCount(headers: Record<string, unknown>): number {
  const raw = headers["x-total-count"] ?? headers["X-Total-Count"];
  const n = Number(raw);
  return Number.isFinite(n) ? n : 0;
}

function parseTotalCountInternal(headers: Record<string, unknown>): number {
  return parseTotalCount(headers);
}

const baseURL = import.meta.env.VITE_API_URL || "";

export const api = axios.create({
  baseURL: `${baseURL}/api/v1`,
  headers: { "Content-Type": "application/json" },
});

type ApiErrorHandler = (message: string) => void;
let apiErrorHandler: ApiErrorHandler | null = null;

export function setApiErrorHandler(handler: ApiErrorHandler | null) {
  apiErrorHandler = handler;
}

export function formatApiError(
  error: AxiosError<{ detail?: unknown; hint?: string }>,
): string {
  const data = error.response?.data;
  const detail = data?.detail;
  let message: string;
  if (typeof detail === "string") {
    message = detail;
  } else if (Array.isArray(detail)) {
    message = detail
      .map((item) => (typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item)))
      .join("; ");
  } else if (detail && typeof detail === "object") {
    message = JSON.stringify(detail);
  } else {
    message = error.message || "请求失败";
  }
  const hint = typeof data?.hint === "string" ? data.hint.trim() : "";
  if (hint && !message.includes(hint)) {
    return `${message}\n${hint}`;
  }
  return message;
}

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: unknown; hint?: string }>) => {
    if (axios.isAxiosError(error) && error.response?.status !== 401) {
      apiErrorHandler?.(formatApiError(error));
    }
    return Promise.reject(error);
  }
);

export function setAuthToken(token: string | null) {
  if (token) {
    api.defaults.headers.common.Authorization = `Bearer ${token}`;
  } else {
    delete api.defaults.headers.common.Authorization;
  }
}

export async function getMe(): Promise<Agent> {
  const { data } = await api.get<Agent>("/agents/me");
  return data;
}

export interface FetchAgentsOptions {
  role?: AgentRole;
  projectId?: string;
}

export async function fetchAgents(opts: FetchAgentsOptions = {}): Promise<Agent[]> {
  const { role, projectId } = opts;
  const { data } = await api.get<Agent[]>("/agents", {
    params: {
      ...(role ? { role } : {}),
      ...(projectId ? { project_id: projectId } : {}),
    },
  });
  return data;
}

export async function fetchGlobalStatus(projectId?: string): Promise<GlobalStatus> {
  const { data } = await api.get<GlobalStatus>("/status", {
    params: projectId ? { project_id: projectId } : undefined,
  });
  return data;
}

export async function fetchProjects(): Promise<Project[]> {
  const { data } = await api.get<Project[]>("/projects");
  return data;
}

export async function fetchProject(id: string): Promise<Project> {
  const { data } = await api.get<Project>(`/projects/${id}`);
  return data;
}

export async function createProject(payload: ProjectCreatePayload): Promise<Project> {
  const { data } = await api.post<Project>("/projects", payload);
  return data;
}

export async function fetchProjectStatus(projectId: string): Promise<ProjectStatus> {
  const { data } = await api.get<ProjectStatus>(`/projects/${projectId}/status`);
  return data;
}

export async function fetchProjectDecisions(projectId: string, limit = 20): Promise<TopicDecision[]> {
  const { data } = await api.get<TopicDecision[]>(`/projects/${projectId}/decisions`, {
    params: { limit },
  });
  return data;
}

export async function fetchProjectActionItems(params: {
  projectId: string;
  ownerAgentId?: string;
  status?: TopicActionItemStatus;
  limit?: number;
}): Promise<TopicActionItem[]> {
  const { projectId, ownerAgentId, status = "open", limit = 100 } = params;
  const { data } = await api.get<TopicActionItem[]>(`/projects/${projectId}/action-items`, {
    params: {
      ...(ownerAgentId ? { owner_agent_id: ownerAgentId } : {}),
      ...(status ? { status } : {}),
      limit,
    },
  });
  return data;
}

export async function fetchProjectStatusVersions(projectId: string): Promise<ProjectStatusVersion[]> {
  const { data } = await api.get<ProjectStatusVersion[]>(`/projects/${projectId}/status/versions`);
  return data;
}

export async function reviseProjectStatus(
  projectId: string,
  body: { content_md: string; change_note?: string | null }
): Promise<ProjectStatusVersion> {
  const { data } = await api.post<ProjectStatusVersion>(`/projects/${projectId}/status/revisions`, body);
  return data;
}

export interface FetchProjectExperimentsOptions {
  phase?: ExperimentPhase;
  q?: string;
  page?: number;
  pageSize?: number;
  includeArchived?: boolean;
}

export async function fetchProjectExperiments(
  projectId: string,
  opts: FetchProjectExperimentsOptions = {}
): Promise<PaginatedResult<ExperimentSummary>> {
  const { phase, q, page = 1, pageSize = 20, includeArchived = false } = opts;
  const response = await api.get<ExperimentSummary[]>(`/projects/${projectId}/experiments`, {
    params: {
      ...(phase ? { phase } : {}),
      ...(q ? { q } : {}),
      page,
      page_size: pageSize,
      include_archived: includeArchived,
    },
  });
  return {
    items: response.data,
    total: parseTotalCountInternal(response.headers as Record<string, unknown>),
  };
}

export async function fetchExperiment(id: string): Promise<ExperimentDetail> {
  const { data } = await api.get<ExperimentDetail>(`/experiments/${id}`);
  return data;
}

export async function fetchExperimentBundle(id: string): Promise<ExperimentBundle> {
  const { data } = await api.get<ExperimentBundle>(`/experiments/${id}/bundle`);
  return data;
}

export async function fetchPlans(experimentId: string): Promise<PlanVersion[]> {
  const { data } = await api.get<PlanVersion[]>(`/experiments/${experimentId}/plans`);
  return data;
}

export async function fetchReviews(experimentId: string): Promise<Review[]> {
  const { data } = await api.get<Review[]>(`/experiments/${experimentId}/reviews`);
  return data;
}

export async function fetchCommentTree(experimentId: string): Promise<CommentTreeNode[]> {
  const { data } = await api.get<CommentTreeNode[]>(`/experiments/${experimentId}/comments`, {
    params: { tree: true },
  });
  return data;
}

export async function fetchLogs(experimentId: string): Promise<ExperimentLog[]> {
  const { data } = await api.get<ExperimentLog[]>(`/experiments/${experimentId}/logs`);
  return data;
}

export async function submitForReview(experimentId: string): Promise<ExperimentSummary> {
  const { data } = await api.post<ExperimentSummary>(`/experiments/${experimentId}/submit-review`);
  return data;
}

export async function approveExperiment(experimentId: string): Promise<ExperimentSummary> {
  const { data } = await api.post<ExperimentSummary>(`/experiments/${experimentId}/approve`);
  return data;
}

export async function startExperiment(experimentId: string): Promise<ExperimentSummary> {
  const { data } = await api.post<ExperimentSummary>(`/experiments/${experimentId}/start`);
  return data;
}

export async function completeExperiment(
  experimentId: string,
  body: { summary: string; content_md: string; metadata?: Record<string, unknown> }
): Promise<ExperimentSummary> {
  const { data } = await api.post<ExperimentSummary>(`/experiments/${experimentId}/complete`, body);
  return data;
}

export async function acceptExperimentResult(
  experimentId: string,
  body: { summary: string; content_md: string; metadata?: Record<string, unknown> }
): Promise<ExperimentSummary> {
  const { data } = await api.post<ExperimentSummary>(`/experiments/${experimentId}/accept-result`, body);
  return data;
}

export async function rejectExperimentResult(
  experimentId: string,
  body: { summary: string; content_md: string; metadata?: Record<string, unknown> }
): Promise<ExperimentSummary> {
  const { data } = await api.post<ExperimentSummary>(`/experiments/${experimentId}/reject-result`, body);
  return data;
}

export async function updateReviewItem(itemId: string, status: string): Promise<void> {
  await api.patch(`/review-items/${itemId}`, { status });
}

export async function createComment(
  experimentId: string,
  body: { anchor_type: string; anchor_id: string; parent_id?: string; body: string }
): Promise<void> {
  await api.post(`/experiments/${experimentId}/comments`, body);
}

// --- experiments: write ---

export async function createExperiment(
  projectId: string,
  payload: ExperimentCreatePayload
): Promise<ExperimentSummary> {
  const { data } = await api.post<ExperimentSummary>(`/projects/${projectId}/experiments`, payload);
  return data;
}

export async function updateExperiment(
  experimentId: string,
  body: { title?: string; description?: string | null; archived?: boolean }
): Promise<ExperimentSummary> {
  const { data } = await api.patch<ExperimentSummary>(`/experiments/${experimentId}`, body);
  return data;
}

export async function deleteExperiment(experimentId: string): Promise<void> {
  await api.delete(`/experiments/${experimentId}`);
}

export async function withdrawExperiment(experimentId: string): Promise<ExperimentSummary> {
  const { data } = await api.post<ExperimentSummary>(`/experiments/${experimentId}/withdraw`);
  return data;
}

export async function cancelExperiment(experimentId: string): Promise<ExperimentSummary> {
  const { data } = await api.post<ExperimentSummary>(`/experiments/${experimentId}/cancel`);
  return data;
}

export async function revisePlan(
  experimentId: string,
  body: { content_md: string; change_note?: string | null; addressed_item_ids?: string[] }
): Promise<PlanVersion> {
  const { data } = await api.post<PlanVersion>(`/experiments/${experimentId}/plans`, body);
  return data;
}

export async function createReview(
  experimentId: string,
  body: { reasonable_items: string[]; unreasonable_items: string[] }
): Promise<Review> {
  const { data } = await api.post<Review>(`/experiments/${experimentId}/reviews`, body);
  return data;
}

export async function createLog(
  experimentId: string,
  body: { summary: string; content_md: string; metadata?: Record<string, unknown> }
): Promise<ExperimentLog> {
  const { data } = await api.post<ExperimentLog>(`/experiments/${experimentId}/logs`, body);
  return data;
}

// --- topics ---

export interface FetchTopicsOptions {
  status?: TopicStatus;
  q?: string;
  page?: number;
  pageSize?: number;
  includeArchived?: boolean;
}

export async function fetchTopics(
  projectId: string,
  opts: FetchTopicsOptions = {}
): Promise<PaginatedResult<TopicSummary>> {
  const { status, q, page = 1, pageSize = 20, includeArchived = false } = opts;
  const response = await api.get<TopicSummary[]>(`/projects/${projectId}/topics`, {
    params: {
      ...(status ? { status } : {}),
      ...(q ? { q } : {}),
      page,
      page_size: pageSize,
      include_archived: includeArchived,
    },
  });
  return {
    items: response.data,
    total: parseTotalCountInternal(response.headers as Record<string, unknown>),
  };
}

export async function fetchTopic(topicId: string): Promise<TopicRead> {
  const { data } = await api.get<TopicRead>(`/topics/${topicId}`);
  return data;
}

export async function markTopicRead(topicId: string): Promise<void> {
  await api.post(`/agents/me/topics/${topicId}/read`);
}

export async function resolveTopic(topicId: string, payload: TopicResolvePayload): Promise<TopicDecision> {
  const { data } = await api.post<TopicDecision>(`/topics/${topicId}/resolve`, payload);
  return data;
}

export async function createTopic(projectId: string, payload: TopicCreatePayload): Promise<TopicSummary> {
  const { data } = await api.post<TopicSummary>(`/projects/${projectId}/topics`, payload);
  return data;
}

export async function updateTopic(
  topicId: string,
  body: { title?: string; description?: string | null; pinned?: boolean; archived?: boolean }
): Promise<TopicSummary> {
  const { data } = await api.patch<TopicSummary>(`/topics/${topicId}`, body);
  return data;
}

export async function closeTopic(topicId: string): Promise<TopicSummary> {
  const { data } = await api.post<TopicSummary>(`/topics/${topicId}/close`);
  return data;
}

export async function reopenTopic(topicId: string): Promise<TopicSummary> {
  const { data } = await api.post<TopicSummary>(`/topics/${topicId}/reopen`);
  return data;
}

export async function dismissTopic(topicId: string): Promise<TopicSummary> {
  const { data } = await api.post<TopicSummary>(`/topics/${topicId}/dismiss`);
  return data;
}

export async function createTopicComment(
  topicId: string,
  payload: { body: string; parent_id?: string }
): Promise<void> {
  await api.post(`/topics/${topicId}/comments`, payload);
}

// --- todos / work ---

export async function fetchWork(): Promise<AgentWorkRead> {
  const { data } = await api.get<AgentWorkRead>("/agents/me/work", {
    params: { notification_category: "all" },
  });
  return data;
}

export interface FetchWorkSummaryParams {
  includeAllPersonas?: boolean;
  topicsLimit?: number;
  experimentsLimit?: number;
}

export async function fetchWorkSummary(
  params: FetchWorkSummaryParams = {},
): Promise<AgentWorkSummary> {
  const { data } = await api.get<AgentWorkSummary>("/agents/me/work/summary", {
    params: {
      include_all_personas: params.includeAllPersonas ?? false,
      topics_limit: params.topicsLimit ?? 10,
      experiments_limit: params.experimentsLimit ?? 5,
    },
  });
  return data;
}

export async function fetchTodos(): Promise<TodoRead> {
  const work = await fetchWork();
  return work.todos;
}

export async function dismissMention(mentionId: string): Promise<void> {
  await api.post(`/agents/me/mentions/${mentionId}/dismiss`);
}

export async function dismissAllMentions(): Promise<{ dismissed: number }> {
  const { data } = await api.post<{ dismissed: number }>(
    "/agents/me/mentions/dismiss-all",
  );
  return data;
}

export interface DocReadResponse {
  path: string;
  content: string;
  size: number;
  exists: boolean;
}

export async function fetchDoc(projectId: string, path: string): Promise<DocReadResponse> {
  const { data } = await api.get<DocReadResponse>(
    `/projects/${projectId}/docs/read`,
    { params: { path } }
  );
  return data;
}
