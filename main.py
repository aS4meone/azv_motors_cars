import asyncio
import httpx
import logging

# ---------------------------
# Конфигурация
# ---------------------------
TELEGRAM_BOT_TOKEN = '7649836420:AAHJkjRAlMOe2NWqK_UIkYXlFBx07BCFXlY'  # замените на токен вашего бота
TELEGRAM_CHAT_ID = '965048905' # замените на нужный chat_id для уведомлений

# Словарь для хранения подключённых устройств: device_id -> StreamWriter
devices = {}
devices_lock = asyncio.Lock()

# Глобальный httpx клиент для работы с Telegram API
telegram_client: httpx.AsyncClient = None

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# ---------------------------
# Функция отправки сообщения в Telegram
# ---------------------------
async def send_telegram_message(text: str):
    """Отправляет сообщение в Telegram через Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    params = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        await telegram_client.post(url, data=params)
    except Exception as e:
        logging.error(f"Ошибка отправки сообщения в Telegram: {e}")


# ---------------------------
# Обработка соединения от устройства
# ---------------------------
async def handle_device(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info('peername')
    device_id = None
    try:
        while True:
            data = await reader.read(1024)
            if not data:
                break  # соединение закрыто
            message = data.decode('utf-8', errors='ignore').strip()
            logging.info(f"[{addr}] Получено: {message}")
            await send_telegram_message(f"[{addr}] Получено: {message}")

            # Проверка на handshake сообщение
            if message.startswith("@NTC"):
                # Извлечение device_id из сообщения
                if ':' in message:
                    parts = message.split(':', 1)
                    device_id = parts[1].strip()
                    async with devices_lock:
                        devices[device_id] = writer
                    logging.info(f"[{addr}] Зарегистрировано устройство: {device_id}")
                    await send_telegram_message(f"Зарегистрировано устройство: {device_id}")

                    # Отправка ответа на handshake
                    handshake_response = f"@NTC*<S"  # Пример ответа
                    writer.write(handshake_response.encode('utf-8'))
                    await writer.drain()
                    logging.info(f"[{addr}] Отправлен ответ на handshake: {handshake_response}")
                else:
                    logging.warning(f"[{addr}] Не удалось извлечь device_id из: {message}")
                    await send_telegram_message(f"[{addr}] Не удалось извлечь device_id из: {message}")
            else:
                # Обработка других сообщений
                chat_message = f"Ответ от устройства {device_id if device_id else addr}:\n<pre>{message}</pre>"
                await send_telegram_message(chat_message)
    except Exception as e:
        logging.error(f"[{addr}] Ошибка: {e}")
    finally:
        writer.close()
        await writer.wait_closed()
        if device_id:
            async with devices_lock:
                if device_id in devices:
                    del devices[device_id]
        logging.info(f"[{addr}] Соединение закрыто")
        await send_telegram_message(f"[{addr}] Соединение закрыто")


# ---------------------------
# Запуск TCP‑сервера для устройств
# ---------------------------
async def start_device_server():
    """Запускает асинхронный TCP‑сервер на порту 12345."""
    server = await asyncio.start_server(handle_device, '0.0.0.0', 12345)
    addr = server.sockets[0].getsockname()
    logging.info(f"Сервер устройств запущен на {addr}")
    await send_telegram_message(f"Сервер устройств запущен на {addr}")
    async with server:
        await server.serve_forever()


# ---------------------------
# Опрос Telegram для получения команд
# ---------------------------
async def telegram_polling():
    """
    Осуществляет long polling Telegram API методом getUpdates.
    При получении команды /send <device_id> <команда> ищет устройство и отправляет команду.
    """
    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    while True:
        params = {"timeout": 30, "offset": offset}
        try:
            response = await telegram_client.get(url, params=params)
            data = response.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message", {})
                text = message.get("text", "")
                # Обработка команды /send
                if text.startswith("/send"):
                    parts = text.split(maxsplit=2)
                    if len(parts) < 3:
                        await send_telegram_message("Использование: /send <device_id> <команда>")
                        continue
                    device_id = parts[1].strip()
                    command = parts[2].strip()
                    async with devices_lock:
                        writer = devices.get(device_id)
                    if writer is None:
                        await send_telegram_message(f"Устройство {device_id} не подключено.")
                    else:
                        try:
                            writer.write(command.encode('utf-8'))
                            await writer.drain()
                            await send_telegram_message(f"Команда отправлена устройству {device_id}.")
                            logging.info(f"Отправлена команда '{command}' устройству {device_id}")
                        except Exception as e:
                            await send_telegram_message(f"Ошибка при отправке команды: {e}")
        except Exception as e:
            logging.error(f"Ошибка при опросе Telegram: {e}")
        await asyncio.sleep(1)  # небольшая задержка для предотвращения излишней нагрузки


# ---------------------------
# Основная функция
# ---------------------------
async def main():
    global telegram_client
    # Инициализируем глобальный httpx клиент с длительным timeout для поддержания постоянного соединения
    telegram_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
    try:
        await asyncio.gather(
            start_device_server(),
            telegram_polling(),
        )
    finally:
        await telegram_client.aclose()


if __name__ == '__main__':
    asyncio.run(main())
