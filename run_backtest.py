"""
PolySniper Backtest CLI

Usage:
    python run_backtest.py scenarios/example_session.json
    python run_backtest.py scenarios/*.json
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from backtest.runner import BacktestRunner
from backtest.scenario import load_scenario

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(name)-30s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python run_backtest.py <scenario.json> [scenario2.json ...]")
        sys.exit(1)

    paths = [Path(p) for p in sys.argv[1:]]
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"Error: file not found: {p}")
        sys.exit(1)

    for path in paths:
        scenario = load_scenario(path)
        runner = BacktestRunner(scenario)
        report = runner.run()
        print(report.summary())


if __name__ == "__main__":
    main()
