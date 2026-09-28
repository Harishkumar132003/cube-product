import { useEffect, useMemo, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { ApiError, api } from '../api';
import { Icon } from '../components/Icon';
import { useToast } from '../components/Toast';
import { Topbar } from '../components/Topbar';
import type { ProjectPrompt } from '../types';
import type { ProjectContext } from './ProjectLayout';

/** Mirrors prompts.placeholders() on the backend: `{{` escapes a literal brace. */
function used(body: string): Set<string> {
  const stripped = body.replaceAll('{{', '').replaceAll('}}', '');
  return new Set([...stripped.matchAll(/\{([a-z_]+)\}/g)].map((m) => m[1]));
}

export function PromptsPage() {
  const { project } = useOutletContext<ProjectContext>();
  const toast = useToast();

  const [prompts, setPrompts] = useState<ProjectPrompt[]>([]);
  const [active, setActive] = useState<string | null>(null);
  /** Edits held per key until saved, so switching prompts keeps the draft. */
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const next = await api.listPrompts(project.id);
      setPrompts(next);
      setActive((current) => current ?? next[0]?.key ?? null);
    } catch {
      setPrompts([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  const current = prompts.find((p) => p.key === active) ?? null;
  const body = current ? drafts[current.key] ?? current.body : '';
  const dirty = current ? body !== current.body : false;

  /* The same checks the server runs, run as you type -- a required token is
   * easy to delete by accident and finding out on Save is too late. */
  const check = useMemo(() => {
    if (!current) return { missing: [] as string[], unknown: [] as string[], empty: false };
    const present = used(body);
    return {
      missing: current.required.filter((r) => !present.has(r)),
      unknown: [...present].filter((p) => !current.allowed.includes(p)),
      empty: !body.trim(),
    };
  }, [current, body]);

  const blocked = check.missing.length > 0 || check.unknown.length > 0 || check.empty;

  const save = async () => {
    if (!current) return;
    setBusy(true);
    try {
      const result = await api.savePrompt(project.id, current.key, body);
      setDrafts((d) => {
        const next = { ...d };
        delete next[current.key];
        return next;
      });
      setPreview(null);
      await load();
      toast.show({
        tone: 'success',
        title: result.customised ? `${current.label} saved` : `${current.label} back to default`,
        body: result.customised
          ? 'This project now uses your wording.'
          : 'It matched the default, so the override was dropped.',
      });
    } catch (err) {
      toast.show({
        tone: 'error',
        title: `Cannot save ${current.label}`,
        body: err instanceof ApiError ? err.message : 'Saving failed.',
      });
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!current) return;
    setBusy(true);
    try {
      await api.resetPrompt(project.id, current.key);
      setDrafts((d) => {
        const next = { ...d };
        delete next[current.key];
        return next;
      });
      setPreview(null);
      await load();
      toast.show({ tone: 'success', title: `${current.label} reset to the default` });
    } finally {
      setBusy(false);
    }
  };

  const showPreview = async () => {
    if (!current) return;
    if (preview) return setPreview(null);
    try {
      const { text } = await api.previewPrompt(project.id, current.key);
      setPreview(text);
    } catch (err) {
      toast.show({
        tone: 'error',
        title: 'Could not build the preview',
        body: err instanceof ApiError ? err.message : 'Preview failed.',
      });
    }
  };

  if (loading) return null;

  const customised = prompts.filter((p) => p.customised).length;

  return (
    <>
      <Topbar
        title="Prompts"
        actions={
          <span className={customised ? 'pill pill--violet' : 'pill pill--idle'}>
            {customised ? `${customised} customised` : 'all default'}
          </span>
        }
      />

      <div className="page stack">
        <div className="banner banner--info">
          These decide what a question means before any data is read. Each falls back
          to a shared default — one you have not edited keeps improving as the
          defaults do, so change only what you need to.
        </div>

        <div className="grid grid--split">
          {/* Numbered, because the order is the order the flow runs them. */}
          <section className="card sticky-col card--clip">
            <div className="card__head">
              <span className="card__label">
                <Icon name="layers" size={13} />
                The flow
              </span>
            </div>
            <div className="promptlist">
              {prompts.map((p, i) => (
                <button
                  type="button"
                  key={p.key}
                  className={`promptlist__row${p.key === active ? ' is-active' : ''}`}
                  onClick={() => {
                    setActive(p.key);
                    setPreview(null);
                  }}
                >
                  <span className="promptlist__n">{i + 1}</span>
                  <span className="promptlist__text">
                    <span className="promptlist__name">
                      {p.label}
                      {drafts[p.key] !== undefined && drafts[p.key] !== p.body && (
                        <span className="yaml__dot" title="Unsaved changes" />
                      )}
                    </span>
                    <span className="promptlist__meta">
                      {p.required.length > 0 && (
                        <span className="req">{p.required.length} required</span>
                      )}
                      {p.tags.includes('safety') && <span className="tagx">safety</span>}
                      {!p.is_llm && <span className="tagx">no model</span>}
                      {p.customised && <span className="tagx tagx--on">customised</span>}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </section>

          {current && (
            <section className="card">
              <div className="card__head">
                <span className="card__label">
                  <Icon name="pencil" size={13} />
                  {current.label}
                </span>
                <span className="card__tools">
                  <span className="pill pill--idle num">{body.length} chars</span>
                  {current.customised && <span className="pill pill--violet">customised</span>}
                </span>
              </div>

              <div className="card__body">
                <p className="lede">{current.purpose}</p>

                {current.allowed.length > 0 && (
                  <div className="vars">
                    <div className="vars__head">
                      Variables
                      <span className="vars__hint">
                        filled in with this project's data when the prompt runs
                      </span>
                    </div>
                    <div className="vars__row">
                      {current.allowed.map((a) => {
                        const isRequired = current.required.includes(a);
                        const missing = check.missing.includes(a);
                        return (
                          <span
                            key={a}
                            className={`var${isRequired ? ' var--required' : ''}${
                              missing ? ' var--missing' : ''
                            }`}
                            title={
                              missing
                                ? 'Required, and not in the prompt above'
                                : isRequired
                                  ? 'Required — the prompt cannot be saved without it'
                                  : 'Optional'
                            }
                          >
                            <span className="var__token">{`{${a}}`}</span>
                            <span className="var__role">
                              {missing ? 'missing' : isRequired ? 'required' : 'in use'}
                            </span>
                          </span>
                        );
                      })}
                    </div>
                  </div>
                )}

                {check.missing.length > 0 && (
                  <div className="banner banner--error">
                    {check.missing.map((m) => `{${m}}`).join(', ')}{' '}
                    {check.missing.length === 1 ? 'is required and is' : 'are required and are'}{' '}
                    not in the prompt. Without it the model is never given that
                    information, so this cannot be saved.
                  </div>
                )}
                {check.unknown.length > 0 && (
                  <div className="banner banner--warn">
                    {check.unknown.map((u) => `{${u}}`).join(', ')} will never be filled
                    in and will reach the model as literal text. Write{' '}
                    <code>{'{{'}</code> to keep a brace as a brace.
                  </div>
                )}

                <textarea
                  className="field__textarea prompt__area"
                  rows={18}
                  spellCheck={false}
                  value={body}
                  onChange={(e) =>
                    setDrafts((d) => ({ ...d, [current.key]: e.target.value }))
                  }
                />

                {preview && (
                  <>
                    <div className="field__label" style={{ marginTop: 'var(--s3)' }}>
                      What the model actually receives
                    </div>
                    <pre className="preview">{preview}</pre>
                  </>
                )}
              </div>

              <div className="card__foot">
                <span className="field__hint">
                  {dirty ? 'Unsaved changes.' : current.customised
                    ? 'Using your wording.'
                    : 'Using the default.'}
                </span>
                <button className="btn btn--ghost btn--sm" type="button" onClick={() => void showPreview()}>
                  <Icon name="search" size={13} />
                  {preview ? 'Hide preview' : 'Preview'}
                </button>
                <button
                  className="btn btn--ghost btn--sm"
                  type="button"
                  disabled={!current.customised || busy}
                  title={current.customised ? 'Go back to the default' : 'Already the default'}
                  onClick={() => void reset()}
                >
                  <Icon name="refresh" size={13} />
                  Reset
                </button>
                <button
                  className="btn btn--solid btn--sm"
                  type="button"
                  disabled={!dirty || blocked || busy}
                  title={blocked ? 'Fix the problems above first' : undefined}
                  onClick={() => void save()}
                >
                  <Icon name="tick" size={13} />
                  {busy ? 'Saving…' : 'Save'}
                </button>
              </div>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
