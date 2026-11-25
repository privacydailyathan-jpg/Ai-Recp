from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.db.session import Base, get_db
from app.main import app
from app.models.billing import (
    BillingPlan,
    BillingSubscription,
    BillingSubscriptionPeriod,
)
from app.models.business import Business
from app.models.receptionist_configuration import ReceptionistConfiguration
from app.models.receptionist_provisioning import ReceptionistProvisioning
from app.models.vapi_configuration import VapiConfiguration
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def register_and_login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Onboarding User",
            "email": email,
            "password": "long-onboarding-password",
        },
    )
    assert response.status_code == 201
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "long-onboarding-password"},
    )
    assert login_response.status_code == 200
    return login_response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_settings(*, include_twilio: bool = True) -> Settings:
    values: dict[str, Any] = {
        "VAPI_API_KEY": SecretStr("test-vapi-api-key"),
        "VAPI_WEBHOOK_SECRET": SecretStr("test-vapi-webhook-secret"),
        "PUBLIC_API_BASE_URL": "https://api.example.test",
    }
    if include_twilio:
        values["TWILIO_ACCOUNT_SID"] = "AC" + "1" * 32
        values["TWILIO_AUTH_TOKEN"] = SecretStr("test-twilio-auth-token")
    return Settings(
        _env_file=None,
        DB_PASSWORD=SecretStr("test-db-password"),
        SECRET_KEY=SecretStr("s" * 32),
        **values,
    )


def create_workspace(client: TestClient, token: str) -> UUID:
    response = client.post(
        "/api/v1/businesses",
        json={"name": "Northstar Dental"},
        headers=auth(token),
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def add_active_billing_subscription(
    session_factory: sessionmaker[Session],
    business_id: UUID,
) -> None:
    now = datetime.now(UTC)
    period_start = now - timedelta(days=1)
    period_end = now + timedelta(days=29)
    with session_factory() as session:
        plan = BillingPlan(
            name=f"Test Plan {business_id}",
            monthly_price=Decimal("10.00"),
            currency="USD",
            included_minutes=100,
            max_phone_numbers=1,
        )
        session.add(plan)
        session.flush()
        subscription = BillingSubscription(
            business_id=business_id,
            plan_id=plan.id,
            provider="test",
            provider_subscription_id=f"sub-{business_id}",
            status="active",
            provider_status="active",
            current_period_start=period_start,
            current_period_end=period_end,
        )
        session.add(subscription)
        session.flush()
        session.add(
            BillingSubscriptionPeriod(
                business_id=business_id,
                subscription_id=subscription.id,
                period_start=period_start,
                period_end=period_end,
                monthly_price_snapshot=plan.monthly_price,
                currency_snapshot=plan.currency,
                included_minutes_snapshot=plan.included_minutes,
                max_phone_numbers_snapshot=plan.max_phone_numbers,
            )
        )
        session.commit()


def add_existing_vapi_configuration(
    session_factory: sessionmaker[Session],
    business_id: UUID,
    *,
    receptionist_enabled: bool = False,
    vapi_enabled: bool = False,
    requirements_text: str | None = (
        "Answer questions using the existing approved assistant setup."
    ),
) -> tuple[str, str]:
    assistant_id = f"assistant-existing-{business_id}"
    phone_number_id = f"phone-existing-{business_id}"
    with session_factory.begin() as session:
        session.add(
            ReceptionistConfiguration(
                business_id=business_id,
                assistant_name="Ryan",
                greeting_message="Thanks for calling Webox Systems.",
                business_timezone="America/New_York",
                requirements_text=requirements_text,
                selected_services=["call_answering"],
                is_enabled=receptionist_enabled,
            )
        )
        session.add(
            VapiConfiguration(
                business_id=business_id,
                assistant_id=assistant_id,
                phone_number_id=phone_number_id,
                is_enabled=vapi_enabled,
            )
        )
    return assistant_id, phone_number_id


def onboarding_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Northstar Dental",
        "industry": "Dental clinic",
        "description": "Family dental care and preventive services.",
        "website": "https://northstar.example.test",
        "address": "12 Main Street",
        "business_hours": "Monday to Friday, 9am to 5pm",
        "contact_phone": "+15551234567",
        "contact_email": "office@northstar.example.com",
        "country_code": "US",
        "requirements_text": (
            "We miss calls after work. Answer questions about our services and "
            "hours. If unsure, explain the team will follow up."
        ),
        "fallback_instructions": "Do not guess; tell callers the team will follow up.",
        "selected_services": ["call_answering"],
        "business_timezone": "America/New_York",
    }
    payload.update(overrides)
    return payload


class FakeVapiClient:
    instances: ClassVar[list[FakeVapiClient]] = []
    assistants_by_id: ClassVar[dict[str, dict[str, Any]]] = {}
    assistant_updates: ClassVar[list[tuple[str, dict[str, Any]]]] = []
    assistant_reads: ClassVar[list[str]] = []
    fail_assistant_update: ClassVar[bool] = False

    def __init__(self, _api_key: str) -> None:
        self.assistants: dict[str, dict[str, Any]] = {}
        self.phones: dict[str, dict[str, Any]] = {}
        self.created_assistants = 0
        self.imported_phones = 0
        self.assignments: list[tuple[str, str]] = []
        self.__class__.instances.append(self)

    def get_assistant(self, assistant_id: str) -> dict[str, Any]:
        self.__class__.assistant_reads.append(assistant_id)
        if assistant_id not in self.__class__.assistants_by_id:
            self.__class__.assistants_by_id[assistant_id] = {
                "id": assistant_id,
                "firstMessage": "Existing custom greeting.",
                "voice": {"provider": "custom-voice", "voiceId": "voice-kept"},
                "model": {
                    "provider": "custom-provider",
                    "model": "custom-model",
                    "messages": [{"role": "system", "content": "Existing instructions."}],
                    "tools": [],
                },
                "server": {"url": "https://existing.example.test/webhook"},
            }
        return self.__class__.assistants_by_id[assistant_id]

    def find_assistant(self, provisioning_id: str) -> dict[str, Any] | None:
        return self.assistants.get(provisioning_id)

    def create_assistant(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.created_assistants += 1
        assistant = {"id": "assistant-managed", **payload}
        self.assistants[payload["metadata"]["provisioningId"]] = assistant
        return assistant

    def find_phone_number(
        self, name: str, phone_number: str
    ) -> dict[str, Any] | None:
        return self.phones.get(name) if self.phones.get(name, {}).get("number") == phone_number else None

    def import_twilio_number(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.imported_phones += 1
        phone = {"id": "phone-managed", **payload}
        self.phones[payload["name"]] = phone
        return phone

    def assign_assistant(self, phone_number_id: str, assistant_id: str) -> None:
        self.assignments.append((phone_number_id, assistant_id))

    def update_assistant(self, assistant_id: str, payload: dict[str, Any]) -> None:
        if self.__class__.fail_assistant_update:
            from app.integrations.vapi.client import VapiProviderError

            raise VapiProviderError(500)
        self.__class__.assistant_updates.append((assistant_id, payload))
        self.__class__.assistants_by_id[assistant_id].update(payload)

    def close(self) -> None:
        return None


class FakeTwilioClient:
    instances: ClassVar[list[FakeTwilioClient]] = []
    fail_availability_once = False

    def __init__(self, _account_sid: str, _auth_token: str) -> None:
        self.owned: dict[str, dict[str, str]] = {}
        self.purchases = 0
        self.__class__.instances.append(self)

    def find_available_number(self, country_code: str) -> str:
        assert country_code == "US"
        if self.__class__.fail_availability_once:
            self.__class__.fail_availability_once = False
            from app.integrations.vapi.twilio_client import TwilioProviderError

            raise TwilioProviderError(no_number=True)
        return "+12125550123"

    def find_owned_number(self, phone_number: str) -> dict[str, str] | None:
        return self.owned.get(phone_number)

    def purchase_number(self, phone_number: str) -> dict[str, str]:
        self.purchases += 1
        number = {"sid": "PN-managed", "phone_number": phone_number}
        self.owned[phone_number] = number
        return number

    def close(self) -> None:
        return None


def install_fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeVapiClient.instances.clear()
    FakeVapiClient.assistants_by_id.clear()
    FakeVapiClient.assistant_updates.clear()
    FakeVapiClient.assistant_reads.clear()
    FakeVapiClient.fail_assistant_update = False
    FakeTwilioClient.instances.clear()
    FakeTwilioClient.fail_availability_once = False
    monkeypatch.setattr(
        "app.services.receptionist_provisioning.get_settings",
        make_settings,
    )
    monkeypatch.setattr(
        "app.services.receptionist_provisioning.VapiClient",
        FakeVapiClient,
    )
    monkeypatch.setattr(
        "app.services.receptionist_provisioning.TwilioClient",
        FakeTwilioClient,
    )


def test_onboarding_saves_business_requirements_and_supported_service(
    client: TestClient,
) -> None:
    token = register_and_login(client, "onboarding@example.com")
    business_id = create_workspace(client, token)

    saved = client.put(
        f"/api/v1/businesses/{business_id}/onboarding",
        json=onboarding_payload(),
        headers=auth(token),
    )
    assert saved.status_code == 200
    result = saved.json()
    assert result["industry"] == "Dental clinic"
    assert result["requirements_text"].startswith("We miss calls")
    assert result["selected_services"] == ["call_answering"]
    assert result["status"] == "draft"

    unsupported = client.put(
        f"/api/v1/businesses/{business_id}/onboarding",
        json=onboarding_payload(selected_services=["appointment_booking"]),
        headers=auth(token),
    )
    assert unsupported.status_code == 422


def test_onboarding_and_provisioning_are_business_scoped(client: TestClient) -> None:
    owner_token = register_and_login(client, "owner-onboarding@example.com")
    other_token = register_and_login(client, "other-onboarding@example.com")
    business_id = create_workspace(client, owner_token)

    response = client.get(
        f"/api/v1/businesses/{business_id}/onboarding",
        headers=auth(other_token),
    )
    assert response.status_code == 404

    provision = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/provision",
        json={"accept_phone_number_provisioning": True},
        headers=auth(other_token),
    )
    assert provision.status_code == 404
    activate_existing = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/activate-existing",
        headers=auth(other_token),
    )
    assert activate_existing.status_code == 404


def test_business_admin_cannot_trigger_billable_phone_provisioning(
    client: TestClient,
) -> None:
    owner_token = register_and_login(client, "owner-billing@example.com")
    admin_token = register_and_login(client, "admin-billing@example.com")
    business_id = create_workspace(client, owner_token)
    member = client.post(
        f"/api/v1/businesses/{business_id}/members",
        json={"email": "admin-billing@example.com", "role": "admin"},
        headers=auth(owner_token),
    )
    assert member.status_code == 201
    saved = client.put(
        f"/api/v1/businesses/{business_id}/onboarding",
        json=onboarding_payload(),
        headers=auth(owner_token),
    )
    assert saved.status_code == 200

    response = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/provision",
        json={"accept_phone_number_provisioning": True},
        headers=auth(admin_token),
    )
    assert response.status_code == 403


def test_phone_number_provisioning_requires_explicit_consent(
    client: TestClient,
) -> None:
    token = register_and_login(client, "consent-onboarding@example.com")
    business_id = create_workspace(client, token)
    saved = client.put(
        f"/api/v1/businesses/{business_id}/onboarding",
        json=onboarding_payload(),
        headers=auth(token),
    )
    assert saved.status_code == 200

    response = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/provision",
        json={"accept_phone_number_provisioning": False},
        headers=auth(token),
    )
    assert response.status_code == 422
    onboarding = client.get(
        f"/api/v1/businesses/{business_id}/onboarding",
        headers=auth(token),
    )
    assert onboarding.status_code == 200
    assert onboarding.json()["phone_number_consent_given"] is False


def test_managed_provisioning_is_idempotent_and_activation_is_truthful(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_providers(monkeypatch)
    token = register_and_login(client, "managed-onboarding@example.com")
    business_id = create_workspace(client, token)
    add_active_billing_subscription(session_factory, business_id)
    saved = client.put(
        f"/api/v1/businesses/{business_id}/onboarding",
        json=onboarding_payload(),
        headers=auth(token),
    )
    assert saved.status_code == 200

    provision_url = f"/api/v1/businesses/{business_id}/receptionist/provision"
    ready = client.post(
        provision_url,
        json={"accept_phone_number_provisioning": True},
        headers=auth(token),
    )
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["phone_number"] == "+12125550123"
    assert ready.json()["phone_number_consent_given"] is True
    assert "assistant_id" not in ready.json()
    assert "phone_number_id" not in ready.json()

    retried = client.post(
        provision_url,
        json={"accept_phone_number_provisioning": True},
        headers=auth(token),
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "ready"
    fake_vapi = FakeVapiClient.instances[-1]
    fake_twilio = FakeTwilioClient.instances[0]
    assert fake_vapi.created_assistants == 1
    assert fake_vapi.imported_phones == 1
    assert fake_twilio.purchases == 1

    activated = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/activate",
        headers=auth(token),
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    fake_vapi = FakeVapiClient.instances[-1]
    assert fake_vapi.assignments == [("phone-managed", "assistant-managed")]

    activation_retry = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/activate",
        headers=auth(token),
    )
    assert activation_retry.status_code == 200
    assert len(fake_vapi.assignments) == 1


def test_assistant_payload_adds_authenticated_knowledge_tool_without_logging_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services.receptionist_provisioning import _assistant_payload

    settings = make_settings()
    webhook_secret = settings.VAPI_WEBHOOK_SECRET.get_secret_value()
    business_id = uuid4()
    business = Business(
        id=business_id,
        name="Northstar Dental",
        owner_user_id=uuid4(),
    )
    receptionist = ReceptionistConfiguration(
        id=uuid4(),
        business_id=business_id,
        assistant_name="Ryan",
        greeting_message="Thanks for calling Northstar Dental.",
        business_timezone="America/New_York",
        requirements_text="Answer detailed questions using verified sources.",
        selected_services=["call_answering"],
    )
    provisioning = ReceptionistProvisioning(
        id=uuid4(),
        business_id=business_id,
        status="ready",
        assistant_id="assistant-test",
        phone_number_id="phone-test",
    )

    payload = _assistant_payload(
        business,
        receptionist,
        provisioning,
        settings,
    )

    assert payload["name"] == "Northstar Dental Receptionist"
    assert payload["firstMessage"] == receptionist.greeting_message
    assert payload["model"]["provider"] == "openai"
    assert payload["model"]["model"] == "gpt-4o-mini"
    assert payload["voice"] == {"provider": "11labs", "voiceId": "paula"}
    assert payload["serverMessages"] == ["status-update", "end-of-call-report"]
    tool = payload["model"]["tools"][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "search_business_knowledge"
    assert tool["function"]["parameters"]["required"] == ["query"]
    assert set(tool["function"]["parameters"]["properties"]) == {"query"}
    assert tool["server"]["url"].endswith(
        "/api/v1/integrations/vapi/knowledge-tool"
    )
    assert tool["server"]["headers"]["Authorization"] == f"Bearer {webhook_secret}"
    assert tool["server"]["timeoutSeconds"] == 20
    assert webhook_secret not in caplog.text
    assert "Search the business knowledge base" in payload["model"]["messages"][0][
        "content"
    ]


def test_existing_vapi_connection_activates_and_preserves_assistant_settings(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_providers(monkeypatch)
    monkeypatch.setattr(
        "app.services.receptionist_provisioning.get_settings",
        lambda: make_settings(include_twilio=False),
    )
    token = register_and_login(client, "native-onboarding@example.com")
    business_id = create_workspace(client, token)
    assistant_id, phone_number_id = add_existing_vapi_configuration(
        session_factory,
        business_id,
        requirements_text=None,
    )
    existing_assistant = {
        "id": assistant_id,
        "firstMessage": "Keep this approved greeting.",
        "voice": {"provider": "existing-voice", "voiceId": "existing-voice-id"},
        "server": {"url": "https://existing.example.test/webhook"},
        "serverMessages": ["status-update", "end-of-call-report"],
        "model": {
            "provider": "existing-provider",
            "model": "existing-model",
            "messages": [{"role": "system", "content": "Keep these instructions."}],
            "temperature": 0.3,
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "unrelated_tool"},
                    "server": {"url": "https://unrelated.example.test"},
                },
                {
                    "type": "function",
                    "function": {"name": "search_business_knowledge"},
                    "server": {"url": "https://old.example.test"},
                },
            ],
        },
    }
    FakeVapiClient.assistants_by_id[assistant_id] = existing_assistant

    import app.services.receptionist_provisioning as provisioning_service

    activate = provisioning_service.activate_receptionist
    observed_connected_state: list[tuple[str, str]] = []

    def observe_connected_state(*args, **kwargs):
        provisioning = args[1]
        observed_connected_state.append(
            (provisioning.connection_type, provisioning.status)
        )
        return activate(*args, **kwargs)

    monkeypatch.setattr(
        provisioning_service,
        "activate_receptionist",
        observe_connected_state,
    )
    activate_url = (
        f"/api/v1/businesses/{business_id}/receptionist/activate-existing"
    )

    response = client.post(activate_url, headers=auth(token))

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["connection_type"] == "vapi_native"
    assert response.json()["phone_number"] is None
    assert response.json()["phone_number_consent_given"] is False
    assert observed_connected_state == [("vapi_native", "connected")]
    assert FakeVapiClient.assistant_reads == [assistant_id]
    assert len(FakeVapiClient.assistant_updates) == 1
    updated_id, update_payload = FakeVapiClient.assistant_updates[0]
    assert updated_id == assistant_id
    assert set(update_payload) == {"model"}
    updated_assistant = FakeVapiClient.assistants_by_id[assistant_id]
    assert updated_assistant["firstMessage"] == "Keep this approved greeting."
    assert updated_assistant["voice"] == {
        "provider": "existing-voice",
        "voiceId": "existing-voice-id",
    }
    assert updated_assistant["server"] == {
        "url": "https://existing.example.test/webhook"
    }
    updated_model = updated_assistant["model"]
    assert updated_model["provider"] == "existing-provider"
    assert updated_model["model"] == "existing-model"
    assert updated_model["messages"] == [
        {"role": "system", "content": "Keep these instructions."}
    ]
    assert updated_model["temperature"] == 0.3
    assert [tool["function"]["name"] for tool in updated_model["tools"]] == [
        "unrelated_tool",
        "search_business_knowledge",
    ]
    assert FakeVapiClient.instances
    assert FakeTwilioClient.instances == []

    with session_factory() as session:
        provisioning = session.scalar(
            select(ReceptionistProvisioning).where(
                ReceptionistProvisioning.business_id == business_id
            )
        )
        receptionist = session.scalar(
            select(ReceptionistConfiguration).where(
                ReceptionistConfiguration.business_id == business_id
            )
        )
        vapi = session.scalar(
            select(VapiConfiguration).where(
                VapiConfiguration.business_id == business_id
            )
        )
        assert provisioning is not None
        assert provisioning.status == "active"
        assert provisioning.connection_type == "vapi_native"
        assert provisioning.assistant_id == assistant_id
        assert provisioning.phone_number_id == phone_number_id
        assert provisioning.phone_number is None
        assert provisioning.twilio_phone_sid is None
        assert provisioning.consented_at is None
        assert provisioning.consented_by_user_id is None
        assert receptionist is not None and receptionist.is_enabled
        assert vapi is not None and vapi.is_enabled

    retry = client.post(activate_url, headers=auth(token))
    assert retry.status_code == 200
    assert retry.json()["status"] == "active"
    assert len(FakeVapiClient.assistant_reads) == 2
    assert len(FakeVapiClient.assistant_updates) == 1
    assert FakeTwilioClient.instances == []


def test_onboarding_save_does_not_create_twilio_draft_for_existing_vapi_connection(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_providers(monkeypatch)
    monkeypatch.setattr(
        "app.services.receptionist_provisioning.get_settings",
        lambda: make_settings(include_twilio=False),
    )
    token = register_and_login(client, "native-onboarding-save@example.com")
    business_id = create_workspace(client, token)
    with session_factory.begin() as session:
        session.add(
            VapiConfiguration(
                business_id=business_id,
                assistant_id=f"assistant-existing-{business_id}",
                phone_number_id=f"phone-existing-{business_id}",
                is_enabled=False,
            )
        )

    saved = client.put(
        f"/api/v1/businesses/{business_id}/onboarding",
        json=onboarding_payload(assistant_name="Ryan"),
        headers=auth(token),
    )

    assert saved.status_code == 200
    assert saved.json()["connection_type"] is None

    provisioning_attempt = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/provision",
        json={"accept_phone_number_provisioning": True},
        headers=auth(token),
    )
    assert provisioning_attempt.status_code == 409
    with session_factory() as session:
        assert (
            session.scalar(
                select(ReceptionistProvisioning.id).where(
                    ReceptionistProvisioning.business_id == business_id
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(ReceptionistConfiguration).where(
                    ReceptionistConfiguration.business_id == business_id
                )
            ).is_enabled
            is False
        )

    activated = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/activate-existing",
        headers=auth(token),
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert activated.json()["connection_type"] == "vapi_native"
    assert FakeVapiClient.assistant_updates
    assert FakeTwilioClient.instances == []


@pytest.mark.parametrize("mismatched_id", ["assistant_id", "phone_number_id"])
def test_existing_vapi_activation_rejects_mismatched_provisioning_ids(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    mismatched_id: str,
) -> None:
    install_fake_providers(monkeypatch)
    monkeypatch.setattr(
        "app.services.receptionist_provisioning.get_settings",
        lambda: make_settings(include_twilio=False),
    )
    token = register_and_login(client, f"native-mismatch-{mismatched_id}@example.com")
    business_id = create_workspace(client, token)
    assistant_id, phone_number_id = add_existing_vapi_configuration(
        session_factory,
        business_id,
    )
    persisted_ids = {
        "assistant_id": assistant_id,
        "phone_number_id": phone_number_id,
    }
    persisted_ids[mismatched_id] = f"unrelated-{mismatched_id}"
    with session_factory.begin() as session:
        session.add(
            ReceptionistProvisioning(
                business_id=business_id,
                connection_type="vapi_native",
                status="connected",
                **persisted_ids,
            )
        )

    response = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/activate-existing",
        headers=auth(token),
    )

    assert response.status_code == 409
    assert FakeVapiClient.instances == []
    assert FakeTwilioClient.instances == []
    with session_factory() as session:
        provisioning = session.scalar(
            select(ReceptionistProvisioning).where(
                ReceptionistProvisioning.business_id == business_id
            )
        )
        receptionist = session.scalar(
            select(ReceptionistConfiguration).where(
                ReceptionistConfiguration.business_id == business_id
            )
        )
        vapi = session.scalar(
            select(VapiConfiguration).where(
                VapiConfiguration.business_id == business_id
            )
        )
        assert provisioning is not None
        assert getattr(provisioning, mismatched_id) == persisted_ids[mismatched_id]
        assert receptionist is not None and not receptionist.is_enabled
        assert vapi is not None and not vapi.is_enabled


@pytest.mark.parametrize(
    "missing_setting",
    ["VAPI_API_KEY", "VAPI_WEBHOOK_SECRET", "PUBLIC_API_BASE_URL"],
)
def test_existing_vapi_activation_requires_webhook_configuration_and_stays_disabled(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    missing_setting: str,
) -> None:
    install_fake_providers(monkeypatch)
    settings_values: dict[str, Any] = {
        "VAPI_API_KEY": SecretStr("test-vapi-api-key"),
        "VAPI_WEBHOOK_SECRET": SecretStr("test-vapi-webhook-secret"),
        "PUBLIC_API_BASE_URL": "https://api.example.test",
    }
    settings_values.pop(missing_setting)
    settings = Settings(
        _env_file=None,
        DB_PASSWORD=SecretStr("test-db-password"),
        SECRET_KEY=SecretStr("s" * 32),
        **settings_values,
    )
    monkeypatch.setattr(
        "app.services.receptionist_provisioning.get_settings",
        lambda: settings,
    )
    token = register_and_login(client, f"native-settings-{missing_setting}@example.com")
    business_id = create_workspace(client, token)
    assistant_id, phone_number_id = add_existing_vapi_configuration(
        session_factory,
        business_id,
        receptionist_enabled=True,
        vapi_enabled=True,
    )
    with session_factory.begin() as session:
        session.add(
            ReceptionistProvisioning(
                business_id=business_id,
                connection_type="vapi_native",
                status="active",
                assistant_id=assistant_id,
                phone_number_id=phone_number_id,
            )
        )

    response = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/activate",
        headers=auth(token),
    )

    assert response.status_code == 503
    assert missing_setting in response.json()["detail"]
    assert FakeVapiClient.instances == []
    assert FakeTwilioClient.instances == []
    with session_factory() as session:
        assert (
            session.scalar(
                select(ReceptionistProvisioning.id).where(
                    ReceptionistProvisioning.business_id == business_id
                )
            )
            is not None
        )
        provisioning = session.scalar(
            select(ReceptionistProvisioning).where(
                ReceptionistProvisioning.business_id == business_id
            )
        )
        assert provisioning is not None
        assert provisioning.status == "connected"
        receptionist = session.scalar(
            select(ReceptionistConfiguration).where(
                ReceptionistConfiguration.business_id == business_id
            )
        )
        vapi = session.scalar(
            select(VapiConfiguration).where(
                VapiConfiguration.business_id == business_id
            )
        )
        assert receptionist is not None and not receptionist.is_enabled
        assert vapi is not None and not vapi.is_enabled


def test_existing_vapi_activation_rejects_invalid_receptionist_configuration(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_providers(monkeypatch)
    monkeypatch.setattr(
        "app.services.receptionist_provisioning.get_settings",
        lambda: make_settings(include_twilio=False),
    )
    token = register_and_login(client, "native-invalid-config@example.com")
    business_id = create_workspace(client, token)
    add_existing_vapi_configuration(
        session_factory,
        business_id,
        receptionist_enabled=True,
        vapi_enabled=True,
    )
    with session_factory.begin() as session:
        receptionist = session.scalar(
            select(ReceptionistConfiguration).where(
                ReceptionistConfiguration.business_id == business_id
            )
        )
        assert receptionist is not None
        receptionist.business_timezone = "Not/A_Timezone"

    response = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/activate-existing",
        headers=auth(token),
    )

    assert response.status_code == 422
    assert FakeVapiClient.instances == []
    assert FakeTwilioClient.instances == []
    with session_factory() as session:
        receptionist = session.scalar(
            select(ReceptionistConfiguration).where(
                ReceptionistConfiguration.business_id == business_id
            )
        )
        vapi = session.scalar(
            select(VapiConfiguration).where(
                VapiConfiguration.business_id == business_id
            )
        )
        assert receptionist is not None and not receptionist.is_enabled
        assert vapi is not None and not vapi.is_enabled


def test_provisioning_failure_is_persisted_and_can_be_retried(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_providers(monkeypatch)
    FakeTwilioClient.fail_availability_once = True
    token = register_and_login(client, "retry-onboarding@example.com")
    business_id = create_workspace(client, token)
    add_active_billing_subscription(session_factory, business_id)
    client.put(
        f"/api/v1/businesses/{business_id}/onboarding",
        json=onboarding_payload(),
        headers=auth(token),
    )
    provision_url = f"/api/v1/businesses/{business_id}/receptionist/provision"

    failed = client.post(
        provision_url,
        json={"accept_phone_number_provisioning": True},
        headers=auth(token),
    )
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["safe_error_code"] == "twilio_no_number"
    assert failed.json()["error_message"]

    retried = client.post(
        provision_url,
        json={"accept_phone_number_provisioning": True},
        headers=auth(token),
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "ready"


@pytest.mark.parametrize(
    "missing_setting",
    [
        "VAPI_API_KEY",
        "VAPI_WEBHOOK_SECRET",
        "PUBLIC_API_BASE_URL",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
    ],
)
def test_provisioning_requires_platform_credentials(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    missing_setting: str,
) -> None:
    install_fake_providers(monkeypatch)
    settings_values: dict[str, Any] = {
        "VAPI_API_KEY": SecretStr("test-vapi-api-key"),
        "VAPI_WEBHOOK_SECRET": SecretStr("test-vapi-webhook-secret"),
        "PUBLIC_API_BASE_URL": "https://api.example.test",
        "TWILIO_ACCOUNT_SID": "AC" + "1" * 32,
        "TWILIO_AUTH_TOKEN": SecretStr("test-twilio-auth-token"),
    }
    settings_values.pop(missing_setting)
    settings = Settings(
        _env_file=None,
        DB_PASSWORD=SecretStr("test-db-password"),
        SECRET_KEY=SecretStr("s" * 32),
        **settings_values,
    )
    monkeypatch.setattr(
        "app.services.receptionist_provisioning.get_settings",
        lambda: settings,
    )
    token = register_and_login(client, "missing-provider@example.com")
    business_id = create_workspace(client, token)
    add_active_billing_subscription(session_factory, business_id)
    client.put(
        f"/api/v1/businesses/{business_id}/onboarding",
        json=onboarding_payload(),
        headers=auth(token),
    )

    response = client.post(
        f"/api/v1/businesses/{business_id}/receptionist/provision",
        json={"accept_phone_number_provisioning": True},
        headers=auth(token),
    )
    assert response.status_code == 503
    assert missing_setting in response.json()["detail"]
    assert FakeVapiClient.instances == []
    assert FakeTwilioClient.instances == []
