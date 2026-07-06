import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ApiErrorBridge } from "./components/ApiErrorBridge";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import { AuthProvider } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { AgentsPage } from "./pages/AgentsPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectTopicsPage } from "./pages/ProjectTopicsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StatusPage } from "./pages/StatusPage";
import { TodosPage } from "./pages/TodosPage";

const ExperimentPage = lazy(() =>
  import("./pages/ExperimentPage").then((m) => ({ default: m.ExperimentPage })),
);
const TopicPage = lazy(() => import("./pages/TopicPage").then((m) => ({ default: m.TopicPage })));
const ProjectExperimentsPage = lazy(() =>
  import("./pages/ProjectExperimentsPage").then((m) => ({ default: m.ProjectExperimentsPage })),
);
const FeedbackPage = lazy(() =>
  import("./pages/FeedbackPage").then((m) => ({ default: m.FeedbackPage })),
);
const NotificationsPage = lazy(() =>
  import("./pages/NotificationsPage").then((m) => ({ default: m.NotificationsPage })),
);

function RouteFallback() {
  return <div className="p-8 text-center text-sm text-slate-400">加载中…</div>;
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <ApiErrorBridge />
        <BrowserRouter>
          <ErrorBoundary>
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                <Route path="/settings" element={<SettingsPage />} />
                <Route element={<Layout />}>
                  <Route index element={<StatusPage />} />
                  <Route path="todos" element={<TodosPage />} />
                  <Route path="notifications" element={<NotificationsPage />} />
                  <Route path="projects/:projectId" element={<ProjectPage />} />
                  <Route path="projects/:projectId/topics" element={<ProjectTopicsPage />} />
                  <Route path="projects/:projectId/experiments" element={<ProjectExperimentsPage />} />
                  <Route path="experiments/:experimentId" element={<ExperimentPage />} />
                  <Route path="topics/:topicId" element={<TopicPage />} />
                  <Route path="feedback" element={<FeedbackPage />} />
                  <Route path="agents" element={<AgentsPage />} />
                </Route>
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  );
}
