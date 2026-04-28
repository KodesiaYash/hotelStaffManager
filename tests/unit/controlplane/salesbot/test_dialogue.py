from __future__ import annotations

import pytest

from controlplane.control.bot.salesbot.services import dialogue as dialogue_module


def test_build_service_clarification_message_uses_llm_and_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    class DummyLLM:
        def generate(self, prompt: str) -> str:
            prompts.append(prompt)
            return "Hey, what did you mean by this?\nPossible suggestions are:\n1. One Hour Hammam"

    monkeypatch.setattr(dialogue_module, "get_llm_interface", lambda: DummyLLM())
    monkeypatch.setattr(
        dialogue_module,
        "build_sales_memory_context",
        lambda **_kwargs: "## Recent Turns\n- User: hamam\n- Assistant: asking for clarification",
    )

    message = dialogue_module.build_service_clarification_message(
        service_name="hamam",
        suggestions=[("One Hour Hammam", 0.94), ("Hammam + Massage", 0.82)],
        chat_id="chat-1",
        sender_id="42",
        sender_name="alice",
    )

    assert "Hey, what did you mean by this?" in message
    assert prompts
    assert "Recent Turns" in prompts[0]
    assert "Closest suggestions" in prompts[0]


def test_interpret_service_reply_uses_llm_to_choose_suggestion(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyLLM:
        def generate(self, _prompt: str) -> str:
            return '{"match":"Transfer To Airport","confidence":"high"}'

    monkeypatch.setattr(dialogue_module, "get_llm_interface", lambda: DummyLLM())
    monkeypatch.setattr(
        dialogue_module,
        "build_sales_memory_context",
        lambda **_kwargs: "## Recent Turns\n- User: trans",
    )

    result = dialogue_module.interpret_service_reply(
        original_service="trans",
        user_reply="trans to airport",
        suggestions=[("Transfer To Airport", 0.94), ("Transfer From Airport", 0.90)],
        chat_id="chat-1",
        sender_id="42",
        sender_name="alice",
    )

    assert result.matched_service == "Transfer To Airport"
    assert result.confidence == "high"


def test_interpret_service_reply_falls_back_when_llm_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dialogue_module,
        "get_llm_interface",
        lambda: (_ for _ in ()).throw(RuntimeError("llm unavailable")),
    )
    monkeypatch.setattr(
        dialogue_module,
        "build_sales_memory_context",
        lambda **_kwargs: "## Recent Turns\n- User: first option",
    )

    result = dialogue_module.interpret_service_reply(
        original_service="hamam",
        user_reply="first option",
        suggestions=[("One Hour Hammam", 0.94), ("Hammam + Massage", 0.82)],
        chat_id="chat-1",
        sender_id="42",
        sender_name="alice",
    )

    assert result.matched_service == "One Hour Hammam"
    assert result.confidence == "medium"
