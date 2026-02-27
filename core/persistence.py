"""
Lightweight JSON persistence for risk manager state.

Saves positions, session PnL, dedup keys, and cooldowns to survive
bot restarts. Uses atomic write (tmp + rename) to prevent corruption.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.risk import RiskManager

log = logging.getLogger("polysniper.persistence")

DEFAULT_STATE_PATH = Path("data/state.json")


class StateStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_STATE_PATH

    @property
    def path(self) -> Path:
        return self._path

    def save(self, risk: RiskManager) -> None:
        """Persist the risk manager state to disk (atomic write)."""
        state = risk.to_state_dict()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        tmp.replace(self._path)
        log.info(
            "State saved: %d positions, PnL=$%.2f → %s",
            len(state["positions"]), state["session_pnl"], self._path,
        )

    def load(self, risk: RiskManager) -> bool:
        """
        Load persisted state into the risk manager.

        Returns True if state was loaded, False otherwise.
        """
        if not self._path.exists():
            log.info("No saved state found at %s", self._path)
            return False
        try:
            with open(self._path) as f:
                state = json.load(f)
            risk.load_state_dict(state)
            return True
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.error("Failed to load state from %s: %s", self._path, exc)
            return False

    def clear(self) -> None:
        """Delete the state file."""
        if self._path.exists():
            self._path.unlink()
            log.info("State file cleared: %s", self._path)
