import asyncio
import logging
import re
from datetime import datetime, timedelta
from fastapi import FastAPI, Path, Query, HTTPException
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from starlette.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import httpx

from app.dependencies.database.database import SessionLocal, get_db
from app.glonassoft_api.glonass_auth import get_auth_token
from app.glonassoft_api.history_car import fetch_gps_coordinates_async
from app.glonassoft_api.last_car_data import get_vehicle_data, get_last_vehicles_data
from app.models.car_model import Vehicle
from app.core.config import POLYGON_COORDS  # Предполагается, что вы добавите
from app.router import router

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = "7649836420:AAHJkjRAlMOe2NWqK_UIkYXlFBx07BCFXlY"
TARGET_CHAT_ID = 965048905

# Глобальные переменные
token: str = None
token_last_update: datetime = None
alert_cache: dict[str, datetime] = {}


def parse_numeric_value(value: str) -> float:
    if not value:
        return 0.0
    value = value.replace(",", ".")
    match = re.search(r'[-+]?\d*\.?\d+', value)
    return float(match.group()) if match else 0.0


def parse_int_value(value: str) -> int:
    return int(parse_numeric_value(value))


def parse_datetime(dt_str: str) -> datetime:
    if dt_str.endswith("Z"):
        dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)


def extract_from_items(items: list, key_name: str) -> str:
    for item in items:
        if item.get("name", "").lower() == key_name.lower():
            return item.get("value", "").strip()
    return ""


def is_point_inside_polygon(lat: float, lon: float, polygon_coords: list) -> bool:
    num_vertices = len(polygon_coords)
    inside = False
    x, y = lon, lat
    j = num_vertices - 1
    for i in range(num_vertices):
        xi, yi = polygon_coords[i]
        xj, yj = polygon_coords[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def clean_alert_cache() -> None:
    now = datetime.utcnow()
    keys_to_remove = [k for k, ts in alert_cache.items() if now - ts > timedelta(minutes=5)]
    for k in keys_to_remove:
        del alert_cache[k]


def should_send_alert(vehicle_imei: str, alert_type: str) -> bool:
    clean_alert_cache()
    key = f"vehicle:{vehicle_imei}:{alert_type}"
    if key in alert_cache:
        return False
    alert_cache[key] = datetime.utcnow()
    return True


async def send_telegram_message(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TARGET_CHAT_ID, "text": message}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            logger.info("Telegram-уведомление отправлено.")
    except Exception as e:
        logger.error(f"Не удалось отправить Telegram-сообщение: {e}")


async def process_vehicle_notifications(data: dict) -> None:
    alerts = []
    imei = data.get("imei", "неизвестно")

    def maybe_alert(condition: bool, alert_type: str, message: str):
        if condition and should_send_alert(imei, alert_type):
            alerts.append(message)

    speed = None
    for group in ["GeneralSensors", "RegistredSensors"]:
        speed_str = extract_from_items(data.get(group, []), "Скорость")
        if speed_str:
            speed = parse_numeric_value(speed_str)
            break
    maybe_alert(speed is not None and speed >= 100, "overspeed", f"⚠️ Превышение скорости: {speed} км/ч")

    rpm = parse_int_value(extract_from_items(data.get("RegistredSensors", []), "Обороты двигателя"))
    maybe_alert(rpm >= 4000, "rpm_high", f"⚠️ Высокие обороты двигателя: {rpm} об/мин")

    temp_str = extract_from_items(data.get("RegistredSensors", []), "Температура двигателя")
    if temp_str and "данных нет" not in temp_str.lower():
        temp = parse_numeric_value(temp_str)
        maybe_alert(temp >= 100, "temp_high", f"⚠️ Высокая температура двигателя: {temp}°C")

    hood_str = extract_from_items(data.get("RegistredSensors", []), "Капот")
    maybe_alert(hood_str and "открыт" in hood_str.lower(), "hood_open", "⚠️ Капот открыт!")

    overload = any("accel_sh" in s.get("name", "").lower() and "true" in s.get("value", "").lower()
                   for s in data.get("UnregisteredSensors", []))
    maybe_alert(overload, "overload", "⚠️ Резкое ускорение/торможение!")

    lat = data.get("latitude")
    lon = data.get("longitude")
    out_of_bounds = lat and lon and not is_point_inside_polygon(lat, lon, POLYGON_COORDS)
    maybe_alert(out_of_bounds, "zone_exit", f"⚠️ Выход за зону! Координаты: {lat}, {lon}")

    if alerts:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        header = f"🚗 Внимание ({imei}) — {ts}\n\n"
        await send_telegram_message(header + "\n".join(alerts))


async def update_token() -> None:
    global token, token_last_update
    logger.info("Обновление токена...")
    token = await get_auth_token("https://regions.glonasssoft.ru", "%CLIENT", "12345678")
    token_last_update = datetime.utcnow()
    logger.info(f"Токен обновлён: {token}")


async def update_vehicles(current_token: str) -> None:
    db: Session = SessionLocal()
    notification_tasks = []
    try:
        vehicles = db.query(Vehicle).all()
        vehicle_ids = [v.vehicle_id for v in vehicles]
        logger.info(f"Обновление данных для {len(vehicles)} автомобилей")

        for vehicle in vehicles:
            logger.info(f"Получение данных для автомобиля: {vehicle.vehicle_imei}")
            data = await get_vehicle_data(current_token, vehicle.vehicle_imei)

            vehicle.last_update_sensors = parse_datetime(data.get("lastactivetime"))
            package_items = data.get("PackageItems", [])
            vehicle.longitude = parse_numeric_value(extract_from_items(package_items, "Долгота"))
            vehicle.latitude = parse_numeric_value(extract_from_items(package_items, "Широта"))
            vehicle.altitude = parse_numeric_value(extract_from_items(package_items, "Высота над уровнем моря"))
            vehicle.course = parse_numeric_value(extract_from_items(package_items, "Курс"))
            vehicle.speed = parse_numeric_value(extract_from_items(package_items, "Скорость"))
            vehicle.engine_hours = parse_numeric_value(extract_from_items(package_items, "engine_hours"))

            registered = data.get("RegistredSensors", [])
            vehicle.mileage = parse_numeric_value(extract_from_items(registered, "Датчик пробега (CAN-шина[5])"))
            vehicle.rpm = parse_int_value(extract_from_items(registered, "Обороты двигателя (CAN-шина[3])"))
            engine_temp_str = extract_from_items(registered, "Температура двигателя (CAN-шина[4])")
            vehicle.is_engine_on = engine_temp_str.lower() != "данных нет"
            vehicle.engine_temperature = parse_numeric_value(engine_temp_str) if vehicle.is_engine_on else None
            hood_state = extract_from_items(registered, "Капот (Дискретный[0])").lower()
            vehicle.is_hood_open = hood_state != "закрыт"
            vehicle.fuel_level = parse_numeric_value(extract_from_items(registered, "Уровень топлива (CAN-шина[1])"))

            notification_tasks.append(asyncio.create_task(process_vehicle_notifications(data)))

        last_data = await get_last_vehicles_data(current_token, vehicle_ids)
        for item in last_data:
            veh_id = item.get("vehicleId")
            record_time = parse_datetime(item.get("recordTime"))
            for vehicle in vehicles:
                if vehicle.vehicle_id == veh_id:
                    vehicle.longitude = float(item.get("longitude", 0))
                    vehicle.latitude = float(item.get("latitude", 0))
                    vehicle.last_update_coordinates = record_time

        db.commit()
        logger.info("Данные автомобилей успешно обновлены")

        if notification_tasks:
            await asyncio.gather(*notification_tasks)

    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при обновлении данных: {e}")
    finally:
        db.close()


async def scheduled_job() -> None:
    await update_vehicles(token)


async def continuous_vehicle_update() -> None:
    while True:
        await scheduled_job()
        logger.info("Обновление завершено, запускаем снова.")


scheduler = AsyncIOScheduler()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(router)


@app.on_event("startup")
async def startup_event() -> None:
    await update_token()
    app.state.vehicle_update_task = asyncio.create_task(continuous_vehicle_update())
    scheduler.add_job(update_token, 'interval', minutes=25)
    scheduler.start()
    logger.info("Сервер запущен и шедулер активен.")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    scheduler.shutdown()
    task = app.state.vehicle_update_task
    task.cancel()
    logger.info("Сервер остановлен, шедулер остановлен.")


@app.get("/")
def root() -> dict:
    return {"message": "Сервер работает"}


@app.get("/vehicles/{device_id}/gps", status_code=200)
async def get_gps_data(
        device_id: str = Path(..., description="Device ID of the vehicle"),
        start_date: str = Query(..., description="Start date in format YYYY-MM-DDThh:mm:ss"),
        end_date: str = Query(..., description="End date in format YYYY-MM-DDThh:mm:ss"),
):
    try:
        db = next(get_db())
        vehicle = db.query(Vehicle).filter(Vehicle.vehicle_id == device_id).first()
        if not vehicle:
            raise HTTPException(status_code=404, detail="Vehicle not found in database")

        print(token)
        result = await fetch_gps_coordinates_async(device_id, start_date, end_date, token)

        if not result:
            raise HTTPException(status_code=404, detail="No GPS data found for the specified period")

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch GPS data: {str(e)}")
