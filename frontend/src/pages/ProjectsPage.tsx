import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, api } from '../api';
import { Empty } from '../components/Empty';
import { Icon } from '../components/Icon';
import { PageHeader } from '../components/PageHeader';
import { useApp } from '../state';
import type { Project } from '../types';

export function ProjectsPage() {
  const { projects, refresh } = useApp();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const create = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    setError(null);
    try {
      const project = await api.createProject(trimmed);
      setName('');
      await refresh();
      navigate(`/projects/${project.id}/connection`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the project.');
    } finally {
      setCreating(false);
    }
  };

  const remove = async (project: Project) => {
    setConfirming(null);
    try {
      await api.deleteProject(project.id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete the project.');
    }
  };

  return (
    <>
      <PageHeader
        title="Projects"
        lede="Each project connects one Postgres database and holds the Cube model generated from it."
      />

      <div className="page">

      <div className="stack">
        {error && <div className="banner banner--error">{error}</div>}

        <section className="card narrow">
          <div className="card__head">
            <div>
              <h2 className="card__title">New project</h2>
            </div>
          </div>
          <div className="card__body">
            <div className="field">
              <label className="field__label" htmlFor="project-name">
                Name
              </label>
              <input
                id="project-name"
                className="field__input"
                value={name}
                placeholder="Production analytics"
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void create();
                }}
              />
              <p className="field__hint">
                You will connect its database on the next screen.
              </p>
            </div>
          </div>
          <div className="card__foot">
            <button
              className="btn btn--primary"
              type="button"
              disabled={creating || name.trim() === ''}
              onClick={() => void create()}
            >
              <Icon name="plus" size={14} />
              {creating ? 'Creating…' : 'Create project'}
            </button>
          </div>
        </section>

        <section className="card">
          <div className="card__head">
            <div>
              <h2 className="card__title">All projects</h2>
            </div>
            <div className="card__head-actions">
              <span className="pill pill--idle">{projects.length}</span>
            </div>
          </div>

          {projects.length === 0 ? (
            <Empty
              icon="folder"
              title="No projects yet"
              body="Create one above to connect a database and generate a semantic layer from it."
            />
          ) : (
            <div className="list">
              {projects.map((project) => (
                <div className="list__row" key={project.id}>
                  <button
                    type="button"
                    className="list__open"
                    onClick={() => navigate(`/projects/${project.id}`)}
                  >
                    <div className="list__name">{project.name}</div>
                    <div className="list__meta">
                      {project.connection
                        ? `${project.connection.user}@${project.connection.host}:${project.connection.port}/${project.connection.database}`
                        : 'no database connected'}
                    </div>
                  </button>

                  {project.connection ? (
                    <span className="pill pill--up">Connected</span>
                  ) : (
                    <span className="pill pill--idle">Not connected</span>
                  )}

                  {confirming === project.id ? (
                    <div className="row">
                      <button
                        className="btn btn--danger btn--sm"
                        type="button"
                        onClick={() => void remove(project)}
                      >
                        Delete
                      </button>
                      <button
                        className="btn btn--ghost btn--sm"
                        type="button"
                        onClick={() => setConfirming(null)}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      className="btn btn--quiet"
                      type="button"
                      onClick={() => setConfirming(project.id)}
                    >
                      Delete
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
      </div>
    </>
  );
}
