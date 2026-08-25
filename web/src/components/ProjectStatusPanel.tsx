import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchProjectDecisions, fetchProjectStatus, fetchProjectStatusVersions } from "../api/client";
import type { ExperimentSummary, TopicDecision, TopicSummary } from "../api/types";
import { ExperimentsListPanel } from "./ExperimentsListPanel";
import { PhaseBadge } from "./PhaseStepper";
import { TopicsListPanel } from "./TopicsListPanel";
import { StatusEditor } from "./StatusEditor";
import { StatusMarkdown } from "./StatusMarkdown";
import { Modal } from "./Modal";
import { CreateExperimentForm } from "./CreateExperimentForm";
import { CreateTopicForm } from "./CreateTopicForm";

interface ProjectStatusPanelProps {
  projectId: string;
  isAdmin: boolean;
  showHeader?: boolean;
}

export function ProjectStatusPanel({ projectId, isAdmin, showHeader = true }: ProjectStatusPanelProps) {
  const [showCreateTopic, setShowCreateTopic] = useState(false);
  const [showCreateExperiment, setShowCreateExperiment] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["project-status", projectId],
    queryFn: () => fetchProjectStatus(projectId),
    refetchInterval: 30_000,
  });
  const { data: decisions } = useQuery({
    queryKey: ["project-decisions", projectId],
    queryFn: () => fetchProjectDecisions(projectId, 5),
    enabled: !!data,
    refetchInterval: 120_000,
  });

  if (isLoading) return <p className="text-slate-400">加载项目状态…</p>;
  if (error || !data) {
    return <p className="text-red-400">项目不存在或无权访问</p>;
  }

  const { project, experiment_counts_by_phase, active_experiments, open_topics, status_md } = data;

  return (
    <div className="space-y-8">
      {showHeader && (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-white">{project.name}</h1>
            <p className="mt-1 font-mono text-sm text-slate-400">
              {project.project_key} · {project.workspace_path}
            </p>
            {project.description && <p className="mt-2 text-slate-300">{project.description}</p>}
          </div>
          <div className="flex gap-2">
            <button type="button" className="btn-secondary" onClick={() => setShowCreateTopic(true)}>
              发布话题
            </button>
            <button type="button" className="btn-primary" onClick={() => setShowCreateExperiment(true)}>
              发布实验
            </button>
          </div>
        </div>
      )}

      <section>
        <h2 className="mb-3 text-lg font-semibold text-white">实验快照</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
          {Object.entries(experiment_counts_by_phase).map(([phase, count]) => (
            <div key={phase} className="card text-center">
              <div className="mb-1 text-2xl font-semibold text-white">{count}</div>
              <PhaseBadge phase={phase as never} />
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">进行中话题</h2>
          <span className="text-xs text-slate-500">{(open_topics ?? []).length} 个</span>
        </div>
        <OpenTopicList topics={open_topics ?? []} emptyLabel="暂无进行中的话题" />
      </section>

      <section className="card">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">最近结论</h2>
          <span className="text-xs text-slate-500">{decisions?.length ?? 0} 条</span>
        </div>
        <DecisionList decisions={decisions ?? []} />
      </section>

      <section className="card">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">Current Status</h2>
          <span className="text-xs text-slate-500">
            v{data.status_version}
            {data.status_updated_at && ` · 更新于 ${new Date(data.status_updated_at).toLocaleString()}`}
          </span>
        </div>
        {status_md ? <StatusMarkdown content={status_md} /> : <p className="text-slate-500">暂无 Status 文档</p>}
      </section>

      {isAdmin && (
        <section className="card">
          <StatusEditor
            projectId={projectId}
            currentContent={status_md ?? ""}
            currentVersion={data.status_version ?? 0}
          />
        </section>
      )}

      {!isAdmin && <StatusVersionHistory projectId={projectId} />}

      <TopicsListPanel
        projectId={projectId}
        viewAllHref={`/projects/${projectId}/topics`}
      />

      {(active_experiments ?? []).length > 0 && (
        <section className="card">
          <h2 className="mb-3 text-lg font-semibold text-white">活跃实验</h2>
          <ActiveExperimentTable experiments={active_experiments ?? []} />
        </section>
      )}

      <ExperimentsListPanel
        projectId={projectId}
        viewAllHref={`/projects/${projectId}/experiments`}
      />

      {showCreateTopic && (
        <Modal title="用 CLI 发布话题" className="max-w-xl" onClose={() => setShowCreateTopic(false)}>
          <CreateTopicForm onCancel={() => setShowCreateTopic(false)} />
        </Modal>
      )}
      {showCreateExperiment && (
        <Modal title="发布实验" onClose={() => setShowCreateExperiment(false)}>
          <CreateExperimentForm
            projectId={projectId}
            onCancel={() => setShowCreateExperiment(false)}
            onCreated={() => setShowCreateExperiment(false)}
          />
        </Modal>
      )}
    </div>
  );
}

function DecisionList({ decisions }: { decisions: TopicDecision[] }) {
  if (decisions.length === 0) return <p className="py-2 text-sm text-slate-500">暂无结构化结论</p>;
  return (
    <ul className="space-y-2">
      {decisions.map((decision) => (
        <li key={decision.id} className="border-b border-surface-border py-2 last:border-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Link to={`/topics/${decision.topic_id}`} className="text-accent hover:underline">
              {decision.topic_title ?? `${decision.topic_id.slice(0, 8)}…`}
            </Link>
            <span className="text-xs text-slate-500">{new Date(decision.updated_at).toLocaleString()}</span>
          </div>
          <p className="mt-1 line-clamp-2 text-sm text-slate-300">
            {decision.decision ?? decision.no_decision_reason ?? "暂无结论正文"}
          </p>
          {(decision.action_items ?? []).length > 0 && (
            <p className="mt-1 text-xs text-slate-500">{(decision.action_items ?? []).length} 个行动项</p>
          )}
        </li>
      ))}
    </ul>
  );
}

function OpenTopicList({ topics, emptyLabel }: { topics: TopicSummary[]; emptyLabel?: string }) {
  if (topics.length === 0) return <p className="py-2 text-sm text-slate-500">{emptyLabel ?? "暂无话题"}</p>;
  return (
    <ul className="space-y-1">
      {topics.map((t) => (
        <li
          key={t.id}
          className="flex flex-wrap items-center justify-between gap-2 border-b border-surface-border py-2 last:border-0"
        >
          <Link to={`/topics/${t.id}`} className="text-accent hover:underline">
            {t.pinned ? "📌 " : ""}
            {t.title}
          </Link>
          <div className="flex items-center gap-3 text-xs text-slate-500">
            <span>{t.creator_name ?? `${t.creator_agent_id.slice(0, 8)}…`}</span>
            <span>{t.comment_count} 评论</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function ActiveExperimentTable({ experiments }: { experiments: ExperimentSummary[] }) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-slate-500">
        <tr>
          <th className="pb-2 pr-4">标题</th>
          <th className="pb-2 pr-4">阶段</th>
          <th className="pb-2">更新</th>
        </tr>
      </thead>
      <tbody>
        {experiments.map((exp) => (
          <tr key={exp.id} className="border-t border-surface-border">
            <td className="py-2 pr-4">
              <Link to={`/experiments/${exp.id}`} className="text-accent hover:underline">
                {exp.title}
              </Link>
            </td>
            <td className="py-2 pr-4">
              <PhaseBadge phase={exp.phase} />
            </td>
            <td className="py-2 text-slate-400">{new Date(exp.updated_at).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StatusVersionHistory({ projectId }: { projectId: string }) {
  const { data: versions, isLoading } = useQuery({
    queryKey: ["project-status-versions", projectId],
    queryFn: () => fetchProjectStatusVersions(projectId),
  });

  if (isLoading) return null;
  if (!versions?.length) return null;

  return (
    <section className="card">
      <h2 className="mb-3 text-lg font-semibold text-white">版本历史</h2>
      <ul className="space-y-1 text-sm">
        {versions.map((v) => (
          <li key={v.id} className="flex justify-between text-slate-400">
            <span>
              v{v.version}
              {v.change_note && <span className="ml-2 text-slate-500">— {v.change_note}</span>}
            </span>
            <span className="text-xs">{new Date(v.created_at).toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
