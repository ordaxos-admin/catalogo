from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from publish_r2 import cache_control_for, make_r2_client, publish


class FakeNotFound(Exception):
    def __init__(self) -> None:
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": 404},
            "Error": {"Code": "NoSuchKey"},
        }
        super().__init__("not found")


class FakeR2:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self.upload_calls: list[str] = []
        self.attempt_calls: list[str] = []
        self.fail_keys: set[str] = set()

    def upload_file(self, Filename: str, Bucket: str, Key: str, ExtraArgs: dict) -> None:  # noqa: N803 - boto3 signature
        self.attempt_calls.append(Key)
        if Key in self.fail_keys:
            raise RuntimeError("forced upload failure")
        body = Path(Filename).read_bytes()
        self.objects[(Bucket, Key)] = {
            "body": body,
            "Metadata": dict(ExtraArgs.get("Metadata", {})),
            "ContentLength": len(body),
            "ContentType": ExtraArgs.get("ContentType"),
            "CacheControl": ExtraArgs.get("CacheControl"),
        }
        self.upload_calls.append(Key)

    def head_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 - boto3 signature
        try:
            item = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise FakeNotFound() from exc
        return {
            "Metadata": item["Metadata"],
            "ContentLength": item["ContentLength"],
            "ContentType": item["ContentType"],
            "CacheControl": item["CacheControl"],
        }


class R2PublisherTests(unittest.TestCase):
    def make_bundle(self, root: Path) -> Path:
        bundle = root / "bundle" / "r2"
        media = bundle / "media" / "mdl_a" / "img_a"
        gallery = bundle / "gallery" / "mdl_a"
        media.mkdir(parents=True)
        gallery.mkdir(parents=True)
        (media / "card.webp").write_bytes(b"webp-card-v1")
        (media / "thumb.webp").write_bytes(b"webp-thumb-v1")
        (gallery / "0123456789abcdef01234567.json").write_text('{"version":1}\n', encoding="utf-8")
        return bundle

    def test_r2_client_forwards_temporary_session_token(self) -> None:
        with patch("boto3.client") as create_client:
            make_r2_client("acct", "access", "secret", "session-token")

        kwargs = create_client.call_args.kwargs
        self.assertEqual(kwargs["endpoint_url"], "https://acct.r2.cloudflarestorage.com")
        self.assertEqual(kwargs["aws_access_key_id"], "access")
        self.assertEqual(kwargs["aws_secret_access_key"], "secret")
        self.assertEqual(kwargs["aws_session_token"], "session-token")

    def test_r2_client_keeps_long_lived_credentials_compatible(self) -> None:
        with patch("boto3.client") as create_client:
            make_r2_client("acct", "access", "secret")

        self.assertIsNone(create_client.call_args.kwargs["aws_session_token"])

    def test_first_publish_uploads_and_second_run_skips_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.make_bundle(root)
            state = root / "state.json"
            client = FakeR2()

            first = publish(bundle, state, "bucket", client=client, workers=2)
            self.assertEqual(first["uploaded"], 3)
            self.assertEqual(first["skipped"], 0)
            self.assertEqual(first["errors"], [])
            self.assertTrue(state.is_file())

            calls = len(client.upload_calls)
            second = publish(bundle, state, "bucket", client=client, workers=2)
            self.assertEqual(second["uploaded"], 0)
            self.assertEqual(second["skipped"], 3)
            self.assertEqual(len(client.upload_calls), calls)

    def test_media_phase_finishes_before_gallery_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.make_bundle(root)
            state = root / "state.json"
            client = FakeR2()

            result = publish(bundle, state, "bucket", client=client, workers=1)

            self.assertEqual(result["errors"], [])
            self.assertEqual(client.upload_calls, [
                "media/mdl_a/img_a/card.webp",
                "media/mdl_a/img_a/thumb.webp",
                "gallery/mdl_a/0123456789abcdef01234567.json",
            ])

    def test_media_failure_defers_gallery_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.make_bundle(root)
            state = root / "state.json"
            client = FakeR2()
            failed_key = "media/mdl_a/img_a/card.webp"
            gallery_key = "gallery/mdl_a/0123456789abcdef01234567.json"
            client.fail_keys.add(failed_key)

            result = publish(bundle, state, "bucket", client=client, workers=1)

            self.assertEqual(result["uploaded"], 1)
            self.assertEqual(len(result["errors"]), 1)
            self.assertEqual(result["errors"][0]["key"], failed_key)
            self.assertEqual(result["deferred"], 1)
            self.assertNotIn(gallery_key, client.attempt_calls)
            self.assertNotIn(("bucket", gallery_key), client.objects)

    def test_only_changed_local_object_is_reuploaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.make_bundle(root)
            state = root / "state.json"
            client = FakeR2()
            publish(bundle, state, "bucket", client=client, workers=2)

            card = bundle / "media" / "mdl_a" / "img_a" / "card.webp"
            card.write_bytes(b"webp-card-v2-with-change")
            before = len(client.upload_calls)
            result = publish(bundle, state, "bucket", client=client, workers=2)

            self.assertEqual(result["uploaded"], 1)
            self.assertEqual(result["skipped"], 2)
            self.assertEqual(client.upload_calls[before:], ["media/mdl_a/img_a/card.webp"])

    def test_verify_remote_recovers_deleted_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.make_bundle(root)
            state = root / "state.json"
            client = FakeR2()
            publish(bundle, state, "bucket", client=client, workers=2)

            key = "media/mdl_a/img_a/thumb.webp"
            del client.objects[("bucket", key)]
            before = len(client.upload_calls)
            result = publish(bundle, state, "bucket", client=client, workers=2, verify_remote=True)

            self.assertEqual(result["uploaded"], 1)
            self.assertEqual(result["verified"], 2)
            self.assertEqual(client.upload_calls[before:], [key])

    def test_dry_run_needs_no_credentials_or_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.make_bundle(root)
            state = root / "state.json"
            result = publish(bundle, state, "bucket", client=None, dry_run=True)
            self.assertEqual(result["pending"], 3)
            self.assertGreater(result["pendingBytes"], 0)
            self.assertFalse(state.exists())

    def test_discovery_does_not_depend_on_pathlib_rglob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.make_bundle(root)
            state = root / 'state.json'

            with patch.object(Path, 'rglob', side_effect=AssertionError('rglob must not be used')):
                result = publish(bundle, state, 'bucket', client=None, dry_run=True)

            self.assertEqual(result['objects'], 3)
            self.assertEqual(result['pending'], 3)

    def test_cache_policy_distinguishes_content_addressed_manifests(self) -> None:
        self.assertIn("immutable", cache_control_for("gallery/mdl/hash.json"))
        self.assertNotIn("immutable", cache_control_for("media/mdl/img/card.webp"))


if __name__ == "__main__":
    unittest.main()
