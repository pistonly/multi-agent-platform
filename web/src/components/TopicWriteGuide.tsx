import { CopyableCommand } from "./CopyableCommand";
import {
  topicArchiveCommand,
  topicCloseCommand,
  topicCommentCommand,
  topicMigrateCommand,
} from "../utils/topicFsCommands";

interface TopicWriteGuideProps {
  /** FS slug when known; omitted for leftover DB topics. */
  slug?: string | null;
  topicId?: string;
  contentSource?: string;
}

export function TopicWriteGuide({ slug, topicId, contentSource }: TopicWriteGuideProps) {
  const isFs = contentSource === "fs-local" || contentSource === "fs-projection";
  const isDb = contentSource === "db";
  const slugLabel = slug?.trim() || "<slug>";

  return (
    <div className="rounded border border-sky-800 bg-sky-950/30 px-4 py-3 text-sm text-sky-100">
      <p>
        话题评论、关闭、结论和归档请用 CLI 写本地文件。看板只读；再点「评论 / 关闭 /
        沉淀结论」会打到已退役的 DB 写接口（410）。
      </p>
      {isFs && (
        <div className="mt-3 space-y-2">
          <CopyableCommand command={topicCommentCommand(slugLabel)} label="评论" />
          <CopyableCommand command={topicCloseCommand(slugLabel)} label="关闭并写入结论" />
          <CopyableCommand command={topicArchiveCommand(slugLabel)} label="归档" />
        </div>
      )}
      {isDb && topicId && (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-sky-50/90">
            这是存量 DB 话题。请先迁移到 <code className="font-mono">map/topics/</code>，再按 FS
            命令写入。
          </p>
          <CopyableCommand command={topicMigrateCommand(topicId)} label="迁移到 FS" />
        </div>
      )}
    </div>
  );
}
