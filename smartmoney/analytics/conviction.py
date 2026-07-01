"""Confluence + conviction scoring (Steps 2 & 3).

For each security that appears in the batch of 13Fs newly made public, we
sum weighted "votes" across managers:

    contribution = manager_quality
                 * position_type_weight(new > add > hold)
                 * portfolio_weight (position value / manager AUM)   # conviction

Then Step 3 applies a catalyst multiplier when an independent signal
(earnings beat, buyback, activist 13D) confirms the move — separating real
conviction from noise.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Vote:
    cik: str
    position_type: str      # "new" | "add" | "hold"
    portfolio_weight: float # this position as a fraction of the manager's book
    quality: float


@dataclass
class Confluence:
    ticker: str
    votes: list[Vote] = field(default_factory=list)

    @property
    def n_managers(self) -> int:
        return len({v.cik for v in self.votes})


def type_weight(cfg, position_type: str) -> float:
    return {
        "new": cfg["strategy"]["new_position_weight"],
        "add": cfg["strategy"]["add_position_weight"],
        "hold": cfg["strategy"]["hold_position_weight"],
    }[position_type]


def score(cfg, conf: Confluence, has_catalyst: bool) -> float:
    """Composite conviction score. Portfolio-weight is scaled so a 10%
    position contributes ~1.0 before quality/type weighting."""
    base = 0.0
    for v in conf.votes:
        base += v.quality * type_weight(cfg, v.position_type) * (v.portfolio_weight * 10.0)
    if has_catalyst:
        base *= cfg["strategy"]["catalyst_multiplier"]
    return base
