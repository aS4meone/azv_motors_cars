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
from app.rented_cache import fetch_rented_plates
from app.router import router
from app.alerts import process_vehicle_notifications

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
token: str = None


def run_migrations():
    try:
        alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        print(f"Ошибка миграции БД: {e}")


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
    try:
        token = await get_auth_token("https://regions.glonasssoft.ru", "%CLIENT", "12345678")
        logger.info("Token updated")
    except Exception as e:
        logger.error(f"Error updating token: {e}")
        token = None


async def update_vehicles():
    # Если ещё нет токена (например, при первом запуске), обновляем его
    if not token:
        await update_token()

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
                
                # Отладка: выводим все данные
                print(f"\n=== Данные для {v.vehicle_imei} ({v.name}) ===")
                
                print("\n--- PackageItems ---")
                for item in pkg:
                    print(f"  {item.get('name', 'N/A')}: {item.get('value', 'N/A')}")
                
                print("\n--- RegistredSensors ---")
                for sensor in regs:
                    print(f"  {sensor.get('name', 'N/A')}: {sensor.get('value', 'N/A')}")
                
                print("\n--- UnregisteredSensors ---")
                for sensor in unregs:
                    print(f"  {sensor.get('name', 'N/A')}: {sensor.get('value', 'N/A')}")
                
                print("=" * 60)

                # — Гео —
                v.latitude = parse_numeric(extract_from_items(pkg, "Широта"))
                v.longitude = parse_numeric(extract_from_items(pkg, "Долгота"))
                v.altitude = parse_numeric(extract_from_items(pkg, "Высота над уровнем моря"))
                v.course = parse_numeric(extract_from_items(pkg, "Курс"))

                # — Скорость (PackageItems) с отладкой —
                raw_speed = extract_from_items(pkg, "Скорость")
                logger.debug(f"[Vehicle {v.vehicle_imei}] raw_speed (PackageItems): {raw_speed!r}")
                try:
                    v.speed = parse_numeric(raw_speed)
                except Exception as e:
                    logger.error(f"[Vehicle {v.vehicle_imei}] failed to parse speed {raw_speed!r}: {e}")
                    v.speed = None
                logger.debug(f"[Vehicle {v.vehicle_imei}] parsed v.speed = {v.speed}")

                # — Пробег (CAN-шина[5]) —
                v.mileage = parse_numeric(extract_from_items(regs, "Датчик пробега (CAN-шина[5])"))

                # — RPM и состояние двигателя —
                v.rpm = parse_int(extract_from_items(regs, "Обороты двигателя (CAN-шина[3])"))
                v.is_engine_on = v.rpm >= 1

                # — Температура двигателя —
                temp = extract_from_items(regs, "Температура двигателя (CAN-шина[4])")
                v.engine_temperature = parse_numeric(temp) if temp and temp.lower() != "данных нет" else None

                # — Капот (RegisteredSensors) с отладкой —
                raw_hood = extract_from_items(regs, "Капот (Дискретный[0])")
                logger.debug(f"[Vehicle {v.vehicle_imei}] raw_hood status (RegisteredSensors): {raw_hood!r}")
                v.is_hood_open = bool(raw_hood and raw_hood.lower() == "открыт")
                logger.debug(f"[Vehicle {v.vehicle_imei}] determined is_hood_open = {v.is_hood_open}")

                # — Уровень топлива (CAN-шина[1]) — обновляем всегда когда доступны данные —
                raw_fuel = extract_from_items(regs, "Уровень топлива (CAN-шина[1])")
                if raw_fuel and raw_fuel.lower() not in ["данных нет", "нет данных", ""]:  # Обновляем только если есть валидные данные о топливе
                    try:
                        v.fuel_level = parse_numeric(raw_fuel)
                        logger.debug(f"[Vehicle {v.vehicle_imei}] updated fuel level: {v.fuel_level}")
                    except Exception as e:
                        logger.error(f"[Vehicle {v.vehicle_imei}] failed to parse fuel level {raw_fuel!r}: {e}")
                        # оставляем предыдущий уровень топлива
                elif v.is_engine_on:
                    # Если двигатель работает, но данных о топливе нет, логируем это
                    logger.debug(f"[Vehicle {v.vehicle_imei}] engine is on but no fuel data available")

                # — Создаём уведомление по данным машины —
                notifications.append(asyncio.create_task(
                    process_vehicle_notifications(data, v)
                ))

            except Exception as e:
                logger.error(f"Failed to update vehicle {v.vehicle_imei}: {e}")

        # — Обновление координат батчем —
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
        try:
            await update_vehicles()
        except Exception as e:
            logger.error(f"Error in continuous_update: {e}")
        await asyncio.sleep(30)  # Пауза между обновлениями


def ensure_initial_vehicles():
    db = SessionLocal()
    try:
        defaults = [
            {"vehicle_id": 800212421, "vehicle_imei": "869132074567851", "name": "MB CLA45s",
             "plate_number": "666AZV02"},
            {"vehicle_id": 800153076, "vehicle_imei": "866011056063951", "name": "Haval F7x",
             "plate_number": "422ABK02"},
        ]
        for d in defaults:
            existing_vehicle = db.query(Vehicle).filter_by(vehicle_id=d["vehicle_id"]).first()
            if not existing_vehicle:
                db.add(Vehicle(**d))
                logger.info(f"Added new vehicle: {d['name']} (ID: {d['vehicle_id']})")
            else:
                # Обновляем существующую запись
                existing_vehicle.vehicle_imei = d["vehicle_imei"]
                existing_vehicle.name = d["name"]
                existing_vehicle.plate_number = d["plate_number"]
                logger.info(f"Updated existing vehicle: {d['name']} (ID: {d['vehicle_id']}, IMEI: {d['vehicle_imei']})")
        db.commit()
        logger.info(f"Initial vehicles updated")
    except Exception as e:
        logger.error(f"Error initializing vehicles: {e}")
        db.rollback()
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
    await fetch_rented_plates()

    # Планировщик только для токена
    scheduler = AsyncIOScheduler()
    scheduler.add_job(fetch_rented_plates, "interval", seconds=60)
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
