import { useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { BarList } from '../components/BarList';
import { Empty } from '../components/Empty';
import { Icon } from '../components/Icon';
import { StatTile } from '../components/StatTile';
import {
  StepTracker,
  type Step,
  type StepIcon,
  type StepState,
} from '../components/StepTracker';
import { Topbar } from '../components/Topbar';
import { useApp } from '../state';
import type { ProjectContext } from './ProjectLayout';

export function OverviewPage() {
  const { project, reload } = useOutletContext<ProjectContext>();
  const { openaiReady } = useApp();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');

  const connection = project.connection;
  const probe = connection?.last_probe ?? null;
  const chosen = connection?.selected_schemas ?? [];
  const hasContext = Boolean(project.business_context?.trim());

  const inScope = (probe?.schemas ?? []).filter(
    (s) => chosen.length === 0 || chosen.includes(s.name),
  );
  const tables = inScope.reduce((n, s) => n + s.tables, 0);
  const relations = inScope.reduce(
    (n, s) => n + s.tables + s.views + s.materialized_views,
    0,
  );
  const rls = inScope.reduce((n, s) => n + s.rls_tables, 0);
  const unanalyzed = inScope.reduce((n, s) => n + s.never_analyzed, 0);
  const unreadable = inScope.reduce((n, s) => n + s.unreadable, 0);
  const pct = (n: number) => (tables ? Math.round((n / tables) * 100) : 0);

  /* The schema has to be read before anyone can be asked to describe a table,
   * so the scan sits between connecting and context. */
  const scanned = Boolean(project.last_scan_at);
  const generated = Boolean(project.last_generated_at);

  const step = (
    done: boolean,
    blocked: boolean,
    title: string,
    icon: StepIcon,
    status: string,
    to: string | null,
  ) => ({ title, icon, status, to, done, blocked });

  const raw = [
    step(
      Boolean(connection),
      false,
      'Connect',
      'plug',
      connection
        ? `${connection.database} · ${tables} tables`
        : 'Attach the Postgres database',
      `/projects/${project.id}/connection`,
    ),
    step(
      scanned,
      !connection,
      'Scan',
      'scan',
      !connection
        ? 'Needs a connection'
        : scanned
          ? `Read ${new Date(project.last_scan_at!).toLocaleDateString()}`
          : 'Read the catalog and column statistics',
      `/projects/${project.id}/tables`,
    ),
    step(
      hasContext && scanned,
      !connection,
      'Context',
      'pencil',
      !connection
        ? 'Needs a connection'
        : hasContext
          ? scanned
            ? 'Flow and tables described'
            : 'Flow described · table descriptions need the scan'
          : 'Describe the business flow and what the tables hold',
      `/projects/${project.id}/context`,
    ),
    step(
      generated,
      !scanned || !openaiReady,
      'Generate',
      'sparkle',
      !openaiReady
        ? 'OpenAI key not set'
        : !scanned
          ? 'Needs the scan'
          : generated
            ? `Generated ${new Date(project.last_generated_at!).toLocaleDateString()}`
            : 'Build cubes, dimensions and measures',
      `/projects/${project.id}/generate`,
    ),
    step(
      false,
      !generated,
      'Review',
      'check',
      generated
        ? 'Read the YAML and settle the open questions'
        : 'Needs a generated model',
      `/projects/${project.id}/review`,
    ),
  ];

  /* First step that is neither done nor blocked is where you are. */
  const currentIndex = raw.findIndex((s) => !s.done && !s.blocked);
  const steps: Step[] = raw.map((s, i) => ({
    title: s.title,
    status: s.status,
    icon: s.icon,
    to: s.to,
    state: (s.done ? 'done' : i === currentIndex ? 'current' : 'locked') as StepState,
  }));
  const current = currentIndex >= 0 ? raw[currentIndex] : null;

  const matchedWarnings = probe
    ? probe.warnings.filter(
        (w) =>
          w.code.toLowerCase().includes(query.toLowerCase()) ||
          w.message.toLowerCase().includes(query.toLowerCase()),
      )
    : [];

  return (
    <>
      <Topbar
        title="Dashboard"
        onSearch={probe ? setQuery : undefined}
        searchPlaceholder="Filter schemas and codes…"
        actions={
          <>
            {connection && (
              <button className="btn btn--ghost" type="button" onClick={() => void reload()}>
                <Icon name="refresh" size={14} />
                Refresh metadata
              </button>
            )}
            <button
              className="btn btn--solid"
              type="button"
              onClick={() =>
                navigate(current?.to ?? `/projects/${project.id}/connection`)
              }
            >
              <Icon name={current?.icon ?? 'plug'} size={14} />
              {current ? current.title : 'Open'}
            </button>
          </>
        }
      />

      <div className="page stack">
        <section className="card">
          <StepTracker steps={steps} onGo={(to) => navigate(to)} />
          {current && (
            <div className="next-bar">
              <span className="next-bar__text">
                Next: <b>{current.title}</b> — {current.status}
              </span>
              <span className="next-bar__spacer" />
              {current.to ? (
                <button
                  className="btn btn--primary btn--sm"
                  type="button"
                  onClick={() => navigate(current.to!)}
                >
                  <Icon name={current.icon} size={13} />
                  Open
                </button>
              ) : (
                <span className="pill pill--idle">Not built yet</span>
              )}
            </div>
          )}
        </section>

        {!connection || !probe ? (
          <section className="card">
            <Empty
              icon="database"
              title="No database connected"
              body="Connect a Postgres database and the dashboard fills in from its metadata."
              actions={
                <button
                  className="btn btn--primary"
                  type="button"
                  onClick={() => navigate(`/projects/${project.id}/connection`)}
                >
                  Connect database
                </button>
              }
            />
          </section>
        ) : (
          <>
            <div className="stats">
              <StatTile
                label="Tables in scope"
                icon="grid"
                value={tables.toLocaleString()}
                pill={{
                  tone: 'violet',
                  text: `${inScope.length} schema${inScope.length === 1 ? '' : 's'}`,
                }}
                note={`${relations.toLocaleString()} relations total`}
              />
              <StatTile
                label="Readable"
                icon="check"
                value={unreadable === 0 ? 'All' : `${relations - unreadable}/${relations}`}
                pill={
                  unreadable === 0
                    ? { tone: 'up', text: 'full access' }
                    : { tone: 'down', text: `${unreadable} blocked` }
                }
                note={unreadable === 0 ? 'nothing excluded' : 'missing SELECT grant'}
              />
              <StatTile
                label="Row-level security"
                icon="shield"
                value={rls.toLocaleString()}
                pill={
                  rls === 0
                    ? { tone: 'idle', text: 'none' }
                    : probe.server.bypasses_rls
                      ? { tone: 'down', text: 'bypassed' }
                      : { tone: 'up', text: 'enforced' }
                }
                note={`${pct(rls)}% of tables`}
              />
              <StatTile
                label="Never analyzed"
                icon="alert"
                value={unanalyzed.toLocaleString()}
                pill={
                  unanalyzed === 0
                    ? { tone: 'up', text: 'all analyzed' }
                    : { tone: 'warn', text: 'sampling needed' }
                }
                note={`${pct(unanalyzed)}% of tables`}
              />
            </div>

            <div className="grid grid--main">
              <section className="card">
                <div className="card__head">
                  <span className="card__label">
                    <Icon name="layers" size={13} />
                    Relations per schema
                  </span>
                  <span className="card__tools">
                    <span className="pill pill--idle">{chosen.length} selected</span>
                  </span>
                </div>
                <div className="card__body">
                  <BarList
                    unit="relations"
                    items={probe.schemas
                      .filter((s) => s.name.toLowerCase().includes(query.toLowerCase()))
                      .map((s) => ({
                        name: s.name,
                        value: s.tables + s.views + s.materialized_views,
                        meta: `${s.tables} tables`,
                        soft: chosen.length > 0 && !chosen.includes(s.name),
                      }))}
                  />
                </div>
              </section>

              <section className="card">
                <div className="card__head">
                  <span className="card__label">
                    <Icon name="alert" size={13} />
                    Diagnostics
                  </span>
                  <span className="card__tools">
                    <span className="pill pill--idle">{matchedWarnings.length}</span>
                  </span>
                </div>
                {matchedWarnings.length === 0 ? (
                  <Empty
                    icon="check"
                    title="Nothing to flag"
                    body="This role sees everything the pipeline needs."
                  />
                ) : (
                  <div className="feed">
                    {matchedWarnings.map((w) => (
                      <div className="feed__row" key={w.code}>
                        <span className={`feed__glyph feed__glyph--${w.level}`}>
                          <Icon name={w.level === 'info' ? 'check' : 'alert'} size={14} />
                        </span>
                        <span className="feed__text">
                          <span className="feed__name mono">{w.code}</span>
                          <span className="feed__sub">{w.message}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>
          </>
        )}
      </div>
    </>
  );
}
