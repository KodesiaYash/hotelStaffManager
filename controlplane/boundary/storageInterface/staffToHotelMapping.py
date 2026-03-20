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
    digits = re.sub(r"\\D", "", value)
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


def normalize_phone(value: str | None) -> str:
    return _normalize_phone(value)
