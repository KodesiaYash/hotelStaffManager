from __future__ import annotations

import logging
import os

from communicationPlane.telegramEngine.telegramInterface.telegram_client import TelegramClient
from controlplane.boundary.llminterface.llm_interface import LLMInterface, get_sales_bot_llm
from controlplane.boundary.storageInterface.salesAudit import SalesAudit
from controlplane.boundary.storageInterface.staffToHotelMapping import StaffToHotelMapping
from models.retry import RetryingTelegramClient

logger = logging.getLogger(__name__)

_sales_audit: SalesAudit | None = None
_llm_interface: LLMInterface | None = None
_staff_mapping: StaffToHotelMapping | None = None
_notification_client: RetryingTelegramClient | None = None


def get_sales_audit() -> SalesAudit:
    global _sales_audit
    if _sales_audit is None:
        _sales_audit = SalesAudit()
    return _sales_audit


def get_llm_interface() -> LLMInterface:
    global _llm_interface
    if _llm_interface is None:
        _llm_interface = get_sales_bot_llm()
    return _llm_interface


def get_staff_mapping() -> StaffToHotelMapping | None:
    global _staff_mapping
    if _staff_mapping is not None:
        return _staff_mapping
    if not (os.getenv("STAFF_MAPPING_SHEET_ID") or os.getenv("STAFF_TO_HOTEL_SHEET_ID")):
        logger.warning("Staff mapping sheet id not set; skipping staff mapping lookup")
        _staff_mapping = None
        return None
    try:
        _staff_mapping = StaffToHotelMapping()
    except Exception as exc:
        logger.warning("Staff mapping not configured: %s", exc)
        _staff_mapping = None
    return _staff_mapping


def get_notification_client() -> RetryingTelegramClient | None:
    global _notification_client
    if _notification_client is not None:
        return _notification_client
    try:
        _notification_client = RetryingTelegramClient(TelegramClient())
    except Exception as exc:
        logger.warning("Notification client not configured: %s", exc)
        _notification_client = None
    return _notification_client
