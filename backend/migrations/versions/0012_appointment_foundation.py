import sqlalchemy as sa
from alembic import op


revision = "0012_appointment_foundation"
down_revision = "0011_vapi_native_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "appointment_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column(
            "weekly_hours",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "appointment_duration_minutes",
            sa.Integer(),
            server_default="30",
            nullable=False,
        ),
        sa.Column(
            "buffer_minutes",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "minimum_notice_minutes",
            sa.Integer(),
            server_default="60",
            nullable=False,
        ),
        sa.Column(
            "maximum_days_ahead",
            sa.Integer(),
            server_default="30",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "appointment_duration_minutes BETWEEN 5 AND 480",
            name="ck_appointment_settings_duration",
        ),
        sa.CheckConstraint(
            "buffer_minutes BETWEEN 0 AND 240",
            name="ck_appointment_settings_buffer",
        ),
        sa.CheckConstraint(
            "minimum_notice_minutes >= 0",
            name="ck_appointment_settings_minimum_notice",
        ),
        sa.CheckConstraint(
            "maximum_days_ahead BETWEEN 1 AND 365",
            name="ck_appointment_settings_maximum_days",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            name="uq_appointment_settings_business",
        ),
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("customer_email", sa.String(length=320), nullable=True),
        sa.Column("customer_phone", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            server_default="dashboard",
            nullable=False,
        ),
        sa.Column("request_key", sa.String(length=128), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ends_at > starts_at",
            name="ck_appointments_valid_time_range",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'cancelled', 'completed', 'no_show')",
            name="ck_appointments_status",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            "request_key",
            name="uq_appointments_business_request_key",
        ),
    )

    op.create_index(
        "ix_appointments_business_starts_at",
        "appointments",
        ["business_id", "starts_at"],
    )
    op.create_index(
        "ix_appointments_business_status_starts_at",
        "appointments",
        ["business_id", "status", "starts_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointments_business_status_starts_at",
        table_name="appointments",
    )
    op.drop_index(
        "ix_appointments_business_starts_at",
        table_name="appointments",
    )
    op.drop_table("appointments")
    op.drop_table("appointment_settings")