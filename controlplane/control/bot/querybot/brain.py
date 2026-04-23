from __future__ import annotations

import datetime
import json
import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from communicationPlane.telegramEngine.telegramInterface.telegram_client import TelegramClient  # noqa: E402
from controlplane.boundary.llminterface.llm_interface import LLMInterface, get_query_bot_llm  # noqa: E402
from controlplane.boundary.storageInterface.saleCommissions import SaleCommissions  # noqa: E402
from controlplane.boundary.storageInterface.salesAudit import SalesAudit  # noqa: E402
from models.retry import RetryingTelegramClient  # noqa: E402

"""LLM is stateless, need to make it aware of current date and time by injecting it in the prompt"""
current_time_str = datetime.datetime.now(datetime.UTC).isoformat()

DEFAULT_QUERY_PROMPT = (
    "You are a spreadsheet assistant for hotel sales operations. Answer the user's question using ONLY the provided "
    "Google Sheets data. The data includes:\n"
    "- Sales audit (Test_Sales): logged sales rows with Service, Quantity, Date, Time, Guest, Room, Assignee, "
    "Selling Price, Cost Price, Hotel, and SaleID\n"
    "- Sale Commissions: commission entries with SaleId, Commission Value, Name, and Phone\n\n"
    "If the answer cannot be determined from the data, say that clearly. Be concise, unless asked to elaborate. "
    "If the user asks a query which requires a sense of time, use the current date and time provided in the input. "
    "Use plain language.\n\n"
    "User question:\n{question}\n\n"
    "Current Date and Time: {current_time_str}\n"
    "Spreadsheet data:\n{spreadsheet_data}\n"
)


def _load_env_files() -> None:
    load_dotenv()
    env_path = os.path.join(PROJECT_ROOT, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)


_load_env_files()

_sales_audit: SalesAudit | None = None
_sale_commissions: SaleCommissions | None = None
_llm_interface: LLMInterface | None = None
_reply_client: RetryingTelegramClient | None = None


def _get_sales_audit() -> SalesAudit:
    global _sales_audit
    if _sales_audit is None:
        _sales_audit = SalesAudit()
    return _sales_audit


def _get_sale_commissions() -> SaleCommissions | None:
    global _sale_commissions
    if _sale_commissions is not None:
        return _sale_commissions
    try:
        _sale_commissions = SaleCommissions()
    except Exception:
        _sale_commissions = None
    return _sale_commissions


def _get_llm_interface() -> LLMInterface:
    global _llm_interface
    if _llm_interface is None:
        _llm_interface = get_query_bot_llm()
    return _llm_interface


def _get_reply_client() -> RetryingTelegramClient:
    global _reply_client
    if _reply_client is None:
        _reply_client = RetryingTelegramClient(TelegramClient())
    return _reply_client


def _get_max_rows(env_key: str, default: int) -> int:
    raw_value = os.getenv(env_key, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(value, 1)


def _trim_records(records: list[dict[str, Any]], *, max_rows: int) -> list[dict[str, Any]]:
    if len(records) <= max_rows:
        return records
    return records[-max_rows:]


def build_spreadsheet_context() -> dict[str, Any]:
    details_rows = _trim_records(
        _get_sales_audit().read_details_sheet(),
        max_rows=_get_max_rows("QUERYBOT_MAX_DETAILS_ROWS", 200),
    )
    context: dict[str, Any] = {
        "sales_audit_rows": details_rows,
        "sales_audit_row_count": len(details_rows),
    }

    # Add sale commissions data
    commissions_client = _get_sale_commissions()
    if commissions_client is not None:
        try:
            commissions_rows = _trim_records(
                commissions_client.read_commissions(),
                max_rows=_get_max_rows("QUERYBOT_MAX_COMMISSIONS_ROWS", 500),
            )
            context["sale_commissions_rows"] = commissions_rows
            context["sale_commissions_row_count"] = len(commissions_rows)
        except Exception:
            context["sale_commissions_rows"] = []
            context["sale_commissions_row_count"] = 0

    return context


def answer_query(question: str) -> str:
    logger.debug("QueryBot building spreadsheet context")
    try:
        context = build_spreadsheet_context()
    except Exception as exc:
        logger.error(
            "QueryBot failed to build context error=%s question_preview=%s",
            str(exc)[:100],
            question[:200],
            exc_info=True,
        )
        return "I encountered an error accessing the spreadsheet data. Please try again."

    logger.debug(
        "QueryBot context: sales_rows=%d, commissions_rows=%d",
        context.get("sales_audit_row_count", 0),
        context.get("sale_commissions_row_count", 0),
    )
    prompt = DEFAULT_QUERY_PROMPT.format(
        question=question.strip(),
        spreadsheet_data=json.dumps(context, ensure_ascii=True, default=str),
        current_time_str=current_time_str,
    )
    logger.info("QueryBot LLM prompt_len=%d question_len=%d", len(prompt), len(question))

    try:
        answer = (_get_llm_interface().generate(prompt) or "").strip()
    except Exception as exc:
        logger.error(
            "QueryBot LLM call failed error=%s question_preview=%s",
            str(exc)[:100],
            question[:200],
            exc_info=True,
        )
        return "I encountered an error processing your question. Please try again."

    if not answer:
        logger.warning("QueryBot LLM returned empty answer question_preview=%s", question[:200])
        return "I can not answer this question from the spreadsheet data."
    logger.info("QueryBot LLM answer_len=%d", len(answer))
    return answer


def process_message(message: str, chat_id: str) -> None:
    logger.debug("QueryBot processing message length=%d chat_id=%s", len(message or ""), chat_id)

    if not message or not message.strip():
        logger.warning("QueryBot received empty message chat_id=%s", chat_id)
        answer = "I received an empty message. Please send your question."
    else:
        answer = answer_query(message)

    try:
        _get_reply_client().send_text(to=chat_id, body=answer)
        logger.info("QueryBot reply sent chat_id=%s answer_len=%d", chat_id, len(answer))
    except Exception as exc:
        logger.error(
            "QueryBot failed to send reply error=%s chat_id=%s answer_preview=%s",
            str(exc)[:100],
            chat_id,
            answer[:200],
            exc_info=True,
        )
