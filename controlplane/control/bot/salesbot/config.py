from __future__ import annotations

import os

# Supports both ESCALATION_CHAT_ID (single) and ESCALATION_CHAT_IDS (comma-separated)
_escalation_ids = os.getenv("ESCALATION_CHAT_IDS", "").strip()
if not _escalation_ids:
    _escalation_ids = os.getenv("ESCALATION_CHAT_ID", "").strip()

ESCALATION_CHAT_IDS = [x.strip() for x in _escalation_ids.split(",") if x.strip()]

DEFAULT_PROMPT = (
    "Known operating context from prior corrections and shared learnings:\n"
    "__MEMORY_CONTEXT__\n\n"
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

CORRECTION_TASK_TYPE = "salesbot_correction_pending"
