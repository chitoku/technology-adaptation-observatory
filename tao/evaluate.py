"""Evaluate a replayed observation with a published, versioned rubric."""

import hashlib
import re

from .replay import (
    CONFIDENCE_POLICY_VERSION, EVIDENCE_RULESET_VERSION, PUBLIC_HTTP_PROFILE, VERSION,
    observation_id, validate_capture_metadata, validate_url,
)

RUBRIC_VERSION = "0.1.0"
ASSESSMENT_SCHEMA_VERSION = "0.1.0"
CRITERION_ID = "public_home.https_landing"
SIGNAL_ID = "transport.landed_https"
SURFACES = ("consumer_homepage", "other", "unknown")
RESULTS = ("met", "unmet", "unknown", "not_applicable")
SHA256 = re.compile(r"[a-f0-9]{64}")
SIGNAL_NAME = re.compile(r"[a-z_]+\.[a-z_]+")
REASON = re.compile(r"[a-z_]+")
OBSERVATION_FIELDS = {
    "observation_id", "observation_schema_version", "producer_version",
    "evidence_ruleset_version", "capture", "source_sha256", "evidence", "confidence",
}


def check_rubric(version):
    if version != RUBRIC_VERSION:
        raise ValueError(f"unsupported rubric version (available: {RUBRIC_VERSION})")


def _index_rows(rows, fields, label):
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"expected a nonempty {label} list")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or row.keys() != fields:
            raise ValueError(f"invalid {label} fields")
        signal = row["signal_id"]
        if not isinstance(signal, str) or not SIGNAL_NAME.fullmatch(signal) or signal in indexed:
            raise ValueError(f"invalid or duplicate {label} signal reference")
        indexed[signal] = row
    return indexed


def _validate_observation(observation):
    if not isinstance(observation, dict) or observation.keys() != OBSERVATION_FIELDS:
        raise ValueError("expected a TAO observation")
    capture = observation["capture"]
    schema_version = validate_capture_metadata(capture)
    if observation["observation_schema_version"] != schema_version:
        raise ValueError("unsupported or inconsistent observation schema version")
    confidence = observation["confidence"]
    if not isinstance(confidence, dict) or confidence.keys() != {"policy_version", "annotations"}:
        raise ValueError("invalid confidence fields")
    ruleset = observation["evidence_ruleset_version"]
    versions = (ruleset, observation["producer_version"], confidence["policy_version"])
    if any(not isinstance(value, str) or not VERSION.fullmatch(value) for value in versions):
        raise ValueError("invalid observation version field")
    source = observation["source_sha256"]
    if not isinstance(source, str) or not SHA256.fullmatch(source):
        raise ValueError("invalid source SHA-256")
    if observation["observation_id"] != observation_id(schema_version, ruleset, source):
        raise ValueError("observation ID does not match its source and evidence version")

    evidence = _index_rows(observation["evidence"], {"signal_id", "locator", "state", "reason"}, "evidence")
    annotations = _index_rows(confidence["annotations"], {"signal_id", "level"}, "confidence")
    if evidence.keys() != annotations.keys():
        raise ValueError("confidence annotations must reference the observation's evidence")
    for signal, row in evidence.items():
        if (
            not isinstance(row["locator"], str) or not row["locator"].startswith("/")
            or not isinstance(row["reason"], str) or not REASON.fullmatch(row["reason"])
            or row["state"] not in ("present", "absent", "unknown")
            or annotations[signal]["level"] not in ("direct", "unassessed")
        ):
            raise ValueError("invalid evidence or confidence value")
        if capture["access"] != "observed" and row["state"] != "unknown":
            raise ValueError("unobserved capture cannot contain a conclusive signal")
    return evidence, annotations


def _assess_https(observation, surface, evidence, annotations):
    if surface == "other":
        return "not_applicable", "surface_out_of_scope", "This criterion applies to public consumer homepages."
    if surface == "unknown":
        return "unknown", "surface_unknown", "The observed surface has not been classified."
    if observation["evidence_ruleset_version"] != EVIDENCE_RULESET_VERSION:
        return "unknown", "unsupported_evidence_ruleset", "This rubric does not interpret that evidence version."
    if observation["confidence"]["policy_version"] != CONFIDENCE_POLICY_VERSION:
        return "unknown", "unsupported_confidence_policy", "This rubric does not interpret that confidence version."

    row = evidence.get(SIGNAL_ID)
    if row is None or row["state"] == "unknown" or annotations[SIGNAL_ID]["level"] != "direct":
        return "unknown", "no_direct_https_evidence", "There is no direct evidence of the final URL scheme."
    capture = observation["capture"]
    final_scheme = validate_url(capture["final_url"], ("http", "https")).scheme
    expected_state = "present" if final_scheme == "https" else "absent"
    if row["locator"] != "/final_url" or row["state"] != expected_state:
        raise ValueError("HTTPS evidence conflicts with the captured final URL")
    if expected_state == "present":
        return "met", "https_landing_observed", "The identified homepage was reached via HTTPS."
    return "unmet", "http_landing_observed", "The identified homepage was reached via HTTP."


def evaluate(observation, rubric_version, surface=None):
    """Return one standalone assessment; leave the source observation unchanged."""
    check_rubric(rubric_version)
    evidence, annotations = _validate_observation(observation)
    if surface is None:
        profile = observation["capture"]["capture_profile"]
        surface = "consumer_homepage" if profile == PUBLIC_HTTP_PROFILE else "unknown"
    if surface not in SURFACES:
        raise ValueError("invalid observation surface")
    result, reason, rationale = _assess_https(observation, surface, evidence, annotations)
    source_id = observation["observation_id"]
    policy_version = observation["confidence"]["policy_version"]
    identity = ":".join((
        ASSESSMENT_SCHEMA_VERSION, source_id, rubric_version, policy_version, CRITERION_ID, surface,
    ))
    return {
        "evaluation_schema_version": ASSESSMENT_SCHEMA_VERSION,
        "evaluation_id": "sha256:" + hashlib.sha256(identity.encode("ascii")).hexdigest(),
        "observation_id": source_id,
        "rubric_version": rubric_version,
        "confidence_policy_version": policy_version,
        "criterion_id": CRITERION_ID,
        "surface": surface,
        "result": result,
        "reason": reason,
        "rationale": rationale,
        "evidence_refs": [{"observation_id": source_id, "signal_id": SIGNAL_ID}]
        if surface == "consumer_homepage" and SIGNAL_ID in evidence else [],
    }
