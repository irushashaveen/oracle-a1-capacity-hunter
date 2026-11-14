"""Telegram, Discord, and generic JSON webhook notifications."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from app.config import AppConfig


class NotificationError(RuntimeError):
    pass


class Notifier:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        notifications = self.config.notifications
        return any((notifications.telegram.enabled, notifications.discord.enabled, notifications.webhook.enabled))

    def _send_telegram(self, message: str) -> None:
        channel = self.config.notifications.telegram
        token = channel.bot_token.get_secret_value()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = httpx.post(
            url,
            json={"chat_id": channel.chat_id, "text": message, "disable_web_page_preview": True},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise NotificationError("Telegram rejected the notification")

    def _send_discord(self, message: str) -> None:
        url = self.config.notifications.discord.webhook_url.get_secret_value()
        response = httpx.post(url, json={"content": message[:1900]}, timeout=10.0)
        response.raise_for_status()

    def _send_webhook(self, message: str, event: str) -> None:
        url = self.config.notifications.webhook.url.get_secret_value()
        response = httpx.post(url, json={"event": event, "message": message}, timeout=10.0)
        response.raise_for_status()

    def send(self, message: str, event: str = "oci_a1_notification") -> list[str]:
        notifications = self.config.notifications
        if not self.enabled:
            raise NotificationError("No notification channel is enabled")
        senders: list[tuple[str, Callable[[str], None]]] = []
        if notifications.telegram.enabled:
            senders.append(("Telegram", self._send_telegram))
        if notifications.discord.enabled:
            senders.append(("Discord", self._send_discord))

        successes: list[str] = []
        failures: list[str] = []
        for name, sender in senders:
            try:
                sender(message)
                successes.append(name)
            except (httpx.HTTPError, ValueError, NotificationError) as exc:
                failures.append(f"{name}: {type(exc).__name__}")
        if notifications.webhook.enabled:
            try:
                self._send_webhook(message, event)
                successes.append("Webhook")
            except (httpx.HTTPError, ValueError, NotificationError) as exc:
                failures.append(f"Webhook: {type(exc).__name__}")
        if failures:
            raise NotificationError("Notification delivery failed for " + ", ".join(failures))
        return successes

    def send_test(self) -> list[str]:
        return self.send("OCI A1 Capacity Hunter test notification", event="test")


def provisioning_message(
    *,
    display_name: str,
    instance_id: str,
    lifecycle_state: str,
    compartment_id: str,
    availability_domain: str,
    public_ip: str | None,
) -> str:
    public_ip_text = public_ip or "Not available yet; check the OCI Console"
    ssh_text = f"ssh ubuntu@{public_ip}" if public_ip else "Public IPv4 is not available yet"
    return "\n".join([
        "OCI A1 Capacity Hunter: instance is ready",
        f"Name: {display_name}",
        f"State: {lifecycle_state}",
        f"Instance OCID: {instance_id}",
        f"Compartment: {compartment_id}",
        f"Availability domain: {availability_domain}",
        f"Public IPv4: {public_ip_text}",
        f"SSH: {ssh_text}",
    ])


def attempt_summary_message(
    *,
    attempt: int,
    availability_domain: str,
    result: str,
    detail: str,
    retry_delay_seconds: float | None = None,
    next_retry_at: str | None = None,
    next_availability_domain: str | None = None,
) -> str:
    lines = [
        f"OCI A1 Capacity Hunter: attempt {attempt}",
        f"Availability domain: {availability_domain}",
        f"Result: {result}",
        detail,
    ]
    if next_availability_domain:
        lines.append(f"Next availability domain: {next_availability_domain}")
    if retry_delay_seconds is not None:
        lines.append(f"Retry in: {retry_delay_seconds:.0f} seconds")
    if next_retry_at:
        lines.append(f"Next retry at (UTC): {next_retry_at}")
    return "\n".join(lines)
