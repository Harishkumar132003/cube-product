import { useState } from 'react';
import type { ConnectionInput, SslMode } from '../types';

const SSL_MODES: SslMode[] = [
  'disable',
  'allow',
  'prefer',
  'require',
  'verify-ca',
  'verify-full',
];

interface Fields {
  host: string;
  port: number;
  database: string;
  user: string;
  password: string;
  sslmode: SslMode;
}

const EMPTY_FIELDS: Fields = {
  host: 'localhost',
  port: 5432,
  database: '',
  user: '',
  password: '',
  sslmode: 'prefer',
};

interface Props {
  projectName: string;
  busy: boolean;
  canSave: boolean;
  onTest: (input: ConnectionInput) => void;
  onSave: (input: ConnectionInput) => void;
}

/** A project carries one connection, given either as a string or as fields. */
export function ConnectionForm({
  projectName,
  busy,
  canSave,
  onTest,
  onSave,
}: Props) {
  const [mode, setMode] = useState<'dsn' | 'fields'>('dsn');
  const [dsn, setDsn] = useState('');
  const [fields, setFields] = useState<Fields>(EMPTY_FIELDS);

  const set = <K extends keyof Fields>(key: K, value: Fields[K]) =>
    setFields((prev) => ({ ...prev, [key]: value }));

  const input: ConnectionInput =
    mode === 'dsn' ? { dsn: dsn.trim() } : { ...fields };

  const complete =
    mode === 'dsn'
      ? dsn.trim() !== ''
      : fields.host.trim() !== '' &&
        fields.database.trim() !== '' &&
        fields.user.trim() !== '';

  return (
    <form
      className="card"
      onSubmit={(event) => {
        event.preventDefault();
        onTest(input);
      }}
    >
      <div className="card__head">
        <div>
          <h2 className="card__title">Connect {projectName}</h2>
          <p className="card__note">
            Read-only. Prefer a replica and a role with no write grants.
          </p>
        </div>
      </div>

      <div className="card__body">
        <div className="segmented">
          <button
            type="button"
            className={mode === 'dsn' ? 'segmented__opt is-on' : 'segmented__opt'}
            onClick={() => setMode('dsn')}
          >
            Connection string
          </button>
          <button
            type="button"
            className={
              mode === 'fields' ? 'segmented__opt is-on' : 'segmented__opt'
            }
            onClick={() => setMode('fields')}
          >
            Fields
          </button>
        </div>

        {mode === 'dsn' ? (
          <div className="field">
            <label className="field__label" htmlFor="dsn">
              Postgres connection string
            </label>
            <textarea
              id="dsn"
              className="field__textarea"
              rows={3}
              spellCheck={false}
              autoComplete="off"
              value={dsn}
              placeholder="postgresql://reader:••••@db.internal:5432/shop?sslmode=require"
              onChange={(e) => setDsn(e.target.value)}
            />
            <p className="field__hint">
              URI or keyword form. The password is pulled out and encrypted
              before anything is stored.
            </p>
          </div>
        ) : (
          <>
            <div className="field-row field-row--2">
              <div className="field">
                <label className="field__label" htmlFor="host">
                  Host
                </label>
                <input
                  id="host"
                  className="field__input field__input--mono"
                  value={fields.host}
                  onChange={(e) => set('host', e.target.value)}
                />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="port">
                  Port
                </label>
                <input
                  id="port"
                  className="field__input field__input--mono"
                  type="number"
                  value={fields.port}
                  onChange={(e) => set('port', Number(e.target.value))}
                />
              </div>
            </div>

            <div className="field">
              <label className="field__label" htmlFor="database">
                Database
              </label>
              <input
                id="database"
                className="field__input field__input--mono"
                value={fields.database}
                onChange={(e) => set('database', e.target.value)}
              />
            </div>

            <div className="field">
              <label className="field__label" htmlFor="user">
                Role
              </label>
              <input
                id="user"
                className="field__input field__input--mono"
                value={fields.user}
                autoComplete="off"
                onChange={(e) => set('user', e.target.value)}
              />
            </div>

            <div className="field">
              <label className="field__label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                className="field__input field__input--mono"
                type="password"
                value={fields.password}
                autoComplete="new-password"
                onChange={(e) => set('password', e.target.value)}
              />
            </div>

            <div className="field">
              <label className="field__label" htmlFor="sslmode">
                SSL mode
              </label>
              <select
                id="sslmode"
                className="field__select"
                value={fields.sslmode}
                onChange={(e) => set('sslmode', e.target.value as SslMode)}
              >
                {SSL_MODES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
          </>
        )}
      </div>

      <div className="card__foot">
        <button className="btn btn--primary" type="submit" disabled={busy || !complete}>
          {busy ? 'Probing…' : 'Test connection'}
        </button>
        <button
          className="btn btn--ghost"
          type="button"
          disabled={busy || !canSave}
          onClick={() => onSave(input)}
        >
          Attach to project
        </button>
      </div>
    </form>
  );
}
