import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { createProject } from "../api/client";

interface CreateProjectFormProps {
  onCreated?: () => void;
  onCancel?: () => void;
}

export function CreateProjectForm({ onCreated, onCancel }: CreateProjectFormProps) {
  const queryClient = useQueryClient();
  const [projectKey, setProjectKey] = useState("");
  const [name, setName] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");
  const [description, setDescription] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      createProject({
        project_key: projectKey.trim().toLowerCase(),
        name: name.trim(),
        workspace_path: workspacePath.trim(),
        description: description.trim() || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["status"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      onCreated?.();
    },
  });

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        createMutation.mutate();
      }}
    >
      <div>
        <label htmlFor="project-key" className="mb-1 block text-xs text-slate-400">
          project_key
        </label>
        <input
          id="project-key"
          required
          pattern="[a-z0-9][a-z0-9-_]{0,62}"
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
          value={projectKey}
          onChange={(e) => setProjectKey(e.target.value)}
          placeholder="test-map"
        />
      </div>
      <div>
        <label htmlFor="project-name" className="mb-1 block text-xs text-slate-400">
          名称
        </label>
        <input
          id="project-name"
          required
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="workspace-path" className="mb-1 block text-xs text-slate-400">
          工作区路径
        </label>
        <input
          id="workspace-path"
          required
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 font-mono text-sm text-white"
          value={workspacePath}
          onChange={(e) => setWorkspacePath(e.target.value)}
          placeholder="/path/to/workspace"
        />
      </div>
      <div>
        <label htmlFor="project-desc" className="mb-1 block text-xs text-slate-400">
          描述（可选）
        </label>
        <input
          id="project-desc"
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      {createMutation.isError && (
        <p className="text-sm text-red-400">创建失败，请检查 project_key 是否已存在</p>
      )}
      <div className="flex justify-end gap-2 pt-1">
        {onCancel && (
          <button type="button" className="btn-secondary" onClick={onCancel}>
            取消
          </button>
        )}
        <button type="submit" className="btn-primary" disabled={createMutation.isPending}>
          {createMutation.isPending ? "创建中…" : "创建项目"}
        </button>
      </div>
    </form>
  );
}
