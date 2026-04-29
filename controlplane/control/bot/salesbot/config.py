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
    "IMPORTANT: Extract the 'Service' field EXACTLY as written in the message. "
    "Do NOT expand, complete, or add location/variant qualifiers (e.g. 'Agafay', 'Palmeraie', pack numbers) "
    "that are not explicitly in the message, even if past corrections suggest a specific match. "
    "Service disambiguation is handled separately after extraction.\n\n"
    "HOTEL NAME RULES (CRITICAL — follow exactly):\n"
    "1. The ONLY two valid hotels are 'RIAD Roxanne' and 'RIAD Persephone'. "
    "Always output them in this exact casing.\n"
    "2. Any mention of 'RIAD', 'Roxanne', 'Roksane', 'Roksanne', 'Rioxane' or any "
    "phonetic/spelling variant of Roxanne → always set HotelName='RIAD Roxanne'.\n"
    "3. Any mention of 'Persephone', 'Persephon', 'Persefone', 'Persefon', 'Persphone', "
    "'Persephone', 'Persephona', or any phonetic/spelling variant of Persephone → "
    "always set HotelName='RIAD Persephone'.\n"
    "4. RIAD Roxanne and RIAD Persephone are HOTEL NAMES, never room names. "
    "NEVER put a hotel name into the 'Room' field. "
    "If the message contains a hotel name with no separate room label, "
    "set HotelName to the correct hotel and leave Room=''.\n"
    "5. Room is a specific room identifier (e.g. 'Lily', 'Fez', 'Rose', 'Suite 1'). "
    "It is NEVER a hotel name. If you cannot identify a room name, set Room=''.\n"
    "6. If the hotel name is absent from the message, set HotelName=''.\n\n"
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
    '24 hour format) or \'\'", "Guest": "number (only mention the number) or \'\'", "Room": "specific room name or \'\'", '
    '"Asignee": "name or \'\'", "HotelName": "RIAD Roxanne or RIAD Persephone or \'\'", '
    '"Amount": number or 0, '
    '"confidence": "high/medium/low", '
    '"message_type": "sales or non_sales"}'
    "] "
    "MESSAGE TYPE RULES: "
    "Set message_type='sales' if the message has a booking-like structure with key:value lines for "
    "any of: service/activity name, date, time, room, guest count, hotel name. "
    "Missing fields are fine — a message IS a sales booking if it has even 2-3 of these. "
    "Set message_type='non_sales' ONLY for messages with absolutely no booking structure "
    "(e.g. greetings, random chitchat, follow-up replies like 'ok thanks'). "
    "SANITY CHECKS - set confidence to low if any of these fail: "
    "1. Quantity <= 0 "
    "2. Service is empty or unclear "
    "3. Date format is invalid or missing "
    "4. Time format is invalid or missing "
    "5. Room is empty or missing "
    "6. HotelName is empty or missing "
    "Message: __MESSAGE__"
)

KNOWN_HOTELS = {
    "riad roxanne": "RIAD Roxanne",
    "riad persephone": "RIAD Persephone",
}

CORRECTION_TASK_TYPE = "salesbot_correction_pending"
