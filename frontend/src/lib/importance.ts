import type { ScanTable } from '../types';

export interface RankedTable {
  table: ScanTable;
  /** How many other relations point at this one. */
  referencedBy: number;
  score: number;
}

/**
 * Order tables by how much a description of them would change the model.
 *
 * Nobody will describe every table, so the ones that matter have to come
 * first. Three signals, all already in the scan:
 *
 *   in-degree  a table many others reference is a hub, and a wrong guess
 *              about it propagates through every join that touches it
 *   size       a table with rows is live; an empty one probably is not
 *   width      more columns means more dimensions hang off the answer
 *
 * In-degree dominates deliberately: `hospitalization` being referenced six
 * times matters more than a wide table nothing points at.
 */
export function rankTables(tables: ScanTable[]): RankedTable[] {
  const inDegree = new Map<string, number>();
  for (const t of tables) {
    for (const fk of t.foreign_keys) {
      if (!fk.target) continue;
      inDegree.set(fk.target, (inDegree.get(fk.target) ?? 0) + 1);
    }
  }

  return tables
    .map((table) => {
      const referencedBy = inDegree.get(table.qualified_name) ?? 0;
      const score =
        referencedBy * 3 +
        Math.log10((table.rows ?? 0) + 1) +
        table.columns.length / 20;
      return { table, referencedBy, score };
    })
    .sort((a, b) => b.score - a.score);
}
