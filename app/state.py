"""Thread-safe status shared by the provisioner, terminal UI, and web UI."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AppState:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._running_requested = True
        self._shutdown_requested = False
        self._status = "Starting"
        self._message = "Preparing OCI configuration"
        self._attempts = 0
        self._availability_domains: list[str] = []
        self._current_ad = ""
        self._last_attempt_at: str | None = None
        self._next_retry_at: str | None = None
        self._instance_id: str | None = None
        self._public_ip: str | None = None
        self._logs: deque[dict[str, str]] = deque(maxlen=100)
        self.add_log("Application started")

    def update(self, **values: Any) -> None:
        with self._condition:
            for name, value in values.items():
                if name not in {
                    "status", "message", "attempts", "availability_domains", "current_ad",
                    "last_attempt_at", "next_retry_at", "instance_id", "public_ip",
                }:
                    raise AttributeError(f"Unknown state field: {name}")
                setattr(self, f"_{name}", value)
            self._condition.notify_all()

    def add_log(self, message: str, level: str = "INFO") -> None:
        with self._condition:
            self._logs.append({"timestamp": utc_now(), "level": level.upper(), "message": message})
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "status": self._status,
                "message": self._message,
                "running": self._running_requested,
                "attempts": self._attempts,
                "availability_domains": list(self._availability_domains),
                "current_ad": self._current_ad,
                "last_attempt_at": self._last_attempt_at,
                "next_retry_at": self._next_retry_at,
                "instance_id": self._instance_id,
                "public_ip": self._public_ip,
                "logs": list(self._logs),
            }

    def toggle_running(self) -> bool:
        with self._condition:
            self._running_requested = not self._running_requested
            if self._running_requested:
                if self._status == "Paused":
                    self._status = "Polling"
                self._message = "Polling resumed"
            else:
                self._status = "Paused"
                self._message = "Polling paused by operator"
            self._logs.append({
                "timestamp": utc_now(),
                "level": "INFO",
                "message": self._message,
            })
            self._condition.notify_all()
            return self._running_requested

    def request_shutdown(self) -> None:
        with self._condition:
            self._shutdown_requested = True
            self._condition.notify_all()

    def wait_until_running(self) -> bool:
        with self._condition:
            while not self._running_requested and not self._shutdown_requested:
                self._condition.wait(timeout=0.5)
            return not self._shutdown_requested

    def wait(self, seconds: float) -> bool:
        """Wait up to seconds, returning false if shutdown was requested."""
        deadline = time.monotonic() + max(0.0, seconds)
        with self._condition:
            while not self._shutdown_requested:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True
                if not self._running_requested:
                    paused_at = time.monotonic()
                    while not self._running_requested and not self._shutdown_requested:
                        self._status = "Paused"
                        self._condition.wait(timeout=0.5)
                    if self._shutdown_requested:
                        return False
                    deadline += time.monotonic() - paused_at
                    continue
                self._condition.wait(timeout=min(remaining, 0.5))
            return False

    def is_shutdown_requested(self) -> bool:
        with self._condition:
            return self._shutdown_requested
