# app/notifications.py
import logging
import httpx
from app.core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS

logger = logging.getLogger(__name__)


async def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        for chat_id in TELEGRAM_CHAT_IDS:
            payload = {"chat_id": chat_id, "text": text}
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                logger.info(f"Telegram notification sent to {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send Telegram to {chat_id}: {e}")
