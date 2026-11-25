"""Support existing Vapi-native receptionist connections.

Revision ID: 0011_vapi_native_connections
Revises: 0010_knowledge_foundation
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_vapi_native_connections"
down_revision = "0010_knowledge_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_receptionist_provisionings_status",
        "receptionist_provisionings",
        type_="check",
    )
    op.create_check_constraint(
        "ck_receptionist_provisionings_status",
        "receptionist_provisionings",
        "status IN ('draft', 'setup_in_progress', 'ready', 'connected', 'active', 'failed')",
    )
    op.add_column(
        "receptionist_provisionings",
        sa.Column(
            "connection_type",
            sa.String(length=24),
            server_default="twilio_managed",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_receptionist_provisionings_connection_type",
        "receptionist_provisionings",
        "connection_type IN ('twilio_managed', 'vapi_native')",
    )


def downgrade() -> None:
    has_native_connections = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM receptionist_provisionings "
            "WHERE connection_type = 'vapi_native')"
        )
    )
    if has_native_connections:
        raise RuntimeError(
            "Cannot downgrade while Vapi-native receptionist connections exist"
        )
    op.drop_constraint(
        "ck_receptionist_provisionings_connection_type",
        "receptionist_provisionings",
        type_="check",
    )
    op.drop_column("receptionist_provisionings", "connection_type")
    op.drop_constraint(
        "ck_receptionist_provisionings_status",
        "receptionist_provisionings",
        type_="check",
    )
    op.create_check_constraint(
        "ck_receptionist_provisionings_status",
        "receptionist_provisionings",
        "status IN ('draft', 'setup_in_progress', 'ready', 'active', 'failed')",
    )
