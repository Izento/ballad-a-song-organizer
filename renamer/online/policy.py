"""Shared pacing and failure vocabulary for external providers."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar


class Provider(StrEnum):
    ACOUSTID = "AcoustID"
    MUSICBRAINZ = "MusicBrainz"
    COVER_ART_ARCHIVE = "Cover Art Archive"


class FailureKind(StrEnum):
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    INVALID_RESPONSE = "invalid_response"


class ProviderError(RuntimeError):
    def __init__(
        self,
        provider: Provider,
        kind: FailureKind,
        message: str,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.kind = kind


class RateLimiter:
    """Serialize a provider's requests around a minimum interval."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._last_request = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            remaining = self.interval - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
            self._last_request = time.monotonic()


Result = TypeVar("Result")


@dataclass(frozen=True)
class RequestPolicy:
    provider: Provider
    limiter: RateLimiter
    retries: int = 0
    backoff: float = 0.5

    def request(
        self,
        operation: Callable[[], Result],
        *,
        transient_errors: tuple[type[BaseException], ...],
    ) -> Result:
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                return operation()
            except transient_errors as exc:
                if attempt == self.retries:
                    raise ProviderError(
                        self.provider,
                        FailureKind.TRANSIENT,
                        str(exc),
                    ) from exc
                time.sleep(self.backoff * (attempt + 1))
        raise AssertionError("Request retry loop did not return or raise")


__all__ = [
    "FailureKind",
    "Provider",
    "ProviderError",
    "RateLimiter",
    "RequestPolicy",
]
