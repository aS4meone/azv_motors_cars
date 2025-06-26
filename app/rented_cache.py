import asyncio
from typing import Set
import httpx
import logging

logger = logging.getLogger(__name__)
RENTED_URL = "https://api.azvmotors.kz/vehicles/rented"
ACCESS_KEY = "dd91875d72ca8dca553f6b7970706ca6"

# здесь будем хранить актуальный набор арендованных номеров
rented_plates: Set[str] = set()


async def fetch_rented_plates() -> None:
    """Обновить кэш арендованных машин."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(RENTED_URL, params={"key": ACCESS_KEY})
            resp.raise_for_status()
            data = resp.json()
        # ожидаем список {name, plate_number}
        rented_plates.clear()
        for item in data:
            rented_plates.add(item["plate_number"])
        logger.debug(f"Rented plates updated: {rented_plates}")
    except Exception as e:
        logger.error(f"Не смогли получить список аренд: {e}")
