# ruff: noqa: I001

from __future__ import annotations

import logging
from typing import Any

from shared.env import ensure_on_sys_path, load_project_env, project_root_from

PROJECT_ROOT = project_root_from(__file__, levels_up=4)
ensure_on_sys_path(PROJECT_ROOT)
load_project_env(PROJECT_ROOT)

from controlplane.control.bot.salesbot import config as salesbot_config  # noqa: E402
from controlplane.control.bot.salesbot.correction_tracker import get_correction_tracker  # noqa: E402
from controlplane.control.bot.salesbot.dependencies import (  # noqa: E402
    get_llm_interface as _get_llm_interface,
    get_sales_audit as _get_sales_audit,
)
from controlplane.control.bot.salesbot.services.correction_flow import (  # noqa: E402
    check_and_handle_correction_reply as _check_and_handle_correction_reply,
    process_expired_corrections as _process_expired_corrections,
)
from controlplane.control.bot.salesbot.services.extraction import (  # noqa: E402
    coerce_quantity as _coerce_quantity,
    extract_hotel_name as _extract_hotel_name,
    get_case_insensitive as _get_case_insensitive,
    is_sales_message as _is_sales_message,
    llm_extract,
    required_fields_present as _required_fields_present,
    resolve_staff_and_hotel as _resolve_staff_and_hotel,
    validate_extracted_data as _validate_extracted_data,
)
from controlplane.control.bot.salesbot.services.memory import (  # noqa: E402
    close_sales_correction_task as _close_sales_correction_task,
    record_sales_event as _record_sales_event,
    refresh_sales_summary as _refresh_sales_summary,
    remember_sales_correction_outcome as _remember_sales_correction_outcome,
)
from controlplane.control.bot.salesbot.services.messaging import (  # noqa: E402
    escalate_unknown_service as _escalate_unknown_service,
    send_commission_notification as _send_commission_notification,
    send_correction_request as _send_correction_request,
    send_escalation as _send_escalation,
    send_final_escalation as _send_final_escalation,
    send_service_suggestions as _send_service_suggestions,
)
from controlplane.control.commissionService import (  # noqa: E402
    calculate_and_distribute_commissions,
    generate_sale_id,
)
from shared.logging_context import log_low_confidence, log_medium_confidence  # noqa: E402

logger = logging.getLogger(__name__)

CORRECTION_TASK_TYPE = salesbot_config.CORRECTION_TASK_TYPE
ESCALATION_CHAT_IDS = salesbot_config.ESCALATION_CHAT_IDS


def _get_missing_mandatory_fields(entry: dict[str, Any]) -> list[str]:
    mandatory = [
        ("Date", ["Date"]),
        ("Time", ["Time"]),
        ("Room", ["Room"]),
        ("HotelName", ["HotelName", "hotel", "hotel_name", "Hotel"]),
    ]
    missing = []
    for label, keys in mandatory:
        val = _get_case_insensitive(entry, keys)
        if not val or (isinstance(val, str) and not val.strip()):
            missing.append(label)
    return missing


def _update_result_details(result_details: dict[str, Any] | None, **updates: Any) -> None:
    if result_details is None:
        return
    result_details.update({key: value for key, value in updates.items() if value is not None})


def check_and_handle_correction_reply(
    message: str,
    sender_id: str | None,
    sender_name: str | None,
    chat_id: str,
    *,
    reply_message_id: str | None = None,
) -> bool:
    return _check_and_handle_correction_reply(
        message,
        sender_id,
        sender_name,
        chat_id,
        process_message_fn=process_message,
        llm_extract_fn=llm_extract,
        reply_message_id=reply_message_id,
    )


def process_expired_corrections() -> int:
    return _process_expired_corrections()


def process_message(
    message: str,
    sender_id: str | None = None,
    chat_id: str | None = None,
    message_id: str | None = None,
    sender_name: str | None = None,
    extracted_override: dict[str, Any] | list[dict[str, Any]] | None = None,
    result_details: dict[str, Any] | None = None,
) -> bool:
    logger.debug(
        "SalesBot processing message length=%d sender_id=%s chat_id=%s message_id=%s sender_name=%s",
        len(message),
        sender_id,
        chat_id,
        message_id,
        sender_name,
    )

    tracker = get_correction_tracker() if chat_id else None
    has_pending_correction = bool(tracker and tracker.get_pending(chat_id, sender_id))
    if chat_id and (has_pending_correction or _is_sales_message(message)):
        _record_sales_event(
            role="user",
            text=message,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            metadata={"message_id": message_id} if message_id else {},
        )

    if chat_id and check_and_handle_correction_reply(
        message, sender_id, sender_name, chat_id, reply_message_id=message_id
    ):
        logger.info("Message handled as correction reply chat_id=%s", chat_id)
        _update_result_details(result_details, status="handled_as_correction")
        return False

    if extracted_override is None and not _is_sales_message(message):
        logger.info(
            "Ignoring non-sales message length=%d sender_id=%s message_preview=%s",
            len(message),
            sender_id,
            message[:100].replace("\n", " "),
        )
        _update_result_details(result_details, status="ignored", reason_code="not_sales_message")
        return False

    extracted = extracted_override
    if extracted is None:
        extracted = llm_extract(message, chat_id=chat_id, sender_id=sender_id, sender_name=sender_name)
        if isinstance(extracted, dict) and "error" in extracted:
            logger.error(
                "SalesBot extraction failed error=%s sender_id=%s message_preview=%s",
                extracted.get("error"),
                sender_id,
                message[:200],
            )
            _update_result_details(
                result_details,
                status="failed",
                reason_code="extraction_failed",
                error=extracted.get("error"),
            )
            return False

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
        _update_result_details(result_details, status="failed", reason_code="unsupported_extraction_type")
        return False

    if not entries:
        logger.error(
            "SalesBot extraction returned empty entries sender_id=%s message_preview=%s",
            sender_id,
            message[:200],
        )
        _update_result_details(result_details, status="failed", reason_code="empty_extraction")
        return False

    wrote_sale = False

    for idx, entry in enumerate(entries):
        message_type = str(_get_case_insensitive(entry, ["message_type"]) or "").strip().lower()
        if message_type == "non_sales":
            logger.info(
                "Entry %d classified as non_sales by LLM, skipping silently sender_id=%s",
                idx,
                sender_id,
            )
            continue

        confidence = str(_get_case_insensitive(entry, ["confidence"]) or "").lower()
        required_fields_present = _required_fields_present(entry)
        if not required_fields_present:
            confidence = "low"
            entry["confidence"] = "low"

        is_valid, validation_failures = _validate_extracted_data(entry)
        if not is_valid:
            confidence = "low"
            entry["confidence"] = "low"
            logger.warning("Entry %d failed sanity checks: %s", idx, "; ".join(validation_failures))

        service = _get_case_insensitive(entry, ["Service"]) or ""
        quantity = _get_case_insensitive(entry, ["Quantity"]) or ""
        if isinstance(quantity, str) and not quantity.strip():
            quantity = 1
        if quantity is None:
            quantity = 1
        quantity_value = _coerce_quantity(quantity)
        quantity_row: Any = int(quantity_value) if quantity_value.is_integer() else quantity_value

        if service and chat_id:
            is_valid_service, matched_service, suggestions = _get_sales_audit().validate_service(
                str(service),
                llm=_get_llm_interface(),
            )
            if not is_valid_service:
                if suggestions:
                    logger.warning(
                        "Service '%s' not found in pricelist, sending suggestions chat_id=%s",
                        service,
                        chat_id,
                    )
                    _missing_fields = _get_missing_mandatory_fields(entry)
                    _send_service_suggestions(
                        chat_id,
                        str(service),
                        suggestions,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        quoted_message_id=message_id,
                        missing_fields=_missing_fields,
                    )
                    tracker = get_correction_tracker()
                    tracker.add_pending(
                        chat_id=chat_id,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        original_message=message,
                        extracted_data=entry,
                        validation_failures=[f"Service '{service}' not found in price list"],
                        service_suggestions=suggestions,
                        original_message_id=message_id,
                        missing_fields=_missing_fields,
                    )
                    continue
                logger.warning("Service '%s' not found and no suggestions, escalating chat_id=%s", service, chat_id)
                _escalate_unknown_service(chat_id, sender_id, sender_name, str(service), message)
                continue
            if matched_service:
                service = matched_service
                entry["Service"] = matched_service
                logger.debug("Service matched to pricelist: %s", service)

        if confidence == "low" and required_fields_present and is_valid and service:
            confidence = "medium"
            entry["confidence"] = "medium"
            logger.info(
                "SalesBot promoted low-confidence extraction after successful service validation service=%s",
                service,
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

            if not validation_failures and service and chat_id:
                _, _, ambiguous_suggestions = _get_sales_audit().validate_service(
                    str(service), llm=None
                )
                if ambiguous_suggestions:
                    logger.warning(
                        "Low confidence with ambiguous service '%s', sending suggestions chat_id=%s",
                        service,
                        chat_id,
                    )
                    _missing_fields = _get_missing_mandatory_fields(entry)
                    _send_service_suggestions(
                        chat_id,
                        str(service),
                        ambiguous_suggestions,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        quoted_message_id=message_id,
                        missing_fields=_missing_fields,
                    )
                    tracker = get_correction_tracker()
                    tracker.add_pending(
                        chat_id=chat_id,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        original_message=message,
                        extracted_data=entry,
                        validation_failures=[f"Service '{service}' is ambiguous"],
                        service_suggestions=ambiguous_suggestions,
                        original_message_id=message_id,
                        missing_fields=_missing_fields,
                    )
                    continue

            if validation_failures and chat_id:
                tracker = get_correction_tracker()
                pending = tracker.add_pending(
                    chat_id=chat_id,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    original_message=message,
                    extracted_data=entry,
                    validation_failures=validation_failures,
                    original_message_id=message_id,
                    missing_fields=_get_missing_mandatory_fields(entry),
                )

                if pending.should_escalate():
                    logger.warning(
                        "Escalating after %d failed correction attempts chat_id=%s",
                        pending.attempt_count,
                        chat_id,
                    )
                    _send_escalation(chat_id, sender_id, sender_name, message, validation_failures)
                    _close_sales_correction_task(
                        chat_id,
                        sender_id=sender_id,
                        status="escalated",
                        resolution_note="Initial correction attempts were exhausted.",
                    )
                    _remember_sales_correction_outcome(
                        chat_id=chat_id,
                        sender_id=sender_id,
                        title="Escalated initial sales correction",
                        content=(
                            f"SalesBot escalated an initial correction request with failures: "
                            f"{', '.join(validation_failures)}."
                        ),
                        metadata={"validation_failures": validation_failures},
                    )
                    tracker.remove_pending(chat_id, sender_id)
                else:
                    _send_correction_request(
                        chat_id,
                        validation_failures,
                        entry,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        quoted_message_id=message_id,
                    )
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
            sender_name=sender_name,
        )
        if mapping_error:
            logger.error(
                "SalesBot skipping sheet write staff_mapping_error sender_id=%s sender_name=%s message_preview=%s",
                sender_id,
                sender_name,
                message[:200],
            )
            continue

        selling_price = _get_sales_audit().get_selling_price(service, quantity_value, llm=_get_llm_interface())
        cost_price = _get_sales_audit().calculate_cost(service, quantity_value, llm=_get_llm_interface())

        if selling_price <= 0 or cost_price <= 0:
            logger.warning(
                "Skipping entry with zero price: service=%s selling_price=%s cost_price=%s chat_id=%s",
                service,
                selling_price,
                cost_price,
                chat_id,
            )
            if chat_id:
                _send_final_escalation(
                    chat_id,
                    sender_id,
                    sender_name,
                    message,
                    reason_code="zero_price",
                    reason_details={
                        "service": service,
                        "selling_price": selling_price,
                        "cost_price": cost_price,
                        "quantity": quantity_value,
                    },
                )
                _remember_sales_correction_outcome(
                    chat_id=chat_id,
                    sender_id=sender_id,
                    title="Escalated zero price issue",
                    content=(
                        f"SalesBot found zero or missing price for service `{service}` "
                        f"with quantity `{quantity_value}`."
                    ),
                    metadata={"service": service, "quantity": quantity_value},
                )
            _update_result_details(
                result_details,
                status="failed",
                reason_code="zero_price",
                service=service,
                selling_price=selling_price,
                cost_price=cost_price,
                quantity=quantity_value,
            )
            continue

        profit = selling_price - cost_price
        if profit <= 0:
            logger.warning(
                "Skipping entry with non-positive profit: service=%s "
                "selling_price=%s cost_price=%s profit=%s chat_id=%s",
                service,
                selling_price,
                cost_price,
                profit,
                chat_id,
            )
            if chat_id:
                _send_final_escalation(
                    chat_id,
                    sender_id,
                    sender_name,
                    message,
                    reason_code="non_positive_profit",
                    reason_details={
                        "service": service,
                        "selling_price": selling_price,
                        "cost_price": cost_price,
                        "profit": profit,
                        "quantity": quantity_value,
                    },
                )
                _remember_sales_correction_outcome(
                    chat_id=chat_id,
                    sender_id=sender_id,
                    title="Escalated non-positive profit issue",
                    content=(
                        f"SalesBot found non-positive profit for service `{service}` with profit `{profit}` MAD."
                    ),
                    metadata={"service": service, "profit": profit, "quantity": quantity_value},
                )
            _update_result_details(
                result_details,
                status="failed",
                reason_code="non_positive_profit",
                service=service,
                selling_price=selling_price,
                cost_price=cost_price,
                profit=profit,
                quantity=quantity_value,
            )
            continue

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
                    "",
                    hotel_name or extracted_hotel or "",
                    sale_id,
                ]
            )
        except Exception as exc:
            logger.error("SalesBot write failed: %s", exc, exc_info=True)
            _update_result_details(
                result_details,
                status="failed",
                reason_code="write_failed",
                service=service,
                error=str(exc),
            )
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

        commission_entries = calculate_and_distribute_commissions(
            sale_id=sale_id,
            selling_price=selling_price,
            cost_price=cost_price,
            seller_name=staff_name,
        )
        if commission_entries:
            _send_commission_notification(staff_name, service, commission_entries)

        if chat_id:
            _record_sales_event(
                role="system",
                text=(
                    f"Recorded sale `{sale_id}` for service `{service}` "
                    f"at hotel `{hotel_name or extracted_hotel or ''}`."
                ),
                chat_id=chat_id,
                sender_id=sender_id,
                sender_name=sender_name,
                event_type="sale_recorded",
                metadata={"sale_id": sale_id, "service": service, "hotel": hotel_name or extracted_hotel or ""},
            )
            _refresh_sales_summary(chat_id, sender_id)
            from controlplane.control.bot.salesbot.services.messaging import react_on_message as _react
            _react(chat_id, message_id, "✅")
        wrote_sale = True
        _update_result_details(
            result_details,
            status="recorded",
            service=service,
            sale_id=sale_id,
        )

    return wrote_sale


if __name__ == "__main__":
    test_msg2 = "Service: 2 Hammame\nDate : 04/03/2026 \nGuest:2px \nTime:6:00pm \nRoom:The Sahara Room \nArjun Rampal"
    process_message(test_msg2)
