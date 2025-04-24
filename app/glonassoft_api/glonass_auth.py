import asyncio
import logging
from typing import Optional
from httpx import Response

from app.RateLimitedHTTPClient import RateLimitedHTTPClient

logger = logging.getLogger(__name__)


async def get_auth_token(base_url: str, login: str, password: str) -> Optional[str]:
    """
    Получает токен авторизации с API, используя очередь запросов.
    """
    client = RateLimitedHTTPClient.get_instance()
    url = f"{base_url}/api/v3/auth/login"
    payload = {"login": login, "password": password}

    try:
        response: Response = await client.send_request("POST", url, json=payload)
        response.raise_for_status()
        auth_data = response.json()
        token = auth_data.get("AuthId")
        if not token:
            logger.error(f"Auth succeeded but no AuthId in response: {auth_data}")
        return token
    except Exception as e:
        logger.error(f"Network/error fetching auth token from {url}: {e}", exc_info=True)
        return None
