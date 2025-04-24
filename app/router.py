# app/router.py
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime

from app.dependencies.database.database import get_db
from app.models.car_model import Vehicle

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


class VehicleCreate(BaseModel):
    vehicle_id: int
    vehicle_imei: str
    name: str  # новое поле


class VehicleResponse(BaseModel):
    vehicle_id: int
    vehicle_imei: str
    name: str

    longitude: Optional[float] = None
    latitude: Optional[float] = None
    altitude: Optional[float] = None
    course: Optional[float] = None
    last_update_coordinates: Optional[datetime] = None
    last_update_sensors: Optional[datetime] = None
    is_engine_on: bool
    mileage: float
    rpm: int
    speed: float
    engine_temperature: Optional[float] = None
    is_hood_open: bool
    fuel_level: Optional[float] = None
    engine_hours: float

    class Config:
        orm_mode = True


@router.post("/", response_model=VehicleResponse, status_code=201)
def create_vehicle(vehicle: VehicleCreate, db: Session = Depends(get_db)):
    try:
        v = Vehicle(**vehicle.dict())
        db.add(v);
        db.commit();
        db.refresh(v)
        return v
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))


@router.get("/", response_model=List[VehicleResponse])
def list_vehicles(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Vehicle).offset(skip).limit(limit).all()


@router.delete("/{vehicle_id}", status_code=204)
def delete_vehicle(vehicle_id: int = Path(...), db: Session = Depends(get_db)):
    v = db.query(Vehicle).filter(Vehicle.vehicle_id == vehicle_id).first()
    if not v:
        raise HTTPException(404, "Not found")
    db.delete(v);
    db.commit()
