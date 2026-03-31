from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from controlplane.boundary.storageInterface.saleCommissions import (  # noqa: E402
    SaleCommissions,
)
from controlplane.boundary.storageInterface.staffToHotelMapping import (  # noqa: E402
    StaffToHotelMapping,
)

logger = logging.getLogger(__name__)


def _load_env_files() -> None:
    load_dotenv()
    env_path = os.path.join(PROJECT_ROOT, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()

_staff_mapping: StaffToHotelMapping | None = None
_sale_commissions: SaleCommissions | None = None


def _get_staff_mapping() -> StaffToHotelMapping | None:
    global _staff_mapping
    if _staff_mapping is not None:
        return _staff_mapping
    if not (os.getenv("STAFF_MAPPING_SHEET_ID") or os.getenv("STAFF_TO_HOTEL_SHEET_ID")):
        logger.warning("Staff mapping sheet id not set; commission calculation disabled")
        return None
    try:
        _staff_mapping = StaffToHotelMapping()
    except Exception as exc:
        logger.error("Failed to initialize staff mapping: %s", exc)
        return None
    return _staff_mapping


def _get_sale_commissions() -> SaleCommissions | None:
    global _sale_commissions
    if _sale_commissions is not None:
        return _sale_commissions
    try:
        _sale_commissions = SaleCommissions()
    except Exception as exc:
        logger.error("Failed to initialize sale commissions: %s", exc)
        return None
    return _sale_commissions


def generate_sale_id() -> str:
    """Generate a unique sale ID (UUID)."""
    return str(uuid.uuid4())


def calculate_and_distribute_commissions(
    sale_id: str,
    selling_price: float,
    cost_price: float,
    seller_name: str,
) -> list[dict[str, Any]]:
    """Calculate and distribute commissions to all staff members.

    Args:
        sale_id: Unique identifier for the sale
        selling_price: The selling price (Amount) of the sale
        cost_price: The cost price of the sale
        seller_name: Name of the person who made the sale

    Returns:
        List of commission entries that were created
    """
    staff_mapping = _get_staff_mapping()
    if staff_mapping is None:
        logger.error("Cannot calculate commissions: staff mapping not available")
        return []

    sale_commissions = _get_sale_commissions()
    if sale_commissions is None:
        logger.error("Cannot write commissions: sale commissions sheet not available")
        return []

    # Calculate profit
    profit = selling_price - cost_price
    if profit < 0:
        logger.warning(
            "Negative profit for sale_id=%s: selling_price=%s, cost_price=%s, profit=%s",
            sale_id,
            selling_price,
            cost_price,
            profit,
        )

    if profit <= 0:
        logger.info(
            "No commission distributed for sale_id=%s: profit=%s is not positive",
            sale_id,
            profit,
        )
        return []

    # Get all staff with commission percentages
    staff_list = staff_mapping.get_all_staff_with_commission()
    if not staff_list:
        logger.error("No staff members found in mapping for commission distribution")
        return []

    commission_entries: list[dict[str, Any]] = []

    for staff in staff_list:
        name = staff.get("name", "")
        phone = staff.get("phone", "")
        commission_pct = staff.get("commission_percentage", 0.0)

        if not name:
            logger.warning("Staff entry missing name, skipping: %s", staff)
            continue

        if commission_pct <= 0:
            logger.info(
                "Staff %s has no commission percentage (%s), skipping",
                name,
                commission_pct,
            )
            continue

        # Calculate commission value: profit * (percentage / 100)
        commission_value = profit * (commission_pct / 100.0)

        if commission_value <= 0:
            logger.warning(
                "Calculated commission for %s is non-positive: %s (profit=%s, pct=%s)",
                name,
                commission_value,
                profit,
                commission_pct,
            )
            continue

        # Write commission entry: [SaleId, Commission Value, Name, Phone]
        entry = [sale_id, round(commission_value, 2), name, phone]
        try:
            sale_commissions.write_commission(entry)
            commission_entries.append(
                {
                    "sale_id": sale_id,
                    "commission_value": round(commission_value, 2),
                    "name": name,
                    "phone": phone,
                }
            )
            logger.info(
                "Commission distributed: sale_id=%s, name=%s, value=%s (profit=%s, pct=%s%%)",
                sale_id,
                name,
                round(commission_value, 2),
                profit,
                commission_pct,
            )
        except Exception as exc:
            logger.error(
                "Failed to write commission for %s: %s",
                name,
                exc,
                exc_info=True,
            )

    logger.info(
        "Commission distribution complete for sale_id=%s: %d entries created, seller=%s",
        sale_id,
        len(commission_entries),
        seller_name,
    )

    return commission_entries


def build_commission_notification(
    seller_name: str,
    service: str,
    commission_entries: list[dict[str, Any]],
) -> str:
    """Build a notification message for the sales group about commissions.

    Args:
        seller_name: Name of the person who made the sale
        service: The service that was sold
        commission_entries: List of commission entries created

    Returns:
        Notification message string
    """
    if not commission_entries:
        return ""

    total_commission = sum(entry.get("commission_value", 0) for entry in commission_entries)
    num_recipients = len(commission_entries)

    message = (
        f"🎉 Great teamwork! {seller_name} just made a sale ({service}). "
        f"Everyone on the team will receive their commission share. "
        f"Total commission pool: {total_commission:.2f} MAD distributed to {num_recipients} team members. "
        f"Keep up the excellent work! 💪"
    )

    return message
