from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.services.knowledge_retrieval import KnowledgeRetrievalResult


@dataclass(frozen=True)
class KnowledgeSourceReference:
    chunk_id: UUID
    document_id: UUID
    document_filename: str
    document_source: str | None
    page_number: int | None


@dataclass(frozen=True)
class KnowledgeContext:
    context_text: str
    sources: tuple[KnowledgeSourceReference, ...]
    included_chunk_ids: tuple[UUID, ...]
    omitted_chunk_ids: tuple[UUID, ...]
    truncated: bool


def build_knowledge_context(
    results: list[KnowledgeRetrievalResult] | tuple[KnowledgeRetrievalResult, ...],
    *,
    max_characters: int = 12_000,
) -> KnowledgeContext:
    if (
        isinstance(max_characters, bool)
        or not isinstance(max_characters, int)
        or max_characters < 1
    ):
        raise ValueError("Context character budget must be a positive integer")

    pieces: list[str] = []
    sources: list[KnowledgeSourceReference] = []
    included: list[UUID] = []
    omitted: list[UUID] = []
    used_characters = 0

    for index, result in enumerate(results):
        page_number = result.chunk_metadata.get("page_number")
        if not isinstance(page_number, int) or isinstance(page_number, bool):
            page_number = None
        reference = KnowledgeSourceReference(
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            document_filename=result.document_filename,
            document_source=result.document_source,
            page_number=page_number,
        )
        page_label = f", page {page_number}" if page_number is not None else ""
        header = (
            f"[Source: {result.document_filename} "
            f"(document {result.document_id}{page_label})]"
        )
        piece = f"{header}\n{result.chunk_text}"
        separator = "\n\n" if pieces else ""
        required_characters = len(separator) + len(piece)
        if used_characters + required_characters > max_characters:
            omitted.extend(item.chunk_id for item in results[index:])
            break
        pieces.append(f"{separator}{piece}")
        used_characters += required_characters
        sources.append(reference)
        included.append(result.chunk_id)

    return KnowledgeContext(
        context_text="".join(pieces),
        sources=tuple(sources),
        included_chunk_ids=tuple(included),
        omitted_chunk_ids=tuple(omitted),
        truncated=bool(omitted),
    )
