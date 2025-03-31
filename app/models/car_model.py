from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

from app.dependencies.database.database import Base


class Vehicle(Base):
    __tablename__ = 'vehicles'

    vehicle_id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_imei = Column(String, unique=True, nullable=False)
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

    def __repr__(self):
        return f"<Vehicle(vehicle_id={self.vehicle_id}, vehicle_imei={self.vehicle_imei}, speed={self.speed})>"
