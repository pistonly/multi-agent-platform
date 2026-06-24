import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { fetchProjectStatusVersions, reviseProjectStatus } from "../api/client";
import type { ProjectStatusVersion } from "../api/types";
import { StatusMarkdown } from "./StatusMarkdown";

interface StatusEditorProps {
  projectId: string;
  currentContent: string;
  currentVersion: number;
}

export function StatusEditor({ projectId, currentContent, currentVersion }: StatusEditorProps) {
  const queryClient = useQueryClient();
  const [content, setContent] = useState(currentContent);
  const [changeNote, setChangeNote] = useState("");
  const [expandedVersion, setExpandedVersion] = useState<number | null>(null);

  useEffect(() => {
    setContent(currentContent);
  }, [currentContent]);

  const versionsQuery = useQuery({
    queryKey: ["project-status-versions", projectId],
    queryFn: () => fetchProjectStatusVersions(projectId),
  });

  const reviseMutation = useMutation({
    mutationFn: () =>
      reviseProjectStatus(projectId, {
        content_md: content,
        change_note: changeNote.trim() || null,
      }),
    onSuccess: () => {
      setChangeNote("");
      queryClient.invalidateQueries({ queryKey: ["project-status", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project-status-versions", projectId] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });

  const versions = versionsQuery.data ?? [];

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">编辑 Current Status</h2>
        <span className="text-xs text-slate-500">当前版本 v{currentVersion}</span>
      </div>

      <textarea
        className="min-h-[240px] w-full rounded border border-surface-border bg-surface px-3 py-2 font-mono text-sm text-white"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        spellCheck={false}
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[200px] flex-1">
          <label htmlFor="change-note" className="mb-1 block text-xs text-slate-400">
            变更说明（可选）
          </label>
          <input
            id="change-note"
            type="text"
            className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
            value={changeNote}
            onChange={(e) => setChangeNote(e.target.value)}
            placeholder="例如：更新本迭代目标"
          />
        </div>
        <button
          type="button"
          className="btn-primary"
          disabled={reviseMutation.isPending || !content.trim()}
          onClick={() => reviseMutation.mutate()}
        >
          {reviseMutation.isPending ? "保存中…" : "发布新版本"}
        </button>
      </div>

      {reviseMutation.isError && (
        <p className="text-sm text-red-400">保存失败，请确认您有 Admin 权限</p>
      )}

      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-300">版本历史</h3>
        {versionsQuery.isLoading && <p className="text-sm text-slate-500">加载版本…</p>}
        <ul className="space-y-2">
          {versions.map((version: ProjectStatusVersion) => (
            <li key={version.id} className="rounded border border-surface-border bg-surface">
              <button
                type="button"
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-surface-raised"
                onClick={() =>
                  setExpandedVersion(expandedVersion === version.version ? null : version.version)
                }
              >
                <span className="text-white">
                  v{version.version}
                  {version.change_note && (
                    <span className="ml-2 text-slate-400">— {version.change_note}</span>
                  )}
                </span>
                <span className="text-xs text-slate-500">
                  {new Date(version.created_at).toLocaleString()}
                </span>
              </button>
              {expandedVersion === version.version && (
                <div className="border-t border-surface-border px-3 py-3">
                  <StatusMarkdown content={version.content_md} />
                </div>
              )}
            </li>
          ))}
          {versions.length === 0 && !versionsQuery.isLoading && (
            <li className="text-sm text-slate-500">暂无历史版本</li>
          )}
        </ul>
      </div>
    </section>
  );
}
