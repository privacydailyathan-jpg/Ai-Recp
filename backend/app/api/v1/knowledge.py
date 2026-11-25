from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_business_permission
from app.core.config import get_settings
from app.db.session import get_db
from app.models.business import Business
from app.models.knowledge import KnowledgeDocument
from app.schemas.knowledge import (
    KnowledgeDocumentPage,
    KnowledgeDocumentRead,
    KnowledgeSearchChunk,
    KnowledgeSearchDocumentReference,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.services.business_authorization import BusinessPermission
from app.services.document_extraction import DocumentValidationError
from app.services.embedding import EmbeddingServiceError
from app.services.knowledge_ingestion import (
    DocumentAlreadyProcessing,
    DocumentIngestionError,
    ingest_document,
)
from app.services.knowledge_retrieval import (
    KnowledgeRetrievalResult,
    retrieve_hybrid,
)

router = APIRouter(prefix="/knowledge/documents", tags=["knowledge documents"])
search_router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := upload.file.read(min(64 * 1024, max_bytes + 1 - size)):
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="The uploaded document exceeds the size limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=KnowledgeDocumentRead)
def upload_knowledge_document(
    business_id: Annotated[
        UUID,
        Query(description="Business workspace that will own this document"),
    ],
    business: Annotated[
        Business,
        Depends(
            require_business_permission(BusinessPermission.MANAGE_OPERATIONAL_RESOURCES)
        ),
    ],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    upload: Annotated[UploadFile, File()],
    source: Annotated[str | None, Form(max_length=2048)] = None,
) -> KnowledgeDocumentRead:
    content = _read_upload(
        upload,
        get_settings().KNOWLEDGE_MAX_UPLOAD_BYTES,
    )
    try:
        document, created = ingest_document(
            db,
            business_id=business.id,
            filename=upload.filename,
            mime_type=upload.content_type,
            content=content,
            source=source,
        )
    except DocumentValidationError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error
    except DocumentAlreadyProcessing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document is already being processed",
        ) from None
    except DocumentIngestionError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if error.unavailable
                else status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail="Document processing failed",
        ) from None
    if created:
        response.status_code = status.HTTP_201_CREATED
    return document


@router.get("", response_model=KnowledgeDocumentPage)
def list_knowledge_documents(
    business: Annotated[
        Business,
        Depends(require_business_permission(BusinessPermission.READ)),
    ],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> KnowledgeDocumentPage:
    filters = (KnowledgeDocument.business_id == business.id,)
    total = (
        db.scalar(select(func.count()).select_from(KnowledgeDocument).where(*filters))
        or 0
    )
    documents = db.scalars(
        select(KnowledgeDocument)
        .where(*filters)
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return KnowledgeDocumentPage(
        items=[
            KnowledgeDocumentRead.model_validate(document) for document in documents
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=KnowledgeDocumentRead)
def get_knowledge_document(
    document_id: UUID,
    business: Annotated[
        Business,
        Depends(require_business_permission(BusinessPermission.READ)),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> KnowledgeDocument:
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.business_id == business.id,
        )
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge document not found",
        )
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_document(
    document_id: UUID,
    business: Annotated[
        Business,
        Depends(
            require_business_permission(BusinessPermission.MANAGE_OPERATIONAL_RESOURCES)
        ),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.business_id == business.id,
        )
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge document not found",
        )
    db.delete(document)
    db.commit()


@search_router.post("/search", response_model=KnowledgeSearchResponse)
def search_knowledge(
    request: KnowledgeSearchRequest,
    business: Annotated[
        Business,
        Depends(require_business_permission(BusinessPermission.READ)),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> KnowledgeSearchResponse:
    try:
        results = retrieve_hybrid(
            db,
            business_id=business.id,
            query=request.query,
            limit=request.top_k,
        )
    except EmbeddingServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge search is temporarily unavailable",
        ) from error

    documents_by_id: dict[UUID, KnowledgeSearchDocumentReference] = {}
    response_results: list[KnowledgeSearchChunk] = []
    for result in results:
        documents_by_id.setdefault(
            result.document_id,
            KnowledgeSearchDocumentReference(
                document_id=result.document_id,
                document_filename=result.document_filename,
                document_source=result.document_source,
            ),
        )
        response_results.append(_search_chunk_response(result))
    return KnowledgeSearchResponse(
        query=request.query,
        results=response_results,
        documents=list(documents_by_id.values()),
    )


def _search_chunk_response(
    result: KnowledgeRetrievalResult,
) -> KnowledgeSearchChunk:
    return KnowledgeSearchChunk(
        chunk_id=result.chunk_id,
        document_id=result.document_id,
        business_id=result.business_id,
        chunk_index=result.chunk_index,
        chunk_text=result.chunk_text,
        chunk_metadata=result.chunk_metadata,
        document_filename=result.document_filename,
        document_source=result.document_source,
        semantic_distance=result.semantic_distance,
        keyword_score=result.keyword_score,
        semantic_rank=result.semantic_rank,
        keyword_rank=result.keyword_rank,
        rrf_score=result.rrf_score,
    )
