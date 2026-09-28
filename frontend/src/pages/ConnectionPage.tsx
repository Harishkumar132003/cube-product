import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { ApiError, api } from '../api';
import { ConnectionForm } from '../components/ConnectionForm';
import { Empty } from '../components/Empty';
import { Icon } from '../components/Icon';
import { ProbeReport } from '../components/ProbeReport';
import { SchemaPicker } from '../components/SchemaPicker';
import { Topbar } from '../components/Topbar';
import { useApp } from '../state';
import type { ConnectionInput, ProbeResult } from '../types';
import type { ProjectContext } from './ProjectLayout';

export function ConnectionPage() {
  const { project, reload } = useOutletContext<ProjectContext>();
  const { refresh } = useApp();
  const connection = project.connection;

  const [probe, setProbe] = useState<ProbeResult | null>(
    connection?.last_probe ?? null,
  );
  const [selected, setSelected] = useState<string[]>(
    connection?.selected_schemas ?? [],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const usable = (result: ProbeResult) =>
    result.schemas
      .filter((s) => s.tables + s.views + s.materialized_views > 0)
      .map((s) => s.name);

  /** Reconnect whenever the project already has a database. */
  useEffect(() => {
    if (!connection) return;
    let cancelled = false;
    setBusy(true);
    api
      .reprobe(project.id)
      .then((response) => {
        if (cancelled) return;
        if (response.ok && response.probe) {
          setProbe(response.probe);
          if ((connection.selected_schemas ?? []).length === 0) {
            setSelected(usable(response.probe));
          }
        } else {
          setError(response.error ?? 'Could not reconnect to this database.');
        }
      })
      .catch(() => setError('Could not reach the backend.'))
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [project.id, connection]);

  const test = async (input: ConnectionInput) => {
    setBusy(true);
    setError(null);
    setProbe(null);
    try {
      const response = await api.testConnection(input);
      if (response.ok && response.probe) {
        setProbe(response.probe);
        setSelected(usable(response.probe));
      } else {
        setError(response.error ?? 'Could not connect.');
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reach the backend.');
    } finally {
      setBusy(false);
    }
  };

  const attach = async (input: ConnectionInput) => {
    setBusy(true);
    setError(null);
    try {
      await api.setConnection(project.id, input);
      await api.selectSchemas(project.id, selected);
      await reload();
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not attach the database.');
    } finally {
      setBusy(false);
    }
  };

  const detach = async () => {
    setConfirming(false);
    try {
      await api.clearConnection(project.id);
      setProbe(null);
      setSelected([]);
      await reload();
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not disconnect.');
    }
  };

  const changeSchemas = async (next: string[]) => {
    setSelected(next);
    if (connection) await api.selectSchemas(project.id, next);
  };

  if (connection) {
    return (
      <>
        <Topbar
          title="Connection"
          actions={
            confirming ? (
              <>
                <button className="btn btn--danger" type="button" onClick={() => void detach()}>
                  Disconnect
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
              <>
                <button
                  className="btn btn--ghost"
                  type="button"
                  disabled={busy}
                  onClick={() => void reload()}
                >
                  <Icon name="refresh" size={14} />
                  {busy ? 'Refreshing…' : 'Refresh metadata'}
                </button>
                <button
                  className="btn btn--ghost"
                  type="button"
                  onClick={() => setConfirming(true)}
                >
                  Disconnect
                </button>
              </>
            )
          }
        />

        <div className="page stack">
          <div className="banner banner--info mono">
            {connection.user}@{connection.host}:{connection.port}/{connection.database}
          </div>
          {error && <div className="banner banner--error">{error}</div>}
          {probe && (
            <>
              <ProbeReport probe={probe} />
              <SchemaPicker
                schemas={probe.schemas}
                selected={selected}
                disabled={busy}
                onChange={changeSchemas}
              />
            </>
          )}
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar title="Connect a database" />

      <div className="page">
        <div className="grid grid--split">
          <ConnectionForm
            projectName={project.name}
            busy={busy}
            canSave={probe !== null}
            onTest={test}
            onSave={attach}
          />

          <div className="stack">
            {error && <div className="banner banner--error">{error}</div>}
            {probe ? (
              <>
                <ProbeReport probe={probe} />
                <SchemaPicker
                  schemas={probe.schemas}
                  selected={selected}
                  onChange={setSelected}
                />
              </>
            ) : (
              !error && (
                <section className="card">
                  <Empty
                    icon="database"
                    title="No metadata yet"
                    body="Test the connection to see the server, the schemas this role can read, and anything that would degrade the generated model."
                  />
                </section>
              )
            )}
          </div>
        </div>
      </div>
    </>
  );
}
