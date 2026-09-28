import { useEffect, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { ApiError, api } from '../api';
import { Empty } from '../components/Empty';
import { CodeDialog } from '../components/CodeDialog';
import { Icon } from '../components/Icon';
import { GenerationProgress } from '../components/GenerationProgress';
import { StatTile } from '../components/StatTile';
import { Topbar } from '../components/Topbar';
import { useApp } from '../state';
import type { Bundle, GeneratedModel, Scan } from '../types';
import type { ProjectContext } from './ProjectLayout';

const TONE: Record<string, string> = {
  declared: 'pill pill--up',
  user: 'pill pill--violet',
  sampled: 'pill pill--info',
  inferred: 'pill pill--warn',
};

export function GeneratePage() {
  const { project } = useOutletContext<ProjectContext>();
  const { openaiReady, openaiModel } = useApp();
  const navigate = useNavigate();

  const [scan, setScan] = useState<Scan | null>(null);
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** null = use the project's saved selection; a set = narrow this run only. */
  const [subset, setSubset] = useState<Set<string> | null>(null);
  const [model, setModel] = useState<GeneratedModel | null>(null);
  const [generating, setGenerating] = useState(false);
  /** The job whose progress the modal is showing; null when nothing is running. */
  const [jobId, setJobId] = useState<string | null>(null);
  /** True while the evidence bundle is open full screen. */
  const [viewingBundle, setViewingBundle] = useState(false);

  useEffect(() => {
    api.getScan(project.id).then(setScan).catch(() => setScan(null));
    api.getModel(project.id).then(setModel).catch(() => setModel(null));
  }, [project.id]);

  /* Generation runs as a background job on the server and reports progress
   * over SSE, so the browser never holds a multi-minute request open. Only the
   * cheap up-front checks can fail here; the work itself fails through the
   * stream. */
  const generate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const { job_id } = await api.startGeneration(
        project.id,
        subset ? [...subset] : undefined,
      );
      setJobId(job_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Generation failed.');
      setGenerating(false);
    }
  };

  const build = async () => {
    setBusy(true);
    setError(null);
    try {
      setBundle(await api.buildBundle(project.id, subset ? [...subset] : undefined));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not build the bundle.');
    } finally {
      setBusy(false);
    }
  };

  /* Views are not offered here. They are never modelled as cubes -- the same
   * grain reached twice would double-count -- and the bundle always carries
   * them as evidence of intended joins, so there is nothing to choose. */
  const relations = scan?.catalog.tables ?? [];
  const viewCount = relations.filter(
    (t) => t.kind === 'view' || t.kind === 'materialized_view',
  ).length;
  const available = relations
    .filter((t) => t.kind !== 'view' && t.kind !== 'materialized_view')
    .map((t) => t.qualified_name);
  const savedList = project.selected_tables?.length
    ? project.selected_tables.filter((n) => available.includes(n))
    : available;
  const effective = subset ?? new Set(savedList);

  const toggle = (name: string) => {
    const next = new Set(effective);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSubset(next);
    setBundle(null);
  };

  const download = () => {
    if (!bundle) return;
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${project.name.replace(/\W+/g, '-')}-bundle.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (!scan) {
    return (
      <>
        <Topbar title="Generate" />
        <div className="page">
          <section className="card">
            <Empty
              icon="scan"
              title="Scan first"
              body="Generation reads the catalog and column statistics. Run a scan on the Tables page."
              actions={
                <button
                  className="btn btn--primary"
                  type="button"
                  onClick={() => navigate(`/projects/${project.id}/tables`)}
                >
                  Go to Tables
                </button>
              }
            />
          </section>
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar
        title="Generate"
        actions={
          <>
            <button className="btn btn--ghost" type="button" disabled={busy} onClick={() => void build()}>
              <Icon name="layers" size={14} />
              {busy ? 'Building…' : 'Build bundle'}
            </button>
            <button
              className="btn btn--solid"
              type="button"
              disabled={generating || busy || !openaiReady}
              title={openaiReady ? undefined : 'Set CUBEGEN_OPENAI_API_KEY'}
              onClick={() => void generate()}
            >
              <Icon name="sparkle" size={14} />
              {generating ? `Generating ${effective.size} cubes…` : 'Generate'}
            </button>
          </>
        }
      />

      <div className="page stack">
        {error && <div className="banner banner--error">{error}</div>}

        <div className="banner banner--info">
          The bundle is everything generation will see. It is assembled locally
          and nothing leaves this machine until Generate runs
          {openaiReady ? ` against ${openaiModel}` : ''}. Inspect it first.
        </div>

        {generating && (
          <div className="banner banner--info">
            Running the generation steps against {openaiModel}. Roughly ten
            seconds per cube — keep this tab open.
          </div>
        )}

        {model && !generating && (
          /* Counts and open questions live on Review, which is where the model
             is read. Here only what bears on generating again: when it last ran,
             over how much, and that a rerun replaces the folder. */
          <div className="banner banner--ok lastrun">
            <span>
              Last generated {new Date(model.created_at).toLocaleString()} from{' '}
              <span className="mono">{model.scope.selected.length} tables</span>
              {model.scope.narrowed_for_this_run && ' selected for that run'}.
              Generating again replaces every file in the model folder.
            </span>
            <button
              className="btn btn--ghost btn--sm"
              type="button"
              onClick={() => navigate(`/projects/${project.id}/review`)}
            >
              <Icon name="check" size={13} />
              Review it
            </button>
          </div>
        )}

        {viewingBundle && bundle && (
          <CodeDialog
            title="Evidence bundle"
            filename={`${project.name.replace(/\W+/g, '-')}-bundle.json`}
            value={JSON.stringify(bundle, null, 2)}
            language="json"
            note="Everything generation will see. Nothing here has left this machine yet."
            onClose={() => setViewingBundle(false)}
          />
        )}

        {jobId && (
          <GenerationProgress
            eventsUrl={api.generationEventsUrl(project.id, jobId)}
            onDone={(built) => {
              setModel(built);
              setGenerating(false);
            }}
            onFailed={(message) => {
              setError(message);
              setGenerating(false);
            }}
            onClose={() => setJobId(null)}
          />
        )}

        <div className="grid grid--split">
          <section className="card">
            <div className="card__head">
              <span className="card__label">
                <Icon name="grid" size={13} />
                Tables for this run
              </span>
              <span className="card__tools">
                {subset && (
                  <button
                    className="btn btn--quiet"
                    type="button"
                    onClick={() => {
                      setSubset(null);
                      setBundle(null);
                    }}
                  >
                    Reset
                  </button>
                )}
                <span className={subset ? 'pill pill--warn' : 'pill pill--idle'}>
                  {subset ? 'narrowed' : 'project selection'}
                </span>
              </span>
            </div>
            <div className="card__body">
              <p className="field__hint" style={{ marginBottom: 'var(--s3)' }}>
                Narrowing affects this run only. The project's saved selection is
                not changed, so a small test generation costs little and leaves
                nothing behind.
                {viewCount > 0 && (
                  <>
                    {' '}
                    The {viewCount} database views are not listed: they are never built
                    as cubes, and the bundle always includes them as evidence of
                    intended joins.
                  </>
                )}
              </p>
              <div className="picks">
                {available.map((name) => (
                  <label className="pick" key={name}>
                    <input
                      className="checkbox"
                      type="checkbox"
                      checked={effective.has(name)}
                      onChange={() => toggle(name)}
                    />
                    <span className="mono">{name.replace(/^public\./, '')}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="card__foot">
              <button className="btn btn--primary" type="button" disabled={busy} onClick={() => void build()}>
                {busy ? 'Building…' : `Build bundle (${effective.size})`}
              </button>
            </div>
          </section>

          <div className="stack">
            {!bundle ? (
              <section className="card">
                <Empty
                  icon="layers"
                  title="No bundle built"
                  body="Build it to see exactly which facts generation would receive, and where each one came from."
                />
              </section>
            ) : (
              <>
                <div className="stats">
                  <StatTile
                    label="Cubes"
                    icon="grid"
                    value={bundle.counts.tables}
                    /* A database with no views is normal; "0 views as evidence"
                       reads like something is missing. */
                    pill={
                      bundle.counts.views_as_evidence
                        ? {
                            tone: 'violet',
                            text: `${bundle.counts.views_as_evidence} ${
                              bundle.counts.views_as_evidence === 1 ? 'view' : 'views'
                            } as evidence`,
                          }
                        : undefined
                    }
                    note={`${bundle.counts.columns} columns`}
                  />
                  <StatTile
                    label="Joins"
                    icon="layers"
                    value={bundle.counts.relationships}
                    pill={
                      bundle.counts.dangling
                        ? { tone: 'warn', text: `${bundle.counts.dangling} dropped` }
                        : { tone: 'up', text: 'all resolved' }
                    }
                    note="from declared keys"
                  />
                  <StatTile
                    label="Vocabularies"
                    icon="check"
                    value={bundle.counts.vocabularies}
                    pill={{ tone: 'up', text: 'status codes' }}
                    note="recovered from statistics"
                  />
                  <StatTile
                    label="Described"
                    icon="pencil"
                    value={bundle.counts.described}
                    pill={{ tone: 'violet', text: 'by you' }}
                    note="tables with a purpose"
                  />
                </div>

                {bundle.heuristics.length > 0 && (
                  <section className="card">
                    <div className="card__head">
                      <span className="card__label">
                        <Icon name="sparkle" size={13} />
                        Inferred
                      </span>
                    </div>
                    <div className="feed">
                      {bundle.heuristics.map((h) => (
                        <div className="feed__row" key={h.kind + h.column}>
                          <span className="feed__glyph feed__glyph--warn">
                            <Icon name="alert" size={14} />
                          </span>
                          <span className="feed__text">
                            <span className="feed__name mono">
                              {h.kind} · {h.column}
                            </span>
                            <span className="feed__sub">
                              {h.basis}. Treated as: {h.action}.
                            </span>
                          </span>
                          <span className="feed__side">
                            <span className="pill pill--warn">{h.confidence}</span>
                          </span>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                {bundle.dangling_foreign_keys.length > 0 && (
                  <section className="card">
                    <div className="card__head">
                      <span className="card__label">
                        <Icon name="alert" size={13} />
                        Joins dropped by this scope
                      </span>
                      <span className="card__tools">
                        <span className="pill pill--warn">{bundle.dangling_foreign_keys.length}</span>
                      </span>
                    </div>
                    <div className="feed">
                      {bundle.dangling_foreign_keys.map((d, i) => (
                        <div className="feed__row" key={i}>
                          <span className="feed__text">
                            <span className="feed__name mono">
                              {d.child.replace(/^public\./, '')}.{d.columns.join(',')} →{' '}
                              {d.target?.replace(/^public\./, '') ?? '?'}
                            </span>
                            <span className="feed__sub">{d.reason}</span>
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
                      Vocabularies
                    </span>
                    <span className="card__tools">
                      <button className="btn btn--ghost btn--sm" type="button" onClick={download}>
                        <Icon name="download" size={13} />
                        Download bundle
                      </button>
                      <button
                        className="btn btn--ghost btn--sm"
                        type="button"
                        onClick={() => setViewingBundle(true)}
                      >
                        <Icon name="expand" size={13} />
                        View bundle
                      </button>
                    </span>
                  </div>
                  <div className="feed">
                    {bundle.vocabularies.map((v) => (
                      <div className="feed__row" key={v.column}>
                        <span className="feed__text">
                          <span className="feed__name mono">
                            {v.column.replace(/^public\./, '')}
                          </span>
                          <span className="feed__sub mono">{v.values.join(' · ')}</span>
                        </span>
                        <span className="feed__side">
                          <span className={TONE[v.provenance]}>{v.provenance}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
