from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from dotenv import load_dotenv

from controlplane.boundary.storageInterface.sheetsConnector import (
    DEFAULT_SCOPES,
    SheetsConnector,
    normalize_env_value,
)

PRICELIST_KEY = "pricelist"


def _load_env_files() -> None:
    load_dotenv()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    env_path = os.path.join(project_root, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()


def build_pricelist_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = env or os.environ
    return {
        "service_account_file": normalize_env_value(env.get("GOOGLE_SHEETS_KEY")),
        "scopes": DEFAULT_SCOPES,
        "sheets": {
            PRICELIST_KEY: {
                "spreadsheet_id": normalize_env_value(
                    env.get("SALES_PRICELIST_SHEET_ID") or env.get("PRICELIST_SHEET_ID")
                ),
                "worksheet": normalize_env_value(env.get("PRICELIST_WORKSHEET")),
            }
        },
    }


class PriceList:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        sheet_key: str = PRICELIST_KEY,
    ) -> None:
        self.config = config or build_pricelist_config()
        self.connector = SheetsConnector(self.config)
        self.sheet_key = sheet_key

    def read_pricelist(self) -> list[dict[str, Any]]:
        return self.connector.read_all_records(self.sheet_key)

    def read_pricelist_values(self) -> list[list[Any]]:
        return self.connector.read_all_values(self.sheet_key)

    def write_pricelist(self, data: Sequence[Any]) -> None:
        self.connector.append_row(self.sheet_key, list(data))

    def update_pricelist(self, cell_range: str, values: Sequence[Sequence[Any]]) -> None:
        self.connector.update_cells(self.sheet_key, cell_range, list(values))


_default_pricelist: PriceList | None = None


def _get_default_pricelist() -> PriceList:
    global _default_pricelist
    if _default_pricelist is None:
        _default_pricelist = PriceList()
    return _default_pricelist


def read_pricelist() -> list[dict[str, Any]]:
    return _get_default_pricelist().read_pricelist()


def read_pricelist_values() -> list[list[Any]]:
    return _get_default_pricelist().read_pricelist_values()


def write_pricelist(data: Sequence[Any]) -> None:
    _get_default_pricelist().write_pricelist(data)


def update_pricelist(cell_range: str, values: Sequence[Sequence[Any]]) -> None:
    _get_default_pricelist().update_pricelist(cell_range, values)


if __name__ == "__main__":
    pricelist = PriceList()
    rows = pricelist.read_pricelist()
    print(f"Price list records: {len(rows)}")
