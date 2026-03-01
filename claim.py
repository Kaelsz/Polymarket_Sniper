"""
Manually claim resolved positions.
Usage: python3 claim.py <condition_id>
       python3 claim.py --all  (claims all resolved positions from state)
"""

from dotenv import load_dotenv
load_dotenv(override=True)

import json
import os
import sys
from pathlib import Path

from core.claimer import PositionClaimer

pk = os.getenv("POLY_PRIVATE_KEY")
safe = os.getenv("POLY_FUNDER", "") or os.getenv("POLYMARKET_ADDRESS")

claimer = PositionClaimer(private_key=pk, safe_address=safe)


def claim_one(condition_id: str) -> None:
    import asyncio
    print(f"Claiming condition: {condition_id[:20]}...")
    receipt = asyncio.run(claimer.redeem(condition_id))
    if receipt:
        status = receipt.get("status", 0)
        tag = "OK" if status == 1 else "FAILED"
        print(f"  {tag} - tx: {receipt.get('transactionHash', b'').hex()[:20]}...")
    else:
        print("  FAILED - check logs")


def claim_all() -> None:
    state_path = Path("data/state.json")
    if not state_path.exists():
        print("No state file found at data/state.json")
        return

    state = json.loads(state_path.read_text())
    positions = state.get("positions", [])
    if not positions:
        print("No open positions in state")
        return

    print(f"Found {len(positions)} position(s)")
    for pos in positions:
        cid = pos.get("condition_id", "")
        team = pos.get("team", "?")
        if not cid:
            print(f"  SKIP {team} - no condition_id")
            continue
        print(f"\n  {team} (condition: {cid[:20]}...)")
        claim_one(cid)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 claim.py <condition_id>")
        print("  python3 claim.py --all")
        sys.exit(1)

    arg = sys.argv[1]
    if arg == "--all":
        claim_all()
    else:
        claim_one(arg)
