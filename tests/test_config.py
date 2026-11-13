import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.config import AppConfig, load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_are_conservative(self):
        config = AppConfig()
        self.assertEqual(config.instance_specs.ocpus, 1)
        self.assertEqual(config.instance_specs.memory_in_gbs, 6)
        self.assertFalse(config.notifications.telegram.enabled)

    def test_rejects_allocations_above_current_free_limits(self):
        with self.assertRaises(ValidationError):
            AppConfig(instance_specs={"ocpus": 3})
        with self.assertRaises(ValidationError):
            AppConfig(instance_specs={"memory_in_gbs": 13})

    def test_environment_values_override_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "config.json"
            config_file.write_text(json.dumps({"instance_specs": {"ocpus": 1}}), encoding="utf-8")
            with patch.dict(os.environ, {"OCI_A1__INSTANCE_SPECS__OCPUS": "2"}):
                config = load_config(config_file)
        self.assertEqual(config.instance_specs.ocpus, 2)

    def test_enabled_telegram_requires_credentials(self):
        with self.assertRaises(ValidationError):
            AppConfig(notifications={"telegram": {"enabled": True}})

    def test_rejects_jitter_equal_to_retry_interval(self):
        with self.assertRaises(ValidationError):
            AppConfig(polling_policy={"standard_retry_interval_seconds": 8, "jitter_seconds": 8})


if __name__ == "__main__":
    unittest.main()
