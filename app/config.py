"""Configuration loading and validation for OCI A1 Capacity Hunter."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OCIProfile(StrictModel):
    config_file_path: str = "~/.oci/config"
    profile_name: str = "DEFAULT"
    compartment_id: str = ""

    @field_validator("config_file_path")
    @classmethod
    def expand_config_path(cls, value: str) -> str:
        return os.path.expanduser(value.strip())


class InstanceSpecs(StrictModel):
    display_name: str = Field(default="oci-a1-node", min_length=1, max_length=255)
    shape: str = "VM.Standard.A1.Flex"
    ocpus: int = Field(default=1, ge=1, le=2)
    memory_in_gbs: int = Field(default=6, ge=1, le=12)
    boot_volume_size_in_gbs: int = Field(default=50, ge=47, le=200)
    operating_system: str = "Canonical Ubuntu"
    os_version: str = "22.04"
    assign_public_ip: bool = True
    ssh_public_key_path: str = "~/.ssh/id_ed25519.pub"
    subnet_id: str = ""
    image_id: str = ""
    cloud_init_file: str = ""

    @field_validator("ssh_public_key_path", "cloud_init_file")
    @classmethod
    def expand_local_path(cls, value: str) -> str:
        return os.path.expanduser(value.strip()) if value.strip() else ""

    @field_validator("shape")
    @classmethod
    def require_ampere_shape(cls, value: str) -> str:
        if value != "VM.Standard.A1.Flex":
            raise ValueError("this application currently supports VM.Standard.A1.Flex only")
        return value


class PollingPolicy(StrictModel):
    standard_retry_interval_seconds: int = Field(default=60, ge=1, le=3600)
    jitter_seconds: int = Field(default=8, ge=0, le=300)
    rate_limit_backoff_seconds: int = Field(default=120, ge=1, le=3600)
    rate_limit_backoff_max_seconds: int = Field(default=1800, ge=1, le=86400)
    instance_status_poll_interval_seconds: int = Field(default=15, ge=1, le=300)
    cycle_availability_domains: bool = True
    maximum_attempts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_backoff_range(self) -> "PollingPolicy":
        if self.jitter_seconds >= self.standard_retry_interval_seconds:
            raise ValueError("jitter_seconds must be smaller than standard_retry_interval_seconds")
        if self.rate_limit_backoff_max_seconds < self.rate_limit_backoff_seconds:
            raise ValueError("rate_limit_backoff_max_seconds must be >= rate_limit_backoff_seconds")
        return self


class UIConfig(StrictModel):
    enable_tui: bool = True
    enable_web: bool = True
    web_host: str = "127.0.0.1"
    web_port: int = Field(default=8080, ge=1, le=65535)


class TelegramConfig(StrictModel):
    enabled: bool = False
    bot_token: SecretStr = SecretStr("")
    chat_id: str = ""

    @model_validator(mode="after")
    def validate_enabled_channel(self) -> "TelegramConfig":
        if self.enabled and (not self.bot_token.get_secret_value() or not self.chat_id.strip()):
            raise ValueError("enabled Telegram notifications require bot_token and chat_id")
        return self


class DiscordConfig(StrictModel):
    enabled: bool = False
    webhook_url: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_enabled_channel(self) -> "DiscordConfig":
        if self.enabled and not self.webhook_url.get_secret_value().strip():
            raise ValueError("enabled Discord notifications require webhook_url")
        return self


class WebhookConfig(StrictModel):
    enabled: bool = False
    url: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_enabled_channel(self) -> "WebhookConfig":
        if self.enabled and not self.url.get_secret_value().strip():
            raise ValueError("enabled generic webhook notifications require url")
        return self


class NotificationsConfig(StrictModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)


class AppConfig(StrictModel):
    oci_profile: OCIProfile = Field(default_factory=OCIProfile)
    instance_specs: InstanceSpecs = Field(default_factory=InstanceSpecs)
    polling_policy: PollingPolicy = Field(default_factory=PollingPolicy)
    ui: UIConfig = Field(default_factory=UIConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _environment_overrides(environ: dict[str, str] | os._Environ[str]) -> dict[str, Any]:
    """Read nested settings using names such as INSTANCE_SPECS__OCPUS."""
    result: dict[str, Any] = {}
    for raw_name, raw_value in environ.items():
        name = raw_name.upper()
        if not name.startswith("OCI_A1__"):
            continue
        parts = name.removeprefix("OCI_A1__").lower().split("__")
        if len(parts) < 2:
            continue
        value: Any = raw_value
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            pass
        cursor = result
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return result


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load config JSON, then apply OCI_A1__SECTION__FIELD environment overrides."""
    selected_path = Path(config_path or os.environ.get("CONFIG_PATH", "config.json")).expanduser()
    data: dict[str, Any] = {}
    if selected_path.is_file():
        try:
            loaded = json.loads(selected_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read configuration file {selected_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"Configuration file {selected_path} must contain a JSON object")
        data = loaded
    elif config_path is not None or os.environ.get("CONFIG_PATH"):
        raise FileNotFoundError(f"Configuration file not found: {selected_path}")

    return AppConfig.model_validate(_deep_merge(data, _environment_overrides(os.environ)))


def read_ssh_public_key(config: AppConfig) -> str:
    """Read and validate the public key that will be sent to OCI instance metadata."""
    key_path = Path(config.instance_specs.ssh_public_key_path)
    if not key_path.is_file():
        raise FileNotFoundError(
            f"SSH public key not found at {key_path}. Set instance_specs.ssh_public_key_path "
            "to a readable .pub file."
        )
    key = key_path.read_text(encoding="utf-8").strip()
    valid_prefixes = ("ssh-rsa ", "ssh-ed25519 ", "ecdsa-")
    if not key.startswith(valid_prefixes):
        raise ValueError(f"SSH public key at {key_path} is not a supported OpenSSH public key")
    return key
