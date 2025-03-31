from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

from app.dependencies.database.database import get_db
from app.glonassoft_api.history_car import fetch_gps_coordinates_async
from app.models.car_model import Vehicle

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


# Pydantic models for request and response
class VehicleCreate(BaseModel):
    vehicle_id: str = Field(..., description="Unique ID of the vehicle")
    vehicle_imei: str = Field(..., description="Unique IMEI of the vehicle")


class VehicleResponse(BaseModel):
    vehicle_id: int
    vehicle_imei: str
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


class GPSQueryParams(BaseModel):
    start_date: str = Field(..., description="Start date in format YYYY-MM-DDThh:mm:ss")
    end_date: str = Field(..., description="End date in format YYYY-MM-DDThh:mm:ss")


# Create vehicle endpoint
@router.post("/", response_model=VehicleResponse, status_code=201)
def create_vehicle(vehicle_data: VehicleCreate, db: Session = Depends(get_db)):
    try:
        new_vehicle = Vehicle(**vehicle_data.dict())
        db.add(new_vehicle)
        db.commit()
        db.refresh(new_vehicle)
        return new_vehicle
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Vehicle with this IMEI already exists")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create vehicle: {str(e)}")


# Get all vehicles endpoint
@router.get("/", response_model=List[VehicleResponse])
def get_all_vehicles(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    vehicles = db.query(Vehicle).offset(skip).limit(limit).all()
    return vehicles


# Delete vehicle endpoint
@router.delete("/{vehicle_id}", status_code=204)
def delete_vehicle(vehicle_id: int = Path(..., description="ID of the vehicle to delete"),
                   db: Session = Depends(get_db)):
    vehicle = db.query(Vehicle).filter(Vehicle.vehicle_id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    try:
        db.delete(vehicle)
        db.commit()
        return None
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete vehicle: {str(e)}")


# GPS coordinates endpoint