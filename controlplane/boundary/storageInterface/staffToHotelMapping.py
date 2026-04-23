from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from dotenv import load_dotenv

from controlplane.boundary.storageInterface.sheetsConnector import (
    DEFAULT_SCOPES,
    SheetsConnector,
    normalize_env_value,
)

MAPPING_KEY = "staff_mapping"


def _load_env_files() -> None:
    load_dotenv()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    env_path = os.path.join(project_root, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()


def build_staff_mapping_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = env or os.environ
    sheet_id = normalize_env_value(env.get("STAFF_MAPPING_SHEET_ID") or env.get("STAFF_TO_HOTEL_SHEET_ID"))
    return {
        "service_account_file": normalize_env_value(env.get("GOOGLE_SHEETS_KEY")),
        "scopes": DEFAULT_SCOPES,
        "sheets": {
            MAPPING_KEY: {
                "spreadsheet_id": sheet_id,
                "worksheet": normalize_env_value(env.get("STAFF_MAPPING_WORKSHEET")),
            }
        },
    }


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    # Remove all non-digit characters (spaces, dashes, plus signs, etc.)
    digits = re.sub(r"\D", "", value)
    return digits


def _phones_match(candidate: str, target: str) -> bool:
    if not candidate or not target:
        return False
    return candidate == target or candidate.endswith(target) or target.endswith(candidate)


def _get_case_insensitive(row: dict[str, Any], keys: list[str]) -> Any | None:
    lookup = {str(k).strip().lower(): k for k in row}
    for key in keys:
        normalized = str(key).strip().lower()
        if normalized in lookup:
            return row.get(lookup[normalized])
    return None


def _parse_percentage(value: Any) -> float:
    """Parse a percentage value, returning 0.0 if invalid."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


class StaffToHotelMapping:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        sheet_key: str = MAPPING_KEY,
    ) -> None:
        self.config = config or build_staff_mapping_config()
        self.connector = SheetsConnector(self.config)
        self.sheet_key = sheet_key

    def read_mapping(self) -> list[dict[str, Any]]:
        return self.connector.read_all_records(self.sheet_key)

    def get_all_staff_with_commission(self) -> list[dict[str, Any]]:
        """Get all staff members with their commission percentage.

        Returns list of dicts with keys: name, phone, commission_percentage
        """
        results: list[dict[str, Any]] = []
        for row in self.read_mapping():
            name = _get_case_insensitive(row, ["name", "staff", "staff_name", "employee"])
            phone = _get_case_insensitive(row, ["phone", "phone_number", "mobile", "number", "whatsapp", "contact"])
            commission_pct = _get_case_insensitive(
                row,
                [
                    "commission_percentage",
                    "Commission Percentage",
                    "commission",
                    "commission_pct",
                    "comm_%",
                ],
            )
            if name:
                results.append(
                    {
                        "name": str(name).strip(),
                        "phone": _normalize_phone(str(phone) if phone else ""),
                        "commission_percentage": _parse_percentage(commission_pct),
                    }
                )
        return results

    def find_by_phone(self, phone: str) -> list[dict[str, Any]]:
        normalized = _normalize_phone(phone)
        if not normalized:
            return []
        matches: list[dict[str, Any]] = []
        for row in self.read_mapping():
            row_phone = _get_case_insensitive(
                row,
                ["phone", "phone_number", "mobile", "number", "whatsapp", "contact"],
            )
            row_norm = _normalize_phone(str(row_phone) if row_phone is not None else "")
            if _phones_match(row_norm, normalized):
                matches.append(row)
        return matches

    def find_by_username(self, username: str) -> list[dict[str, Any]]:
        """Find staff by username (e.g., Telegram username)."""
        if not username:
            return []
        # Normalize: remove @ prefix if present, lowercase
        normalized = username.lstrip("@").strip().lower()
        if not normalized:
            return []
        matches: list[dict[str, Any]] = []
        for row in self.read_mapping():
            row_username = _get_case_insensitive(
                row,
                ["username", "telegram_username", "telegram", "tg_username", "tg"],
            )
            if row_username:
                row_norm = str(row_username).lstrip("@").strip().lower()
                if row_norm == normalized:
                    matches.append(row)
        return matches


def normalize_phone(value: str | None) -> str:
    return _normalize_phone(value)
