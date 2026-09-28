import { useCallback, useEffect, useState } from 'react';
import { Outlet, useParams } from 'react-router-dom';
import { api } from '../api';
import { useApp } from '../state';
import type { Project } from '../types';

export interface ProjectContext {
  project: Project;
  reload: () => Promise<Project | null>;
}

/** Loads the project named in the URL and hands it to the nested pages. */
export function ProjectLayout() {
  const { projectId } = useParams();
  const { replace } = useApp();
  const [project, setProject] = useState<Project | null>(null);
  const [missing, setMissing] = useState(false);

  const reload = useCallback(async () => {
    if (!projectId) return null;
    try {
      const fresh = await api.getProject(projectId);
      setProject(fresh);
      replace(fresh);
      return fresh;
    } catch {
      setMissing(true);
      return null;
    }
  }, [projectId, replace]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (missing) {
    return (
      <div className="page">
        <div className="banner banner--error">
          That project no longer exists.
        </div>
      </div>
    );
  }

  if (!project) {
    return <div className="loading">Loading project…</div>;
  }

  return <Outlet context={{ project, reload } satisfies ProjectContext} />;
}
