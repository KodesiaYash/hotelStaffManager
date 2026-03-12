from __future__ import annotations

import json
import re
from typing import Any


def safe_json_parse(text: str | None) -> dict[str, Any]:
    """Clean + parse JSON responses from LLMs."""
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
