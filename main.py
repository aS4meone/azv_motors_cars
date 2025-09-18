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


def extract_first_match(items: list[dict], possible_keys: list[str]) -> str:
    """Return value for the first present key name (case-insensitive), else empty string."""
    lower_to_value = {item.get("name", "").lower(): item.get("value", "").strip() for item in items}
    for key in possible_keys:
        val = lower_to_value.get(key.lower())
        if val is not None and val != "":
            return val
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

                # — Пробег (универсальный поиск) —
                mileage_keys = ["Пробег (can97)", "Датчик пробега (can_mileage)"]
                for key in mileage_keys:
                    mileage_value = extract_from_items(regs, key)
                    if mileage_value:
                        v.mileage = parse_numeric(mileage_value)
                        break
                else:
                    v.mileage = 0.0

                # — RPM и состояние двигателя (универсальный поиск) —
                rpm_keys = ["Обороты двигателя (can101)", "Обороты двигателя (engine_rpm)"]
                rpm_value = None
                for key in rpm_keys:
                    rpm_value = extract_from_items(regs, key)
                    if rpm_value and rpm_value.lower() != "данных нет":
                        break
                
                if rpm_value and rpm_value.lower() != "данных нет":
                    v.rpm = parse_int(rpm_value)
                else:
                    v.rpm = 0
                v.is_engine_on = v.rpm >= 1

                # — Температура двигателя (универсальный поиск) —
                temp_keys = ["Температура двигателя (can102)", "Температура двигателя (engine_coolant_temp)"]
                temp_value = None
                for key in temp_keys:
                    temp_value = extract_from_items(regs, key)
                    if temp_value and temp_value.lower() != "данных нет":
                        break
                
                v.engine_temperature = parse_numeric(temp_value) if temp_value and temp_value.lower() != "данных нет" else None

                # — Капот (универсальный поиск) —
                hood_keys = ["Капот (can37)", "Капот (in0;iobits0)", "Капот (can34)"]
                raw_hood = None
                for key in hood_keys:
                    raw_hood = extract_from_items(regs, key)
                    if raw_hood:
                        break
                
                logger.debug(f"[Vehicle {v.vehicle_imei}] raw_hood status (RegisteredSensors): {raw_hood!r}")
                v.is_hood_open = bool(raw_hood and raw_hood.lower() == "открыт")
                logger.debug(f"[Vehicle {v.vehicle_imei}] determined is_hood_open = {v.is_hood_open}")

                # — Уровень топлива (универсальный поиск) —
                fuel_keys = ["Уровень топлива (can100)", "Уровень топлива (can_fuel_volume)"]
                raw_fuel = None
                for key in fuel_keys:
                    raw_fuel = extract_from_items(regs, key)
                    if raw_fuel and raw_fuel.lower() not in ["данных нет", "нет данных", ""]:
                        break
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

                # — Багажник —
                # Сначала ищем в RegistredSensors (для MB CLA45s, Hongqi e-qm5)
                trunk_val = extract_first_match(regs, ["Багажник (can35)", "Багажник (can38)"])
                if trunk_val:
                    v.is_trunk_open = bool(trunk_val.lower() == "открыт")
                else:
                    # Формат UnregisteredSensors: CanSafetyFlags_trunk = "True"/"False"
                    # False = багажник открыт, True = багажник закрыт
                    trunk_unreg = extract_first_match(unregs, ["CanSafetyFlags_trunk"])
                    v.is_trunk_open = trunk_unreg.lower() == "false" if trunk_unreg else True

                # — Стояночный/парковочный тормоз —
                # Сначала ищем в RegistredSensors (для MB CLA45s, Hongqi e-qm5)
                handbrake_val = extract_first_match(regs, [
                    "Стояночный тормоз (can41)",
                    "Парковочный тормоз (can43)",
                ])
                if handbrake_val:
                    v.is_handbrake_on = bool(handbrake_val.lower().startswith("вкл"))
                else:
                    # Формат UnregisteredSensors: CanSafetyFlags_handbrake = "True"/"False"
                    # False = ручник включен, True = ручник выключен
                    handbrake_unreg = extract_first_match(unregs, ["CanSafetyFlags_handbrake"])
                    v.is_handbrake_on = handbrake_unreg.lower() == "false" if handbrake_unreg else True

                # — Фары — (учтём «Фары» как общий признак и «Ближний свет»)
                lights_val = extract_first_match(regs, [
                    "Фары (can38)",
                    "Ближний свет (can41)",
                ])
                v.are_lights_on = bool(lights_val and (lights_val.lower().startswith("вкл") or lights_val.lower() == "включен"))

                # — Режим AUTO света —
                auto_light_val = extract_first_match(regs, [
                    "Режим света AUTO (can42)",
                    "Режим AUTO света",
                ])
                v.is_light_auto_mode_on = bool(auto_light_val and auto_light_val.lower().startswith("вкл"))

                # — Двери —
                # Сначала ищем в RegistredSensors (для MB CLA45s, Hongqi e-qm5)
                fr_door = extract_first_match(regs, ["ПП Дверь (can42)", "ПП Дверь", "passenger front door"])
                fl_door = extract_first_match(regs, ["ПЛ Дверь (can44)", "ПЛ Дверь", "driver front door"])
                rl_door = extract_first_match(regs, ["ЗЛ Дверь (can46)", "ЗЛ Дверь", "rear left door"])
                rr_door = extract_first_match(regs, ["ЗП Дверь (can48)", "ЗП Дверь", "rear right door"])
                
                if fr_door or fl_door or rl_door or rr_door:
                    # Формат RegistredSensors: "Открыта"/"Закрыта"
                    v.front_right_door_open = bool(fr_door and fr_door.lower() == "открыта")
                    v.front_left_door_open = bool(fl_door and fl_door.lower() == "открыта")
                    v.rear_left_door_open = bool(rl_door and rl_door.lower() == "открыта")
                    v.rear_right_door_open = bool(rr_door and rr_door.lower() == "открыта")
                else:
                    # Формат UnregisteredSensors: CanSafetyFlags_* = "True"/"False"
                    # False = дверь открыта, True = дверь закрыта
                    fr_door_unreg = extract_first_match(unregs, ["CanSafetyFlags_passangerdoor"])  # передняя правая
                    fl_door_unreg = extract_first_match(unregs, ["CanSafetyFlags_driverdoor"])     # передняя левая
                    rl_door_unreg = extract_first_match(unregs, ["CanSafetyFlags_backdoor"])       # задняя левая
                    rr_door_unreg = extract_first_match(unregs, ["CanSafetyFlags_frontdoor"])      # задняя правая
                    
                    v.front_right_door_open = fr_door_unreg.lower() == "false" if fr_door_unreg else False
                    v.front_left_door_open = fl_door_unreg.lower() == "false" if fl_door_unreg else False
                    v.rear_left_door_open = rl_door_unreg.lower() == "false" if rl_door_unreg else False
                    v.rear_right_door_open = rr_door_unreg.lower() == "false" if rr_door_unreg else False

                # — Замки дверей —
                # Сначала ищем в RegistredSensors (для MB CLA45s, Hongqi e-qm5)
                fr_lock = extract_first_match(regs, ["ПП Замок (can43)", "ПП Замок", "front right lock"])
                fl_lock = extract_first_match(regs, ["ПЛ Замок (can45)", "ПЛ Замок", "front left lock"])
                rl_lock = extract_first_match(regs, ["ЗЛ Замок (can47)", "ЗЛ Замок", "rear left lock"])
                rr_lock = extract_first_match(regs, ["ЗП Замок (can49)", "ЗП Замок", "rear right lock"])
                
                if fr_lock or fl_lock or rl_lock or rr_lock:
                    # Формат RegistredSensors: "Открыт"/"Закрыт"
                    v.front_right_door_locked = bool(fr_lock and fr_lock.lower() != "открыт")
                    v.front_left_door_locked = bool(fl_lock and fl_lock.lower() != "открыт")
                    v.rear_left_door_locked = bool(rl_lock and rl_lock.lower() != "открыт")
                    v.rear_right_door_locked = bool(rr_lock and rr_lock.lower() != "открыт")
                else:
                    # Формат UnregisteredSensors: CanSafetyFlags_* = "True"/"False"
                    # False = замок заблокирован, True = замок открыт
                    fr_lock_unreg = extract_first_match(unregs, ["CanSafetyFlags_passangerdoor"])  # передняя правая
                    fl_lock_unreg = extract_first_match(unregs, ["CanSafetyFlags_driverdoor"])     # передняя левая
                    rl_lock_unreg = extract_first_match(unregs, ["CanSafetyFlags_backdoor"])       # задняя левая
                    rr_lock_unreg = extract_first_match(unregs, ["CanSafetyFlags_frontdoor"])      # задняя правая
                    
                    v.front_right_door_locked = fr_lock_unreg.lower() == "false" if fr_lock_unreg else True
                    v.front_left_door_locked = fl_lock_unreg.lower() == "false" if fl_lock_unreg else True
                    v.rear_left_door_locked = rl_lock_unreg.lower() == "false" if rl_lock_unreg else True
                    v.rear_right_door_locked = rr_lock_unreg.lower() == "false" if rr_lock_unreg else True

                # — Центральные замки —
                # Сначала ищем в RegistredSensors (для MB CLA45s, Hongqi e-qm5)
                central_locks = extract_first_match(regs, ["Замки (can40)", "Замки (центральный)", "Замки"])
                if central_locks:
                    v.central_locks_locked = bool(central_locks.lower().startswith("закрыт"))
                else:
                    # Если не найдено в RegistredSensors, ищем в UnregisteredSensors (для Haval F7x)
                    central_locks_unreg = extract_first_match(unregs, ["CanSafetyFlags_lock"])
                    if central_locks_unreg:
                        # CanSafetyFlags_lock: "False" = замки заблокированы, "True" = замки открыты
                        v.central_locks_locked = central_locks_unreg.lower() == "false"
                    else:
                        v.central_locks_locked = True

                # — Стёкла —
                fl_win = extract_first_match(regs, ["ПЛ Стекло (can50)", "ПЛ Стекло", "front left window"])
                fr_win = extract_first_match(regs, ["ПП Стекло (can51)", "ПП Стекло", "front right window"])
                rl_win = extract_first_match(regs, ["ЗЛ Стекло (can52)", "ЗЛ Стекло", "rear left window"])
                rr_win = extract_first_match(regs, ["ЗП Стекло (can53)", "ЗП Стекло", "rear right window"])
                # Если данных по стеклу нет, считаем закрытым (default True)
                v.front_left_window_closed = True if not fl_win else (fl_win.lower() == "закрыто")
                v.front_right_window_closed = True if not fr_win else (fr_win.lower() == "закрыто")
                v.rear_left_window_closed = True if not rl_win else (rl_win.lower() == "закрыто")
                v.rear_right_window_closed = True if not rr_win else (rr_win.lower() == "закрыто")

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
            {"vehicle_id": 800283232, "vehicle_imei": "869132074464026", "name": "Hongqi e-qm5",
             "plate_number": "890AVB09"},
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
