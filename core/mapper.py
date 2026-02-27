"""
Market Scheduler & Fuzzy Mapper

Fetches active Polymarket esport markets, then maps incoming
MatchEvent team names to the correct token IDs using rapidfuzz.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz, process

from core.polymarket import polymarket

log = logging.getLogger("polysniper.mapper")

_ESPORT_KEYWORDS = [
    "cs2", "counter-strike", "csgo",
    "league of legends", "lol", "worlds",
    "valorant", "vct", "champions tour",
    "dota", "the international",
    "esport", "esports",
]

_TEAM_ALIASES: dict[str, list[str]] = {
    "Natus Vincere": ["NAVI", "NaVi", "Natus Vincere"],
    "G2 Esports": ["G2", "G2 Esports"],
    "Team Liquid": ["Liquid", "Team Liquid", "TL"],
    "T1": ["T1", "SK Telecom", "SKT"],
    "Fnatic": ["Fnatic", "FNC"],
    "Cloud9": ["Cloud9", "C9"],
    "Team Spirit": ["Spirit", "Team Spirit"],
    "Sentinels": ["Sentinels", "SEN"],
}


@dataclass
class MarketMapping:
    condition_id: str
    token_id_yes: str
    token_id_no: str
    question: str
    team_name: str
    game_hint: str
    score: float = 0.0


class FuzzyMapper:
    """Maps raw team names from adapters to Polymarket token IDs."""

    REFRESH_INTERVAL: float = 60.0
    MIN_MATCH_SCORE: float = 65.0

    def __init__(self) -> None:
        self._markets: list[MarketMapping] = []
        self._lock = asyncio.Lock()
        self._last_refresh: float = 0

    async def refresh(self) -> None:
        """Fetch and index all active esport-related Polymarket markets."""
        async with self._lock:
            try:
                raw = await polymarket.get_markets()
            except Exception as exc:
                log.error("Failed to fetch markets: %s", exc)
                return

            markets_list = raw if isinstance(raw, list) else raw.get("data", [])
            self._markets.clear()

            for m in markets_list:
                question = m.get("question", "")
                if not self._is_esport(question):
                    continue

                tokens = m.get("tokens", [])
                yes_token = next(
                    (t for t in tokens if t.get("outcome", "").lower() == "yes"),
                    None,
                )
                no_token = next(
                    (t for t in tokens if t.get("outcome", "").lower() == "no"),
                    None,
                )
                if not yes_token:
                    continue

                team = self._extract_team_from_question(question)
                game_hint = self._detect_game(question)

                self._markets.append(MarketMapping(
                    condition_id=m.get("condition_id", ""),
                    token_id_yes=yes_token.get("token_id", ""),
                    token_id_no=no_token.get("token_id", "") if no_token else "",
                    question=question,
                    team_name=team,
                    game_hint=game_hint,
                ))

            log.info("Indexed %d esport markets", len(self._markets))

    def find_token(self, team_won: str, game: str) -> MarketMapping | None:
        """
        Find the best-matching YES token for a winning team.
        Returns None if no confident match is found.
        """
        candidates = [
            m for m in self._markets
            if not m.game_hint or m.game_hint.lower() == game.lower()
        ]
        if not candidates:
            candidates = self._markets

        if not candidates:
            return None

        expanded = self._expand_aliases(team_won)
        best: MarketMapping | None = None
        best_score = 0.0

        for alias in expanded:
            result = process.extractOne(
                alias,
                [m.team_name for m in candidates],
                scorer=fuzz.token_sort_ratio,
                score_cutoff=self.MIN_MATCH_SCORE,
            )
            if result and result[1] > best_score:
                idx = result[2]
                best = candidates[idx]
                best_score = result[1]

        if best:
            best.score = best_score
            log.info(
                "Mapped '%s' → '%s' (score=%.1f, token=%s)",
                team_won, best.team_name, best_score, best.token_id_yes[:12],
            )
        return best

    @staticmethod
    def _is_esport(question: str) -> bool:
        q = question.lower()
        return any(kw in q for kw in _ESPORT_KEYWORDS)

    @staticmethod
    def _extract_team_from_question(question: str) -> str:
        """
        Heuristic: "Will <Team> win Map 1?" → "Team"
        """
        m = re.match(r"Will\s+(.+?)\s+win", question, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m = re.match(r"(.+?)\s+(?:to|vs\.?|versus)\s+", question, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return question.split("?")[0].strip()

    @staticmethod
    def _detect_game(question: str) -> str:
        q = question.lower()
        if any(k in q for k in ("cs2", "counter-strike", "csgo")):
            return "CS2"
        if any(k in q for k in ("league of legends", "lol", "worlds")):
            return "LoL"
        if any(k in q for k in ("valorant", "vct")):
            return "Valorant"
        if any(k in q for k in ("dota", "the international")):
            return "Dota2"
        return ""

    @staticmethod
    def _expand_aliases(team: str) -> list[str]:
        names = [team]
        for canonical, aliases in _TEAM_ALIASES.items():
            for alias in aliases:
                if alias.lower() == team.lower():
                    names.extend(aliases)
                    names.append(canonical)
                    return list(set(names))
        return names


mapper = FuzzyMapper()
