import type { SchemaSummary } from '../types';

interface Props {
  schemas: SchemaSummary[];
  selected: string[];
  disabled?: boolean;
  onChange: (next: string[]) => void;
}

/** Greys out at zero; turns amber when the number means work. */
function Count({ value, flag = false }: { value: number; flag?: boolean }) {
  const className = value === 0 ? 'is-zero' : flag ? 'is-flagged' : undefined;
  return <td className={className}>{value}</td>;
}

export function SchemaPicker({ schemas, selected, disabled, onChange }: Props) {
  const toggle = (name: string) =>
    onChange(
      selected.includes(name)
        ? selected.filter((s) => s !== name)
        : [...selected, name],
    );

  const relations = schemas.reduce(
    (total, s) => total + s.tables + s.views + s.materialized_views,
    0,
  );

  return (
    <section className="card">
      <div className="card__head">
        <div>
          <h2 className="card__title">Schemas to model</h2>
          <p className="card__note">
            {schemas.length} readable {schemas.length === 1 ? 'schema' : 'schemas'},{' '}
            {relations} relations. System and extension schemas are already
            excluded.
          </p>
        </div>
        <div className="card__head-actions">
          <span className="pill pill--violet">{selected.length} selected</span>
        </div>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Schema</th>
              <th>Tables</th>
              <th>Partitioned</th>
              <th>Views</th>
              <th>Matviews</th>
              <th>RLS</th>
              <th>Unanalyzed</th>
              <th>Unreadable</th>
            </tr>
          </thead>
          <tbody>
            {schemas.map((schema) => (
              <tr key={schema.name}>
                <td>
                  <label className="cell-name">
                    <input
                      className="checkbox"
                      type="checkbox"
                      disabled={disabled}
                      checked={selected.includes(schema.name)}
                      onChange={() => toggle(schema.name)}
                    />
                    <span className="mono">{schema.name}</span>
                  </label>
                </td>
                <Count value={schema.tables} />
                <Count value={schema.partitioned_tables} />
                <Count value={schema.views} />
                <Count value={schema.materialized_views} />
                <Count value={schema.rls_tables} />
                <Count value={schema.never_analyzed} flag />
                <Count value={schema.unreadable} flag />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
