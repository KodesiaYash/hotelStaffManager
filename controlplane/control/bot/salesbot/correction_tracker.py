"""Track pending corrections for SalesBot validation failures.

This module maintains a mapping of per-user conversation thread -> pending correction requests,
allowing users to reply with corrected information that gets merged with
the original extraction.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# How long to keep pending corrections before expiring (in seconds)
# Default: 24 hours - after this, escalate to QueryBot
CORRECTION_EXPIRY_SECONDS = int(os.getenv("CORRECTION_EXPIRY_SECONDS", "86400"))  # 24 hours default

# Maximum retry attempts before escalating
MAX_CORRECTION_ATTEMPTS = int(os.getenv("MAX_CORRECTION_ATTEMPTS", "2"))


@dataclass
class PendingCorrection:
    """Represents a pending correction request."""

    chat_id: str
    sender_id: str | None
    sender_name: str | None
    original_message: str
    extracted_data: dict[str, Any]
    validation_failures: list[str]
    created_at: float = field(default_factory=time.time)
    attempt_count: int = 1
    # Service suggestions for numeric selection (e.g., user replies "1" to select first option)
    service_suggestions: list[tuple[str, float]] = field(default_factory=list)
    # Original message ID for quoted replies
    original_message_id: str | None = None
    # Awaiting confirmation for selected service
    awaiting_service_confirmation: str | None = None
    # Mandatory fields that were missing when the correction was created
    missing_fields: list[str] = field(default_factory=list)

    def is_expired(self) -> bool:
        return time.time() - self.created_at > CORRECTION_EXPIRY_SECONDS

    def should_escalate(self) -> bool:
        return self.attempt_count >= MAX_CORRECTION_ATTEMPTS

    def get_selected_service(self, reply: str) -> str | None:
        """Parse user reply to get selected service.

        Accept simple ordinal replies such as 1, first, second, last, etc.
        """
        reply = reply.strip().lower()
        if not reply:
            return None

        ordinal_map = {
            "first": 0,
            "1st": 0,
            "one": 0,
            "second": 1,
            "2nd": 1,
            "two": 1,
            "third": 2,
            "3rd": 2,
            "three": 2,
            "fourth": 3,
            "4th": 3,
            "four": 3,
            "fifth": 4,
            "5th": 4,
            "five": 4,
            "last": len(self.service_suggestions) - 1,
        }

        index = int(reply) - 1 if reply.isdigit() else ordinal_map.get(reply, -1)
        if 0 <= index < len(self.service_suggestions):
            return self.service_suggestions[index][0]
        return None

    def resolve_service_reply(self, reply: str) -> str | None:
        reply_stripped = reply.strip()
        if not reply_stripped:
            return None

        selected_service = self.get_selected_service(reply_stripped)
        if selected_service:
            return selected_service

        reply_lower = reply_stripped.lower()
        cleaned_reply = _strip_reply_prefixes(reply_lower)
        for service_name, _ in self.service_suggestions:
            service_lower = service_name.lower()
            if cleaned_reply == service_lower or cleaned_reply in service_lower or service_lower in cleaned_reply:
                return service_name

        return _llm_resolve_service_reply(
            original_service=str(self.extracted_data.get("Service", "") or ""),
            user_reply=reply_stripped,
            suggestions=self.service_suggestions,
        )


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
        "maybe ",
        "probably ",
        "the service is ",
        "the first one is ",
        "the second one is ",
    ]
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    return cleaned


def _llm_resolve_service_reply(
    *,
    original_service: str,
    user_reply: str,
    suggestions: list[tuple[str, float]],
) -> str | None:
    if not suggestions:
        return None
    try:
        from controlplane.control.bot.salesbot.dependencies import get_llm_interface

        llm = get_llm_interface()
    except Exception:
        return None

    suggestion_names = [name for name, _ in suggestions]
    prompt = (
        "You are helping a hotel sales assistant understand a user's correction reply. "
        "The user may be informal, low-literacy, use shorthand, broken English, or partial names. "
        "Pick the single best matching service suggestion only if the user's reply clearly points to one of them. "
        "Understand replies like `hammam`, `first option`, `1`, `I meant massage`, or short forms. "
        'Return ONLY JSON: {"match": "<one exact suggestion or empty>", "confidence": "high|medium|low"}.\n\n'
        f'Original extracted service: "{original_service}"\n'
        f'User reply: "{user_reply}"\n'
        f"Suggestions: {suggestion_names}\n"
    )

    try:
        response = llm.generate(prompt)
    except Exception:
        return None

    try:
        import json

        data = json.loads(response.strip())
    except Exception:
        return None

    match = str(data.get("match") or "").strip()
    confidence = str(data.get("confidence") or "").strip().lower()
    if not match or confidence == "low":
        return None

    for suggestion_name in suggestion_names:
        if suggestion_name.lower() == match.lower():
            return suggestion_name
    return None


class CorrectionTracker:
    """Thread-safe tracker for pending corrections.

    Maintains a mapping of per-user conversation thread -> PendingCorrection so that when a user
    replies to a correction request, we can match it to the original extraction.
    """

    _instance: CorrectionTracker | None = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        # Only initialize once (singleton pattern)
        if not hasattr(self, "_initialized"):
            self._pending: dict[str, PendingCorrection] = {}
            self._data_lock = threading.Lock()
            self._initialized = True

    def __new__(cls) -> CorrectionTracker:
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def add_pending(
        self,
        chat_id: str,
        sender_id: str | None,
        sender_name: str | None,
        original_message: str,
        extracted_data: dict[str, Any],
        validation_failures: list[str],
        service_suggestions: list[tuple[str, float]] | None = None,
        original_message_id: str | None = None,
        missing_fields: list[str] | None = None,
    ) -> PendingCorrection:
        """Add a new pending correction request."""
        with self._data_lock:
            key = self._thread_key(chat_id, sender_id)
            # Check if there's an existing pending correction for this chat
            existing = self._pending.get(key)
            if existing and not existing.is_expired():
                # Increment attempt count for retry
                existing.attempt_count += 1
                existing.validation_failures = validation_failures
                existing.extracted_data = extracted_data
                if service_suggestions is not None:
                    existing.service_suggestions = service_suggestions
                if original_message_id is not None:
                    existing.original_message_id = original_message_id
                if missing_fields is not None:
                    existing.missing_fields = missing_fields
                logger.info(
                    "Updated pending correction for chat_id=%s attempt=%d",
                    chat_id,
                    existing.attempt_count,
                )
                return existing

            correction = PendingCorrection(
                chat_id=chat_id,
                sender_id=sender_id,
                sender_name=sender_name,
                original_message=original_message,
                extracted_data=extracted_data,
                validation_failures=validation_failures,
                service_suggestions=service_suggestions or [],
                original_message_id=original_message_id,
                missing_fields=missing_fields or [],
            )
            self._pending[key] = correction
            logger.info("Added pending correction for chat_id=%s", chat_id)
            return correction

    def get_pending(self, chat_id: str, sender_id: str | None = None) -> PendingCorrection | None:
        """Get pending correction for a chat, if any."""
        with self._data_lock:
            key = self._thread_key(chat_id, sender_id)
            correction = self._pending.get(key)
            if correction and correction.is_expired():
                del self._pending[key]
                logger.debug("Expired pending correction for chat_id=%s", chat_id)
                return None
            return correction

    def remove_pending(self, chat_id: str, sender_id: str | None = None) -> bool:
        """Remove a pending correction (after successful processing)."""
        with self._data_lock:
            key = self._thread_key(chat_id, sender_id)
            if key in self._pending:
                del self._pending[key]
                logger.info("Removed pending correction for chat_id=%s", chat_id)
                return True
            return False

    def get_and_remove_expired(self) -> list[PendingCorrection]:
        """Get all expired corrections and remove them. Returns list for escalation."""
        with self._data_lock:
            expired_corrections = [correction for correction in self._pending.values() if correction.is_expired()]
            for correction in expired_corrections:
                del self._pending[correction.chat_id]
            if expired_corrections:
                logger.info("Found %d expired corrections for escalation", len(expired_corrections))
            return expired_corrections

    def cleanup_expired(self) -> int:
        """Remove all expired corrections. Returns count removed."""
        expired = self.get_and_remove_expired()
        return len(expired)

    @staticmethod
    def _thread_key(chat_id: str, sender_id: str | None) -> str:
        return f"{chat_id}:{sender_id or 'unknown'}"


def get_correction_tracker() -> CorrectionTracker:
    """Get the singleton CorrectionTracker instance."""
    return CorrectionTracker()


def build_correction_prompt(validation_failures: list[str], extracted_data: dict[str, Any]) -> str:
    """Build a user-friendly message asking for corrections."""
    lines = ["⚠️ *Some information seems incomplete or incorrect:*\n"]

    for failure in validation_failures:
        lines.append(f"• {failure}")

    lines.append("\n*Please reply with the corrected information.*")
    lines.append("For example:")
    lines.append("```")

    # Show what fields need correction based on failures
    example_fields = []
    for failure in validation_failures:
        failure_lower = failure.lower()
        if "service" in failure_lower:
            example_fields.append("Service: Soda")
        elif "quantity" in failure_lower:
            example_fields.append("Quantity: 2")
        elif "date" in failure_lower:
            example_fields.append("Date: 01/04/2026")
        elif "guest" in failure_lower:
            example_fields.append("Guest: 1px")
        elif "time" in failure_lower:
            example_fields.append("Time: 21:25")
        elif "room" in failure_lower:
            example_fields.append("Room: Chefchaouen")

    if example_fields:
        lines.extend(example_fields)
        lines.append("")
        lines.append("Riad Roxanne")
    else:
        # Show full example format
        lines.append("Service: Soda")
        lines.append("Quantity: 2")
        lines.append("Date: 01/04/2026")
        lines.append("Guest: 1px")
        lines.append("Time: 21:25")
        lines.append("Room: Chefchaouen")
        lines.append("")
        lines.append("Riad Roxanne")

    lines.append("```")

    return "\n".join(lines)


def build_service_suggestion_prompt(
    service_name: str,
    suggestions: list[tuple[str, float]],
) -> str:
    """Build a friendly message asking what the user meant."""
    lines = [
        f"Hey, I couldn't fully understand the service *{service_name}*.\n",
        "What did you mean by this?\n",
        "Possible suggestions are:\n",
    ]

    for i, (name, score) in enumerate(suggestions, 1):
        pct = int(score * 100)
        lines.append(f"{i}. {name} ({pct}% match)")

    lines.append(
        "\nYou can reply naturally, for example: `I meant hammam`, `hammam`, `first option`, or `1`."
    )

    return "\n".join(lines)


def build_service_confirmation_message(service_name: str) -> str:
    """Build a message to confirm the selected service."""
    return (
        f"✅ *You selected: {service_name}*\n\n*Is this correct?*\nReply *'yes'* to confirm or *'no'* to choose again."
    )


def build_invalid_selection_message(
    reply: str,
    suggestions: list[tuple[str, float]],
) -> str:
    """Build a message when the reply still cannot be understood."""
    lines = [
        f"I still couldn't understand what you meant by `{reply}`.\n",
        "These were the closest options I found:",
    ]

    for i, (name, _) in enumerate(suggestions, 1):
        lines.append(f"{i}. {name}")

    return "\n".join(lines)


def build_final_escalation_message() -> str:
    """Build a user-facing message when SalesBot could not recover the sale."""
    return "I could not record your sale. Please contact *Omar* to add the details."


def build_entry_recorded_message() -> str:
    return "Thank you, your entry has been recorded."


def build_timeout_escalation_message(
    original_message: str,
    validation_failures: list[str],
    sender_name: str | None,
) -> str:
    """Build an escalation message when user didn't reply within 24 hours."""
    lines = [
        "🚨 *SalesBot Escalation - No Response (24h Timeout)*\n",
        f"*User:* {sender_name or 'Unknown'}",
        "\n*Validation Issues:*",
    ]

    for failure in validation_failures:
        lines.append(f"• {failure}")

    lines.append("\n*Original Message:*")
    lines.append(f"```\n{original_message[:500]}\n```")
    lines.append("\n_User did not respond to correction request within 24 hours._")

    return "\n".join(lines)


def build_service_not_found_escalation(
    service_name: str,
    original_message: str,
    sender_name: str | None,
) -> str:
    """Build an escalation message when service is not found in pricelist."""
    lines = [
        "🚨 *SalesBot Escalation - Unknown Service*\n",
        f"*Service:* `{service_name}`",
        f"*User:* {sender_name or 'Unknown'}",
        "\n*Original Message:*",
        f"```\n{original_message[:500]}\n```",
        "\n_This service does not exist in the price list. Please add it or manually process._",
    ]

    return "\n".join(lines)


def build_escalation_message(
    original_message: str,
    validation_failures: list[str],
    sender_name: str | None,
) -> str:
    """Build an escalation message for the alert number."""
    lines = [
        "🚨 *SalesBot Escalation - Repeated Validation Failure*\n",
        f"*User:* {sender_name or 'Unknown'}",
        "\n*Validation Issues:*",
    ]

    for failure in validation_failures:
        lines.append(f"• {failure}")

    lines.append("\n*Original Message:*")
    lines.append(f"```\n{original_message[:500]}\n```")

    lines.append("\n_Please review and manually process this entry._")

    return "\n".join(lines)
