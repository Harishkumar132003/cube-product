import type { Diagnostic, ProbeResult, ServerInfo } from '../types';
import { Icon } from './Icon';

type Tone = 'ok' | 'warn' | 'plain';

/** Facts that change what the pipeline can do, in the order they matter. */
function serverFacts(server: ServerInfo): {
  label: string;
  value: string;
  tone: Tone;
}[] {
  const major = Math.floor(server.server_version_num / 10000);
  return [
    {
      label: 'Version',
      value: `Postgres ${major}`,
      tone: major >= 14 ? 'ok' : 'warn',
    },
    { label: 'Role', value: server.role_name, tone: server.is_superuser ? 'warn' : 'ok' },
    {
      label: 'Server',
      value: server.is_replica ? 'replica' : 'primary',
      tone: server.is_replica ? 'ok' : 'plain',
    },
    {
      label: 'Read-only',
      value: server.read_only_enforced ? 'enforced' : 'off',
      tone: server.read_only_enforced ? 'ok' : 'warn',
    },
    {
      label: 'Query stats',
      value: server.has_pg_stat_statements ? 'available' : 'absent',
      tone: server.has_pg_stat_statements ? 'ok' : 'plain',
    },
    {
      label: 'All roles',
      value: server.has_read_all_stats ? 'visible' : 'own only',
      tone: server.has_read_all_stats ? 'ok' : 'plain',
    },
  ];
}

const GLYPH = { error: 'alert', warning: 'alert', info: 'check' } as const;

function DiagnosticRow({ item }: { item: Diagnostic }) {
  return (
    <div className="feed__row">
      <span className={`feed__glyph feed__glyph--${item.level}`}>
        <Icon name={GLYPH[item.level]} size={14} />
      </span>
      <span className="feed__text">
        <span className="feed__name mono">{item.code}</span>
        <span className="feed__sub">{item.message}</span>
      </span>
    </div>
  );
}

export function ProbeReport({ probe }: { probe: ProbeResult }) {
  const facts = serverFacts(probe.server);
  const counts = probe.warnings.reduce<Record<string, number>>((acc, w) => {
    acc[w.level] = (acc[w.level] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="stack">
      <section className="card">
        <div className="card__head">
          <div>
            <h2 className="card__title">What this role can see</h2>
            <p className="card__note mono">{probe.server.server_version}</p>
          </div>
        </div>
        <div className="card__body">
          <div className="facts">
            {facts.map((fact) => (
              <div className="fact" key={fact.label}>
                <div className="fact__label">{fact.label}</div>
                <div
                  className={
                    fact.tone === 'plain'
                      ? 'fact__value'
                      : `fact__value fact__value--${fact.tone}`
                  }
                >
                  {fact.value}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {probe.warnings.length > 0 && (
        <section className="card">
          <div className="card__head">
            <div>
              <h2 className="card__title">Before generating</h2>
              <p className="card__note">
                Conditions that change what later steps can do. None of them
                block you.
              </p>
            </div>
            <div className="card__head-actions row">
              {counts.error && (
                <span className="pill pill--down">{counts.error} blocking</span>
              )}
              {counts.warning && (
                <span className="pill pill--warn">{counts.warning} to weigh</span>
              )}
              {counts.info && (
                <span className="pill pill--idle">{counts.info} noted</span>
              )}
            </div>
          </div>
          <div className="feed">
            {probe.warnings.map((item) => (
              <DiagnosticRow key={item.code} item={item} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
