from __future__ import annotations

import logging
import os
from typing import Any

from controlplane.control.bot.salesbot.config import ESCALATION_CHAT_IDS
from controlplane.control.bot.salesbot.correction_tracker import (
    build_entry_recorded_message,
    build_escalation_message,
    build_final_escalation_message,
    build_service_not_found_escalation,
)
from controlplane.control.bot.salesbot.dependencies import get_notification_client
from controlplane.control.bot.salesbot.services.dialogue import (
    build_correction_request_message,
    build_service_clarification_message,
)
from controlplane.control.bot.salesbot.services.memory import (
    close_sales_correction_task,
    open_sales_correction_task,
    record_sales_event,
    remember_sales_correction_outcome,
)
from controlplane.control.commissionService import build_commission_notification

logger = logging.getLogger(__name__)


def _address_user_message(body: str, sender_name: str | None) -> str:
    cleaned_body = body.strip()
    if not cleaned_body or not sender_name:
        return cleaned_body
    return f"Dear {sender_name},\n\n{cleaned_body}"


def send_correction_request(
    chat_id: str,
    validation_failures: list[str],
    extracted_data: dict[str, Any],
    sender_id: str | None = None,
    sender_name: str | None = None,
    quoted_message_id: str | None = None,
) -> bool:
    notification_client = get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot send correction request")
        return False

    message = build_correction_request_message(
        validation_failures=validation_failures,
        extracted_data=extracted_data,
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
    )
    message = _address_user_message(message, sender_name)
    try:
        notification_client.send_text(to=chat_id, body=message, quoted=quoted_message_id)
        logger.info(
            "Sent correction request to chat_id=%s failures=%s quoted=%s",
            chat_id,
            validation_failures,
            quoted_message_id,
        )
        record_sales_event(
            role="assistant",
            text=message,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            event_type="correction_request",
            metadata={"validation_failures": validation_failures, "quoted_message_id": quoted_message_id},
        )
        open_sales_correction_task(
            chat_id=chat_id,
            sender_id=sender_id,
            content="Awaiting user correction for invalid or missing sales fields.",
            metadata={"validation_failures": validation_failures, "extracted_data": extracted_data},
        )
        return True
    except Exception as exc:
        logger.error("Failed to send correction request: %s", exc, exc_info=True)
        return False


def send_escalation_to_all(message: str) -> bool:
    if not ESCALATION_CHAT_IDS:
        logger.warning("ESCALATION_CHAT_IDS not set; cannot escalate")
        return False

    notification_client = get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot escalate")
        return False

    success = False
    for chat_id in ESCALATION_CHAT_IDS:
        try:
            notification_client.send_text(to=chat_id, body=message)
            logger.info("Escalated to %s", chat_id)
            success = True
        except Exception as exc:
            logger.error("Failed to send escalation to %s: %s", chat_id, exc, exc_info=True)
    return success


def send_escalation(
    chat_id: str,
    sender_id: str | None,
    sender_name: str | None,
    original_message: str,
    validation_failures: list[str],
) -> bool:
    message = build_escalation_message(original_message, validation_failures, sender_name)
    return send_escalation_to_all(message)


def send_service_suggestions(
    chat_id: str,
    service_name: str,
    suggestions: list[tuple[str, float]],
    sender_id: str | None = None,
    sender_name: str | None = None,
    user_reply: str | None = None,
    attempt_count: int = 1,
    quoted_message_id: str | None = None,
    missing_fields: list[str] | None = None,
) -> bool:
    notification_client = get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot send service suggestions")
        return False

    message = build_service_clarification_message(
        service_name=service_name,
        suggestions=suggestions,
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
        user_reply=user_reply,
        attempt_count=attempt_count,
        missing_fields=missing_fields,
    )
    message = _address_user_message(message, sender_name)
    try:
        notification_client.send_text(to=chat_id, body=message, quoted=quoted_message_id)
        logger.info(
            "Sent service suggestions to chat_id=%s for service=%s suggestions=%d quoted=%s",
            chat_id,
            service_name,
            len(suggestions),
            quoted_message_id,
        )
        record_sales_event(
            role="assistant",
            text=message,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            event_type="service_suggestions",
            metadata={
                "service_name": service_name,
                "suggestion_count": len(suggestions),
                "attempt_count": attempt_count,
                "user_reply": user_reply,
            },
        )
        open_sales_correction_task(
            chat_id=chat_id,
            sender_id=sender_id,
            content=f"Awaiting service correction for unrecognized service `{service_name}`.",
            metadata={"service_name": service_name, "suggestions": [name for name, _ in suggestions]},
        )
        return True
    except Exception as exc:
        logger.error("Failed to send service suggestions: %s", exc, exc_info=True)
        return False


def escalate_unknown_service(
    chat_id: str,
    sender_id: str | None,
    sender_name: str | None,
    service_name: str,
    original_message: str,
) -> bool:
    message = build_service_not_found_escalation(service_name, original_message, sender_name)
    success = send_escalation_to_all(message)
    if success:
        close_sales_correction_task(
            chat_id,
            sender_id=sender_id,
            status="escalated",
            resolution_note=f"Unknown service `{service_name}` had no good suggestions.",
        )
        remember_sales_correction_outcome(
            chat_id=chat_id,
            sender_id=sender_id,
            title="Escalated unknown service",
            content=f"SalesBot could not resolve unknown service `{service_name}` and escalated it.",
            metadata={"service_name": service_name, "sender_id": sender_id, "sender_name": sender_name},
        )
    return success


def send_commission_notification(
    seller_name: str,
    service: str,
    commission_entries: list[dict[str, Any]],
) -> None:
    sales_group_id = (os.getenv("SALES_GROUP_ID") or "").strip()
    if not sales_group_id:
        logger.warning("SALES_GROUP_ID not set; skipping commission notification")
        return

    notification_client = get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; skipping commission notification")
        return

    message = build_commission_notification(seller_name, service, commission_entries)
    if not message:
        logger.debug("No commission notification to send (empty message)")
        return

    try:
        notification_client.send_text(to=sales_group_id, body=message)
        logger.info("Commission notification sent to sales_group=%s", sales_group_id)
    except Exception as exc:
        logger.error("Failed to send commission notification: %s", exc, exc_info=True)


def send_entry_recorded_confirmation(
    chat_id: str,
    *,
    sender_id: str | None = None,
    sender_name: str | None = None,
    quoted_message_id: str | None = None,
) -> bool:
    notification_client = get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot send recorded confirmation")
        return False

    message = _address_user_message(build_entry_recorded_message(), sender_name)
    try:
        notification_client.send_text(to=chat_id, body=message, quoted=quoted_message_id)
        logger.info("Sent entry recorded confirmation to chat_id=%s", chat_id)
        record_sales_event(
            role="assistant",
            text=message,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            event_type="entry_recorded_confirmation",
        )
        return True
    except Exception as exc:
        logger.error("Failed to send entry recorded confirmation: %s", exc, exc_info=True)
        return False


def send_final_escalation(
    chat_id: str,
    sender_id: str | None,
    sender_name: str | None,
    original_message: str,
    *,
    reason_code: str | None = None,
    reason_details: dict[str, Any] | None = None,
) -> bool:
    notification_client = get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot send final escalation")
        return False

    user_message = build_final_escalation_message()
    user_message = _address_user_message(user_message, sender_name)
    try:
        notification_client.send_text(to=chat_id, body=user_message)
        logger.info("Sent final escalation message to user chat_id=%s", chat_id)
        record_sales_event(
            role="assistant",
            text=user_message,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            event_type="final_escalation",
        )
        close_sales_correction_task(
            chat_id,
            sender_id=sender_id,
            status="escalated",
            resolution_note=f"Escalated after record failure reason={reason_code or 'unknown'}.",
        )
    except Exception as exc:
        logger.error("Failed to send final escalation to user: %s", exc, exc_info=True)

    if ESCALATION_CHAT_IDS:
        if reason_code == "non_positive_profit":
            reason_label = "Negative/Zero Profit"
            reason_note = "The service could not be recorded because the profit was zero or negative."
        elif reason_code == "zero_price":
            reason_label = "Zero/Missing Price"
            reason_note = "The service could not be recorded because some price information was missing or zero."
        else:
            reason_label = "Repeated Invalid Input"
            reason_note = "The sale could not be completed automatically."
        details_str = ""
        if reason_details:
            details_str = "\n".join(f"*{k}:* {v}" for k, v in reason_details.items())
        admin_message = (
            f"🚨 *SalesBot Escalation - {reason_label}*\n\n"
            f"*User:* {sender_name or 'Unknown'}\n\n"
            "*Original Message:*\n"
            f"```\n{original_message[:500]}\n```\n\n"
            f"_{reason_note}_"
            + (f"\n\n*Details:*\n{details_str}" if details_str else "")
        )
        send_escalation_to_all(admin_message)

    return True
