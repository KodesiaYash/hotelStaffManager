from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from dotenv import load_dotenv
from google import genai

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from boundary.storageInterface.salesAudit import SalesAudit  # noqa: E402


def _load_env_files() -> None:
    load_dotenv()
    env_path = os.path.join(PROJECT_ROOT, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()

_sales_audit: SalesAudit | None = None


def _get_sales_audit() -> SalesAudit:
    global _sales_audit
    if _sales_audit is None:
        _sales_audit = SalesAudit()
    return _sales_audit


_genai_client: genai.Client | None = None


def _get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        _genai_client = genai.Client(api_key=api_key)
    return _genai_client


def safe_json_parse(text: str | None) -> dict[str, Any]:
    """Clean + parse Gemini JSON responses."""
    if not text:
        return {"error": "empty_response"}

    cleaned = text.strip()
    cleaned = re.sub(r"```json\s*|\s*```", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"([\{\}\[\]])", r" \1 ", cleaned)
    cleaned = cleaned.replace("\\n", "").replace("\\t", "")

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        print(f"JSON Error: {exc}")
        print(f"Raw (first 100): {text[:100]!r}")
        print(f"Cleaned: {cleaned[:100]!r}")
        return {"error": "parse_failed", "raw": cleaned}


def llm_extract(message: str) -> dict[str, Any]:
    """LLM extracts structured data from message using Gemini."""
    prompt = (
        "Analyze this WhatsApp message for a sales lead. The service name may be mentioned "
        "along with a number, use your best intelligence to judge if it is the quantity "
        'of the service sold. If yes, populate the "Quantity" field, otherwise default to 1. '
        "Respond ONLY with valid JSON in the following format (no extra text, no explanations, "
        "no unnecessary special characters): "
        '{"Service": "task or \'\'", "Quantity": "number or \'1\'", "Date": "number or \'\'", '
        '"Time": "number or \'\'", "Guest": "name or \'\'", "Room": "name or \'\'", '
        '"Asignee": "name or \'\'", "Amount": number or 0, '
        '"confidence": "high/medium/low"} '
        f"Message: {message}"
    )

    client = _get_genai_client()
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents={"text": prompt},
        config={
            "temperature": 0,
            "response_mime_type": "application/json",
        },
    )
    try:
        extracted = safe_json_parse(response.text)
        client.close()
        return extracted
    except json.JSONDecodeError:
        prompt = f"{prompt}\nCRITICAL: Pure JSON, no extra text."
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents={"text": prompt},
            config={
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        )
        client.close()
        return {"error": "Parse failed", "raw": response}


def update_costs(service: str, quantity: Any) -> float:
    """Read costs sheet, find matching cost, log."""
    try:
        return _get_sales_audit().calculate_cost(service, quantity)
    except Exception as exc:
        print(f"Cost lookup failed: {exc}")
        return 0.0


def process_message(message: str) -> None:
    extracted = llm_extract(message)
    if "error" in extracted:
        print("Extraction failed:", extracted)
        return

    cost = update_costs(str(extracted.get("Service", "")), extracted.get("Quantity", 1))

    try:
        _get_sales_audit().write_details_sheet(
            [
                extracted.get("Service", ""),
                extracted.get("Quantity", ""),
                extracted.get("Date", ""),
                extracted.get("Time", ""),
                extracted.get("Guest", ""),
                extracted.get("Room", ""),
                extracted.get("Asignee", ""),
                cost,
            ]
        )
    except Exception as exc:
        print(f"Write failed: {exc}")
        return
    print(f"Logged: {extracted} | Cost: Rs{cost}")


if __name__ == "__main__":
    test_msg2 = "Service: 2 Hammame\nDate : 04/03/2026 \nGuest:2px \nTime:6:00pm \nRoom:The Sahara Room \nArjun Rampal"
    process_message(test_msg2)
