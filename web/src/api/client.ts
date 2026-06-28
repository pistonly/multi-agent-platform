import axios, { type AxiosError } from "axios";
import type {
  Agent,
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
  TopicCreatePayload,
  TodoRead,
  TopicRead,
  TopicStatus,
  TopicSummary,
  NotificationList,
  NotificationStreamEvent,
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

export async function createTopicComment(
  topicId: string,
  payload: { body: string; parent_id?: string }
): Promise<void> {
  await api.post(`/topics/${topicId}/comments`, payload);
}

// --- todos ---

export async function fetchTodos(): Promise<TodoRead> {
  const { data } = await api.get<TodoRead>("/agents/me/todos");
  return data;
}

export async function fetchNotifications(params?: {
  unread_only?: boolean;
  limit?: number;
  offset?: number;
}): Promise<NotificationList> {
  const { data } = await api.get<NotificationList>("/agents/me/notifications", { params });
  return data;
}

const SSE_RETRY_MS = 3000;

export async function streamNotifications(
  token: string,
  opts: {
    signal: AbortSignal;
    onEvent: (event: NotificationStreamEvent) => void;
    onError?: (error: unknown) => void;
  }
): Promise<void> {
  const baseURL = api.defaults.baseURL ?? "";
  const url = `${baseURL}/agents/me/notifications/stream`;

  while (!opts.signal.aborted) {
    try {
      const response = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
        signal: opts.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`SSE failed: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (!opts.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";
        for (const part of parts) {
          for (const line of part.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            opts.onEvent(JSON.parse(line.slice(6)) as NotificationStreamEvent);
          }
        }
      }
    } catch (err) {
      if (opts.signal.aborted) return;
      opts.onError?.(err);
      await new Promise((resolve) => setTimeout(resolve, SSE_RETRY_MS));
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
