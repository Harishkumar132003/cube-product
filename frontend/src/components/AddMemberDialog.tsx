import { useEffect, useState } from 'react';
import { Icon } from './Icon';

export type MemberKind = 'dimension' | 'measure' | 'segment';

export interface NewMember {
  kind: MemberKind;
  name: string;
  title: string;
  type?: string;
  sql?: string;
  description: string;
  filter?: string;
  primary_key?: boolean;
  public?: boolean;
}

interface Props {
  cube: string;
  /** Names already used in this cube, across all three kinds. */
  taken: Set<string>;
  onCancel: () => void;
  /** Resolves with blocking errors and advisory warnings; empty means added. */
  onDone: (member: NewMember, acknowledged: boolean) => Promise<{
    errors: string[];
    warnings: string[];
  }>;
}

const DIMENSION_TYPES = ['string', 'number', 'time', 'boolean'];
const MEASURE_TYPES = ['count', 'count_distinct', 'sum', 'avg', 'min', 'max', 'number'];
const NAME = /^[a-z][a-z0-9_]*$/;

function titleFor(name: string): string {
  return name
    .split('_')
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ');
}

/** One popup for all three kinds. The fields differ, the flow does not. */
export function AddMemberDialog({ cube, taken, onCancel, onDone }: Props) {
  const [kind, setKind] = useState<MemberKind>('dimension');
  const [name, setName] = useState('');
  const [title, setTitle] = useState('');
  const [titleTouched, setTitleTouched] = useState(false);
  const [type, setType] = useState('string');
  const [sql, setSql] = useState('');
  const [description, setDescription] = useState('');
  const [filter, setFilter] = useState('');
  const [primaryKey, setPrimaryKey] = useState(false);
  const [hidden, setHidden] = useState(false);

  const [errors, setErrors] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  /* Warnings are shown once; clicking again is the acknowledgement. Any edit
   * to the fields resets it, so a warning is never waved through for a value
   * it was not raised about. */
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setType(kind === 'measure' ? 'count' : 'string');
  }, [kind]);

  useEffect(() => {
    if (!titleTouched) setTitle(titleFor(name));
  }, [name, titleTouched]);

  useEffect(() => {
    setErrors([]);
    setWarnings([]);
    setAcknowledged(false);
  }, [kind, name, type, sql, filter]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const needsSql = !(kind === 'measure' && type === 'count');

  /* The checks that need nothing but the form run here, instantly; the ones
   * that need the table run on the server. */
  const local = (): string[] => {
    const found: string[] = [];
    if (!name) found.push('Give it a name.');
    else if (!NAME.test(name))
      found.push('Names start with a letter and use lowercase letters, numbers and underscores.');
    else if (taken.has(name))
      found.push(`\`${name}\` is already used in ${cube}. Dimensions, measures and segments share one set of names.`);
    if (needsSql && !sql.trim()) found.push('SQL is required.');
    if (!description.trim())
      found.push('Add a description. AI agents read it through the Meta API to decide what this is.');
    return found;
  };

  const submit = async () => {
    const blocking = local();
    if (blocking.length) {
      setErrors(blocking);
      return;
    }
    setBusy(true);
    try {
      const result = await onDone(
        {
          kind,
          name,
          title: title.trim() || titleFor(name),
          type: kind === 'segment' ? undefined : type,
          sql: needsSql ? sql.trim() : undefined,
          description: description.trim(),
          filter: kind === 'measure' && filter.trim() ? filter.trim() : undefined,
          primary_key: kind === 'dimension' && primaryKey ? true : undefined,
          public: kind === 'dimension' && hidden ? false : undefined,
        },
        acknowledged,
      );
      setErrors(result.errors);
      setWarnings(result.warnings);
      if (result.warnings.length && !result.errors.length) setAcknowledged(true);
    } finally {
      setBusy(false);
    }
  };

  const types = kind === 'measure' ? MEASURE_TYPES : DIMENSION_TYPES;

  return (
    <div
      className="scrim"
      role="dialog"
      aria-modal="true"
      aria-label={`Add a ${kind}`}
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div className="modal modal--form">
        <div className="modal__head">
          <span className="card__label">
            <Icon name="plus" size={13} />
            Add to {cube}
          </span>
        </div>

        <div className="segmented" role="tablist" aria-label="What to add">
          {(['dimension', 'measure', 'segment'] as const).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={kind === k}
              className={`segmented__opt${kind === k ? ' is-on' : ''}`}
              onClick={() => setKind(k)}
            >
              {k[0].toUpperCase() + k.slice(1)}
            </button>
          ))}
        </div>

        <div className="modal__body">
          <div className="field-row field-row--2">
            <label className="field">
              <span className="field__label">Name</span>
              <input
                className="field__input field__input--mono"
                value={name}
                autoFocus
                placeholder={kind === 'measure' ? 'sum_claimed_amount' : 'claim_status'}
                onChange={(e) => setName(e.target.value.trim())}
              />
            </label>
            <label className="field">
              <span className="field__label">Title</span>
              <input
                className="field__input"
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  setTitleTouched(true);
                }}
              />
            </label>
          </div>

          {kind !== 'segment' && (
            <label className="field">
              <span className="field__label">Type</span>
              <select
                className="field__select"
                value={type}
                onChange={(e) => setType(e.target.value)}
              >
                {types.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
          )}

          {needsSql && (
            <label className="field">
              <span className="field__label">SQL</span>
              <input
                className="field__input field__input--mono"
                value={sql}
                placeholder={
                  kind === 'segment'
                    ? "{CUBE}.status = 'CLAIM_APPROVED'"
                    : 'approved_amount'
                }
                onChange={(e) => setSql(e.target.value)}
              />
              <span className="field__hint">
                {kind === 'segment'
                  ? 'A condition. Use {CUBE}.column to refer to this cube.'
                  : 'A column name is checked against the table. An expression is written as-is.'}
              </span>
            </label>
          )}

          {kind === 'measure' && (
            <label className="field">
              <span className="field__label">Filter (optional)</span>
              <input
                className="field__input field__input--mono"
                value={filter}
                placeholder="{CUBE}.status = 'CLAIM_APPROVED'"
                onChange={(e) => setFilter(e.target.value)}
              />
              <span className="field__hint">
                Counts or sums only the rows matching this condition.
              </span>
            </label>
          )}

          <label className="field">
            <span className="field__label">Description</span>
            <textarea
              className="field__textarea"
              rows={2}
              value={description}
              placeholder="What the number is, its unit, and what it is not."
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>

          {kind === 'dimension' && (
            <div className="checks">
              <label className="pick">
                <input
                  className="checkbox"
                  type="checkbox"
                  checked={primaryKey}
                  onChange={(e) => setPrimaryKey(e.target.checked)}
                />
                Primary key
              </label>
              <label className="pick">
                <input
                  className="checkbox"
                  type="checkbox"
                  checked={hidden}
                  onChange={(e) => setHidden(e.target.checked)}
                />
                Hidden from the API
              </label>
            </div>
          )}

          {errors.length > 0 && (
            <div className="banner banner--error">
              {errors.map((e) => (
                <div key={e}>{e}</div>
              ))}
            </div>
          )}
          {warnings.length > 0 && errors.length === 0 && (
            <div className="banner banner--warn">
              {warnings.map((w) => (
                <div key={w}>{w}</div>
              ))}
            </div>
          )}
        </div>

        <div className="modal__foot">
          <span className="field__hint">
            Checked against the table before anything is written.
          </span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--solid btn--sm"
            disabled={busy}
            onClick={() => void submit()}
          >
            <Icon name="tick" size={13} />
            {busy ? 'Checking…' : acknowledged ? 'Add anyway' : 'Done'}
          </button>
        </div>
      </div>
    </div>
  );
}
