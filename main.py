import socket
import asyncio
import httpx

TG_BOT_TOKEN = 'your_telegram_token'
TG_CHAT_ID = 'your_chat_id'
TELEGRAM_API_URL = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'

# Храним активные соединения
devices = {}

async def send_to_telegram(message):
    """Отправка сообщения в Telegram"""
    async with httpx.AsyncClient() as client:
        await client.post(TELEGRAM_API_URL, json={'chat_id': TG_CHAT_ID, 'text': message})

async def handle_data(client_socket, addr):
    """Обработчик входящих данных"""
    device_ip, device_port = addr
    while True:
        try:
            data = await asyncio.get_running_loop().run_in_executor(None, client_socket.recv, 1024)
            if not data:
                break

            message = data.decode(errors='ignore').strip()
            await send_to_telegram(f'📡 Данные от {device_ip}:{device_port}: {message}')

            # Проверяем, если это авторизация
            if message.startswith('@NTC') and 'S:' in message:
                imei = message.split('S:')[-1].strip()
                devices[imei] = client_socket
                await send_to_telegram(f'✅ Устройство {imei} зарегистрировано!')

                # Подтверждаем соединение
                client_socket.sendall(b'@NTC OK\r\n')
                await send_to_telegram(f'📤 Подтверждение отправлено: @NTC OK')

        except Exception as e:
            await send_to_telegram(f'❌ Ошибка получения данных: {e}')
            break

    # Удаляем устройство при разрыве соединения
    for imei, sock in list(devices.items()):
        if sock == client_socket:
            del devices[imei]
            await send_to_telegram(f'❌ Устройство {imei} отключилось!')
            break
    client_socket.close()

async def send_to_device(message):
    """Отправка команды на устройство"""
    try:
        imei = '866011056063951'
        client_socket = devices.get(imei)

        if not client_socket:
            await send_to_telegram(f'❌ Устройство {imei} не подключено')
            return

        client_socket.sendall(f'{message}\r\n'.encode())
        await send_to_telegram(f'📤 Отправлено: {message}')

    except Exception as e:
        await send_to_telegram(f'❌ Ошибка отправки: {e}')

async def start_server():
    """Запуск TCP-сервера"""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('0.0.0.0', 12345))
    server_socket.listen(5)
    print('Сервер запущен...')

    loop = asyncio.get_running_loop()
    while True:
        client_socket, addr = await loop.run_in_executor(None, server_socket.accept)
        print(f'Подключено устройство: {addr}')
        asyncio.create_task(handle_data(client_socket, addr))

if __name__ == '__main__':
    asyncio.run(start_server())
