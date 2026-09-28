"""Join direction, decided deterministically (plan.md step 8).

Cube joins are directed: the declaring cube is the left side of a LEFT JOIN,
and nothing auto-reverses. A join declared on a child pointing at its parent
lets the child reach the parent and nobody else -- so a query, or a view,
rooted at the parent cannot see that child, and two children can never be
combined at all.

A view has to name one root cube and reach every member from it. That makes
reachability a hard requirement rather than a preference, and direction a fact
about the graph rather than a judgement call, so it is settled here instead of
being asked of the model.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.generate.ir import JoinDecision

INVERSE = {
    "many_to_one": "one_to_many",
    "one_to_many": "many_to_one",
    "one_to_one": "one_to_one",
}

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")


def _reorient_sql(sql: str, declaring: str) -> str:
    """Rewrite the placeholders so the other side becomes `{CUBE}`.

    The condition itself is an equality and needs no reordering; only which
    side `{CUBE}` refers to changes. The old declaring cube is named
    explicitly, and the renderer resolves it to the real cube name.
    """

    def swap(match: re.Match[str]) -> str:
        if match.group(1) == "CUBE":
            return "{" + declaring + "}"
        return "{CUBE}"

    return _PLACEHOLDER.sub(swap, sql)


def invert(join: JoinDecision) -> JoinDecision:
    """The same relationship, declared from the other end."""
    return JoinDecision(
        declaring_cube=join.target_cube,
        target_cube=join.declaring_cube,
        relationship=INVERSE[join.relationship],
        sql=_reorient_sql(join.sql, join.declaring_cube),
        reason=f"{join.reason} (declared from {join.target_cube} so the root can traverse it)",
    )


def reachable(root: str, joins: Iterable[JoinDecision]) -> set[str]:
    """Everything a query rooted at `root` can reach, following direction."""
    out: dict[str, set[str]] = {}
    for join in joins:
        out.setdefault(join.declaring_cube, set()).add(join.target_cube)

    seen = {root}
    stack = [root]
    while stack:
        node = stack.pop()
        for nxt in out.get(node, set()) - seen:
            seen.add(nxt)
            stack.append(nxt)
    return seen


def choose_root(tables: list[str], joins: list[JoinDecision]) -> str | None:
    """The hub: whichever cube the most others point at.

    In-degree is the signal that survives naming differences. `hospitalization`
    wins on the reference schema because seven tables carry its foreign key,
    which is also why every hand-written view starts there.
    """
    if not tables:
        return None
    in_degree: dict[str, int] = {t: 0 for t in tables}
    for join in joins:
        if join.target_cube in in_degree:
            in_degree[join.target_cube] += 1
    # Ties break on name so a rerun on unchanged evidence gives the same root.
    return max(sorted(in_degree), key=lambda t: in_degree[t])


def orient(
    tables: list[str], joins: list[JoinDecision], root: str | None = None
) -> tuple[str | None, list[JoinDecision], list[str]]:
    """Point joins so every cube is reachable from one root.

    Returns the root, the reoriented joins, and the cubes still out of reach --
    which means no path of any direction connects them, not that the direction
    is wrong. Those are reported rather than forced, because inventing a join
    to connect them would be a guess.
    """
    root = root or choose_root(tables, joins)
    if root is None:
        return None, joins, []

    current = list(joins)
    # Each inversion can expose more of the graph, so repeat to a fixpoint. The
    # bound is the join count: every join is inverted at most once.
    for _ in range(len(current) + 1):
        seen = reachable(root, current)
        if all(t in seen for t in tables):
            break
        changed = False
        for i, join in enumerate(current):
            # An edge pointing from the unreachable side into the reachable
            # side is exactly the one to turn around.
            if join.declaring_cube not in seen and join.target_cube in seen:
                current[i] = invert(join)
                changed = True
                break
        if not changed:
            break

    seen = reachable(root, current)
    unreachable = sorted(t for t in tables if t not in seen)
    return root, current, unreachable
