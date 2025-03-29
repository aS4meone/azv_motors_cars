import socket
import threading
import time
import struct
import json
import httpx

# Configuration (fill in actual values for bot and chat)
HOST = '0.0.0.0'
PORT = 12345
BOT_TOKEN = '7649836420:AAHJkjRAlMOe2NWqK_UIkYXlFBx07BCFXlY'
CHAT_ID = 965048905  # Telegram chat ID (целое число)
LOG_FILE = "telemetry.log"

# Global variables for sharing state between threads
current_connection = None
current_connection_lock = threading.Lock()
device_bitmask = None  # list of booleans representing active fields from FLEX mask
record_length = None  # length of one telemetry record (bytes), computed after bitmask known
device_ids = {}  # store device_id and server_id from handshake for sending commands


# Logging utility
def log_message(direction, data_bytes):
    """Log incoming/outgoing messages in hex format to file."""
    hex_str = data_bytes.hex(' ', 1)
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} [{direction}] {hex_str}\n")


# CRC8 calculation for FLEX messages (полином 0x31, начальное значение 0x00)
def calc_crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc & 0xFF


# XOR checksum for NTCB header/data
def xor_checksum(data: bytes) -> int:
    cs = 0
    for b in data:
        cs ^= b
    return cs & 0xFF


# Parse bitmask bytes into list of booleans (True for field present)
def parse_bitmask(bitmask_bytes: bytes, bit_count: int):
    bits = []
    for i in range(bit_count):
        byte_index = i // 8
        bit_index = i % 8
        if byte_index < len(bitmask_bytes):
            bit_val = (bitmask_bytes[byte_index] >> bit_index) & 1
            bits.append(bool(bit_val))
        else:
            bits.append(False)
    return bits


# Build NTCB (16-byte header + payload) for sending server->device commands
def build_ntcb_packet(recipient_id: int, sender_id: int, payload: bytes) -> bytes:
    preamble = b'@NTC'
    idr_bytes = struct.pack('<I', recipient_id)
    ids_bytes = struct.pack('<I', sender_id)
    n_bytes = struct.pack('<H', len(payload))
    csd_val = xor_checksum(payload)
    csd_byte = struct.pack('B', csd_val)
    header_15 = preamble + idr_bytes + ids_bytes + n_bytes + csd_byte
    csp_val = xor_checksum(header_15)
    header = header_15 + struct.pack('B', csp_val)
    return header + payload


# Отправка текстового сообщения в Telegram чат
def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}
    try:
        httpx.post(url, data=data, timeout=10)
    except Exception as e:
        # Log error but do not raise
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [ERROR] Failed to send Telegram message: {e}\n")


# Функция опроса обновлений Telegram для приёма команд *! от пользователя
def poll_telegram_commands():
    update_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    last_update_id = 0
    while True:
        try:
            resp = httpx.get(update_url, params={"offset": last_update_id + 1, "timeout": 30}, timeout=35)
            data = resp.json()
        except Exception as e:
            with open(LOG_FILE, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [ERROR] Telegram polling failed: {e}\n")
            time.sleep(5)
            continue
        if not data.get("ok"):
            time.sleep(5)
            continue
        for upd in data.get("result", []):
            last_update_id = max(last_update_id, upd.get("update_id", 0))
            msg = upd.get("message")
            if msg and "text" in msg:
                text = msg["text"].strip()
                if text.startswith("*!"):
                    # Received command to send to device
                    with current_connection_lock:
                        conn = current_connection
                    if conn:
                        # Build and send command packet to device
                        cmd_bytes = text.encode('ascii')
                        dev_id = device_ids.get("device_id", 0)
                        srv_id = device_ids.get("server_id", 1)
                        packet = build_ntcb_packet(recipient_id=dev_id, sender_id=srv_id, payload=cmd_bytes)
                        try:
                            conn.sendall(packet)
                            log_message("OUT", packet)
                            send_telegram_message(f"Команда «{text}» отправлена устройству.")
                        except Exception as e:
                            with open(LOG_FILE, "a") as f:
                                f.write(
                                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} [ERROR] Failed to send command to device: {e}\n")
                            send_telegram_message(f"Ошибка: устройство недоступно, команда «{text}» не отправлена.")
                    else:
                        send_telegram_message("Устройство не подключено. Команда не отправлена.")


# Функция для расшифровки одного телеметрического пакета (записи) по сохраненной маске FLEX
def decode_telemetry_record(record_bytes: bytes):
    result = {}
    idx = 0
    for bit_index, active in enumerate(device_bitmask, start=1):
        if not active:
            continue
        # Каждому активному полю сопоставляем размер и формат
        if bit_index == 1:  # Сквозной номер записи (U32)
            if idx + 4 <= len(record_bytes):
                result["msg_number"] = struct.unpack('<I', record_bytes[idx:idx + 4])[0]
            idx += 4
        elif bit_index == 2:  # Код события (U16)
            if idx + 2 <= len(record_bytes):
                result["event_code"] = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
            idx += 2
        elif bit_index == 3:  # Время события (U32, Unix time)
            if idx + 4 <= len(record_bytes):
                ts = struct.unpack('<I', record_bytes[idx:idx + 4])[0]
                try:
                    result["event_time_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))
                except:
                    result["event_time_utc"] = str(ts)
            idx += 4
        elif bit_index == 4:  # Статус устройства (1 байт)
            if idx + 1 <= len(record_bytes):
                status = record_bytes[idx]
                result["test_mode"] = bool(status & 0x01)  # бит0: тестовый режим
                result["alarm_notify"] = bool(status & 0x02)  # бит1: оповещение о тревоге
                result["alarm_active"] = bool(status & 0x04)  # бит2: тревога
                mode_val = (status >> 3) & 0x03  # биты3-4: режим работы
                mode_map = {0: "наблюдение", 1: "охрана", 2: "доп. охрана", 3: "сервис"}
                result["work_mode"] = mode_map.get(mode_val, mode_val)
                result["evacuation"] = bool(status & 0x20)  # бит5: эвакуация
                result["power_save"] = bool(status & 0x40)  # бит6: энергосбережение
                result["accel_calibrated"] = bool(status & 0x80)  # бит7: акселерометр откалиброван
            idx += 1
        elif bit_index == 5:  # Статус функциональных модулей 1 (1 байт)
            if idx + 1 <= len(record_bytes):
                mod1 = record_bytes[idx]
                result["gsm_on"] = bool(mod1 & 0x01)  # GSM модем включен
                result["usb_on"] = bool(mod1 & 0x02)  # USB включен
                result["gps_high_precision"] = bool(mod1 & 0x04)  # высокоточный приемник
                result["gps_time_sync"] = bool(mod1 & 0x08)  # часы синхронизированы по GPS
                result["sim2_active"] = bool(mod1 & 0x10)  # вторая SIM активна
                result["gsm_registered"] = bool(mod1 & 0x20)  # регистрация в сети
                result["roaming"] = bool(mod1 & 0x40)  # роуминг
                result["engine_running"] = bool(mod1 & 0x80)  # двигатель запущен
            idx += 1
        elif bit_index == 6:  # Статус функциональных модулей 2 (1 байт)
            if idx + 1 <= len(record_bytes):
                mod2 = record_bytes[idx]
                result["gsm_jamming_detected"] = bool(mod2 & 0x01)  # глушение GSM
                result["industrial_interference"] = bool(mod2 & 0x02)  # промышленные помехи
                result["gnss_jamming_detected"] = bool(mod2 & 0x04)  # глушение GNSS
                result["gnss_averaging"] = bool(mod2 & 0x08)  # усреднение координат
                result["accel_error"] = bool(mod2 & 0x10)  # ошибка акселерометра
                result["bluetooth_on"] = bool(mod2 & 0x20)  # Bluetooth включен
                result["wifi_on"] = bool(mod2 & 0x40)  # Wi-Fi включен
                result["rtc_source_internal"] = bool(mod2 & 0x80)  # источник тактирования RTC: True=внутренний
            idx += 1
        elif bit_index == 7:  # Уровень GSM (1 байт)
            if idx + 1 <= len(record_bytes):
                level = record_bytes[idx]
                if level == 99:
                    result["gsm_signal_percent"] = None
                    result["gsm_signal_status"] = "нет сети"
                else:
                    percent = int(level * 100 / 31) if level <= 31 else 100
                    result["gsm_signal_percent"] = percent
            idx += 1
        elif bit_index == 8:  # Состояние GPS/ГЛОНАСС (1 байт)
            if idx + 1 <= len(record_bytes):
                nav = record_bytes[idx]
                result["gps_receiver_on"] = bool(nav & 0x01)  # приемник включен
                result["gps_valid_fix"] = bool(nav & 0x02)  # навигация валидна
                result["satellites"] = (nav >> 2) & 0x3F  # количество спутников
            idx += 1
        elif bit_index == 9:  # Время последних валидных координат (U32)
            if idx + 4 <= len(record_bytes):
                ts = struct.unpack('<I', record_bytes[idx:idx + 4])[0]
                try:
                    result["last_valid_time_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))
                except:
                    result["last_valid_time_utc"] = str(ts)
            idx += 4
        elif bit_index == 10:  # Последняя валидная широта (I32, в 1e-4 минуты)
            if idx + 4 <= len(record_bytes):
                lat_raw = struct.unpack('<i', record_bytes[idx:idx + 4])[0]
                result["latitude"] = round(lat_raw / 600000.0, 6)
            idx += 4
        elif bit_index == 11:  # Последняя валидная долгота (I32, в 1e-4 минуты)
            if idx + 4 <= len(record_bytes):
                lon_raw = struct.unpack('<i', record_bytes[idx:idx + 4])[0]
                result["longitude"] = round(lon_raw / 600000.0, 6)
            idx += 4
        elif bit_index == 12:  # Последняя валидная высота (I32, дм)
            if idx + 4 <= len(record_bytes):
                alt_raw = struct.unpack('<i', record_bytes[idx:idx + 4])[0]
                result["altitude_m"] = alt_raw / 10.0
            idx += 4
        elif bit_index == 13:  # Скорость (Float, км/ч)
            if idx + 4 <= len(record_bytes):
                speed = struct.unpack('<f', record_bytes[idx:idx + 4])[0]
                result["speed_kmh"] = round(speed, 2)
            idx += 4
        elif bit_index == 14:  # Курс (U16, градусы)
            if idx + 2 <= len(record_bytes):
                result["course_deg"] = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
            idx += 2
        elif bit_index == 15:  # Текущий пробег (Float, км)
            if idx + 4 <= len(record_bytes):
                mileage = struct.unpack('<f', record_bytes[idx:idx + 4])[0]
                result["mileage_km"] = round(mileage, 3)
            idx += 4
        elif bit_index == 16:  # Последний отрезок пути (Float, км)
            if idx + 4 <= len(record_bytes):
                segment = struct.unpack('<f', record_bytes[idx:idx + 4])[0]
                result["segment_distance_km"] = round(segment, 3)
            idx += 4
        elif bit_index == 17:  # Общее число секунд на последнем отрезке (U16)
            if idx + 2 <= len(record_bytes):
                result["segment_time_s"] = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
            idx += 2
        elif bit_index == 18:  # Число валидных секунд на последнем отрезке (U16)
            if idx + 2 <= len(record_bytes):
                result["segment_valid_time_s"] = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
            idx += 2
        elif bit_index == 19:  # Напряжение основного питания (U16, мВ)
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["pwr_ext_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 20:  # Напряжение резервного питания (U16, мВ)
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["pwr_int_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 21:  # Ain1 (U16, мВ)
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["adc1_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 22:  # Ain2
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["adc2_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 23:  # Ain3
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["adc3_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 24:  # Ain4
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["adc4_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 25:  # Ain5
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["adc5_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 26:  # Ain6
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["adc6_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 27:  # Ain7
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["adc7_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 28:  # Ain8
            if idx + 2 <= len(record_bytes):
                mv = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                result["adc8_V"] = round(mv / 1000.0, 3)
            idx += 2
        elif bit_index == 29:  # Дискретные датчики 1-8 (U8)
            if idx + 1 <= len(record_bytes):
                din1 = record_bytes[idx]
                result["in1"] = bool(din1 & 0x01)
                result["in2"] = bool(din1 & 0x02)
                result["in3"] = bool(din1 & 0x04)
                result["in4"] = bool(din1 & 0x08)
                result["in5"] = bool(din1 & 0x10)
                result["in6"] = bool(din1 & 0x20)
                result["in7"] = bool(din1 & 0x40)
                result["in8"] = bool(din1 & 0x80)
            idx += 1
        elif bit_index == 30:  # Дискретные датчики 9-16 (U8)
            if idx + 1 <= len(record_bytes):
                din2 = record_bytes[idx]
                result["in9"] = bool(din2 & 0x01)
                result["in10"] = bool(din2 & 0x02)
                result["in11"] = bool(din2 & 0x04)
                result["in12"] = bool(din2 & 0x08)
                result["in13"] = bool(din2 & 0x10)
                result["in14"] = bool(din2 & 0x20)
                result["in15"] = bool(din2 & 0x40)
                result["in16"] = bool(din2 & 0x80)
            idx += 1
        elif bit_index == 31:  # Выходы 1-8 (U8)
            if idx + 1 <= len(record_bytes):
                out1 = record_bytes[idx]
                result["out1"] = bool(out1 & 0x01)
                result["out2"] = bool(out1 & 0x02)
                result["out3"] = bool(out1 & 0x04)
                result["out4"] = bool(out1 & 0x08)
                result["out5"] = bool(out1 & 0x10)
                result["out6"] = bool(out1 & 0x20)
                result["out7"] = bool(out1 & 0x40)
                result["out8"] = bool(out1 & 0x80)
            idx += 1
        elif bit_index == 32:  # Выходы 9-16 (U8)
            if idx + 1 <= len(record_bytes):
                out2 = record_bytes[idx]
                result["out9"] = bool(out2 & 0x01)
                result["out10"] = bool(out2 & 0x02)
                result["out11"] = bool(out2 & 0x04)
                result["out12"] = bool(out2 & 0x08)
                result["out13"] = bool(out2 & 0x10)
                result["out14"] = bool(out2 & 0x20)
                result["out15"] = bool(out2 & 0x40)
                result["out16"] = bool(out2 & 0x80)
            idx += 1
        elif bit_index == 33:  # Счетчик импульсов 1 (U32)
            if idx + 4 <= len(record_bytes):
                result["imp_counter1"] = struct.unpack('<I', record_bytes[idx:idx + 4])[0]
            idx += 4
        elif bit_index == 34:  # Счетчик импульсов 2 (U32)
            if idx + 4 <= len(record_bytes):
                result["imp_counter2"] = struct.unpack('<I', record_bytes[idx:idx + 4])[0]
            idx += 4
        elif bit_index == 35:  # Частота датчика 1 (U16, Гц)
            if idx + 2 <= len(record_bytes):
                result["freq1_hz"] = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
            idx += 2
        elif bit_index == 36:  # Частота датчика 2 (U16, Гц)
            if idx + 2 <= len(record_bytes):
                result["freq2_hz"] = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
            idx += 2
        elif bit_index == 37:  # Моточасы (U32, сек)
            if idx + 4 <= len(record_bytes):
                sec = struct.unpack('<I', record_bytes[idx:idx + 4])[0]
                result["engine_hours_h"] = round(sec / 3600.0, 1)
            idx += 4
        elif bit_index == 38:  # ДУТ1 уровень топлива (U16)
            if idx + 2 <= len(record_bytes):
                val = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                if val >= 65500:
                    result["fuel_level1_error_code"] = val
                else:
                    result["fuel_level1_raw"] = val
            idx += 2
        elif bit_index == 39:  # ДУТ2
            if idx + 2 <= len(record_bytes):
                val = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                if val >= 65500:
                    result["fuel_level2_error_code"] = val
                else:
                    result["fuel_level2_raw"] = val
            idx += 2
        elif bit_index == 40:  # ДУТ3
            if idx + 2 <= len(record_bytes):
                val = struct.unpack('<H', record_bytes[idx:idx + 2])[0]
                if val >= 65500:
                    result["fuel_level3_error_code"] = val
                else:
                    result["fuel_level3_raw"] = val
            idx += 2
        else:
            # Если битовая маска содержит поля >40 (FLEX 2.0 доп. пакеты), пропускаем (не реализовано подробно)
            break
    return result


# Обработка подключения устройства
def handle_client(conn, addr):
    global current_connection, device_bitmask, record_length, device_ids
    with current_connection_lock:
        current_connection = conn
    conn.settimeout(10.0)
    try:
        # Шаг 1: Прием пакета рукопожатия (handshake)
        header = conn.recv(16)
        if len(header) < 16 or header[:4] != b'@NTC':
            return
        preamble = header[:4]
        idr = struct.unpack('<I', header[4:8])[0]
        ids = struct.unpack('<I', header[8:12])[0]
        n = struct.unpack('<H', header[12:14])[0]
        csd = header[14]
        csp = header[15]
        # Проверка целостности заголовка
        if xor_checksum(header[:15]) != csp:
            log_message("WARN", b"Handshake header checksum mismatch")
            return
        # Получение данных рукопожатия
        handshake_data = b''
        if n > 0:
            while len(handshake_data) < n:
                chunk = conn.recv(n - len(handshake_data))
                if not chunk:
                    break
                handshake_data += chunk
        if len(handshake_data) < n:
            return
        if xor_checksum(handshake_data) != csd:
            log_message("WARN", b"Handshake data checksum mismatch")
        log_message("IN", header + handshake_data)
        # Извлечение идентификатора устройства (IMEI) из handshake-пакета
        try:
            handshake_text = handshake_data.decode('ascii', errors='ignore')
        except:
            handshake_text = ""
        if handshake_text.startswith("*>S:"):
            device_id_str = handshake_text[4:].strip('*\x00')
        else:
            device_id_str = None
        device_ids["device_id"] = ids  # sender (device) ID
        device_ids["server_id"] = idr  # recipient (server) ID
        # Шаг 2: Отправка подтверждения рукопожатия
        resp_payload = b'*<S'
        resp_packet = build_ntcb_packet(recipient_id=ids, sender_id=idr, payload=resp_payload)
        conn.sendall(resp_packet)
        log_message("OUT", resp_packet)
        # Шаг 3: Прием битовой маски параметров FLEX (протокол/версия)
        header2 = conn.recv(16)
        if len(header2) < 16 or header2[:4] != b'@NTC':
            return
        idr2 = struct.unpack('<I', header2[4:8])[0]
        ids2 = struct.unpack('<I', header2[8:12])[0]
        n2 = struct.unpack('<H', header2[12:14])[0]
        csd2 = header2[14]
        csp2 = header2[15]
        if xor_checksum(header2[:15]) != csp2:
            log_message("WARN", b"Bitmask header checksum mismatch")
            return
        bitmask_data = b''
        if n2 > 0:
            while len(bitmask_data) < n2:
                chunk = conn.recv(n2 - len(bitmask_data))
                if not chunk:
                    break
                bitmask_data += chunk
        if len(bitmask_data) < n2:
            return
        if xor_checksum(bitmask_data) != csd2:
            log_message("WARN", b"Bitmask data checksum mismatch")
        log_message("IN", header2 + bitmask_data)
        # Разбор сообщения с битовой маской FLEX
        if bitmask_data.startswith(b'*<FLEX') and len(bitmask_data) >= 10:
            protocol_symbol = bitmask_data[6]
            protocol_version = bitmask_data[7]
            struct_version = bitmask_data[8]
            data_size = bitmask_data[9]
            bitmask_bytes = bitmask_data[10:]
            if data_size == 0:
                data_size = len(bitmask_bytes) * 8
            # Список активных полей
            device_bitmask = parse_bitmask(bitmask_bytes, data_size)
            # Подсчет длины одной записи телеметрии в байтах по активным полям
            size_map = {
                1: 4, 2: 2, 3: 4, 4: 1, 5: 1, 6: 1, 7: 1, 8: 1, 9: 4, 10: 4, 11: 4, 12: 4, 13: 4,
                14: 2, 15: 4, 16: 4, 17: 2, 18: 2, 19: 2, 20: 2, 21: 2, 22: 2, 23: 2, 24: 2, 25: 2,
                26: 2, 27: 2, 28: 2, 29: 1, 30: 1, 31: 1, 32: 1, 33: 4, 34: 4, 35: 2, 36: 2, 37: 4,
                38: 2, 39: 2, 40: 2
            }
            rec_len = 0
            for bit_idx, flag in enumerate(device_bitmask, start=1):
                if flag:
                    if bit_idx in size_map:
                        rec_len += size_map[bit_idx]
                    else:
                        # Останавливаем расчет при неизвестном поле (за границами основной структуры)
                        break
            record_length = rec_len
        # Шаг 4: Отправка подтверждения битовой маски FLEX (отправляем тот же пакет обратно)
        resp_packet2 = build_ntcb_packet(recipient_id=ids2, sender_id=idr2, payload=bitmask_data)
        conn.sendall(resp_packet2)
        log_message("OUT", resp_packet2)
        # Переход к приему телеметрических сообщений FLEX (без 16-байтового заголовка)
        conn.settimeout(None)
        buffer = b''
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buffer += data
            # Разбор всех полноценных сообщений в буфере
            while True:
                if len(buffer) == 0:
                    break
                first_byte = buffer[0]
                if first_byte == 0x7F:  # ping message (0x7F)
                    buffer = buffer[1:]
                    continue
                if first_byte != 0x7E:  # not '~', discard until next potential message
                    idx = buffer.find(b'\x7E')
                    buffer = buffer[idx:] if idx != -1 else b''
                    continue
                # We have a potential message starting at '~'
                if len(buffer) < 2:
                    break  # wait for message type
                msg_type = buffer[1:2]  # second byte
                if msg_type == b'A':  # ~A (array of telemetry records)
                    if len(buffer) < 3:
                        break
                    count = buffer[2]
                    if record_length is None:
                        break
                    total_len = 2 + 1 + record_length * count + 1
                    if len(buffer) < total_len:
                        break
                    msg_bytes = buffer[:total_len]
                    buffer = buffer[total_len:]
                    # Проверка CRC8
                    if calc_crc8(msg_bytes[:-1]) != msg_bytes[-1]:
                        log_message("WARN", b"CRC8 mismatch for ~A")
                        continue
                    # Разбор пакета ~A: содержит count записей подряд
                    records_data = msg_bytes[3:-1]
                    for i in range(count):
                        rec_bytes = records_data[i * record_length: (i + 1) * record_length]
                        rec = decode_telemetry_record(rec_bytes)
                        json_text = json.dumps(rec, ensure_ascii=False, indent=2)
                        print(json_text)
                        send_telegram_message(json_text)
                elif msg_type == b'C':  # ~C (state packet)
                    if record_length is None:
                        break
                    total_len = 2 + record_length + 1
                    if len(buffer) < total_len:
                        break
                    msg_bytes = buffer[:total_len]
                    buffer = buffer[total_len:]
                    if calc_crc8(msg_bytes[:-1]) != msg_bytes[-1]:
                        log_message("WARN", b"CRC8 mismatch for ~C")
                        continue
                    rec_bytes = msg_bytes[2:-1]
                    rec = decode_telemetry_record(rec_bytes)
                    json_text = json.dumps(rec, ensure_ascii=False, indent=2)
                    print(json_text)
                    send_telegram_message(json_text)
                elif msg_type == b'T':  # ~T (out-of-order telemetry with event index)
                    if record_length is None:
                        break
                    total_len = 2 + 4 + record_length + 1
                    if len(buffer) < total_len:
                        break
                    msg_bytes = buffer[:total_len]
                    buffer = buffer[total_len:]
                    if calc_crc8(msg_bytes[:-1]) != msg_bytes[-1]:
                        log_message("WARN", b"CRC8 mismatch for ~T")
                        continue
                    event_index = struct.unpack('<I', msg_bytes[2:6])[0]
                    rec_bytes = msg_bytes[6:-1]
                    rec = decode_telemetry_record(rec_bytes)
                    rec["event_index"] = event_index
                    json_text = json.dumps(rec, ensure_ascii=False, indent=2)
                    print(json_text)
                    send_telegram_message(json_text)
                elif msg_type == b'E':  # ~E (array of additional telemetry records)
                    if len(buffer) < 3:
                        break
                    count = buffer[2]
                    if record_length is None:
                        break
                    total_len = 2 + 1 + record_length * count + 1
                    if len(buffer) < total_len:
                        break
                    msg_bytes = buffer[:total_len]
                    buffer = buffer[total_len:]
                    if calc_crc8(msg_bytes[:-1]) != msg_bytes[-1]:
                        log_message("WARN", b"CRC8 mismatch for ~E")
                        continue
                    records_data = msg_bytes[3:-1]
                    for i in range(count):
                        rec_bytes = records_data[i * record_length: (i + 1) * record_length]
                        rec = decode_telemetry_record(rec_bytes)
                        json_text = json.dumps(rec, ensure_ascii=False, indent=2)
                        print(json_text)
                        send_telegram_message(json_text)
                elif msg_type == b'X':  # ~X (out-of-order additional telemetry with index)
                    if record_length is None:
                        break
                    total_len = 2 + 4 + record_length + 1
                    if len(buffer) < total_len:
                        break
                    msg_bytes = buffer[:total_len]
                    buffer = buffer[total_len:]
                    if calc_crc8(msg_bytes[:-1]) != msg_bytes[-1]:
                        log_message("WARN", b"CRC8 mismatch for ~X")
                        continue
                    event_index = struct.unpack('<I', msg_bytes[2:6])[0]
                    rec_bytes = msg_bytes[6:-1]
                    rec = decode_telemetry_record(rec_bytes)
                    rec["event_index"] = event_index
                    json_text = json.dumps(rec, ensure_ascii=False, indent=2)
                    print(json_text)
                    send_telegram_message(json_text)
                else:
                    break
    except Exception as e:
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [ERROR] Exception: {e}\n")
    finally:
        with current_connection_lock:
            current_connection = None
        conn.close()


# Запуск потока опроса команд Telegram
telegram_thread = threading.Thread(target=poll_telegram_commands, daemon=True)
telegram_thread.start()

# Запуск TCP-сервера для приема соединений от устройства
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(5)
    print(f"TCP сервер запущен на порту {PORT}")
    while True:
        client_conn, client_addr = server_socket.accept()
        threading.Thread(target=handle_client, args=(client_conn, client_addr), daemon=True).start()
