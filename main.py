#!/usr/bin/env python3
import asyncio
import httpx

# ---------------------------
# Конфигурация
# ---------------------------

TELEGRAM_BOT_TOKEN = '7649836420:AAHJkjRAlMOe2NWqK_UIkYXlFBx07BCFXlY'  # замените на токен вашего бота
TELEGRAM_CHAT_ID = '965048905'# замените на нужный chat_id для уведомлений

# Словарь для хранения подключённых устройств: device_id -> StreamWriter
devices = {}
devices_lock = asyncio.Lock()

# Глобальный httpx клиент для работы с Telegram API
telegram_client: httpx.AsyncClient = None

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
        print(f"Ошибка отправки сообщения в Telegram: {e}")

# ---------------------------
# Обработка соединения от устройства
# ---------------------------
async def handle_device(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """
    Обрабатывает подключение устройства:
      - При получении регистрационного сообщения (начинается с "@NTC") извлекает device_id
      - Все последующие данные от устройства пересылаются в Telegram
    """
    addr = writer.get_extra_info('peername')
    device_id = None
    try:
        while True:
            data = await reader.read(1024)
            if not data:
                break  # соединение закрыто
            message = data.decode('utf-8', errors='ignore').strip()
            print(f"[{addr}] Получено: {message}")

            # Регистрационное сообщение устройства
            if message.startswith("@NTC"):
                if ':' in message:
                    parts = message.split(':', 1)
                    device_id = parts[1].strip()
                    async with devices_lock:
                        devices[device_id] = writer
                    print(f"[{addr}] Зарегистрировано устройство: {device_id}")
                else:
                    print(f"[{addr}] Не удалось извлечь device_id из: {message}")
            else:
                # Любое иное сообщение считаем ответом и пересылаем в Telegram
                chat_message = f"Ответ от устройства {device_id if device_id else addr}:\n<pre>{message}</pre>"
                await send_telegram_message(chat_message)
    except Exception as e:
        print(f"[{addr}] Ошибка: {e}")
    finally:
        writer.close()
        await writer.wait_closed()
        if device_id:
            async with devices_lock:
                if device_id in devices:
                    del devices[device_id]
        print(f"[{addr}] Соединение закрыто")

# ---------------------------
# Запуск TCP‑сервера для устройств
# ---------------------------
async def start_device_server():
    """Запускает асинхронный TCP‑сервер на порту 12345."""
    server = await asyncio.start_server(handle_device, '0.0.0.0', 12345)
    addr = server.sockets[0].getsockname()
    print(f"Сервер устройств запущен на {addr}")
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
                            print(f"Отправлена команда '{command}' устройству {device_id}")
                        except Exception as e:
                            await send_telegram_message(f"Ошибка при отправке команды: {e}")
        except Exception as e:
            print(f"Ошибка при опросе Telegram: {e}")
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
