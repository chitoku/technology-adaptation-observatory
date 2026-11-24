import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tao.stack import MAX_HTML_BYTES, inspect_stack, unavailable_stack, validate_stack

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "stack.html"


def findings(report):
    return {row["technology"]: row for row in report["findings"]}


class StackTests(unittest.TestCase):
    def test_cli_classifies_libraries_and_drops_source_content(self):
        command = [sys.executable, "-m", "tao", "stack", str(FIXTURE)]
        output = subprocess.check_output(command)
        self.assertEqual(output, subprocess.check_output(command))
        report = json.loads(output)
        validate_stack(report, "complete_html")
        rows = findings(report)
        self.assertEqual(rows["nextjs"]["identity"], "inferred")
        self.assertEqual(rows["react"]["identity"], "inferred")
        self.assertEqual(rows["react"]["version_status"], "unknown")
        self.assertEqual(rows["jquery"]["identity"], "referenced")
        self.assertEqual(rows["jquery"]["version_labels"], ["3.7.1"])
        self.assertEqual(rows["jquery"]["version_status"], "labelled")
        self.assertEqual(report["source_sha256"], hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        self.assertNotIn(b"fixture-private", output)
        self.assertNotIn(b"fixture-build", output)

    def test_version_references_distinguish_react_from_react_dom_and_query_values(self):
        report = inspect_stack(
            b'<script src="https://unpkg.com/react@18.2.0/umd/react.production.min.js"></script>'
            b'<script type="module" src="/react-17.0.2.js"></script>'
            b'<script src="/react-dom-19.0.0.production.min.js"></script>'
            b'<script src="/jquery.min.js?v=9.2.1"></script>'
        )
        rows = findings(report)
        self.assertEqual(rows["react"]["identity"], "referenced")
        self.assertEqual(rows["react"]["version_labels"], ["17.0.2", "18.2.0"])
        self.assertEqual(rows["react"]["version_status"], "multiple_labels")
        self.assertEqual(rows["jquery"]["identity"], "referenced")
        self.assertEqual(rows["jquery"]["version_status"], "unknown")

    def test_react_dom_without_react_version_is_only_inferred(self):
        report = inspect_stack(b'<script src="/react-dom-18.2.0.production.min.js"></script>')
        react = findings(report)["react"]
        self.assertEqual(react["identity"], "inferred")
        self.assertEqual(react["version_labels"], [])

    def test_one_next_marker_and_generic_bundles_leave_identity_unknown(self):
        for html in (
            b'<script src="/_next/static/chunks/main.js"></script>',
            b'<script id="__NEXT_DATA__" type="application/json">{"page":"/","buildId":"x","props":{}}</script>',
            b'<script id="__NEXT_DATA__" type="application/json">{"example":true}</script>'
            b'<script src="/main.abcd1234.js"></script>',
        ):
            with self.subTest(html=html):
                report = inspect_stack(html)
                self.assertTrue(all(row["identity"] == "unknown" for row in report["findings"]))

    def test_inert_markup_and_non_executable_script_types_do_not_count(self):
        report = inspect_stack(
            b'<!-- <script src="/react-18.2.0.js"></script> -->'
            b'<template><template><script src="/jquery-3.7.1.js"></script></template></template>'
            b'<noscript><script src="/jquery-3.6.0.js"></script></noscript>'
            b'<script src="/react-18.2.0.js" type="application/json"></script>'
            b'<script src="javascript:/jquery-3.7.1.js"></script>'
            b'<script src="/jquery.js.map"></script>'
            b'<script src="/jquery-migrate-3.4.0.js"></script>'
        )
        self.assertEqual(report["evidence"], [])

    def test_anonymous_and_malformed_cdn_references_do_not_assert_a_release(self):
        report = inspect_stack(
            b'<script src="//unpkg.com/react@18/umd/react.production.min.js"></script>'
            b'<script src="https://unpkg.com@wrong.example/react@17.0.2/umd/react.production.min.js"></script>'
        )
        react = findings(report)["react"]
        self.assertEqual(react["identity"], "referenced")
        self.assertEqual(react["version_status"], "unknown")

    def test_unavailable_and_invalid_evidence_are_checked(self):
        validate_stack(unavailable_stack(), "truncated_html")
        stack = inspect_stack(FIXTURE.read_bytes())
        with self.assertRaisesRegex(ValueError, "coverage"):
            validate_stack(stack, "unavailable")
        stack["findings"][0]["version_labels"] = ["9.9.9"]
        with self.assertRaisesRegex(ValueError, "do not match"):
            validate_stack(stack, "complete_html")

    def test_input_size_and_encoding_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "exceeds 1 MiB"):
            inspect_stack(b" " * (MAX_HTML_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            inspect_stack(b"\xff")
