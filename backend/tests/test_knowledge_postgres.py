from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from app.db.session import Base
from app.models.business import Business
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.models.user import User
from app.services import knowledge_retrieval
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture
def postgres_session_factory() -> Iterator[sessionmaker[Session]]:
    database_url = os.environ.get("VAPI_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set VAPI_TEST_DATABASE_URL to an isolated local PostgreSQL DB")
    url = make_url(database_url)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in _LOCAL_HOSTS
        or url.database is None
        or not url.database.startswith("vapi_test_")
    ):
        pytest.fail(
            "VAPI_TEST_DATABASE_URL must target a local database named vapi_test_*"
        )

    engine = create_engine(database_url, pool_size=2, max_overflow=0)
    schema_name = f"knowledge_test_{uuid4().hex}"
    schema_created = False
    try:
        with engine.begin() as connection:
            actual_database = connection.scalar(text("SELECT current_database()"))
            if actual_database != url.database:
                pytest.fail("Connected database does not match test database URL")
            vector_available = connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_available_extensions "
                    "WHERE name = 'vector')"
                )
            )
            vector_enabled = connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension "
                    "WHERE extname = 'vector')"
                )
            )
            if not vector_available or not vector_enabled:
                pytest.skip(
                    "pgvector must be installed and enabled for integration tests"
                )
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
            schema_created = True

        test_engine = engine.execution_options(schema_translate_map={None: schema_name})
        tables = [
            User.__table__,
            Business.__table__,
            KnowledgeDocument.__table__,
            KnowledgeChunk.__table__,
        ]
        Base.metadata.create_all(test_engine, tables=tables)
        yield sessionmaker(
            bind=test_engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )
    finally:
        if schema_created:
            test_engine = engine.execution_options(
                schema_translate_map={None: schema_name}
            )
            Base.metadata.drop_all(
                test_engine,
                tables=[
                    KnowledgeChunk.__table__,
                    KnowledgeDocument.__table__,
                    Business.__table__,
                    User.__table__,
                ],
            )
            with engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}"')
        engine.dispose()


def test_knowledge_chunk_rejects_cross_business_document_reference(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    with postgres_session_factory() as db:
        user_a = User(
            full_name="Tenant A",
            email=f"{uuid4()}@example.test",
            password_hash="test",
        )
        user_b = User(
            full_name="Tenant B",
            email=f"{uuid4()}@example.test",
            password_hash="test",
        )
        db.add_all([user_a, user_b])
        db.flush()
        business_a = Business(name="A", owner_user_id=user_a.id)
        business_b = Business(name="B", owner_user_id=user_b.id)
        db.add_all([business_a, business_b])
        db.flush()
        document = KnowledgeDocument(
            business_id=business_a.id,
            original_filename="a.txt",
            document_type="text",
            mime_type="text/plain",
            file_size_bytes=1,
            content_sha256="a" * 64,
            processing_status="completed",
        )
        db.add(document)
        db.flush()

        chunk = KnowledgeChunk(
            document_id=document.id,
            business_id=business_b.id,
            chunk_index=0,
            chunk_text="cross tenant",
            embedding=[1.0] + [0.0] * 383,
            embedding_model="test",
            chunk_metadata={},
        )
        db.add(chunk)
        with pytest.raises(IntegrityError):
            db.commit()


def test_postgres_hybrid_retrieval_filters_tenants_and_incomplete_documents(
    postgres_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_vector = [1.0] + [0.0] * 383
    orthogonal_vector = [0.0, 1.0] + [0.0] * 382
    monkeypatch.setattr(knowledge_retrieval, "embed_text", lambda _query: query_vector)

    with postgres_session_factory.begin() as db:
        user_a = User(
            full_name="Retrieval A",
            email=f"{uuid4()}@example.test",
            password_hash="test",
        )
        user_b = User(
            full_name="Retrieval B",
            email=f"{uuid4()}@example.test",
            password_hash="test",
        )
        db.add_all([user_a, user_b])
        db.flush()
        business_a = Business(name="Retrieval A", owner_user_id=user_a.id)
        business_b = Business(name="Retrieval B", owner_user_id=user_b.id)
        db.add_all([business_a, business_b])
        db.flush()

        documents = [
            KnowledgeDocument(
                business_id=business_a.id,
                original_filename=name,
                document_type="text",
                mime_type="text/plain",
                file_size_bytes=1,
                content_sha256=hash_value * 64,
                processing_status=processing_status,
            )
            for name, hash_value, processing_status in (
                ("best.txt", "a", "completed"),
                ("other.txt", "b", "completed"),
                ("pending.txt", "c", "pending"),
                ("processing.txt", "d", "processing"),
                ("failed.txt", "e", "failed"),
            )
        ]
        tenant_document = KnowledgeDocument(
            business_id=business_b.id,
            original_filename="tenant.txt",
            document_type="text",
            mime_type="text/plain",
            file_size_bytes=1,
            content_sha256="f" * 64,
            processing_status="completed",
        )
        empty_business = Business(name="Empty", owner_user_id=user_a.id)
        db.add_all([*documents, tenant_document, empty_business])
        db.flush()
        chunks = [
            KnowledgeChunk(
                document_id=document.id,
                business_id=document.business_id,
                chunk_index=0,
                chunk_text=chunk_text,
                embedding=vector,
                embedding_model="test",
                chunk_metadata={"page_number": index + 1},
            )
            for index, (document, chunk_text, vector) in enumerate(
                [
                    (
                        documents[0],
                        "Dentistry hours dentistry hours open Monday",
                        query_vector,
                    ),
                    (documents[1], "Dentistry hours are nine to five", orthogonal_vector),
                    (
                        documents[2],
                        "Dentistry hours pending document",
                        query_vector,
                    ),
                    (
                        documents[3],
                        "Dentistry hours processing document",
                        query_vector,
                    ),
                    (documents[4], "Dentistry hours failed document", query_vector),
                    (
                        tenant_document,
                        "Dentistry hours other tenant",
                        query_vector,
                    ),
                ]
            )
        ]
        db.add_all(chunks)
        business_a_id = business_a.id
        empty_business_id = empty_business.id

    with postgres_session_factory() as db:
        semantic = knowledge_retrieval.retrieve_semantic(
            db,
            business_id=business_a_id,
            query="dentistry hours",
            limit=10,
        )
        keyword = knowledge_retrieval.retrieve_keyword(
            db,
            business_id=business_a_id,
            query="dentistry hours",
            limit=10,
        )
        hybrid = knowledge_retrieval.retrieve_hybrid(
            db,
            business_id=business_a_id,
            query="dentistry hours",
            limit=10,
        )
        empty = knowledge_retrieval.retrieve_hybrid(
            db,
            business_id=empty_business_id,
            query="dentistry hours",
            limit=10,
        )

    completed_ids = {chunks[0].id, chunks[1].id}
    assert [result.chunk_id for result in semantic] == [chunks[0].id, chunks[1].id]
    assert [result.semantic_distance for result in semantic] == pytest.approx([0, 1])
    assert [result.semantic_rank for result in semantic] == [1, 2]
    assert {result.chunk_id for result in keyword} == completed_ids
    assert [result.keyword_score for result in keyword] == sorted(
        [result.keyword_score for result in keyword], reverse=True
    )
    assert {result.chunk_id for result in hybrid} == completed_ids
    assert len({result.chunk_id for result in hybrid}) == len(hybrid)
    assert all(result.semantic_rank and result.keyword_rank for result in hybrid)
    assert empty == []
