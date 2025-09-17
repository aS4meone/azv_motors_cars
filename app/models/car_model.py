from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from datetime import datetime

from app.dependencies.database.database import Base


class Vehicle(Base):
    __tablename__ = 'vehicles'

    vehicle_id = Column(Integer, primary_key=True, autoincrement=False)
    vehicle_imei = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)  # новое поле
    plate_number = Column(String, unique=True, nullable=False)
    longitude = Column(Float, nullable=True)
    latitude = Column(Float, nullable=True)
    altitude = Column(Float, nullable=True)
    course = Column(Float, nullable=True)
    last_update_coordinates = Column(DateTime, default=datetime.utcnow, nullable=True)
    last_update_sensors = Column(DateTime, default=datetime.utcnow, nullable=True)
    is_engine_on = Column(Boolean, default=False)
    mileage = Column(Float, default=0.0)
    rpm = Column(Integer, default=0)
    speed = Column(Float, default=0.0)
    engine_temperature = Column(Float, nullable=True)
    is_hood_open = Column(Boolean, default=False)
    fuel_level = Column(Float, nullable=True)
    engine_hours = Column(Float, default=0.0)

    # Doors (open status)
    front_right_door_open = Column(Boolean, default=False)
    front_left_door_open = Column(Boolean, default=False)
    rear_left_door_open = Column(Boolean, default=False)
    rear_right_door_open = Column(Boolean, default=False)

    # Door locks (locked status)
    front_right_door_locked = Column(Boolean, default=False)
    front_left_door_locked = Column(Boolean, default=False)
    rear_left_door_locked = Column(Boolean, default=False)
    rear_right_door_locked = Column(Boolean, default=False)

    # Central locks (locked status)
    central_locks_locked = Column(Boolean, default=False)

    # Windows (closed status)
    front_left_window_closed = Column(Boolean, default=False)
    front_right_window_closed = Column(Boolean, default=False)
    rear_left_window_closed = Column(Boolean, default=False)
    rear_right_window_closed = Column(Boolean, default=False)

    # Trunk and lights/handbrake
    is_trunk_open = Column(Boolean, default=False)
    is_handbrake_on = Column(Boolean, default=False)
    are_lights_on = Column(Boolean, default=False)
    is_light_auto_mode_on = Column(Boolean, default=False)

    def __repr__(self):
        return (
            f"<Vehicle(id={self.vehicle_id}, name='{self.name}', "
            f"imei={self.vehicle_imei}, speed={self.speed})>"
        )
