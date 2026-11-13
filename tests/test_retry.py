import unittest

from oci.exceptions import ServiceError

from app.provisioner import is_capacity_error, retry_after_seconds


class RetryTests(unittest.TestCase):
    def test_identifies_capacity_response(self):
        error = ServiceError(500, "InternalError", {}, "Out of host capacity")
        self.assertTrue(is_capacity_error(error))

    def test_does_not_classify_other_client_error_as_capacity(self):
        error = ServiceError(403, "NotAuthorized", {}, "Not authorized")
        self.assertFalse(is_capacity_error(error))

    def test_capacity_message_on_non_server_error_is_not_retryable(self):
        error = ServiceError(400, "InvalidParameter", {}, "Out of host capacity")
        self.assertFalse(is_capacity_error(error))

    def test_reads_retry_after_seconds(self):
        error = ServiceError(429, "TooManyRequests", {"Retry-After": "240"}, "Slow down")
        self.assertEqual(retry_after_seconds(error), 240.0)

    def test_missing_retry_after_returns_none(self):
        error = ServiceError(429, "TooManyRequests", {}, "Slow down")
        self.assertIsNone(retry_after_seconds(error))


if __name__ == "__main__":
    unittest.main()
