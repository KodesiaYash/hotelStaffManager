from __future__ import annotations

import json
import os
import sys
from typing import Any
import datetime
from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from communicationPlane.whatsappEngine.whapiInterface.whapi_client import WhapiClient  # noqa: E402
from controlplane.boundary.llminterface.gemini_interface import GeminiInterface  # noqa: E402 # noqa: E402
from controlplane.boundary.storageInterface.salesAudit import SalesAudit  # noqa: E402
from models.chat_message import ChatMessage  # noqa: E402
from models.retry import RetryingWhapiClient  # noqa: E402

"""LLM is stateless, need to make it aware of current date and time by injecting it in the prompt"""
current_time_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

DEFAULT_QUERY_PROMPT = (
    "You are a spreadsheet assistant for hotel sales operations. Answer the user's question using ONLY the provided "
    "Google Sheets data. The sales audit sheet contains logged sales rows. If the answer cannot be determined from the data, say that clearly. Be concise, unless asked to elaborate."
    "If the user asks a query which requires a sense of time, use the current date and time provided in the input."
    "use plain language\n\n"
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
_llm_interface: GeminiInterface | None = None
_reply_client: RetryingWhapiClient | None = None


def _get_sales_audit() -> SalesAudit:
    global _sales_audit
    if _sales_audit is None:
        _sales_audit = SalesAudit()
    return _sales_audit

def _get_llm_interface() -> GeminiInterface:
    global _llm_interface
    if _llm_interface is None:
        _llm_interface = GeminiInterface(config={"temperature": 0})
    return _llm_interface


def _get_reply_client() -> RetryingWhapiClient:
    global _reply_client
    if _reply_client is None:
        _reply_client = RetryingWhapiClient(WhapiClient())
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
    return {
        "sales_audit_rows": details_rows,
        "sales_audit_row_count": len(details_rows)
    }


def answer_query(question: str) -> str:
    context = build_spreadsheet_context()
    prompt = DEFAULT_QUERY_PROMPT.format(
        question=question.strip(),
        spreadsheet_data=json.dumps(context, ensure_ascii=True, default=str),
        current_time_str=current_time_str,
    )
    answer = (_get_llm_interface().generate(prompt) or "").strip()
    if not answer:
        return "I can not answer this question from the spreadsheet data."
    return answer


def process_message(message: str, chat_id:str) -> None:
    answer = answer_query(message or "")
    _get_reply_client().send_text(to=chat_id, body=answer)
