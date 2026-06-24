import { Link, useParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ProjectStatusPanel } from "../components/ProjectStatusPanel";

export function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const { isAdmin } = useAuth();

  if (!projectId) {
    return <p className="text-red-400">缺少项目 ID</p>;
  }

  return (
    <div className="space-y-4">
      <Link to="/" className="text-sm text-slate-500 hover:text-white">
        ← {isAdmin ? "看板" : "Current Status"}
      </Link>
      <ProjectStatusPanel projectId={projectId} isAdmin={isAdmin} showHeader />
    </div>
  );
}
