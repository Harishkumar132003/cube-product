import {
  Navigate,
  Route,
  BrowserRouter as Router,
  Routes,
  useParams,
} from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { ConnectionPage } from './pages/ConnectionPage';
import { ContextPage } from './pages/ContextPage';
import { GeneratePage } from './pages/GeneratePage';
import { OverviewPage } from './pages/OverviewPage';
import { ProjectLayout } from './pages/ProjectLayout';
import { AgentPage } from './pages/AgentPage';
import { ProjectsPage } from './pages/ProjectsPage';
import { PromptsPage } from './pages/PromptsPage';
import { ReviewPage } from './pages/ReviewPage';
import { ViewsPage } from './pages/ViewsPage';
import { SettingsPage } from './pages/SettingsPage';
import { TablesPage } from './pages/TablesPage';
import { ToastProvider } from './components/Toast';
import { AppProvider, useApp } from './state';

/** Shell: the rail is always present, the route fills the canvas. */
function Shell({ children }: { children: React.ReactNode }) {
  const { projects, openaiReady, openaiModel, backendUp } = useApp();
  const { projectId } = useParams();
  const active = projects.find((p) => p.id === projectId) ?? null;

  return (
    <div className="shell">
      <Sidebar
        projects={projects}
        active={active}
        openaiReady={openaiReady}
        openaiModel={openaiModel}
        backendUp={backendUp}
      />
      <div className="content">
        {!backendUp && (
          <div className="page" style={{ paddingBottom: 0 }}>
            <div className="banner banner--error">
              The backend is not answering on port 8000. Start it with{' '}
              <code>uvicorn app.main:app --reload --port 8000</code>.
            </div>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

/** Sends you to the last connected project, or to the project list. */
function Landing() {
  const { loading, activeProjectId, projects } = useApp();
  if (loading) return <div className="loading">Loading…</div>;
  const target = activeProjectId ?? projects[0]?.id;
  return <Navigate to={target ? `/projects/${target}` : '/projects'} replace />;
}

function Routed() {
  const { loading } = useApp();
  if (loading) return <div className="loading">Loading…</div>;

  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route
        path="/projects"
        element={
          <Shell>
            <ProjectsPage />
          </Shell>
        }
      />
      <Route
        path="/projects/:projectId"
        element={
          <Shell>
            <ProjectLayout />
          </Shell>
        }
      >
        <Route index element={<OverviewPage />} />
        <Route path="connection" element={<ConnectionPage />} />
        <Route path="tables" element={<TablesPage />} />
        <Route path="context" element={<ContextPage />} />
        <Route path="generate" element={<GeneratePage />} />
        <Route path="model" element={<Navigate to="../generate" replace />} />
        <Route path="views" element={<ViewsPage />} />
        <Route path="prompts" element={<PromptsPage />} />
        <Route path="agent" element={<AgentPage />} />
        <Route path="review" element={<ReviewPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AppProvider>
      <ToastProvider>
        <Router>
          <Routed />
        </Router>
      </ToastProvider>
    </AppProvider>
  );
}
