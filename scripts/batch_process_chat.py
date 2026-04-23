#!/usr/bin/env python3
"""Batch process historical WhatsApp messages through SalesBot with detailed analysis.

Usage:
    python scripts/batch_process_chat.py <chat_id> [--count 100] [--dry-run] [--output report.json]

Example:
    python scripts/batch_process_chat.py 120363408154982447@g.us --count 500 --workers 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()
load_dotenv(dotenv_path="env", override=True)

from communicationPlane.telegramEngine.telegramInterface.telegram_client import TelegramClient  # noqa: E402
from controlplane.control.bot.salesbot.brain import (  # noqa: E402
    _coerce_quantity,
    _extract_hotel_name,
    _get_case_insensitive,
    _get_llm_interface,
    _get_sales_audit,
    _required_fields_present,
    _resolve_staff_and_hotel,
    generate_sale_id,
    llm_extract,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class BatchAnalyzer:
    """Collects and analyzes batch processing results (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_messages = 0
        self.text_messages = 0
        self.processed = 0
        self.sheet_writes = 0
        self.sheet_write_errors = 0
        self.confidence_counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
        self.error_counts: dict[str, int] = defaultdict(int)
        # Errors segregated by confidence level
        self.errors_by_confidence: dict[str, list[dict[str, Any]]] = {
            "high": [],
            "medium": [],
            "low": [],
            "unknown": [],
            "pre_extraction": [],  # Errors before confidence is determined
        }
        self.errors: list[dict[str, Any]] = []
        self.low_confidence_samples: list[dict[str, Any]] = []
        self.medium_confidence_samples: list[dict[str, Any]] = []
        self.high_confidence_samples: list[dict[str, Any]] = []
        self.extraction_failures: list[dict[str, Any]] = []

    def increment_sheet_writes(self) -> None:
        with self._lock:
            self.sheet_writes += 1

    def increment_sheet_write_errors(self) -> None:
        with self._lock:
            self.sheet_write_errors += 1

    def record_extraction(
        self,
        message: str,
        sender: str,
        extracted: Any,
        error: Exception | None = None,
    ) -> None:
        with self._lock:
            self.processed += 1

            if error:
                error_type = type(error).__name__
                self.error_counts[error_type] += 1
                error_entry = {
                    "message": message[:200],
                    "sender": sender,
                    "error_type": error_type,
                    "error_message": str(error),
                }
                self.errors.append(error_entry)
                self.errors_by_confidence["pre_extraction"].append(error_entry)
                return

            if isinstance(extracted, dict) and "error" in extracted:
                self.error_counts["ExtractionError"] += 1
                self.extraction_failures.append(
                    {
                        "message": message[:200],
                        "sender": sender,
                        "error": extracted.get("error"),
                    }
                )
                return

            entries = []
            if isinstance(extracted, list):
                entries = [e for e in extracted if isinstance(e, dict)]
            elif isinstance(extracted, dict):
                entries = [extracted]

            if not entries:
                self.error_counts["EmptyExtraction"] += 1
                self.extraction_failures.append(
                    {
                        "message": message[:200],
                        "sender": sender,
                        "error": "No entries extracted",
                    }
                )
                return

            for entry in entries:
                confidence = str(_get_case_insensitive(entry, ["confidence"]) or "").lower()
                if confidence not in self.confidence_counts:
                    confidence = "unknown"
                self.confidence_counts[confidence] += 1

                sample = {
                    "message": message[:200],
                    "sender": sender,
                    "extracted": entry,
                }

                if confidence == "low" and len(self.low_confidence_samples) < 10:
                    self.low_confidence_samples.append(sample)
                elif confidence == "medium" and len(self.medium_confidence_samples) < 10:
                    self.medium_confidence_samples.append(sample)
                elif confidence == "high" and len(self.high_confidence_samples) < 5:
                    self.high_confidence_samples.append(sample)

    def record_post_extraction_error(
        self,
        message: str,
        sender: str,
        confidence: str,
        error: Exception,
        extracted: dict[str, Any] | None = None,
    ) -> None:
        """Record errors that happen after extraction (e.g., validation, sheet write)."""
        with self._lock:
            error_type = type(error).__name__
            self.error_counts[f"PostExtraction_{error_type}"] += 1
            error_entry = {
                "message": message[:200],
                "sender": sender,
                "confidence": confidence,
                "error_type": error_type,
                "error_message": str(error),
                "extracted": extracted,
            }
            self.errors.append(error_entry)
            if confidence in self.errors_by_confidence:
                self.errors_by_confidence[confidence].append(error_entry)

    def generate_report(self) -> dict[str, Any]:
        with self._lock:
            total_confidence = sum(self.confidence_counts.values())
            return {
                "summary": {
                    "total_messages_fetched": self.total_messages,
                    "text_messages": self.text_messages,
                    "processed": self.processed,
                    "total_extractions": total_confidence,
                    "sheet_writes": self.sheet_writes,
                    "sheet_write_errors": self.sheet_write_errors,
                },
                "confidence_analysis": {
                    "high": {
                        "count": self.confidence_counts["high"],
                        "percentage": f"{100 * self.confidence_counts['high'] / max(total_confidence, 1):.1f}%",
                    },
                    "medium": {
                        "count": self.confidence_counts["medium"],
                        "percentage": f"{100 * self.confidence_counts['medium'] / max(total_confidence, 1):.1f}%",
                    },
                    "low": {
                        "count": self.confidence_counts["low"],
                        "percentage": f"{100 * self.confidence_counts['low'] / max(total_confidence, 1):.1f}%",
                    },
                    "unknown": {
                        "count": self.confidence_counts["unknown"],
                        "percentage": f"{100 * self.confidence_counts['unknown'] / max(total_confidence, 1):.1f}%",
                    },
                },
                "error_analysis": {
                    "total_errors": sum(self.error_counts.values()),
                    "by_type": dict(self.error_counts),
                    "by_confidence": {
                        "high": len(self.errors_by_confidence["high"]),
                        "medium": len(self.errors_by_confidence["medium"]),
                        "low": len(self.errors_by_confidence["low"]),
                        "unknown": len(self.errors_by_confidence["unknown"]),
                        "pre_extraction": len(self.errors_by_confidence["pre_extraction"]),
                    },
                },
                "samples": {
                    "high_confidence": self.high_confidence_samples,
                    "medium_confidence": self.medium_confidence_samples,
                    "low_confidence": self.low_confidence_samples,
                    "extraction_failures": self.extraction_failures[:10],
                    "errors": self.errors[:10],
                    "errors_by_confidence": {k: v[:5] for k, v in self.errors_by_confidence.items()},
                },
                "generated_at": datetime.now().isoformat(),
            }

    def print_report(self) -> None:
        report = self.generate_report()

        print("\n" + "=" * 60)
        print("BATCH PROCESSING ANALYSIS REPORT")
        print("=" * 60)

        print("\n## SUMMARY")
        print(f"  Total messages fetched: {report['summary']['total_messages_fetched']}")
        print(f"  Text messages (from others): {report['summary']['text_messages']}")
        print(f"  Messages processed: {report['summary']['processed']}")
        print(f"  Total extractions: {report['summary']['total_extractions']}")
        print(f"  Sheet writes: {report['summary']['sheet_writes']}")
        print(f"  Sheet write errors: {report['summary']['sheet_write_errors']}")

        print("\n## CONFIDENCE ANALYSIS")
        ca = report["confidence_analysis"]
        print(f"  HIGH:    {ca['high']['count']:4d} ({ca['high']['percentage']})")
        print(f"  MEDIUM:  {ca['medium']['count']:4d} ({ca['medium']['percentage']})")
        print(f"  LOW:     {ca['low']['count']:4d} ({ca['low']['percentage']})")
        print(f"  UNKNOWN: {ca['unknown']['count']:4d} ({ca['unknown']['percentage']})")

        print("\n## ERROR ANALYSIS")
        ea = report["error_analysis"]
        print(f"  Total errors: {ea['total_errors']}")
        if ea["by_type"]:
            print("  By type:")
            for error_type, count in sorted(ea["by_type"].items(), key=lambda x: -x[1]):
                print(f"    - {error_type}: {count}")
        if any(ea["by_confidence"].values()):
            print("  By confidence level:")
            for conf, count in ea["by_confidence"].items():
                if count > 0:
                    print(f"    - {conf}: {count}")

        if report["samples"]["low_confidence"]:
            print("\n## LOW CONFIDENCE SAMPLES (first 10)")
            for i, sample in enumerate(report["samples"]["low_confidence"], 1):
                print(f"\n  [{i}] Message: {sample['message'][:100]}...")
                ext = sample["extracted"]
                print(f"      Service: {ext.get('Service', 'N/A')}")
                print(f"      Quantity: {ext.get('Quantity', 'N/A')}")
                print(f"      Date: {ext.get('Date', 'N/A')}")

        if report["samples"]["extraction_failures"]:
            print("\n## EXTRACTION FAILURES (first 10)")
            for i, sample in enumerate(report["samples"]["extraction_failures"], 1):
                print(f"\n  [{i}] Message: {sample['message'][:100]}...")
                print(f"      Error: {sample['error']}")

        print("\n" + "=" * 60)


def fetch_all_messages(client: TelegramClient, chat_id: str, max_count: int = 500) -> list[dict[str, Any]]:
    """Fetch messages from chat, paginating if needed."""
    all_messages: list[dict[str, Any]] = []
    offset = 0
    batch_size = 100

    while len(all_messages) < max_count:
        remaining = max_count - len(all_messages)
        count = min(batch_size, remaining)

        logger.info(f"Fetching messages offset={offset} count={count}")
        messages = client.get_messages(chat_id, count=count, offset=offset)

        if not messages:
            break

        all_messages.extend(messages)
        offset += len(messages)

        if len(messages) < count:
            break

    return all_messages


def save_report(analyzer: BatchAnalyzer, output_path: str) -> None:
    """Save current report to file."""
    report = analyzer.generate_report()
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)


def write_entry_to_sheet(
    entry: dict[str, Any],
    message: str,
    sender_id: str | None,
) -> tuple[bool, Exception | None]:
    """Write a single entry to the sales sheet. Returns (success, error)."""
    try:
        # Check required fields
        if not _required_fields_present(entry):
            return (False, ValueError("Missing required fields"))

        # Extract hotel name
        extracted_hotel = _extract_hotel_name(message, entry)

        # Resolve staff and hotel
        staff_name, hotel_name, mapping_error = _resolve_staff_and_hotel(
            sender_id,
            extracted_hotel,
            str(_get_case_insensitive(entry, ["Asignee"]) or "").strip() or None,
        )
        if mapping_error:
            return (False, ValueError("Staff/hotel mapping error"))

        service = _get_case_insensitive(entry, ["Service"]) or ""
        quantity = _get_case_insensitive(entry, ["Quantity"]) or ""
        if isinstance(quantity, str) and not quantity.strip():
            quantity = 1
        if quantity is None:
            quantity = 1
        quantity_value = _coerce_quantity(quantity)
        quantity_row: Any = int(quantity_value) if quantity_value.is_integer() else quantity_value

        # Get selling price and cost price from Pricing_Sales sheet
        selling_price = _get_sales_audit().get_selling_price(service, quantity_value, llm=_get_llm_interface())
        cost_price = _get_sales_audit().calculate_cost(service, quantity_value, llm=_get_llm_interface())

        # Generate unique sale ID
        sale_id = generate_sale_id()

        # Write to sheet
        _get_sales_audit().write_details_sheet(
            [
                service,
                quantity_row,
                _get_case_insensitive(entry, ["Date"]) or "",
                _get_case_insensitive(entry, ["Time"]) or "",
                _get_case_insensitive(entry, ["Guest"]) or "",
                _get_case_insensitive(entry, ["Room"]) or "",
                staff_name,
                selling_price,
                cost_price,
                "",  # Additional Details
                hotel_name or extracted_hotel or "",
                sale_id,
            ]
        )
        return (True, None)
    except Exception as e:
        return (False, e)


def process_single_message(
    msg: dict[str, Any],
    index: int,
    total: int,
    analyzer: BatchAnalyzer,
    write_to_sheet: bool = True,
) -> tuple[str, str, Any, Exception | None]:
    """Process a single message, optionally write to sheet, return results for analyzer."""
    sender_id = msg.get("from", "unknown")
    text = msg.get("text", {}).get("body", "")

    try:
        extracted = llm_extract(text)

        # Write high/medium confidence entries to sheet immediately
        if write_to_sheet and extracted:
            entries = []
            if isinstance(extracted, list):
                entries = [e for e in extracted if isinstance(e, dict)]
            elif isinstance(extracted, dict) and "error" not in extracted:
                entries = [extracted]

            for entry in entries:
                confidence = str(_get_case_insensitive(entry, ["confidence"]) or "").lower()
                if confidence in ("high", "medium"):
                    success, error = write_entry_to_sheet(entry, text, sender_id)
                    if success:
                        analyzer.increment_sheet_writes()
                        logger.info(f"Wrote entry to sheet (confidence={confidence})")
                    else:
                        analyzer.increment_sheet_write_errors()
                        if error:
                            analyzer.record_post_extraction_error(text, sender_id, confidence, error, entry)

        return (text, sender_id, extracted, None)
    except Exception as e:
        return (text, sender_id, None, e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch process WhatsApp messages through SalesBot")
    parser.add_argument("chat_id", help="WhatsApp chat ID (e.g., 120363408154982447@g.us)")
    parser.add_argument("--count", type=int, default=100, help="Number of messages to fetch (default: 100)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of messages to process (default: all)")
    parser.add_argument("--workers", type=int, default=5, help="Number of parallel workers (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Only fetch and display messages, don't process")
    parser.add_argument("--no-write", action="store_true", help="Don't write to sheet, only analyze")
    parser.add_argument(
        "--output", type=str, default="report.json", help="Output JSON report file (default: report.json)"
    )
    args = parser.parse_args()

    client = TelegramClient()
    analyzer = BatchAnalyzer()

    # Create report file early
    save_report(analyzer, args.output)
    logger.info(f"Created report file: {args.output}")

    logger.info(f"Fetching up to {args.count} messages from {args.chat_id}")
    messages = fetch_all_messages(client, args.chat_id, args.count)
    analyzer.total_messages = len(messages)
    logger.info(f"Fetched {len(messages)} messages")

    # Filter to text messages only, exclude from_me
    text_messages = [
        m for m in messages if m.get("type") == "text" and not m.get("from_me") and m.get("text", {}).get("body")
    ]
    analyzer.text_messages = len(text_messages)
    logger.info(f"Found {len(text_messages)} text messages from others")

    # Apply limit if specified
    if args.limit and args.limit < len(text_messages):
        text_messages = text_messages[: args.limit]
        logger.info(f"Limited to {args.limit} messages for processing")

    if args.dry_run:
        print("\n--- DRY RUN: Messages that would be processed ---\n")
        for i, msg in enumerate(text_messages[:20]):
            sender = msg.get("from", "unknown")
            text = msg.get("text", {}).get("body", "")[:100]
            print(f"{i + 1}. [{sender}]: {text}...")
        if len(text_messages) > 20:
            print(f"\n... and {len(text_messages) - 20} more messages")
        return

    # Process messages in parallel
    total = len(text_messages)
    completed = 0
    write_to_sheet = not args.no_write
    logger.info(f"Processing {total} messages with {args.workers} workers (write_to_sheet={write_to_sheet})...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        # Submit all tasks
        futures = {
            executor.submit(process_single_message, msg, i, total, analyzer, write_to_sheet): i
            for i, msg in enumerate(text_messages)
        }

        # Process results as they complete
        for future in as_completed(futures):
            idx = futures[future]
            try:
                text, sender_id, extracted, error = future.result()
                analyzer.record_extraction(text, sender_id, extracted, error)
            except Exception as e:
                logger.error(f"Unexpected error processing message {idx}: {e}")

            completed += 1
            if completed % 10 == 0:
                logger.info(f"Progress: {completed}/{total} messages processed")
                save_report(analyzer, args.output)

    # Generate and print report
    analyzer.print_report()

    # Save final report
    save_report(analyzer, args.output)
    logger.info(f"Report saved to {args.output}")


if __name__ == "__main__":
    main()
