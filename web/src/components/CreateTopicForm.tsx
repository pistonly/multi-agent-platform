import { useMemo, useState } from "react";
import { CopyableCommand } from "./CopyableCommand";
import { slugifyTopicTitle, topicCreateCommand } from "../utils/topicFsCommands";

interface CreateTopicFormProps {
  onCancel?: () => void;
}

export function CreateTopicForm({ onCancel }: CreateTopicFormProps) {
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const derivedSlug = slugifyTopicTitle(title);
  const command = useMemo(
    () => topicCreateCommand(title, slug.trim() || derivedSlug),
    [title, slug, derivedSlug],
  );

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-300">
        平台不再接收「发布话题」API。填好标题后复制命令，在仓库根目录执行。
      </p>
      <div>
        <label className="mb-1 block text-xs text-slate-400">标题</label>
        <input
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-white"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="一条讨论 / 提问 / 状态同步"
        />
      </div>
      <div>
        <label className="mb-1 block text-xs text-slate-400">slug（文件夹名，可选）</label>
        <input
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 font-mono text-sm text-white"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          placeholder={derivedSlug || "my-topic"}
        />
      </div>
      <CopyableCommand command={command} label="在仓库根目录执行" />
      {onCancel && (
        <div className="flex justify-end pt-1">
          <button type="button" className="btn-secondary" onClick={onCancel}>
            关闭
          </button>
        </div>
      )}
    </div>
  );
}
