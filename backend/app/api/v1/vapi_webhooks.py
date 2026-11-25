import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.receptionist_configuration import ReceptionistConfiguration
from app.models.receptionist_provisioning import ReceptionistProvisioning
from app.models.vapi_configuration import VapiConfiguration
from app.schemas.vapi_webhook import (
    KnowledgeSearchToolArguments,
    VapiEndOfCallReportEnvelope,
    VapiStatusUpdateEnvelope,
    VapiToolCall,
    VapiToolCallContext,
    VapiToolCallsEnvelope,
    VapiToolIdentity,
    VapiWebhookEnvelope,
)
from app.services.embedding import EmbeddingServiceError
from app.services.knowledge_context import build_knowledge_context
from app.services.knowledge_retrieval import retrieve_hybrid
from app.services.vapi_call_events import (
    VapiCallEventResult,
    process_vapi_end_of_call_report,
    process_vapi_status_update,
)
from app.services.vapi_webhook_auth import verify_vapi_webhook_authorization

router = APIRouter(prefix="/integrations/vapi", tags=["Vapi webhooks"])
MAX_WEBHOOK_BODY_BYTES = 64 * 1024
MAX_KNOWLEDGE_CONTEXT_CHARACTERS = 6000
MAX_KNOWLEDGE_RESULTS = 5
logger = logging.getLogger(__name__)


async def _read_bounded_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        if not content_length.isascii() or not content_length.isdecimal():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid webhook payload",
            )
        if int(content_length) > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Webhook payload is too large",
            )

    chunks: list[bytes] = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Webhook payload is too large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def receive_vapi_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    verify_vapi_webhook_authorization(authorization)

    body = await _read_bounded_body(request)
    try:
        payload = json.loads(body)
        envelope = VapiWebhookEnvelope.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook payload",
        ) from error

    if envelope.message.type == "end-of-call-report":
        try:
            event = VapiEndOfCallReportEnvelope.model_validate(payload).message
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid end-of-call report",
            ) from error
        result = process_vapi_end_of_call_report(event, db)
    elif envelope.message.type == "status-update":
        try:
            event = VapiStatusUpdateEnvelope.model_validate(payload).message
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid status-update event",
            ) from error
        result = process_vapi_status_update(event, db)
    else:
        return {"status": "ignored"}

    if result is VapiCallEventResult.FAILED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vapi call could not be associated with a business",
        )
    return {"status": result.value}


def _tool_call_identifiers(
    call: VapiToolCallContext,
    assistant: VapiToolIdentity | None = None,
) -> tuple[str | None, str | None]:
    assistant_id = call.assistantId
    nested_assistant_id = call.assistant.id if call.assistant is not None else None
    phone_number_id = call.phoneNumberId
    nested_phone_number_id = (
        call.phoneNumber.id if call.phoneNumber is not None else None
    )
    if (
        assistant_id is not None
        and nested_assistant_id is not None
        and assistant_id != nested_assistant_id
    ) or (
        phone_number_id is not None
        and nested_phone_number_id is not None
        and phone_number_id != nested_phone_number_id
    ):
        return None, None
    assistant_id = assistant_id or nested_assistant_id
    message_assistant_id = getattr(assistant, "id", None)
    if (
        assistant_id is not None
        and message_assistant_id is not None
        and assistant_id != message_assistant_id
    ):
        return None, None
    return (
        assistant_id or message_assistant_id,
        phone_number_id or nested_phone_number_id,
    )


def _resolve_active_knowledge_business(
    db: Session,
    call: VapiToolCallContext,
    assistant: VapiToolIdentity | None = None,
) -> UUID:
    assistant_id, phone_number_id = _tool_call_identifiers(call, assistant)
    if assistant_id is None and phone_number_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge search is not enabled for this assistant.",
        )

    statement = (
        select(VapiConfiguration.business_id)
        .join(
            ReceptionistProvisioning,
            ReceptionistProvisioning.business_id == VapiConfiguration.business_id,
        )
        .join(
            ReceptionistConfiguration,
            ReceptionistConfiguration.business_id == VapiConfiguration.business_id,
        )
        .where(
            VapiConfiguration.is_enabled.is_(True),
            ReceptionistConfiguration.is_enabled.is_(True),
            ReceptionistProvisioning.status == "active",
            VapiConfiguration.assistant_id == ReceptionistProvisioning.assistant_id,
            VapiConfiguration.phone_number_id
            == ReceptionistProvisioning.phone_number_id,
        )
    )
    if assistant_id is not None:
        statement = statement.where(VapiConfiguration.assistant_id == assistant_id)
    if phone_number_id is not None:
        statement = statement.where(
            VapiConfiguration.phone_number_id == phone_number_id
        )

    business_ids = db.scalars(statement.limit(2)).all()
    if len(business_ids) != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge search is not enabled for this assistant.",
        )
    return business_ids[0]


def _safe_tool_error(error: Exception) -> str:
    if isinstance(error, HTTPException):
        if error.status_code == status.HTTP_404_NOT_FOUND:
            return "Knowledge search is not enabled for this assistant."
        return "Knowledge search is temporarily unavailable. Follow the configured fallback instructions."
    if isinstance(error, (EmbeddingServiceError, SQLAlchemyError)):
        return "Knowledge search is temporarily unavailable. Follow the configured fallback instructions."
    return "Knowledge search could not be completed. Follow the configured fallback instructions."


def _knowledge_tool_result(
    db: Session,
    call: VapiToolCallContext,
    assistant: VapiToolIdentity | None,
    tool_call: VapiToolCall,
) -> dict[str, str]:
    if tool_call.function.name != "search_business_knowledge":
        return {
            "toolCallId": tool_call.id,
            "error": "This tool is not supported.",
        }

    try:
        arguments_value = tool_call.function.arguments
        if isinstance(arguments_value, str):
            arguments_value = json.loads(arguments_value)
        arguments = KnowledgeSearchToolArguments.model_validate(arguments_value)
        query = arguments.query.strip()
        if not query:
            return {
                "toolCallId": tool_call.id,
                "error": "Provide a non-empty business question to search.",
            }

        business_id = _resolve_active_knowledge_business(
            db,
            call,
            assistant=assistant,
        )
        db.rollback()
        results = retrieve_hybrid(
            db,
            business_id=business_id,
            query=query,
            limit=MAX_KNOWLEDGE_RESULTS,
        )
        if any(result.business_id != business_id for result in results):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Knowledge search returned an invalid business scope.",
            )
        context = build_knowledge_context(
            results,
            max_characters=MAX_KNOWLEDGE_CONTEXT_CHARACTERS,
        )
        db.rollback()

        included_results = {
            result.chunk_id: result for result in results
        }
        source_texts: list[str] = []
        source_filenames: list[str] = []
        seen_filenames: set[str] = set()
        for source in context.sources:
            result = included_results[source.chunk_id]
            page_label = (
                f", page {source.page_number}"
                if source.page_number is not None
                else ""
            )
            source_texts.append(
                f"[Source: {source.document_filename}{page_label}]\n"
                f"{result.chunk_text}"
            )
            if source.document_filename not in seen_filenames:
                seen_filenames.add(source.document_filename)
                source_filenames.append(source.document_filename)

        found = bool(source_texts)
        response = {
            "found": found,
            "context": (
                "\n\n".join(source_texts)
                if found
                else (
                    "The verified business knowledge base did not return a "
                    "relevant answer. Do not guess; follow the configured fallback "
                    "instructions."
                )
            ),
            "sources": source_filenames,
        }
        return {
            "toolCallId": tool_call.id,
            "result": json.dumps(response, ensure_ascii=True, separators=(",", ":")),
        }
    except (ValidationError, json.JSONDecodeError):
        return {
            "toolCallId": tool_call.id,
            "error": "The knowledge search request is invalid.",
        }
    except HTTPException as error:
        return {"toolCallId": tool_call.id, "error": _safe_tool_error(error)}
    except (EmbeddingServiceError, SQLAlchemyError, TimeoutError) as error:
        db.rollback()
        logger.error(
            "Vapi knowledge tool execution failed",
            extra={
                "tool_name": tool_call.function.name,
                "error_type": type(error).__name__,
            },
        )
        return {"toolCallId": tool_call.id, "error": _safe_tool_error(error)}
    except ValueError as error:
        return {"toolCallId": tool_call.id, "error": _safe_tool_error(error)}


@router.post("/knowledge-tool")
async def receive_vapi_knowledge_tool(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, list[dict[str, str]]]:
    verify_vapi_webhook_authorization(authorization)
    body = await _read_bounded_body(request)
    try:
        payload = json.loads(body)
        envelope = VapiToolCallsEnvelope.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Vapi tool-call payload",
        ) from error

    call_ids = [tool_call.id for tool_call in envelope.message.toolCallList]
    if len(call_ids) != len(set(call_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Vapi tool-call payload",
        )
    results = [
        _knowledge_tool_result(
            db,
            envelope.message.call,
            envelope.message.assistant,
            tool_call,
        )
        for tool_call in envelope.message.toolCallList
    ]
    return {"results": results}
