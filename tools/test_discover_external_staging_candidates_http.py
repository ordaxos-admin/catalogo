from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from discover_external_staging_candidates import fetch_source_image, request_json


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size=-1):
        body, self._body = self._body, b""
        return body


class _BinaryResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size=-1):
        body, self._body = self._body, b""
        return body


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
        urlopen.side_effect = [urllib.error.URLError("temporary"), _Response([1, 2, 3])]
        self.assertEqual(request_json("https://source.test/api"), [1, 2, 3])
        self.assertEqual(urlopen.call_count, 2)


class SourceImageRetryTests(unittest.TestCase):
    @patch("discover_external_staging_candidates.time.sleep", return_value=None)
    @patch("discover_external_staging_candidates.urllib.request.urlopen")
    def test_retries_transient_image_403_and_preserves_exact_sha(self, urlopen, _sleep):
        body = b"exact-source-bytes"
        urlopen.side_effect = [
            urllib.error.HTTPError("https://source.test/a.webp", 403, "Forbidden", None, io.BytesIO()),
            _BinaryResponse(body),
        ]
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "a.webp"
            sha, size = fetch_source_image("https://source.test/a.webp", target, 1024, 30)
            self.assertEqual(sha, hashlib.sha256(body).hexdigest())
            self.assertEqual(size, len(body))
            self.assertEqual(target.read_bytes(), body)
        self.assertEqual(urlopen.call_count, 2)

    @patch("discover_external_staging_candidates.time.sleep", return_value=None)
    @patch("discover_external_staging_candidates.time.monotonic", side_effect=[0.0, 0.0, 91.0])
    @patch("discover_external_staging_candidates.urllib.request.urlopen")
    def test_total_image_transfer_deadline_fails_closed(self, urlopen, _monotonic, _sleep):
        urlopen.return_value = _BinaryResponse(b"partial")
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "slow.webp"
            with self.assertRaisesRegex(RuntimeError, "orçamento total"):
                fetch_source_image("https://source.test/slow.webp", target, 1024, 90)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
