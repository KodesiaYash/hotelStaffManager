from __future__ import annotations

import csv
import os
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any, Protocol

from dotenv import load_dotenv

from controlplane.boundary.storageInterface.priceList import PriceList
from controlplane.boundary.storageInterface.sheetsConnector import (
    DEFAULT_SCOPES,
    SheetsConnector,
    normalize_env_value,
)

DETAILS_KEY = "details"


def _load_env_files() -> None:
    load_dotenv()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    env_path = os.path.join(project_root, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()


def build_sales_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = env or os.environ
    details_sheet_id = normalize_env_value(env.get("SALES_AUDIT_SHEET_ID") or env.get("DETAILS_SHEET_ID"))
    sheets: dict[str, dict[str, Any]] = {
        DETAILS_KEY: {
            "spreadsheet_id": details_sheet_id,
            "worksheet": normalize_env_value(env.get("DETAILS_WORKSHEET")),
        }
    }
    legacy_costs_id = normalize_env_value(env.get("COSTS_SHEET_ID"))
    if legacy_costs_id:
        sheets["costs"] = {
            "spreadsheet_id": legacy_costs_id,
            "worksheet": normalize_env_value(env.get("COSTS_WORKSHEET")),
        }
    return {
        "service_account_file": normalize_env_value(env.get("GOOGLE_SHEETS_KEY")),
        "scopes": DEFAULT_SCOPES,
        "sheets": sheets,
    }


class PriceListClient(Protocol):
    def read_pricelist(self) -> list[dict[str, Any]]: ...

    def write_pricelist(self, data: Sequence[Any]) -> None: ...


class LLMClient(Protocol):
    def generate(self, prompt: str) -> str: ...


class SalesAudit:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        details_key: str = DETAILS_KEY,
        pricelist: PriceListClient | None = None,
    ) -> None:
        self.config = config or build_sales_config()
        self.connector = SheetsConnector(self.config)
        self.details_key = details_key
        self.pricelist: PriceListClient | None = pricelist
        if self.pricelist is None:
            try:
                self.pricelist = PriceList()
            except Exception:
                self.pricelist = None

    def read_details_sheet(self) -> list[dict[str, Any]]:
        return self.connector.read_all_records(self.details_key)

    def write_details_sheet(self, data: Sequence[Any]) -> float:
        row = list(data)
        while len(row) < 7:
            row.append("")

        cost: float | None = None
        if len(row) >= 8:
            cost = _parse_number(row[7])

        if cost is None:
            service = str(row[0]) if row else ""
            quantity = row[1] if len(row) > 1 else 1
            cost = self.calculate_cost(service, quantity)

        if len(row) < 8:
            row.append(cost)
        else:
            row[7] = cost

        self.connector.append_row(self.details_key, row)
        return float(cost)

    def read_costs_sheet(self) -> list[dict[str, Any]]:
        if not self.pricelist:
            return []
        return self.pricelist.read_pricelist()

    def write_costs_sheet(self, data: Sequence[Any]) -> None:
        if not self.pricelist:
            raise RuntimeError("PriceList not configured")
        self.pricelist.write_pricelist(list(data))

    def calculate_cost(self, service: str, quantity: float = 1, llm: LLMClient | None = None) -> float:
        if not self.pricelist:
            return 0.0

        service_value = (service or "").strip().lower()
        if not service_value:
            return 0.0

        records = self.pricelist.read_pricelist()
        if not records:
            return 0.0

        direct_match = _find_pricelist_match(records, service_value)
        if direct_match is None and llm:
            matched_name = _llm_match_service(service_value, records, llm)
            if matched_name:
                direct_match = _find_pricelist_match(records, matched_name)

        if direct_match is None:
            return 0.0

        unit_cost = _parse_number(
            _get_case_insensitive(
                direct_match,
                ["cost", "price", "rate", "unit_cost", "unit price", "unitprice", "amount"],
            )
        )
        if unit_cost is None:
            return 0.0
        qty = _parse_number(quantity)
        if qty is None:
            qty = 1
        return float(unit_cost) * float(qty)

    def export_details_to_csv(self, filename: str = "details_export.csv") -> None:
        records = self.read_details_sheet()
        if not records:
            return
        with open(filename, encoding="utf-8", newline="") as csvfile:
            fieldnames = records[0].keys()
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)

    def import_details_from_csv(self, filename: str = "details_import.csv") -> None:
        with open(filename, encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                data = [
                    row.get("Service", ""),
                    row.get("Quantity", ""),
                    row.get("Date", ""),
                    row.get("Time", ""),
                    row.get("Guest", ""),
                    row.get("Room", ""),
                    row.get("Asignee", ""),
                    row.get("Cost", ""),
                ]
                self.write_details_sheet(data)


def _get_case_insensitive(row: dict[str, Any], keys: Sequence[str]) -> Any | None:
    lookup = {str(k).strip().lower(): k for k in row}
    for key in keys:
        normalized = str(key).strip().lower()
        if normalized in lookup:
            return row.get(lookup[normalized])
    return None


def _parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"[-+]?[0-9]*\.?[0-9]+", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _find_pricelist_match(records: list[dict[str, Any]], service_value: str) -> dict[str, Any] | None:
    for row in records:
        row_service = _get_case_insensitive(row, ["service", "item", "name"])
        if not row_service:
            continue
        row_service_value = str(row_service).strip().lower()
        if row_service_value in service_value.lower() or service_value.lower() in row_service_value:
            return row
    return None


def _llm_match_service(service_value: str, records: list[dict[str, Any]], llm: LLMClient) -> str | None:
    if os.getenv("SALES_BOT_LLM_MATCHING") != "1":
        return None
    candidates = [
        str(_get_case_insensitive(row, ["service", "item", "name"]))
        for row in records
        if _get_case_insensitive(row, ["service", "item", "name"])
    ]
    if not candidates:
        return None
    scored = sorted(
        candidates,
        key=lambda name: SequenceMatcher(None, service_value, str(name).lower()).ratio(),
        reverse=True,
    )
    top_candidates = scored[:20]
    prompt = (
        "You are matching a service name to a price list. "
        'Return ONLY JSON: {"match": "<exact candidate or empty>", "confidence": "high|medium|low"}.\n\n'
        f'Service to match: "{service_value}"\n'
        f"Candidates: {top_candidates}\n"
    )
    try:
        response = llm.generate(prompt)
    except Exception:
        return None
    try:
        import json

        data = json.loads(response.strip())
    except Exception:
        return None
    match = str(data.get("match") or "").strip()
    confidence = str(data.get("confidence") or "").strip().lower()
    if not match:
        return None
    candidate_lookup = {str(name).strip().lower(): str(name) for name in top_candidates}
    canonical = candidate_lookup.get(match.lower())
    if not canonical:
        return None
    if confidence == "low":
        return None
    return canonical


_default_audit: SalesAudit | None = None


def _get_default_audit() -> SalesAudit:
    global _default_audit
    if _default_audit is None:
        _default_audit = SalesAudit()
    return _default_audit


def read_details_sheet() -> list[dict[str, Any]]:
    return _get_default_audit().read_details_sheet()


def write_details_sheet(data: Sequence[Any]) -> float:
    return _get_default_audit().write_details_sheet(data)


def read_costs_sheet() -> list[dict[str, Any]]:
    return _get_default_audit().read_costs_sheet()


def write_costs_sheet(data: Sequence[Any]) -> None:
    _get_default_audit().write_costs_sheet(data)


def export_details_to_csv(filename: str = "details_export.csv") -> None:
    _get_default_audit().export_details_to_csv(filename)


def import_details_from_csv(filename: str = "details_import.csv") -> None:
    _get_default_audit().import_details_from_csv(filename)


if __name__ == "__main__":
    audit = SalesAudit()
    details = audit.read_details_sheet()
    costs = audit.read_costs_sheet()
    print(f"Details records: {len(details)}")
    print(f"Costs records: {len(costs)}")
    audit.export_details_to_csv()
