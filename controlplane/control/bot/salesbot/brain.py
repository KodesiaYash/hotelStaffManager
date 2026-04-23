from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from communicationPlane.telegramEngine.telegramInterface.telegram_client import (  # noqa: E402
    TelegramClient,
)
from controlplane.boundary.llminterface.llm_interface import (  # noqa: E402
    LLMInterface,
    get_sales_bot_llm,
)
from controlplane.boundary.storageInterface.salesAudit import SalesAudit  # noqa: E402
from controlplane.boundary.storageInterface.staffToHotelMapping import (  # noqa: E402
    StaffToHotelMapping,
    normalize_phone,
)
from controlplane.control.bot.salesbot.correction_tracker import (  # noqa: E402
    build_correction_prompt,
    build_escalation_message,
    build_final_escalation_message,
    build_invalid_selection_message,
    build_service_not_found_escalation,
    build_service_suggestion_prompt,
    build_timeout_escalation_message,
    get_correction_tracker,
)
from controlplane.control.commissionService import (  # noqa: E402
    build_commission_notification,
    calculate_and_distribute_commissions,
    generate_sale_id,
)
from models.retry import RetryingTelegramClient  # noqa: E402
from shared.logging_context import (  # noqa: E402
    log_low_confidence,
    log_medium_confidence,
)

logger = logging.getLogger(__name__)

# Alert number for escalations (QueryBot DM)
ESCALATION_CHAT_ID = os.getenv("ESCALATION_CHAT_ID", "").strip()

DEFAULT_PROMPT = (
    "Analyze this WhatsApp message for a sales lead. The service name may be mentioned "
    "along with a number; use your best intelligence to judge if it is the quantity "
    'of the service sold. If yes, populate the "Quantity" field, otherwise default to 1. '
    "Do NOT use guest count (e.g., 2px) as quantity unless explicitly stated as quantity. "
    "If the message contains a line like 'Quantity: 3', 'Qty: 3', 'Qnty: 3', or '3 quantity', treat "
    "that as the quantity for the service(s). If a standalone number appears directly next to the "
    "service name or on the same line, treat it as quantity (e.g., '3 Transfer', 'Transfer 3'). "
    "If multiple services are listed and only one quantity is given, apply it to all services unless "
    "a specific per-service quantity is stated. "
    "If multiple services are mentioned, return multiple entries (one per service). "
    "Respond ONLY with valid JSON in the following format (no extra text, no explanations, "
    "no unnecessary special characters): "
    "["
    '{"Service": "task or \'\'", "Quantity": "number or \'1\'", "Date": "number in DD/MM/YYYY format or \'\'", '
    '"Time": "number in 24 hour format (If am or pm is given, infer the equivalent time in '
    '24 hour format) or \'\'", "Guest": "number (only mention the number) or \'\'", "Room": "name or \'\'", '
    '"Asignee": "name or \'\'", "HotelName": "RIAD Roxanne or RIAD Persephone or \'\'", '
    '"Amount": number or 0, '
    '"confidence": "high/medium/low"}'
    "] "
    "SANITY CHECKS - set confidence to low if any of these fail: "
    "1. Quantity <= 0 "
    "2. Service is empty or unclear "
    "3. Date format is invalid or missing "
    "4. Time format is invalid or missing "
    "5. Room is empty or missing "
    "Message: __MESSAGE__"
)

KNOWN_HOTELS = {
    "riad roxanne": "RIAD Roxanne",
    "riad persephone": "RIAD Persephone",
}


def _normalize_hotel_name(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if "roxanne" in lowered:
        return "RIAD Roxanne"
    if "persephone" in lowered:
        return "RIAD Persephone"
    return value.strip()


def _coerce_quantity(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 1.0
    match = re.search(r"[-+]?[0-9]*\.?[0-9]+", text.replace(",", ""))
    if not match:
        return 1.0
    try:
        return float(match.group(0))
    except ValueError:
        return 1.0


def _load_env_files() -> None:
    load_dotenv()
    env_path = os.path.join(PROJECT_ROOT, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()

_sales_audit: SalesAudit | None = None
_llm_interface: LLMInterface | None = None
_staff_mapping: StaffToHotelMapping | None = None
_notification_client: RetryingTelegramClient | None = None


def _get_case_insensitive(row: dict[str, Any], keys: list[str]) -> Any | None:
    lookup = {str(k).strip().lower(): k for k in row}
    for key in keys:
        normalized = str(key).strip().lower()
        if normalized in lookup:
            return row.get(lookup[normalized])
    return None


def _get_sales_audit() -> SalesAudit:
    global _sales_audit
    if _sales_audit is None:
        _sales_audit = SalesAudit()
    return _sales_audit


def _get_llm_interface() -> LLMInterface:
    global _llm_interface
    if _llm_interface is None:
        _llm_interface = get_sales_bot_llm()
    return _llm_interface


def _get_staff_mapping() -> StaffToHotelMapping | None:
    global _staff_mapping
    if _staff_mapping is not None:
        return _staff_mapping
    if not (os.getenv("STAFF_MAPPING_SHEET_ID") or os.getenv("STAFF_TO_HOTEL_SHEET_ID")):
        logger.warning("Staff mapping sheet id not set; skipping staff mapping lookup")
        _staff_mapping = None
        return None
    try:
        _staff_mapping = StaffToHotelMapping()
    except Exception as exc:
        logger.warning("Staff mapping not configured: %s", exc)
        _staff_mapping = None
    return _staff_mapping


def _get_notification_client() -> RetryingTelegramClient | None:
    global _notification_client
    if _notification_client is not None:
        return _notification_client
    try:
        _notification_client = RetryingTelegramClient(TelegramClient())
    except Exception as exc:
        logger.warning("Notification client not configured: %s", exc)
        _notification_client = None
    return _notification_client


def _send_correction_request(
    chat_id: str,
    validation_failures: list[str],
    extracted_data: dict[str, Any],
    quoted_message_id: str | None = None,
) -> bool:
    """Send a correction request to the user asking for missing/invalid fields."""
    notification_client = _get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot send correction request")
        return False

    message = build_correction_prompt(validation_failures, extracted_data)
    try:
        notification_client.send_text(to=chat_id, body=message, quoted=quoted_message_id)
        logger.info(
            "Sent correction request to chat_id=%s failures=%s quoted=%s",
            chat_id,
            validation_failures,
            quoted_message_id,
        )
        return True
    except Exception as exc:
        logger.error("Failed to send correction request: %s", exc, exc_info=True)
        return False


def _send_escalation(
    chat_id: str,
    sender_id: str | None,
    original_message: str,
    validation_failures: list[str],
) -> bool:
    """Escalate to the alert number when corrections fail repeatedly."""
    if not ESCALATION_CHAT_ID:
        logger.warning("ESCALATION_CHAT_ID not set; cannot escalate")
        return False

    notification_client = _get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot escalate")
        return False

    message = build_escalation_message(original_message, validation_failures, sender_id, chat_id)
    try:
        notification_client.send_text(to=ESCALATION_CHAT_ID, body=message)
        logger.info(
            "Escalated validation failure to %s for chat_id=%s",
            ESCALATION_CHAT_ID,
            chat_id,
        )
        return True
    except Exception as exc:
        logger.error("Failed to send escalation: %s", exc, exc_info=True)
        return False


def _send_service_suggestions(
    chat_id: str,
    service_name: str,
    suggestions: list[tuple[str, float]],
    quoted_message_id: str | None = None,
) -> bool:
    """Send service suggestions to the user when service not found in pricelist."""
    notification_client = _get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot send service suggestions")
        return False

    message = build_service_suggestion_prompt(service_name, suggestions)
    try:
        notification_client.send_text(to=chat_id, body=message, quoted=quoted_message_id)
        logger.info(
            "Sent service suggestions to chat_id=%s for service=%s suggestions=%d quoted=%s",
            chat_id,
            service_name,
            len(suggestions),
            quoted_message_id,
        )
        return True
    except Exception as exc:
        logger.error("Failed to send service suggestions: %s", exc, exc_info=True)
        return False


def _escalate_unknown_service(
    chat_id: str,
    sender_id: str | None,
    service_name: str,
    original_message: str,
) -> bool:
    """Escalate when a service is not found in pricelist and no good suggestions exist."""
    if not ESCALATION_CHAT_ID:
        logger.warning("ESCALATION_CHAT_ID not set; cannot escalate unknown service")
        return False

    notification_client = _get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot escalate")
        return False

    message = build_service_not_found_escalation(service_name, original_message, sender_id, chat_id)
    try:
        notification_client.send_text(to=ESCALATION_CHAT_ID, body=message)
        logger.info(
            "Escalated unknown service to %s service=%s chat_id=%s",
            ESCALATION_CHAT_ID,
            service_name,
            chat_id,
        )
        return True
    except Exception as exc:
        logger.error("Failed to escalate unknown service: %s", exc, exc_info=True)
        return False


def _send_commission_notification(
    seller_name: str,
    service: str,
    commission_entries: list[dict[str, Any]],
) -> None:
    """Send commission notification to sales group."""
    sales_group_id = (os.getenv("SALES_GROUP_ID") or "").strip()
    if not sales_group_id:
        logger.warning("SALES_GROUP_ID not set; skipping commission notification")
        return

    notification_client = _get_notification_client()
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


def _send_invalid_selection(
    chat_id: str,
    reply: str,
    suggestions: list[tuple[str, float]],
    quoted_message_id: str | None = None,
) -> bool:
    """Send invalid selection message to user."""
    notification_client = _get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot send invalid selection message")
        return False

    message = build_invalid_selection_message(reply, suggestions)
    try:
        notification_client.send_text(to=chat_id, body=message, quoted=quoted_message_id)
        logger.info("Sent invalid selection message to chat_id=%s reply=%s", chat_id, reply)
        return True
    except Exception as exc:
        logger.error("Failed to send invalid selection message: %s", exc, exc_info=True)
        return False


def _send_final_escalation(
    chat_id: str,
    sender_id: str | None,
    original_message: str,
) -> bool:
    """Send final escalation - tell user to contact Omar and alert admin."""
    notification_client = _get_notification_client()
    if notification_client is None:
        logger.warning("Notification client not available; cannot send final escalation")
        return False

    # Send message to user telling them to contact Omar
    user_message = build_final_escalation_message()
    try:
        notification_client.send_text(to=chat_id, body=user_message)
        logger.info("Sent final escalation message to user chat_id=%s", chat_id)
    except Exception as exc:
        logger.error("Failed to send final escalation to user: %s", exc, exc_info=True)

    # Also alert admin via ESCALATION_CHAT_ID
    if ESCALATION_CHAT_ID:
        admin_message = (
            "🚨 *SalesBot Escalation - Repeated Invalid Input*\n\n"
            f"*Chat ID:* `{chat_id}`\n"
            f"*Sender:* `{sender_id or 'Unknown'}`\n\n"
            "*Original Message:*\n"
            f"```\n{original_message[:500]}\n```\n\n"
            "_User failed to provide valid input after multiple attempts._"
        )
        try:
            notification_client.send_text(to=ESCALATION_CHAT_ID, body=admin_message)
            logger.info("Sent final escalation to admin %s", ESCALATION_CHAT_ID)
        except Exception as exc:
            logger.error("Failed to send final escalation to admin: %s", exc, exc_info=True)

    return True


def process_expired_corrections() -> int:
    """Check for expired corrections (24h timeout) and escalate them.

    This should be called periodically (e.g., every hour) to catch
    corrections where the user never replied.

    Returns:
        Number of expired corrections escalated
    """
    tracker = get_correction_tracker()
    expired = tracker.get_and_remove_expired()

    if not expired:
        return 0

    notification_client = _get_notification_client()
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

        # Send timeout message to user
        try:
            user_message = build_final_escalation_message()
            notification_client.send_text(to=correction.chat_id, body=user_message)
        except Exception as exc:
            logger.error("Failed to send timeout message to user: %s", exc, exc_info=True)

        # Escalate to admin
        if ESCALATION_CHAT_ID:
            try:
                admin_message = build_timeout_escalation_message(
                    correction.original_message,
                    correction.validation_failures,
                    correction.sender_id,
                    correction.chat_id,
                )
                notification_client.send_text(to=ESCALATION_CHAT_ID, body=admin_message)
                logger.info(
                    "Escalated expired correction to admin %s chat_id=%s",
                    ESCALATION_CHAT_ID,
                    correction.chat_id,
                )
                escalated += 1
            except Exception as exc:
                logger.error("Failed to escalate expired correction: %s", exc, exc_info=True)

    return escalated


def check_and_handle_correction_reply(message: str, sender_id: str | None, chat_id: str) -> bool:
    """Check if this message is a reply to a correction request.

    If there's a pending correction for this chat, merge the new message
    with the original and reprocess.

    Returns:
        True if this was handled as a correction reply, False otherwise
    """
    tracker = get_correction_tracker()
    pending = tracker.get_pending(chat_id)

    if not pending:
        return False

    logger.info(
        "Found pending correction for chat_id=%s, merging with reply",
        chat_id,
    )

    # Check if this is a reply to service suggestions
    if pending.service_suggestions:
        reply_stripped = message.strip()

        # Check if it's a valid number selection
        if reply_stripped.isdigit():
            selected_service = pending.get_selected_service(reply_stripped)
            if selected_service:
                # Valid selection - replace service in extracted data and reprocess
                logger.info(
                    "User selected service #%s: %s chat_id=%s",
                    reply_stripped,
                    selected_service,
                    chat_id,
                )
                # Update extracted data with selected service
                pending.extracted_data["Service"] = selected_service
                # Clear service suggestions and validation failures related to service
                pending.service_suggestions = []
                pending.validation_failures = [
                    f
                    for f in pending.validation_failures
                    if "service" not in f.lower() and "price list" not in f.lower()
                ]

                # Remove pending and reprocess with corrected service
                tracker.remove_pending(chat_id)

                # Rebuild message with corrected service
                combined_message = f"{pending.original_message}\n\n[CORRECTION: Service is '{selected_service}']"
                process_message(combined_message, sender_id, chat_id=None)
                return True

        # Check if reply matches one of the suggested service names (case-insensitive)
        reply_lower = reply_stripped.lower()
        for service_name, _ in pending.service_suggestions:
            if reply_lower == service_name.lower():
                # User typed the exact service name
                logger.info(
                    "User typed service name: %s chat_id=%s",
                    service_name,
                    chat_id,
                )
                pending.extracted_data["Service"] = service_name
                pending.service_suggestions = []
                pending.validation_failures = [
                    f
                    for f in pending.validation_failures
                    if "service" not in f.lower() and "price list" not in f.lower()
                ]
                tracker.remove_pending(chat_id)
                combined_message = f"{pending.original_message}\n\n[CORRECTION: Service is '{service_name}']"
                process_message(combined_message, sender_id, chat_id=None)
                return True

        # Invalid reply (random word like "xyzqwer" or invalid number like "6")
        pending.attempt_count += 1
        logger.warning(
            "Invalid service reply '%s' chat_id=%s attempt=%d",
            reply_stripped[:50],
            chat_id,
            pending.attempt_count,
        )

        if pending.should_escalate():
            # Too many failed attempts - escalate and tell user to contact Omar
            logger.warning(
                "Escalating after %d failed selection attempts chat_id=%s",
                pending.attempt_count,
                chat_id,
            )
            _send_final_escalation(chat_id, sender_id, pending.original_message)
            tracker.remove_pending(chat_id)
        else:
            _send_invalid_selection(
                chat_id,
                reply_stripped,
                pending.service_suggestions,
                quoted_message_id=pending.original_message_id,
            )
        return True

    # Combine original message with correction reply for re-extraction
    combined_message = f"{pending.original_message}\n\n[CORRECTION from user]:\n{message}"

    # Re-extract with combined context
    extracted = llm_extract(combined_message)

    if isinstance(extracted, dict) and "error" in extracted:
        logger.warning("Correction re-extraction failed: %s", extracted.get("error"))
        # Keep pending, user can try again
        return True

    # Validate the new extraction
    entries: list[dict[str, Any]] = []
    if isinstance(extracted, list):
        entries = [e for e in extracted if isinstance(e, dict)]
    elif isinstance(extracted, dict):
        entries = [extracted]

    if not entries:
        logger.warning("Correction re-extraction returned no entries")
        return True

    # Check if validation passes now
    entry = entries[0]  # Take first entry
    is_valid, validation_failures = _validate_extracted_data(entry)

    if not is_valid:
        # Still failing, update pending and send another correction request
        pending = tracker.add_pending(
            chat_id=chat_id,
            sender_id=sender_id,
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
            _send_escalation(chat_id, sender_id, pending.original_message, validation_failures)
            tracker.remove_pending(chat_id)
        else:
            _send_correction_request(chat_id, validation_failures, entry, quoted_message_id=pending.original_message_id)
        return True

    # Validation passed! Remove pending and process normally
    tracker.remove_pending(chat_id)
    logger.info("Correction successful, processing entry chat_id=%s", chat_id)

    # Process the corrected entry (call process_message with the combined message)
    # Set chat_id to None to avoid re-triggering correction flow
    process_message(combined_message, sender_id, chat_id=None)
    return True


def _get_llm_provider_name() -> str:
    """Get the name of the current LLM provider for logging."""
    llm = _get_llm_interface()
    return type(llm).__name__


def llm_extract(message: str) -> dict[str, Any] | list[dict[str, Any]]:
    """LLM extracts structured data from message using configured provider."""
    prompt = DEFAULT_PROMPT.replace("__MESSAGE__", message)
    provider_name = _get_llm_provider_name()

    logger.debug("SalesBot LLM extract prompt length=%d provider=%s", len(prompt), provider_name)
    try:
        response_text = _get_llm_interface().generate(prompt)
    except Exception as exc:
        exc_str = str(exc).lower()
        is_quota_error = "429" in exc_str or "quota" in exc_str or "rate" in exc_str
        logger.error(
            "SalesBot LLM call failed provider=%s quota_exhausted=%s error=%s message_preview=%s",
            provider_name,
            is_quota_error,
            str(exc)[:100],
            message[:200],
        )
        return {"error": "llm_call_failed", "exception": str(exc), "message": message[:500]}

    extracted = _safe_json_load(response_text, original_message=message)
    if isinstance(extracted, dict) and extracted.get("error") == "parse_failed":
        logger.warning(
            "SalesBot LLM parse failed on first attempt, retrying message_len=%d",
            len(message),
        )
        prompt = f"{prompt}\nCRITICAL: Pure JSON, no extra text."
        try:
            response_text = _get_llm_interface().generate(prompt)
        except Exception as exc:
            exc_str = str(exc).lower()
            is_quota_error = "429" in exc_str or "quota" in exc_str or "rate" in exc_str
            logger.error(
                "SalesBot LLM retry call failed provider=%s quota_exhausted=%s error=%s message_preview=%s",
                provider_name,
                is_quota_error,
                str(exc)[:100],
                message[:200],
            )
            return {"error": "llm_retry_failed", "exception": str(exc), "message": message[:500]}
        extracted = _safe_json_load(response_text, original_message=message)
        if isinstance(extracted, dict) and extracted.get("error") == "parse_failed":
            logger.warning(
                "SalesBot LLM parse failed after retry message_preview=%s raw_response=%s",
                message[:200],
                (response_text or "")[:200],
            )
    return extracted


def _safe_json_load(
    text: str | None,
    original_message: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    if not text:
        logger.warning(
            "SalesBot LLM returned empty response message_preview=%s",
            (original_message or "")[:200],
        )
        return {"error": "empty_response", "message": (original_message or "")[:500]}
    cleaned = text.strip()
    cleaned = re.sub(r"```json\s*|\s*```", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = cleaned.replace("\\n", "").replace("\\t", "")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.debug("JSON Error: %s", exc)
        logger.debug("Raw (first 200): %r", text[:200])
        logger.debug("Cleaned (first 200): %r", cleaned[:200])
        return {
            "error": "parse_failed",
            "raw": cleaned[:500],
            "message": (original_message or "")[:500],
            "json_error": str(exc),
        }


def _required_fields_present(extracted: dict[str, Any]) -> bool:
    required = ["Service", "Date", "Time", "Room"]
    for key in required:
        value = _get_case_insensitive(extracted, [key])
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
    return True


def _validate_extracted_data(extracted: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate extracted data with sanity checks.

    Returns:
        Tuple of (is_valid, list of validation failure reasons)
    """
    failures: list[str] = []

    # Check Quantity > 0
    quantity = _get_case_insensitive(extracted, ["Quantity"])
    quantity_value = _coerce_quantity(quantity) if quantity is not None else 1.0
    if quantity_value <= 0:
        failures.append(f"Quantity is invalid or <= 0: {quantity}")
        logger.warning("Sanity check failed: Quantity=%s (value=%s)", quantity, quantity_value)

    # Check Service is not empty
    service = _get_case_insensitive(extracted, ["Service"])
    if not service or (isinstance(service, str) and not service.strip()):
        failures.append("Service is empty or missing")
        logger.warning("Sanity check failed: Service is empty")

    # Check Date format (basic check for DD/MM/YYYY pattern)
    date = _get_case_insensitive(extracted, ["Date"])
    if not date or (isinstance(date, str) and not date.strip()):
        failures.append("Date is empty or missing")
        logger.warning("Sanity check failed: Date is empty")
    elif isinstance(date, str):
        import re

        date_pattern = r"^\d{1,2}/\d{1,2}/\d{4}$"
        if not re.match(date_pattern, date.strip()):
            failures.append(f"Date format invalid (expected DD/MM/YYYY): {date}")
            logger.warning("Sanity check failed: Date format invalid: %s", date)

    # Check Time is not empty
    time_val = _get_case_insensitive(extracted, ["Time"])
    if not time_val or (isinstance(time_val, str) and not time_val.strip()):
        failures.append("Time is empty or missing")
        logger.warning("Sanity check failed: Time is empty")

    # Check Room is not empty
    room = _get_case_insensitive(extracted, ["Room"])
    if not room or (isinstance(room, str) and not room.strip()):
        failures.append("Room is empty or missing")
        logger.warning("Sanity check failed: Room is empty")

    # Check Guest is valid if present (should be a number)
    guest = _get_case_insensitive(extracted, ["Guest"])
    if guest and isinstance(guest, str) and guest.strip():
        guest_value = _coerce_quantity(guest)
        if guest_value <= 0:
            failures.append(f"Guest count is invalid or <= 0: {guest}")
            logger.warning("Sanity check failed: Guest=%s (value=%s)", guest, guest_value)

    is_valid = len(failures) == 0
    return is_valid, failures


def _extract_hotel_name(message: str, extracted: dict[str, Any]) -> str | None:
    value = _get_case_insensitive(extracted, ["HotelName", "hotel", "hotel_name", "Hotel"])
    if isinstance(value, str) and value.strip():
        return _normalize_hotel_name(value)
    lowered = message.lower()
    for token, canonical in KNOWN_HOTELS.items():
        if token in lowered:
            return canonical
    return None


def _resolve_staff_and_hotel(
    sender_id: str | None,
    extracted_hotel: str | None,
    extracted_name: str | None,
) -> tuple[str, str | None, bool]:
    mapping = _get_staff_mapping()
    if mapping is None:
        logger.error("Staff mapping not configured; cannot resolve staff name")
        return extracted_name or "", extracted_hotel, True

    normalized = normalize_phone(sender_id)
    if not normalized:
        logger.error("Missing sender phone, cannot map staff")
        return extracted_name or "", extracted_hotel, True

    try:
        matches = mapping.find_by_phone(normalized)
    except Exception as exc:
        logger.error("Failed to read staff mapping: %s", exc, exc_info=True)
        return extracted_name or "", extracted_hotel, True
    if not matches:
        logger.error("No staff mapping for phone=%s", normalized)
        return extracted_name or "", extracted_hotel, True

    names = {
        str(val).strip()
        for val in (_get_case_insensitive(row, ["name", "staff", "staff_name", "employee"]) for row in matches)
        if val
    }
    if not names:
        logger.error("Staff mapping missing name for phone=%s", normalized)
        return extracted_name or "", extracted_hotel, True
    if len(names) > 1:
        logger.warning("Multiple staff names found for phone=%s: %s", normalized, sorted(names))
    staff_name = sorted(names)[0]

    hotels = {
        _normalize_hotel_name(str(val)) or str(val).strip()
        for val in (_get_case_insensitive(row, ["hotel", "hotel_name", "property"]) for row in matches)
        if val
    }
    if extracted_hotel:
        extracted_hotel = _normalize_hotel_name(extracted_hotel) or extracted_hotel
        return staff_name, extracted_hotel, False
    if len(hotels) == 1:
        return staff_name, next(iter(hotels)), False
    if len(hotels) > 1:
        logger.error(
            "Ambiguous staff mapping: phone=%s supports multiple hotels %s but message has no hotel name",
            normalized,
            sorted(hotels),
        )
        return staff_name, None, True
    logger.error("Staff mapping missing hotel for phone=%s", normalized)
    return staff_name, extracted_hotel, True


def _is_sales_message(message: str) -> bool:
    """Check if a message looks like a sales message.

    Sales messages typically:
    1. Contain 'Riad Roxanne' or 'Riad Persephone', OR
    2. Have a sales-like structure with fields like Service:, Date:, Time:, Room:, Guest:
    """
    if not message:
        return False

    message_lower = message.lower()

    # Check for Riad identifiers (case-insensitive)
    riad_identifiers = [
        "riad roxanne",
        "riad persephone",
        "roxanne",
        "persephone",
    ]

    if any(identifier in message_lower for identifier in riad_identifiers):
        return True

    # Check for sales-like structure - look for common field patterns
    sales_field_patterns = [
        "service:",
        "service :",
        "date:",
        "date :",
        "time:",
        "time :",
        "room:",
        "room :",
        "guest:",
        "guest :",
        "quantity:",
        "quantity :",
        "qty:",
        "qty :",
    ]

    # Count how many sales field patterns are present
    field_count = sum(1 for pattern in sales_field_patterns if pattern in message_lower)

    # If message has 3+ sales-like fields, treat it as a sales message
    # (e.g., Service + Date + Time, or Service + Room + Guest)
    return field_count >= 3


def process_message(
    message: str,
    sender_id: str | None = None,
    chat_id: str | None = None,
    message_id: str | None = None,
) -> None:
    """Process a sales message, with optional correction flow.

    Args:
        message: The message text to process
        sender_id: The sender's phone number/ID
        chat_id: The chat ID (needed for correction requests)
        message_id: The original message ID (for quoted replies)
    """
    logger.debug(
        "SalesBot processing message length=%d sender_id=%s chat_id=%s message_id=%s",
        len(message),
        sender_id,
        chat_id,
        message_id,
    )

    # Check if this is a reply to a pending correction request
    if chat_id and check_and_handle_correction_reply(message, sender_id, chat_id):
        logger.info("Message handled as correction reply chat_id=%s", chat_id)
        return

    # Check if message looks like a sales message
    if not _is_sales_message(message):
        logger.info(
            "Ignoring non-sales message length=%d sender_id=%s message_preview=%s",
            len(message),
            sender_id,
            message[:100].replace("\n", " "),
        )
        return

    extracted = llm_extract(message)
    if isinstance(extracted, dict) and "error" in extracted:
        logger.error(
            "SalesBot extraction failed error=%s sender_id=%s message_preview=%s",
            extracted.get("error"),
            sender_id,
            message[:200],
        )
        return

    entries: list[dict[str, Any]]
    if isinstance(extracted, list):
        entries = [entry for entry in extracted if isinstance(entry, dict)]
    elif isinstance(extracted, dict):
        entries = [extracted]
    else:
        logger.error(
            "SalesBot extraction returned unsupported type=%s sender_id=%s message_preview=%s",
            type(extracted).__name__,
            sender_id,
            message[:200],
        )
        return

    if not entries:
        logger.error(
            "SalesBot extraction returned empty entries sender_id=%s message_preview=%s",
            sender_id,
            message[:200],
        )
        return

    for idx, entry in enumerate(entries):
        confidence = str(_get_case_insensitive(entry, ["confidence"]) or "").lower()
        if not _required_fields_present(entry):
            confidence = "low"
            entry["confidence"] = "low"

        # Run sanity checks on extracted data
        is_valid, validation_failures = _validate_extracted_data(entry)
        if not is_valid:
            confidence = "low"
            entry["confidence"] = "low"
            logger.warning(
                "Entry %d failed sanity checks: %s",
                idx,
                "; ".join(validation_failures),
            )

        if confidence == "low":
            log_low_confidence(
                {
                    "event": "salesbot_low_confidence",
                    "confidence": confidence,
                    "message": message,
                    "entry_index": idx,
                    "extracted": entry,
                    "validation_failures": validation_failures if not is_valid else [],
                }
            )
            logger.info("SalesBot confidence=%s skipping sheet write", confidence or "unknown")

            # Trigger correction flow if we have validation failures and chat_id
            if validation_failures and chat_id:
                tracker = get_correction_tracker()
                pending = tracker.add_pending(
                    chat_id=chat_id,
                    sender_id=sender_id,
                    original_message=message,
                    extracted_data=entry,
                    validation_failures=validation_failures,
                    original_message_id=message_id,
                )

                if pending.should_escalate():
                    # Too many failed attempts, escalate
                    logger.warning(
                        "Escalating after %d failed correction attempts chat_id=%s",
                        pending.attempt_count,
                        chat_id,
                    )
                    _send_escalation(chat_id, sender_id, message, validation_failures)
                    tracker.remove_pending(chat_id)
                else:
                    # Send correction request to user (as reply to original message)
                    _send_correction_request(chat_id, validation_failures, entry, quoted_message_id=message_id)
            continue
        if confidence == "medium":
            log_medium_confidence(
                {
                    "event": "salesbot_medium_confidence",
                    "confidence": confidence,
                    "message": message,
                    "entry_index": idx,
                    "extracted": entry,
                }
            )
        if confidence not in {"high", "medium"}:
            logger.info("SalesBot confidence=%s skipping sheet write", confidence or "unknown")
            continue

        extracted_hotel = _extract_hotel_name(message, entry)
        staff_name, hotel_name, mapping_error = _resolve_staff_and_hotel(
            sender_id,
            extracted_hotel,
            str(_get_case_insensitive(entry, ["Asignee"]) or "").strip() or None,
        )
        if mapping_error:
            logger.error(
                "SalesBot skipping sheet write staff_mapping_error sender_id=%s message_preview=%s",
                sender_id,
                message[:200],
            )
            continue

        service = _get_case_insensitive(entry, ["Service"]) or ""
        quantity = _get_case_insensitive(entry, ["Quantity"]) or ""
        if isinstance(quantity, str) and not quantity.strip():
            quantity = 1
        if quantity is None:
            quantity = 1
        quantity_value = _coerce_quantity(quantity)
        quantity_row: Any = int(quantity_value) if quantity_value.is_integer() else quantity_value

        # Validate service against pricelist
        if service and chat_id:
            is_valid_service, matched_service, suggestions = _get_sales_audit().validate_service(str(service))
            if not is_valid_service:
                if suggestions:
                    # Has suggestions - prompt user to choose
                    logger.warning(
                        "Service '%s' not found in pricelist, sending suggestions chat_id=%s",
                        service,
                        chat_id,
                    )
                    _send_service_suggestions(chat_id, str(service), suggestions, quoted_message_id=message_id)

                    # Track as pending correction with service-specific failure and suggestions
                    tracker = get_correction_tracker()
                    tracker.add_pending(
                        chat_id=chat_id,
                        sender_id=sender_id,
                        original_message=message,
                        extracted_data=entry,
                        validation_failures=[f"Service '{service}' not found in price list"],
                        service_suggestions=suggestions,
                        original_message_id=message_id,
                    )
                    continue
                else:
                    # No suggestions - escalate to QueryBot
                    logger.warning(
                        "Service '%s' not found and no suggestions, escalating chat_id=%s",
                        service,
                        chat_id,
                    )
                    _escalate_unknown_service(chat_id, sender_id, str(service), message)
                    continue
            elif matched_service:
                # Use the matched service name from pricelist
                service = matched_service
                logger.debug("Service matched to pricelist: %s", service)
        # Get selling price and cost price from Pricing_Sales sheet
        selling_price = _get_sales_audit().get_selling_price(service, quantity_value, llm=_get_llm_interface())
        cost_price = _get_sales_audit().calculate_cost(service, quantity_value, llm=_get_llm_interface())

        # Never write entry with 0 selling or cost price - escalate instead
        if selling_price <= 0 or cost_price <= 0:
            logger.warning(
                "Skipping entry with zero price: service=%s selling_price=%s cost_price=%s chat_id=%s",
                service,
                selling_price,
                cost_price,
                chat_id,
            )
            if chat_id:
                # Alert admin about zero price issue
                _escalate_unknown_service(chat_id, sender_id, str(service), message)
            continue

        # Generate unique sale ID for commission tracking
        sale_id = generate_sale_id()

        try:
            _get_sales_audit().write_details_sheet(
                [
                    service,
                    quantity_row,
                    _get_case_insensitive(entry, ["Date"]) or "",
                    _get_case_insensitive(entry, ["Time"]) or "",
                    _get_case_insensitive(entry, ["Guest"]) or "",
                    _get_case_insensitive(entry, ["Room"]) or "",
                    staff_name,
                    selling_price,
                    cost_price,
                    "",  # Additional Details
                    hotel_name or extracted_hotel or "",
                    sale_id,
                ]
            )
        except Exception as exc:
            logger.error("SalesBot write failed: %s", exc, exc_info=True)
            continue
        logger.info(
            "SalesBot sheet write success sale_id=%s service=%s confidence=%s selling_price=%s cost_price=%s",
            sale_id,
            service,
            confidence,
            selling_price,
            cost_price,
        )
        logger.debug("SalesBot entry details: %s", entry)

        # Calculate and distribute commissions
        commission_entries = calculate_and_distribute_commissions(
            sale_id=sale_id,
            selling_price=selling_price,
            cost_price=cost_price,
            seller_name=staff_name,
        )

        # Send notification to sales group
        if commission_entries:
            _send_commission_notification(staff_name, service, commission_entries)


if __name__ == "__main__":
    test_msg2 = "Service: 2 Hammame\nDate : 04/03/2026 \nGuest:2px \nTime:6:00pm \nRoom:The Sahara Room \nArjun Rampal"
    process_message(test_msg2)
