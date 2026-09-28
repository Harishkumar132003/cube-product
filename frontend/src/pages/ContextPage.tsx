import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { ApiError, api } from '../api';
import { Icon } from '../components/Icon';
import { Topbar } from '../components/Topbar';
import { rankTables } from '../lib/importance';
import { useApp } from '../state';
import type { Scan, ScanTable } from '../types';
import type { ProjectContext } from './ProjectLayout';

/** What helps most, roughly in order of how much it changes the output. */
const PROMPTS: [string, string][] = [
  ['What the business does', 'One paragraph on the business and how work flows through it.'],
  ['What the main tables are for', 'Especially any whose name does not give it away.'],
  [
    'Code meanings',
    'The highest-value thing you can tell us: status = 3 means shipped, type = "PA" means pre-auth. The schema cannot tell us this.',
  ],
  ['Metric definitions', 'Revenue is paid claims minus refunds. Which date decides when something counts.'],
  ['Relationships the schema does not declare', 'owner_id points at users, not admins.'],
  ['Tables to ignore', 'Audit logs, scratch tables, anything dead.'],
];

const MAX = 60000;

export function ContextPage() {
  const { project, reload } = useOutletContext<ProjectContext>();
  const { refresh } = useApp();

  const [text, setText] = useState(project.business_context ?? '');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scan, setScan] = useState<Scan | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});

  useEffect(() => {
    setText(project.business_context ?? '');
  }, [project.business_context]);

  useEffect(() => {
    api
      .getScan(project.id)
      .then(setScan)
      .catch(() => setScan(null));
  }, [project.id]);

  useEffect(() => {
    const stored = project.table_notes ?? {};
    setNotes(
      Object.fromEntries(Object.entries(stored).map(([k, v]) => [k, v.purpose])),
    );
  }, [project.table_notes]);

  /** Persist on blur: one write per table the user actually touched. */
  const saveNote = async (qualified: string, purpose: string) => {
    const previous = project.table_notes?.[qualified]?.purpose ?? '';
    if (purpose.trim() === previous.trim()) return;
    const payload = Object.fromEntries(
      Object.entries({ ...notes, [qualified]: purpose })
        .filter(([, v]) => v.trim())
        .map(([k, v]) => [k, { purpose: v.trim() }]),
    );
    try {
      await api.setTableNotes(project.id, payload);
      await reload();
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the description.');
    }
  };

  const dirty = text !== (project.business_context ?? '');

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.setContext(project.id, text);
      await reload();
      await refresh();
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save.');
    } finally {
      setSaving(false);
    }
  };

  const selected = new Set(
    project.selected_tables?.length
      ? project.selected_tables
      : (scan?.catalog.tables ?? []).map((t) => t.qualified_name),
  );
  const ranked = rankTables(
    (scan?.catalog.tables ?? []).filter((t) => selected.has(t.qualified_name)),
  );
  const described = ranked.filter((r) => (notes[r.table.qualified_name] ?? '').trim()).length;

  const savedAt = project.business_context_updated_at
    ? new Date(project.business_context_updated_at).toLocaleString()
    : null;

  return (
    <>
      <Topbar
        title="Business context"
        actions={
          <>
            {dirty && <span className="pill pill--warn">Unsaved</span>}
            {!dirty && saved && <span className="pill pill--up">Saved</span>}
            <button
              className="btn btn--primary"
              type="button"
              disabled={saving || !dirty}
              onClick={() => void save()}
            >
              {saving ? 'Saving…' : 'Save context'}
            </button>
          </>
        }
      />

      <div className="page">
        <div className="grid grid--main">
          <section className="card">
            <div className="card__head">
              <span className="card__label">
                <Icon name="pencil" size={13} />
                What this database is for
              </span>
              <span className="card__tools">
                <span className="pill pill--idle">
                  {text.length.toLocaleString()} / {MAX.toLocaleString()}
                </span>
              </span>
            </div>
            <div className="card__body">
              {error && <div className="banner banner--error" style={{ marginBottom: 'var(--s3)' }}>{error}</div>}

              <textarea
                className="field__textarea context__area"
                value={text}
                maxLength={MAX}
                spellCheck
                placeholder={
                  'We run pre-authorisation and billing for hospitals.\n\n' +
                  'A hospitalization is created when a patient is admitted. One pre_auth is raised ' +
                  'against it and goes through approval before treatment starts. claim_bill_item ' +
                  'holds the line items we bill.\n\n' +
                  'pre_auth.status: 1 draft, 2 submitted, 3 approved, 4 rejected.\n' +
                  'A claim only counts as settled once settlements.paid_at is set.\n\n' +
                  'Ignore execution_logs and ai_chat_message — they are internal.'
                }
                onChange={(e) => {
                  setText(e.target.value);
                  setSaved(false);
                }}
              />

              <p className="field__hint" style={{ marginTop: 'var(--s3)' }}>
                Optional, and you can come back to it. Anything you write here is
                sent to the model provider when generation runs, so leave out
                anything that should not go there.
              </p>
            </div>
            <div className="card__foot">
              <button
                className="btn btn--primary"
                type="button"
                disabled={saving || !dirty}
                onClick={() => void save()}
              >
                {saving ? 'Saving…' : 'Save context'}
              </button>
              {savedAt && <span className="field__hint">Last saved {savedAt}</span>}
            </div>
          </section>

          <section className="card">
            <div className="card__head">
              <span className="card__label">
                <Icon name="sparkle" size={13} />
                What helps most
              </span>
            </div>
            <div className="feed">
              {PROMPTS.map(([title, note]) => (
                <div className="feed__row" key={title}>
                  <span className="feed__text">
                    <span className="feed__name">{title}</span>
                    <span className="feed__sub">{note}</span>
                  </span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className="card" style={{ marginTop: 'var(--s4)' }}>
          <div className="card__head">
            <span className="card__label">
              <Icon name="database" size={13} />
              What each table is for
            </span>
            <span className="card__tools">
              {scan ? (
                <span className="pill pill--idle">
                  {described} of {ranked.length} described
                </span>
              ) : (
                <span className="pill pill--idle">needs a scan</span>
              )}
            </span>
          </div>

          {!scan ? (
            <div className="empty">
              <div className="empty__glyph">
                <Icon name="scan" size={18} />
              </div>
              <div className="empty__title">Nothing to describe yet</div>
              <p className="empty__body">
                Run a scan on the Tables page first. Until the catalog is read,
                the app does not know which tables exist.
              </p>
            </div>
          ) : (
            <div className="purposes">
              {ranked.map(({ table, referencedBy }) => (
                <PurposeRow
                  key={table.qualified_name}
                  table={table}
                  referencedBy={referencedBy}
                  value={notes[table.qualified_name] ?? ''}
                  onChange={(v) =>
                    setNotes((prev) => ({ ...prev, [table.qualified_name]: v }))
                  }
                  onBlur={(v) => void saveNote(table.qualified_name, v)}
                />
              ))}
            </div>
          )}
        </section>
      </div>
    </>
  );
}

/**
 * One table, with the evidence needed to answer without leaving the page:
 * how many relations point at it, its size, and its first few columns.
 */
function PurposeRow({
  table,
  referencedBy,
  value,
  onChange,
  onBlur,
}: {
  table: ScanTable;
  referencedBy: number;
  value: string;
  onChange: (v: string) => void;
  onBlur: (v: string) => void;
}) {
  return (
    <div className="purpose">
      <div className="purpose__about">
        <div className="purpose__name mono">{table.name}</div>
        <div className="purpose__facts">
          {table.rows === null ? 'rows unknown' : `${table.rows.toLocaleString()} rows`}
          {' · '}
          {table.columns.length} cols
          {referencedBy > 0 && ` · referenced by ${referencedBy}`}
          {table.kind !== 'table' && ` · ${table.kind.replace('_', ' ')}`}
        </div>
        <div className="purpose__cols mono">
          {table.columns.slice(0, 6).map((c) => c.name).join(', ')}
          {table.columns.length > 6 ? '…' : ''}
        </div>
      </div>
      <input
        className="field__input purpose__input"
        value={value}
        placeholder="What is this table for?"
        onChange={(e) => onChange(e.target.value)}
        onBlur={(e) => onBlur(e.target.value)}
      />
    </div>
  );
}
