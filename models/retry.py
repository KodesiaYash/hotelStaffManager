from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import requests

T = TypeVar("T")


class TelegramSender(Protocol):
    def send_text(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    def send_notification(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    def send_image(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    def send_video(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    def send_document(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    def set_reaction(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 5.0
    jitter: float = 0.1
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    retry_exceptions: tuple[type[BaseException], ...] = (requests.RequestException,)

    def next_delay(self, attempt: int) -> float:
        exponential = min(self.base_delay * (2 ** max(attempt - 1, 0)), self.max_delay)
        jitter = random.uniform(0, self.jitter) if self.jitter > 0 else 0.0  # nosec B311
        return exponential + jitter


def retry_call(
    func: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    policy = policy or RetryPolicy()
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except BaseException as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code is not None:
                should_retry = status_code in policy.retry_statuses
                if not should_retry or attempt >= policy.max_attempts:
                    raise
            elif not isinstance(exc, policy.retry_exceptions) or attempt >= policy.max_attempts:
                raise
            if on_retry:
                on_retry(attempt, exc)
            time.sleep(policy.next_delay(attempt))


@dataclass
class RetryingTelegramClient:
    client: TelegramSender
    policy: RetryPolicy = field(default_factory=RetryPolicy)

    def send_text(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return retry_call(lambda: self.client.send_text(*args, **kwargs), policy=self.policy)

    def send_notification(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return retry_call(lambda: self.client.send_notification(*args, **kwargs), policy=self.policy)

    def send_image(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return retry_call(lambda: self.client.send_image(*args, **kwargs), policy=self.policy)

    def send_video(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return retry_call(lambda: self.client.send_video(*args, **kwargs), policy=self.policy)

    def send_document(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return retry_call(lambda: self.client.send_document(*args, **kwargs), policy=self.policy)

    def set_reaction(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return retry_call(lambda: self.client.set_reaction(*args, **kwargs), policy=self.policy)
