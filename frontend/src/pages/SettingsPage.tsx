import { useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { ApiError, api } from '../api';
import { PageHeader } from '../components/PageHeader';
import { useApp } from '../state';
import type { ProjectContext } from './ProjectLayout';

export function SettingsPage() {
  const { project, reload } = useOutletContext<ProjectContext>();
  const { refresh } = useApp();
  const navigate = useNavigate();

  const [name, setName] = useState(project.name);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rename = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api.renameProject(project.id, name.trim());
      await reload();
      await refresh();
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not rename the project.');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    try {
      await api.deleteProject(project.id);
      await refresh();
      navigate('/projects');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete the project.');
    }
  };

  return (
    <>
      <PageHeader title="Settings" lede={`Project created ${new Date(project.created_at).toLocaleDateString()}.`} />

      <div className="page">

      <div className="stack narrow">
        {error && <div className="banner banner--error">{error}</div>}
        {saved && <div className="banner banner--ok">Project renamed.</div>}

        <section className="card">
          <div className="card__head">
            <div>
              <h2 className="card__title">Name</h2>
            </div>
          </div>
          <div className="card__body">
            <div className="field">
              <label className="field__label" htmlFor="rename">
                Project name
              </label>
              <input
                id="rename"
                className="field__input"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setSaved(false);
                }}
              />
            </div>
          </div>
          <div className="card__foot">
            <button
              className="btn btn--primary"
              type="button"
              disabled={saving || name.trim() === '' || name === project.name}
              onClick={() => void rename()}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </section>

        <section className="card">
          <div className="card__head">
            <div>
              <h2 className="card__title">Delete project</h2>
              <p className="card__note">
                Removes the project and its stored connection. The database
                itself is never touched.
              </p>
            </div>
          </div>
          <div className="card__foot">
            {confirming ? (
              <>
                <button className="btn btn--danger" type="button" onClick={() => void remove()}>
                  Delete {project.name}
                </button>
                <button
                  className="btn btn--ghost"
                  type="button"
                  onClick={() => setConfirming(false)}
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                className="btn btn--ghost"
                type="button"
                onClick={() => setConfirming(true)}
              >
                Delete project
              </button>
            )}
          </div>
        </section>
      </div>
      </div>
    </>
  );
}
