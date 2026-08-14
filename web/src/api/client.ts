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
  NotificationList,
  NotificationStreamEvent,
  PlatformFeedback,
  FeedbackCreatePayload,
  FeedbackUpdatePayload,
  FeedbackCategory,
  FeedbackStatus,
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

export function formatApiError(error: AxiosError<{ detail?: unknown }>): string {
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === "object" && item && "msg" in item ? String(item.msg) : String(item)))
      .join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return error.message || "请求失败";
}

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: unknown }>) => {
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

export async function fetchNotifications(params?: {
  unread_only?: boolean;
  category?: "wakeable" | "digest" | "all";
  target_type?: string;
  limit?: number;
  offset?: number;
}): Promise<NotificationList> {
  const { data } = await api.get<NotificationList>("/agents/me/notifications", { params });
  return data;
}

const SSE_INITIAL_RETRY_MS = 1000;
const SSE_MAX_RETRY_MS = 30_000;
// 致命状态码：token 失效，重连只会再次失败 → 停止重连（其他 axios 请求的
// 401/403 会被 AuthContext 拦截器处理登出；SSE 这边停止即可，避免死循环）。
const SSE_FATAL_STATUS = new Set([401, 403]);

export interface NotificationStreamError {
  /** HTTP 状态码（连接建立失败时）。 */
  status?: number;
  /** true = 不应再重连（401/403）。 */
  fatal: boolean;
}

export async function streamNotifications(
  token: string,
  opts: {
    signal: AbortSignal;
    onEvent: (event: NotificationStreamEvent) => void;
    onError?: (error: NotificationStreamError) => void;
  }
): Promise<void> {
  const baseURL = api.defaults.baseURL ?? "";
  const url = `${baseURL}/agents/me/notifications/stream`;
  // 最后收到的 event id。重连时通过 Last-Event-ID 头带回，服务端从 ring
  // buffer 回放断线期间丢失的事件（P2 #9 服务端已支持，这里是客户端半边）。
  let lastEventId = 0;
  let attempt = 0;

  while (!opts.signal.aborted) {
    try {
      const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
      if (lastEventId > 0) headers["Last-Event-ID"] = String(lastEventId);
      const response = await fetch(url, { headers, signal: opts.signal });

      if (!response.ok || !response.body) {
        if (SSE_FATAL_STATUS.has(response.status)) {
          opts.onError?.({ status: response.status, fatal: true });
          return;
        }
        throw new Error(`SSE failed: ${response.status}`);
      }

      // 连接建立成功，重置退避计数。
      attempt = 0;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (!opts.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const eventBlock of events) {
          // 一个 event block 含 id:/data:/event:/retry:/心跳(:) 多行。
          // 后端帧顺序是 id 在 data 前；仅在 data 成功解析后再推进 lastEventId，
          // 这样坏数据不会卡住重连（重连会从上一个好 id 重放）。
          let dataLine: string | null = null;
          let pendingId: number | null = null;
          for (const line of eventBlock.split("\n")) {
            if (line.startsWith("id: ")) {
              pendingId = Number(line.slice(4));
            } else if (line.startsWith("data: ")) {
              dataLine = line.slice(6);
            }
            // event: / retry: / 心跳注释行（`: heartbeat`）忽略。
          }
          if (dataLine === null) continue;
          try {
            opts.onEvent(JSON.parse(dataLine) as NotificationStreamEvent);
          } catch {
            continue; // 跳过无法解析的事件，不更新 lastEventId（重连会重放它）。
          }
          if (pendingId !== null && Number.isFinite(pendingId)) {
            lastEventId = Math.max(lastEventId, pendingId);
          }
        }
      }
    } catch {
      if (opts.signal.aborted) return;
      opts.onError?.({ fatal: false });
      // 指数退避 + jitter：1s→2s→4s…→30s 封顶，加随机抖动避免所有客户端
      // 同步重连（thundering herd）。
      const base = Math.min(SSE_INITIAL_RETRY_MS * 2 ** attempt, SSE_MAX_RETRY_MS);
      const delay = base / 2 + Math.random() * (base / 2);
      attempt += 1;
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
}

export async function markNotificationRead(notificationId: string): Promise<void> {
  await api.post(`/notifications/${notificationId}/read`);
}

export async function markAllNotificationsRead(): Promise<{ marked: number }> {
  const { data } = await api.post<{ marked: number }>("/agents/me/notifications/read-all");
  return data;
}

export interface FetchFeedbacksOptions {
  status?: FeedbackStatus;
  category?: FeedbackCategory;
  projectId?: string;
  page?: number;
  pageSize?: number;
  includeArchived?: boolean;
}

export async function fetchFeedbacks(
  opts: FetchFeedbacksOptions = {}
): Promise<PaginatedResult<PlatformFeedback>> {
  const { status, category, projectId, page = 1, pageSize = 50, includeArchived = false } = opts;
  const response = await api.get<PlatformFeedback[]>("/feedback", {
    params: {
      ...(status ? { status } : {}),
      ...(category ? { category } : {}),
      ...(projectId ? { project_id: projectId } : {}),
      page,
      page_size: pageSize,
      include_archived: includeArchived,
    },
  });
  return {
    items: response.data,
    total: parseTotalCount(response.headers as Record<string, unknown>),
  };
}

export async function submitFeedback(payload: FeedbackCreatePayload): Promise<PlatformFeedback> {
  const { data } = await api.post<PlatformFeedback>("/feedback", payload);
  return data;
}

export async function updateFeedback(
  feedbackId: string,
  payload: FeedbackUpdatePayload
): Promise<PlatformFeedback> {
  const { data } = await api.patch<PlatformFeedback>(`/feedback/${feedbackId}`, payload);
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
