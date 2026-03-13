"""Unit tests for the retry policy and retrying WHAPI client wrapper."""

from __future__ import annotations

import pytest
import requests

from models import retry as retry_module
from models.retry import RetryingWhapiClient, RetryPolicy, retry_call


class DummyHTTPError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def test_retry_call_retries_on_request_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry until success for retryable request exceptions."""
    calls = {"count": 0}

    def func() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise requests.RequestException("boom")
        return "ok"

    monkeypatch.setattr(retry_module.time, "sleep", lambda _delay: None)
    policy = RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0, jitter=0.0)
    result = retry_call(func, policy=policy)
    assert result == "ok"
    assert calls["count"] == 3


def test_retry_call_does_not_retry_non_retry_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not retry exceptions that are not marked retryable."""
    calls = {"count": 0}

    def func() -> str:
        calls["count"] += 1
        raise ValueError("no")

    monkeypatch.setattr(retry_module.time, "sleep", lambda _delay: None)
    with pytest.raises(ValueError):
        retry_call(func, policy=RetryPolicy(max_attempts=3))
    assert calls["count"] == 1


def test_retry_call_retries_on_retry_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry when the error has a retryable HTTP status code."""
    calls = {"count": 0}

    def func() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise DummyHTTPError(500)
        return "ok"

    monkeypatch.setattr(retry_module.time, "sleep", lambda _delay: None)
    result = retry_call(func, policy=RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0))
    assert result == "ok"
    assert calls["count"] == 2


def test_retry_call_does_not_retry_on_non_retry_status_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not retry when the error status code is not retryable."""
    calls = {"count": 0}

    def func() -> str:
        calls["count"] += 1
        raise DummyHTTPError(400)

    monkeypatch.setattr(retry_module.time, "sleep", lambda _delay: None)
    with pytest.raises(DummyHTTPError):
        retry_call(func, policy=RetryPolicy(max_attempts=3))
    assert calls["count"] == 1


def test_retry_call_invokes_on_retry_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invoke the on_retry callback for each retry attempt."""
    calls = {"count": 0, "retries": []}

    def func() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise requests.RequestException("boom")
        return "ok"

    def on_retry(attempt: int, exc: BaseException) -> None:
        calls["retries"].append((attempt, type(exc)))

    monkeypatch.setattr(retry_module.time, "sleep", lambda _delay: None)
    policy = RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0, jitter=0.0)
    result = retry_call(func, policy=policy, on_retry=on_retry)
    assert result == "ok"
    assert calls["retries"] == [(1, requests.RequestException), (2, requests.RequestException)]


def test_retry_policy_next_delay_caps_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap exponential backoff at max_delay while adding jitter."""
    monkeypatch.setattr(retry_module.random, "uniform", lambda _a, _b: 0.1)
    policy = RetryPolicy(base_delay=1.0, max_delay=2.0, jitter=0.1)
    delay = policy.next_delay(10)
    assert delay == pytest.approx(2.1)


def test_retrying_whapi_client_retries_send_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry sending text through the wrapper client until it succeeds."""
    monkeypatch.setattr(retry_module.time, "sleep", lambda _delay: None)

    class DummyClient:
        def __init__(self) -> None:
            self.calls = 0

        def send_text(self, *args, **kwargs) -> dict[str, str]:
            self.calls += 1
            if self.calls < 3:
                raise requests.RequestException("boom")
            return {"status": "ok"}

    client = DummyClient()
    wrapper = RetryingWhapiClient(client, policy=RetryPolicy(max_attempts=3, jitter=0.0))
    result = wrapper.send_text("123", "hello")
    assert result == {"status": "ok"}
    assert client.calls == 3
