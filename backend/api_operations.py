"""HTTP API thực hiện chuyến, thực hiện thuê xe và tệp bằng chứng."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field

from . import attachments, rental_ops, trip_ops
from .api_common import (Body, Limit, NameText, NoteText, Offset, ReasonText, get_conn, idempotent_write, now_of,
                         role_dependency)
from .auth import ATTACHMENT_READ_ROLES, ATTACHMENT_WRITE_ROLES, READ_ROLES, WRITE_ROLES, Principal
from .db import write_tx

router = APIRouter()
reader = role_dependency(READ_ROLES)
writer = role_dependency(WRITE_ROLES)
file_reader = role_dependency(ATTACHMENT_READ_ROLES)
file_writer = role_dependency(ATTACHMENT_WRITE_ROLES)
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]
AttachmentCodes = Annotated[list[str], Field(max_length=20)]


# ---------- Tệp bằng chứng ----------

@router.post("/attachments", status_code=201)
def upload_attachment(request: Request, file: UploadFile = File(...), p: Principal = Depends(file_writer),
                      conn=Depends(get_conn)):
    data = file.file.read(attachments.MAX_BYTES + 1)
    with write_tx(conn):
        return attachments.store(conn, request.app.state.files_dir, filename=file.filename or "", data=data,
                                 actor=p.username, now=now_of(request))


@router.get("/attachments/{code}")
def download_attachment(code: str, request: Request, _: Principal = Depends(file_reader), conn=Depends(get_conn)):
    meta, path = attachments.open_file(conn, request.app.state.files_dir, code)
    return FileResponse(path, media_type=meta["content_type"], filename=meta["original_name"],
                        headers={"X-Content-Type-Options": "nosniff"})


# ---------- Chuyến vận tải (TRN-03/04/05) ----------

class Pickup(Body):
    actual_at: datetime
    handed_over_by: NameText
    cargo_condition: ReasonText
    actual_weight_kg: float | None = Field(None, gt=0)
    discrepancy_note: NoteText | None = None


class ActualTime(Body):
    actual_at: datetime


class Delivery(Body):
    actual_at: datetime
    result: Literal["SUCCESS", "FAILED"]
    receiver_name: str | None = Field(None, max_length=200)
    note: NoteText | None = None
    attachment_codes: AttachmentCodes = []


class TripComplete(Body):
    released_at: datetime


class IncidentCreate(Body):
    incident_type: Literal["DELAY", "BREAKDOWN", "DAMAGE", "DELIVERY_FAILED", "OTHER"]
    occurred_at: datetime
    description: ReasonText
    handler_name: NameText
    expected_resolution_at: datetime | None = None


class IncidentResolve(Body):
    version: int
    resolution: ReasonText
    resolved_at: datetime


@router.get("/trips")
def list_trips(
    request: Request, status: str | None = None, q: str | None = None, overdue_only: bool = False,
    limit: Limit = 50, offset: Offset = 0, _: Principal = Depends(reader), conn=Depends(get_conn),
):
    return trip_ops.list_trips(conn, now=now_of(request), status=status, q=q, overdue_only=overdue_only,
                               limit=limit, offset=offset)


@router.post("/trips/{trip_code}/pickup")
def pickup(trip_code: str, body: Pickup, request: Request, response: Response, idempotency_key: IdemKey = None,
           p: Principal = Depends(writer), conn=Depends(get_conn)):
    now = now_of(request)
    return idempotent_write(conn, response, key=idempotency_key, principal=p, operation=f"trip.pickup:{trip_code}",
                            body=body, now=now,
                            action=lambda: trip_ops.pickup(conn, trip_code, **body.model_dump(), actor=p.username,
                                                           now=now))


@router.post("/trips/{trip_code}/in-transit")
def in_transit(trip_code: str, body: ActualTime, request: Request, response: Response,
               idempotency_key: IdemKey = None, p: Principal = Depends(writer), conn=Depends(get_conn)):
    now = now_of(request)
    return idempotent_write(conn, response, key=idempotency_key, principal=p, operation=f"trip.transit:{trip_code}",
                            body=body, now=now,
                            action=lambda: trip_ops.start_transit(conn, trip_code, actual_at=body.actual_at,
                                                                  actor=p.username, now=now))


@router.post("/trips/{trip_code}/delivery")
def delivery(trip_code: str, body: Delivery, request: Request, response: Response,
             idempotency_key: IdemKey = None, p: Principal = Depends(writer), conn=Depends(get_conn)):
    now = now_of(request)
    return idempotent_write(conn, response, key=idempotency_key, principal=p, operation=f"trip.deliver:{trip_code}",
                            body=body, now=now,
                            action=lambda: trip_ops.deliver(conn, trip_code, **body.model_dump(), actor=p.username,
                                                            now=now))


@router.post("/trips/{trip_code}/complete")
def complete_trip(trip_code: str, body: TripComplete, request: Request, response: Response,
                  idempotency_key: IdemKey = None, p: Principal = Depends(writer), conn=Depends(get_conn)):
    now = now_of(request)
    return idempotent_write(conn, response, key=idempotency_key, principal=p, operation=f"trip.complete:{trip_code}",
                            body=body, now=now,
                            action=lambda: trip_ops.complete(conn, trip_code, released_at=body.released_at,
                                                             actor=p.username, now=now))


@router.post("/trips/{trip_code}/incidents", status_code=201)
def create_incident(trip_code: str, body: IncidentCreate, request: Request, p: Principal = Depends(writer),
                    conn=Depends(get_conn)):
    with write_tx(conn):
        return trip_ops.create_incident(conn, trip_code, **body.model_dump(), actor=p.username, now=now_of(request))


@router.post("/incidents/{incident_code}/resolve")
def resolve_incident(incident_code: str, body: IncidentResolve, request: Request, p: Principal = Depends(writer),
                     conn=Depends(get_conn)):
    with write_tx(conn):
        return trip_ops.resolve_incident(conn, incident_code, expected_version=body.version,
                                         resolution=body.resolution, resolved_at=body.resolved_at,
                                         actor=p.username, now=now_of(request))


# ---------- Thuê xe (RNT-03/04/05) ----------

class Inspection(Body):
    actual_at: datetime
    odometer_km: int = Field(ge=0)
    body_condition: ReasonText
    fuel_level: str = Field(min_length=1, max_length=200)
    accessories: ReasonText
    note: NoteText | None = None
    attachment_codes: AttachmentCodes = []


class Handover(Inspection):
    receiver_name: NameText


class Return(Inspection):
    returned_by: NameText


class RentalNote(Body):
    note: ReasonText


@router.get("/rentals")
def list_rentals(
    request: Request, status: str | None = None, q: str | None = None, overdue_only: bool = False,
    limit: Limit = 50, offset: Offset = 0, _: Principal = Depends(reader), conn=Depends(get_conn),
):
    return rental_ops.list_rentals(conn, now=now_of(request), status=status, q=q, overdue_only=overdue_only,
                                   limit=limit, offset=offset)


@router.post("/rentals/{rental_code}/handover")
def handover(rental_code: str, body: Handover, request: Request, response: Response,
             idempotency_key: IdemKey = None, p: Principal = Depends(writer), conn=Depends(get_conn)):
    now = now_of(request)
    return idempotent_write(conn, response, key=idempotency_key, principal=p,
                            operation=f"rental.handover:{rental_code}", body=body, now=now,
                            action=lambda: rental_ops.handover(conn, rental_code, **body.model_dump(),
                                                               actor=p.username, now=now))


@router.post("/rentals/{rental_code}/return")
def return_vehicle(rental_code: str, body: Return, request: Request, response: Response,
                   idempotency_key: IdemKey = None, p: Principal = Depends(writer), conn=Depends(get_conn)):
    now = now_of(request)
    return idempotent_write(conn, response, key=idempotency_key, principal=p,
                            operation=f"rental.return:{rental_code}", body=body, now=now,
                            action=lambda: rental_ops.return_vehicle(conn, rental_code, **body.model_dump(),
                                                                     actor=p.username, now=now))


@router.post("/rentals/{rental_code}/complete")
def complete_rental(rental_code: str, request: Request, p: Principal = Depends(writer), conn=Depends(get_conn)):
    with write_tx(conn):
        return rental_ops.complete_rental(conn, rental_code, actor=p.username, now=now_of(request))


@router.post("/rentals/{rental_code}/notes", status_code=201)
def add_rental_note(rental_code: str, body: RentalNote, request: Request, p: Principal = Depends(writer),
                    conn=Depends(get_conn)):
    with write_tx(conn):
        return rental_ops.add_note(conn, rental_code, note=body.note, actor=p.username, now=now_of(request))
