import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ApiErrorBridge } from "./components/ApiErrorBridge";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import { AuthProvider } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ExperimentPage } from "./pages/ExperimentPage";
import { AgentsPage } from "./pages/AgentsPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectExperimentsPage } from "./pages/ProjectExperimentsPage";
import { ProjectTopicsPage } from "./pages/ProjectTopicsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StatusPage } from "./pages/StatusPage";
import { TodosPage } from "./pages/TodosPage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { TopicPage } from "./pages/TopicPage";
import { FeedbackPage } from "./pages/FeedbackPage";

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <ApiErrorBridge />
        <BrowserRouter>
          <ErrorBoundary>
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
          </ErrorBoundary>
        </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  );
}
