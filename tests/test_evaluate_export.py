import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tao.evaluate import CRITERION_ID, RUBRIC_VERSION, SIGNAL_ID, evaluate
from tao.export import export_records
from tao.replay import observation_id, replay

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SNAPSHOT = FIXTURES / "home.snapshot.json"
HOME = "consumer_homepage"


def replay_fixture(**changes):
    snapshot = json.loads(SNAPSHOT.read_bytes())
    snapshot.update(changes)
    return replay(json.dumps(snapshot).encode("utf-8"))


class EvaluationTests(unittest.TestCase):
    def test_four_result_states(self):
        https = replay_fixture()
        http = replay_fixture(final_url="http://airline.example/ja/")
        blocked = replay_fixture(access="blocked", body_kind="unavailable", http_status=403, html=None)
        for observation, surface, expected in (
            (https, HOME, "met"), (http, HOME, "unmet"), (blocked, HOME, "unknown"),
            (https, "other", "not_applicable"), (https, None, "unknown"),
        ):
            with self.subTest(surface=surface, expected=expected):
                assessment = evaluate(observation, RUBRIC_VERSION, surface)
                self.assertEqual(assessment["result"], expected)
                self.assertEqual(assessment["criterion_id"], CRITERION_ID)
                self.assertEqual(assessment["observation_id"], observation["observation_id"])
                if surface == HOME:
                    self.assertEqual(assessment["evidence_refs"], [{
                        "observation_id": observation["observation_id"], "signal_id": SIGNAL_ID,
                    }])
                else:
                    self.assertEqual(assessment["evidence_refs"], [])

    def test_policy_changes_leave_observation_and_previous_assessment_intact(self):
        original = replay_fixture()
        first = evaluate(original, RUBRIC_VERSION, HOME)
        revised = deepcopy(original)
        revised["confidence"]["policy_version"] = "2.0.0"
        later = evaluate(revised, RUBRIC_VERSION, HOME)
        self.assertEqual(later["reason"], "unsupported_confidence_policy")
        self.assertEqual(later["observation_id"], first["observation_id"])
        self.assertNotEqual(later["evaluation_id"], first["evaluation_id"])
        self.assertEqual(first, evaluate(original, RUBRIC_VERSION, HOME))
        with self.assertRaisesRegex(ValueError, "unsupported rubric version"):
            evaluate(original, "9.0.0", HOME)
        self.assertEqual(first, evaluate(original, RUBRIC_VERSION, HOME))

    def test_unsupported_ruleset_yields_unknown(self):
        observation = replay_fixture()
        observation["evidence_ruleset_version"] = "2.0.0"
        observation["observation_id"] = observation_id(
            observation["observation_schema_version"], "2.0.0", observation["source_sha256"],
        )
        assessment = evaluate(observation, RUBRIC_VERSION, HOME)
        self.assertEqual(assessment["result"], "unknown")
        self.assertEqual(assessment["reason"], "unsupported_evidence_ruleset")

    def test_broken_evidence_references_and_conflicting_scheme_are_rejected(self):
        reference = replay_fixture()
        reference["confidence"]["annotations"][0]["signal_id"] = "transport.missing"
        with self.assertRaisesRegex(ValueError, "must reference"):
            evaluate(reference, RUBRIC_VERSION, HOME)

        duplicate = replay_fixture()
        duplicate["evidence"].append(duplicate["evidence"][0].copy())
        with self.assertRaisesRegex(ValueError, "duplicate evidence"):
            evaluate(duplicate, RUBRIC_VERSION, HOME)

        conflicting = replay_fixture()
        conflicting["evidence"][0]["state"] = "absent"
        with self.assertRaisesRegex(ValueError, "conflicts with"):
            evaluate(conflicting, RUBRIC_VERSION, HOME)

    def test_unknown_schema_and_wrong_source_identity_are_rejected(self):
        observation = replay_fixture()
        observation["observation_schema_version"] = "2.0.0"
        with self.assertRaisesRegex(ValueError, "schema version"):
            evaluate(observation, RUBRIC_VERSION, HOME)
        observation = replay_fixture()
        observation["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "ID does not match"):
            evaluate(observation, RUBRIC_VERSION, HOME)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.first = replay_fixture()
        self.blocked = replay_fixture(
            requested_url="https://second.example/ja/", final_url="https://second.example/ja/",
            access="blocked", body_kind="unavailable", http_status=403, html=None,
        )
        self.manifest = {
            "manifest_version": "0.1.0", "surface": HOME, "targets": [
                {"target_id": "first-home", "market": "JP", "url": "https://airline.example/ja/",
                 "review": {"private_note": "fixture-private-marker"}},
                {"target_id": "second-home", "market": "JP", "url": "https://second.example/ja/"},
                {"target_id": "third-home", "market": "US", "url": "https://third.example/en/"},
            ],
        }

    def test_cohort_output_preserves_status_and_explicit_denominator(self):
        self.first["evidence"][1]["locator"] = "/html#fixture-private-marker"
        records = export_records(self.manifest, [self.blocked, self.first], RUBRIC_VERSION)
        first, blocked, missing, summary = records
        self.assertEqual([row["result"] for row in records[:-1]], ["met", "unknown", "unknown"])
        self.assertEqual(first["source"]["source_sha256"], self.first["source_sha256"])
        self.assertEqual(blocked["source"]["http_status"], 403)
        self.assertEqual(blocked["missing_reason"], "blocked")
        self.assertIsNone(missing["source"])
        self.assertEqual(missing["missing_reason"], "no_observation")
        self.assertEqual(summary["valid_observation_count"], 1)
        self.assertEqual(summary["target_count"], 3)
        self.assertEqual(summary["missing_observation_count"], 2)
        self.assertEqual(summary["assessment_counts"], {
            "met": 1, "unmet": 0, "unknown": 2, "not_applicable": 0,
        })
        self.assertNotIn("fixture-private-marker", json.dumps(records))

    def test_ambiguous_selection_never_emits_partial_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps(self.manifest), encoding="utf-8")
            fixture = FIXTURES / "home.observation.json"
            result = subprocess.run(
                [sys.executable, "-m", "tao", "export", "--manifest", str(manifest),
                 "--rubric-version", RUBRIC_VERSION, str(fixture), str(fixture)],
                capture_output=True, check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"exactly one observation", result.stderr)

    def test_observation_outside_cohort_is_rejected(self):
        other = replay_fixture(requested_url="https://outside.example/en/")
        with self.assertRaisesRegex(ValueError, "does not match"):
            export_records(self.manifest, [other], RUBRIC_VERSION)
