import socket
import httpx
import asyncio

TG_BOT_TOKEN = '7649836420:AAHJkjRAlMOe2NWqK_UIkYXlFBx07BCFXlY'
TG_CHAT_ID = '965048905'
TELEGRAM_API_URL = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'
TELEGRAM_UPDATES_URL = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates'

SERVER_HOST = '0.0.0.0'
SERVER_PORT = 12345


async def send_to_telegram(message):
    """Отправка сообщения в Telegram"""
    async with httpx.AsyncClient() as client:
        await client.post(TELEGRAM_API_URL, json={'chat_id': TG_CHAT_ID, 'text': message})


async def send_to_device(message):
    """Отправка текста на устройство"""
    try:
        with socket.create_connection(('127.0.0.1', SERVER_PORT), timeout=5) as sock:
            sock.sendall(message.encode())
            response = sock.recv(1024).decode(errors='ignore')
            await send_to_telegram(f'📡 Ответ от устройства: {response.strip()}')
    except Exception as e:
        await send_to_telegram(f'❌ Ошибка отправки: {e}')


async def start_server():
    """Запуск TCP-сервера для приема данных с устройства"""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((SERVER_HOST, SERVER_PORT))
    server_socket.listen(5)
    print('Сервер запущен и ожидает подключения...')

    loop = asyncio.get_running_loop()
    while True:
        client_socket, addr = await loop.run_in_executor(None, server_socket.accept)
        print(f'Подключено устройство: {addr}')
        raw_data = await loop.run_in_executor(None, client_socket.recv, 1024)

        try:
            data = raw_data.decode('utf-8', errors='ignore').strip()
            if data:
                await send_to_telegram(f'📡 Данные от устройства: {data}')
        except UnicodeDecodeError:
            await send_to_telegram(f"❌ Ошибка декодирования: {raw_data}")

        client_socket.close()


async def listen_telegram():
    """Мониторинг Telegram на новые сообщения"""
    last_update_id = None
    async with httpx.AsyncClient() as client:
        while True:
            try:
                params = {'offset': last_update_id + 1} if last_update_id else {}
                response = await client.get(TELEGRAM_UPDATES_URL, params=params)
                updates = response.json()

                for update in updates.get("result", []):
                    last_update_id = update["update_id"]
                    message = update.get("message", {}).get("text", "").strip()

                    if message:
                        await send_to_telegram(f'📤 Отправка на устройство: {message}')
                        await send_to_device(message)

            except Exception as e:
                print(f"Ошибка получения сообщений: {e}")

            await asyncio.sleep(3)


async def main():
    """Запуск сервера и слушателя Telegram"""
    await asyncio.gather(start_server(), listen_telegram())


if __name__ == '__main__':
    asyncio.run(main())
