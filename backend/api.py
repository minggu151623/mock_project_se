"""HTTP API cho đội xe, lịch nguồn lực, phân công chuyến và giữ xe thuê."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import Field

from . import assignments, booking, fleet, rental_ops, trip_ops
from .api_common import Body, Limit, Offset, ReasonText, get_conn, idempotent_write, now_of, role_dependency
from .auth import READ_ROLES, WRITE_ROLES, Principal
from .db import write_tx

router = APIRouter()
reader = role_dependency(READ_ROLES)
writer = role_dependency(WRITE_ROLES)


# ---------- Xe và tài xế (TRN-01) ----------

class VehicleCreate(Body):
    plate_number: str = Field(min_length=1, max_length=20)
    vehicle_type: str = Field(min_length=1, max_length=50)
    capacity_kg: int = Field(gt=0)
    description: str = Field("", max_length=2000)
    status: Literal["ACTIVE", "MAINTENANCE", "INACTIVE"] = "ACTIVE"


class VehicleUpdate(Body):
    version: int
    plate_number: str | None = Field(None, min_length=1, max_length=20)
    vehicle_type: str | None = Field(None, min_length=1, max_length=50)
    capacity_kg: int | None = Field(None, gt=0)
    description: str | None = Field(None, max_length=2000)


class VehicleStatusChange(Body):
    version: int
    status: Literal["ACTIVE", "MAINTENANCE", "INACTIVE"]
    reason: str | None = Field(None, max_length=2000)


class DriverCreate(Body):
    full_name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=30)
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class DriverUpdate(Body):
    version: int
    full_name: str | None = Field(None, min_length=1, max_length=200)
    phone: str | None = Field(None, min_length=1, max_length=30)


class DriverStatusChange(Body):
    version: int
    status: Literal["ACTIVE", "INACTIVE"]
    reason: str | None = Field(None, max_length=2000)


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/vehicles", status_code=201)
def create_vehicle(body: VehicleCreate, request: Request, p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return fleet.create_vehicle(conn, **body.model_dump(), actor=p.username, now=now_of(request))


@router.get("/vehicles")
def list_vehicles(
    status: Literal["ACTIVE", "MAINTENANCE", "INACTIVE"] | None = None, q: str | None = None,
    limit: Limit = 50, offset: Offset = 0, _: Principal = Depends(reader), conn=Depends(get_conn),
):
    return fleet.list_vehicles(conn, status=status, q=q, limit=limit, offset=offset)


@router.get("/vehicles/{vehicle_id}")
def get_vehicle(vehicle_id: int, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return fleet.get_vehicle(conn, vehicle_id)


@router.patch("/vehicles/{vehicle_id}")
def update_vehicle(vehicle_id: int, body: VehicleUpdate, request: Request,
                   p: Principal = Depends(writer), conn=Depends(get_conn)):
    data = body.model_dump(exclude={"version"})
    with write_tx(conn):
        return fleet.update_vehicle(conn, vehicle_id, expected_version=body.version, **data,
                                    actor=p.username, now=now_of(request))


@router.post("/vehicles/{vehicle_id}/status")
def change_vehicle_status(vehicle_id: int, body: VehicleStatusChange, request: Request,
                          p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return fleet.set_vehicle_status(conn, vehicle_id, status=body.status, expected_version=body.version,
                                        reason=body.reason, actor=p.username, now=now_of(request))


@router.post("/drivers", status_code=201)
def create_driver(body: DriverCreate, request: Request, p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return fleet.create_driver(conn, **body.model_dump(), actor=p.username, now=now_of(request))


@router.get("/drivers")
def list_drivers(
    status: Literal["ACTIVE", "INACTIVE"] | None = None, q: str | None = None,
    limit: Limit = 50, offset: Offset = 0, _: Principal = Depends(reader), conn=Depends(get_conn),
):
    return fleet.list_drivers(conn, status=status, q=q, limit=limit, offset=offset)


@router.get("/drivers/{driver_id}")
def get_driver(driver_id: int, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return fleet.get_driver(conn, driver_id)


@router.patch("/drivers/{driver_id}")
def update_driver(driver_id: int, body: DriverUpdate, request: Request,
                  p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return fleet.update_driver(conn, driver_id, expected_version=body.version,
                                   full_name=body.full_name, phone=body.phone,
                                   actor=p.username, now=now_of(request))


@router.post("/drivers/{driver_id}/status")
def change_driver_status(driver_id: int, body: DriverStatusChange, request: Request,
                         p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return fleet.set_driver_status(conn, driver_id, status=body.status, expected_version=body.version,
                                       reason=body.reason, actor=p.username, now=now_of(request))


# ---------- Nguồn lực còn trống và lịch (TRN-02, RNT-01) ----------

@router.get("/availability/vehicles")
def available_vehicles(
    start: datetime, end: datetime, min_capacity_kg: float = Query(0, ge=0),
    vehicle_type: str | None = None, _: Principal = Depends(reader), conn=Depends(get_conn),
):
    return {"items": fleet.available_vehicles(conn, start=start, end=end,
                                              min_capacity_kg=min_capacity_kg, vehicle_type=vehicle_type)}


@router.get("/availability/drivers")
def available_drivers(start: datetime, end: datetime, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return {"items": fleet.available_drivers(conn, start=start, end=end)}


@router.get("/schedule")
def schedule(
    request: Request, start: datetime, end: datetime, vehicle_id: int | None = None,
    driver_id: int | None = None, include_released: bool = False, limit: Limit = 50,
    offset: Offset = 0, _: Principal = Depends(reader), conn=Depends(get_conn),
):
    return booking.list_schedule(conn, start=start, end=end, now=now_of(request), vehicle_id=vehicle_id,
                                 driver_id=driver_id, include_released=include_released,
                                 limit=limit, offset=offset)


# ---------- Phân công chuyến vận tải (TRN-02, TRN-05) ----------

class TripAssign(Body):
    vehicle_id: int
    driver_id: int
    planned_start: datetime
    planned_end: datetime


class TripReassign(TripAssign):
    version: int
    reason: ReasonText


class TripUnassign(Body):
    version: int
    reason: ReasonText


class PlannedEndChange(Body):
    new_planned_end: datetime
    reason: ReasonText


@router.get("/trips/{trip_code}")
def get_trip(trip_code: str, request: Request, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return trip_ops.get_trip_detail(conn, trip_code, now_of(request))


@router.post("/trips/{trip_code}/assignment", status_code=201)
def assign_trip(
    trip_code: str, body: TripAssign, request: Request, response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    p: Principal = Depends(writer), conn=Depends(get_conn),
):
    now = now_of(request)
    return idempotent_write(
        conn, response, key=idempotency_key, principal=p, operation=f"trip.assign:{trip_code}", body=body,
        now=now, status_code=201,
        action=lambda: assignments.assign_trip(conn, trip_code, **body.model_dump(), actor=p.username, now=now))


@router.put("/trips/{trip_code}/assignment")
def reassign_trip(trip_code: str, body: TripReassign, request: Request,
                  p: Principal = Depends(writer), conn=Depends(get_conn)):
    data = body.model_dump(exclude={"version"})
    with write_tx(conn):
        return assignments.change_trip_assignment(conn, trip_code, expected_version=body.version, **data,
                                                  actor=p.username, now=now_of(request))


@router.post("/trips/{trip_code}/assignment/release")
def unassign_trip(trip_code: str, body: TripUnassign, request: Request,
                  p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return assignments.unassign_trip(conn, trip_code, expected_version=body.version, reason=body.reason,
                                         actor=p.username, now=now_of(request))


@router.post("/trips/{trip_code}/planned-end")
def change_trip_planned_end(trip_code: str, body: PlannedEndChange, request: Request,
                            p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return assignments.change_trip_planned_end(conn, trip_code, new_planned_end=body.new_planned_end,
                                                   reason=body.reason, actor=p.username, now=now_of(request))


# ---------- Giữ xe cho hồ sơ thuê (RNT-01, RNT-02) ----------

class RentalReserve(Body):
    vehicle_id: int
    planned_start: datetime | None = None
    planned_end: datetime | None = None


class RentalReserveChange(Body):
    version: int
    vehicle_id: int
    planned_start: datetime
    planned_end: datetime
    reason: ReasonText
    customer_note: ReasonText


class RentalRelease(Body):
    version: int
    reason: ReasonText
    customer_note: ReasonText


@router.get("/rentals/{rental_code}")
def get_rental(rental_code: str, request: Request, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return rental_ops.get_rental_detail(conn, rental_code, now_of(request))


@router.post("/rentals/{rental_code}/reservation", status_code=201)
def reserve_rental(
    rental_code: str, body: RentalReserve, request: Request, response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    p: Principal = Depends(writer), conn=Depends(get_conn),
):
    now = now_of(request)
    return idempotent_write(
        conn, response, key=idempotency_key, principal=p, operation=f"rental.reserve:{rental_code}", body=body,
        now=now, status_code=201,
        action=lambda: assignments.reserve_rental(conn, rental_code, **body.model_dump(), actor=p.username, now=now))


@router.put("/rentals/{rental_code}/reservation")
def change_rental_reservation(rental_code: str, body: RentalReserveChange, request: Request,
                              p: Principal = Depends(writer), conn=Depends(get_conn)):
    data = body.model_dump(exclude={"version"})
    with write_tx(conn):
        return assignments.change_rental_reservation(conn, rental_code, expected_version=body.version,
                                                     **data, actor=p.username, now=now_of(request))


@router.post("/rentals/{rental_code}/reservation/release")
def release_rental_reservation(rental_code: str, body: RentalRelease, request: Request,
                               p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return assignments.release_rental_reservation(
            conn, rental_code, expected_version=body.version, reason=body.reason,
            customer_note=body.customer_note, actor=p.username, now=now_of(request))
