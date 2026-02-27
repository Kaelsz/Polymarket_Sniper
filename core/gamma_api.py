"""
Polymarket Gamma API Client

Fetches esport markets directly via the Gamma API (gamma-api.polymarket.com).
This is the primary source for market discovery — more reliable than CLOB get_markets
for filtered esport content.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

log = logging.getLogger("polysniper.gamma")

_GAMMA_BASE = "https://gamma-api.polymarket.com"
_HEADERS = {"User-Agent": "PolySniper/1.0"}

# Tag IDs for esport categories (from Polymarket tags/sports metadata)
_ESPORT_TAG_IDS = {
    "LoL": 65,        # league of legends
    "Dota2": 102366,  # dota 2
    "Valorant": None, # no dedicated tag yet
}


async def fetch_markets_by_tag(tag_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """
    Fetch active markets for a given tag ID.

    Returns list of markets with: question, conditionId, clobTokenIds, outcomes.
    """
    url = f"{_GAMMA_BASE}/markets"
    params = {"tag_id": tag_id, "active": "true", "closed": "false", "limit": limit}

    async with aiohttp.ClientSession(headers=_HEADERS) as sess:
        async with sess.get(url, params=params) as resp:
            if resp.status != 200:
                log.warning("Gamma API markets %d: %s", resp.status, await resp.text())
                return []

            data = await resp.json()
            return data if isinstance(data, list) else []


async def fetch_all_esport_markets(limit_per_tag: int = 50) -> list[dict[str, Any]]:
    """
    Fetch markets from all known esport tags and merge (deduplicated by conditionId).
    """
    seen_ids: set[str] = set()
    all_markets: list[dict[str, Any]] = []

    for game, tag_id in _ESPORT_TAG_IDS.items():
        if tag_id is None:
            continue
        try:
            markets = await fetch_markets_by_tag(tag_id, limit=limit_per_tag)
            for m in markets:
                cid = m.get("conditionId") or m.get("condition_id", "")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    all_markets.append(m)
        except Exception as exc:
            log.debug("Gamma fetch for %s (tag=%s) failed: %s", game, tag_id, exc)

    return all_markets
