from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from discover_external_staging_candidates import request_json


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


class RequestJsonRetryTests(unittest.TestCase):
    @patch("discover_external_staging_candidates.time.sleep", return_value=None)
    @patch("discover_external_staging_candidates.urllib.request.urlopen")
    def test_retries_anonymous_transient_403_then_succeeds(self, urlopen, _sleep):
        urlopen.side_effect = [
            urllib.error.HTTPError("https://source.test/api", 403, "Forbidden", None, io.BytesIO()),
            _Response({"ok": True}),
        ]
        self.assertEqual(request_json("https://source.test/api"), {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        _sleep.assert_called_once_with(1)

    @patch("discover_external_staging_candidates.time.sleep", return_value=None)
    @patch("discover_external_staging_candidates.urllib.request.urlopen")
    def test_authenticated_403_fails_closed_without_retry(self, urlopen, sleep):
        urlopen.side_effect = urllib.error.HTTPError(
            "https://api.cloudflare.com/client/v4/test", 403, "Forbidden", None, io.BytesIO()
        )
        with self.assertRaises(urllib.error.HTTPError):
            request_json("https://api.cloudflare.com/client/v4/test", token="secret")
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    @patch("discover_external_staging_candidates.time.sleep", return_value=None)
    @patch("discover_external_staging_candidates.urllib.request.urlopen")
    def test_retries_network_failure_then_succeeds(self, urlopen, _sleep):
        urlopen.side_effect = [
            urllib.error.URLError("temporary"),
            _Response([1, 2, 3]),
        ]
        self.assertEqual(request_json("https://source.test/api"), [1, 2, 3])
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
