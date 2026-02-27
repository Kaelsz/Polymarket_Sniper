"""
Non-blocking Telegram alert system.

Sends notifications for: trade executions, WebSocket disconnects, critical crashes.
"""

from __future__ import annotations

import logging

import aiohttp

from core.config import settings

log = logging.getLogger("polysniper.alerts")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_alert(message: str) -> None:
    """Send a Telegram message. Fails silently if not configured."""
    cfg = settings.telegram
    if not cfg.enabled:
        log.debug("Telegram not configured — skipping alert")
        return

    url = _TELEGRAM_API.format(token=cfg.bot_token)
    payload = {
        "chat_id": cfg.chat_id,
        "text": f"[PolySniper] {message}",
        "parse_mode": "HTML",
    }

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("Telegram API error %d: %s", resp.status, body)
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)


async def alert_disconnect(adapter_name: str, error: str) -> None:
    await send_alert(f"WebSocket Disconnected\nAdapter: {adapter_name}\nError: {error}")


async def alert_crash(error: str) -> None:
    await send_alert(f"CRITICAL CRASH\n{error}")
