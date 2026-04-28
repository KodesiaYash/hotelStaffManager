from __future__ import annotations

from dataclasses import dataclass

from controlplane.control.memory.types import BotName


@dataclass(frozen=True)
class MemoryProfile:
    bot_name: BotName
    summary_layers: tuple[str, ...]
    fact_layers: tuple[str, ...]
    task_layers: tuple[str, ...]
    episode_layers: tuple[str, ...]


QUERYBOT_PROFILE = MemoryProfile(
    bot_name="querybot",
    summary_layers=("summary",),
    fact_layers=("semantic",),
    task_layers=("task",),
    episode_layers=("episodic",),
)

SALESBOT_PROFILE = MemoryProfile(
    bot_name="salesbot",
    summary_layers=("summary",),
    fact_layers=("semantic",),
    task_layers=("task",),
    episode_layers=("episodic",),
)


def get_profile(bot_name: BotName) -> MemoryProfile:
    return QUERYBOT_PROFILE if bot_name == "querybot" else SALESBOT_PROFILE
