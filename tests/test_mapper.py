from __future__ import annotations

import pytest

from core.mapper import FuzzyMapper, MarketMapping


class TestIsEsport:
    @pytest.mark.parametrize(
        "question",
        [
            "Will NAVI win CS2 Major?",
            "Will T1 win League of Legends Worlds?",
            "Will Sentinels win Valorant VCT?",
            "Will Spirit win Dota 2 The International?",
            "Who wins the esports grand final?",
        ],
    )
    def test_esport_questions_detected(self, question):
        assert FuzzyMapper._is_esport(question)

    @pytest.mark.parametrize(
        "question",
        [
            "Will Biden win the election?",
            "Will Bitcoin reach $100k?",
            "Who wins the Super Bowl?",
        ],
    )
    def test_non_esport_questions_rejected(self, question):
        assert not FuzzyMapper._is_esport(question)


class TestExtractTeamFromQuestion:
    @pytest.mark.parametrize(
        "question, expected",
        [
            ("Will Natus Vincere win Map 1?", "Natus Vincere"),
            ("Will T1 win Worlds Finals?", "T1"),
            ("Will Sentinels win VCT Americas?", "Sentinels"),
            ("Will Team Spirit win The International?", "Team Spirit"),
        ],
    )
    def test_will_x_win_pattern(self, question, expected):
        assert FuzzyMapper._extract_team_from_question(question) == expected

    def test_vs_pattern(self):
        result = FuzzyMapper._extract_team_from_question("NAVI vs G2 Esports")
        assert result == "NAVI"

    def test_fallback(self):
        result = FuzzyMapper._extract_team_from_question("Some random market?")
        assert result == "Some random market"


class TestDetectGame:
    @pytest.mark.parametrize(
        "question, expected",
        [
            ("Will NAVI win CS2 Major?", "CS2"),
            ("Counter-Strike 2 finals", "CS2"),
            ("Will T1 win League of Legends?", "LoL"),
            ("LoL Worlds 2026", "LoL"),
            ("Valorant VCT Champions", "Valorant"),
            ("Dota 2 The International", "Dota2"),
            ("Super Bowl winner", ""),
        ],
    )
    def test_game_detection(self, question, expected):
        assert FuzzyMapper._detect_game(question) == expected


class TestExpandAliases:
    def test_known_alias(self):
        expanded = FuzzyMapper._expand_aliases("NAVI")
        assert "Natus Vincere" in expanded
        assert "NAVI" in expanded
        assert "NaVi" in expanded

    def test_canonical_name(self):
        expanded = FuzzyMapper._expand_aliases("Natus Vincere")
        assert "NAVI" in expanded

    def test_unknown_team(self):
        expanded = FuzzyMapper._expand_aliases("Unknown Esports")
        assert expanded == ["Unknown Esports"]

    def test_case_insensitive(self):
        expanded = FuzzyMapper._expand_aliases("navi")
        assert "Natus Vincere" in expanded

    def test_short_alias(self):
        expanded = FuzzyMapper._expand_aliases("TL")
        assert "Team Liquid" in expanded
        assert "Liquid" in expanded


class TestFuzzyMapperRefreshAndFind:
    @pytest.mark.asyncio
    async def test_refresh_indexes_esport_markets(self, mock_polymarket_get_markets):
        fm = FuzzyMapper()
        await fm.refresh()
        assert len(fm._markets) == 4  # 4 esport + 1 politics filtered out

    @pytest.mark.asyncio
    async def test_refresh_skips_non_esport(self, mock_polymarket_get_markets):
        fm = FuzzyMapper()
        await fm.refresh()
        questions = [m.question for m in fm._markets]
        assert not any("election" in q for q in questions)

    @pytest.mark.asyncio
    async def test_find_token_exact_name(self, mock_polymarket_get_markets):
        fm = FuzzyMapper()
        await fm.refresh()
        result = fm.find_token("Natus Vincere", "CS2")
        assert result is not None
        assert result.token_id_yes == "tok_yes_navi"

    @pytest.mark.asyncio
    async def test_find_token_alias(self, mock_polymarket_get_markets):
        fm = FuzzyMapper()
        await fm.refresh()
        result = fm.find_token("NAVI", "CS2")
        assert result is not None
        assert result.token_id_yes == "tok_yes_navi"

    @pytest.mark.asyncio
    async def test_find_token_cross_game(self, mock_polymarket_get_markets):
        fm = FuzzyMapper()
        await fm.refresh()
        result = fm.find_token("T1", "LoL")
        assert result is not None
        assert result.token_id_yes == "tok_yes_t1"

    @pytest.mark.asyncio
    async def test_find_token_no_match(self, mock_polymarket_get_markets):
        fm = FuzzyMapper()
        await fm.refresh()
        result = fm.find_token("Totally Unknown Team XYZ", "CS2")
        assert result is None

    @pytest.mark.asyncio
    async def test_find_token_game_filter(self, mock_polymarket_get_markets):
        fm = FuzzyMapper()
        await fm.refresh()
        result = fm.find_token("Sentinels", "Valorant")
        assert result is not None
        assert "Valorant" in result.question or "VCT" in result.question

    @pytest.mark.asyncio
    async def test_find_token_short_alias_sen(self, mock_polymarket_get_markets):
        fm = FuzzyMapper()
        await fm.refresh()
        result = fm.find_token("SEN", "Valorant")
        assert result is not None
        assert result.token_id_yes == "tok_yes_sen"

    @pytest.mark.asyncio
    async def test_find_token_dota(self, mock_polymarket_get_markets):
        fm = FuzzyMapper()
        await fm.refresh()
        result = fm.find_token("Spirit", "Dota2")
        assert result is not None
        assert result.token_id_yes == "tok_yes_spirit"

    @pytest.mark.asyncio
    async def test_empty_markets(self):
        from unittest.mock import AsyncMock, patch

        with patch("core.mapper.polymarket") as mock_pm:
            mock_pm.get_markets = AsyncMock(return_value=[])
            fm = FuzzyMapper()
            await fm.refresh()
            assert fm.find_token("NAVI", "CS2") is None
