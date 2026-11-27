from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class DayHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    open_time: time | None = None
    close_time: time | None = None

    @model_validator(mode="after")
    def validate_hours(self):
        if self.enabled:
            if self.open_time is None or self.close_time is None:
                raise ValueError(
                    "Enabled days require opening and closing times"
                )

            if self.open_time >= self.close_time:
                raise ValueError(
                    "Closing time must be after opening time"
                )

        return self


class AppointmentSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekly_hours: dict[str, DayHours]
    appointment_duration_minutes: int = Field(ge=5, le=480)
    buffer_minutes: int = Field(ge=0, le=240)
    minimum_notice_minutes: int = Field(ge=0)
    maximum_days_ahead: int = Field(ge=1, le=365)

    @field_validator("weekly_hours")
    @classmethod
    def validate_weekdays(cls, value):
        allowed = {
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        }

        if set(value) != allowed:
            raise ValueError("Provide exactly seven weekdays")

        return value


class AppointmentSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    weekly_hours: dict
    appointment_duration_minutes: int
    buffer_minutes: int
    minimum_notice_minutes: int
    maximum_days_ahead: int


class AppointmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: EmailStr | None = None
    customer_phone: str = Field(min_length=3, max_length=64)
    reason: str = Field(min_length=1, max_length=4000)
    starts_at: AwareDatetime
    request_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @field_validator("customer_name", "customer_phone", "reason")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("This field must not be empty")

        return value


class AppointmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    customer_name: str
    customer_email: str | None
    customer_phone: str
    reason: str
    starts_at: datetime
    ends_at: datetime
    status: str
    source: str
    internal_notes: str | None
    created_at: datetime
    updated_at: datetime


class AppointmentStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "confirmed",
        "cancelled",
        "completed",
        "no_show",
    ]
    internal_notes: str | None = Field(
        default=None,
        max_length=4000,
    )


class AppointmentReschedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: AwareDatetime
