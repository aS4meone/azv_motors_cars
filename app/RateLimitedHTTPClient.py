import asyncio
from httpx import AsyncClient
import logging

logger = logging.getLogger(__name__)


class RateLimitedHTTPClient:
    _instance = None

    def __init__(self):
        self.queue = asyncio.Queue()
        self.client = AsyncClient()
        self._worker_task = asyncio.create_task(self._worker())

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def _worker(self):
        while True:
            task = await self.queue.get()
            method, url, kwargs, future = (
                task["method"], task["url"], task["kwargs"], task["future"]
            )

            await asyncio.sleep(1)  # минимальный интервал запросов
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    response = await self.client.request(method, url, **kwargs)
                    if response.status_code == 429:
                        backoff = min(2 ** (attempt + 1), 60)
                        logger.warning(f"429 received, retrying in {backoff}s (attempt {attempt + 1})")
                        await asyncio.sleep(backoff)
                        continue
                    future.set_result(response)
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        backoff = min(2 ** (attempt + 1), 60)
                        await asyncio.sleep(backoff)
                        continue
                    future.set_exception(e)
            self.queue.task_done()

    async def send_request(self, method: str, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.queue.put({
            "method": method, "url": url, "kwargs": kwargs, "future": future
        })
        return await future
