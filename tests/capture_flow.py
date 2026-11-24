"""Exercise the capture policy against synthetic responses and a loopback server."""

import contextlib
import http.client
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

from jsonschema import Draft202012Validator, FormatChecker

from tao.__main__ import main
from tao.capture import MAX_HTML_BYTES, ROBOTS_BYTES, USER_AGENT, Response, _read_body, capture
from tao.replay import replay

ROOT = Path(__file__).resolve().parents[1]
HOST = "https://airline.example.com"
ROBOTS = HOST + "/robots.txt"
HOME = HOST + "/en/"
TITLE = "Example Airline"
HTML = (
    b'<!doctype html><html lang="en"><head><title>Example Airline</title>'
    b'<meta name="viewport" content="width=device-width"></head>'
    b'<body><a href="/?private-query=unpublished#private-fragment">Secret body text</a>'
    b'<script>const secret = "private-credential";</script></body></html>'
)
ALLOW = Response(200, media_type="text/plain", body=b"User-agent: *\nAllow: /\n")
DOCUMENT = Response(200, media_type="text/html", body=HTML)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.manifest = self.directory / "manifest.json"
        self.target = {
            "target_id": "airline-en-home",
            "url": HOME,
            "market": "US",
            "locale": "en-US",
            "terms_url": HOST + "/terms",
            "robots_url": ROBOTS,
            "admission": "approved",
            "review": {
                "reviewed_on": "2025-01-01",
                "scope": "single_get_and_local_projection",
                "expected_title": TITLE,
                "redirect_urls": [HOST + "/one", HOST + "/two", HOST + "/three"],
            },
        }

    def write_manifest(self):
        self.manifest.write_text(json.dumps({
            "manifest_version": "0.2.0",
            "surface": "consumer_homepage",
            "targets": [self.target],
        }), encoding="utf-8")

    def run_capture(self, *responses):
        self.write_manifest()
        request = Mock(side_effect=responses)
        return capture("airline-en-home", self.manifest, "US", request), request

    def assert_reason(self, snapshot, access, reason):
        self.assertEqual(snapshot["access"], access)
        self.assertEqual(snapshot["capture_context"]["reason"], reason)

    def test_pilot_candidates_never_contact_the_network(self):
        manifest = ROOT / "pilot" / "airlines-v0.2.0.json"
        request = Mock()
        for target_id, reason in (
            ("ana-ja-home", "pending_review"), ("jal-ja-home", "pending_review"),
            ("delta-en-home", "candidate"), ("american-en-home", "permission_required"),
        ):
            with self.subTest(target=target_id):
                snapshot = capture(target_id, manifest, "US", request)
                self.assert_reason(snapshot, "not_collected", reason)
                self.assertIsNone(snapshot["html"])
        with self.assertRaisesRegex(ValueError, "exactly once"):
            capture("unknown-home", manifest, "US", request)
        request.assert_not_called()

    def test_missing_invalid_or_cross_origin_review_cannot_trigger_requests(self):
        request = Mock()
        valid_review = self.target["review"].copy()
        for review in (
            None,
            dict(valid_review, reviewed_on="2025-02-30"),
            dict(valid_review, redirect_urls=["https://other.example.com/en/"]),
            dict(valid_review, redirect_urls=[HOME + "?private-query=secret"]),
        ):
            with self.subTest(review=review):
                self.target["review"] = review
                self.write_manifest()
                with self.assertRaises(ValueError):
                    capture("airline-en-home", self.manifest, "US", request)
        request.assert_not_called()

    def test_robots_denial_and_unavailable_robots_never_fetch_target(self):
        for robots, reason in (
            (Response(200, media_type="text/plain", body=b"User-agent: *\nDisallow: /en*$\n"),
             "robots_disallowed"),
            (Response(200, media_type="text/plain", body=b"User-agent: *\nCrawl-delay: 10\n"),
             "robots_schedule_present"),
            (Response(200, media_type="text/plain", body=b"\xff"), "robots_unavailable"),
            (Response(200, media_type="text/plain", body=b"Temporarily unavailable"), "robots_unavailable"),
            (Response(200, media_type="text/plain", truncated=True), "robots_unavailable"),
            (Response(200, media_type="text/html"), "robots_unavailable"),
            (Response(429), "robots_unavailable"),
            (Response(503), "robots_unavailable"),
            (Response(403), "robots_unavailable"),
            (TimeoutError(), "robots_unavailable"),
        ):
            with self.subTest(reason=reason, status=getattr(robots, "status", None)):
                snapshot, request = self.run_capture(robots)
                self.assert_reason(snapshot, "not_collected", reason)
                request.assert_called_once_with(ROBOTS, "en-US", "text/plain", ROBOTS_BYTES)

    def test_missing_robots_allows_one_reviewed_page_request(self):
        for status in (404, 410):
            with self.subTest(status=status):
                snapshot, request = self.run_capture(Response(status), DOCUMENT)
                self.assert_reason(snapshot, "observed", "page_verified")
                self.assertEqual(snapshot["capture_context"]["robots_status"], status)
                self.assertEqual([args[0] for args, _ in request.call_args_list], [ROBOTS, HOME])

    def test_best_effort_attempts_exact_page_when_robots_endpoint_rejects_request(self):
        self.target["review"]["robots_policy"] = "best_effort"
        for status in (403, 444):
            with self.subTest(status=status):
                snapshot, request = self.run_capture(Response(status), Response(302, location="/one"))
                self.assert_reason(snapshot, "failed", "redirect_not_approved")
                self.assertEqual(snapshot["capture_context"]["robots_status"], status)
                self.assertEqual(snapshot["capture_context"]["robots_policy"], "best_effort")
                self.assertEqual([args[0] for args, _ in request.call_args_list], [ROBOTS, HOME])
        for robots, reason in (
            (Response(429), "robots_unavailable"),
            (Response(503), "robots_unavailable"),
            (Response(200, media_type="text/plain", body=b"User-agent: *\nDisallow: /en/\n"),
             "robots_disallowed"),
        ):
            with self.subTest(status=robots.status):
                snapshot, request = self.run_capture(robots)
                self.assert_reason(snapshot, "not_collected", reason)
                request.assert_called_once_with(ROBOTS, "en-US", "text/plain", ROBOTS_BYTES)

    def test_parseable_robots_rules_survive_an_unrelated_malformed_line(self):
        rules = b"User-agent: *\nThis line is malformed\nDisallow: /en/\n"
        snapshot, request = self.run_capture(Response(200, media_type="text/plain", body=rules))
        self.assert_reason(snapshot, "not_collected", "robots_disallowed")
        request.assert_called_once_with(ROBOTS, "en-US", "text/plain", ROBOTS_BYTES)

    def test_approved_redirect_is_checked_against_robots(self):
        robots = Response(200, media_type="text/plain", body=b"User-agent: *\nDisallow: /one\n")
        snapshot, request = self.run_capture(robots, Response(302, location="/one"))
        self.assert_reason(snapshot, "not_collected", "robots_disallowed")
        self.assertEqual([args[0] for args, _ in request.call_args_list], [ROBOTS, HOME])

    def test_unapproved_or_dirty_redirect_is_never_requested_or_persisted(self):
        for location in (
            "/unknown", "https://other.example.com/one", "/one?private-query=secret",
            "/one#private-fragment", "https://user:secret@airline.example.com/one",
            "https://[broken", "http://airline.example.com/one", HOME,
        ):
            with self.subTest(location=location):
                snapshot, request = self.run_capture(ALLOW, Response(302, location=location))
                self.assert_reason(snapshot, "failed", "redirect_not_approved")
                self.assertEqual([args[0] for args, _ in request.call_args_list], [ROBOTS, HOME])
                self.assertEqual(snapshot["final_url"], HOME)
                self.assertNotIn("private-", json.dumps(snapshot))

    def test_two_approved_redirects_and_hop_limit(self):
        first = Response(301, location="/one")
        second = Response(308, location=HOST + "/two")
        snapshot, request = self.run_capture(ALLOW, first, second, DOCUMENT)
        self.assert_reason(snapshot, "observed", "page_verified")
        self.assertEqual(request.call_args_list, [
            call(ROBOTS, "en-US", "text/plain", ROBOTS_BYTES),
            call(HOME, "en-US", "text/html", MAX_HTML_BYTES),
            call(HOST + "/one", "en-US", "text/html", MAX_HTML_BYTES),
            call(HOST + "/two", "en-US", "text/html", MAX_HTML_BYTES),
        ])
        snapshot, request = self.run_capture(ALLOW, first, second, Response(307, location="/three"))
        self.assert_reason(snapshot, "failed", "redirect_limit")
        self.assertEqual(request.call_count, 4)

    def test_http_and_content_failures_do_not_support_evidence(self):
        for response, access, reason in (
            (Response(403), "blocked", "http_access_denied"),
            (Response(429), "blocked", "http_access_denied"),
            (Response(503), "failed", "http_error"),
            (TimeoutError(), "failed", "network_error"),
            (Response(200, media_type="application/json"), "failed", "non_html"),
            (Response(200, media_type="text/html", identity_encoding=False), "failed", "unsupported_encoding"),
            (Response(200, media_type="text/html", body=b"\xff"), "failed", "unsupported_encoding"),
            (Response(200, media_type="text/html", body=b"<head><title>Consent</title></head>"),
             "failed", "page_not_verified"),
        ):
            with self.subTest(reason=reason, status=getattr(response, "status", None)):
                snapshot, request = self.run_capture(ALLOW, response)
                self.assert_reason(snapshot, access, reason)
                self.assertEqual(request.call_count, 2)
                self.assertIsNone(snapshot["html"])
                evidence = replay(json.dumps(snapshot).encode())["evidence"]
                self.assertTrue(all(row["state"] == "unknown" for row in evidence))

    def test_truncated_response_only_supports_transport_after_title_verification(self):
        response = Response(200, media_type="text/html", body=HTML, truncated=True)
        snapshot, _ = self.run_capture(ALLOW, response)
        self.assert_reason(snapshot, "observed", "html_truncated")
        self.assertEqual(snapshot["html"], "")
        evidence = replay(json.dumps(snapshot).encode())["evidence"]
        self.assertEqual([row["state"] for row in evidence], ["present", "unknown", "unknown"])
        snapshot, _ = self.run_capture(ALLOW, Response(
            200, media_type="text/html", body=b"<html><head><title>Example", truncated=True,
        ))
        self.assert_reason(snapshot, "failed", "page_not_verified")

    def test_body_reader_caps_bytes_and_detects_short_responses(self):
        for payload, length, limit, expected in (
            (b"abcdef", 6, 4, (b"abcd", True)),
            (b"abc", 6, 8, (b"abc", True)),
            (b"abc", 3, 3, (b"abc", False)),
        ):
            with self.subTest(length=length, limit=limit):
                response = io.BytesIO(payload)
                response.length = length
                self.assertEqual(_read_body(response, limit), expected)
                self.assertLessEqual(response.tell(), limit + 1)
        response = io.BytesIO(b"body")
        response.length = 4
        with patch("tao.capture.monotonic", side_effect=[0, 11]), self.assertRaises(TimeoutError):
            _read_body(response, 4)

    def test_cli_capture_then_replay_using_local_fixture_server(self):
        requests = []

        class FixtureHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers))
                body, media = {
                    "/robots.txt": (ALLOW.body, "text/plain"),
                    "/en/": (HTML, "text/html; charset=utf-8"),
                }[self.path]
                self.send_response(200)
                self.send_header("Content-Type", media)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Set-Cookie", "secret-cookie=fixture")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.target["review"]["redirect_urls"] = []
        self.write_manifest()
        out = self.directory / "capture.json"
        argv = [
            "capture", "airline-en-home", "--manifest", str(self.manifest),
            "--region", "US", "--out", str(out),
        ]
        with HTTPServer(("127.0.0.1", 0), FixtureHandler) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                with patch("tao.capture.http.client.HTTPSConnection", side_effect=lambda host, timeout: (
                    http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=timeout)
                )) as connection:
                    self.assertEqual(main(argv), 0)
                    self.assertEqual(connection.call_count, 2)
                    self.assertTrue(all(args == ("airline.example.com",) for args, _ in connection.call_args_list))
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                        main(argv)
                    self.assertEqual(error.exception.code, 2)
                    self.assertEqual(connection.call_count, 2)
            finally:
                server.shutdown()
                worker.join()

        self.assertEqual([path for path, _ in requests], ["/robots.txt", "/en/"])
        for _, headers in requests:
            self.assertEqual(headers["User-Agent"], USER_AGENT)
            self.assertEqual(headers["Accept-Language"], "en-US")
            self.assertNotIn("Cookie", headers)
            self.assertNotIn("Authorization", headers)
        raw = out.read_bytes()
        self.assert_reason(json.loads(raw), "observed", "page_verified")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(main(["replay", str(out)]), 0)
        observation = json.loads(printed.getvalue())
        self.assertEqual([row["state"] for row in observation["evidence"]], ["present"] * 3)
        self.assertEqual(observation["observation_schema_version"], "1.1.0")
        self.assertEqual(observation["capture"]["capture_context"]["region"], "US")
        schema = json.loads((ROOT / "schemas" / "observation-v1.1.0.schema.json").read_bytes())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(observation)
        observation_file = self.directory / "observed.json"
        observation_file.write_text(printed.getvalue(), encoding="utf-8")
        evaluated = io.StringIO()
        with contextlib.redirect_stdout(evaluated):
            self.assertEqual(main(["evaluate", str(observation_file), "--rubric-version", "0.1.0"]), 0)
        assessment = json.loads(evaluated.getvalue())
        self.assertEqual(assessment["result"], "met")
        exported = io.StringIO()
        with contextlib.redirect_stdout(exported):
            self.assertEqual(main([
                "export", "--manifest", str(self.manifest), "--rubric-version", "0.1.0", str(observation_file),
            ]), 0)
        data, summary = [json.loads(line) for line in exported.getvalue().splitlines()]
        self.assertEqual(data["assessment"], assessment)
        self.assertEqual(summary["valid_observation_count"], 1)
        for marker in (b"secret-cookie", b"private-query", b"private-fragment", b"private-credential",
                       b"Secret body text", TITLE.encode()):
            self.assertNotIn(marker, raw)
            self.assertNotIn(marker, printed.getvalue().encode())
            self.assertNotIn(marker, exported.getvalue().encode())
