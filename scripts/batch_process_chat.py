#!/usr/bin/env python3
"""Batch process historical WhatsApp messages through SalesBot.

Usage:
    python scripts/batch_process_chat.py <chat_id> [--count 100] [--dry-run]

Example:
    python scripts/batch_process_chat.py 120363408154982447@g.us --count 500
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()
load_dotenv(dotenv_path="env", override=True)

from communicationPlane.whatsappEngine.whapiInterface.whapi_client import WhapiClient
from controlplane.control.bot.salesbot.brain import process_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_all_messages(client: WhapiClient, chat_id: str, max_count: int = 500) -> list[dict]:
    """Fetch messages from chat, paginating if needed."""
    all_messages = []
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


def main():
    parser = argparse.ArgumentParser(description="Batch process WhatsApp messages through SalesBot")
    parser.add_argument("chat_id", help="WhatsApp chat ID (e.g., 120363408154982447@g.us)")
    parser.add_argument("--count", type=int, default=100, help="Number of messages to fetch (default: 100)")
    parser.add_argument("--dry-run", action="store_true", help="Only fetch and display messages, don't process")
    args = parser.parse_args()

    client = WhapiClient()
    
    logger.info(f"Fetching up to {args.count} messages from {args.chat_id}")
    messages = fetch_all_messages(client, args.chat_id, args.count)
    logger.info(f"Fetched {len(messages)} messages")

    # Filter to text messages only, exclude from_me
    text_messages = [
        m for m in messages
        if m.get("type") == "text" and not m.get("from_me") and m.get("text", {}).get("body")
    ]
    logger.info(f"Found {len(text_messages)} text messages from others")

    if args.dry_run:
        print("\n--- DRY RUN: Messages that would be processed ---\n")
        for i, msg in enumerate(text_messages[:20]):  # Show first 20
            sender = msg.get("from", "unknown")
            text = msg.get("text", {}).get("body", "")[:100]
            print(f"{i+1}. [{sender}]: {text}...")
        if len(text_messages) > 20:
            print(f"\n... and {len(text_messages) - 20} more messages")
        return

    # Process each message through SalesBot
    processed = 0
    errors = 0
    for i, msg in enumerate(text_messages):
        sender_id = msg.get("from")
        text = msg.get("text", {}).get("body", "")
        
        logger.info(f"Processing message {i+1}/{len(text_messages)} from {sender_id}")
        try:
            process_message(text, sender_id)
            processed += 1
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            errors += 1

    logger.info(f"Done! Processed: {processed}, Errors: {errors}")


if __name__ == "__main__":
    main()
