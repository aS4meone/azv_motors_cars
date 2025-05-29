import re
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

from app.core.config import POLYGON_COORDS
from app.notifications import send_telegram_message
from app.models.car_model import Vehicle

# Кэш отправленных алертов
alert_cache: Dict[str, datetime] = {}


def parse_numeric(value: str) -> float:
    if not value:
        return 0.0
    m = re.search(r"[-+]?\d*\.?\d+", value.replace(",", "."))
    return float(m.group()) if m else 0.0


def parse_int(value: str) -> int:
    return int(parse_numeric(value))


def extract_from_items(items: List[Dict], key_name: str) -> str:
    for item in items:
        if item.get("name", "").lower() == key_name.lower():
            return item.get("value", "").strip()
    return ""


def is_point_inside_polygon(lat: float, lon: float, polygon: List[Tuple[float, float]]) -> bool:
    inside = False
    x, y = lon, lat
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def clean_cache():
    now = datetime.utcnow()
    to_del = [k for k, t in alert_cache.items() if now - t > timedelta(minutes=5)]
    for k in to_del:
        del alert_cache[k]


def should_alert(imei: str, alert_type: str) -> bool:
    clean_cache()
    key = f"{imei}:{alert_type}"
    if key in alert_cache:
        return False
    alert_cache[key] = datetime.utcnow()
    return True


async def process_vehicle_notifications(data: Dict, vehicle: Vehicle):
    imei = vehicle.vehicle_imei
    name = vehicle.name
    alerts: List[str] = []

    def maybe(cond: bool, atype: str, msg: str):
        if cond and should_alert(imei, atype):
            alerts.append(msg)

    # Маппинг описаний для accel-датчиков
    sensor_map = {
        "Accel_SH1": "слабый удар",
        "Accel_SH2": "сильный удар",
        "Accel_SH3": "перемещение",
        "Accel_SH4": "наклон",
    }

    pkg = data.get("PackageItems", [])
    regs = data.get("RegistredSensors", [])
    unregs = data.get("UnregisteredSensors", [])

    # — Скорость из PackageItems —
    raw_speed = extract_from_items(pkg, "Скорость")
    speed = parse_numeric(raw_speed)
    maybe(speed >= 100, "overspeed", f"{name}: Превышение скорости {speed} км/ч")

    # — Обороты двигателя из RegisteredSensors —
    raw_rpm = extract_from_items(regs, "Обороты двигателя (CAN-шина[3])")
    rpm = parse_int(raw_rpm)
    maybe(rpm >= 4000, "rpm_high", f"{name}: Высокие обороты двигателя {rpm}")

    # — Температура двигателя из RegisteredSensors —
    raw_temp = extract_from_items(regs, "Температура двигателя (CAN-шина[4])")
    temp = parse_numeric(raw_temp)
    maybe(temp >= 100, "temp_high", f"{name}: Температура двигателя {temp}°C")

    # — Капот из RegisteredSensors —
    raw_hood = extract_from_items(regs, "Капот (Дискретный[0])")
    hood_open = raw_hood.lower() == "открыт"
    maybe(hood_open, "hood_open", f"{name}: Капот открыт")

    # — Accel_SH1–4 и Accel_WAKEUP —
    for item in unregs:
        raw_val = item.get("value", "")
        val_lower = raw_val.lower()
        # ищем 'true' и имя датчика в скобках
        if val_lower.startswith("true") and "(" in raw_val and ")" in raw_val:
            m = re.search(r"\(([^)]+)\)", raw_val)
            if not m:
                continue
            sensor = m.group(1)  # e.g. "Accel_SH2"
            if sensor in sensor_map:
                desc = sensor_map[sensor]
                maybe(True,
                      sensor.lower(),
                      f"{name}: {sensor} ({desc})")

    # — Выход за зону по GPS —
    lat = parse_numeric(extract_from_items(pkg, "Широта"))
    lon = parse_numeric(extract_from_items(pkg, "Долгота"))
    if lat and lon and not is_point_inside_polygon(lat, lon, POLYGON_COORDS):
        maybe(True, "zone_exit", f"{name}: Выход за зону ({lat}, {lon})")

    # — Отправка Telegram, если есть алерты —
    if alerts:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"🚗 Уведомления для {name}:"
        text = header + "\n" + "\n".join(alerts) + f"\n\n{ts}"
        await send_telegram_message(text)
