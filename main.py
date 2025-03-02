import socket
import httpx
import asyncio

TG_BOT_TOKEN = '7649836420:AAHJkjRAlMOe2NWqK_UIkYXlFBx07BCFXlY'
TG_CHAT_ID = '965048905'
TELEGRAM_API_URL = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'


async def send_to_telegram(message):
    async with httpx.AsyncClient() as client:
        await client.post(TELEGRAM_API_URL, json={'chat_id': TG_CHAT_ID, 'text': message})


async def handle_data(data):
    await send_to_telegram(f'\U0001F4E1 Необработанные данные: {data}')

    parts = data.split('#')
    if len(parts) < 3:
        return
    packet_type = parts[1]
    message = parts[2].strip()

    if packet_type == 'L':  # Пакет логина
        device_id = message.split(';')[0]
        parsed_data = f'\U0001F50C Устройство {device_id} подключено.'
    elif packet_type == 'SD':  # Сокращенный пакет с данными
        fields = message.split(';')
        if len(fields) < 8:
            return
        date, time, lat, lon, speed, course, height, sats = fields[:8]
        parsed_data = (f'\U0001F4CD GPS Данные:\nДата: {date}\nВремя: {time}\n'
                       f'Широта: {lat}\nДолгота: {lon}\nСкорость: {speed} км/ч\n'
                       f'Курс: {course}\nВысота: {height} м\nСпутники: {sats}')
    else:
        parsed_data = f'\U00002753 Неизвестный тип пакета: {packet_type}'

    await send_to_telegram(parsed_data)


async def start_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('0.0.0.0', 12345))
    server_socket.listen(5)
    print('Сервер запущен и ожидает подключения...')

    loop = asyncio.get_running_loop()
    while True:
        client_socket, addr = await loop.run_in_executor(None, server_socket.accept)
        print(f'Подключено устройство: {addr}')
        data = await loop.run_in_executor(None, client_socket.recv, 1024)
        data = data.decode('utf-8')
        await handle_data(data)
        client_socket.close()


if __name__ == '__main__':
    asyncio.run(start_server())
