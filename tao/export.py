"""Export a selected cohort and its coverage as privacy-minimized JSONL records."""

from collections import Counter

from .evaluate import CRITERION_ID, RESULTS, check_rubric, evaluate
from .replay import HOME_SURFACE, REGION, TARGET_ID, VERSION, validate_url

EXPORT_SCHEMA_VERSION = "0.1.0"


def _cohort(manifest):
    if not isinstance(manifest, dict) or manifest.get("surface") != HOME_SURFACE:
        raise ValueError("expected a consumer_homepage manifest")
    version = manifest.get("manifest_version")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("invalid manifest version")
    targets = manifest.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("manifest must contain at least one target")

    by_id = {}
    by_url = {}
    for entry in targets:
        if not isinstance(entry, dict):
            raise ValueError("invalid cohort target")
        target_id = entry.get("target_id")
        market = entry.get("market")
        url = entry.get("url")
        if not isinstance(target_id, str) or not TARGET_ID.fullmatch(target_id):
            raise ValueError("invalid cohort target ID")
        if not isinstance(market, str) or not REGION.fullmatch(market):
            raise ValueError("invalid cohort target market")
        validate_url(url, ("https",))
        if target_id in by_id or url in by_url:
            raise ValueError("cohort target IDs and URLs must be unique")
        by_id[target_id] = {"target_id": target_id, "market": market, "requested_url": url}
        by_url[url] = target_id
    return version, by_id, by_url


def _source_fields(observation):
    capture = observation["capture"]
    context = capture.get("capture_context", {})
    return {
        "observation_id": observation["observation_id"],
        "observation_schema_version": observation["observation_schema_version"],
        "evidence_ruleset_version": observation["evidence_ruleset_version"],
        "source_sha256": observation["source_sha256"],
        "captured_at": capture["captured_at"],
        "capture_profile": capture["capture_profile"],
        "capture_manifest_version": context.get("manifest_version"),
        "collection_region": context.get("region"),
        "accept_language": context.get("accept_language"),
        "access": capture["access"],
        "http_status": capture["http_status"],
    }


def export_records(manifest, observations, rubric_version):
    """Build one record per target plus a denominator summary."""
    check_rubric(rubric_version)
    manifest_version, targets, by_url = _cohort(manifest)
    records = {}
    for observation in observations:
        assessment = evaluate(observation, rubric_version, surface=HOME_SURFACE)
        capture = observation["capture"]
        context = capture.get("capture_context", {})
        if context.get("surface", HOME_SURFACE) != HOME_SURFACE:
            raise ValueError("only consumer homepages can be exported in this cohort")
        target_id = by_url.get(capture["requested_url"])
        if target_id is None or context.get("target_id", target_id) != target_id:
            raise ValueError("observation URL or target ID does not match the cohort")
        if target_id in records:
            raise ValueError("select exactly one observation per cohort target")
        missing_reason = None
        if capture["access"] != "observed":
            missing_reason = context.get("reason", capture["access"])
        records[target_id] = (_source_fields(observation), assessment, missing_reason)

    rows = []
    for target_id, target in targets.items():
        source, assessment, missing_reason = records.get(target_id, (None, None, "no_observation"))
        rows.append({
            "export_schema_version": EXPORT_SCHEMA_VERSION,
            "record_type": "target_result",
            **target,
            "source": source,
            "assessment": assessment,
            "result": assessment["result"] if assessment is not None else "unknown",
            "missing_reason": missing_reason,
        })
    counts = Counter(row["result"] for row in rows)
    missing = Counter(row["missing_reason"] for row in rows if row["missing_reason"] is not None)
    missing_count = sum(missing.values())
    rows.append({
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "record_type": "summary",
        "cohort_manifest_version": manifest_version,
        "surface": HOME_SURFACE,
        "rubric_version": rubric_version,
        "criterion_id": CRITERION_ID,
        "target_count": len(targets),
        "valid_observation_count": len(targets) - missing_count,
        "missing_observation_count": missing_count,
        "missing_reasons": dict(missing),
        "assessment_counts": {result: counts[result] for result in RESULTS},
    })
    return rows
