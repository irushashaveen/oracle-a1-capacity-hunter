import unittest

from app.state import AppState


class AppStateTests(unittest.TestCase):
    def test_toggle_updates_running_status_and_log(self):
        state = AppState()
        self.assertFalse(state.toggle_running())
        snapshot = state.snapshot()
        self.assertFalse(snapshot["running"])
        self.assertEqual(snapshot["status"], "Paused")
        self.assertTrue(state.toggle_running())
        self.assertTrue(state.snapshot()["running"])

    def test_update_rejects_unknown_fields(self):
        with self.assertRaises(AttributeError):
            AppState().update(secret="value")


if __name__ == "__main__":
    unittest.main()
