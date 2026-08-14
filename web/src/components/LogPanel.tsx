import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { createLog } from "../api/client";
import type { ExperimentLog } from "../api/types";
import { MarkdownBody } from "./MarkdownBody";
import { FileBreadcrumb } from "./FileBreadcrumb";
import { useDoc } from "../hooks/useDoc";
import { useAuth } from "../context/AuthContext";

interface LogPanelProps {
  experimentId: string;
  logs: ExperimentLog[];
  canAppend: boolean;
  onUpdated: () => void;
  logFilePath?: string | null;
}

export function LogPanel({ experimentId, logs, canAppend, onUpdated, logFilePath = null }: LogPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [summary, setSummary] = useState("");
  const [body, setBody] = useState("");
  const { agent } = useAuth();
  const docQuery = useDoc(agent?.project_id ?? null, logFilePath);

  const appendMutation = useMutation({
    mutationFn: () =>
      createLog(experimentId, {
        summary: summary.trim(),
        content_md: body.trim(),
      }),
    onSuccess: () => {
      setSummary("");
      setBody("");
      setExpanded(false);
      onUpdated();
    },
  });

  return (
    <div className="space-y-4">
      {logFilePath && (
        <article className="rounded-lg border border-surface-border bg-surface/40 p-4">
          <FileBreadcrumb path={logFilePath} className="mb-2" />
          <div className="text-sm">
            <MarkdownBody content={docQuery.data?.content ?? ""} />
          </div>
        </article>
      )}
      {logs.map((log) => (
        <article key={log.id} className="rounded-lg border border-surface-border bg-surface/40 p-4">
          <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-medium text-white">{log.summary}</h3>
            <time className="text-xs text-slate-500">{new Date(log.created_at).toLocaleString()}</time>
          </header>
          <div className="markdown-body text-sm">
            <MarkdownBody content={log.content_md} />
          </div>
          {log.metadata_json && Object.keys(log.metadata_json).length > 0 && (
            <pre className="mt-2 overflow-x-auto rounded bg-surface p-2 text-xs text-slate-400">
              {JSON.stringify(log.metadata_json, null, 2)}
            </pre>
          )}
        </article>
      ))}
      {logs.length === 0 && !logFilePath && <p className="text-sm text-slate-500">暂无实验日志</p>}

      {canAppend &&
        (expanded ? (
          <div className="space-y-2 rounded-lg border border-surface-border bg-surface/40 p-3">
            <input
              className="w-full rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
              placeholder="日志摘要"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
            />
            <textarea
              className="min-h-[80px] w-full rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
              placeholder="日志正文（Markdown）"
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />
            {appendMutation.isError && <p className="text-sm text-red-400">追加失败</p>}
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={() => setExpanded(false)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!summary.trim() || !body.trim() || appendMutation.isPending}
                onClick={() => appendMutation.mutate()}
              >
                {appendMutation.isPending ? "提交中…" : "追加日志"}
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className="btn-secondary" onClick={() => setExpanded(true)}>
            追加日志
          </button>
        ))}
    </div>
  );
}
