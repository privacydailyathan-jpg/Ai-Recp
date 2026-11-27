
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_business_permission
from app.db.session import get_db
from app.models.appointment import Appointment, AppointmentSettings
from app.models.business import Business
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentRead,
    AppointmentReschedule,
    AppointmentSettingsRead,
    AppointmentSettingsUpdate,
    AppointmentStatusUpdate,
)
from app.services.appointment_service import (
    AppointmentError,
    check_availability,
    create_appointment,
    get_appointment_settings,
    reschedule_appointment,
    update_appointment_status,
)
from app.services.business_authorization import BusinessPermission


router = APIRouter(
    prefix="/businesses/{business_id}",
    tags=["appointments"],
)


def raise_appointment_error(error: AppointmentError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail=error.message,
    ) from error


@router.get(
    "/appointment-settings",
    response_model=AppointmentSettingsRead,
)
def read_appointment_settings(
    business: Annotated[
        Business,
        Depends(require_business_permission(BusinessPermission.READ)),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return get_appointment_settings(db, business.id)
    except AppointmentError as error:
        raise_appointment_error(error)


@router.put(
    "/appointment-settings",
    response_model=AppointmentSettingsRead,
)
def save_appointment_settings(
    payload: AppointmentSettingsUpdate,
    business: Annotated[
        Business,
        Depends(require_business_permission(BusinessPermission.MANAGE_SETTINGS)),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    settings = db.scalar(
        select(AppointmentSettings).where(
            AppointmentSettings.business_id == business.id
        )
    )

    values = payload.model_dump(mode="json")

    if settings is None:
        settings = AppointmentSettings(
            business_id=business.id,
            **values,
        )
        db.add(settings)
    else:
        for field, value in values.items():
            setattr(settings, field, value)

    db.commit()
    db.refresh(settings)

    return settings


@router.get("/availability")
def read_availability(
    starts_at: AwareDatetime,
    business: Annotated[
        Business,
        Depends(require_business_permission(BusinessPermission.READ)),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return check_availability(
            db,
            business.id,
            starts_at,
        )
    except AppointmentError as error:
        raise_appointment_error(error)


@router.post(
    "/appointments",
    response_model=AppointmentRead,
    status_code=status.HTTP_201_CREATED,
)
def book_appointment(
    payload: AppointmentCreate,
    business: Annotated[
        Business,
        Depends(
            require_business_permission(
                BusinessPermission.MANAGE_OPERATIONAL_RESOURCES
            )
        ),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return create_appointment(
            db=db,
            business_id=business.id,
            customer_name=payload.customer_name,
            customer_email=(
                str(payload.customer_email)
                if payload.customer_email is not None
                else None
            ),
            customer_phone=payload.customer_phone,
            reason=payload.reason,
            starts_at=payload.starts_at,
            request_key=payload.request_key,
        )
    except AppointmentError as error:
        raise_appointment_error(error)


@router.get(
    "/appointments",
    response_model=list[AppointmentRead],
)
def list_appointments(
    business: Annotated[
        Business,
        Depends(require_business_permission(BusinessPermission.READ)),
    ],
    db: Annotated[Session, Depends(get_db)],
    appointment_status: str | None = Query(
        default=None,
        alias="status",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
):
    statement = select(Appointment).where(
        Appointment.business_id == business.id
    )

    if appointment_status is not None:
        allowed = {
            "pending",
            "confirmed",
            "cancelled",
            "completed",
            "no_show",
        }

        if appointment_status not in allowed:
            raise HTTPException(
                status_code=422,
                detail="Invalid appointment status",
            )

        statement = statement.where(
            Appointment.status == appointment_status
        )

    return list(
        db.scalars(
            statement
            .order_by(
                Appointment.starts_at.asc(),
                Appointment.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()
    )


@router.patch(
    "/appointments/{appointment_id}/status",
    response_model=AppointmentRead,
)
def change_appointment_status(
    appointment_id: UUID,
    payload: AppointmentStatusUpdate,
    business: Annotated[
        Business,
        Depends(
            require_business_permission(
                BusinessPermission.MANAGE_OPERATIONAL_RESOURCES
            )
        ),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return update_appointment_status(
            db=db,
            business_id=business.id,
            appointment_id=appointment_id,
            new_status=payload.status,
            internal_notes=payload.internal_notes,
        )
    except AppointmentError as error:
        raise_appointment_error(error)


@router.patch(
    "/appointments/{appointment_id}/reschedule",
    response_model=AppointmentRead,
)
def reschedule_existing_appointment(
    appointment_id: UUID,
    payload: AppointmentReschedule,
    business: Annotated[
        Business,
        Depends(
            require_business_permission(
                BusinessPermission.MANAGE_OPERATIONAL_RESOURCES
            )
        ),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return reschedule_appointment(
            db=db,
            business_id=business.id,
            appointment_id=appointment_id,
            starts_at=payload.starts_at,
        )
    except AppointmentError as error:
        raise_appointment_error(error)
