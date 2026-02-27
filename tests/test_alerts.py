from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_skips_when_not_configured(self):
        """Telegram not configured → silent skip, no HTTP call."""
        with patch("utils.alerts.settings") as mock_settings:
            mock_settings.telegram.enabled = False

            from utils.alerts import send_alert

            # Should not raise
            await send_alert("Test message")

    @pytest.mark.asyncio
    async def test_sends_when_configured(self):
        with patch("utils.alerts.settings") as mock_settings:
            mock_settings.telegram.enabled = True
            mock_settings.telegram.bot_token = "tok123"
            mock_settings.telegram.chat_id = "chat456"

            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_resp)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            with patch("utils.alerts.aiohttp.ClientSession", return_value=mock_session):
                from utils.alerts import send_alert

                await send_alert("Trade executed!")

                mock_session.post.assert_called_once()
                call_args = mock_session.post.call_args
                assert "tok123" in call_args[0][0]
                payload = call_args[1]["json"]
                assert payload["chat_id"] == "chat456"
                assert "Trade executed!" in payload["text"]

    @pytest.mark.asyncio
    async def test_handles_http_error_gracefully(self):
        with patch("utils.alerts.settings") as mock_settings:
            mock_settings.telegram.enabled = True
            mock_settings.telegram.bot_token = "tok"
            mock_settings.telegram.chat_id = "123"

            mock_resp = AsyncMock()
            mock_resp.status = 403
            mock_resp.text = AsyncMock(return_value="Forbidden")
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_resp)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            with patch("utils.alerts.aiohttp.ClientSession", return_value=mock_session):
                from utils.alerts import send_alert

                await send_alert("Test")  # should not raise

    @pytest.mark.asyncio
    async def test_handles_network_exception(self):
        with patch("utils.alerts.settings") as mock_settings:
            mock_settings.telegram.enabled = True
            mock_settings.telegram.bot_token = "tok"
            mock_settings.telegram.chat_id = "123"

            with patch("utils.alerts.aiohttp.ClientSession", side_effect=Exception("DNS fail")):
                from utils.alerts import send_alert

                await send_alert("Test")  # should not raise


class TestAlertHelpers:
    @pytest.mark.asyncio
    async def test_alert_disconnect(self):
        with patch("utils.alerts.send_alert", new_callable=AsyncMock) as mock_send:
            from utils.alerts import alert_disconnect

            await alert_disconnect("CS2", "Connection reset")
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Disconnected" in msg
            assert "CS2" in msg

    @pytest.mark.asyncio
    async def test_alert_crash(self):
        with patch("utils.alerts.send_alert", new_callable=AsyncMock) as mock_send:
            from utils.alerts import alert_crash

            await alert_crash("OOM")
            mock_send.assert_called_once()
            assert "CRITICAL" in mock_send.call_args[0][0]
