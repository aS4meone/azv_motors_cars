import socket
import httpx
import asyncio
import re

TG_BOT_TOKEN = '7649836420:AAHJkjRAlMOe2NWqK_UIkYXlFBx07BCFXlY'
TG_CHAT_ID = '965048905'
TELEGRAM_API_URL = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'
TELEGRAM_UPDATES_URL = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates'

SERVER_HOST = '0.0.0.0'
SERVER_PORT = 12345

# Словарь для хранения IP и порта каждого устройства по их IMEI
devices = {}


async def send_to_telegram(message: str) -> None:
    """Отправка сообщения в Telegram"""
    async with httpx.AsyncClient() as client:
        await client.post(TELEGRAM_API_URL, json={'chat_id': TG_CHAT_ID, 'text': message})


async def send_to_device(imei: str, message: str) -> None:
    """Отправка текста на устройство по его IMEI"""
    device_info = devices.get(imei)
    if not device_info:
        await send_to_telegram(f'❌ Устройство с IMEI {imei} не найдено.')
        return

    device_ip, device_port = device_info
    try:
        with socket.create_connection((device_ip, device_port), timeout=5) as sock:
            sock.sendall(message.encode())
            response = sock.recv(1024).decode(errors='ignore')
            await send_to_telegram(f'📡 Ответ от устройства {imei}: {response.strip()}')
    except Exception as e:
        await send_to_telegram(f'❌ Ошибка отправки на устройство {imei}: {e}')


async def start_server() -> None:
    """Запуск TCP-сервера для приема данных с устройств"""
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
            if data.startswith('@NTC'):
                match = re.search(r'NE\*>S:(\d+)', data)
                if match:
                    imei = match.group(1)
                    devices[imei] = addr  # Сохраняем IP и порт устройства по его IMEI
                    await send_to_telegram(f'✅ Устройство {imei} зарегистрировано!')
                    ack_message = '@NTC OK'
                    client_socket.sendall(ack_message.encode())
                    await send_to_telegram(f'📤 Подтверждение отправлено: {ack_message}')
                else:
                    await send_to_telegram(f'❌ Не удалось извлечь IMEI из сообщения: {data}')
            else:
                await send_to_telegram(f'📡 Данные от {addr}: {data}')
        except UnicodeDecodeError:
            await send_to_telegram(f"❌ Ошибка декодирования: {raw_data}")

        client_socket.close()


async def listen_telegram() -> None:
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
                        # Ожидается, что сообщение будет в формате "IMEI: команда"
                        if ':' in message:
                            imei, command = map(str.strip, message.split(':', 1))
                            await send_to_telegram(f'📤 Отправка на устройство {imei}: {command}')
                            await send_to_device(imei, command)
                        else:
                            await send_to_telegram('❌ Неверный формат сообщения. Используйте "IMEI: команда".')

            except Exception as e:
                print(f"Ошибка получения сообщений: {e}")

            await asyncio.sleep(3)


async def main() -> None:
    """Запуск сервера и слушателя Telegram"""
    await asyncio.gather(start_server(), listen_telegram())


if __name__ == '__main__':
    asyncio.run(main())
