
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.appointment import (
    Appointment,
    AppointmentSettings,
    AppointmentStatus,
)
from app.models.business import Business
from app.models.receptionist_configuration import ReceptionistConfiguration


class AppointmentError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


ACTIVE_STATUSES = (
    AppointmentStatus.PENDING.value,
    AppointmentStatus.CONFIRMED.value,
)

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def get_business_timezone(
    db: Session,
    business_id: UUID,
) -> ZoneInfo:
    configuration = db.scalar(
        select(ReceptionistConfiguration).where(
            ReceptionistConfiguration.business_id == business_id
        )
    )

    if configuration is None:
        raise AppointmentError(
            "Configure the receptionist timezone before enabling appointments",
            409,
        )

    try:
        return ZoneInfo(configuration.business_timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise AppointmentError(
            "Business timezone is invalid",
            409,
        ) from error


def get_appointment_settings(
    db: Session,
    business_id: UUID,
) -> AppointmentSettings:
    settings = db.scalar(
        select(AppointmentSettings).where(
            AppointmentSettings.business_id == business_id
        )
    )

    if settings is None:
        raise AppointmentError(
            "Appointment settings have not been configured",
            404,
        )

    return settings


def _parse_time(value: str | time | None) -> time | None:
    if value is None:
        return None

    if isinstance(value, time):
        return value

    return time.fromisoformat(value)


def _validate_slot(
    db: Session,
    business_id: UUID,
    starts_at: datetime,
    settings: AppointmentSettings,
) -> tuple[datetime, datetime]:
    if starts_at.tzinfo is None or starts_at.utcoffset() is None:
        raise AppointmentError(
            "Appointment time must include a timezone"
        )

    timezone = get_business_timezone(db, business_id)
    now = datetime.now(UTC)

    start_utc = starts_at.astimezone(UTC)

    local_start = start_utc.astimezone(timezone)

    ends_at = start_utc + timedelta(
        minutes=settings.appointment_duration_minutes
    )

    local_end = ends_at.astimezone(timezone)

    if start_utc < now + timedelta(
        minutes=settings.minimum_notice_minutes
    ):
        raise AppointmentError(
            "Appointment does not meet the minimum notice period"
        )

    if start_utc > now + timedelta(
        days=settings.maximum_days_ahead
    ):
        raise AppointmentError(
            "Appointment is beyond the booking window"
        )

    if local_start.date() != local_end.date():
        raise AppointmentError(
            "Appointments cannot extend beyond business closing time"
        )

    day_name = WEEKDAYS[local_start.weekday()]
    day_hours = settings.weekly_hours.get(day_name)

    if not day_hours or not day_hours.get("enabled", False):
        raise AppointmentError(
            "Business is closed on the requested day"
        )

    opening = _parse_time(day_hours.get("open_time"))
    closing = _parse_time(day_hours.get("close_time"))

    if opening is None or closing is None:
        raise AppointmentError(
            "Business hours are not configured correctly",
            409,
        )

    start_time = local_start.time().replace(tzinfo=None)
    end_time = local_end.time().replace(tzinfo=None)

    if start_time < opening:
        raise AppointmentError(
            "Requested time is before business opening"
        )

    if end_time > closing:
        raise AppointmentError(
            "Requested time is after business closing"
        )

    return start_utc, ends_at


def _ensure_slot_available(
    db: Session,
    business_id: UUID,
    starts_at: datetime,
    ends_at: datetime,
    buffer_minutes: int,
    exclude_appointment_id: UUID | None = None,
) -> None:
    buffer = timedelta(minutes=buffer_minutes)

    statement = select(Appointment.id).where(
        Appointment.business_id == business_id,
        Appointment.status.in_(ACTIVE_STATUSES),
        Appointment.starts_at < ends_at + buffer,
        Appointment.ends_at > starts_at - buffer,
    )

    if exclude_appointment_id is not None:
        statement = statement.where(
            Appointment.id != exclude_appointment_id
        )

    if db.scalar(statement) is not None:
        raise AppointmentError(
            "This appointment slot is no longer available",
            409,
        )


def check_availability(
    db: Session,
    business_id: UUID,
    starts_at: datetime,
) -> dict:
    settings = get_appointment_settings(db, business_id)

    start_utc, ends_at = _validate_slot(
        db,
        business_id,
        starts_at,
        settings,
    )

    _ensure_slot_available(
        db,
        business_id,
        start_utc,
        ends_at,
        settings.buffer_minutes,
    )

    timezone = get_business_timezone(db, business_id)

    return {
        "available": True,
        "starts_at": start_utc,
        "ends_at": ends_at,
        "business_timezone": str(timezone),
    }


def create_appointment(
    db: Session,
    business_id: UUID,
    customer_name: str,
    customer_email: str | None,
    customer_phone: str,
    reason: str,
    starts_at: datetime,
    request_key: str | None = None,
    source: str = "dashboard",
) -> Appointment:
    business = db.scalar(
        select(Business)
        .where(Business.id == business_id)
        .with_for_update()
    )

    if business is None:
        raise AppointmentError(
            "Business not found",
            404,
        )

    if request_key is not None:
        existing = db.scalar(
            select(Appointment).where(
                Appointment.business_id == business_id,
                Appointment.request_key == request_key,
            )
        )

        if existing is not None:
            return existing

    settings = get_appointment_settings(db, business_id)

    start_utc, ends_at = _validate_slot(
        db,
        business_id,
        starts_at,
        settings,
    )

    _ensure_slot_available(
        db,
        business_id,
        start_utc,
        ends_at,
        settings.buffer_minutes,
    )

    appointment = Appointment(
        business_id=business_id,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
        reason=reason,
        starts_at=start_utc,
        ends_at=ends_at,
        status=AppointmentStatus.PENDING.value,
        source=source,
        request_key=request_key,
    )

    db.add(appointment)
    db.commit()
    db.refresh(appointment)

    return appointment


def update_appointment_status(
    db: Session,
    business_id: UUID,
    appointment_id: UUID,
    new_status: str,
    internal_notes: str | None = None,
) -> Appointment:
    appointment = db.scalar(
        select(Appointment)
        .where(
            Appointment.id == appointment_id,
            Appointment.business_id == business_id,
        )
        .with_for_update()
    )

    if appointment is None:
        raise AppointmentError(
            "Appointment not found",
            404,
        )

    allowed_transitions = {
        "pending": {"confirmed", "cancelled"},
        "confirmed": {"cancelled", "completed", "no_show"},
        "cancelled": set(),
        "completed": set(),
        "no_show": set(),
    }

    if new_status not in allowed_transitions.get(
        appointment.status,
        set(),
    ):
        raise AppointmentError(
            f"Cannot change appointment from {appointment.status} to {new_status}",
            409,
        )

    appointment.status = new_status

    if internal_notes is not None:
        appointment.internal_notes = internal_notes

    db.commit()
    db.refresh(appointment)

    return appointment

def reschedule_appointment(
    db: Session,
    business_id: UUID,
    appointment_id: UUID,
    starts_at: datetime,
) -> Appointment:
    business = db.scalar(
        select(Business)
        .where(Business.id == business_id)
        .with_for_update()
    )

    if business is None:
        raise AppointmentError("Business not found", 404)

    appointment = db.scalar(
        select(Appointment)
        .where(
            Appointment.id == appointment_id,
            Appointment.business_id == business_id,
        )
        .with_for_update()
    )

    if appointment is None:
        raise AppointmentError("Appointment not found", 404)

    if appointment.status not in ACTIVE_STATUSES:
        raise AppointmentError(
            "Only pending or confirmed appointments can be rescheduled",
            409,
        )

    settings = get_appointment_settings(db, business_id)

    start_utc, ends_at = _validate_slot(
        db,
        business_id,
        starts_at,
        settings,
    )

    _ensure_slot_available(
        db,
        business_id,
        start_utc,
        ends_at,
        settings.buffer_minutes,
        exclude_appointment_id=appointment.id,
    )

    appointment.starts_at = start_utc
    appointment.ends_at = ends_at

    db.commit()
    db.refresh(appointment)

    return appointment
