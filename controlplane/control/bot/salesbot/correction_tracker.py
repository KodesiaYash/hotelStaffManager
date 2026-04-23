"""Track pending corrections for SalesBot validation failures.

This module maintains a mapping of chat_id -> pending correction requests,
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

    def is_expired(self) -> bool:
        return time.time() - self.created_at > CORRECTION_EXPIRY_SECONDS

    def should_escalate(self) -> bool:
        return self.attempt_count >= MAX_CORRECTION_ATTEMPTS

    def get_selected_service(self, reply: str) -> str | None:
        """Parse user reply to get selected service.

        If reply is a number 1-5, return the corresponding suggestion.
        Otherwise return None (reply should be treated as a service name).
        """
        reply = reply.strip()
        if not reply.isdigit():
            return None

        index = int(reply) - 1  # Convert to 0-indexed
        if 0 <= index < len(self.service_suggestions):
            return self.service_suggestions[index][0]

        # Invalid number (e.g., "6" when only 5 suggestions)
        return None


class CorrectionTracker:
    """Thread-safe tracker for pending corrections.

    Maintains a mapping of chat_id -> PendingCorrection so that when a user
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
    ) -> PendingCorrection:
        """Add a new pending correction request."""
        with self._data_lock:
            # Check if there's an existing pending correction for this chat
            existing = self._pending.get(chat_id)
            if existing and not existing.is_expired():
                # Increment attempt count for retry
                existing.attempt_count += 1
                existing.validation_failures = validation_failures
                existing.extracted_data = extracted_data
                if service_suggestions is not None:
                    existing.service_suggestions = service_suggestions
                if original_message_id is not None:
                    existing.original_message_id = original_message_id
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
            )
            self._pending[chat_id] = correction
            logger.info("Added pending correction for chat_id=%s", chat_id)
            return correction

    def get_pending(self, chat_id: str) -> PendingCorrection | None:
        """Get pending correction for a chat, if any."""
        with self._data_lock:
            correction = self._pending.get(chat_id)
            if correction and correction.is_expired():
                del self._pending[chat_id]
                logger.debug("Expired pending correction for chat_id=%s", chat_id)
                return None
            return correction

    def remove_pending(self, chat_id: str) -> bool:
        """Remove a pending correction (after successful processing)."""
        with self._data_lock:
            if chat_id in self._pending:
                del self._pending[chat_id]
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
    """Build a message asking user to select from suggested services."""
    lines = [
        f"⚠️ *Service '{service_name}' not found in price list.*\n",
        "*Did you mean one of these?*\n",
    ]

    for i, (name, score) in enumerate(suggestions, 1):
        # Show percentage match
        pct = int(score * 100)
        lines.append(f"{i}. {name} ({pct}% match)")

    max_num = len(suggestions)
    lines.append(f"\n*Please reply with the correct service name or number (1-{max_num}).*")

    return "\n".join(lines)


def build_invalid_selection_message(
    reply: str,
    suggestions: list[tuple[str, float]],
) -> str:
    """Build a message when user selects an invalid option."""
    max_num = len(suggestions)
    lines = [
        f"❌ *'{reply}' is not a valid selection.*\n",
        f"*Please reply with a number from 1-{max_num}, or type the correct service name.*\n",
        "*Available options:*",
    ]

    for i, (name, _) in enumerate(suggestions, 1):
        lines.append(f"{i}. {name}")

    return "\n".join(lines)


def build_final_escalation_message() -> str:
    """Build a message when user has failed too many times - tell them to contact Omar."""
    return "❌ *Information is incorrect.*\n\nAdmin has been alerted. Please contact *Omar* for more details."


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
