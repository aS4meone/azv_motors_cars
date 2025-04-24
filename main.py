import asyncio
import logging
import os
import re
from datetime import datetime

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Path, Query
from starlette.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from app.dependencies.database.database import SessionLocal
from app.glonassoft_api.glonass_auth import get_auth_token
from app.glonassoft_api.history_car import fetch_gps_coordinates_async
from app.glonassoft_api.last_car_data import get_vehicle_data, get_last_vehicles_data
from app.models.car_model import Vehicle
from app.router import router
from app.alerts import process_vehicle_notifications

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
token: str = None


def run_migrations():
    alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


def parse_numeric(value: str) -> float:
    if not value:
        return 0.0
    m = re.search(r'[-+]?\d*\.?\d+', value.replace(",", "."))
    return float(m.group()) if m else 0.0


def parse_int(value: str) -> int:
    return int(parse_numeric(value))


def parse_datetime(dt_str: str) -> datetime:
    if dt_str.endswith("Z"):
        dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)


def extract_from_items(items: list[dict], key_name: str) -> str:
    for item in items:
        if item.get("name", "").lower() == key_name.lower():
            return item.get("value", "").strip()
    return ""


async def update_token():
    global token
    token = await get_auth_token("https://regions.glonasssoft.ru", "%CLIENT", "12345678")
    logger.info("Token updated")


async def update_vehicles():
    db: Session = SessionLocal()
    try:
        vehicles = db.query(Vehicle).all()
        notifications = []
        ids = [v.vehicle_id for v in vehicles]

        for v in vehicles:
            try:
                data = await get_vehicle_data(token, v.vehicle_imei)
                v.last_update_sensors = parse_datetime(data.get("lastactivetime", ""))

                pkg = data.get("PackageItems", [])
                regs = data.get("RegistredSensors", [])
                unregs = data.get("UnregisteredSensors", [])

                # Гео/скорость
                v.latitude = parse_numeric(extract_from_items(pkg, "Широта"))
                v.longitude = parse_numeric(extract_from_items(pkg, "Долгота"))
                v.altitude = parse_numeric(extract_from_items(pkg, "Высота над уровнем моря"))
                v.course = parse_numeric(extract_from_items(pkg, "Курс"))
                v.speed = parse_numeric(extract_from_items(pkg, "Скорость"))
                v.engine_hours = parse_numeric(extract_from_items(pkg, "engine_hours"))

                # Пробег
                v.mileage = parse_numeric(extract_from_items(regs, "Датчик пробега (CAN-шина[5])"))

                # RPM и состояние двигателя
                v.rpm = parse_int(extract_from_items(regs, "Обороты двигателя (CAN-шина[3])"))
                v.is_engine_on = v.rpm >= 1

                # Температура
                temp = extract_from_items(regs, "Температура двигателя (CAN-шина[4])")
                v.engine_temperature = parse_numeric(temp) if temp and temp.lower() != "данных нет" else None

                # Капот
                hood = extract_from_items(unregs, "CanSafetyFlags_hood")
                v.is_hood_open = hood.lower() == "true"

                # Топливо
                v.fuel_level = parse_numeric(extract_from_items(regs, "Уровень топлива (CAN-шина[1])"))

                notifications.append(asyncio.create_task(
                    process_vehicle_notifications(data, v)
                ))
            except Exception as e:
                logger.error(f"Failed to update vehicle {v.vehicle_imei}: {e}")

        # Обновление координат батчем
        try:
            batch = await get_last_vehicles_data(token, ids)
            if batch:
                for item in batch:
                    rec = parse_datetime(item.get("recordTime", ""))
                    for v in vehicles:
                        if v.vehicle_id == item.get("vehicleId"):
                            v.last_update_coordinates = rec
                            break
        except Exception as e:
            logger.error(f"Batch update failed: {e}")

        db.commit()
        if notifications:
            await asyncio.gather(*notifications)
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating vehicles: {e}")
    finally:
        db.close()


async def continuous_update():
    while True:
        await update_vehicles()


def ensure_initial_vehicles():
    db = SessionLocal()
    try:
        defaults = [
            {"vehicle_id": 800212421, "vehicle_imei": "866011056074131", "name": "MB CLA45s"},
            {"vehicle_id": 800153076, "vehicle_imei": "866011056063951", "name": "Haval F7x"},
        ]
        for d in defaults:
            if not db.query(Vehicle).filter_by(vehicle_imei=d["vehicle_imei"]).first():
                db.add(Vehicle(**d))
        db.commit()
        logger.info(f"Initial vehicles updated")
    finally:
        db.close()


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(router)


@app.on_event("startup")
async def startup():
    run_migrations()
    # Инициализация машин
    ensure_initial_vehicles()

    # Токен и непрерывное обновление
    await update_token()
    asyncio.create_task(continuous_update())

    # Планировщик только для токена
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_token, 'interval', minutes=25)
    scheduler.start()


@app.get("/")
def root():
    return {"message": "OK"}


@app.get("/vehicles/{device_id}/gps")
async def get_gps(device_id: str = Path(...), start_date: str = Query(...), end_date: str = Query(...)):
    try:
        data = await fetch_gps_coordinates_async(device_id, start_date, end_date, token)
        if not data:
            raise HTTPException(status_code=404, detail="No GPS data")
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
