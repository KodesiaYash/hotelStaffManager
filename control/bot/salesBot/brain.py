from __future__ import annotations

import os
import sys
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from boundary.llminterface import GeminiInterface  # noqa: E402
from boundary.storageInterface.salesAudit import SalesAudit  # noqa: E402
from shared.utils import safe_json_parse  # noqa: E402

DEFAULT_PROMPT = (
    "Analyze this WhatsApp message for a sales lead. The service name may be mentioned "
    "along with a number, use your best intelligence to judge if it is the quantity "
    'of the service sold. If yes, populate the "Quantity" field, otherwise default to 1. '
    "Respond ONLY with valid JSON in the following format (no extra text, no explanations, "
    "no unnecessary special characters): "
    '{{"Service": "task or \'\'", "Quantity": "number or \'1\'", "Date": "number or \'\'", '
    '"Time": "number or \'\'", "Guest": "name or \'\'", "Room": "name or \'\'", '
    '"Asignee": "name or \'\'", "Amount": number or 0, '
    '"confidence": "high/medium/low"}} '
    "Message: {message}"
)


def _load_env_files() -> None:
    load_dotenv()
    env_path = os.path.join(PROJECT_ROOT, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()

_sales_audit: SalesAudit | None = None
_llm_interface: GeminiInterface | None = None


def _get_sales_audit() -> SalesAudit:
    global _sales_audit
    if _sales_audit is None:
        _sales_audit = SalesAudit()
    return _sales_audit


def _get_llm_interface() -> GeminiInterface:
    global _llm_interface
    if _llm_interface is None:
        _llm_interface = GeminiInterface()
    return _llm_interface


def llm_extract(message: str) -> dict[str, Any]:
    """LLM extracts structured data from message using Gemini."""
    prompt = DEFAULT_PROMPT.format(message=message)

    response_text = _get_llm_interface().generate(prompt)
    extracted = safe_json_parse(response_text)
    if extracted.get("error") == "parse_failed":
        prompt = f"{prompt}\nCRITICAL: Pure JSON, no extra text."
        response_text = _get_llm_interface().generate(prompt)
        extracted = safe_json_parse(response_text)
    return extracted


def process_message(message: str) -> None:
    extracted = llm_extract(message)
    if "error" in extracted:
        print("Extraction failed:", extracted)
        return

    try:
        cost = _get_sales_audit().write_details_sheet(
            [
                extracted.get("Service", ""),
                extracted.get("Quantity", ""),
                extracted.get("Date", ""),
                extracted.get("Time", ""),
                extracted.get("Guest", ""),
                extracted.get("Room", ""),
                extracted.get("Asignee", ""),
            ]
        )
    except Exception as exc:
        print(f"Write failed: {exc}")
        return
    print(f"Logged: {extracted} | Cost: Rs{cost}")


if __name__ == "__main__":
    test_msg2 = "Service: 2 Hammame\nDate : 04/03/2026 \nGuest:2px \nTime:6:00pm \nRoom:The Sahara Room \nArjun Rampal"
    process_message(test_msg2)
