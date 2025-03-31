import asyncio
from typing import Optional
from httpx import Response

from app.RateLimitedHTTPClient import RateLimitedHTTPClient


async def get_auth_token(base_url: str, login: str, password: str) -> Optional[str]:
    """
    Получает токен авторизации с API, используя очередь запросов.

    :param base_url: Базовый URL API (например, https://hosting.glonasssoft.ru)
    :param login: Логин пользователя
    :param password: Пароль пользователя
    :return: Токен авторизации или None при ошибке
    """
    client = RateLimitedHTTPClient.get_instance()
    url = f"{base_url}/api/v3/auth/login"
    payload = {"login": login, "password": password}

    try:
        response: Response = await client.send_request("POST", url, json=payload)
        if response.status_code == 200:
            auth_data = response.json()
            print(auth_data.get("AuthId"))
            return auth_data.get("AuthId")
        else:
            print(f"Ошибка авторизации: {response.status_code}, {response.text}")
    except Exception as e:
        print(f"Ошибка сети: {e}")

    return None
