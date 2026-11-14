"""OCI launch loop with conservative retries and duplicate-launch protection."""

from __future__ import annotations

import random
import configparser
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any

import oci
from oci.exceptions import ServiceError

from app.cloud_init import encoded_user_data
from app.config import AppConfig, read_ssh_public_key
from app.discovery import DiscoveredResources, discover_resources
from app.notifier import (
    Notifier,
    NotificationError,
    attempt_summary_message,
    provisioning_message,
)
from app.state import AppState, utc_now


class ExistingInstanceError(RuntimeError):
    pass


def create_oci_clients(config: AppConfig) -> tuple[dict[str, Any], Any, Any, Any]:
    """Load the selected local OCI profile and construct SDK clients."""
    config_path = Path(config.oci_profile.config_file_path)
    if os.environ.get("OCI_A1_CONTAINER") == "1":
        # OCI setup writes key_file as a host-absolute path. In Docker, the same
        # directory is mounted at /home/app/.oci, so remap that path in a
        # temporary copy of the profile instead of changing the user's file.
        parser = configparser.ConfigParser(interpolation=None)
        if not parser.read(config_path, encoding="utf-8"):
            raise FileNotFoundError(f"OCI config file not found: {config_path}")
        profile = config.oci_profile.profile_name
        is_default_profile = profile.upper() == parser.default_section.upper()
        if not is_default_profile and not parser.has_section(profile):
            raise ValueError(f"OCI profile {profile!r} was not found in {config_path}")
        key_file = parser.get(profile, "key_file", fallback="").strip()
        if key_file:
            normalized = key_file.replace("\\", "/")
            host_dir = os.environ.get("OCI_A1_HOST_OCI_DIR", "").replace("\\", "/").rstrip("/")
            if host_dir and normalized.lower().startswith(host_dir.lower() + "/"):
                relative_key = normalized[len(host_dir) + 1:]
            else:
                relative_key = PurePosixPath(normalized).name
            mounted_key = Path("/home/app/.oci") / Path(*PurePosixPath(relative_key).parts)
            if not mounted_key.is_file():
                raise FileNotFoundError(
                    "The OCI API signing key must be inside the mounted OCI config directory. "
                    f"Expected to find it at {mounted_key}."
                )
            if is_default_profile:
                parser.defaults()["key_file"] = str(mounted_key)
            else:
                parser.set(profile, "key_file", str(mounted_key))
        with tempfile.TemporaryDirectory(prefix="oci-a1-profile-") as temporary_directory:
            temporary_config = Path(temporary_directory) / "config"
            with temporary_config.open("w", encoding="utf-8") as output:
                parser.write(output)
            sdk_config = oci.config.from_file(
                file_location=str(temporary_config),
                profile_name=profile,
            )
    else:
        sdk_config = oci.config.from_file(
            file_location=str(config_path),
            profile_name=config.oci_profile.profile_name,
        )
    return (
        sdk_config,
        oci.identity.IdentityClient(sdk_config),
        oci.core.VirtualNetworkClient(sdk_config),
        oci.core.ComputeClient(sdk_config),
    )


def is_capacity_error(error: ServiceError) -> bool:
    message = str(getattr(error, "message", "") or "").lower()
    code = str(getattr(error, "code", "") or "").lower()
    if getattr(error, "status", None) != 500:
        return False
    return "out of host capacity" in message or "capacity" in code or code == "internalerror"


def retry_after_seconds(error: ServiceError) -> float | None:
    headers = getattr(error, "headers", None) or {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_time = parsedate_to_datetime(str(value))
            if retry_time.tzinfo is None:
                retry_time = retry_time.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_time - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class Provisioner:
    def __init__(
        self,
        config: AppConfig,
        state: AppState,
        notifier: Notifier,
        *,
        sdk_config: dict[str, Any] | None = None,
        identity_client: Any | None = None,
        network_client: Any | None = None,
        compute_client: Any | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.notifier = notifier
        self.sdk_config = sdk_config
        self.identity_client = identity_client
        self.network_client = network_client
        self.compute_client = compute_client

    def _send_notification(self, message: str, event: str, description: str) -> None:
        if not self.notifier.enabled:
            return
        try:
            channels = self.notifier.send(message, event=event)
            self.state.add_log(f"{description} sent to " + ", ".join(channels))
        except NotificationError as exc:
            self.state.add_log(f"{description} could not be delivered: {exc}", "WARNING")

    def _prepare_clients(self) -> None:
        if all((self.sdk_config, self.identity_client, self.network_client, self.compute_client)):
            return
        clients = create_oci_clients(self.config)
        self.sdk_config, self.identity_client, self.network_client, self.compute_client = clients

    def _guard_existing_instance(self, compartment_id: str) -> None:
        assert self.compute_client is not None
        response = oci.pagination.list_call_get_all_results(
            self.compute_client.list_instances,
            compartment_id,
            display_name=self.config.instance_specs.display_name,
        )
        existing = [
            item for item in response.data
            if item.display_name == self.config.instance_specs.display_name
            and item.lifecycle_state != "TERMINATED"
        ]
        if existing:
            states = ", ".join(sorted({item.lifecycle_state for item in existing}))
            raise ExistingInstanceError(
                f"An instance named {self.config.instance_specs.display_name!r} already exists "
                f"({states}). Terminate or rename it before starting another launch."
            )

    def _launch_details(
        self,
        resources: DiscoveredResources,
        availability_domain: str,
        ssh_key: str,
    ) -> Any:
        specs = self.config.instance_specs
        metadata = {"ssh_authorized_keys": ssh_key}
        user_data = encoded_user_data(self.config)
        if user_data:
            metadata["user_data"] = user_data
        return oci.core.models.LaunchInstanceDetails(
            availability_domain=availability_domain,
            compartment_id=resources.compartment_id,
            display_name=specs.display_name,
            shape=specs.shape,
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=float(specs.ocpus),
                memory_in_gbs=float(specs.memory_in_gbs),
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                image_id=resources.image_id,
                boot_volume_size_in_gbs=specs.boot_volume_size_in_gbs,
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=resources.subnet_id,
                assign_public_ip=specs.assign_public_ip,
            ),
            metadata=metadata,
        )

    def _resolve_public_ip(self, instance_id: str, compartment_id: str) -> str | None:
        assert self.compute_client is not None
        assert self.network_client is not None
        for attempt in range(4):
            try:
                response = oci.pagination.list_call_get_all_results(
                    self.compute_client.list_vnic_attachments,
                    compartment_id,
                    instance_id=instance_id,
                )
                for attachment in response.data:
                    if not attachment.vnic_id:
                        continue
                    vnic = self.network_client.get_vnic(attachment.vnic_id).data
                    if getattr(vnic, "public_ip", None):
                        return vnic.public_ip
            except ServiceError as exc:
                self.state.add_log(f"Could not look up the instance public IP: {exc.code}", "WARNING")
                return None
            if attempt < 3:
                time.sleep(1.5)
        return None

    def _wait_for_running(self, instance: Any, ad_name: str) -> Any | None:
        assert self.compute_client is not None
        current = instance
        while True:
            lifecycle_state = str(getattr(current, "lifecycle_state", "UNKNOWN"))
            if lifecycle_state == "RUNNING":
                return current
            if lifecycle_state == "TERMINATED":
                message = (
                    f"Instance {current.id} entered TERMINATED before reaching RUNNING "
                    f"in {ad_name}."
                )
                self.state.update(status="Error", message=message)
                self.state.add_log(message, "ERROR")
                self._send_notification(
                    "OCI A1 Capacity Hunter: instance provisioning failed\n" + message,
                    event="instance_provisioning_failed",
                    description="Provisioning failure alert",
                )
                return None
            if self.state.is_shutdown_requested():
                self.state.update(
                    status="Stopped",
                    message="Stopped while waiting for the instance to reach RUNNING",
                )
                return None

            self.state.update(
                status="Provisioning",
                message=f"Instance state is {lifecycle_state}; waiting for RUNNING",
                instance_id=current.id,
            )
            interval = self.config.polling_policy.instance_status_poll_interval_seconds
            if not self.state.wait(interval):
                self.state.update(
                    status="Stopped",
                    message="Stopped while waiting for the instance to reach RUNNING",
                )
                return None
            try:
                current = self.compute_client.get_instance(current.id).data
            except ServiceError as exc:
                if exc.status not in (404, 429):
                    raise RuntimeError(
                        f"Could not check the launched instance state: HTTP {exc.status} ({exc.code})"
                    ) from exc
                wait_seconds = interval
                if exc.status == 429:
                    wait_seconds = min(
                        max(interval, self.config.polling_policy.rate_limit_backoff_seconds),
                        300,
                    )
                self.state.add_log(
                    f"Could not check the launched instance state ({exc.code}); "
                    f"retrying the status check in {wait_seconds}s",
                    "WARNING",
                )
                if not self.state.wait(wait_seconds):
                    self.state.update(
                        status="Stopped",
                        message="Stopped while waiting for the instance to reach RUNNING",
                    )
                    return None
                continue

    def _report_success(
        self,
        instance: Any,
        resources: DiscoveredResources,
        ad_name: str,
        attempt: int,
    ) -> None:
        self.state.update(
            status="Provisioning",
            message="OCI accepted the launch request; waiting for RUNNING",
            instance_id=instance.id,
        )
        self.state.add_log(
            f"Launch accepted in {ad_name}; waiting for instance {instance.id} to reach RUNNING"
        )
        self._send_notification(
            attempt_summary_message(
                attempt=attempt,
                availability_domain=ad_name,
                result="Launch accepted",
                detail=f"Instance OCID: {instance.id}. Waiting for RUNNING before the final alert.",
            ),
            event="launch_attempt_accepted",
            description=f"Attempt {attempt} summary",
        )

        instance = self._wait_for_running(instance, ad_name)
        if instance is None:
            return

        public_ip = self._resolve_public_ip(instance.id, resources.compartment_id)
        self.state.update(
            status="Provisioned",
            message="Instance reached RUNNING",
            instance_id=instance.id,
            public_ip=public_ip,
        )
        self.state.add_log(f"Instance reached RUNNING in {ad_name}; instance OCID: {instance.id}")
        if not self.notifier.enabled:
            self.state.add_log("No notification channel is enabled; success is available in the dashboard")
            return
        message = provisioning_message(
            display_name=self.config.instance_specs.display_name,
            instance_id=instance.id,
            lifecycle_state=instance.lifecycle_state,
            compartment_id=resources.compartment_id,
            availability_domain=ad_name,
            public_ip=public_ip,
        )
        self._send_notification(message, event="instance_provisioned", description="Final ready alert")

    def run(self) -> None:
        try:
            self.state.update(status="Initializing", message="Validating configuration and OCI access")
            ssh_key = read_ssh_public_key(self.config)
            self._prepare_clients()
            assert self.sdk_config is not None
            assert self.identity_client is not None
            assert self.network_client is not None
            assert self.compute_client is not None
            tenancy_id = str(self.sdk_config.get("tenancy", "")).strip()
            if not tenancy_id:
                raise ValueError("The selected OCI profile does not define a tenancy OCID")
            compartment_id = self.config.oci_profile.compartment_id.strip() or tenancy_id
            self._guard_existing_instance(compartment_id)

            self.state.update(status="Discovering", message="Resolving availability domains, subnet, and image")
            resources = discover_resources(
                self.config,
                self.sdk_config,
                self.identity_client,
                self.network_client,
                self.compute_client,
            )
            self.state.update(
                availability_domains=resources.availability_domains,
                status="Polling",
                message="Waiting for available A1 host capacity",
            )
            self.state.add_log(
                f"Using region {resources.region}, {len(resources.availability_domains)} availability domain(s), "
                f"shape {self.config.instance_specs.shape} ({self.config.instance_specs.ocpus} OCPU, "
                f"{self.config.instance_specs.memory_in_gbs} GB RAM)"
            )

            attempt = 0
            ad_index = 0
            throttle_count = 0
            policy = self.config.polling_policy
            while not self.state.is_shutdown_requested():
                if not self.state.wait_until_running():
                    break
                if policy.maximum_attempts and attempt >= policy.maximum_attempts:
                    self.state.update(status="Stopped", message="Maximum launch attempts reached")
                    self.state.add_log("Maximum launch attempts reached", "WARNING")
                    return

                ad_name = resources.availability_domains[ad_index]
                attempt += 1
                self.state.update(
                    attempts=attempt,
                    current_ad=ad_name,
                    last_attempt_at=utc_now(),
                    next_retry_at=None,
                    message=f"Launch attempt {attempt} in {ad_name}",
                    status="Polling",
                )
                self.state.add_log(f"Attempt {attempt}: requesting A1 capacity in {ad_name}")
                try:
                    details = self._launch_details(resources, ad_name, ssh_key)
                    response = self.compute_client.launch_instance(details)
                    self._report_success(response.data, resources, ad_name, attempt)
                    return
                except ServiceError as exc:
                    if exc.status == 429:
                        throttle_count += 1
                        multiplier = 2 ** min(throttle_count - 1, 20)
                        delay = min(
                            policy.rate_limit_backoff_seconds * multiplier,
                            policy.rate_limit_backoff_max_seconds,
                        )
                        server_delay = retry_after_seconds(exc)
                        if server_delay is not None:
                            delay = max(delay, server_delay)
                        delay += random.uniform(0, min(delay * 0.1, 30))
                        self.state.add_log(
                            f"OCI rate limit reached; waiting {delay:.0f}s before retrying",
                            "WARNING",
                        )
                    elif is_capacity_error(exc):
                        throttle_count = 0
                        delay = policy.standard_retry_interval_seconds + random.uniform(
                            -policy.jitter_seconds,
                            policy.jitter_seconds,
                        )
                        self.state.add_log(
                            f"No host capacity in {ad_name}; retrying in {delay:.0f}s",
                            "WARNING",
                        )
                        if len(resources.availability_domains) > 1:
                            ad_index = (ad_index + 1) % len(resources.availability_domains)
                    else:
                        raise RuntimeError(
                            f"OCI launch failed with HTTP {exc.status} ({exc.code}): {exc.message}"
                        ) from exc

                    retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    next_ad = resources.availability_domains[ad_index]
                    self.state.update(
                        status="Waiting",
                        message=f"Retrying after OCI response {exc.code}",
                        current_ad=resources.availability_domains[ad_index],
                        next_retry_at=retry_at.isoformat(timespec="seconds"),
                    )
                    if exc.status == 429:
                        result = "Rate limited"
                        detail = f"OCI returned HTTP 429 ({exc.code})."
                    else:
                        result = "No A1 capacity"
                        detail = f"OCI returned HTTP {exc.status} ({exc.code}) in {ad_name}."
                    self._send_notification(
                        attempt_summary_message(
                            attempt=attempt,
                            availability_domain=ad_name,
                            result=result,
                            detail=detail,
                            retry_delay_seconds=delay,
                            next_retry_at=retry_at.isoformat(timespec="seconds"),
                            next_availability_domain=next_ad if next_ad != ad_name else None,
                        ),
                        event="launch_attempt_failed",
                        description=f"Attempt {attempt} summary",
                    )
                    if not self.state.wait(delay):
                        break

            self.state.update(status="Stopped", message="Provisioning stopped")
        except ExistingInstanceError as exc:
            self.state.update(status="Stopped", message=str(exc))
            self.state.add_log(str(exc), "WARNING")
            if self.notifier.enabled:
                self._send_notification(
                    "OCI A1 Capacity Hunter stopped: an existing instance was found.\n" + str(exc),
                    event="existing_instance",
                    description="Existing-instance alert",
                )
        except Exception as exc:
            self.state.update(status="Error", message=str(exc), next_retry_at=None)
            self.state.add_log(f"Provisioner stopped: {exc}", "ERROR")
            self._send_notification(
                f"OCI A1 Capacity Hunter stopped with an error:\n{exc}",
                event="provisioning_error",
                description="Provisioning error alert",
            )
