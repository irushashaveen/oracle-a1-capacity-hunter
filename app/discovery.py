"""Resolve region-specific prerequisites through the OCI Python SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import oci

from app.config import AppConfig


class DiscoveryError(RuntimeError):
    """A prerequisite could not be discovered safely."""


@dataclass(frozen=True)
class DiscoveredResources:
    compartment_id: str
    tenancy_id: str
    availability_domains: list[str]
    subnet_id: str
    image_id: str
    region: str


def _first_public_subnet(network_client: Any, compartment_id: str) -> str:
    response = oci.pagination.list_call_get_all_results(
        network_client.list_subnets,
        compartment_id,
    )
    candidates = [
        subnet for subnet in response.data
        if getattr(subnet, "lifecycle_state", "") == "AVAILABLE"
        and not getattr(subnet, "prohibit_public_ip_on_vnic", True)
    ]
    candidates.sort(key=lambda subnet: (getattr(subnet, "display_name", ""), subnet.id))
    if not candidates:
        raise DiscoveryError(
            "No AVAILABLE subnet permitting public IP assignment was found. Create a subnet in "
            "the target compartment, confirm its route table and security rules, or set "
            "instance_specs.subnet_id explicitly. Network resources are not created automatically."
        )
    return candidates[0].id


def _latest_image(compute_client: Any, config: AppConfig, compartment_id: str) -> str:
    specs = config.instance_specs
    response = oci.pagination.list_call_get_all_results(
        compute_client.list_images,
        compartment_id,
        operating_system=specs.operating_system,
        operating_system_version=specs.os_version,
        shape=specs.shape,
        sort_by="TIMECREATED",
        sort_order="DESC",
    )
    images = [image for image in response.data if getattr(image, "lifecycle_state", "AVAILABLE") == "AVAILABLE"]
    images.sort(
        key=lambda image: (
            getattr(image, "time_created", None).isoformat()
            if hasattr(getattr(image, "time_created", None), "isoformat")
            else str(getattr(image, "time_created", None) or "")
        ),
        reverse=True,
    )
    if not images:
        raise DiscoveryError(
            f"No available image matched {specs.operating_system} {specs.os_version} for "
            f"{specs.shape} in this region. Set instance_specs.image_id explicitly or choose "
            "an OS version available in your region."
        )
    return images[0].id


def discover_resources(
    config: AppConfig,
    oci_config: dict[str, Any],
    identity_client: Any,
    network_client: Any,
    compute_client: Any,
) -> DiscoveredResources:
    tenancy_id = str(oci_config.get("tenancy", "")).strip()
    if not tenancy_id:
        raise DiscoveryError(
            "The selected OCI profile has no tenancy OCID. Check the tenancy value in your OCI config file."
        )
    compartment_id = config.oci_profile.compartment_id.strip() or tenancy_id

    ad_response = oci.pagination.list_call_get_all_results(
        identity_client.list_availability_domains,
        tenancy_id,
    )
    availability_domains = sorted({item.name for item in ad_response.data})
    if not availability_domains:
        raise DiscoveryError("OCI returned no availability domains for this tenancy and region.")

    specs = config.instance_specs
    subnet_id = specs.subnet_id.strip() or _first_public_subnet(network_client, compartment_id)
    image_id = specs.image_id.strip() or _latest_image(compute_client, config, compartment_id)
    if not config.polling_policy.cycle_availability_domains:
        availability_domains = availability_domains[:1]

    return DiscoveredResources(
        compartment_id=compartment_id,
        tenancy_id=tenancy_id,
        availability_domains=availability_domains,
        subnet_id=subnet_id,
        image_id=image_id,
        region=str(oci_config.get("region", "unknown")),
    )
