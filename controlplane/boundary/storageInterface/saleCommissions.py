from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

from dotenv import load_dotenv

from controlplane.boundary.storageInterface.sheetsConnector import (
    DEFAULT_SCOPES,
    SheetsConnector,
    normalize_env_value,
)

COMMISSIONS_KEY = "commissions"

logger = logging.getLogger(__name__)


def _load_env_files() -> None:
    load_dotenv()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    env_path = os.path.join(project_root, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()


def build_commissions_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = env or os.environ
    sheet_id = normalize_env_value(env.get("SALE_COMMISSIONS_SHEET_ID") or env.get("SALES_AUDIT_SHEET_ID"))
    return {
        "service_account_file": normalize_env_value(env.get("GOOGLE_SHEETS_KEY")),
        "scopes": DEFAULT_SCOPES,
        "sheets": {
            COMMISSIONS_KEY: {
                "spreadsheet_id": sheet_id,
                "worksheet": normalize_env_value(env.get("SALE_COMMISSIONS_WORKSHEET") or "Sale Commissions"),
            }
        },
    }


class SaleCommissions:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        sheet_key: str = COMMISSIONS_KEY,
    ) -> None:
        self.config = config or build_commissions_config()
        self.connector = SheetsConnector(self.config)
        self.sheet_key = sheet_key

    def read_commissions(self) -> list[dict[str, Any]]:
        return self.connector.read_all_records(self.sheet_key)

    def write_commission(self, data: Sequence[Any]) -> None:
        """Write a commission entry: [SaleId, Commission Value, Name, Phone]"""
        row = list(data)
        while len(row) < 4:
            row.append("")
        self.connector.append_row(self.sheet_key, row)
        logger.info(
            "Commission entry added: SaleId=%s, Value=%s, Name=%s",
            row[0],
            row[1],
            row[2],
        )

    def write_commissions_batch(self, entries: list[Sequence[Any]]) -> None:
        """Write multiple commission entries for a single sale."""
        for entry in entries:
            self.write_commission(entry)


_default_commissions: SaleCommissions | None = None


def _get_default_commissions() -> SaleCommissions:
    global _default_commissions
    if _default_commissions is None:
        _default_commissions = SaleCommissions()
    return _default_commissions


def read_commissions() -> list[dict[str, Any]]:
    return _get_default_commissions().read_commissions()


def write_commission(data: Sequence[Any]) -> None:
    _get_default_commissions().write_commission(data)


def write_commissions_batch(entries: list[Sequence[Any]]) -> None:
    _get_default_commissions().write_commissions_batch(entries)


if __name__ == "__main__":
    commissions = SaleCommissions()
    rows = commissions.read_commissions()
    print(f"Commission records: {len(rows)}")
