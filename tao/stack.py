"""Inspect technology signatures in public HTML without executing scripts."""

import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_HTML_BYTES = 1024 * 1024
STACK_LAYER = "static_html"
STACK_SCHEMA_VERSION = "0.1.0"
STACK_RULESET_VERSION = "0.1.0"
MAX_EVIDENCE_PER_KIND = 3
RELEASE_LABEL = r"(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}"
VERSION_LABEL = re.compile(RELEASE_LABEL, re.ASCII)
JQUERY_SCRIPT = re.compile(rf"jquery(?:-({RELEASE_LABEL}))?(?:\.slim)?(?:\.min)?\.js", re.ASCII)
REACT_SCRIPT = re.compile(
    rf"react(?:-({RELEASE_LABEL}))?(?:\.(?:development|production))?(?:\.min)?\.js", re.ASCII,
)
REACT_DOM_SCRIPT = re.compile(
    rf"react-dom(?:-{RELEASE_LABEL})?(?:\.(?:development|production))?(?:\.min)?\.js", re.ASCII,
)
REACT_UMD = re.compile(
    rf"/(?:npm/)?react@({RELEASE_LABEL})/umd/react\.(?:development|production)(?:\.min)?\.js",
    re.ASCII,
)
REACT_CDNJS = re.compile(
    rf"/ajax/libs/react/({RELEASE_LABEL})/umd/react\.(?:development|production)(?:\.min)?\.js",
    re.ASCII,
)
SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
JS_TYPES = {
    "", "module", "text/javascript", "application/javascript", "text/ecmascript", "application/ecmascript",
}
INERT = {"template", "noscript", "textarea", "title", "style", "script"}
VERSIONED_EVIDENCE = {"jquery_script", "react_script", "react_cdn_release"}
EVIDENCE_KINDS = VERSIONED_EVIDENCE | {"next_pages_data", "next_static_script", "react_dom_script"}


def _reference(src):
    if not src or any(char.isspace() or char == "\\" for char in src):
        return None
    try:
        parsed = urlsplit(src)
    except ValueError:
        return None
    if parsed.scheme not in ("", "https", "http") or "@" in parsed.netloc:
        return None
    return parsed


def _next_page_data(value):
    try:
        data = json.loads(value)
    except (ValueError, RecursionError):
        return False
    return (
        isinstance(data, dict)
        and isinstance(data.get("buildId"), str) and bool(data["buildId"])
        and isinstance(data.get("page"), str) and data["page"].startswith("/")
        and isinstance(data.get("props"), dict)
    )


class StackParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.script_index = 0
        self.inert = []
        self.next_data = None
        self.signals = {}

    def _record(self, kind, release=None):
        by_label = self.signals.setdefault(kind, {})
        if len(by_label) < MAX_EVIDENCE_PER_KIND and release not in by_label:
            by_label[release] = {
                "kind": kind, "script_index": self.script_index, "version_label": release,
            }

    def _script_reference(self, src):
        url = _reference(src)
        if url is None:
            return
        basename = url.path.rsplit("/", 1)[-1]
        jquery = JQUERY_SCRIPT.fullmatch(basename)
        react = REACT_SCRIPT.fullmatch(basename)
        if jquery:
            self._record("jquery_script", jquery.group(1))
        if REACT_DOM_SCRIPT.fullmatch(basename):
            self._record("react_dom_script")
        if "/_next/static/" in url.path and basename.endswith(".js"):
            self._record("next_static_script")
        if react:
            release = None
            if url.netloc in ("unpkg.com", "cdn.jsdelivr.net"):
                release = REACT_UMD.fullmatch(url.path)
            elif url.netloc == "cdnjs.cloudflare.com":
                release = REACT_CDNJS.fullmatch(url.path)
            if release:
                self._record("react_cdn_release", release.group(1))
            else:
                self._record("react_script", react.group(1))

    def handle_starttag(self, tag, attrs):
        if self.inert:
            if self.inert[-1] in ("template", "noscript") and tag in INERT:
                self.inert.append(tag)
            return
        if tag != "script":
            if tag in INERT:
                self.inert.append(tag)
            return
        self.inert.append(tag)
        self.script_index += 1
        attrs = dict(reversed(attrs))
        media = (attrs.get("type") or "").partition(";")[0].strip().lower()
        if attrs.get("id") == "__NEXT_DATA__" and media == "application/json" and "src" not in attrs:
            self.next_data = []
        elif media in JS_TYPES:
            self._script_reference(attrs.get("src"))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self.next_data is not None:
            self.next_data.append(data)

    def handle_endtag(self, tag):
        if not self.inert or self.inert[-1] != tag:
            return
        self.inert.pop()
        if tag == "script" and self.next_data is not None:
            if _next_page_data("".join(self.next_data)):
                self._record("next_pages_data")
            self.next_data = None

    def evidence(self):
        return [row for kind in sorted(self.signals) for row in self.signals[kind].values()]


def _finding(technology, identity, evidence):
    versions = sorted({row["version_label"] for row in evidence if row["version_label"] is not None})
    version_status = "unknown"
    if len(versions) == 1:
        version_status = "labelled"
    elif versions:
        version_status = "multiple_labels"
    return {
        "technology": technology,
        "identity": identity,
        "version_status": version_status,
        "version_labels": versions,
        "evidence_kinds": sorted({row["kind"] for row in evidence}),
    }


def _findings(evidence):
    next_signals = [row for row in evidence if row["kind"] in {"next_pages_data", "next_static_script"}]
    next_inferred = len(next_signals) == 2
    react_refs = [row for row in evidence if row["kind"] in {"react_script", "react_cdn_release"}]
    react_dom = [row for row in evidence if row["kind"] == "react_dom_script"]
    if react_refs:
        react = _finding("react", "referenced", react_refs + react_dom)
    elif next_inferred or react_dom:
        react = _finding("react", "inferred", react_dom + (next_signals if next_inferred else []))
    else:
        react = _finding("react", "unknown", [])
    jquery = [row for row in evidence if row["kind"] == "jquery_script"]
    return [
        _finding("jquery", "referenced" if jquery else "unknown", jquery),
        _finding("nextjs", "inferred" if next_inferred else "unknown", next_signals),
        react,
    ]


def _report(evidence, digest):
    return {
        "schema_version": STACK_SCHEMA_VERSION,
        "layer": STACK_LAYER,
        "ruleset_version": STACK_RULESET_VERSION,
        "coverage": "complete_html" if digest else "unavailable",
        "source_sha256": digest,
        "evidence": evidence,
        "findings": _findings(evidence),
    }


def unavailable_stack():
    return _report([], None)


def inspect_stack(raw, *, html=None):
    """Return structural and labelled-version evidence for bounded HTML bytes."""
    if len(raw) > MAX_HTML_BYTES:
        raise ValueError("stack HTML exceeds 1 MiB")
    if html is None:
        try:
            html = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("stack input must be UTF-8 HTML") from error
    page = StackParser()
    try:
        page.feed(html)
        page.close()
    except (AssertionError, ValueError):
        return unavailable_stack()
    return _report(page.evidence(), hashlib.sha256(raw).hexdigest())


def validate_stack(report, body_kind):
    """Reject invalid or inconsistent stack evidence in a replayed capture."""
    fields = {"layer", "schema_version", "ruleset_version", "coverage", "source_sha256", "evidence", "findings"}
    if not isinstance(report, dict) or report.keys() != fields:
        raise ValueError("invalid technology stack fields")
    if (
        report["layer"] != STACK_LAYER or report["schema_version"] != STACK_SCHEMA_VERSION
        or report["ruleset_version"] != STACK_RULESET_VERSION
    ):
        raise ValueError("unsupported technology stack version")
    digest = report["source_sha256"]
    evidence = report["evidence"]
    if report["coverage"] == "unavailable":
        if report != unavailable_stack():
            raise ValueError("unavailable stack cannot contain evidence")
        return
    if (
        report["coverage"] != "complete_html" or body_kind != "complete_html"
        or not isinstance(digest, str) or not SHA256.fullmatch(digest) or not isinstance(evidence, list)
        or len(evidence) > len(EVIDENCE_KINDS) * MAX_EVIDENCE_PER_KIND
    ):
        raise ValueError("invalid technology stack coverage")
    seen = set()
    counts = {}
    for row in evidence:
        if not isinstance(row, dict) or row.keys() != {"kind", "script_index", "version_label"}:
            raise ValueError("invalid technology evidence fields")
        kind, index, release = row["kind"], row["script_index"], row["version_label"]
        if not isinstance(kind, str) or kind not in EVIDENCE_KINDS or type(index) is not int or index < 1:
            raise ValueError("invalid technology evidence locator")
        if release is not None and (
            kind not in VERSIONED_EVIDENCE or not isinstance(release, str) or not VERSION_LABEL.fullmatch(release)
        ):
            raise ValueError("invalid technology version label")
        if kind == "react_cdn_release" and release is None:
            raise ValueError("React CDN release evidence requires a version label")
        counts[kind] = counts.get(kind, 0) + 1
        if (kind, release) in seen or counts[kind] > MAX_EVIDENCE_PER_KIND:
            raise ValueError("duplicate or excessive technology evidence")
        seen.add((kind, release))
    if report["findings"] != _findings(evidence):
        raise ValueError("technology findings do not match their evidence")
