from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID, uuid4

import numpy as np
import pytest
from app.api.v1 import knowledge as knowledge_api
from app.core.config import Settings
from app.core.security import hash_password
from app.db.session import Base, get_db
from app.main import app
from app.models.business import Business, BusinessMember, BusinessRole
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.models.receptionist_configuration import ReceptionistConfiguration
from app.models.receptionist_provisioning import ReceptionistProvisioning
from app.models.vapi_configuration import VapiConfiguration
from app.services import document_extraction, embedding, knowledge_ingestion
from app.services.document_extraction import (
    DocumentExtractionError,
    UnsupportedDocumentType,
    extract_document_text,
    validate_upload,
)
from app.services.embedding import EmbeddingServiceError, embed_text, embed_texts
from app.services.knowledge_ingestion import ingest_document
from app.services.knowledge_retrieval import KnowledgeRetrievalResult
from app.services.text_chunking import chunk_pages
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
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def _create_workspace(
    session_factory: sessionmaker[Session],
    *,
    email: str,
) -> tuple[UUID, UUID, str]:
    from app.core.security import create_access_token
    from app.models.user import User

    with session_factory.begin() as db:
        user = User(
            full_name="Knowledge test",
            email=email,
            password_hash=hash_password("test-password"),
        )
        db.add(user)
        db.flush()
        business = Business(name=f"Workspace {email}", owner_user_id=user.id)
        db.add(business)
        db.flush()
        db.add(
            BusinessMember(
                business_id=business.id,
                user_id=user.id,
                role=BusinessRole.OWNER,
            )
        )
        user_id, business_id, version = user.id, business.id, user.token_version
    return user_id, business_id, create_access_token(user_id, version)


def _configure_vapi_tool_auth(
    monkeypatch: pytest.MonkeyPatch,
    secret: str | None = "test-tool-webhook-secret",
) -> None:
    settings = Settings(
        _env_file=None,
        DB_PASSWORD=SecretStr("test-db-password"),
        SECRET_KEY=SecretStr("s" * 32),
        VAPI_WEBHOOK_SECRET=(
            SecretStr(secret) if secret is not None else None
        ),
    )
    monkeypatch.setattr(
        "app.services.vapi_webhook_auth.get_settings",
        lambda: settings,
    )


def _add_active_knowledge_assistant(
    session_factory: sessionmaker[Session],
    business_id: UUID,
    *,
    assistant_id: str | None = None,
    phone_number_id: str | None = None,
    provisioning_status: str = "active",
) -> tuple[str, str]:
    assistant_id = assistant_id or f"assistant-{business_id}"
    phone_number_id = phone_number_id or f"phone-{business_id}"
    with session_factory.begin() as db:
        db.add(
            VapiConfiguration(
                business_id=business_id,
                assistant_id=assistant_id,
                phone_number_id=phone_number_id,
                is_enabled=True,
            )
        )
        db.add(
            ReceptionistConfiguration(
                business_id=business_id,
                assistant_name="Receptionist",
                greeting_message="Hello.",
                business_timezone="America/New_York",
                is_enabled=True,
            )
        )
        db.add(
            ReceptionistProvisioning(
                business_id=business_id,
                connection_type="vapi_native",
                status=provisioning_status,
                assistant_id=assistant_id,
                phone_number_id=phone_number_id,
            )
        )
    return assistant_id, phone_number_id


def _pdf_with_text(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def test_document_extraction_accepts_pdf_text_and_markdown() -> None:
    pdf = _pdf_with_text("Clinic services")
    pdf_upload = validate_upload("details.pdf", "application/pdf", pdf)
    assert "Clinic services" in extract_document_text(pdf_upload, pdf)[0]

    txt = b"  Weekday hours\r\n9 AM to 5 PM "
    txt_upload = validate_upload("hours.txt", "text/plain", txt)
    assert extract_document_text(txt_upload, txt) == ("Weekday hours\n9 AM to 5 PM",)

    markdown = b"# Services\n\nDental cleaning"
    md_upload = validate_upload("services.md", "text/markdown", markdown)
    assert extract_document_text(md_upload, markdown) == (
        "# Services\n\nDental cleaning",
    )


def test_document_extraction_rejects_bad_types_empty_and_invalid_content() -> None:
    with pytest.raises(UnsupportedDocumentType):
        validate_upload("script.exe", "application/octet-stream", b"x")
    with pytest.raises(UnsupportedDocumentType):
        validate_upload("guide.pdf", "text/plain", b"x")
    with pytest.raises(document_extraction.DocumentValidationError):
        validate_upload("empty.txt", "text/plain", b"")
    with pytest.raises(DocumentExtractionError):
        upload = validate_upload("bad.pdf", "application/pdf", b"not a pdf")
        extract_document_text(upload, b"not a pdf")
    with pytest.raises(DocumentExtractionError):
        upload = validate_upload("bad.txt", "text/plain", b"\xff")
        extract_document_text(upload, b"\xff")


def test_upload_filename_is_not_used_as_a_path() -> None:
    upload = validate_upload("../private/guide.txt", "text/plain", b"Public text")
    assert upload.filename == "guide.txt"


def test_chunking_is_deterministic_ordered_and_preserves_page_metadata() -> None:
    pages = [
        "First sentence. Second sentence. Third sentence. Fourth sentence.",
        "Another page has a short note.",
    ]
    first = chunk_pages(pages, chunk_size=32, overlap=8)
    second = chunk_pages(pages, chunk_size=32, overlap=8)

    assert first == second
    assert [chunk.index for chunk in first] == list(range(len(first)))
    assert first[0].metadata == {"page_number": 1}
    assert first[-1].metadata == {"page_number": 2}
    assert all(0 < len(chunk.text) <= 32 for chunk in first)


def test_chunking_handles_short_long_empty_and_overlap() -> None:
    short = chunk_pages(["Short text."], chunk_size=50, overlap=10)
    assert [chunk.text for chunk in short] == ["Short text."]
    assert chunk_pages(["  ", ""], chunk_size=50, overlap=10) == []

    text = "One two three four five six seven eight nine ten. " * 8
    chunks = chunk_pages([text], chunk_size=64, overlap=16)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 64 for chunk in chunks)
    assert any(
        chunks[index].text[-8:] in chunks[index + 1].text
        for index in range(len(chunks) - 1)
    )


def test_embedding_outputs_normalized_384_dimension_vectors_and_caches_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[str] = []
    batches: list[tuple[int, int]] = []

    class FakeModel:
        def encode(self, texts, **kwargs):
            batches.append((len(texts), kwargs["batch_size"]))
            return np.tile(np.array([1.0] + [0.0] * 383), (len(texts), 1))

    def fake_loader(model_name: str):
        loads.append(model_name)
        return FakeModel()

    monkeypatch.setattr(embedding, "_load_model", fake_loader)
    vectors = embed_texts(["first", "second"], model_name="test-model", batch_size=2)
    one_vector = embed_text("third", model_name="test-model")

    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert sum(value * value for value in vectors[0]) == pytest.approx(1.0)
    assert len(one_vector) == 384
    assert loads == ["test-model", "test-model"]
    assert batches == [(2, 2), (1, 8)]


@pytest.mark.parametrize("texts", [[], [""], ["  "]])
def test_embedding_rejects_empty_inputs(texts: list[str]) -> None:
    with pytest.raises(ValueError):
        embed_texts(texts)


def test_embedding_handles_model_load_inference_and_dimension_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_loader(_model_name: str):
        raise EmbeddingServiceError("load failed")

    monkeypatch.setattr(embedding, "_load_model", failed_loader)
    with pytest.raises(EmbeddingServiceError):
        embed_text("text")

    class WrongDimensionModel:
        def encode(self, _texts, **_kwargs):
            return np.ones((1, 3))

    monkeypatch.setattr(embedding, "_load_model", lambda _name: WrongDimensionModel())
    with pytest.raises(EmbeddingServiceError):
        embed_text("text")

    class FailedInferenceModel:
        def encode(self, _texts, **_kwargs):
            raise RuntimeError("model error")

    monkeypatch.setattr(embedding, "_load_model", lambda _name: FailedInferenceModel())
    with pytest.raises(EmbeddingServiceError):
        embed_text("text")


def test_ingestion_persists_document_and_chunks_and_is_idempotent(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, business_id, _ = _create_workspace(session_factory, email="ingest@example.test")
    monkeypatch.setattr(
        knowledge_ingestion,
        "embed_texts",
        lambda texts, **_kwargs: [[1.0] + [0.0] * 383 for _ in texts],
    )

    with session_factory() as db:
        document, created = ingest_document(
            db,
            business_id=business_id,
            filename="services.txt",
            mime_type="text/plain",
            content=b"Cleaning costs 100 dollars. Whitening costs 200 dollars.",
        )
        document_id = document.id
        assert created
        assert document.processing_status == "completed"
        assert document.error_code is None

    with session_factory() as db:
        chunks = db.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
        ).all()
        assert len(chunks) == 1
        assert chunks[0].business_id == business_id
        assert chunks[0].embedding_model
        assert chunks[0].chunk_metadata == {"page_number": 1}
        duplicate, created = ingest_document(
            db,
            business_id=business_id,
            filename="services.txt",
            mime_type="text/plain",
            content=b"Cleaning costs 100 dollars. Whitening costs 200 dollars.",
        )
        assert duplicate.id == document_id
        assert not created


def test_failed_ingestion_sets_status_and_retry_replaces_no_partial_chunks(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, business_id, _ = _create_workspace(session_factory, email="retry@example.test")

    def fail_embeddings(_texts, **_kwargs):
        raise EmbeddingServiceError("inference failed")

    monkeypatch.setattr(knowledge_ingestion, "embed_texts", fail_embeddings)
    with session_factory() as db:
        with pytest.raises(knowledge_ingestion.DocumentIngestionError):
            ingest_document(
                db,
                business_id=business_id,
                filename="faq.md",
                mime_type="text/markdown",
                content=b"# FAQ\n\nWe close at 5 PM.",
            )
        document = db.scalar(select(KnowledgeDocument))
        assert document is not None
        document_id = document.id
        assert document.processing_status == "failed"
        assert document.error_code == "embedding_failed"
        assert db.scalar(select(KnowledgeChunk.id)) is None

    monkeypatch.setattr(
        knowledge_ingestion,
        "embed_texts",
        lambda texts, **_kwargs: [[1.0] + [0.0] * 383 for _ in texts],
    )
    with session_factory() as db:
        retried, created = ingest_document(
            db,
            business_id=business_id,
            filename="faq.md",
            mime_type="text/markdown",
            content=b"# FAQ\n\nWe close at 5 PM.",
        )
        assert retried.id == document_id
        assert not created
        assert retried.processing_status == "completed"
        assert db.scalar(select(KnowledgeChunk.id)) is not None


def test_failed_extraction_is_recorded(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, business_id, _ = _create_workspace(session_factory, email="extract@example.test")
    monkeypatch.setattr(
        knowledge_ingestion,
        "embed_texts",
        lambda texts, **_kwargs: [[1.0] + [0.0] * 383 for _ in texts],
    )
    with session_factory() as db:
        with pytest.raises(knowledge_ingestion.DocumentIngestionError):
            ingest_document(
                db,
                business_id=business_id,
                filename="empty.pdf",
                mime_type="application/pdf",
                content=b"%PDF-1.4\n%%EOF",
            )
        document = db.scalar(select(KnowledgeDocument))
        assert document is not None
        assert document.processing_status == "failed"
        assert document.error_code == "extraction_failed"
        assert db.scalar(select(KnowledgeChunk.id)) is None


def test_knowledge_api_requires_membership_and_scopes_document_operations(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, business_a, token_a = _create_workspace(
        session_factory,
        email="tenant-a@example.test",
    )
    _, business_b, token_b = _create_workspace(
        session_factory,
        email="tenant-b@example.test",
    )
    monkeypatch.setattr(
        knowledge_ingestion,
        "embed_texts",
        lambda texts, **_kwargs: [[1.0] + [0.0] * 383 for _ in texts],
    )
    response = client.post(
        f"/api/v1/knowledge/documents?business_id={business_b}",
        headers={"Authorization": f"Bearer {token_b}"},
        files={"upload": ("business.txt", b"Business B information", "text/plain")},
    )
    assert response.status_code == 201
    document_id = response.json()["id"]

    headers_a = {"Authorization": f"Bearer {token_a}"}
    assert (
        client.get(
            f"/api/v1/knowledge/documents?business_id={business_b}",
            headers=headers_a,
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{document_id}?business_id={business_a}",
            headers=headers_a,
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/knowledge/documents/{document_id}?business_id={business_a}",
            headers=headers_a,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/knowledge/documents?business_id={business_b}",
            headers=headers_a,
            files={"upload": ("private.txt", b"Not authorized", "text/plain")},
        ).status_code
        == 404
    )
    assert client.get("/api/v1/knowledge/documents").status_code == 401


def test_knowledge_search_uses_authorized_business_and_returns_ranked_results(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, business_a, token_a = _create_workspace(
        session_factory,
        email="search-a@example.test",
    )
    _, business_b, _ = _create_workspace(
        session_factory,
        email="search-b@example.test",
    )
    chunk_id, document_id = UUID(int=1), UUID(int=2)
    observed_business_ids: list[UUID] = []

    def fake_retrieve(
        _db: Session,
        *,
        business_id: UUID,
        query: str,
        limit: int,
    ) -> list[KnowledgeRetrievalResult]:
        observed_business_ids.append(business_id)
        assert query == "opening hours"
        assert limit == 3
        return [
            KnowledgeRetrievalResult(
                chunk_id=chunk_id,
                document_id=document_id,
                business_id=business_id,
                chunk_index=0,
                chunk_text="Open from 9 AM to 5 PM.",
                chunk_metadata={"page_number": 2},
                document_filename="hours.pdf",
                document_source="https://example.test/hours.pdf",
                semantic_distance=0.1,
                keyword_score=0.8,
                semantic_rank=1,
                keyword_rank=1,
                rrf_score=2 / 61,
            )
        ]

    monkeypatch.setattr(knowledge_api, "retrieve_hybrid", fake_retrieve)
    response = client.post(
        f"/api/v1/knowledge/search?business_id={business_a}",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"query": "opening hours", "top_k": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert observed_business_ids == [business_a]
    assert body["query"] == "opening hours"
    assert body["results"][0]["chunk_id"] == str(chunk_id)
    assert body["results"][0]["chunk_metadata"] == {"page_number": 2}
    assert body["results"][0]["rrf_score"] == pytest.approx(2 / 61)
    assert body["documents"] == [
        {
            "document_id": str(document_id),
            "document_filename": "hours.pdf",
            "document_source": "https://example.test/hours.pdf",
        }
    ]
    assert "embedding" not in body["results"][0]

    assert (
        client.post(
            f"/api/v1/knowledge/search?business_id={business_b}",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"query": "opening hours"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/knowledge/search?business_id={business_a}",
            headers={"Authorization": f"Bearer {token_a}"},
            json={
                "query": "opening hours",
                "business_id": str(business_b),
            },
        ).status_code
        == 422
    )
    assert client.post(
        f"/api/v1/knowledge/search?business_id={business_a}",
        json={"query": "opening hours"},
    ).status_code == 401


def test_knowledge_search_returns_empty_results(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, business_id, token = _create_workspace(
        session_factory,
        email="search-empty@example.test",
    )
    monkeypatch.setattr(knowledge_api, "retrieve_hybrid", lambda *_args, **_kwargs: [])

    response = client.post(
        f"/api/v1/knowledge/search?business_id={business_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "not found"},
    )

    assert response.status_code == 200
    assert response.json() == {"query": "not found", "results": [], "documents": []}


def _vapi_tool_call(
    call_id: str,
    *,
    name: str = "search_business_knowledge",
    arguments: dict[str, object] | str | None = None,
) -> dict[str, object]:
    call: dict[str, object] = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments or {}},
    }
    return call


def _vapi_tool_payload(
    assistant_id: str,
    phone_number_id: str,
    tool_calls: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "message": {
            "type": "tool-calls",
            "call": {
                "id": "call-tool-test",
                "assistantId": assistant_id,
                "phoneNumberId": phone_number_id,
            },
            "toolCallList": tool_calls,
        }
    }


def test_vapi_knowledge_tool_supports_multiple_calls_and_ignores_forged_tenant(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_vapi_tool_auth(monkeypatch)
    _, business_a, _ = _create_workspace(
        session_factory,
        email="tool-tenant-a@example.test",
    )
    _, business_b, _ = _create_workspace(
        session_factory,
        email="tool-tenant-b@example.test",
    )
    assistant_id, phone_number_id = _add_active_knowledge_assistant(
        session_factory,
        business_a,
    )
    business_b_assistant, business_b_phone = _add_active_knowledge_assistant(
        session_factory,
        business_b,
    )
    observed_business_ids: list[UUID] = []
    retrieved_chunk_id, retrieved_document_id = uuid4(), uuid4()

    def fake_retrieve(
        _db: Session,
        *,
        business_id: UUID,
        query: str,
        limit: int,
    ) -> list[KnowledgeRetrievalResult]:
        observed_business_ids.append(business_id)
        assert limit == 5
        return [
            KnowledgeRetrievalResult(
                chunk_id=retrieved_chunk_id,
                document_id=retrieved_document_id,
                business_id=business_id,
                chunk_index=0,
                chunk_text=f"Verified answer for {query}.",
                chunk_metadata={"page_number": 2},
                document_filename="business-faq.pdf",
                document_source="https://private.example.test/faq.pdf",
                semantic_distance=0.1,
                keyword_score=0.8,
                semantic_rank=1,
                keyword_rank=1,
                rrf_score=2 / 61,
            )
        ]

    monkeypatch.setattr(
        "app.api.v1.vapi_webhooks.retrieve_hybrid",
        fake_retrieve,
    )
    payload = _vapi_tool_payload(
        assistant_id,
        phone_number_id,
        [
            _vapi_tool_call(
                "tool-call-1",
                arguments={
                    "query": "What services do you offer?",
                    "business_id": str(business_b),
                },
            ),
            _vapi_tool_call(
                "tool-call-2",
                arguments='{"query":"What are your policies?"}',
            ),
        ],
    )

    response = client.post(
        "/api/v1/integrations/vapi/knowledge-tool",
        json=payload,
        headers={"Authorization": "Bearer test-tool-webhook-secret"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert [result["toolCallId"] for result in results] == [
        "tool-call-1",
        "tool-call-2",
    ]
    assert all("error" not in result for result in results)
    assert observed_business_ids == [business_a, business_a]
    decoded_results = [json.loads(result["result"]) for result in results]
    assert all(result["found"] for result in decoded_results)
    assert all(result["sources"] == ["business-faq.pdf"] for result in decoded_results)
    serialized_response = response.text
    assert str(business_a) not in serialized_response
    assert str(business_b) not in serialized_response
    assert str(retrieved_chunk_id) not in serialized_response
    assert str(retrieved_document_id) not in serialized_response
    assert "private.example.test" not in serialized_response
    assert (business_b_assistant, business_b_phone) != (assistant_id, phone_number_id)


def test_vapi_knowledge_tool_authentication_and_malformed_payload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_vapi_tool_auth(monkeypatch)
    url = "/api/v1/integrations/vapi/knowledge-tool"
    payload = _vapi_tool_payload("assistant", "phone", [_vapi_tool_call("id")])

    assert client.post(url, json=payload).status_code == 401
    assert (
        client.post(
            url,
            content=b'{"message":',
            headers={"Authorization": "Bearer test-tool-webhook-secret"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            url,
            json={"message": {"type": "tool-calls", "toolCallList": []}},
            headers={"Authorization": "Bearer test-tool-webhook-secret"},
        ).status_code
        == 400
    )


def test_vapi_knowledge_tool_reports_invalid_unknown_and_empty_calls(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_vapi_tool_auth(monkeypatch)
    _, business_id, _ = _create_workspace(
        session_factory,
        email="tool-invalid@example.test",
    )
    assistant_id, phone_number_id = _add_active_knowledge_assistant(
        session_factory,
        business_id,
    )

    def unexpected_retrieval(*_args, **_kwargs):
        raise AssertionError("invalid tool calls must not retrieve knowledge")

    monkeypatch.setattr(
        "app.api.v1.vapi_webhooks.retrieve_hybrid",
        unexpected_retrieval,
    )
    payload = _vapi_tool_payload(
        assistant_id,
        phone_number_id,
        [
            _vapi_tool_call("unknown", name="delete_business"),
            _vapi_tool_call("missing-query", arguments={}),
            _vapi_tool_call("empty-query", arguments={"query": "  "}),
        ],
    )

    response = client.post(
        "/api/v1/integrations/vapi/knowledge-tool",
        json=payload,
        headers={"Authorization": "Bearer test-tool-webhook-secret"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert [item["toolCallId"] for item in results] == [
        "unknown",
        "missing-query",
        "empty-query",
    ]
    assert all(isinstance(item.get("error"), str) for item in results)


@pytest.mark.parametrize(
    ("assistant_id", "phone_number_id", "provisioning_status"),
    [
        ("unknown-assistant", "unknown-phone", "active"),
        ("", "", "active"),
        (None, None, "connected"),
    ],
)
def test_vapi_knowledge_tool_rejects_unknown_or_inactive_assistants(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    assistant_id: str | None,
    phone_number_id: str | None,
    provisioning_status: str,
) -> None:
    _configure_vapi_tool_auth(monkeypatch)
    _, business_id, _ = _create_workspace(
        session_factory,
        email=f"tool-untrusted-{uuid4()}@example.test",
    )
    configured_assistant, configured_phone = _add_active_knowledge_assistant(
        session_factory,
        business_id,
        provisioning_status=provisioning_status,
    )
    call_assistant = assistant_id or configured_assistant
    call_phone = phone_number_id or configured_phone
    if assistant_id == "" and phone_number_id == "":
        call_assistant = "unregistered-assistant"
        call_phone = "unregistered-phone"
    if provisioning_status == "connected":
        call_assistant = configured_assistant
        call_phone = configured_phone
    monkeypatch.setattr(
        "app.api.v1.vapi_webhooks.retrieve_hybrid",
        lambda *_args, **_kwargs: pytest.fail("inactive assistant reached retrieval"),
    )

    response = client.post(
        "/api/v1/integrations/vapi/knowledge-tool",
        json=_vapi_tool_payload(
            call_assistant,
            call_phone,
            [_vapi_tool_call("not-authorized", arguments={"query": "hours"})],
        ),
        headers={"Authorization": "Bearer test-tool-webhook-secret"},
    )

    assert response.status_code == 200
    assert "error" in response.json()["results"][0]


def test_vapi_knowledge_tool_empty_results_and_retrieval_failures_are_safe(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_vapi_tool_auth(monkeypatch)
    _, business_id, _ = _create_workspace(
        session_factory,
        email="tool-empty@example.test",
    )
    assistant_id, phone_number_id = _add_active_knowledge_assistant(
        session_factory,
        business_id,
    )
    monkeypatch.setattr(
        "app.api.v1.vapi_webhooks.retrieve_hybrid",
        lambda *_args, **_kwargs: [],
    )
    request_payload = _vapi_tool_payload(
        assistant_id,
        phone_number_id,
        [_vapi_tool_call("empty", arguments={"query": "not in documents"})],
    )
    headers = {"Authorization": "Bearer test-tool-webhook-secret"}

    empty_response = client.post(
        "/api/v1/integrations/vapi/knowledge-tool",
        json=request_payload,
        headers=headers,
    )
    assert empty_response.status_code == 200
    empty_result = json.loads(empty_response.json()["results"][0]["result"])
    assert empty_result["found"] is False
    assert empty_result["sources"] == []
    assert "Do not guess" in empty_result["context"]

    def fail_retrieval(*_args, **_kwargs):
        raise EmbeddingServiceError("private provider details")

    monkeypatch.setattr(
        "app.api.v1.vapi_webhooks.retrieve_hybrid",
        fail_retrieval,
    )
    failure_response = client.post(
        "/api/v1/integrations/vapi/knowledge-tool",
        json=request_payload,
        headers=headers,
    )
    assert failure_response.status_code == 200
    failure = failure_response.json()["results"][0]
    assert "temporarily unavailable" in failure["error"]
    assert "private provider details" not in failure["error"]


def test_vapi_knowledge_tool_does_not_return_cross_business_chunks(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_vapi_tool_auth(monkeypatch)
    _, business_a, _ = _create_workspace(
        session_factory,
        email="tool-scope-a@example.test",
    )
    _, business_b, _ = _create_workspace(
        session_factory,
        email="tool-scope-b@example.test",
    )
    assistant_id, phone_number_id = _add_active_knowledge_assistant(
        session_factory,
        business_a,
    )
    monkeypatch.setattr(
        "app.api.v1.vapi_webhooks.retrieve_hybrid",
        lambda *_args, **_kwargs: [
            KnowledgeRetrievalResult(
                chunk_id=uuid4(),
                document_id=uuid4(),
                business_id=business_b,
                chunk_index=0,
                chunk_text="Business B confidential content.",
                chunk_metadata={},
                document_filename="private.pdf",
                document_source=None,
            )
        ],
    )

    response = client.post(
        "/api/v1/integrations/vapi/knowledge-tool",
        json=_vapi_tool_payload(
            assistant_id,
            phone_number_id,
            [_vapi_tool_call("cross-tenant", arguments={"query": "hours"})],
        ),
        headers={"Authorization": "Bearer test-tool-webhook-secret"},
    )

    assert response.status_code == 200
    tool_result = response.json()["results"][0]
    assert "error" in tool_result
    assert "Business B confidential content" not in response.text
    assert "private.pdf" not in response.text
