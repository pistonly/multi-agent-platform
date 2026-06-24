import ReactMarkdown from "react-markdown";
import type { ExperimentLog } from "../api/types";

export function LogPanel({ logs }: { logs: ExperimentLog[] }) {
  if (logs.length === 0) {
    return <p className="text-sm text-slate-500">暂无实验日志</p>;
  }

  return (
    <div className="space-y-4">
      {logs.map((log) => (
        <article key={log.id} className="rounded-lg border border-surface-border bg-surface/40 p-4">
          <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-medium text-white">{log.summary}</h3>
            <time className="text-xs text-slate-500">{new Date(log.created_at).toLocaleString()}</time>
          </header>
          <div className="markdown-body text-sm">
            <ReactMarkdown>{log.content_md}</ReactMarkdown>
          </div>
          {log.metadata_json && Object.keys(log.metadata_json).length > 0 && (
            <pre className="mt-2 overflow-x-auto rounded bg-surface p-2 text-xs text-slate-400">
              {JSON.stringify(log.metadata_json, null, 2)}
            </pre>
          )}
        </article>
      ))}
    </div>
  );
}
