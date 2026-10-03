"""Global callout <-> symbol assignment.

Per-callout greedy mis-pairs in dense bays and can assign two callouts to one symbol
(observed: duplicate locators I-3 and J-7 on CLP S-502). The counts are equal by
construction (70 callouts / 70 dimension-matching symbols on that sheet), so a global
optimum exists - solve it as min-cost bipartite matching (PLAN SS6.2).
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import linear_sum_assignment

UNREACHABLE = 1e6


def assign(costs: np.ndarray) -> dict[int, int]:
    """costs[i, j] = distance from callout i to symbol j (UNREACHABLE if incompatible).

    Returns {callout_index: symbol_index} for feasible pairs only.
    """
    if costs.size == 0:
        return {}
    rows, cols = linear_sum_assignment(costs)
    return {
        int(r): int(c)
        for r, c in zip(rows, cols)
        if costs[r, c] < UNREACHABLE / 2
    }


def runner_up_ratio(costs: np.ndarray, row: int, chosen: int) -> float:
    """0.0 when the winner is unambiguous, ->1.0 when the runner-up is just as close."""
    row_costs = np.sort(costs[row])
    row_costs = row_costs[row_costs < UNREACHABLE / 2]
    if len(row_costs) < 2:
        return 0.0
    best, second = row_costs[0], row_costs[1]
    return float(min(1.0, best / max(second, 1e-6)))
