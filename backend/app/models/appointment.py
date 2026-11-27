from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AppointmentStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class AppointmentSettings(Base):
    __tablename__ = "appointment_settings"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_appointment_settings_business"),
        CheckConstraint(
            "appointment_duration_minutes BETWEEN 5 AND 480",
            name="ck_appointment_settings_duration",
        ),
        CheckConstraint(
            "buffer_minutes BETWEEN 0 AND 240",
            name="ck_appointment_settings_buffer",
        ),
        CheckConstraint(
            "minimum_notice_minutes >= 0",
            name="ck_appointment_settings_minimum_notice",
        ),
        CheckConstraint(
            "maximum_days_ahead BETWEEN 1 AND 365",
            name="ck_appointment_settings_maximum_days",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekly_hours: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    appointment_duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    buffer_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    minimum_notice_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
    maximum_days_ahead: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(
            "ends_at > starts_at",
            name="ck_appointments_valid_time_range",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'cancelled', 'completed', 'no_show')",
            name="ck_appointments_status",
        ),
        UniqueConstraint(
            "business_id",
            "request_key",
            name="uq_appointments_business_request_key",
        ),
        Index(
            "ix_appointments_business_starts_at",
            "business_id",
            "starts_at",
        ),
        Index(
            "ix_appointments_business_status_starts_at",
            "business_id",
            "status",
            "starts_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_email: Mapped[str | None] = mapped_column(String(320))
    customer_phone: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AppointmentStatus.PENDING.value,
        server_default=AppointmentStatus.PENDING.value,
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="dashboard", server_default="dashboard"
    )
    request_key: Mapped[str | None] = mapped_column(String(128))
    internal_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )