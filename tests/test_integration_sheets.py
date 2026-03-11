from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pytest
from dotenv import load_dotenv

from boundary.storageInterface.priceList import PriceList
from boundary.storageInterface.salesAudit import SalesAudit
from boundary.storageInterface.sheetsConnector import normalize_env_value

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_env() -> None:
    load_dotenv()
    env_path = os.path.join(PROJECT_ROOT, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


def _require_env() -> None:
    required = [
        "GOOGLE_SHEETS_KEY",
        "SALES_AUDIT_SHEET_ID",
        "SALES_PRICELIST_SHEET_ID",
    ]
    missing = [key for key in required if not normalize_env_value(os.getenv(key))]
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")


def _cleanup_by_marker(worksheet: Any, marker: str) -> None:
    if os.getenv("HEALTHCHECK_CLEANUP", "1") == "0":
        return
    cell = worksheet.find(marker)
    if cell:
        worksheet.delete_rows(cell.row)


_load_env()


@pytest.mark.integration
def test_read_sales_audit_sheet() -> None:
    _require_env()
    audit = SalesAudit()
    rows = audit.read_details_sheet()
    assert isinstance(rows, list)


@pytest.mark.integration
def test_read_pricelist_sheet() -> None:
    _require_env()
    pricelist = PriceList()
    rows = pricelist.read_pricelist()
    assert isinstance(rows, list)


@pytest.mark.integration
def test_write_sales_audit_sheet() -> None:
    _require_env()
    if os.getenv("HEALTHCHECK_WRITE") != "1":
        pytest.skip("Set HEALTHCHECK_WRITE=1 to enable write tests")

    audit = SalesAudit()
    marker = f"PYTEST_AUDIT_{datetime.utcnow().isoformat()}"
    row = [marker, "1", "", "", "", "", "", "0"]
    audit.write_details_sheet(row)

    worksheet = audit.connector.get_worksheet(audit.details_key)
    _cleanup_by_marker(worksheet, marker)


@pytest.mark.integration
def test_write_pricelist_sheet() -> None:
    _require_env()
    if os.getenv("HEALTHCHECK_WRITE") != "1":
        pytest.skip("Set HEALTHCHECK_WRITE=1 to enable write tests")

    pricelist = PriceList()
    worksheet = pricelist.connector.get_worksheet(pricelist.sheet_key)
    marker = f"PYTEST_PRICE_{datetime.utcnow().isoformat()}"
    row = [marker, "123", "pytest"]
    worksheet.append_row(row)
    _cleanup_by_marker(worksheet, marker)


@pytest.mark.integration
def test_process_message_pipeline() -> None:
    _require_env()
    if os.getenv("HEALTHCHECK_WRITE") != "1" or os.getenv("HEALTHCHECK_TESTPY") != "1":
        pytest.skip("Set HEALTHCHECK_WRITE=1 and HEALTHCHECK_TESTPY=1 to enable")

    os.environ.setdefault("GEMINI_API_KEY", "DUMMY")

    from control.bot.salesBot import test as test_module

    marker = f"PYTEST_TESTPY_{datetime.utcnow().isoformat()}"
    fake_payload = {
        "Service": marker,
        "Quantity": 1,
        "Date": "01/01/2026",
        "Time": "00:00",
        "Guest": "Healthcheck",
        "Room": "N/A",
        "Asignee": "System",
        "Amount": 0,
        "confidence": "high",
    }

    original_llm_extract = test_module.llm_extract

    def _fake_extract(_message: str) -> dict[str, Any]:
        return fake_payload

    test_module.llm_extract = _fake_extract  # type: ignore[assignment]
    try:
        test_module.process_message("healthcheck")
    finally:
        test_module.llm_extract = original_llm_extract

    audit = SalesAudit()
    worksheet = audit.connector.get_worksheet(audit.details_key)
    _cleanup_by_marker(worksheet, marker)
