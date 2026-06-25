import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { createTopic } from "../api/client";

interface CreateTopicFormProps {
  projectId: string;
  onCreated?: () => void;
  onCancel?: () => void;
}

export function CreateTopicForm({ projectId, onCreated, onCancel }: CreateTopicFormProps) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      createTopic(projectId, {
        title: title.trim(),
        description: description.trim() || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["topics", projectId] });
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
        <label className="mb-1 block text-xs text-slate-400">标题</label>
        <input
          required
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="一条讨论 / 提问 / 状态同步"
        />
      </div>
      <div>
        <label className="mb-1 block text-xs text-slate-400">描述（可选）</label>
        <textarea
          className="min-h-[80px] w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      {createMutation.isError && <p className="text-sm text-red-400">创建失败</p>}
      <div className="flex justify-end gap-2 pt-1">
        {onCancel && (
          <button type="button" className="btn-secondary" onClick={onCancel}>
            取消
          </button>
        )}
        <button type="submit" className="btn-primary" disabled={createMutation.isPending}>
          {createMutation.isPending ? "创建中…" : "发布话题"}
        </button>
      </div>
    </form>
  );
}
