import unittest

from fastapi.testclient import TestClient

from app.config import AppConfig
from app.state import AppState
from app.web.server import create_app


class FakeProvisioner:
    def run(self):
        return None


class FakeNotifier:
    def send_test(self):
        return ["Test channel"]


class DashboardTests(unittest.TestCase):
    def test_dashboard_status_toggle_and_test_alert(self):
        state = AppState()
        app = create_app(state, FakeProvisioner(), FakeNotifier())
        with TestClient(app) as client:
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("OCI A1 Capacity Hunter", page.text)
            self.assertEqual(client.get("/api/status").json()["status"], "Starting")
            self.assertFalse(client.post("/api/toggle").json()["running"])
            alert = client.post("/api/test-alert")
            self.assertEqual(alert.status_code, 200)
            self.assertEqual(alert.json()["channels"], ["Test channel"])


if __name__ == "__main__":
    unittest.main()
