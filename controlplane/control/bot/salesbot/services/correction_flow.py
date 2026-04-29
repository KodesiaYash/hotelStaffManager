from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from controlplane.control.bot.salesbot.correction_tracker import (
    build_timeout_escalation_message,
    get_correction_tracker,
)
from controlplane.control.bot.salesbot.dependencies import get_notification_client
from controlplane.control.bot.salesbot.services.dialogue import interpret_service_reply
from controlplane.control.bot.salesbot.services.extraction import (
    get_case_insensitive,
    llm_extract,
    validate_extracted_data,
)
from controlplane.control.bot.salesbot.services.memory import (
    close_sales_correction_task,
    remember_sales_correction_outcome,
)
from controlplane.control.bot.salesbot.services.messaging import (
    send_correction_request,
    send_entry_recorded_confirmation,
    send_escalation,
    send_escalation_to_all,
    send_final_escalation,
    send_service_suggestions,
)

logger = logging.getLogger(__name__)


def process_expired_corrections() -> int:
    tracker = get_correction_tracker()
    expired = tracker.get_and_remove_expired()
    if not expired:
        return 0

    notification_client = get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot escalate expired corrections")
        return 0

    escalated = 0
    for correction in expired:
        logger.warning(
            "Escalating expired correction (24h timeout) chat_id=%s sender_id=%s",
            correction.chat_id,
            correction.sender_id,
        )
        try:
            from controlplane.control.bot.salesbot.correction_tracker import build_final_escalation_message
            from controlplane.control.bot.salesbot.services.memory import record_sales_event

            user_message = build_final_escalation_message()
            if correction.sender_name:
                user_message = f"Dear {correction.sender_name},\n\n{user_message}"
            notification_client.send_text(to=correction.chat_id, body=user_message)
            record_sales_event(
                role="assistant",
                text=user_message,
                chat_id=correction.chat_id,
                sender_id=correction.sender_id,
                sender_name=correction.sender_name,
                event_type="correction_timeout",
            )
        except Exception as exc:
            logger.error("Failed to send timeout message to user: %s", exc, exc_info=True)

        admin_message = build_timeout_escalation_message(
            correction.original_message,
            correction.validation_failures,
            correction.sender_name,
        )
        if send_escalation_to_all(admin_message):
            escalated += 1
        close_sales_correction_task(
            correction.chat_id,
            sender_id=correction.sender_id,
            status="expired",
            resolution_note="Correction timed out and was escalated.",
        )
        remember_sales_correction_outcome(
            chat_id=correction.chat_id,
            sender_id=correction.sender_id,
            title="Expired SalesBot correction",
            content=(
                f"Correction expired for sender "
                f"`{correction.sender_name or correction.sender_id or correction.chat_id}`. "
                f"Failures: {', '.join(correction.validation_failures)}."
            ),
            metadata={
                "sender_id": correction.sender_id,
                "sender_name": correction.sender_name,
                "validation_failures": correction.validation_failures,
            },
        )

    return escalated


def check_and_handle_correction_reply(
    message: str,
    sender_id: str | None,
    sender_name: str | None,
    chat_id: str,
    *,
    process_message_fn: Callable[..., bool],
    llm_extract_fn: Callable[..., dict[str, Any] | list[dict[str, Any]]] = llm_extract,
) -> bool:
    tracker = get_correction_tracker()
    pending = tracker.get_pending(chat_id, sender_id)
    if not pending:
        return False

    logger.info("Found pending correction for chat_id=%s, merging with reply", chat_id)

    if pending.service_suggestions:
        interpretation = interpret_service_reply(
            original_service=str(pending.extracted_data.get("Service", "") or ""),
            user_reply=message,
            suggestions=pending.service_suggestions,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
        )
        selected_service = interpretation.matched_service
        if selected_service:
            original_service = pending.extracted_data.get("Service", "")
            logger.info("Resolved service reply chat_id=%s selected_service=%s", chat_id, selected_service)
            pending.extracted_data["Service"] = selected_service
            pending.service_suggestions = []
            pending.awaiting_service_confirmation = None
            pending.validation_failures = [
                failure
                for failure in pending.validation_failures
                if "service" not in failure.lower() and "price list" not in failure.lower()
            ]
            tracker.remove_pending(chat_id, pending.sender_id)
            close_sales_correction_task(
                chat_id,
                sender_id=pending.sender_id,
                status="resolved",
                resolution_note=f"User clarified corrected service `{selected_service}`.",
            )
            remember_sales_correction_outcome(
                chat_id=chat_id,
                sender_id=pending.sender_id,
                title="Resolved SalesBot service correction",
                content=(
                    f"User clarified an unknown service as `{selected_service}`. "
                    f"Original extracted service was `{original_service}`."
                ),
                fact_title="Sales service alias correction",
                fact_content=f"When service correction flow resolved, `{original_service}` meant `{selected_service}`.",
                metadata={
                    "resolved_service": selected_service,
                    "original_service": original_service,
                    "user_reply": message,
                },
            )
            corrected_entry = dict(pending.extracted_data)
            corrected_entry["Service"] = selected_service
            corrected_entry["confidence"] = "high"
            result_details: dict[str, Any] = {}
            recorded = process_message_fn(
                pending.original_message,
                sender_id,
                chat_id=None,
                message_id=pending.original_message_id,
                sender_name=sender_name,
                extracted_override=corrected_entry,
                result_details=result_details,
            )
            if recorded:
                send_entry_recorded_confirmation(
                    chat_id,
                    sender_id=pending.sender_id,
                    sender_name=pending.sender_name,
                    quoted_message_id=pending.original_message_id,
                )
            else:
                reason_code = result_details.get("reason_code")
                reason_details = {
                    k: result_details[k]
                    for k in ("service", "selling_price", "cost_price", "profit", "quantity")
                    if k in result_details
                } or None
                send_final_escalation(
                    chat_id,
                    pending.sender_id,
                    pending.sender_name,
                    pending.original_message,
                    reason_code=reason_code,
                    reason_details=reason_details,
                )
            return True

        pending.attempt_count += 1
        logger.warning(
            "Could not resolve service reply '%s' chat_id=%s attempt=%d",
            message.strip()[:50],
            chat_id,
            pending.attempt_count,
        )
        if pending.should_escalate():
            logger.warning(
                "Escalating after %d failed selection attempts chat_id=%s",
                pending.attempt_count,
                chat_id,
            )
            send_final_escalation(chat_id, sender_id, pending.sender_name, pending.original_message)
            remember_sales_correction_outcome(
                chat_id=chat_id,
                sender_id=pending.sender_id,
                title="Escalated SalesBot invalid service selection",
                content=(
                    f"Service correction escalated after unresolved replies for sender "
                    f"`{pending.sender_name or pending.sender_id or chat_id}`."
                ),
                metadata={"attempt_count": pending.attempt_count},
            )
            tracker.remove_pending(chat_id, pending.sender_id)
        else:
            send_service_suggestions(
                chat_id,
                str(pending.extracted_data.get("Service", "") or ""),
                pending.service_suggestions,
                sender_id=pending.sender_id,
                sender_name=pending.sender_name,
                user_reply=message.strip(),
                attempt_count=pending.attempt_count,
                quoted_message_id=pending.original_message_id,
            )
        return True

    combined_message = f"{pending.original_message}\n\n[CORRECTION from user]:\n{message}"
    extracted = llm_extract_fn(combined_message, chat_id=chat_id, sender_id=sender_id, sender_name=sender_name)
    if isinstance(extracted, dict) and "error" in extracted:
        logger.warning("Correction re-extraction failed: %s", extracted.get("error"))
        pending.attempt_count += 1
        send_final_escalation(chat_id, pending.sender_id, pending.sender_name, pending.original_message)
        remember_sales_correction_outcome(
            chat_id=chat_id,
            sender_id=pending.sender_id,
            title="Escalated SalesBot correction re-extraction failure",
            content="Correction reply could not be re-extracted into valid sale details.",
            metadata={"attempt_count": pending.attempt_count, "error": extracted.get("error")},
        )
        tracker.remove_pending(chat_id, pending.sender_id)
        return True

    entries: list[dict[str, Any]] = []
    if isinstance(extracted, list):
        entries = [entry for entry in extracted if isinstance(entry, dict)]
    elif isinstance(extracted, dict):
        entries = [extracted]
    if not entries:
        logger.warning("Correction re-extraction returned no entries")
        pending.attempt_count += 1
        send_final_escalation(chat_id, pending.sender_id, pending.sender_name, pending.original_message)
        remember_sales_correction_outcome(
            chat_id=chat_id,
            sender_id=pending.sender_id,
            title="Escalated SalesBot empty correction result",
            content="Correction reply returned no usable sale entries.",
            metadata={"attempt_count": pending.attempt_count},
        )
        tracker.remove_pending(chat_id, pending.sender_id)
        return True

    entry = entries[0]
    is_valid, validation_failures = validate_extracted_data(entry)
    if not is_valid:
        pending = tracker.add_pending(
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=pending.sender_name,
            original_message=combined_message,
            extracted_data=entry,
            validation_failures=validation_failures,
            original_message_id=pending.original_message_id,
        )
        if pending.should_escalate():
            logger.warning(
                "Escalating after %d failed correction attempts chat_id=%s",
                pending.attempt_count,
                chat_id,
            )
            send_escalation(
                chat_id,
                pending.sender_id,
                pending.sender_name,
                pending.original_message,
                validation_failures,
            )
            close_sales_correction_task(
                chat_id,
                sender_id=pending.sender_id,
                status="escalated",
                resolution_note="Correction failed repeatedly and was escalated.",
            )
            remember_sales_correction_outcome(
                chat_id=chat_id,
                sender_id=pending.sender_id,
                title="Escalated SalesBot correction",
                content=f"Correction escalated after repeated validation failures: {', '.join(validation_failures)}.",
                metadata={"validation_failures": validation_failures, "attempt_count": pending.attempt_count},
            )
            tracker.remove_pending(chat_id, pending.sender_id)
        else:
            send_correction_request(
                chat_id,
                validation_failures,
                entry,
                sender_id=pending.sender_id,
                sender_name=pending.sender_name,
                quoted_message_id=pending.original_message_id,
            )
        return True

    tracker.remove_pending(chat_id, pending.sender_id)
    logger.info("Correction successful, processing entry chat_id=%s", chat_id)
    close_sales_correction_task(
        chat_id,
        sender_id=pending.sender_id,
        status="resolved",
        resolution_note="Correction validated successfully and was reprocessed.",
    )
    remember_sales_correction_outcome(
        chat_id=chat_id,
        sender_id=pending.sender_id,
        title="Resolved SalesBot correction",
        content=(
            f"Correction resolved for sender `{pending.sender_name or pending.sender_id or chat_id}`. "
            f"Original failures: {', '.join(pending.validation_failures)}. "
            f"Final service `{get_case_insensitive(entry, ['Service']) or ''}`, "
            f"room `{get_case_insensitive(entry, ['Room']) or ''}`, "
            f"date `{get_case_insensitive(entry, ['Date']) or ''}`, "
            f"time `{get_case_insensitive(entry, ['Time']) or ''}`."
        ),
        fact_title="Resolved sales correction pattern",
        fact_content=(
            f"Resolved correction required fields `{', '.join(pending.validation_failures)}` and ended with "
            f"service `{get_case_insensitive(entry, ['Service']) or ''}`."
        ),
        metadata={"validation_failures": pending.validation_failures, "final_entry": entry},
    )
    recorded = process_message_fn(
        combined_message,
        sender_id,
        chat_id=None,
        message_id=pending.original_message_id,
        sender_name=sender_name,
    )
    if recorded:
        send_entry_recorded_confirmation(
            chat_id,
            sender_id=pending.sender_id,
            sender_name=pending.sender_name,
            quoted_message_id=pending.original_message_id,
        )
    else:
        send_final_escalation(chat_id, pending.sender_id, pending.sender_name, pending.original_message)
    return True
