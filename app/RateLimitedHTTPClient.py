import asyncio

import httpx


class RateLimitedHTTPClient:
    _instance = None

    def __init__(self):
        self.queue = asyncio.Queue()
        self.client = httpx.AsyncClient()
        # Запуск фонового воркера, который обрабатывает запросы из очереди
        self._worker_task = asyncio.create_task(self._worker())

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def _worker(self):
        while True:
            task = await self.queue.get()
            method = task["method"]
            url = task["url"]
            kwargs = task["kwargs"]
            future = task["future"]

            # Ждем 1 секунду между запросами
            await asyncio.sleep(1)
            while True:
                try:
                    response = await self.client.request(method, url, **kwargs)
                    if response.status_code == 429:
                        # Если сервер вернул 429, ждём 2 секунды и пробуем снова
                        await asyncio.sleep(2)
                        continue
                    # Если запрос успешен (или пришёл другой код), возвращаем ответ
                    future.set_result(response)
                    break
                except Exception as e:
                    future.set_exception(e)
                    break

            self.queue.task_done()

    async def send_request(self, method: str, url: str, **kwargs):
        """
        Отправляет HTTP запрос с заданным методом, url и дополнительными параметрами.
        Возвращает httpx.Response.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.queue.put({
            "method": method,
            "url": url,
            "kwargs": kwargs,
            "future": future
        })
        return await future
