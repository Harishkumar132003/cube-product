import { Fragment, useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { ApiError, api } from '../api';
import { Empty } from '../components/Empty';
import { Icon } from '../components/Icon';
import { StatTile } from '../components/StatTile';
import { Topbar } from '../components/Topbar';
import { joinsKept, missingTargets } from '../lib/selection';
import { useApp } from '../state';
import type { Scan, ScanTable } from '../types';
import type { ProjectContext } from './ProjectLayout';

const KIND_LABEL: Record<ScanTable['kind'], string> = {
  table: 'table',
  partitioned_table: 'partitioned',
  view: 'view',
  materialized_view: 'matview',
};

export function TablesPage() {
  const { project, reload } = useOutletContext<ProjectContext>();
  const { refresh } = useApp();

  const [scan, setScan] = useState<Scan | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  /* Empty selection on the server means "everything". The UI resolves that to
   * an explicit set once a scan exists, so the checkboxes have a real state. */
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const load = async () => {
    try {
      const fresh = await api.getScan(project.id);
      setScan(fresh);
      const all = fresh.catalog.tables.map((t) => t.qualified_name);
      const stored = project.selected_tables ?? [];
      setSelected(new Set(stored.length ? stored.filter((n) => all.includes(n)) : all));
      setError(null);
    } catch {
      setScan(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  const runScan = async () => {
    setScanning(true);
    setError(null);
    try {
      await api.runScan(project.id);
      await load();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Scan failed.');
    } finally {
      setScanning(false);
    }
  };

  const all = scan?.catalog.tables.map((t) => t.qualified_name) ?? [];

  const persist = async (next: Set<string>) => {
    setSelected(next);
    try {
      // A full selection is stored as empty, so a later scan picks up new tables.
      await api.selectTables(project.id, next.size === all.length ? [] : [...next]);
      await reload();
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the selection.');
    }
  };

  const toggle = (name: string) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    void persist(next);
  };

  const scanButton = (
    <button
      className="btn btn--solid"
      type="button"
      disabled={scanning || !project.connection}
      onClick={() => void runScan()}
    >
      <Icon name="scan" size={14} />
      {scanning ? 'Scanning…' : scan ? 'Rescan' : 'Run scan'}
    </button>
  );

  if (loading) return <div className="loading">Loading scan…</div>;

  if (!scan) {
    return (
      <>
        <Topbar title="Tables" actions={scanButton} />
        <div className="page">
          {error && <div className="banner banner--error">{error}</div>}
          <section className="card">
            <Empty
              icon="database"
              title="Not scanned yet"
              body="A scan reads the catalog and column statistics from the selected schemas. It is read-only and takes a moment."
              actions={scanButton}
            />
          </section>
        </div>
      </>
    );
  }

  const tables = scan.catalog.tables;
  const missing = missingTargets(tables, selected);
  const kept = joinsKept(tables, selected);
  const visible = tables.filter((t) => {
    const q = query.toLowerCase();
    return (
      !q ||
      t.name.toLowerCase().includes(q) ||
      t.columns.some((c) => c.name.toLowerCase().includes(q))
    );
  });

  const selectedColumns = tables
    .filter((t) => selected.has(t.qualified_name))
    .reduce((n, t) => n + t.columns.length, 0);

  return (
    <>
      <Topbar
        title="Tables"
        onSearch={setQuery}
        searchPlaceholder="Filter tables and columns…"
        actions={scanButton}
      />

      <div className="page stack">
        {error && <div className="banner banner--error">{error}</div>}

        <div className="stats">
          <StatTile
            label="In the model"
            icon="grid"
            value={selected.size}
            pill={{
              tone: selected.size === tables.length ? 'up' : 'violet',
              text: selected.size === tables.length ? 'all relations' : `of ${tables.length}`,
            }}
            note={`${selectedColumns} columns`}
          />
          <StatTile
            label="Joins kept"
            icon="layers"
            value={kept}
            pill={
              missing.length === 0
                ? { tone: 'up', text: 'nothing dangling' }
                : { tone: 'warn', text: `${missing.length} unreachable` }
            }
            note={`of ${scan.counts.declared_foreign_keys} declared`}
          />
          <StatTile
            label="Profiled"
            icon="check"
            value={scan.profile.columns_profiled}
            pill={
              scan.profile.columns_without_stats > 0
                ? { tone: 'idle', text: `${scan.profile.columns_without_stats} without stats` }
                : { tone: 'up', text: 'all columns' }
            }
            note="from pg_stats, no table reads"
          />
          <StatTile
            label="Redacted"
            icon="shield"
            value={scan.profile.columns_redacted}
            pill={{ tone: 'warn', text: 'values withheld' }}
            note="PII and high-cardinality columns"
          />
        </div>

        {missing.length > 0 && (
          <section className="card">
            <div className="card__head">
              <span className="card__label">
                <Icon name="alert" size={13} />
                Foreign keys leaving the selection
              </span>
              <span className="card__tools">
                <button
                  className="btn btn--ghost btn--sm"
                  type="button"
                  onClick={() =>
                    void persist(
                      new Set([
                        ...selected,
                        ...missing.filter((m) => !m.outsideScan).map((m) => m.target),
                      ]),
                    )
                  }
                >
                  <Icon name="plus" size={13} />
                  Include all
                </button>
              </span>
            </div>
            <div className="feed">
              {missing.map((m) => (
                <div className="feed__row" key={m.target}>
                  <span className="feed__glyph feed__glyph--warn">
                    <Icon name="alert" size={14} />
                  </span>
                  <span className="feed__text">
                    <span className="feed__name mono">{m.target}</span>
                    <span className="feed__sub">
                      {m.outsideScan
                        ? 'Outside the scanned schemas. '
                        : 'Not selected. '}
                      Referenced by {m.referencedBy.map((r) => r).join(', ')}.{' '}
                      {m.referencedBy.length === 1 ? 'That join' : 'Those joins'} cannot
                      be generated without it.
                    </span>
                  </span>
                  <span className="feed__side">
                    {m.outsideScan ? (
                      <span className="pill pill--idle">out of scope</span>
                    ) : (
                      <button
                        className="btn btn--ghost btn--sm"
                        type="button"
                        onClick={() => toggle(m.target)}
                      >
                        Include
                      </button>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="card">
          <div className="card__head">
            <span className="card__label">
              <Icon name="database" size={13} />
              {visible.length} of {tables.length} relations
            </span>
            <span className="card__tools">
              <button
                className="btn btn--quiet"
                type="button"
                onClick={() => void persist(new Set(all))}
              >
                Select all
              </button>
              <button
                className="btn btn--quiet"
                type="button"
                onClick={() => void persist(new Set())}
              >
                Clear
              </button>
              <span className="pill pill--idle">
                scanned {new Date(scan.created_at).toLocaleString()}
              </span>
            </span>
          </div>

          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Relation</th>
                  <th>Kind</th>
                  <th>Rows</th>
                  <th>Columns</th>
                  <th>Primary key</th>
                  <th>Foreign keys</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t) => {
                  const on = selected.has(t.qualified_name);
                  const dangling = t.foreign_keys.filter(
                    (f) => on && f.target && !selected.has(f.target),
                  ).length;
                  return (
                    <Fragment key={t.qualified_name}>
                      <tr className={on ? undefined : 'is-excluded'}>
                        <td>
                          <span className="cell-name">
                            <input
                              className="checkbox"
                              type="checkbox"
                              checked={on}
                              onChange={() => toggle(t.qualified_name)}
                              onClick={(e) => e.stopPropagation()}
                              aria-label={`Include ${t.name}`}
                            />
                            <button
                              type="button"
                              className="cell-expand"
                              onClick={() =>
                                setOpen(open === t.qualified_name ? null : t.qualified_name)
                              }
                            >
                              <Icon
                                name="chevron"
                                size={12}
                                className={open === t.qualified_name ? 'rot-open' : 'rot-shut'}
                              />
                              <span className="mono">{t.name}</span>
                            </button>
                            {t.rls && <span className="pill pill--idle">RLS</span>}
                          </span>
                        </td>
                        <td>{KIND_LABEL[t.kind]}</td>
                        <td className={t.rows === null ? 'is-flagged' : undefined}>
                          {t.rows === null ? 'unknown' : t.rows.toLocaleString()}
                        </td>
                        <td>{t.columns.length}</td>
                        <td className={t.primary_key.length === 0 ? 'is-flagged' : undefined}>
                          {t.primary_key.join(', ') || 'none'}
                        </td>
                        <td className={t.foreign_keys.length === 0 ? 'is-zero' : undefined}>
                          {t.foreign_keys.length}
                          {dangling > 0 && (
                            <span className="pill pill--warn" style={{ marginLeft: 6 }}>
                              {dangling} unreachable
                            </span>
                          )}
                        </td>
                      </tr>
                      {open === t.qualified_name && (
                        <tr>
                          <td colSpan={6} className="col-detail">
                            <ColumnList table={t} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </>
  );
}

function ColumnList({ table }: { table: ScanTable }) {
  return (
    <div className="cols">
      {table.columns.map((c) => {
        const s = c.stats;
        const values = c.enum_values?.length ? c.enum_values : s?.values?.map((v) => v.value);
        return (
          <div className="cols__row" key={c.name}>
            <span className="cols__name mono">{c.name}</span>
            <span className="cols__type mono">{c.data_type}</span>
            <span className="cols__flags">
              {table.primary_key.includes(c.name) && <span className="pill pill--violet">pk</span>}
              {table.foreign_keys.some((f) => f.columns.includes(c.name)) && (
                <span className="pill pill--info">fk</span>
              )}
              {c.not_null && <span className="pill pill--idle">not null</span>}
              {s?.unique && <span className="pill pill--idle">unique</span>}
              {s?.pii_suspected && <span className="pill pill--warn">pii</span>}
            </span>
            <span className="cols__stat">
              {s?.distinct != null ? `${s.distinct.toLocaleString()} distinct` : 'no stats'}
              {s?.null_fraction ? ` · ${Math.round(s.null_fraction * 100)}% null` : ''}
            </span>
            <span className="cols__values mono">
              {values?.length ? values.slice(0, 4).join(' · ') : ''}
            </span>
          </div>
        );
      })}
    </div>
  );
}
