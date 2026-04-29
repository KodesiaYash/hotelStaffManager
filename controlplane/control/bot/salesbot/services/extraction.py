from __future__ import annotations

import json
import logging
import re
from typing import Any

from controlplane.boundary.storageInterface.staffToHotelMapping import normalize_phone
from controlplane.control.bot.salesbot.config import DEFAULT_PROMPT, KNOWN_HOTELS
from controlplane.control.bot.salesbot.dependencies import get_llm_interface, get_staff_mapping
from controlplane.control.bot.salesbot.services.memory import build_sales_memory_context

logger = logging.getLogger(__name__)


def get_case_insensitive(row: dict[str, Any], keys: list[str]) -> Any | None:
    lookup = {str(k).strip().lower(): k for k in row}
    for key in keys:
        normalized = str(key).strip().lower()
        if normalized in lookup:
            return row.get(lookup[normalized])
    return None


def normalize_hotel_name(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if "roxanne" in lowered:
        return "RIAD Roxanne"
    if "persephone" in lowered:
        return "RIAD Persephone"
    return value.strip()


def coerce_quantity(value: Any) -> float:
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


def _get_llm_provider_name() -> str:
    llm = get_llm_interface()
    return type(llm).__name__


def llm_extract(
    message: str,
    *,
    chat_id: str | None = None,
    sender_id: str | None = None,
    sender_name: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    prompt = (
        DEFAULT_PROMPT.replace(
            "__MEMORY_CONTEXT__",
            build_sales_memory_context(
                message=message,
                chat_id=chat_id,
                sender_id=sender_id,
                sender_name=sender_name,
            ),
        ).replace("__MESSAGE__", message)
    )
    provider_name = _get_llm_provider_name()

    logger.debug("SalesBot LLM extract prompt length=%d provider=%s", len(prompt), provider_name)
    try:
        response_text = get_llm_interface().generate(prompt)
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

    extracted = safe_json_load(response_text, original_message=message)
    if isinstance(extracted, dict) and extracted.get("error") == "parse_failed":
        logger.warning("SalesBot LLM parse failed on first attempt, retrying message_len=%d", len(message))
        prompt = f"{prompt}\nCRITICAL: Pure JSON, no extra text."
        try:
            response_text = get_llm_interface().generate(prompt)
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
        extracted = safe_json_load(response_text, original_message=message)
        if isinstance(extracted, dict) and extracted.get("error") == "parse_failed":
            logger.warning(
                "SalesBot LLM parse failed after retry message_preview=%s raw_response=%s",
                message[:200],
                (response_text or "")[:200],
            )
    return extracted


def safe_json_load(
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


_HOTEL_KEYWORDS = ("riad", "roxanne", "persephone", "persephon")
_HOTEL_KEYS = ["HotelName", "hotel", "hotel_name", "Hotel"]


def _looks_like_hotel(value: str) -> bool:
    low = value.strip().lower()
    return any(kw in low for kw in _HOTEL_KEYWORDS)


def required_fields_present(extracted: dict[str, Any]) -> bool:
    required = ["Service", "Date", "Time", "Room", "HotelName"]
    for key in required:
        keys = ["HotelName", "hotel", "hotel_name", "Hotel"] if key == "HotelName" else [key]
        value = get_case_insensitive(extracted, keys)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
    return True


def validate_extracted_data(extracted: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []

    quantity = get_case_insensitive(extracted, ["Quantity"])
    quantity_value = coerce_quantity(quantity) if quantity is not None else 1.0
    if quantity_value <= 0:
        failures.append(f"Quantity is invalid or <= 0: {quantity}")
        logger.warning("Sanity check failed: Quantity=%s (value=%s)", quantity, quantity_value)

    service = get_case_insensitive(extracted, ["Service"])
    if not service or (isinstance(service, str) and not service.strip()):
        failures.append("Service is empty or missing")
        logger.warning("Sanity check failed: Service is empty")

    date = get_case_insensitive(extracted, ["Date"])
    if not date or (isinstance(date, str) and not date.strip()):
        failures.append("Date is empty or missing")
        logger.warning("Sanity check failed: Date is empty")
    elif isinstance(date, str):
        date_pattern = r"^\d{1,2}/\d{1,2}/\d{4}$"
        if not re.match(date_pattern, date.strip()):
            failures.append(f"Date format invalid (expected DD/MM/YYYY): {date}")
            logger.warning("Sanity check failed: Date format invalid: %s", date)

    time_val = get_case_insensitive(extracted, ["Time"])
    if not time_val or (isinstance(time_val, str) and not time_val.strip()):
        failures.append("Time is empty or missing")
        logger.warning("Sanity check failed: Time is empty")

    room = get_case_insensitive(extracted, ["Room"])
    _room_str = room.strip().lower() if isinstance(room, str) else ""
    _room_is_hotel = any(kw in _room_str for kw in _HOTEL_KEYWORDS)
    if not room or (isinstance(room, str) and not room.strip()) or _room_is_hotel:
        failures.append("Room is empty or missing")
        logger.warning("Sanity check failed: Room is empty or looks like hotel name: %s", room)

    hotel = get_case_insensitive(extracted, ["HotelName", "hotel", "hotel_name", "Hotel"])
    if not hotel or (isinstance(hotel, str) and not hotel.strip()):
        failures.append("Hotel name is empty or missing")
        logger.warning("Sanity check failed: HotelName is empty")

    guest = get_case_insensitive(extracted, ["Guest"])
    if guest and isinstance(guest, str) and guest.strip():
        guest_value = coerce_quantity(guest)
        if guest_value <= 0:
            failures.append(f"Guest count is invalid or <= 0: {guest}")
            logger.warning("Sanity check failed: Guest=%s (value=%s)", guest, guest_value)

    return len(failures) == 0, failures


def extract_hotel_name(message: str, extracted: dict[str, Any]) -> str | None:
    value = get_case_insensitive(extracted, ["HotelName", "hotel", "hotel_name", "Hotel"])
    if isinstance(value, str) and value.strip():
        return normalize_hotel_name(value)
    lowered = message.lower()
    for token, canonical in KNOWN_HOTELS.items():
        if token in lowered:
            return canonical
    return None


def resolve_staff_and_hotel(
    sender_id: str | None,
    extracted_hotel: str | None,
    extracted_name: str | None,
    sender_name: str | None = None,
) -> tuple[str, str | None, bool]:
    mapping = get_staff_mapping()
    if mapping is None:
        logger.error("Staff mapping not configured; cannot resolve staff name")
        return extracted_name or "", extracted_hotel, True

    matches: list[dict[str, Any]] = []

    if sender_name:
        try:
            matches = mapping.find_by_username(sender_name)
            if matches:
                logger.debug("Found staff mapping by username=%s", sender_name)
        except Exception as exc:
            logger.warning("Username lookup failed: %s", exc)

    if not matches:
        normalized = normalize_phone(sender_id)
        if normalized:
            try:
                matches = mapping.find_by_phone(normalized)
                if matches:
                    logger.debug("Found staff mapping by phone=%s", normalized)
            except Exception as exc:
                logger.error("Failed to read staff mapping: %s", exc, exc_info=True)
                return extracted_name or "", extracted_hotel, True

    if not matches:
        logger.error("No staff mapping for username=%s phone=%s", sender_name, sender_id)
        return extracted_name or "", extracted_hotel, True

    names = {
        str(val).strip()
        for val in (get_case_insensitive(row, ["name", "staff", "staff_name", "employee"]) for row in matches)
        if val
    }
    if not names:
        logger.error("Staff mapping missing name for username=%s phone=%s", sender_name, sender_id)
        return extracted_name or "", extracted_hotel, True
    if len(names) > 1:
        logger.warning("Multiple staff names found for username=%s phone=%s: %s", sender_name, sender_id, sorted(names))
    staff_name = sorted(names)[0]

    hotels = {
        normalize_hotel_name(str(val)) or str(val).strip()
        for val in (get_case_insensitive(row, ["hotel", "hotel_name", "property"]) for row in matches)
        if val
    }
    if extracted_hotel:
        extracted_hotel = normalize_hotel_name(extracted_hotel) or extracted_hotel
        return staff_name, extracted_hotel, False
    if len(hotels) == 1:
        return staff_name, next(iter(hotels)), False
    if len(hotels) > 1:
        logger.error(
            "Ambiguous staff mapping: username=%s phone=%s supports multiple hotels %s but message has no hotel name",
            sender_name,
            sender_id,
            sorted(hotels),
        )
        return staff_name, None, True
    logger.error("Staff mapping missing hotel for username=%s phone=%s", sender_name, sender_id)
    return staff_name, extracted_hotel, True


def is_sales_message(message: str) -> bool:
    if not message:
        return False

    message_lower = message.lower()
    riad_identifiers = [
        "riad roxanne",
        "riad persephone",
        "roxanne",
        "persephone",
        "persephon",
        "riad roxann",
        "roxann",
    ]
    if any(identifier in message_lower for identifier in riad_identifiers):
        return True

    sales_field_keywords = [
        "service",
        "date",
        "time",
        "room",
        "guest",
        "quantity",
        "qty",
    ]
    field_count = sum(1 for kw in sales_field_keywords if kw in message_lower)
    return field_count >= 2
