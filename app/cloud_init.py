"""Explicit user-provided cloud-init handling.

Cloud-init executes with elevated privileges on the new instance. This module only
encodes a file selected by the operator; it does not inject background load or
attempt to influence OCI idle-resource reclamation.
"""

from __future__ import annotations

import base64
from pathlib import Path

from app.config import AppConfig

MAX_USER_DATA_BYTES = 16 * 1024


def encoded_user_data(config: AppConfig) -> str | None:
    path_value = config.instance_specs.cloud_init_file
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"Configured cloud-init file not found: {path}")
    content = path.read_bytes()
    if not content.strip():
        raise ValueError(f"Configured cloud-init file is empty: {path}")
    if len(content) > MAX_USER_DATA_BYTES:
        raise ValueError(f"Cloud-init content must be {MAX_USER_DATA_BYTES} bytes or smaller")
    return base64.b64encode(content).decode("ascii")
