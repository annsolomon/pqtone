"""Optimal one-to-one assignment (Hungarian algorithm), pure Python. Milestone Q5, ADR-028.

The scorer pairs ground-truth episodes with detected incidents inside a match window. Greedy
"closest pair first" can lose a match: one incident taken by its nearest episode leaves another
episode with nothing in range, although a different pairing matches both. The Hungarian
algorithm finds the pairing with the most matches and, among those, the smallest total time
distance.
"""
from __future__ import annotations

INF = float("inf")


def hungarian(cost: list[list[float]]) -> list[int]:
    """Minimum-cost assignment for an n x m matrix with n <= m.

    Returns col[i] for each row i: the column assigned to it. O(n^2 m), potentials method
    (Kuhn-Munkres with Jonker-Volgenant style shortest augmenting paths).
    """
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    if n > m:
        raise ValueError("hungarian needs rows <= columns; transpose first")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)       # p[j] = row matched to column j (1-based), 0 = free
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, -1
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    col = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            col[p[j] - 1] = j - 1
    return col


def max_matching_min_distance(n_rows: int, n_cols: int,
                              distance: dict[tuple[int, int], int]) -> list[tuple[int, int]]:
    """Pairs (row, col) using only allowed edges in `distance`: most pairs first, then least total distance."""
    if not distance:
        return []
    # A forbidden pairing costs more than every allowed distance together, so the optimum always
    # prefers one more match over any saving in distance.
    big = float(sum(distance.values()) + 1)
    transpose = n_rows > n_cols
    rows, cols = (n_cols, n_rows) if transpose else (n_rows, n_cols)
    cost = [[big] * cols for _ in range(rows)]
    for (r, c), d in distance.items():
        if transpose:
            cost[c][r] = float(d)
        else:
            cost[r][c] = float(d)
    out = []
    for i, j in enumerate(hungarian(cost)):
        r, c = (j, i) if transpose else (i, j)
        if (r, c) in distance:
            out.append((r, c))
    return sorted(out)
