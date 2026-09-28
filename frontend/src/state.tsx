import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { api } from './api';
import type { Project } from './types';

interface AppState {
  projects: Project[];
  loading: boolean;
  backendUp: boolean;
  openaiReady: boolean;
  openaiModel: string;
  activeProjectId: string | null;
  /** Re-read projects from the server. */
  refresh: () => Promise<Project[]>;
  /** Replace one project in place, after a connection change. */
  replace: (project: Project) => void;
}

const Ctx = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [backendUp, setBackendUp] = useState(true);
  const [openaiReady, setOpenaiReady] = useState(false);
  const [openaiModel, setOpenaiModel] = useState('');
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const next = await api.listProjects();
    setProjects(next);
    return next;
  }, []);

  const replace = useCallback((project: Project) => {
    setProjects((prev) =>
      prev.map((p) => (p.id === project.id ? project : p)),
    );
  }, []);

  useEffect(() => {
    api
      .bootstrap()
      .then((boot) => {
        setProjects(boot.projects);
        setOpenaiReady(boot.openai_configured);
        setOpenaiModel(boot.openai_model);
        setActiveProjectId(boot.active_project_id);
        setBackendUp(true);
      })
      .catch(() => setBackendUp(false))
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo(
    () => ({
      projects,
      loading,
      backendUp,
      openaiReady,
      openaiModel,
      activeProjectId,
      refresh,
      replace,
    }),
    [
      projects,
      loading,
      backendUp,
      openaiReady,
      openaiModel,
      activeProjectId,
      refresh,
      replace,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const value = useContext(Ctx);
  if (!value) throw new Error('useApp must be used inside AppProvider');
  return value;
}
