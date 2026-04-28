from __future__ import annotations

from controlplane.control.memory.types import BotName


class MemoryAccessPolicy:
    def private_readers(self, bot_name: BotName) -> list[str]:
        return [bot_name]

    def common_readers_for_sales_learning(self) -> list[str]:
        return ["salesbot", "querybot"]

    def common_readers_for_query_memory(self) -> list[str]:
        return ["querybot"]
