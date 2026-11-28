"""Read-only integration endpoints for TRN-02 and RNT-05 screens."""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from . import fe2_queries, fe2_reports
from .audit import write_audit
from .api_common import Limit, Offset, get_conn, now_of, role_dependency
from .auth import READ_ROLES, Principal
from .db import write_tx

router = APIRouter(prefix="/fe2", tags=["FE2"])
reader = role_dependency(READ_ROLES)


@router.get("/schedule")
def schedule(request: Request, start: datetime, end: datetime, vehicle_id: int | None = None,
             driver_id: int | None = None, include_released: bool = False, limit: Limit = 50,
             offset: Offset = 0, _: Principal = Depends(reader), conn=Depends(get_conn)):
    return fe2_queries.schedule(conn, start=start, end=end, now=now_of(request), vehicle_id=vehicle_id,
                                driver_id=driver_id, include_released=include_released,
                                limit=limit, offset=offset)


@router.get("/{kind}/{code}/commercial-summary")
def commercial_summary(kind: Literal["trips", "rentals"], code: str,
                       _: Principal = Depends(reader), conn=Depends(get_conn)):
    return fe2_queries.commercial_summary(conn, kind, code)


report_reader = role_dependency(fe2_reports.ROLES)


@router.get("/dashboard")
def dashboard(request: Request, start: date | None = None, end: date | None = None,
              p: Principal = Depends(report_reader), conn=Depends(get_conn)):
    return fe2_reports.dashboard(conn, p.roles, start, end, now_of(request))


@router.get("/dashboard/{metric}")
def dashboard_detail(metric: str, request: Request, start: date | None = None, end: date | None = None,
                     limit: Limit = 20, offset: Offset = 0,
                     p: Principal = Depends(report_reader), conn=Depends(get_conn)):
    return fe2_reports.metric_rows(conn, metric, p.roles, start, end, now_of(request), limit=limit, offset=offset)


@router.get("/reports/{report}.csv")
def report_csv(report: str, request: Request, start: date | None = None, end: date | None = None,
               p: Principal = Depends(report_reader), conn=Depends(get_conn)):
    now = now_of(request)
    content = fe2_reports.export_csv(conn, report, p.roles, start, end, now)
    with write_tx(conn):
        write_audit(conn, actor=p.username, action="REPORT_EXPORT", object_type="Report",
                    object_code=report, at=now,
                    after={"start": str(start) if start else None, "end": str(end) if end else None})
    suffix = f"{start or 'all'}_{end or 'all'}_{now.astimezone(fe2_reports.TZ):%Y%m%d_%H%M%S}"
    return Response("\ufeff" + content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{report}_{suffix}.csv"'})
