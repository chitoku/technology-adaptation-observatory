"""Single-page capture for a locally reviewed public Web target."""

import codecs
import http.client
import json
import os
import platform
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import monotonic
from urllib.parse import urljoin

from .replay import (
    CAPTURE_SCHEMA_VERSION, LANGUAGE, MAX_HTML_BYTES, MAX_SNAPSHOT_BYTES,
    PUBLIC_HTTP_PROFILE, REGION, REVIEW_DATE, TARGET_ID, VERSION,
    PageDeclarations, read_json, validate_url,
)

PRODUCT_TOKEN = "TAOObservatory"
USER_AGENT = f"{PRODUCT_TOKEN}/1 (+https://github.com/chitoku/technology-adaptation-observatory)"
REQUEST_TIMEOUT = 10
ROBOTS_BYTES = 512 * 1024
MAX_REDIRECTS = 2
REDIRECT_STATUS = {301, 302, 303, 307, 308}
DENIED_STATUS = {401, 403, 429, 451}
MISSING_ROBOTS_STATUS = {404, 410}
UNAVAILABLE_ROBOTS_ENDPOINT_STATUS = {403, 444}
DEFERRED_ADMISSIONS = ("candidate", "pending_review", "permission_required")
HTML_CODECS = {"ascii", "cp1252", "cp932", "euc_jp", "iso8859-1", "shift_jis", "utf-8", "utf-8-sig"}
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
ROBOTS_DIRECTIVE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*[ \t]*:")


@dataclass(frozen=True)
class Response:
    status: int
    location: str = ""
    media_type: str = ""
    charset: str = "utf-8"
    identity_encoding: bool = True
    body: bytes = b""
    truncated: bool = False


def _read_body(response, limit):
    body = bytearray()
    declared_length = response.length
    deadline = monotonic() + REQUEST_TIMEOUT
    while len(body) <= limit:
        chunk = response.read1(min(65536, limit + 1 - len(body)))
        if monotonic() > deadline:
            raise TimeoutError("body read exceeded its time budget")
        if not chunk:
            break
        body.extend(chunk)
    truncated = len(body) > limit or (declared_length is not None and len(body) < declared_length)
    return bytes(body[:limit]), truncated


def get_document(url, language, media_type, limit):
    """Use a direct HTTPS connection with no automatic redirects or session state."""
    parsed = validate_url(url, ("https",))
    connection = http.client.HTTPSConnection(parsed.netloc, timeout=REQUEST_TIMEOUT)
    try:
        connection.request("GET", parsed.path, headers={
            "User-Agent": USER_AGENT,
            "Accept": media_type,
            "Accept-Language": language,
            "Accept-Encoding": "identity",
            "Connection": "close",
        })
        with connection.getresponse() as response:
            if not 100 <= response.status <= 599:
                raise http.client.HTTPException("unexpected HTTP status")
            kind = response.headers.get_content_type() if response.getheader("Content-Type") else ""
            charset = response.headers.get_content_charset() or "utf-8"
            coding = response.getheader("Content-Encoding") or "identity"
            identity = coding.strip().lower() == "identity"
            body, truncated = b"", False
            if response.status in (200, 206) and kind == media_type and identity:
                body, truncated = _read_body(response, limit)
            return Response(
                status=response.status,
                location=response.getheader("Location", ""),
                media_type=kind,
                charset=charset,
                identity_encoding=identity,
                body=body,
                truncated=truncated or response.status == 206,
            )
    finally:
        connection.close()


def _public_url(value):
    parsed = validate_url(value, ("https",))
    host = parsed.netloc
    labels = host.split(".")
    if (
        len(host) > 253
        or len(labels) < 2
        or not labels[-1][-1:].isalpha()
        or any(not DNS_LABEL.fullmatch(label) for label in labels)
        or host.endswith((".localhost", ".local", ".internal", ".onion"))
    ):
        raise ValueError("expected a public HTTPS hostname")
    return parsed


def _target_from_manifest(manifest_path, target_id):
    if not TARGET_ID.fullmatch(target_id):
        raise ValueError("invalid target ID")
    with manifest_path.open("rb") as stream:
        manifest = read_json(stream.read(MAX_SNAPSHOT_BYTES + 1), "manifest")
    if not isinstance(manifest, dict):
        raise ValueError("expected a manifest JSON object")
    manifest_version = manifest.get("manifest_version")
    targets = manifest.get("targets")
    if not isinstance(manifest_version, str) or not VERSION.fullmatch(manifest_version):
        raise ValueError("invalid manifest version")
    if manifest.get("surface") != "consumer_homepage" or not isinstance(targets, list):
        raise ValueError("expected a consumer_homepage manifest with a targets list")
    matches = [entry for entry in targets if isinstance(entry, dict) and entry.get("target_id") == target_id]
    if len(matches) != 1:
        raise ValueError("target ID must appear exactly once in the manifest")
    target = matches[0]
    admission = target.get("admission")
    if admission not in (*DEFERRED_ADMISSIONS, "approved"):
        raise ValueError("invalid target admission")
    locale = target.get("locale")
    if not isinstance(locale, str) or not LANGUAGE.fullmatch(locale):
        raise ValueError("invalid target locale")
    _public_url(target.get("url"))
    return manifest_version, target


def _approved_scope(target, now):
    base = _public_url(target["url"])
    robots_url = f"https://{base.netloc}/robots.txt"
    if target.get("robots_url") != robots_url:
        raise ValueError("robots_url must be /robots.txt on the target origin")
    _public_url(target.get("terms_url"))
    review = target.get("review")
    if not isinstance(review, dict) or review.get("scope") != "single_get_and_local_projection":
        raise ValueError("approved targets require a review covering one capture and local storage")
    reviewed_on = review.get("reviewed_on")
    if not isinstance(reviewed_on, str) or not REVIEW_DATE.fullmatch(reviewed_on):
        raise ValueError("approved targets require a review date")
    if date.fromisoformat(reviewed_on) > now.date():
        raise ValueError("review date cannot be in the future")
    title = review.get("expected_title")
    if not isinstance(title, str) or not title.strip() or len(title) > 512:
        raise ValueError("approved targets require a reviewed document title")
    redirects = review.get("redirect_urls")
    if target["url"] == robots_url or not isinstance(redirects, list) or any(
        _public_url(url).netloc != base.netloc or url == robots_url for url in redirects
    ):
        raise ValueError("redirect_urls must contain approved same-origin document URLs")
    robots_policy = review.get("robots_policy", "standard")
    if robots_policy not in ("standard", "best_effort"):
        raise ValueError("invalid robots_policy")
    return reviewed_on, " ".join(title.split()), frozenset(redirects), robots_policy


def _decode_html(response):
    charset = codecs.lookup(response.charset).name
    if charset not in HTML_CODECS:
        raise ValueError("unsupported HTML charset")
    codec = "utf-8-sig" if charset == "utf-8" else charset
    return codecs.getincrementaldecoder(codec)().decode(response.body, final=not response.truncated)


def _capture_page(snapshot, target, approved_redirects, expected_title, rules, request):
    url = target["url"]
    visited = set()
    for redirect_count in range(MAX_REDIRECTS + 1):
        if rules is not None and not rules.can_fetch(url, PRODUCT_TOKEN):
            return _record(snapshot, "not_collected", "robots_disallowed")
        visited.add(url)
        try:
            response = request(url, target["locale"], "text/html", MAX_HTML_BYTES)
        except (OSError, http.client.HTTPException):
            return _record(snapshot, "failed", "network_error")
        if response.status not in REDIRECT_STATUS:
            return _page_result(snapshot, url, response, expected_title)
        if redirect_count == MAX_REDIRECTS:
            return _record(snapshot, "failed", "redirect_limit", url, response.status)
        location = response.location
        try:
            destination = urljoin(url, location) if location and not any(char in location for char in "?#") else ""
        except ValueError:
            destination = ""
        if destination not in approved_redirects or destination in visited:
            return _record(snapshot, "failed", "redirect_not_approved", url, response.status)
        url = destination


def _page_result(snapshot, url, response, expected_title):
    def failure(access, reason):
        return _record(snapshot, access, reason, url, response.status)

    if response.status in DENIED_STATUS:
        return failure("blocked", "http_access_denied")
    if response.status not in (200, 206):
        return failure("failed", "http_error")
    if response.media_type != "text/html":
        return failure("failed", "non_html")
    if not response.identity_encoding:
        return failure("failed", "unsupported_encoding")
    try:
        html = _decode_html(response)
    except (LookupError, ValueError):
        return failure("failed", "unsupported_encoding")

    page = PageDeclarations()
    try:
        page.feed(html)
        page.close()
    except (AssertionError, ValueError):
        return failure("failed", "page_not_verified")
    if page.titles != [expected_title]:
        return failure("failed", "page_not_verified")
    if response.truncated:
        return _record(snapshot, "observed", "html_truncated", url, response.status, "truncated_html", "")
    return _record(
        snapshot, "observed", "page_verified", url, response.status,
        "complete_html", page.as_replay_html(),
    )


def _record(snapshot, access, reason, url=None, status=None, body_kind="unavailable", html=None):
    snapshot.update(access=access, final_url=url, http_status=status, body_kind=body_kind, html=html)
    snapshot["capture_context"]["reason"] = reason
    return snapshot


def _robots_text(response):
    if (
        response.status != 200 or response.media_type != "text/plain"
        or response.truncated or not response.identity_encoding
    ):
        raise ValueError("robots response unavailable")
    text = response.body.decode("utf-8-sig")
    # Preserve the valid records even if an unrelated line is malformed.
    directives = [line.partition("#")[0].strip() for line in text.splitlines()]
    meaningful = [line for line in directives if line]
    if meaningful and not any(ROBOTS_DIRECTIVE.match(line) for line in meaningful):
        raise ValueError("unexpected robots response")
    return text


def capture(target_id, manifest_path, region, request=get_document):
    """Capture exactly one reviewed target, or return an explicit non-observation."""
    if not REGION.fullmatch(region):
        raise ValueError("region must be a two-letter uppercase country code")
    manifest_version, target = _target_from_manifest(manifest_path, target_id)
    now = datetime.now(timezone.utc)
    snapshot = {
        "snapshot_schema_version": CAPTURE_SCHEMA_VERSION,
        "captured_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "capture_profile": PUBLIC_HTTP_PROFILE,
        "requested_url": target["url"],
        "capture_context": {
            "target_id": target_id,
            "manifest_version": manifest_version,
            "region": region,
            "accept_language": target["locale"],
            "system": platform.system(),
            "python_version": platform.python_version(),
            "user_agent": USER_AGENT,
            "robots_parser_version": None,
            "robots_status": None,
            "reviewed_on": None,
        },
    }
    if target["admission"] != "approved":
        return _record(snapshot, "not_collected", target["admission"])

    reviewed_on, expected_title, redirects, robots_policy = _approved_scope(target, now)
    try:
        from protego import Protego
    except ModuleNotFoundError as error:
        raise ValueError("install the capture extra to collect an approved target") from error
    snapshot["capture_context"].update(
        reviewed_on=reviewed_on, robots_parser_version=version("Protego"), robots_policy=robots_policy,
    )
    rules = None
    try:
        robots = request(target["robots_url"], target["locale"], "text/plain", ROBOTS_BYTES)
        snapshot["capture_context"]["robots_status"] = robots.status
        unavailable_endpoint = (
            robots_policy == "best_effort" and robots.status in UNAVAILABLE_ROBOTS_ENDPOINT_STATUS
        )
        if robots.status not in MISSING_ROBOTS_STATUS and not unavailable_endpoint:
            rules = Protego.parse(_robots_text(robots))
    except (OSError, http.client.HTTPException, ValueError):
        return _record(snapshot, "not_collected", "robots_unavailable")
    if rules is not None and (
        rules.crawl_delay(PRODUCT_TOKEN) or rules.request_rate(PRODUCT_TOKEN) or rules.visit_time(PRODUCT_TOKEN)
    ):
        return _record(snapshot, "not_collected", "robots_schedule_present")
    if unavailable_endpoint:
        redirects = frozenset()
    return _capture_page(snapshot, target, redirects, expected_title, rules, request)


def save_capture(target_id, manifest_path, region, destination):
    """Write a private snapshot atomically without replacing caller data."""
    if destination.exists() or destination.is_symlink():
        raise ValueError("capture output already exists")
    with tempfile.NamedTemporaryFile(dir=destination.parent, mode="wb", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        snapshot = capture(target_id, manifest_path, region, request=get_document)
        temporary.write_bytes((json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        os.link(temporary, destination)
    finally:
        temporary.unlink()
