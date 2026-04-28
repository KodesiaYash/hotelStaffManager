from __future__ import annotations

from collections import deque

from controlplane.control.memory.types import MemoryEvent, MemoryItem


class DeterministicSummaryStrategy:
    def build_summary(
        self,
        *,
        bot_name: str,
        conversation_id: str,
        chat_id: str,
        recent_events: list[MemoryEvent],
        tasks: list[MemoryItem],
        facts: list[MemoryItem],
    ) -> str:
        user_turns = deque(maxlen=4)
        assistant_turns = deque(maxlen=3)
        for event in recent_events:
            compact = " ".join(event.text.strip().split())
            if not compact:
                continue
            trimmed = compact[:180]
            if event.role == "user":
                user_turns.append(trimmed)
            elif event.role == "assistant":
                assistant_turns.append(trimmed)

        lines = [f"{bot_name} conversation `{conversation_id}` in chat `{chat_id}`."]
        if user_turns:
            lines.append("Recent user focus: " + " | ".join(user_turns))
        if assistant_turns:
            lines.append("Recent assistant responses: " + " | ".join(assistant_turns))
        active_tasks = [item.title for item in tasks if item.status == "active"]
        if active_tasks:
            lines.append("Open workflows: " + ", ".join(active_tasks[:4]))
        learned = [item.title for item in facts]
        if learned:
            lines.append("Known learned context: " + ", ".join(learned[:4]))
        return " ".join(lines)
