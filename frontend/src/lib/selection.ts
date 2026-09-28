import type { ScanTable } from '../types';

export interface MissingTarget {
  /** The unselected relation that selected tables point at. */
  target: string;
  /** Selected tables whose foreign keys reference it. */
  referencedBy: string[];
  /** True when the target was not in the scan at all. */
  outsideScan: boolean;
}

/**
 * Foreign keys from selected tables that land outside the selection.
 *
 * Each one is a join Cube cannot generate: the target cube would not exist.
 * Dropping them silently is how a model ends up quietly wrong, so they are
 * surfaced and grouped by the table that would fix them.
 */
export function missingTargets(
  tables: ScanTable[],
  selected: Set<string>,
): MissingTarget[] {
  const scanned = new Set(tables.map((t) => t.qualified_name));
  const grouped = new Map<string, MissingTarget>();

  for (const table of tables) {
    if (!selected.has(table.qualified_name)) continue;
    for (const fk of table.foreign_keys) {
      if (!fk.target || selected.has(fk.target)) continue;
      const entry = grouped.get(fk.target) ?? {
        target: fk.target,
        referencedBy: [],
        outsideScan: !scanned.has(fk.target),
      };
      if (!entry.referencedBy.includes(table.name)) {
        entry.referencedBy.push(table.name);
      }
      grouped.set(fk.target, entry);
    }
  }

  // Most-referenced first: including that one repairs the most joins.
  return [...grouped.values()].sort(
    (a, b) => b.referencedBy.length - a.referencedBy.length,
  );
}

/** Foreign keys between two selected tables, i.e. joins that survive. */
export function joinsKept(tables: ScanTable[], selected: Set<string>): number {
  let kept = 0;
  for (const table of tables) {
    if (!selected.has(table.qualified_name)) continue;
    for (const fk of table.foreign_keys) {
      if (fk.target && selected.has(fk.target)) kept += 1;
    }
  }
  return kept;
}
