from __future__ import annotations

import csv
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
        """Write a row to the details sheet.

        Expected columns:
        0: Service Name, 1: Quantity, 2: Date, 3: Time, 4: Guest, 5: Room,
        6: Assignee, 7: Selling Price, 8: Cost Price, 9: Additional Details,
        10: hotelName, 11: SaleId
        """
        row = list(data)
        # Ensure minimum columns up to Assignee
        while len(row) < 7:
            row.append("")

        # Get selling price from column 7
        selling_price_raw = _parse_number(row[7]) if len(row) > 7 else None
        selling_price: float = selling_price_raw if selling_price_raw is not None else 0.0

        # Get or calculate cost price from column 8
        cost_price: float | None = None
        if len(row) > 8:
            cost_price = _parse_number(row[8])

        if cost_price is None:
            service = str(row[0]) if row else ""
            quantity = row[1] if len(row) > 1 else 1
            cost_price = self.calculate_cost(service, quantity)

        # Ensure row has all 12 columns
        while len(row) < 12:
            row.append("")

        row[7] = selling_price
        row[8] = cost_price

        self.connector.append_row(self.details_key, row)
        return float(cost_price)

    def validate_service(
        self,
        service: str,
        threshold: float = 0.6,
        llm: LLMClient | None = None,
    ) -> tuple[bool, str | None, list[tuple[str, float]]]:
        """Validate if a service exists in the pricelist.

        Args:
            service: The service name to validate
            threshold: Minimum similarity score for suggestions

        Returns:
            Tuple of (is_valid, matched_name, suggestions)
            - is_valid: True if exact/substring match found
            - matched_name: The matched service name if valid
            - suggestions: List of (name, score) tuples if not valid
        """
        if not self.pricelist:
            return True, service, []  # Can't validate without pricelist

        records = self.pricelist.read_pricelist()
        if not records:
            return True, service, []  # Empty pricelist, allow anything

        return service_exists_in_pricelist(service, records, threshold, llm=llm)

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
            llm_result = _llm_match_service(service_value, records, llm)
            if llm_result and llm_result.status == "matched" and llm_result.match:
                direct_match = _find_pricelist_match(records, llm_result.match)

        if direct_match is None:
            return 0.0

        unit_cost = _parse_number(
            _get_case_insensitive(
                direct_match,
                [
                    "Cost Price (DH)",
                    "Cost Price(MAD)",
                    "cost_price",
                    "cost price",
                    "cost",
                    "unit_cost",
                ],
            )
        )
        if unit_cost is None:
            return 0.0
        qty = _parse_number(quantity)
        if qty is None:
            qty = 1
        return float(unit_cost) * float(qty)

    def get_selling_price(self, service: str, quantity: float = 1, llm: LLMClient | None = None) -> float:
        """Get selling price from Pricing_Sales sheet for a service."""
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
            llm_result = _llm_match_service(service_value, records, llm)
            if llm_result and llm_result.status == "matched" and llm_result.match:
                direct_match = _find_pricelist_match(records, llm_result.match)

        if direct_match is None:
            return 0.0

        unit_price = _parse_number(
            _get_case_insensitive(
                direct_match,
                [
                    "Selling Price (MAD)",
                    "Selling Price",
                    "selling_price",
                    "selling price",
                    "price",
                    "rate",
                    "amount",
                    "unit price",
                    "unitprice",
                ],
            )
        )
        if unit_price is None:
            return 0.0
        qty = _parse_number(quantity)
        if qty is None:
            qty = 1
        return float(unit_price) * float(qty)

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
    service_lower = service_value.strip().lower()
    for row in records:
        row_service = _get_case_insensitive(row, ["service", "item", "name"])
        if not row_service:
            continue
        if str(row_service).strip().lower() == service_lower:
            return row
    return None


@dataclass
class LLMMatchResult:
    status: str
    match: str | None = None
    suggestions: list[str] = field(default_factory=list)


def _llm_match_service(
    service_value: str, records: list[dict[str, Any]], llm: LLMClient
) -> LLMMatchResult | None:
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
        key=lambda name: SequenceMatcher(None, service_value.lower(), str(name).lower()).ratio(),
        reverse=True,
    )
    top_candidates = scored[:20]
    candidate_lookup = {str(name).strip().lower(): str(name) for name in top_candidates}
    prompt = (
        "You are matching a rough service name written by hotel staff to a price list.\n"
        "Do not require an exact full-string match. Staff may use shorthand, partial names, "
        "phonetic spellings, misspellings, broken English, or low-literacy wording.\n"
        "Rules:\n"
        "1. Choose 'matched' only when one candidate clearly and unambiguously represents the intended service.\n"
        "2. Prefer the plain/base service over bundle or combo candidates unless the input mentions the extras.\n"
        "3. A candidate is a COMBO if it contains a joining keyword ('+', 'and', '/', 'with') AND both "
        "sides of the join are distinct products or activities (e.g., 'Hammam + Massage', 'Quad + Dinner', "
        "'Camel Ride + Dinner'). Only match the input to a combo if the input explicitly names all "
        "component products. A location or destination appended without a joining keyword is NOT a combo "
        "(e.g., 'Camel Ride Agafay' is not a combo — 'Agafay' is a destination qualifier, not a product).\n"
        "4. If the input words form a prefix shared by two or more candidates that differ only by a trailing "
        "qualifier (location, tier, number) — where the qualifier is NOT a separate product — return "
        "'ambiguous' listing ALL those candidates. Do not pick one arbitrarily. "
        "Example: input='camel ride', candidates 'Camel Ride Agafay' and 'Camel Ride Palmeraie' differ "
        "only by destination → return ambiguous with both.\n"
        "5. If two or more plain/base candidates are equally strong, return 'ambiguous'.\n"
        "6. If no candidate is a reasonable match, return 'no_match'.\n"
        "Examples:\n"
        "- Input='hammam', candidates include 'Hammam 1h' and 'Hammam + Massage': "
        "return matched='Hammam 1h'. Hammam + Massage is a combo — input only mentions one activity.\n"
        "- Input='massage', candidates include 'Massage 1h' and 'Hammam + Massage': "
        "return matched='Massage 1h'. Hammam + Massage is a combo — input only mentions one activity.\n"
        "- Input='camel ride', candidates include 'Camel Ride Agafay', 'Camel Ride Palmeraie', "
        "'Camel Ride + Dinner': "
        "return ambiguous=['Camel Ride Agafay', 'Camel Ride Palmeraie']. "
        "Both share the 'camel ride' prefix and differ only by location. "
        "'Camel Ride + Dinner' is a combo and is excluded.\n"
        "- Input='camel ride agafay', candidates include 'Camel Ride Agafay', 'Camel Ride Palmeraie': "
        "return matched='Camel Ride Agafay'. Location is explicitly mentioned.\n"
        "- Input='dinner', candidates include 'Dinner (150)' and 'Dinner (170)': "
        "return ambiguous=['Dinner (150)', 'Dinner (170)'].\n"
        "- Input='agafay', candidates include 'Agafay pack 1', 'Agafay pack 2', 'Agafay pack 3', "
        "'Agafay pack 4', 'Horse Ride Agafay': "
        "return ambiguous=['Agafay pack 1', 'Agafay pack 2', 'Agafay pack 3', 'Agafay pack 4']. "
        "'Horse Ride Agafay' is a combo (horse riding + Agafay destination) — input only says 'agafay'.\n"
        "- Input='horse ride agafay', candidates include 'Agafay pack 1', 'Horse Ride Agafay': "
        "return matched='Horse Ride Agafay'. Input explicitly mentions both activity and location.\n"
        "- Input='trans' or 'trans to airport': return matched='Transfer to airport'.\n"
        "Return ONLY JSON in one of these formats:\n"
        '{"status": "matched", "match": "<exact candidate>"}\n'
        '{"status": "ambiguous", "suggestions": ["<candidate1>", "<candidate2>", ...]}\n'
        '{"status": "no_match"}\n\n'
        f'Service to match: "{service_value}"\n'
        f"Candidates: {top_candidates}\n"
    )
    try:
        response = llm.generate(prompt)
    except Exception:
        return None
    try:
        import json

        raw = response.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[\w]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
    except Exception:
        return None
    status = str(data.get("status") or "").strip().lower()
    if status == "matched":
        match_name = str(data.get("match") or "").strip()
        canonical = candidate_lookup.get(match_name.lower())
        if canonical:
            return LLMMatchResult(status="matched", match=canonical)
    elif status == "ambiguous":
        raw_suggestions = data.get("suggestions") or []
        resolved = [
            candidate_lookup[s.strip().lower()]
            for s in raw_suggestions
            if isinstance(s, str) and s.strip().lower() in candidate_lookup
        ]
        if resolved:
            return LLMMatchResult(status="ambiguous", suggestions=resolved)
    return LLMMatchResult(status="no_match")


def find_nearest_services(service_value: str, records: list[dict[str, Any]], top_n: int = 5) -> list[tuple[str, float]]:
    """Find the nearest matching services using fuzzy matching.

    Returns:
        List of (service_name, similarity_score) tuples, sorted by score descending.
    """
    if not service_value or not records:
        return []

    service_lower = service_value.strip().lower()
    candidates: list[tuple[str, float]] = []

    for row in records:
        row_service = _get_case_insensitive(row, ["service", "item", "name"])
        if not row_service:
            continue
        row_service_str = str(row_service).strip()
        row_service_lower = row_service_str.lower()

        # Calculate similarity score
        score = SequenceMatcher(None, service_lower, row_service_lower).ratio()
        candidates.append((row_service_str, score))

    # Sort by score descending and return top N
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:top_n]


def service_exists_in_pricelist(
    service_value: str,
    records: list[dict[str, Any]],
    threshold: float = 0.6,
    llm: LLMClient | None = None,
) -> tuple[bool, str | None, list[tuple[str, float]]]:
    """Check if a service exists in the pricelist.

    Args:
        service_value: The service name to check
        records: The pricelist records
        threshold: Minimum similarity score for a "near match"

    Returns:
        Tuple of (exact_match_found, matched_service_name, nearest_matches)
    """
    if not service_value or not records:
        return False, None, []

    service_lower = service_value.strip().lower()

    # Exact match (case-insensitive equality only)
    for row in records:
        row_service = _get_case_insensitive(row, ["service", "item", "name"])
        if not row_service:
            continue
        if str(row_service).strip().lower() == service_lower:
            return True, str(row_service).strip(), []

    # LLM matching - handles everything: single match, ambiguous suggestions, no match
    if llm:
        llm_result = _llm_match_service(service_value, records, llm)
        if llm_result:
            if llm_result.status == "matched" and llm_result.match:
                return True, llm_result.match, []
            if llm_result.status == "ambiguous" and llm_result.suggestions:
                return False, None, [(s, 0.95) for s in llm_result.suggestions]

    # Fuzzy fallback when LLM is disabled or returns no_match
    nearest = find_nearest_services(service_value, records, top_n=5)
    good_matches = [(name, score) for name, score in nearest if score >= threshold]
    if good_matches:
        return False, None, good_matches
    return False, None, nearest


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
