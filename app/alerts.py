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

    regs = data.get("RegistredSensors", [])
    unregs = data.get("UnregisteredSensors", [])

    # Скорость
    speed = parse_numeric(extract_from_items(regs, "Скорость"))
    maybe(speed >= 100, "overspeed", f"{name}: Превышение скорости {speed} км/ч")

    # Обороты
    rpm = parse_int(extract_from_items(regs, "Обороты двигателя"))
    maybe(rpm >= 4000, "rpm_high", f"{name}: Высокие обороты двигателя {rpm}")

    # Температура
    temp = parse_numeric(extract_from_items(regs, "Температура двигателя"))
    maybe(temp >= 100, "temp_high", f"{name}: Температура двигателя {temp}°C")

    # Капот по CAN-флагу
    hood_flag = extract_from_items(unregs, "CanSafetyFlags_hood")
    maybe(hood_flag.lower() == "true", "hood_open", f"{name}: Капот открыт")

    # Резкое ускорение/торможение
    overload = any(
        "accel_sh" in item.get("name", "").lower() and "true" in item.get("value", "").lower()
        for item in unregs
    )
    maybe(overload, "overload", f"{name}: Резкое ускорение/торможение")

    # Выход за зону
    pkg = data.get("PackageItems", [])
    lat = parse_numeric(extract_from_items(pkg, "Широта"))
    lon = parse_numeric(extract_from_items(pkg, "Долгота"))
    if lat and lon and not is_point_inside_polygon(lat, lon, POLYGON_COORDS):
        maybe(True, "zone_exit", f"{name}: Выход за зону ({lat}, {lon})")

    if alerts:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"🚗 Уведомления для {name}:"
        text = header + "\n" + "\n".join(alerts) + f"\n\n{ts}"
        await send_telegram_message(text)
