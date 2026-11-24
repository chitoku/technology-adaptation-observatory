import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tao.replay import MAX_HTML_BYTES, MAX_SNAPSHOT_BYTES, replay

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "home.snapshot.json"


def states(result):
    return {row["signal_id"]: row["state"] for row in result["evidence"]}


class ReplayTests(unittest.TestCase):
    def replay_fixture(self, **changes):
        snapshot = json.loads(FIXTURE.read_bytes())
        snapshot.update(changes)
        return replay(json.dumps(snapshot).encode("utf-8"))

    def test_cli_reproducible_and_matches_expected_contract(self):
        command = [sys.executable, "-m", "tao", "replay", str(FIXTURE)]
        first = subprocess.check_output(command)
        self.assertEqual(first, subprocess.check_output(command))
        self.assertEqual(first, FIXTURE.with_name("home.observation.json").read_bytes())

    def test_capture_identity_changes_with_input_bytes(self):
        raw = FIXTURE.read_bytes()
        self.assertNotEqual(replay(raw)["observation_id"], replay(raw + b"\n")["observation_id"])

    def test_missing_signals_on_complete_capture(self):
        result = self.replay_fixture(
            final_url="http://airline.example/ja/",
            html="<html lang=' '><head><meta name='viewport' content='width=960'></head></html>",
        )
        self.assertEqual(set(states(result).values()), {"absent"})
        self.assertEqual({row["level"] for row in result["confidence"]["annotations"]}, {"direct"})

    def test_viewport_in_inert_markup_or_body_is_not_evidence(self):
        viewport = "<meta name='viewport' content='width=device-width'>"
        result = self.replay_fixture(html=(
            "<html lang='ja'><head>"
            f"<!-- {viewport} --><script>{viewport}</script><title>{viewport}</title>"
            f"<template><template>{viewport}</template></template>"
            f"<noscript>{viewport}</noscript></head><body>{viewport}</body></html>"
        ))
        self.assertEqual(states(result)["html.viewport_device_width"], "absent")

    def test_omitted_html_or_head_is_unknown(self):
        result = self.replay_fixture(html="<!doctype html><body>Content")
        self.assertEqual(states(result)["html.root_lang_declared"], "unknown")
        self.assertEqual(states(result)["html.viewport_device_width"], "unknown")

    def test_incomplete_or_non_html_bodies_only_support_transport(self):
        for body_kind, html in (("truncated_html", "<html lang='ja'>"), ("other", None), ("unavailable", None)):
            with self.subTest(body_kind=body_kind):
                result = self.replay_fixture(body_kind=body_kind, html=html)
                self.assertEqual(states(result), {
                    "transport.landed_https": "present",
                    "html.root_lang_declared": "unknown",
                    "html.viewport_device_width": "unknown",
                })
                self.assertEqual(result["evidence"][1]["reason"], body_kind)

    def test_blocked_failed_and_uncollected_never_turn_into_absence(self):
        for access, status in (("blocked", 403), ("blocked", 200), ("blocked", 429),
                               ("failed", 503), ("not_collected", None)):
            with self.subTest(access=access, status=status):
                result = self.replay_fixture(
                    access=access, http_status=status, body_kind="unavailable", html=None,
                    final_url=None if status is None else "https://airline.example/ja/",
                )
                self.assertEqual(set(states(result).values()), {"unknown"})
                self.assertEqual({row["reason"] for row in result["evidence"]}, {access})
                self.assertEqual({row["level"] for row in result["confidence"]["annotations"]}, {"unassessed"})

    def test_invalid_metadata_is_rejected(self):
        invalid = (
            {"requested_url": "https://airline.example/ja/?token=redacted"},
            {"requested_url": "https://person@airline.example/ja/"},
            {"requested_url": "HTTPS://airline.example/ja/"},
            {"final_url": "file:///tmp/local"},
            {"final_url": "https://airline.example/ja/#fragment"},
            {"final_url": "https://airline.example/ja/%zz"},
            {"captured_at": "2026-02-30T12:00:00Z"},
            {"http_status": True},
            {"http_status": 403},
            {"snapshot_schema_version": "2.0.0"},
            {"capture_profile": []},
            {"html": None},
            {"html": "\ud800"},
            {"html": "a" * (MAX_HTML_BYTES + 1)},
            {"body_kind": "other"},
            {"access": "blocked"},
            {"private_field": "sensitive"},
        )
        for index, change in enumerate(invalid):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.replay_fixture(**change)

    def test_invalid_input_exits_two_without_output_or_echoing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_bytes(b'{"private-token": 1, "private-token": 2}')
            result = subprocess.run(
                [sys.executable, "-m", "tao", "replay", str(path)],
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"duplicate JSON key", result.stderr)
        self.assertNotIn(b"private-token", result.stderr)

    def test_snapshot_size_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "2 MiB"):
            replay(b" " * (MAX_SNAPSHOT_BYTES + 1))
