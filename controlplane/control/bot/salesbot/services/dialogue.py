from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from controlplane.control.bot.salesbot.dependencies import get_llm_interface
from controlplane.control.bot.salesbot.services.memory import build_sales_memory_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceReplyInterpretation:
    matched_service: str | None = None
    confidence: str = "low"


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
        "Write a short, warm, human-sounding message asking the user to clarify missing or incorrect sale details. "
        "Do not sound like a form, template, or machine. "
        "Do not ask them to fill a rigid format unless absolutely necessary. "
        "Ask only for the fields that are actually missing or wrong. "
        "Keep it concise and practical.\n\n"
        "Recent operating context:\n"
        f"{memory_context}\n\n"
        f"Validation failures: {validation_failures}\n"
        f"Current extracted data: {json.dumps(extracted_data, ensure_ascii=True)}\n\n"
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
) -> str:
    fallback = _fallback_service_clarification_message(
        service_name=service_name,
        suggestions=suggestions,
        user_reply=user_reply,
    )
    memory_context = build_sales_memory_context(
        message=user_reply or service_name,
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
    )
    prompt = (
        "You are SalesBot speaking to hotel staff over WhatsApp. "
        "Write a short, friendly clarification message about an unclear service name. "
        "Sound like a real person, not a form or decision tree. "
        "If this is the first clarification, ask what they meant and mention the closest possible services. "
        "If this is a follow-up after an unclear reply, say you still did not fully understand "
        "and ask once more naturally. "
        "Make it clear the user can answer naturally, for example with the service name, "
        "a short phrase, or first option. "
        "Keep it concise.\n\n"
        "Recent operating context:\n"
        f"{memory_context}\n\n"
        f"Original unclear service: {service_name}\n"
        f"Closest suggestions: {[name for name, _ in suggestions]}\n"
        f"Attempt count: {attempt_count}\n"
        f"Latest user reply: {user_reply or ''}\n\n"
        "Return only the WhatsApp message text."
    )
    return _generate_text(prompt, fallback)


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
) -> str:
    intro = (
        f"Hey, I couldn't fully understand the service `{service_name}`."
        if not user_reply
        else f"I still couldn't fully understand what you meant by `{user_reply}`."
    )
    lines = [intro, "What did you mean by this?", "Possible suggestions are:"]
    for index, (name, score) in enumerate(suggestions, 1):
        lines.append(f"{index}. {name} ({int(score * 100)}% match)")
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
