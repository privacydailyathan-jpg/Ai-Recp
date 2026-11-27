
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.appointment import (
    Appointment,
    AppointmentSettings,
    AppointmentStatus,
)
from app.models.business import Business
from app.models.receptionist_configuration import ReceptionistConfiguration
from app.models.user import User
from app.services.appointment_service import (
    AppointmentError,
    check_availability,
    create_appointment,
    reschedule_appointment,
    update_appointment_status,
)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )

    Base.metadata.create_all(engine)

    with TestingSessionLocal() as session:
        yield session

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def business(db: Session) -> Business:
    user = User(
        full_name="Appointment Test User",
        email=f"{uuid4()}@example.com",
        password_hash="test-password-hash",
    )
    db.add(user)
    db.flush()

    business = Business(
        name="Test Business",
        owner_user_id=user.id,
    )
    db.add(business)
    db.flush()

    configuration = ReceptionistConfiguration(
        business_id=business.id,
        assistant_name="Test Receptionist",
        greeting_message="Hello",
        business_timezone="UTC",
        is_enabled=True,
    )
    db.add(configuration)

    weekly_hours = {
        day: {
            "enabled": True,
            "open_time": "00:00",
            "close_time": "23:59",
        }
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }

    settings = AppointmentSettings(
        business_id=business.id,
        weekly_hours=weekly_hours,
        appointment_duration_minutes=30,
        buffer_minutes=0,
        minimum_notice_minutes=60,
        maximum_days_ahead=30,
    )

    db.add(settings)
    db.commit()
    db.refresh(business)

    return business


@pytest.fixture
def future_slot() -> datetime:
    return (
        datetime.now(UTC)
        .replace(hour=10, minute=0, second=0, microsecond=0)
        + timedelta(days=3)
    )


def book_test_appointment(
    db: Session,
    business_id,
    starts_at: datetime,
    request_key: str | None = None,
) -> Appointment:
    return create_appointment(
        db=db,
        business_id=business_id,
        customer_name="Test Customer",
        customer_email="customer@example.com",
        customer_phone="+1234567890",
        reason="General inquiry",
        starts_at=starts_at,
        request_key=request_key,
    )


def test_create_appointment_starts_as_pending(
    db: Session,
    business: Business,
    future_slot: datetime,
):
    appointment = book_test_appointment(
        db,
        business.id,
        future_slot,
    )

    assert appointment.status == AppointmentStatus.PENDING.value
    assert appointment.customer_name == "Test Customer"
    assert appointment.business_id == business.id
    assert appointment.ends_at - appointment.starts_at == timedelta(minutes=30)


def test_check_availability_returns_available_slot(
    db: Session,
    business: Business,
    future_slot: datetime,
):
    result = check_availability(
        db,
        business.id,
        future_slot,
    )

    assert result["available"] is True
    assert result["business_timezone"] == "UTC"
    assert result["ends_at"] - result["starts_at"] == timedelta(minutes=30)


def test_overlapping_appointment_is_rejected(
    db: Session,
    business: Business,
    future_slot: datetime,
):
    book_test_appointment(db, business.id, future_slot)

    with pytest.raises(AppointmentError) as error:
        book_test_appointment(db, business.id, future_slot)

    assert error.value.status_code == 409
    assert "no longer available" in error.value.message


def test_request_key_returns_existing_appointment(
    db: Session,
    business: Business,
    future_slot: datetime,
):
    first = book_test_appointment(
        db,
        business.id,
        future_slot,
        request_key="request-123",
    )

    second = create_appointment(
        db=db,
        business_id=business.id,
        customer_name="Different Customer",
        customer_email=None,
        customer_phone="+9876543210",
        reason="Different reason",
        starts_at=future_slot,
        request_key="request-123",
    )

    assert first.id == second.id
    assert second.customer_name == "Test Customer"


def test_appointment_requires_timezone_aware_datetime(
    db: Session,
    business: Business,
):
    naive_datetime = datetime.now() + timedelta(days=3)

    with pytest.raises(AppointmentError) as error:
        check_availability(
            db,
            business.id,
            naive_datetime,
        )

    assert "timezone" in error.value.message


def test_appointment_cannot_extend_past_closing_time(
    db: Session,
    business: Business,
):
    late_slot = (
        datetime.now(UTC)
        .replace(hour=23, minute=45, second=0, microsecond=0)
        + timedelta(days=3)
    )

    with pytest.raises(AppointmentError) as error:
        check_availability(
            db,
            business.id,
            late_slot,
        )

    assert error.value.status_code == 400


def test_appointment_status_can_be_confirmed(
    db: Session,
    business: Business,
    future_slot: datetime,
):
    appointment = book_test_appointment(
        db,
        business.id,
        future_slot,
    )

    updated = update_appointment_status(
        db,
        business.id,
        appointment.id,
        "confirmed",
        "Confirmed by staff",
    )

    assert updated.status == "confirmed"
    assert updated.internal_notes == "Confirmed by staff"


def test_pending_appointment_can_be_cancelled(
    db: Session,
    business: Business,
    future_slot: datetime,
):
    appointment = book_test_appointment(
        db,
        business.id,
        future_slot,
    )

    updated = update_appointment_status(
        db,
        business.id,
        appointment.id,
        "cancelled",
    )

    assert updated.status == "cancelled"


def test_cancelled_appointment_cannot_be_confirmed_again(
    db: Session,
    business: Business,
    future_slot: datetime,
):
    appointment = book_test_appointment(
        db,
        business.id,
        future_slot,
    )

    update_appointment_status(
        db,
        business.id,
        appointment.id,
        "cancelled",
    )

    with pytest.raises(AppointmentError) as error:
        update_appointment_status(
            db,
            business.id,
            appointment.id,
            "confirmed",
        )

    assert error.value.status_code == 409


def test_reschedule_appointment_to_available_slot(
    db: Session,
    business: Business,
    future_slot: datetime,
):
    appointment = book_test_appointment(
        db,
        business.id,
        future_slot,
    )

    new_slot = future_slot + timedelta(hours=2)

    updated = reschedule_appointment(
        db,
        business.id,
        appointment.id,
        new_slot,
    )

    assert updated.starts_at.replace(tzinfo=UTC) == new_slot.astimezone(UTC)
    assert updated.status == "pending"


def test_reschedule_rejects_occupied_slot(
    db: Session,
    business: Business,
    future_slot: datetime,
):
    first = book_test_appointment(
        db,
        business.id,
        future_slot,
    )

    occupied_slot = future_slot + timedelta(hours=2)

    book_test_appointment(
        db,
        business.id,
        occupied_slot,
    )

    with pytest.raises(AppointmentError) as error:
        reschedule_appointment(
            db,
            business.id,
            first.id,
            occupied_slot,
        )

    assert error.value.status_code == 409
