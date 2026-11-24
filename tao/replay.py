"""Derive evidence and confidence annotations from a local capture."""

import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from . import __version__

SNAPSHOT_SCHEMA_VERSION = "1.0.0"
OBSERVATION_SCHEMA_VERSION = "1.0.0"
EVIDENCE_RULESET_VERSION = "1.0.0"
CONFIDENCE_POLICY_VERSION = "1.0.0"
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_HTML_BYTES = 1024 * 1024

SNAPSHOT_FIELDS = {
    "snapshot_schema_version", "captured_at", "capture_profile", "requested_url",
    "final_url", "access", "http_status", "body_kind", "html",
}
UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
URL_PATH = re.compile(r"(?:[A-Za-z0-9._~!$&'()*+,;=:@/-]|%[0-9A-Fa-f]{2})+")
DEVICE_WIDTH = re.compile(r"(?:^|[,;])\s*width\s*=\s*device-width\s*(?=$|[,;])", re.ASCII)
INERT_TAGS = {"template", "script", "style", "title", "textarea", "noscript"}


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_url(value, schemes):
    if not isinstance(value, str) or any(
        char in "?#\\" or ord(char) <= 32 or ord(char) >= 127 for char in value
    ):
        raise ValueError("expected an ASCII HTTP(S) URL without query, fragment or credentials")
    url = urlsplit(value)
    if (
        url.scheme not in schemes
        or not value.startswith(f"{url.scheme}://")
        or not HOSTNAME.fullmatch(url.netloc)
        or not url.path.startswith("/")
        or not URL_PATH.fullmatch(url.path)
    ):
        raise ValueError("expected an HTTP(S) URL with a hostname and path, without a custom port")


def _read_snapshot(raw):
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 2 MiB")
    try:
        snapshot = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("snapshot must be a valid UTF-8 JSON document") from error

    if (
        not isinstance(snapshot, dict)
        or snapshot.keys() != SNAPSHOT_FIELDS
        or snapshot["snapshot_schema_version"] != SNAPSHOT_SCHEMA_VERSION
    ):
        raise ValueError("expected the exact snapshot fields for schema 1.0.0")
    timestamp = snapshot["captured_at"]
    if not isinstance(timestamp, str) or not UTC_TIMESTAMP.fullmatch(timestamp):
        raise ValueError("captured_at must be a UTC timestamp with second precision")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError("captured_at has an invalid date or time") from error

    if snapshot["capture_profile"] not in ("synthetic/1", "manual-public-http/1"):
        raise ValueError("unrecognized capture_profile")
    _validate_url(snapshot["requested_url"], ("https",))
    if snapshot["final_url"] is not None:
        _validate_url(snapshot["final_url"], ("http", "https"))
    status = snapshot["http_status"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise ValueError("http_status must be an integer between 100 and 599 or null")

    access = snapshot["access"]
    if access not in ("observed", "blocked", "failed", "not_collected"):
        raise ValueError("unrecognized access")
    if access == "observed" and (
        status is None or not 200 <= status < 300 or snapshot["final_url"] is None
    ):
        raise ValueError("observed requires a final URL and a 2xx HTTP status")
    if access == "not_collected" and (status is not None or snapshot["final_url"] is not None):
        raise ValueError("not_collected cannot include response metadata")

    body_kind = snapshot["body_kind"]
    if body_kind not in ("complete_html", "truncated_html", "other", "unavailable"):
        raise ValueError("unrecognized body_kind")
    if access != "observed" and body_kind != "unavailable":
        raise ValueError("only an observed page can include a response body")
    html = snapshot["html"]
    if body_kind in ("complete_html", "truncated_html"):
        if not isinstance(html, str):
            raise ValueError("HTML captures require an html string")
        try:
            html_size = len(html.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ValueError("html must contain valid Unicode") from error
        if html_size > MAX_HTML_BYTES:
            raise ValueError("html exceeds 1 MiB")
    elif html is not None:
        raise ValueError("html must be null when the body is unavailable or non-HTML")
    return snapshot


class PageDeclarations(HTMLParser):
    """Read explicit source declarations without constructing a browser DOM."""

    def __init__(self):
        super().__init__()
        self.root_language = None
        self.has_explicit_head = False
        self.in_head = False
        self.viewport_device_width = False
        self.inert = []

    def handle_starttag(self, tag, attrs):
        if self.inert:
            if self.inert[-1] == "template" and tag in INERT_TAGS:
                self.inert.append(tag)
            return
        if tag in INERT_TAGS:
            self.inert.append(tag)
            return

        attributes = dict(reversed(attrs))
        if tag == "html" and self.root_language is None:
            self.root_language = bool((attributes.get("lang") or "").strip())
        elif tag == "head" and not self.has_explicit_head:
            self.has_explicit_head = True
            self.in_head = True
        elif tag == "body":
            self.in_head = False
        elif self.in_head and tag == "meta" and (attributes.get("name") or "").lower() == "viewport":
            content = (attributes.get("content") or "").lower()
            self.viewport_device_width |= bool(DEVICE_WIDTH.search(content))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.inert:
            if tag == self.inert[-1]:
                self.inert.pop()
            return
        if tag == "head":
            self.in_head = False


def _evidence(signal_id, locator, value, reason):
    state = "unknown" if value is None else ("present" if value else "absent")
    return {
        "signal_id": signal_id,
        "locator": locator,
        "state": state,
        "reason": reason,
    }


def _derive_evidence(snapshot):
    access = snapshot["access"]
    body_kind = snapshot["body_kind"]
    https = language = viewport = None
    transport_reason = access
    language_reason = viewport_reason = access if access != "observed" else body_kind
    if access == "observed":
        https = urlsplit(snapshot["final_url"]).scheme == "https"
        transport_reason = "final_url_observed"
        if body_kind == "complete_html":
            page = PageDeclarations()
            page.feed(snapshot["html"])
            page.close()
            language = page.root_language
            language_reason = "complete_html" if language is not None else "no_explicit_html_element"
            viewport = page.viewport_device_width if page.has_explicit_head else None
            viewport_reason = "complete_html" if page.has_explicit_head else "no_explicit_head_element"
    return [
        _evidence("transport.landed_https", "/final_url", https, transport_reason),
        _evidence("html.root_lang_declared", "/html#html[lang]", language, language_reason),
        _evidence("html.viewport_device_width", "/html#head/meta[viewport]", viewport, viewport_reason),
    ]


def replay(raw):
    """Return a deterministic observation for the given UTF-8 JSON bytes."""
    snapshot = _read_snapshot(raw)
    capture_hash = hashlib.sha256(raw).hexdigest()
    evidence = _derive_evidence(snapshot)
    identity = f"{OBSERVATION_SCHEMA_VERSION}:{EVIDENCE_RULESET_VERSION}:{capture_hash}"
    return {
        "observation_id": "sha256:" + hashlib.sha256(identity.encode("ascii")).hexdigest(),
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "producer_version": __version__,
        "evidence_ruleset_version": EVIDENCE_RULESET_VERSION,
        "capture": {
            key: snapshot[key] for key in sorted(SNAPSHOT_FIELDS - {"html"})
        },
        "source_sha256": capture_hash,
        "evidence": evidence,
        "confidence": {
            "policy_version": CONFIDENCE_POLICY_VERSION,
            "annotations": [
                {
                    "signal_id": row["signal_id"],
                    "level": "unassessed" if row["state"] == "unknown" else "direct",
                }
                for row in evidence
            ],
        },
    }
