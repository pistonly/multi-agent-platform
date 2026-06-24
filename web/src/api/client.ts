import axios from "axios";
import type {
  Agent,
  CommentTreeNode,
  ExperimentDetail,
  ExperimentLog,
  ExperimentSummary,
  GlobalStatus,
  PlanVersion,
  Project,
  ProjectCreatePayload,
  ProjectStatus,
  ProjectStatusVersion,
  Review,
} from "./types";

const baseURL = import.meta.env.VITE_API_URL || "";

export const api = axios.create({
  baseURL: `${baseURL}/api/v1`,
  headers: { "Content-Type": "application/json" },
});

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

export async function fetchProjectExperiments(projectId: string): Promise<ExperimentSummary[]> {
  const { data } = await api.get<ExperimentSummary[]>(`/projects/${projectId}/experiments`);
  return data;
}

export async function fetchExperiment(id: string): Promise<ExperimentDetail> {
  const { data } = await api.get<ExperimentDetail>(`/experiments/${id}`);
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
