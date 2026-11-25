from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from app.services import knowledge_retrieval
from app.services.knowledge_context import build_knowledge_context
from app.services.knowledge_retrieval import (
    KnowledgeRetrievalResult,
    reciprocal_rank_fusion,
)


def _result(
    chunk_id: UUID,
    *,
    text: str = "Clinic hours are 9 to 5.",
    metadata: dict[str, object] | None = None,
    document_id: UUID | None = None,
    semantic_distance: float | None = None,
    keyword_score: float | None = None,
) -> KnowledgeRetrievalResult:
    return KnowledgeRetrievalResult(
        chunk_id=chunk_id,
        document_id=document_id or uuid4(),
        business_id=uuid4(),
        chunk_index=0,
        chunk_text=text,
        chunk_metadata=metadata or {},
        document_filename="hours.pdf",
        document_source="https://example.test/hours.pdf",
        semantic_distance=semantic_distance,
        keyword_score=keyword_score,
    )


def test_rrf_combines_ranks_and_preserves_both_scores() -> None:
    shared = _result(uuid4(), semantic_distance=0.1)
    only_semantic = _result(uuid4(), semantic_distance=0.2)
    only_keyword = _result(uuid4(), keyword_score=0.8)

    results = reciprocal_rank_fusion(
        [shared, only_semantic],
        [replace(shared, semantic_distance=None, keyword_score=0.9), only_keyword],
        k=60,
    )

    assert len(results) == 3
    assert results[0].chunk_id == shared.chunk_id
    assert results[0].semantic_rank == results[0].keyword_rank == 1
    assert results[0].semantic_distance == 0.1
    assert results[0].keyword_score == 0.9
    assert results[0].rrf_score == pytest.approx(2 / 61)
    singles = {result.chunk_id: result for result in results[1:]}
    assert singles[only_semantic.chunk_id].semantic_rank == 2
    assert singles[only_semantic.chunk_id].keyword_rank is None
    assert singles[only_semantic.chunk_id].rrf_score == pytest.approx(1 / 62)
    assert singles[only_keyword.chunk_id].semantic_rank is None
    assert singles[only_keyword.chunk_id].keyword_rank == 2
    assert singles[only_keyword.chunk_id].rrf_score == pytest.approx(1 / 62)


def test_rrf_deduplicates_and_breaks_equal_scores_deterministically() -> None:
    chunk_a, chunk_b = sorted((uuid4(), uuid4()), key=str)
    semantic = [_result(chunk_b), _result(chunk_a)]
    keyword = [_result(chunk_a), _result(chunk_b)]

    first = reciprocal_rank_fusion(semantic, keyword, k=60)
    second = reciprocal_rank_fusion(semantic, keyword, k=60)

    assert [result.chunk_id for result in first] == [chunk_a, chunk_b]
    assert [result.chunk_id for result in second] == [chunk_a, chunk_b]
    assert all(result.semantic_rank and result.keyword_rank for result in first)


@pytest.mark.parametrize(
    ("has_semantic", "has_keyword"),
    [
        (True, False),
        (False, True),
    ],
)
def test_rrf_supports_semantic_only_and_keyword_only(
    has_semantic: bool,
    has_keyword: bool,
) -> None:
    only_result = _result(uuid4())
    semantic_results = [only_result] if has_semantic else []
    keyword_results = [only_result] if has_keyword else []

    result = reciprocal_rank_fusion(semantic_results, keyword_results)

    assert len(result) == 1
    assert result[0].chunk_id == only_result.chunk_id
    assert (result[0].semantic_rank is not None) is has_semantic
    assert (result[0].keyword_rank is not None) is has_keyword


def test_retrievers_handle_empty_query_without_embedding_or_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_embedding(_query: str):
        raise AssertionError("empty query must not be embedded")

    monkeypatch.setattr(knowledge_retrieval, "embed_text", unexpected_embedding)

    assert knowledge_retrieval.retrieve_semantic(
        None, business_id=uuid4(), query=" \t\n", limit=3
    ) == []
    assert knowledge_retrieval.retrieve_keyword(
        None, business_id=uuid4(), query="", limit=3
    ) == []
    assert knowledge_retrieval.retrieve_hybrid(
        None, business_id=uuid4(), query="   ", limit=3
    ) == []


@pytest.mark.parametrize(
    ("query", "limit"),
    [
        ("valid", 0),
        ("valid", 101),
        ("valid", True),
        ("x" * 4001, 1),
    ],
)
def test_retrieval_rejects_invalid_input(query: str, limit: int) -> None:
    with pytest.raises(ValueError):
        knowledge_retrieval.retrieve_semantic(
            None,
            business_id=uuid4(),
            query=query,
            limit=limit,
        )


def test_retrieval_rejects_non_string_queries_and_invalid_rrf_parameters() -> None:
    with pytest.raises(TypeError):
        knowledge_retrieval.retrieve_semantic(
            None,
            business_id=uuid4(),
            query=None,
        )
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([], [], k=0)


def test_context_builder_preserves_sources_order_and_page_metadata() -> None:
    document_id = uuid4()
    first = _result(
        uuid4(),
        text="First complete chunk.",
        metadata={"page_number": 2},
        document_id=document_id,
    )
    second = _result(
        uuid4(),
        text="Second complete chunk.",
        metadata={"page_number": 3},
        document_id=document_id,
    )

    context = build_knowledge_context([first, second])

    assert context.context_text.index(first.chunk_text) < context.context_text.index(
        second.chunk_text
    )
    assert context.included_chunk_ids == (first.chunk_id, second.chunk_id)
    assert [source.page_number for source in context.sources] == [2, 3]
    assert context.sources[0].document_source == "https://example.test/hours.pdf"
    assert not context.omitted_chunk_ids
    assert not context.truncated


def test_context_builder_enforces_budget_without_splitting_chunks() -> None:
    first = _result(uuid4(), text="A complete first chunk.")
    second = _result(uuid4(), text="A second chunk that must stay whole.")
    first_context = build_knowledge_context([first])
    budget = len(first_context.context_text) + 2

    context = build_knowledge_context([first, second], max_characters=budget)

    assert context.context_text == first_context.context_text
    assert context.included_chunk_ids == (first.chunk_id,)
    assert context.omitted_chunk_ids == (second.chunk_id,)
    assert context.truncated
    assert len(context.context_text) <= budget


def test_context_builder_handles_empty_results_and_invalid_budget() -> None:
    assert build_knowledge_context([]).context_text == ""
    with pytest.raises(ValueError):
        build_knowledge_context([], max_characters=0)
