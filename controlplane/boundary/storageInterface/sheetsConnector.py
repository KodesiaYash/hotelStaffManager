from __future__ import annotations

import logging
import os
import time
from collections.abc import Sequence
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

DEFAULT_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _resolve_path(path: str, base_dir: str | None = None) -> tuple[str | None, list[str]]:
    expanded = os.path.expanduser(os.path.expandvars(path))
    tried: list[str] = []

    if os.path.isabs(expanded):
        tried.append(expanded)
        return (expanded if os.path.exists(expanded) else None), tried

    candidates: list[str] = []
    if base_dir:
        candidates.append(os.path.abspath(os.path.join(base_dir, expanded)))

    candidates.append(os.path.abspath(expanded))

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    candidates.append(os.path.abspath(os.path.join(project_root, expanded)))

    for candidate in candidates:
        tried.append(candidate)
        if os.path.exists(candidate):
            return candidate, tried

    return None, tried


def normalize_env_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ('"', "'"):
            cleaned = cleaned[1:-1].strip()
        return cleaned or None
    return str(value)


class SheetsConnector:
    def __init__(self, config: dict[str, Any]):
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.scopes: list[str] = list(config.get("scopes") or DEFAULT_SCOPES)
        service_account_file = normalize_env_value(
            config.get("service_account_file") or config.get("service_account_path") or config.get("credentials_file")
        )
        if not service_account_file:
            raise ValueError("Missing service_account_file in config")
        resolved_path, tried = _resolve_path(
            service_account_file,
            base_dir=normalize_env_value(config.get("base_dir") or config.get("project_root")),
        )
        if not resolved_path:
            tried_str = ", ".join(tried)
            raise FileNotFoundError(f"Service account file not found: {service_account_file}. Tried: {tried_str}")

        creds = Credentials.from_service_account_file(resolved_path, scopes=self.scopes)
        self.client = gspread.authorize(creds)

        sheets = config.get("sheets")
        if not sheets:
            sheet_id = normalize_env_value(config.get("sheet_id") or config.get("spreadsheet_id"))
            if sheet_id:
                sheets = {
                    "default": {
                        "spreadsheet_id": sheet_id,
                        "worksheet": config.get("worksheet"),
                        "worksheet_index": config.get("worksheet_index"),
                    }
                }
            else:
                raise ValueError("No sheets configured in config")

        self.sheets: dict[str, dict[str, Any]] = sheets
        self._worksheet_cache: dict[str, Any] = {}

    def get_worksheet(self, name: str = "default") -> Any:
        if name in self._worksheet_cache:
            return self._worksheet_cache[name]
        if name not in self.sheets:
            raise KeyError(f"Unknown sheet key: {name}")

        sheet_cfg = self.sheets[name]
        sheet_id = normalize_env_value(sheet_cfg.get("spreadsheet_id") or sheet_cfg.get("sheet_id"))
        if not sheet_id:
            raise ValueError(f"Missing spreadsheet_id for sheet '{name}'")

        spreadsheet = self.client.open_by_key(sheet_id)
        worksheet_name = sheet_cfg.get("worksheet")
        worksheet_index = sheet_cfg.get("worksheet_index")

        if worksheet_name:
            worksheet = spreadsheet.worksheet(worksheet_name)
        elif worksheet_index is not None:
            worksheet = spreadsheet.get_worksheet(int(worksheet_index))
        else:
            worksheet = spreadsheet.sheet1

        self._worksheet_cache[name] = worksheet
        return worksheet

    def read_all_records(self, name: str = "default") -> list[dict[str, Any]]:
        worksheet = self.get_worksheet(name)
        return worksheet.get_all_records()

    def read_all_values(self, name: str = "default") -> list[list[Any]]:
        worksheet = self.get_worksheet(name)
        return worksheet.get_all_values()

    def append_row(
        self, name: str, row_values: Sequence[Any], max_retries: int = 5
    ) -> None:
        worksheet = self.get_worksheet(name)
        for attempt in range(max_retries):
            try:
                worksheet.append_row(list(row_values))
                return
            except gspread.exceptions.APIError as e:
                if e.response.status_code == 429:  # Rate limit exceeded
                    wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4, 8, 16 seconds
                    logger.warning(
                        "Sheets rate limit hit, retrying in %ds (attempt %d/%d)",
                        wait_time,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(wait_time)
                else:
                    raise
        # Final attempt without catching
        worksheet.append_row(list(row_values))

    def update_cells(
        self, name: str, cell_range: str, values: Sequence[Sequence[Any]], max_retries: int = 5
    ) -> None:
        worksheet = self.get_worksheet(name)
        for attempt in range(max_retries):
            try:
                worksheet.update(cell_range, list(values))
                return
            except gspread.exceptions.APIError as e:
                if e.response.status_code == 429:  # Rate limit exceeded
                    wait_time = 2 ** attempt
                    logger.warning(
                        "Sheets rate limit hit, retrying in %ds (attempt %d/%d)",
                        wait_time,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(wait_time)
                else:
                    raise
        # Final attempt without catching
        worksheet.update(cell_range, list(values))
