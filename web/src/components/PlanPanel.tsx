import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { revisePlan } from "../api/client";
import type { PlanVersion } from "../api/types";
import { PlanDiffView } from "./PlanDiffView";
import { MarkdownBody } from "./MarkdownBody";
import { FileBreadcrumb } from "./FileBreadcrumb";
import { useDoc } from "../hooks/useDoc";
import { useAuth } from "../context/AuthContext";

interface PlanPanelProps {
  experimentId: string;
  plan: PlanVersion | null;
  versions: PlanVersion[];
  version: number;
  onSelectVersion: (v: number) => void;
  onUpdated: () => void;
  canRevise?: boolean;
  highlightRevise?: boolean;
  planFilePath?: string | null;
}

export function PlanPanel({
  experimentId,
  plan,
  versions,
  version,
  onSelectVersion,
  onUpdated,
  canRevise = false,
  highlightRevise = false,
  planFilePath = null,
}: PlanPanelProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");
  const [diffMode, setDiffMode] = useState(false);
  const [compareVersion, setCompareVersion] = useState<number | null>(null);
  const { agent } = useAuth();
  const docQuery = useDoc(agent?.project_id ?? null, planFilePath);

  const comparePlan =
    compareVersion != null ? versions.find((p) => p.version === compareVersion) ?? null : null;

  const reviseMutation = useMutation({
    mutationFn: () =>
      revisePlan(experimentId, {
        content_md: draft.trim(),
        change_note: note.trim() || null,
      }),
    onSuccess: () => {
      setEditing(false);
      setDraft("");
      setNote("");
      onUpdated();
    },
  });

  useEffect(() => {
    if (highlightRevise && canRevise && plan) {
      setDraft(plan.content_md);
      setEditing(true);
    }
  }, [highlightRevise, canRevise, plan]);

  return (
    <div className={`card h-full ${highlightRevise ? "ring-2 ring-accent" : ""}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-white">实验计划 v{version}</h2>
        <div className="flex flex-wrap items-center gap-2">
          {versions.length > 1 && (
            <>
              <button
                type="button"
                className={`btn-secondary py-1 text-xs ${diffMode ? "ring-1 ring-accent" : ""}`}
                onClick={() => {
                  setDiffMode((v) => !v);
                  if (!diffMode && compareVersion == null) {
                    const prevVersions = versions.filter((p) => p.version < version);
                    const prev = prevVersions.length > 0 ? prevVersions[prevVersions.length - 1] : undefined;
                    setCompareVersion(prev?.version ?? versions[0]?.version ?? null);
                  }
                }}
              >
                {diffMode ? "关闭对比" : "对比版本"}
              </button>
              {diffMode && (
                <select
                  className="rounded border border-surface-border bg-surface px-2 py-1 text-sm text-slate-200"
                  value={compareVersion ?? ""}
                  onChange={(e) => setCompareVersion(Number(e.target.value))}
                >
                  {versions
                    .filter((p) => p.version !== version)
                    .map((p) => (
                      <option key={p.id} value={p.version}>
                        对比 v{p.version}
                        {p.change_note ? ` — ${p.change_note}` : ""}
                      </option>
                    ))}
                </select>
              )}
            </>
          )}
          {versions.length > 1 && !diffMode && (
            <select
              className="rounded border border-surface-border bg-surface px-2 py-1 text-sm text-slate-200"
              value={version}
              onChange={(e) => onSelectVersion(Number(e.target.value))}
            >
              {versions.map((p) => (
                <option key={p.id} value={p.version}>
                  v{p.version}
                  {p.change_note ? ` — ${p.change_note}` : ""}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>
      {plan && diffMode && comparePlan && comparePlan.version !== plan.version ? (
        <PlanDiffView
          oldText={comparePlan.content_md}
          newText={plan.content_md}
          oldLabel={`v${comparePlan.version}`}
          newLabel={`v${plan.version}`}
        />
      ) : planFilePath ? (
        <div className="max-h-[480px] overflow-y-auto">
          <FileBreadcrumb path={planFilePath} className="mb-2" />
          <MarkdownBody content={docQuery.data?.content ?? plan?.content_md ?? ""} />
        </div>
      ) : plan ? (
        <div className="markdown-body max-h-[480px] overflow-y-auto">
          <MarkdownBody content={plan.content_md} />
        </div>
      ) : (
        <p className="text-sm text-slate-500">暂无计划</p>
      )}

      {editing ? (
        <div className="mt-4 space-y-2 border-t border-surface-border pt-4">
          <textarea
            className="min-h-[120px] w-full rounded border border-surface-border bg-surface px-2 py-1 font-mono text-sm text-white"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="修订后的计划（Markdown）"
          />
          <input
            className="w-full rounded border border-surface-border bg-surface px-2 py-1 text-sm text-white"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="变更说明（可选）"
          />
          {reviseMutation.isError && <p className="text-sm text-red-400">提交失败，请检查权限</p>}
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">修订后将通知评审方重新确认</span>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setEditing(false);
                  setDraft("");
                }}
              >
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!draft.trim() || reviseMutation.isPending}
                onClick={() => reviseMutation.mutate()}
              >
                {reviseMutation.isPending ? "提交中…" : "提交修订"}
              </button>
            </div>
          </div>
        </div>
      ) : (
        canRevise && (
          <div className="mt-3 border-t border-surface-border pt-3">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setDraft(plan?.content_md ?? "");
                setEditing(true);
              }}
            >
              修订计划
            </button>
          </div>
        )
      )}
    </div>
  );
}
