import { Link, useParams } from "react-router-dom";
import { TopicsListPanel } from "../components/TopicsListPanel";

export function ProjectTopicsPage() {
  const { projectId } = useParams<{ projectId: string }>();

  if (!projectId) {
    return <p className="text-red-400">缺少项目 ID</p>;
  }

  return (
    <div className="space-y-4">
      <Link to={`/projects/${projectId}`} className="text-sm text-slate-500 hover:text-white">
        ← 项目
      </Link>
      <h1 className="text-2xl font-bold text-white">话题列表</h1>
      <TopicsListPanel projectId={projectId} title="全部话题" />
    </div>
  );
}
