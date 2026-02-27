"""
Backtest Scenario — data structures and JSON loader.

A scenario is a sequence of esport match events with associated
market prices and eventual resolutions, used to simulate the
bot's behaviour over historical or hypothetical data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("polysniper.backtest.scenario")


@dataclass(slots=True)
class ScenarioEvent:
    """A single event in a backtest scenario."""

    timestamp: float
    game: str
    team_won: str
    team_lost: str = ""
    event: str = "Match Ended"
    match_id: str = ""
    token_id: str = ""
    ask_price: float = 0.50
    resolution: str = ""  # "win", "loss", or "" (unresolved)
    resolution_delay_s: float = 0.0
    fuzzy_score: float = 90.0


@dataclass(slots=True)
class Scenario:
    """A complete backtest scenario."""

    name: str = "Unnamed"
    description: str = ""
    events: list[ScenarioEvent] = field(default_factory=list)
    min_buy_price: float | None = None
    max_buy_price: float | None = None
    order_size_usdc: float | None = None
    fee_rate: float | None = None
    stop_loss_pct: float | None = None
    sizing_mode: str | None = None
    min_order_usdc: float | None = None
    max_order_usdc: float | None = None

    @property
    def duration_s(self) -> float:
        if len(self.events) < 2:
            return 0.0
        return self.events[-1].timestamp - self.events[0].timestamp


def load_scenario(path: Path | str) -> Scenario:
    """Load a scenario from a JSON file."""
    path = Path(path)
    with open(path) as f:
        raw = json.load(f)

    events = []
    for i, e in enumerate(raw.get("events", [])):
        try:
            events.append(ScenarioEvent(
                timestamp=float(e.get("timestamp", i)),
                game=e["game"],
                team_won=e["team_won"],
                team_lost=e.get("team_lost", ""),
                event=e.get("event", "Match Ended"),
                match_id=e.get("match_id", f"bt_match_{i}"),
                token_id=e.get("token_id", f"bt_token_{i}"),
                ask_price=float(e.get("ask_price", 0.50)),
                resolution=e.get("resolution", ""),
                resolution_delay_s=float(e.get("resolution_delay_s", 0.0)),
                fuzzy_score=float(e.get("fuzzy_score", 90.0)),
            ))
        except (KeyError, ValueError) as exc:
            log.warning("Skipping malformed event #%d: %s", i, exc)

    scenario = Scenario(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        events=events,
        min_buy_price=raw.get("min_buy_price"),
        max_buy_price=raw.get("max_buy_price"),
        order_size_usdc=raw.get("order_size_usdc"),
        fee_rate=raw.get("fee_rate"),
        stop_loss_pct=raw.get("stop_loss_pct"),
        sizing_mode=raw.get("sizing_mode"),
        min_order_usdc=raw.get("min_order_usdc"),
        max_order_usdc=raw.get("max_order_usdc"),
    )
    log.info("Loaded scenario '%s': %d events", scenario.name, len(events))
    return scenario
