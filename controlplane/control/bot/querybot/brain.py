from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Any

from shared.env import ensure_on_sys_path, load_project_env, project_root_from

logger = logging.getLogger(__name__)

PROJECT_ROOT = project_root_from(__file__, levels_up=4)
ensure_on_sys_path(PROJECT_ROOT)
load_project_env(PROJECT_ROOT)

from communicationPlane.telegramEngine.telegramInterface.telegram_client import TelegramClient  # noqa: E402
from controlplane.boundary.llminterface.llm_interface import LLMInterface, get_query_bot_llm  # noqa: E402
from controlplane.boundary.storageInterface.saleCommissions import SaleCommissions  # noqa: E402
from controlplane.boundary.storageInterface.salesAudit import SalesAudit  # noqa: E402
from controlplane.control.memory import get_memory_service  # noqa: E402
from controlplane.control.memory.types import MemoryEvent, RecallRequest, utc_now  # noqa: E402
from models.retry import RetryingTelegramClient  # noqa: E402

DEFAULT_QUERY_PROMPT = (
    "You are a spreadsheet assistant for hotel sales operations. Answer the user's question using ONLY the provided "
    "Google Sheets data. The data includes:\n"
    "- Sales audit (Test_Sales): logged sales rows with Service, Quantity, Date, Time, Guest, Room, Assignee, "
    "Selling Price, Cost Price, Hotel, and SaleID\n"
    "- Sale Commissions: commission entries with SaleId, Commission Value, Name, and Phone\n\n"
    "Memory context may be provided from prior conversations. Use it for continuity and references, but rely on "
    "spreadsheet data as the factual source of truth.\n\n"
    "If the answer cannot be determined from the data, say that clearly. Be concise, unless asked to elaborate. "
    "If the user asks a query which requires a sense of time, use the current date and time provided in the input. "
    "Use plain language.\n\n"
    "Memory context:\n{memory_context}\n\n"
    "User question:\n{question}\n\n"
    "Current Date and Time: {current_time_str}\n"
    "Spreadsheet data:\n{spreadsheet_data}\n"
)
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
    # Read all sales audit rows (unbounded)
    details_rows = _get_sales_audit().read_details_sheet()
    context: dict[str, Any] = {
        "sales_audit_rows": details_rows,
        "sales_audit_row_count": len(details_rows),
    }

    # Add sale commissions data (limited to last 50 rows)
    commissions_client = _get_sale_commissions()
    if commissions_client is not None:
        try:
            commissions_rows = _trim_records(
                commissions_client.read_commissions(),
                max_rows=_get_max_rows("QUERYBOT_MAX_COMMISSIONS_ROWS", 50),
            )
            context["sale_commissions_rows"] = commissions_rows
            context["sale_commissions_row_count"] = len(commissions_rows)
        except Exception:
            context["sale_commissions_rows"] = []
            context["sale_commissions_row_count"] = 0

    return context


def _conversation_id(chat_id: str) -> str:
    return f"telegram:{chat_id}"


def answer_query(question: str, *, memory_context: str = "") -> str:
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
        current_time_str=datetime.datetime.now(datetime.UTC).isoformat(),
        memory_context=memory_context or "No prior memory available.",
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


def process_message(
    message: str,
    chat_id: str,
    sender_id: str | None = None,
    message_id: str | None = None,
    sender_name: str | None = None,
) -> None:
    logger.debug("QueryBot processing message length=%d chat_id=%s", len(message or ""), chat_id)
    memory_service = get_memory_service()
    conversation_id = _conversation_id(chat_id)
    recall = memory_service.recall(
        RecallRequest(
            bot_name="querybot",
            conversation_id=conversation_id,
            chat_id=chat_id,
            query_text=message or "",
            user_id=sender_id,
            sender_name=sender_name,
        )
    )

    if not message or not message.strip():
        logger.warning("QueryBot received empty message chat_id=%s", chat_id)
        answer = "I received an empty message. Please send your question."
    else:
        memory_service.record_event(
            MemoryEvent(
                bot_name="querybot",
                conversation_id=conversation_id,
                chat_id=chat_id,
                user_id=sender_id,
                sender_name=sender_name,
                role="user",
                text=message.strip(),
                metadata={"message_id": message_id} if message_id else {},
            )
        )
        answer = answer_query(message, memory_context=recall.to_markdown())

    try:
        _get_reply_client().send_text(to=chat_id, body=answer)
        logger.info("QueryBot reply sent chat_id=%s answer_len=%d", chat_id, len(answer))
        memory_service.record_event(
            MemoryEvent(
                bot_name="querybot",
                conversation_id=conversation_id,
                chat_id=chat_id,
                user_id=sender_id,
                sender_name=sender_name,
                role="assistant",
                text=answer,
                metadata={"responded_at": utc_now().isoformat()},
            )
        )
        memory_service.refresh_summary(bot_name="querybot", conversation_id=conversation_id, chat_id=chat_id)
    except Exception as exc:
        logger.error(
            "QueryBot failed to send reply error=%s chat_id=%s answer_preview=%s",
            str(exc)[:100],
            chat_id,
            answer[:200],
            exc_info=True,
        )
