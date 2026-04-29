from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from controlplane.control.bot.salesbot.dependencies import get_llm_interface
from controlplane.control.bot.salesbot.services.memory import build_sales_memory_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceReplyInterpretation:
    matched_service: str | None = None
    confidence: str = "low"


@dataclass
class CombinedReplyInterpretation:
    matched_service: str | None = None
    field_values: dict[str, str] = field(default_factory=dict)
    confidence: str = "low"
    unresolved_fields: list[str] = field(default_factory=list)


def build_correction_request_message(
    *,
    validation_failures: list[str],
    extracted_data: dict[str, Any],
    chat_id: str | None,
    sender_id: str | None,
    sender_name: str | None,
) -> str:
    fallback = _fallback_correction_request_message(validation_failures, extracted_data)
    memory_context = build_sales_memory_context(
        message="Need a natural correction request for missing or invalid sale details.",
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
    )
    prompt = (
        "You are SalesBot speaking to hotel staff over WhatsApp. "
        "Write a short, warm, human-sounding message asking the user to provide the missing booking details. "
        "Do not sound like a form, template, or machine. "
        "Keep it concise and practical.\n\n"
        "STRICT RULE: Ask ONLY about the exact fields listed in 'Validation failures' below. "
        "Do NOT add, infer, or mention any other fields — even if the extracted data looks incomplete. "
        "The validation failures list is the single authoritative source of what is missing.\n\n"
        "Recent operating context:\n"
        f"{memory_context}\n\n"
        f"Validation failures (the ONLY fields to ask about): {validation_failures}\n\n"
        "Return only the WhatsApp message text."
    )
    return _generate_text(prompt, fallback)


def build_service_clarification_message(
    *,
    service_name: str,
    suggestions: list[tuple[str, float]],
    chat_id: str | None,
    sender_id: str | None,
    sender_name: str | None,
    user_reply: str | None = None,
    attempt_count: int = 1,
    missing_fields: list[str] | None = None,
) -> str:
    fallback = _fallback_service_clarification_message(
        service_name=service_name,
        suggestions=suggestions,
        user_reply=user_reply,
        missing_fields=missing_fields,
    )
    memory_context = build_sales_memory_context(
        message=user_reply or service_name,
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
    )
    missing_section = ""
    if missing_fields:
        missing_section = (
            f"\n\nIn addition to the service name, the following mandatory booking fields are also missing: "
            f"{', '.join(missing_fields)}. "
            "Ask for these in the SAME message. "
            "Tell the user they can reply with everything at once in any order, for example: "
            "'Palmerie, 25/04/2026, 9pm, Paradise, RIAD Persephone'. "
            "Mention that date can be in any format, time can be hour only (will default to :00), "
            "and hotel should be RIAD Persephone or RIAD Roxanne."
        )
    prompt = (
        "You are SalesBot speaking to hotel staff over WhatsApp. "
        "Write a short, friendly clarification message about an unclear service name. "
        "Sound like a real person, not a form or decision tree. "
        "If this is the first clarification, ask what they meant and mention the closest possible services. "
        "If this is a follow-up after an unclear reply, say you still did not fully understand "
        "and ask once more naturally. "
        "Make it clear the user can answer naturally — they can reply with service name, "
        "short phrase, number, or a combined answer with all missing details at once. "
        "Keep it concise."
        f"{missing_section}\n\n"
        "Recent operating context:\n"
        f"{memory_context}\n\n"
        f"Original unclear service: {service_name}\n"
        f"Closest suggestions: {[name for name, _ in suggestions]}\n"
        f"Missing mandatory fields: {missing_fields or []}\n"
        f"Attempt count: {attempt_count}\n"
        f"Latest user reply: {user_reply or ''}\n\n"
        "Return only the WhatsApp message text."
    )
    return _generate_text(prompt, fallback)


def interpret_combined_reply(
    *,
    original_service: str,
    user_reply: str,
    suggestions: list[tuple[str, float]],
    missing_fields: list[str],
    chat_id: str | None,
    sender_id: str | None,
    sender_name: str | None,
) -> CombinedReplyInterpretation:
    """Parse a free-form reply that may contain service selection + missing field values.

    The user might reply with: 'Palmerie, 25/04, 9pm, Paradise, RIAD Persephone'
    or just: 'Palmerie' or '1' or 'Camel Ride Agafay 25/4 room paradise'.
    """
    suggestion_names = [name for name, _ in suggestions]
    missing_section = ""
    if missing_fields:
        missing_section = (
            "Missing fields to also extract from the reply (if provided): "
            + json.dumps(missing_fields)
            + "\n"
        )
    prompt = (
        "You are parsing a hotel staff member's reply to a SalesBot clarification request.\n"
        "The staff member was asked to:\n"
        f"  1. Clarify which service they meant (original unclear value: '{original_service}')\n"
        f"     Possible services: {suggestion_names}\n"
        f"  2. Provide missing booking fields: {missing_fields}\n\n"
        "The reply may contain some or all of this information in any order, with or without labels, "
        "with or without commas, in informal or shorthand style.\n\n"
        "Rules for extracting each field:\n"
        "SERVICE: Match the reply against the possible services. Accept partial names, short forms, "
        "numbers (1 = first suggestion, 2 = second, etc.), ordinals (first, second), "
        "or informal phrases ('I meant X', 'its X'). Leave empty string if genuinely unclear.\n"
        "DATE: Accept any date format (25/04, 25 april, april 25, 25-04-2026, etc.). "
        "Convert to DD/MM/YYYY. If year is missing, use the current or nearest future year. "
        "Leave empty string if no date found.\n"
        "TIME: Accept any time format (9pm, 21:00, 9, 9h, 09:30, 9:30am, 9-10pm take minimum = 21:00). "
        "Convert to HH:MM 24-hour format. If only hour given (e.g. '9pm'), use HH:00. "
        "If a range is given (e.g. '9-11pm'), take the start time. "
        "Leave empty string if no time found.\n"
        "ROOM: Accept any room name as-is. Leave empty string if not found.\n"
        "HOTELNAME: Normalize to exactly 'RIAD Persephone' or 'RIAD Roxanne'. "
        "Accept typos/abbreviations (persephon, roxann, riad p, riad r). "
        "Leave empty string if not found.\n\n"
        f"{missing_section}"
        "Examples:\n"
        "- Reply='Palmerie, 25/04, 9pm, Paradise, RIAD Persephone' with suggestions=['Camel Ride Agafay','Camel Ride Palmeraie']\n"
        "  → service='Camel Ride Palmeraie', Date='25/04/2026', Time='21:00', Room='Paradise', HotelName='RIAD Persephone'\n"
        "- Reply='1 tomorrow 8am' with suggestions=['Hammam 1h','Hammam + Massage']\n"
        "  → service='Hammam 1h', Date='<tomorrow as DD/MM/YYYY>', Time='08:00'\n"
        "- Reply='camel agafay' with suggestions=['Camel Ride Agafay','Camel Ride Palmeraie']\n"
        "  → service='Camel Ride Agafay'\n"
        "- Reply='9-11pm' (only time range provided)\n"
        "  → Time='21:00' (start of range in 24h)\n\n"
        "Return ONLY valid JSON:\n"
        '{\n'
        '  "service": "<exact suggestion name or empty string>",\n'
        '  "service_confidence": "high|medium|low",\n'
        '  "fields": {\n'
        '    "Date": "DD/MM/YYYY or empty",\n'
        '    "Time": "HH:MM or empty",\n'
        '    "Room": "name or empty",\n'
        '    "HotelName": "RIAD Persephone or RIAD Roxanne or empty"\n'
        '  }\n'
        '}\n\n'
        f'User reply: "{user_reply}"\n'
    )
    data = _generate_json(prompt)
    if not data:
        interp = _fallback_interpretation(user_reply=user_reply, suggestions=suggestions)
        return CombinedReplyInterpretation(
            matched_service=interp.matched_service,
            confidence=interp.confidence,
            unresolved_fields=list(missing_fields),
        )

    raw_service = str(data.get("service") or "").strip()
    service_confidence = str(data.get("service_confidence") or "low").strip().lower()
    fields_raw: dict[str, Any] = data.get("fields") or {}

    matched_service: str | None = None
    if raw_service and service_confidence != "low":
        for suggestion_name, _ in suggestions:
            if suggestion_name.lower() == raw_service.lower():
                matched_service = suggestion_name
                break
        if not matched_service:
            interp = _fallback_interpretation(user_reply=raw_service, suggestions=suggestions)
            matched_service = interp.matched_service

    field_values: dict[str, str] = {}
    for key in ("Date", "Time", "Room", "HotelName"):
        val = str(fields_raw.get(key) or "").strip()
        if val:
            field_values[key] = val

    unresolved = [f for f in missing_fields if not field_values.get(f)]
    return CombinedReplyInterpretation(
        matched_service=matched_service,
        field_values=field_values,
        confidence=service_confidence if matched_service else "low",
        unresolved_fields=unresolved,
    )


def interpret_service_reply(
    *,
    original_service: str,
    user_reply: str,
    suggestions: list[tuple[str, float]],
    chat_id: str | None,
    sender_id: str | None,
    sender_name: str | None,
) -> ServiceReplyInterpretation:
    if not suggestions:
        return ServiceReplyInterpretation()

    memory_context = build_sales_memory_context(
        message=user_reply,
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
    )
    prompt = (
        "You are helping SalesBot understand a hotel staff member's correction reply. "
        "The staff member may use shorthand, partial names, bad spelling, broken English, low-literacy wording, "
        "or simple replies like `first option`, `1`, or `I meant hammam`. "
        "Choose the single best matching suggestion only if the reply clearly points to it. "
        "If it is still unclear, leave match empty. "
        "Only choose from the provided suggestions.\n\n"
        "Recent operating context:\n"
        f"{memory_context}\n\n"
        f'Original unclear service: "{original_service}"\n'
        f'User reply: "{user_reply}"\n'
        f"Suggestions: {[name for name, _ in suggestions]}\n\n"
        'Return ONLY JSON: {"match": "<exact suggestion or empty>", "confidence": "high|medium|low"}'
    )
    data = _generate_json(prompt)
    if not data:
        return _fallback_interpretation(user_reply=user_reply, suggestions=suggestions)

    match = str(data.get("match") or "").strip()
    confidence = str(data.get("confidence") or "").strip().lower() or "low"
    if not match or confidence == "low":
        return _fallback_interpretation(user_reply=user_reply, suggestions=suggestions)

    for suggestion_name, _ in suggestions:
        if suggestion_name.lower() == match.lower():
            return ServiceReplyInterpretation(matched_service=suggestion_name, confidence=confidence)
    return _fallback_interpretation(user_reply=user_reply, suggestions=suggestions)


def _generate_text(prompt: str, fallback: str) -> str:
    try:
        text = get_llm_interface().generate(prompt).strip()
    except Exception as exc:
        logger.warning("SalesBot dialogue generation failed: %s", exc)
        return fallback
    return text or fallback


def _generate_json(prompt: str) -> dict[str, Any] | None:
    try:
        response = get_llm_interface().generate(prompt).strip()
    except Exception as exc:
        logger.warning("SalesBot dialogue JSON generation failed: %s", exc)
        return None
    if not response:
        return None
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        logger.warning("SalesBot dialogue JSON parse failed: %s", response[:200])
        return None


def _fallback_correction_request_message(validation_failures: list[str], extracted_data: dict[str, Any]) -> str:
    details = ", ".join(validation_failures) or "some sale details"
    service = str(extracted_data.get("Service") or "").strip()
    service_line = f" for `{service}`" if service else ""
    return (
        f"Hey, I need a little more detail{service_line} before I can record this sale. "
        f"Could you please clarify: {details}?"
    )


def _fallback_service_clarification_message(
    *,
    service_name: str,
    suggestions: list[tuple[str, float]],
    user_reply: str | None,
    missing_fields: list[str] | None = None,
) -> str:
    intro = (
        f"Hey, I couldn't fully understand the service `{service_name}`."
        if not user_reply
        else f"I still couldn't fully understand what you meant by `{user_reply}`."
    )
    lines = [intro, "What did you mean by this?", "Possible suggestions are:"]
    for index, (name, score) in enumerate(suggestions, 1):
        lines.append(f"{index}. {name} ({int(score * 100)}% match)")
    if missing_fields:
        lines.append(f"\nAlso, these booking details are missing: {', '.join(missing_fields)}.")
        lines.append(
            "You can reply with everything at once in any order, e.g.:\n"
            "`Palmerie, 25/04, 9pm, Paradise, RIAD Persephone`"
        )
    else:
        lines.append("You can reply naturally, for example `I meant hammam`, `hammam`, `first option`, or `1`.")
    return "\n".join(lines)


def _fallback_interpretation(
    *,
    user_reply: str,
    suggestions: list[tuple[str, float]],
) -> ServiceReplyInterpretation:
    normalized_reply = _normalize_ordinal_reply(user_reply.strip().lower())
    if not normalized_reply:
        return ServiceReplyInterpretation()

    ordinal_map = {
        "first": 0,
        "1st": 0,
        "one": 0,
        "1": 0,
        "second": 1,
        "2nd": 1,
        "two": 1,
        "2": 1,
        "third": 2,
        "3rd": 2,
        "three": 2,
        "3": 2,
        "fourth": 3,
        "4th": 3,
        "four": 3,
        "4": 3,
        "fifth": 4,
        "5th": 4,
        "five": 4,
        "5": 4,
        "last": len(suggestions) - 1,
    }
    selected_index = ordinal_map.get(normalized_reply, -1)
    if 0 <= selected_index < len(suggestions):
        return ServiceReplyInterpretation(
            matched_service=suggestions[selected_index][0],
            confidence="medium",
        )

    cleaned_reply = _strip_reply_prefixes(normalized_reply)
    for suggestion_name, _ in suggestions:
        suggestion_lower = suggestion_name.lower()
        if cleaned_reply == suggestion_lower or cleaned_reply in suggestion_lower or suggestion_lower in cleaned_reply:
            return ServiceReplyInterpretation(matched_service=suggestion_name, confidence="medium")
    return ServiceReplyInterpretation()


def _strip_reply_prefixes(reply: str) -> str:
    cleaned = " ".join(reply.split())
    prefixes = [
        "i meant ",
        "meant ",
        "it was ",
        "its ",
        "it's ",
        "service is ",
        "service was ",
        "the service is ",
        "maybe ",
        "probably ",
    ]
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :].strip()
    return cleaned


def _normalize_ordinal_reply(reply: str) -> str:
    cleaned = " ".join(reply.split())
    suffixes = [" option", " one", " please", " pls"]
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned
