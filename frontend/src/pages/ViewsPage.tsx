import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { ApiError, api } from '../api';
import { CodeDialog } from '../components/CodeDialog';
import { CodeView } from '../components/CodeView';
import { Empty } from '../components/Empty';
import { Icon } from '../components/Icon';
import { Topbar } from '../components/Topbar';
import { useToast } from '../components/Toast';
import type {
  AvailableCube,
  CubeCatalogue,
  SavedView,
  ViewIndexStatus,
  ViewSearchResult,
} from '../types';
import type { ProjectContext } from './ProjectLayout';

/** Which members are picked, per cube. */
type Picks = Record<string, Set<string>>;

export function ViewsPage() {
  const { project } = useOutletContext<ProjectContext>();
  const navigate = useNavigate();

  const [catalogue, setCatalogue] = useState<CubeCatalogue | null>(null);
  const [saved, setSaved] = useState<SavedView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [picks, setPicks] = useState<Picks>({});
  const [open, setOpen] = useState<string | null>(null);
  const [yaml, setYaml] = useState('');

  /* Retrieval: the index is rebuilt by hand, so its state is shown rather than
   * assumed to match the views above it. */
  const toast = useToast();
  const [index, setIndex] = useState<ViewIndexStatus | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<ViewSearchResult | null>(null);
  /** The saved view being read full screen, as { name, yaml }. */
  const [reading, setReading] = useState<{ name: string; yaml: string } | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([api.cubeCatalogue(project.id), api.listViews(project.id)])
      .then(([cat, views]) => {
        if (!live) return;
        setCatalogue(cat);
        setSaved(views);
      })
      .catch((err) => live && setError(err instanceof ApiError ? err.message : null))
      .finally(() => live && setLoading(false));
    api
      .viewIndexStatus(project.id)
      .then((s) => live && setIndex(s))
      .catch(() => live && setIndex(null));
    return () => {
      live = false;
    };
  }, [project.id]);

  const root = catalogue?.root_cube ?? '';
  const cubes = catalogue?.cubes ?? [];
  const chosen = useMemo(
    () => cubes.filter((c) => (picks[c.name]?.size ?? 0) > 0),
    [cubes, picks],
  );

  /* The preview is rendered by the backend, from the same code that writes the
   * file, so what is shown is what will be saved. */
  useEffect(() => {
    if (!name.trim() || chosen.length === 0) {
      setYaml('');
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      api
        .previewView(project.id, {
          name,
          description,
          root_cube: root,
          members: chosen.map((c) => ({ cube: c.name, includes: [...picks[c.name]] })),
        })
        .then((r) => live && setYaml(r.yaml))
        .catch(() => live && setYaml(''));
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [project.id, name, description, root, chosen, picks]);

  const toggle = (cube: string, member: string) => {
    setPicks((current) => {
      const next = { ...current };
      const set = new Set(next[cube] ?? []);
      if (set.has(member)) set.delete(member);
      else set.add(member);
      if (set.size === 0) delete next[cube];
      else next[cube] = set;
      return next;
    });
  };

  const toggleAll = (cube: AvailableCube) => {
    const all = [...cube.dimensions, ...cube.measures].map((m) => m.name);
    setPicks((current) => {
      const next = { ...current };
      if ((next[cube.name]?.size ?? 0) === all.length) delete next[cube.name];
      else next[cube.name] = new Set(all);
      return next;
    });
  };

  const load = (view: SavedView) => {
    setName(view.name);
    setDescription(view.description);
    setPicks(
      Object.fromEntries(view.members.map((m) => [m.cube, new Set(m.includes)])),
    );
    setOpen(view.members[0]?.cube ?? null);
    setError(null);
  };

  const reset = () => {
    setName('');
    setDescription('');
    setPicks({});
    setYaml('');
    setError(null);
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.saveView(project.id, {
        name,
        description,
        root_cube: root,
        members: chosen.map((c) => ({ cube: c.name, includes: [...picks[c.name]] })),
      });
      setSaved(await api.listViews(project.id));
      // Saving changed what the index should hold; say so immediately.
      refreshIndex();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the view.');
    } finally {
      setBusy(false);
    }
  };

  const refreshIndex = () => {
    api
      .viewIndexStatus(project.id)
      .then(setIndex)
      .catch(() => setIndex(null));
  };

  const rebuildIndex = async () => {
    setIndexing(true);
    try {
      const report = await api.rebuildViewIndex(project.id);
      refreshIndex();
      toast.show({
        tone: 'success',
        title: `Indexed ${report.members} members from ${report.views} views`,
        body: report.undescribed.length
          ? `${report.undescribed.length} have no description and will be hard to find: ${report.undescribed
              .slice(0, 3)
              .join(', ')}${report.undescribed.length > 3 ? '…' : ''}`
          : `Collection ${report.collection}.`,
      });
    } catch (err) {
      toast.show({
        tone: 'error',
        title: 'Could not build the index',
        body: err instanceof ApiError ? err.message : 'Indexing failed.',
      });
    } finally {
      setIndexing(false);
    }
  };

  const ask = async () => {
    if (!question.trim()) return;
    setAsking(true);
    try {
      setAnswer(await api.searchViews(project.id, question.trim()));
    } catch (err) {
      setAnswer(null);
      toast.show({
        tone: 'error',
        title: 'Search failed',
        body: err instanceof ApiError ? err.message : 'Could not search the index.',
      });
    } finally {
      setAsking(false);
    }
  };

  const show = async (view: SavedView) => {
    try {
      const { yaml: text } = await api.previewView(project.id, {
        name: view.name,
        title: view.title,
        description: view.description,
        root_cube: view.root_cube,
        members: view.members,
      });
      setReading({ name: view.name, yaml: text });
    } catch (err) {
      toast.show({
        tone: 'error',
        title: `Could not render ${view.name}`,
        body: err instanceof ApiError ? err.message : 'Rendering failed.',
      });
    }
  };

  const remove = async (view: SavedView) => {
    await api.deleteView(project.id, view.name);
    setSaved(await api.listViews(project.id));
    refreshIndex();
    if (view.name === name) reset();
  };

  const memberCount = chosen.reduce((n, c) => n + (picks[c.name]?.size ?? 0), 0);
  /* Offered by several cubes. Worth flagging, but harmless on its own: only
   * one of them has to contribute it. */
  const ambiguous = new Set(catalogue?.ambiguous_members ?? []);

  /* The real problem: the same name taken from two cubes. A view flattens
   * members into one namespace, so this cannot resolve and Cube fails the
   * whole model, not just this view. */
  const takenFrom = new Map<string, string[]>();
  for (const cube of chosen) {
    for (const member of picks[cube.name] ?? []) {
      takenFrom.set(member, [...(takenFrom.get(member) ?? []), cube.name]);
    }
  }
  const collisions = [...takenFrom.entries()].filter(([, cubes]) => cubes.length > 1);
  const collidingNames = new Set(collisions.map(([name]) => name));

  if (loading) return null;

  if (!catalogue) {
    return (
      <>
        <Topbar title="Views" />
        <div className="page">
          <section className="card">
            <Empty
              icon="sparkle"
              title="Generate the cubes first"
              body="A view selects members from generated cubes along a join path, so there is nothing to assemble until the model exists."
              actions={
                <button
                  className="btn btn--primary"
                  type="button"
                  onClick={() => navigate(`/projects/${project.id}/generate`)}
                >
                  Go to Generate
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
        title="Views"
        actions={
          <>
            {name && (
              <button className="btn btn--ghost" type="button" onClick={reset}>
                <Icon name="plus" size={14} />
                New view
              </button>
            )}
            <button
              className="btn btn--solid"
              type="button"
              disabled={
                busy || !name.trim() || memberCount === 0 || collisions.length > 0
              }
              title={
                collisions.length
                  ? 'Two cubes are contributing the same member name'
                  : undefined
              }
              onClick={() => void save()}
            >
              <Icon name="check" size={14} />
              {busy ? 'Saving…' : 'Save view'}
            </button>
          </>
        }
      />

      {reading && (
        <CodeDialog
          title={`views/${reading.name}.yml`}
          filename={`${reading.name}.yml`}
          value={reading.yaml}
          note="Rendered from the saved view. This is what Cube compiles."
          onClose={() => setReading(null)}
        />
      )}

      <div className="page stack">
        {error && <div className="banner banner--error">{error}</div>}

        {saved.length > 0 && (
          <section className="card">
            <div className="card__head">
              <span className="card__label">
                <Icon name="layers" size={13} />
                Saved views
              </span>
              <span className="card__tools">
                <span className="pill pill--idle">{saved.length}</span>
                {!index?.reachable && index?.configured !== false && (
                  <span className="pill pill--down">Qdrant unreachable</span>
                )}
                {index?.reachable && index.count === 0 && (
                  <span className="pill pill--warn">not indexed</span>
                )}
                {index?.reachable && index.count > 0 && (
                  <span
                    className={`pill ${index.stale ? 'pill--warn' : 'pill--up'}`}
                    title={
                      index.stale
                        ? [
                            index.added ? `${index.added} new` : '',
                            index.removed?.length
                              ? `removed: ${index.removed.join(', ')}`
                              : '',
                            index.changed?.length
                              ? `changed: ${index.changed.join(', ')}`
                              : '',
                          ]
                            .filter(Boolean)
                            .join(' · ')
                        : `Indexed ${
                            index.indexed_at
                              ? new Date(index.indexed_at).toLocaleString()
                              : ''
                          }`
                    }
                  >
                    {index.stale
                      ? `index out of date (${index.count}/${index.expected})`
                      : `${index.count} indexed`}
                  </span>
                )}
                <button
                  className={`btn btn--sm ${
                    index?.stale || (index?.reachable && index.count === 0)
                      ? 'btn--solid'
                      : 'btn--ghost'
                  }`}
                  type="button"
                  disabled={indexing || index?.configured === false}
                  title={
                    index?.configured === false
                      ? 'Set CUBEGEN_OPENAI_API_KEY and CUBEGEN_QDRANT_URL'
                      : 'Embed every view member into Qdrant'
                  }
                  onClick={() => void rebuildIndex()}
                >
                  <Icon name="refresh" size={13} />
                  {indexing ? 'Indexing…' : 'Sync index'}
                </button>
              </span>
            </div>
            <div className="feed">
              {saved.map((view) => (
                <div className="feed__row" key={view.id}>
                  <span className="feed__glyph">
                    <Icon name="grid" size={14} />
                  </span>
                  <span className="feed__text">
                    <span className="feed__name">{view.name}</span>
                    <span className="feed__sub">
                      {view.members.length} cubes ·{' '}
                      {view.members.reduce((n, m) => n + m.includes.length, 0)} members
                    </span>
                  </span>
                  <span className="card__tools">
                    <button
                      className="btn btn--ghost btn--sm"
                      type="button"
                      title="Read the YAML Cube compiles for this view"
                      onClick={() => void show(view)}
                    >
                      <Icon name="expand" size={13} />
                      View YAML
                    </button>
                    <button
                      className="btn btn--ghost btn--sm"
                      type="button"
                      onClick={() => load(view)}
                    >
                      <Icon name="pencil" size={13} />
                      Edit
                    </button>
                    <button
                      className="btn btn--ghost btn--sm"
                      type="button"
                      onClick={() => void remove(view)}
                    >
                      Delete
                    </button>
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {(index?.count ?? 0) > 0 && (
          <section className="card">
            <div className="card__head">
              <span className="card__label">
                <Icon name="search" size={13} />
                Ask
              </span>
              <span className="card__tools">
                <span className="pill pill--idle">
                  searching {index?.count} members
                </span>
              </span>
            </div>
            <div className="card__body">
              {index?.stale && (
                <div
                  className="banner banner--warn"
                  style={{ marginBottom: 'var(--s3)' }}
                >
                  The index no longer matches the views
                  {index.added ? `, ${index.added} member(s) are missing from it` : ''}
                  {index.removed?.length
                    ? `, and it still holds ${index.removed.length} that no longer exist`
                    : ''}
                  . Results below are from the last sync.
                </div>
              )}
              <div className="ask">
                <input
                  className="field__input"
                  value={question}
                  placeholder="How much did insurers approve per case status?"
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void ask();
                  }}
                />
                <button
                  className="btn btn--solid"
                  type="button"
                  disabled={asking || !question.trim()}
                  onClick={() => void ask()}
                >
                  <Icon name="search" size={14} />
                  {asking ? 'Searching…' : 'Search'}
                </button>
              </div>
              <p className="field__hint" style={{ marginTop: 'var(--s2)' }}>
                Matches the question against every member of every indexed view.
                This is the retrieval step only — nothing is queried yet.
              </p>
            </div>

            {answer && (
              <div className="feed">
                {answer.views.length === 0 && (
                  <div className="feed__row">
                    <span className="feed__text">
                      <span className="feed__name">No match</span>
                      <span className="feed__sub">
                        Nothing in the index is close to that question.
                      </span>
                    </span>
                  </div>
                )}
                {answer.views.map((v) => (
                  <div className="feed__row" key={v.view}>
                    <span className="feed__glyph">
                      <Icon name="grid" size={14} />
                    </span>
                    <span className="feed__text">
                      <span className="feed__name mono">{v.view}</span>
                      {v.members.map((m) => (
                        <span className="feed__sub" key={m.name}>
                          <span className="num">{m.score.toFixed(3)}</span>{' '}
                          <span className="mono">{m.name}</span> ({m.type}
                          {m.cube ? ` · ${m.cube}` : ''}){' '}
                          {m.description && `— ${m.description}`}
                        </span>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        <div className="grid grid--split">
          <section className="card sticky-col">
            <div className="card__head">
              <span className="card__label">
                <Icon name="pencil" size={13} />
                The view
              </span>
              <span className="card__tools">
                <span className="pill pill--violet">root · {root}</span>
              </span>
            </div>
            <div className="card__body stack">
              <label className="field">
                <span className="field__label">View name</span>
                <input
                  className="field__input field__input--mono"
                  value={name}
                  placeholder="revenue_lifecycle"
                  onChange={(e) => setName(e.target.value)}
                />
                <span className="field__hint">
                  Becomes <code>views/{name || 'name'}.yml</code> and the name analysts
                  query. Lowercase, underscores.
                </span>
              </label>

              <label className="field">
                <span className="field__label">Description</span>
                <textarea
                  className="field__textarea"
                  rows={3}
                  value={description}
                  placeholder="What question this view answers, and for whom."
                  onChange={(e) => setDescription(e.target.value)}
                />
                <span className="field__hint">
                  Read by AI agents through Cube's Meta API, so it is worth writing.
                </span>
              </label>

              {chosen.length > 0 && (
                <p className="field__hint">
                  {chosen.length} cubes · {memberCount} members.
                </p>
              )}

              {collisions.length > 0 && (
                <div className="banner banner--error">
                  {collisions.length === 1
                    ? `"${collisions[0][0]}" is being taken from ${collisions[0][1].join(' and ')}.`
                    : `${collisions.length} member names are being taken from more than one cube: ${collisions
                        .map(([n]) => n)
                        .slice(0, 4)
                        .join(', ')}${collisions.length > 4 ? '…' : ''}.`}{' '}
                  A view flattens members into one namespace, so this cannot be
                  saved. Drop one side, or rename it in its cube first.
                </div>
              )}
            </div>
          </section>

          <section className="card card--clip">
            <div className="card__head">
              <span className="card__label">
                <Icon name="grid" size={13} />
                Cubes and members
              </span>
            </div>
            <div className="steps">
              {cubes.map((cube) => {
                const picked = picks[cube.name]?.size ?? 0;
                const total = cube.dimensions.length + cube.measures.length;
                const expanded = open === cube.name;
                return (
                  <div key={cube.name}>
                    <button
                      type="button"
                      className="step"
                      disabled={!cube.reachable}
                      title={
                        cube.reachable
                          ? cube.join_path ?? ''
                          : `No join path from ${root}, so a view rooted there cannot reach it`
                      }
                      onClick={() => setOpen(expanded ? null : cube.name)}
                    >
                      <span
                        className={`step__n ${
                          picked ? 'step__n--done' : 'step__n--empty'
                        }`}
                      >
                        {picked || ''}
                      </span>
                      <span className="step__text">
                        <span className="feed__name mono">{cube.name}</span>
                        <span className="feed__sub">
                          {cube.reachable
                            ? `${cube.join_path} · ${total} members`
                            : 'unreachable — no join connects it'}
                        </span>
                      </span>
                      {cube.reachable && <Icon name="chevron" size={14} />}
                    </button>

                    {expanded && cube.reachable && (
                      <div className="members">
                        <button
                          type="button"
                          className="btn btn--quiet btn--sm"
                          onClick={() => toggleAll(cube)}
                        >
                          {picked === total ? 'Clear all' : 'Select all'}
                        </button>
                        {[
                          ['Dimensions', cube.dimensions],
                          ['Measures', cube.measures],
                        ].map(([label, list]) => (
                          <div key={label as string}>
                            <div className="members__group">{label as string}</div>
                            <div className="picks">
                              {(list as typeof cube.dimensions).map((m) => (
                                <label
                                  className={`pick${
                                    collidingNames.has(m.name) ? ' pick--clash' : ''
                                  }`}
                                  key={m.name}
                                >
                                  <input
                                    className="checkbox"
                                    type="checkbox"
                                    checked={picks[cube.name]?.has(m.name) ?? false}
                                    /* Taking it from a second cube is what breaks
                                       the view, so the first one stays tickable
                                       and the rest are refused. */
                                    disabled={
                                      !picks[cube.name]?.has(m.name) &&
                                      (takenFrom.get(m.name)?.length ?? 0) > 0
                                    }
                                    title={
                                      !picks[cube.name]?.has(m.name) &&
                                      takenFrom.get(m.name)?.length
                                        ? `Already taken from ${takenFrom
                                            .get(m.name)
                                            ?.join(', ')}`
                                        : undefined
                                    }
                                    onChange={() => toggle(cube.name, m.name)}
                                  />
                                  <span className="mono">{m.name}</span>
                                  {collidingNames.has(m.name) ? (
                                    <span className="shared shared--clash">conflict</span>
                                  ) : (
                                    !picks[cube.name]?.has(m.name) &&
                                    takenFrom.get(m.name)?.length ? (
                                      <span className="shared">
                                        in {takenFrom.get(m.name)?.[0]}
                                      </span>
                                    ) : (
                                      ambiguous.has(m.name) && (
                                        <span className="shared">shared</span>
                                      )
                                    )
                                  )}
                                </label>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        </div>

        {yaml && (
          <section className="card card--code">
            <div className="card__head">
              <span className="card__label">
                <Icon name="file" size={13} />
                views/{name}.yml
              </span>
              <span className="card__tools">
                <span className="pill pill--idle">preview</span>
              </span>
            </div>
            <div className="yaml yaml--single">
              <div className="yaml__body">
                <CodeView value={yaml} />
              </div>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
