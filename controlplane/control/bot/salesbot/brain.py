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

from controlplane.boundary.llminterface.llm_interface import (  # noqa: E402
    LLMInterface,
    get_sales_bot_llm,
)
from controlplane.boundary.storageInterface.salesAudit import SalesAudit  # noqa: E402
from controlplane.boundary.storageInterface.staffToHotelMapping import (  # noqa: E402
    StaffToHotelMapping,
    normalize_phone,
)
from shared.logging_context import (  # noqa: E402
    log_low_confidence,
    log_medium_confidence,
)

logger = logging.getLogger(__name__)

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
    '{"Service": "task or \'\'", "Quantity": "number or \'1\'", "Date": "number or \'\'", '
    '"Time": "number or \'\'", "Guest": "name or \'\'", "Room": "name or \'\'", '
    '"Asignee": "name or \'\'", "HotelName": "RIAD Roxanne or RIAD Persephone or \'\'", '
    '"Amount": number or 0, '
    '"confidence": "high/medium/low"}'
    "] "
    "If Service, Date, Time, or Room is missing or unclear, set confidence to low. "
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


def llm_extract(message: str) -> dict[str, Any] | list[dict[str, Any]]:
    """LLM extracts structured data from message using configured provider."""
    prompt = DEFAULT_PROMPT.replace("__MESSAGE__", message)

    logger.info("SalesBot LLM extract prompt length=%d", len(prompt))
    response_text = _get_llm_interface().generate(prompt)
    extracted = _safe_json_load(response_text)
    if isinstance(extracted, dict) and extracted.get("error") == "parse_failed":
        logger.info("SalesBot parse failed, retrying with strict JSON prompt")
        prompt = f"{prompt}\nCRITICAL: Pure JSON, no extra text."
        response_text = _get_llm_interface().generate(prompt)
        extracted = _safe_json_load(response_text)
    return extracted


def _safe_json_load(text: str | None) -> dict[str, Any] | list[dict[str, Any]]:
    if not text:
        return {"error": "empty_response"}
    cleaned = text.strip()
    cleaned = re.sub(r"```json\s*|\s*```", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = cleaned.replace("\\n", "").replace("\\t", "")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.info("JSON Error: %s", exc)
        logger.info("Raw (first 120): %r", text[:120])
        logger.info("Cleaned (first 120): %r", cleaned[:120])
        return {"error": "parse_failed", "raw": cleaned}


def _required_fields_present(extracted: dict[str, Any]) -> bool:
    required = ["Service", "Date", "Time", "Room"]
    for key in required:
        value = _get_case_insensitive(extracted, [key])
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
    return True


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


def process_message(message: str, sender_id: str | None = None) -> None:
    logger.info("SalesBot processing message length=%d", len(message))
    extracted = llm_extract(message)
    if isinstance(extracted, dict) and "error" in extracted:
        logger.error("SalesBot extraction failed: %s", extracted)
        return

    entries: list[dict[str, Any]]
    if isinstance(extracted, list):
        entries = [entry for entry in extracted if isinstance(entry, dict)]
    elif isinstance(extracted, dict):
        entries = [extracted]
    else:
        logger.error("SalesBot extraction returned unsupported type: %s", type(extracted))
        return

    if not entries:
        logger.error("SalesBot extraction returned empty entries")
        return

    for idx, entry in enumerate(entries):
        confidence = str(_get_case_insensitive(entry, ["confidence"]) or "").lower()
        if not _required_fields_present(entry):
            confidence = "low"
            entry["confidence"] = "low"
        if confidence == "low":
            log_low_confidence(
                {
                    "event": "salesbot_low_confidence",
                    "confidence": confidence,
                    "message": message,
                    "entry_index": idx,
                    "extracted": entry,
                }
            )
            logger.info("Skipping sheet write due to confidence=%s", confidence or "unknown")
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
            logger.info("Skipping sheet write due to confidence=%s", confidence or "unknown")
            continue

        extracted_hotel = _extract_hotel_name(message, entry)
        staff_name, hotel_name, mapping_error = _resolve_staff_and_hotel(
            sender_id,
            extracted_hotel,
            str(_get_case_insensitive(entry, ["Asignee"]) or "").strip() or None,
        )
        if mapping_error:
            logger.error("Skipping sheet write due to staff/hotel mapping error")
            continue

        service = _get_case_insensitive(entry, ["Service"]) or ""
        quantity = _get_case_insensitive(entry, ["Quantity"]) or ""
        if isinstance(quantity, str) and not quantity.strip():
            quantity = 1
        if quantity is None:
            quantity = 1
        quantity_value = _coerce_quantity(quantity)
        quantity_row: Any = int(quantity_value) if quantity_value.is_integer() else quantity_value
        cost = _get_sales_audit().calculate_cost(service, quantity_value, llm=_get_llm_interface())

        try:
            cost = _get_sales_audit().write_details_sheet(
                [
                    service,
                    quantity_row,
                    _get_case_insensitive(entry, ["Date"]) or "",
                    _get_case_insensitive(entry, ["Time"]) or "",
                    _get_case_insensitive(entry, ["Guest"]) or "",
                    _get_case_insensitive(entry, ["Room"]) or "",
                    staff_name,
                    cost,
                    "",
                    hotel_name or extracted_hotel or "",
                ]
            )
        except Exception as exc:
            logger.error("SalesBot write failed: %s", exc, exc_info=True)
            continue
        logger.info("SalesBot logged entry cost=Rs%s", cost)
        logger.info("SalesBot entry: %s", entry)


if __name__ == "__main__":
    test_msg2 = "Service: 2 Hammame\nDate : 04/03/2026 \nGuest:2px \nTime:6:00pm \nRoom:The Sahara Room \nArjun Rampal"
    process_message(test_msg2)
